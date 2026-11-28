"""Load an already staged adapter with one registered MCP SAVEWORK+DESIGN call.

Reserve a unique durable journal before touching E3D. Observe the new build,
project/MDB identity and empty startup Drawlist. View restoration is a separate
explicit option, never part of Addin startup.
"""
import argparse
import json
import os
import time
from pathlib import Path
from live_mcp import LiveMcp
from reload_validation import create_restart_journal, validate_project_reload

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--build', required=True)
parser.add_argument('--feature', action='append', default=[])
parser.add_argument('--report', type=Path, required=True)
parser.add_argument('--restore-view', action='store_true')
args = parser.parse_args()
state = {'passed': False, 'calls': [], 'design_replayed': False}
create_restart_journal(args.report, state)


def persist():
    args.report.parent.mkdir(parents=True, exist_ok=True)
    temp = args.report.with_suffix(args.report.suffix + '.tmp')
    with temp.open('w', encoding='utf-8') as stream:
        json.dump(state, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    for attempt in range(50):
        try:
            temp.replace(args.report)
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.1)


with LiveMcp() as client:
    def call(name, arguments=None):
        row = {'tool': name, 'arguments': arguments or {}, 'status': 'started'}
        state['calls'].append(row)
        persist()
        try:
            response = client.rpc('tools/call', {'name': name, 'arguments': arguments or {}})
        except Exception as exc:
            row.update(status='observation_failed', error=str(exc),
                next_action='Inspect the current session and this journal; do not replay DESIGN.')
            persist()
            raise
        result = response.get('structuredContent', response)
        row.update(status='returned', result=result, response=response)
        persist()
        assert not response.get('isError'), result
        return result

    before = call('e3d_status')
    project = call('e3d_project_info')['project']
    drawlist = call('e3d_drawlist_snapshot')['drawlist']
    assert before['connected'] and before['bridge_build'] != args.build
    call('e3d_reenter_design', {'wait_seconds': 120})
    after = call('e3d_status')
    assert after['session_id'] != before['session_id'] and after['bridge_build'] == args.build
    assert set(args.feature) <= set(after['api_features'])
    current = call('e3d_project_info')['project']
    state['project_before'] = project
    state['project_after'] = current
    state['project_reload_observation'] = validate_project_reload(project, current)
    state['current_element_changed'] = state['project_reload_observation']['current_element_changed']
    empty = call('e3d_drawlist_snapshot')['drawlist']
    assert empty['available'] and empty['member_count'] == 0
    state['startup_drawlist_empty'] = True
    if args.restore_view:
        restored = call('e3d_restore_view', {'fit': True})
        assert restored['result']['ready']
        actual = call('e3d_drawlist_snapshot')['drawlist']
        assert sorted(actual['members'], key=lambda x: x['refno']) == sorted(drawlist['members'], key=lambda x: x['refno'])
        state['explicitly_displayed'] = actual['member_count']
    state['passed'] = True
    persist()
    print(json.dumps({'passed': True, 'health': after, 'explicitly_displayed': state.get('explicitly_displayed', 0)}))

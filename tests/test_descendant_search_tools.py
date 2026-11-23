from __future__ import annotations

import json
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class DescendantSearchToolsTests(unittest.TestCase):
    def rpc(self, method, params, settings=None):
        output = StringIO()
        with patch('sys.stdout', output):
            create_server(settings or Settings(mode='bridge', allow_commands=False)).serve([
                json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}) + '\n'])
        return json.loads(output.getvalue())['result']

    def call(self, args, settings=None):
        return self.rpc('tools/call', {'name': 'e3d_find_descendants', 'arguments': args}, settings)

    def test_discovery_has_only_bounded_read_controls(self):
        tools = self.rpc('tools/list', {})['tools']
        tool = next(t for t in tools if t['name'] == 'e3d_find_descendants')
        self.assertEqual(len(tools), 117)
        self.assertTrue(tool['annotations']['readOnlyHint'])
        self.assertFalse(tool['annotations']['destructiveHint'])
        self.assertEqual(set(tool['inputSchema']['properties']),
                         {'root_refno', 'element_type', 'name_contains', 'max_depth', 'limit', 'cursor'})
        self.assertNotIn('default', tool['inputSchema']['properties']['max_depth'])
        self.assertFalse(tool['inputSchema']['additionalProperties'])

    def test_first_page_and_continuation_preserve_omitted_filters(self):
        result = {'ok': True, 'result': {'success': True, 'has_more': True, 'elements': [], 'next_cursor': 'signed'}}
        with patch.object(BridgeClient, 'find_descendants', return_value=result) as native:
            self.assertEqual(self.call({'root_refno': ' /PUMP '})['structuredContent'], result)
            native.assert_called_once_with('/PUMP', None, None, None, 50, '')
            native.reset_mock()
            self.call({'cursor': 'signed', 'limit': 7})
            native.assert_called_once_with('', None, None, None, 7, 'signed')

    def test_explicit_empty_filters_are_distinct_from_omitted(self):
        with patch.object(BridgeClient, 'find_descendants', return_value={'ok': True}) as native:
            self.call({'cursor': 'signed', 'element_type': '', 'name_contains': '', 'max_depth': 1})
            native.assert_called_once_with('', '', '', 1, 50, 'signed')
            native.reset_mock()
            self.call({'root_refno': '=1/2', 'element_type': ' ncyl ', 'name_contains': ' washer ', 'max_depth': 64})
            native.assert_called_once_with('=1/2', 'NCYL', ' washer ', 64, 50, '')

    def test_invalid_input_rejected_before_bridge(self):
        invalid = [{}, {'root_refno': ''}, {'root_refno': 'CE'}, {'root_refno': '/'}, {'root_refno': '=x/2'}]
        invalid += [{'root_refno': value} for value in (None, True, 2, [], '/PUMP\nDELETE CE')]
        for key, values in {
            'limit': [True, 0, 201, 1.5, None], 'max_depth': [None, True, 0, 65, '2'],
            'element_type': [None, False, 'NCYL;DELETE', 'x' * 65],
            'name_contains': [None, [], 'x' * 257, '\n'], 'cursor': [None, True, 'x' * 16385],
        }.items():
            invalid += [{'root_refno': '/PUMP', key: value} for value in values]
        invalid += [{'root_refno': '/PUMP', key: value} for key, value in
                    [('dry_run', True), ('capture_after', False), ('command', 'Q NAME'), ('offset', 1)]]
        with patch.object(BridgeClient, 'find_descendants') as native:
            for args in invalid:
                with self.subTest(args=args):
                    self.assertTrue(self.call(args)['isError'])
            native.assert_not_called()

    def test_partial_read_errors_and_depth_omissions_are_not_fabricated(self):
        for payload, failed in [
            ({'success': False, 'complete': False, 'scope_complete': False, 'has_more': None,
              'next_cursor': None, 'elements': [{'refno': '=1/3'}], 'read_errors': ['read failed']}, True),
            ({'success': True, 'complete': False, 'scope_complete': True, 'has_more': False,
              'depth_truncated': True, 'elements': []}, False),
            ({'success': True, 'complete': False, 'scope_complete': False, 'has_more': True,
              'next_cursor': 'signed', 'elements': [], 'visited_count': 1000, 'stop_reason': 'scan_limit'}, False),
        ]:
            response = {'ok': True, 'result': payload}
            with patch.object(BridgeClient, 'find_descendants', return_value=response):
                actual = self.call({'root_refno': '/PUMP'})
                self.assertEqual(actual.get('isError', False), failed)
                self.assertEqual(actual['structuredContent'], response)

    def test_native_route_omits_inherited_values_and_keeps_explicit_empty(self):
        client = BridgeClient(Settings(mode='bridge', version='E3D21'))
        with patch.object(client, '_request', return_value={'ok': True}) as request:
            client.find_descendants(cursor='signed', version='E3D21')
            request.assert_called_once_with('POST', '/api/v1/elements/find-descendants',
                {'root_refno': '', 'limit': 50, 'cursor': 'signed'}, version='E3D21')
            request.reset_mock()
            client.find_descendants('/PUMP', '', '', 1, 3, '', version='E3D21')
            request.assert_called_once_with('POST', '/api/v1/elements/find-descendants',
                {'root_refno': '/PUMP', 'element_type': '', 'name_contains': '', 'max_depth': 1, 'limit': 3, 'cursor': ''}, version='E3D21')

    def test_mock_cannot_claim_native_completion_or_cursors(self):
        response = self.call({'root_refno': '/PUMP'}, Settings(mode='mock', version='E3D21'))
        self.assertTrue(response['isError'])
        result = response['structuredContent']['result']
        self.assertFalse(result['available'])
        self.assertFalse(result['complete'])
        self.assertIsNone(result['has_more'])
        self.assertIsNone(result['next_cursor'])

    def test_transport_failure_never_retries_or_claims_write_uncertainty(self):
        with patch.object(BridgeClient, 'find_descendants', side_effect=BridgeError('closed', transport_error=True)) as read, \
                patch.object(BridgeClient, 'command') as command, patch.object(BridgeClient, 'capture_view') as capture:
            result = self.call({'root_refno': '/PUMP'})
            self.assertTrue(result['isError'])
            read.assert_called_once()
            command.assert_not_called()
            capture.assert_not_called()
            self.assertNotIn('outcome_uncertain', json.dumps(result))


if __name__ == '__main__':
    unittest.main()

from __future__ import annotations

from contextlib import ExitStack
from io import BytesIO, StringIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class DisplayStateToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(Settings(mode='bridge', allow_commands=False))

    def _rpc(self, method: str, params: dict, *, server=None) -> dict:
        request = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
        output = StringIO()
        with patch('sys.stdout', output):
            (server or self.server).serve([request + '\n'])
        response = json.loads(output.getvalue())
        self.assertNotIn('error', response, response)
        return response['result']

    def _call(self, arguments: dict, *, server=None) -> dict:
        return self._rpc('tools/call', {'name': 'e3d_display_state', 'arguments': arguments}, server=server)

    @staticmethod
    def _native(**fields) -> dict:
        return {'ok': True, 'version': 'E3D21', 'result': {
            'success': True, 'available': True, 'complete': True, 'refno': '=1/2',
            'element': {'refno': '=1/2', 'type': 'CYLI', 'name': '/PART'}, 'drawlist_id': 17,
            'ce_before': '=9/1', 'ce_after': '=9/1', 'ce_unchanged': True,
            'top_level_element': False, 'exact_member': False,
            'resolved_member': {'refno': '=1/1', 'type': 'EQUI', 'name': '/PUMP',
                                'colour': 21, 'transparency': 0, 'edges': True,
                                'visibility': True, 'shallow': False},
            'resolved_member_matches_target': False,
            'primitive_match_count': 1, 'primitive_matches': [
                {'refno': '=1/2', 'owner_refno': '=1/1', 'colour': 2, 'transparency': 35,
                 'edges': False, 'visibility': False, 'shallow': False}],
            'primitive_count': 8, 'primitive_scan_complete': True, 'read_errors': [],
            'pixel_visibility_verified': False, **fields,
        }}

    def test_catalog_is_explicit_read_only_and_does_not_add_a_receipt_tool(self) -> None:
        tools = {tool['name']: tool for tool in self._rpc('tools/list', {})['tools']}
        self.assertEqual(len(tools), 124)
        self.assertEqual(sum('capture_after' in tool['inputSchema'].get('properties', {}) for tool in tools.values()), 19)
        read = tools['e3d_display_state']
        self.assertEqual(read['annotations'], {
            'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False,
        })
        schema = read['inputSchema']
        self.assertEqual(schema['required'], ['refno'])
        self.assertEqual(set(schema['properties']), {'refno'})
        self.assertNotIn('default', schema['properties']['refno'])
        self.assertFalse(schema['additionalProperties'])
        self.assertIn('native primitive entries', read['description'])
        self.assertIn('no matching entry does not imply no rendering', read['description'])
        self.assertIn('Pixel visibility is not verified', read['description'])
        self.assertNotIn('override', read['description'].lower())
        visual = tools['e3d_set_drawlist_visual']['description']
        self.assertIn('exact top-level', visual)
        self.assertIn('e3d_display_state', visual)
        self.assertIn('Add does not guarantee', visual)

    def test_read_without_write_permission_preserves_requested_owner_and_primitive_states(self) -> None:
        payload = self._native()
        with patch.object(BridgeClient, 'display_state', return_value=payload) as read:
            response = self._call({'refno': ' =1/2 '})
        read.assert_called_once_with('=1/2')
        self.assertFalse(response.get('isError', False), response)
        self.assertEqual(response['structuredContent'], payload)
        result = response['structuredContent']['result']
        self.assertFalse(result['resolved_member_matches_target'])
        self.assertEqual(result['resolved_member']['refno'], '=1/1')
        self.assertTrue(result['resolved_member']['visibility'])
        self.assertFalse(result['primitive_matches'][0]['visibility'])
        self.assertFalse(result['pixel_visibility_verified'])
        self.assertEqual([part['type'] for part in response['content']], ['text'])
        self.assertEqual(json.loads(response['content'][0]['text']), payload)

    def test_exact_member_and_multiple_native_entries_are_preserved_without_merging(self) -> None:
        entries = [
            {'refno': '=1/2', 'owner_refno': '=1/2', 'colour': 5, 'transparency': 0,
             'edges': True, 'visibility': True, 'shallow': False},
            {'refno': '=1/2', 'owner_refno': '=1/1', 'colour': 7, 'transparency': 70,
             'edges': False, 'visibility': False, 'shallow': True},
        ]
        payload = self._native(top_level_element=True, exact_member=True, resolved_member_matches_target=True,
                              resolved_member={'refno': '=1/2', 'type': 'SUBE', 'name': '/PART', **{
                                  key: entries[0][key] for key in ('colour', 'transparency', 'edges', 'visibility', 'shallow')}},
                              primitive_match_count=2, primitive_matches=entries)
        with patch.object(BridgeClient, 'display_state', return_value=payload):
            response = self._call({'refno': '/PART'})
        self.assertEqual(response['structuredContent'], payload)
        for key in ('effective_colour', 'effective_visibility', 'override', 'pixel_visible'):
            self.assertNotIn(key, response['structuredContent']['result'])

    def test_no_native_primitive_entry_does_not_synthesize_hidden_or_missing_rendering(self) -> None:
        payload = self._native(primitive_match_count=0, primitive_matches=[])
        with patch.object(BridgeClient, 'display_state', return_value=payload):
            response = self._call({'refno': '=1/2'})
        self.assertFalse(response.get('isError', False))
        self.assertEqual(response['structuredContent'], payload)
        self.assertTrue(response['structuredContent']['result']['resolved_member']['visibility'])
        self.assertNotIn('target_display_enabled', response['structuredContent']['result'])

    def test_unresolved_owner_is_not_replaced_by_requested_element(self) -> None:
        payload = self._native(resolved_member=None, resolved_member_matches_target=None,
                              primitive_match_count=0, primitive_matches=[], primitive_count=0)
        with patch.object(BridgeClient, 'display_state', return_value=payload):
            response = self._call({'refno': '=1/2'})
        self.assertEqual(response['structuredContent'], payload)
        self.assertIsNone(response['structuredContent']['result']['resolved_member'])
        self.assertIsNone(response['structuredContent']['result']['resolved_member_matches_target'])

    def test_missing_implicit_and_malformed_references_are_rejected_before_native(self) -> None:
        invalid = [{}, *({'refno': value} for value in (
            None, True, 1, [], {}, '', ' ', 'CE', 'OWNER', 'WORLD', '/', '=x/2', '=1/2/3',
            '/PUMP\nDELETE CE', '/PUMP\x00', '/PUMP\x80', '/PUMP PART',
        ))]
        with patch.object(BridgeClient, 'display_state') as read:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self._call(arguments)['isError'])
        read.assert_not_called()

    def test_unknown_write_receipt_and_query_controls_are_rejected_before_native(self) -> None:
        controls = [('dry_run', True), ('capture_after', False), ('capture_width', 64), ('capture_height', 64),
                    ('colour', 'grey'), ('visibility', True), ('add', False), ('wrt', 'WORLD'),
                    ('scope', 'primitive'), ('limit', 20), ('expected_revision', 'a' * 64)]
        with patch.object(BridgeClient, 'display_state') as read:
            for name, value in controls:
                with self.subTest(name=name):
                    self.assertTrue(self._call({'refno': '/PART', name: value})['isError'])
        read.assert_not_called()

    def test_partial_scan_preserves_observed_entries_unknowns_and_read_errors(self) -> None:
        payload = self._native(success=False, complete=False, top_level_element=None,
                              exact_member=None, primitive_scan_complete=False,
                              read_errors=['TOPF unavailable', 'Primitive scan reached its native limit'])
        with patch.object(BridgeClient, 'display_state', return_value=payload):
            response = self._call({'refno': '/PART'})
        self.assertTrue(response['isError'])
        self.assertEqual(response['structuredContent'], payload)
        self.assertIsNone(response['structuredContent']['result']['top_level_element'])
        self.assertEqual(response['structuredContent']['result']['primitive_match_count'], 1)

    def test_successful_read_preserves_native_incompleteness_without_invented_claims(self) -> None:
        payload = self._native(complete=False, primitive_scan_complete=False,
                              read_errors=['Unrelated member entry was unreadable'])
        with patch.object(BridgeClient, 'display_state', return_value=payload):
            response = self._call({'refno': '/PART'})
        self.assertFalse(response.get('isError', False))
        self.assertEqual(response['structuredContent'], payload)

    def test_read_never_invokes_geometry_display_camera_capture_or_command_mutations(self) -> None:
        blocked = ('set_drawlist_visual', 'restore_view', 'fit_view', 'set_view', 'capture_view', 'command',
                   'command_batch', 'cancel_command', 'create_element', 'copy_element', 'set_transform', 'set_attribute')
        with ExitStack() as stack:
            mutations = [stack.enter_context(patch.object(BridgeClient, name)) for name in blocked]
            read = stack.enter_context(patch.object(BridgeClient, 'display_state', return_value=self._native()))
            response = self._call({'refno': '/PART'})
        self.assertFalse(response.get('isError', False))
        read.assert_called_once_with('/PART')
        for mutation in mutations:
            mutation.assert_not_called()

    def test_read_transport_failure_does_not_claim_possible_mutation_or_capture(self) -> None:
        with patch.object(BridgeClient, 'display_state', side_effect=BridgeError('closed', transport_error=True)) as read, \
                patch.object(BridgeClient, 'capture_view') as capture:
            response = self._call({'refno': '/PART'})
        self.assertTrue(response['isError'])
        read.assert_called_once_with('/PART')
        capture.assert_not_called()
        for key in ('outcome_uncertain', 'retry_safe', 'receipt'):
            self.assertNotIn(key, json.dumps(response))

    def test_mock_reports_unavailable_without_fabricating_members_entries_or_ce(self) -> None:
        mock = create_server(Settings(mode='mock', version='E3D21'))
        with patch.object(BridgeClient, '_request') as request:
            response = self._call({'refno': '/PART'}, server=mock)
        request.assert_not_called()
        self.assertTrue(response['isError'])
        result = response['structuredContent']['result']
        self.assertEqual(response['structuredContent']['version'], 'E3D21')
        self.assertEqual(result['source'], 'mock')
        self.assertEqual(result['refno'], '/PART')
        for key in ('success', 'available', 'complete', 'pixel_visibility_verified'):
            self.assertFalse(result[key])
        for key in ('top_level_element', 'exact_member', 'resolved_member', 'resolved_member_matches_target',
                    'primitive_count', 'primitive_matches', 'primitive_match_count', 'ce_unchanged', 'drawlist_id'):
            self.assertNotIn(key, result)

    def test_bridge_route_forwards_only_refno_and_selected_instance(self) -> None:
        client = BridgeClient(Settings(mode='bridge', version='E3D21'))
        with patch.object(client, '_request', return_value={'ok': True}) as request:
            client.display_state('=123/456', version='E3D21')
            request.assert_called_once_with('GET', '/api/v1/view/element-state', query={'refno': '=123/456'}, version='E3D21')
            request.reset_mock()
            client.display_state('/PART')
            request.assert_called_once_with('GET', '/api/v1/view/element-state', query={'refno': '/PART'}, version=None)

    def test_http_uses_encoded_get_and_preserves_raw_native_fields(self) -> None:
        client = BridgeClient(Settings(mode='bridge', version='E3D21'))
        payload = self._native()
        with patch('urllib.request.urlopen') as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode('utf-8')
            actual = client.display_state('/PART+01')
        self.assertEqual(actual, payload)
        urlopen.assert_called_once()
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertIsNone(request.data)
        parsed = urllib.parse.urlsplit(request.full_url)
        self.assertEqual(parsed.path, '/api/v1/view/element-state')
        self.assertEqual(urllib.parse.parse_qs(parsed.query), {'refno': ['/PART+01']})

    def test_http_error_and_timeout_are_not_replayed_recovered_or_cancelled(self) -> None:
        failures = [TimeoutError('timed out'), urllib.error.HTTPError(
            'http://bridge/api/v1/view/element-state', 503, 'Unavailable', {}, BytesIO(b'{"error":"native view unavailable"}'))]
        for failure in failures:
            client = BridgeClient(Settings(mode='bridge', version='E3D21'))
            with self.subTest(failure=type(failure).__name__), \
                    patch('urllib.request.urlopen', side_effect=failure) as request, \
                    patch.object(client, '_recover_command_timeout') as recover, \
                    patch.object(client, 'cancel_command') as cancel:
                with self.assertRaises(BridgeError) as caught:
                    client.display_state('/PART')
            request.assert_called_once()
            recover.assert_not_called()
            cancel.assert_not_called()
            if isinstance(failure, urllib.error.HTTPError):
                self.assertEqual(caught.exception.http_status, 503)
                self.assertIn('native view unavailable', str(caught.exception))
            else:
                self.assertTrue(caught.exception.transport_error)


if __name__ == '__main__':
    unittest.main()

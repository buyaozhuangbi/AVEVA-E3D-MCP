"""Offline contracts for one explicit E3D21 database spatial-map observation."""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import unittest
import urllib.parse
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class SpatialMapStatusToolsTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(mode="bridge", version="E3D21", allow_commands=False)
        self.server = create_server(self.settings)
        self.arguments = {"database_number": 12345, "extract_number": 1}

    def invoke(self, arguments, settings=None):
        server = create_server(settings) if settings else self.server
        return server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "e3d_spatial_map_status", "arguments": arguments}})["result"]

    def test_discovery_is_strict_readonly_without_capture_and_uses_database_context(self):
        definitions = self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        self.assertEqual(len(definitions), 120)
        tool = next(item for item in definitions if item["name"] == "e3d_spatial_map_status")
        schema = tool["inputSchema"]
        self.assertEqual(set(schema["properties"]), {"database_number", "extract_number", "expected_context"})
        self.assertEqual(schema["required"], ["database_number", "extract_number"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["database_number"]["minimum"], 1)
        self.assertEqual(schema["properties"]["extract_number"]["minimum"], 0)
        self.assertEqual(schema["properties"]["database_number"]["maximum"], 2147483647)
        self.assertEqual(schema["properties"]["expected_context"]["default"], "")
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertTrue(tool["annotations"]["idempotentHint"])
        self.assertFalse(tool["annotations"]["destructiveHint"])
        self.assertEqual(sum("capture_after" in item["inputSchema"]["properties"] for item in definitions), 19)
        for phrase in ("e3d_database_state", "NOWARNINGS", "UPTODATE", "multiple extracts", "extract_number=1",
                       "internal diagnostic caches", "no status proves geometry correctness"):
            self.assertIn(phrase, tool["description"])

    def test_bad_numbers_context_and_extra_fields_fail_before_bridge(self):
        invalid = [{}, {"database_number": 1}, {"extract_number": 1}]
        invalid += [{**self.arguments, "database_number": value} for value in (None, True, False, 0, -1, 2147483648, 1.0, "1", [])]
        invalid += [{**self.arguments, "extract_number": value} for value in (None, True, False, -1, 2147483648, 0.0, "0", {})]
        invalid += [{**self.arguments, "expected_context": value} for value in (None, True, 1, [], " ", "a", "A" * 64, "a" * 65, "a" * 64 + "\n")]
        with patch.object(BridgeClient, "spatial_map_status") as check:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self.invoke(arguments)["isError"])
            for extra in ("version", "dry_run", "capture_after", "secret-field"):
                response = self.invoke({**self.arguments, extra: True})
                self.assertTrue(response["isError"])
                self.assertNotIn("secret-field", json.dumps(response))
        check.assert_not_called()
        client = BridgeClient(self.settings)
        with patch.object(client, "_request") as request:
            for arguments in invalid:
                with self.subTest(direct=arguments), self.assertRaises((ValueError, TypeError)):
                    client.spatial_map_status(**arguments)
        request.assert_not_called()

    def test_primary_extract_one_query_needs_no_command_permission(self):
        native = {"ok": True, "version": "E3D21", "result": {"success": True, "complete": True,
                  "database": {"number": 12345, "extract_number": 1, "is_extract": False, "is_primary": True},
                  "status": "UPTODATE", "status_value": 1}}
        with patch.object(BridgeClient, "_request", return_value=native) as request, \
                patch.object(BridgeClient, "command") as command, patch.object(BridgeClient, "capture_view") as capture:
            response = self.invoke(self.arguments)
        request.assert_called_once_with("GET", "/api/v1/spatial-map/status", query={
            "database_number": "12345", "extract_number": "1", "expected_context": ""}, version="E3D21")
        self.assertEqual(response["structuredContent"], native)
        self.assertFalse(response.get("isError", False))
        command.assert_not_called()
        capture.assert_not_called()

    def test_boundaries_and_context_forward_without_normalization(self):
        for db, extract in ((1, 0), (2147483647, 2147483647)):
            with self.subTest(db=db, extract=extract), patch.object(BridgeClient, "spatial_map_status", return_value={"ok": True}) as check:
                response = self.invoke({"database_number": db, "extract_number": extract, "expected_context": "ab" * 32})
            check.assert_called_once_with(database_number=db, extract_number=extract, expected_context="ab" * 32)
            self.assertFalse(response.get("isError", False))

    def test_nowarnings_and_unknown_raw_status_are_preserved_without_verdict(self):
        for status, number in (("NOWARNINGS", 2), ("UNKNOWN", 0), (None, 999)):
            native = {"ok": True, "result": {"success": True, "complete": True,
                      "check_attempted": True, "check_completed": True, "status": status, "status_value": number,
                      "messages": [{"type": "SYSTEM", "description": "Native map diagnostic"}]}}
            with self.subTest(status=status), patch.object(BridgeClient, "_request", return_value=native):
                response = self.invoke(self.arguments)
            self.assertEqual(response["structuredContent"], native)
            self.assertFalse(response.get("isError", False))
            self.assertNotIn("geometry_valid", response["structuredContent"]["result"])

    def test_native_typed_gate_failure_and_partial_data_survive(self):
        for code in ("context_mismatch", "unsupported_spatial_map_extract_scope", "ambiguous_database_number", "read_failed"):
            native = {"ok": True, "result": {"success": False, "complete": False,
                      "check_attempted": False, "check_completed": False, "status": None, "status_value": None,
                      "database": {"number": 12345, "extract_number": 1},
                      "read_errors": [{"path": "database", "code": code, "message": "Native typed diagnostic"}]}}
            with self.subTest(code=code), patch.object(BridgeClient, "_request", return_value=native) as request:
                response = self.invoke(self.arguments)
            self.assertTrue(response["isError"])
            self.assertEqual(response["structuredContent"], native)
            request.assert_called_once()

    def test_mock_and_other_versions_do_not_request_native_or_fabricate_status(self):
        for settings, code in ((Settings(mode="mock", version="E3D21"), "native_unavailable"),
                               (Settings(mode="bridge", version="E3D31"), "unsupported_version")):
            with self.subTest(code=code), patch.object(BridgeClient, "_request") as request:
                response = self.invoke(self.arguments, settings)
            request.assert_not_called()
            self.assertTrue(response["isError"])
            result = response["structuredContent"]["result"]
            self.assertFalse(result["available"])
            self.assertFalse(result["complete"])
            self.assertFalse(result["check_attempted"])
            self.assertFalse(result["check_completed"])
            self.assertIsNone(result["status"])
            self.assertIsNone(result["status_value"])
            self.assertEqual(result["read_errors"][0]["code"], code)

    def test_transport_failure_preserves_state_without_recovery_or_replay(self):
        state = {"database_ready": False, "recovery_required": True}
        with patch.object(BridgeClient, "_request", side_effect=BridgeError("closed", transport_error=True,
                          diagnostics={"state": state})) as request, \
                patch.object(BridgeClient, "_recover_command_timeout") as recover, \
                patch.object(BridgeClient, "command") as command, patch.object(BridgeClient, "capture_view") as capture:
            response = self.invoke(self.arguments)
        self.assertTrue(response["isError"])
        self.assertEqual(response["structuredContent"]["state"], state)
        self.assertNotIn("outcome_uncertain", response["structuredContent"])
        request.assert_called_once()
        recover.assert_not_called()
        command.assert_not_called()
        capture.assert_not_called()

    def test_http_get_keeps_context_and_does_not_post(self):
        native = {"ok": True, "version": "E3D21", "result": {"status": "NOWARNINGS", "status_value": 2}}
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(native).encode())) as request:
            response = BridgeClient(self.settings).spatial_map_status(12345, 1, "ab" * 32)
        request.assert_called_once()
        actual = request.call_args.args[0]
        self.assertEqual(actual.method, "GET")
        self.assertIsNone(actual.data)
        url = urllib.parse.urlparse(actual.full_url)
        self.assertEqual(url.path, "/api/v1/spatial-map/status")
        self.assertEqual(urllib.parse.parse_qs(url.query), {"database_number": ["12345"],
            "extract_number": ["1"], "expected_context": ["ab" * 32]})
        self.assertEqual(response, native)


if __name__ == "__main__":
    unittest.main()

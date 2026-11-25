from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.database_tools import database_identity_snapshot, database_state
from aveva_e3d_mcp.tools import create_server


class DatabaseStateToolsTests(unittest.TestCase):
    def rpc(self, method, params, settings=None):
        server = create_server(settings or Settings(mode="bridge", version="E3D21", allow_commands=False))
        return server._dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})["result"]

    def call(self, args, settings=None):
        return self.rpc("tools/call", {"name": "e3d_database_state", "arguments": args}, settings)

    def test_schema_and_validation_are_bounded(self):
        tools = self.rpc("tools/list", {})["tools"]
        self.assertEqual(len(tools), 126)
        tool = next(item for item in tools if item["name"] == "e3d_database_state")
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["destructiveHint"])
        self.assertEqual(set(tool["inputSchema"]["properties"]), {"offset", "limit", "expected_context"})
        self.assertFalse(tool["inputSchema"]["additionalProperties"])
        snapshot = next(item for item in tools if item["name"] == "e3d_database_identity_snapshot")
        self.assertEqual(snapshot["annotations"], {"readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False})
        self.assertEqual(set(snapshot["inputSchema"]["properties"]), {"expected_context"})
        self.assertEqual(snapshot["inputSchema"]["required"], ["expected_context"])


    def test_invalid_inputs_fail_before_bridge(self):
        invalid = [{"offset": 1}]
        for key, values in {
            "offset": [None, True, -1, 100001, 1.5, "0"],
            "limit": [None, True, 0, 201, 1.5, "2"],
            "expected_context": [None, 2, [], "a", "A" * 64, "a" * 65, "a" * 64 + "\n"],
        }.items():
            invalid.extend({key: value} for value in values)
        invalid.extend({key: value} for key, value in [
            ("include_claims", False), ("claims_limit", 1), ("capture_after", False),
            ("dry_run", True), ("version", "E3D21"), ("session_id", "SESSION")])
        with patch.object(BridgeClient, "_request") as request:
            for args in invalid:
                with self.subTest(args=args):
                    self.assertTrue(self.call(args)["isError"])
            request.assert_not_called()

    def test_context_is_sent_and_mock_is_explicitly_unavailable(self):
        bridge = BridgeClient(Settings(mode="bridge", version="E3D21"))
        with patch.object(bridge, "_request", return_value={"ok": True, "result": {"success": True}}) as request:
            database_state(bridge, {"offset": 7, "limit": 2, "expected_context": "a" * 64})
            request.assert_called_once_with("GET", "/api/v1/database/state", query={
                "offset": "7", "limit": "2", "expected_context": "a" * 64}, version="E3D21")
        mock = BridgeClient(Settings(mode="mock", version="E3D21"))
        result = database_state(mock, {})["result"]
        self.assertFalse(result["available"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["read_errors"][0]["code"], "native_unavailable")

    def test_snapshot_bridge_method_and_mcp_wrapper_use_public_method(self):
        context_id = "a" * 64
        bridge = BridgeClient(Settings(mode="bridge", version="E3D21"))
        native = {"ok": True, "version": "E3D21", "result": {"success": True, "complete": True}}
        with patch.object(bridge, "_request", return_value=native) as request:
            self.assertEqual(bridge.database_identity_snapshot(context_id, version="E3D21"), native)
        request.assert_called_once_with("GET", "/api/v1/database/identity-snapshot",
                                        query={"expected_context": context_id}, version="E3D21")
        with patch.object(BridgeClient, "database_identity_snapshot", return_value=native) as read:
            reply = self.rpc("tools/call", {"name": "e3d_database_identity_snapshot",
                "arguments": {"expected_context": context_id}})
        self.assertFalse(reply.get("isError", False), reply)
        self.assertEqual(reply["structuredContent"], native)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(read.call_args.args[0], context_id)
        self.assertEqual(read.call_args.kwargs, {})
        with patch.object(BridgeClient, "database_identity_snapshot") as read:
            invalid = self.rpc("tools/call", {"name": "e3d_database_identity_snapshot",
                "arguments": {"expected_context": "bad"}})
        self.assertTrue(invalid["isError"])
        read.assert_not_called()


    def test_snapshot_mock_and_other_versions_do_not_contact_bridge(self):
        for settings, code in ((Settings(mode="mock", version="E3D21"), "native_unavailable"),
                               (Settings(mode="bridge", version="E3D31"), "unsupported_version")):
            with self.subTest(code=code), patch.object(BridgeClient, "_request") as native:
                result = database_identity_snapshot(BridgeClient(settings), "a" * 64)["result"]
            self.assertFalse(result["available"])
            self.assertFalse(result["complete"])
            self.assertEqual(result["read_errors"][0]["code"], code)
            native.assert_not_called()

    def test_registered_tool_uses_read_route_without_command_permission(self):
        native = {"ok": True, "version": "E3D21", "result": {"success": True, "page_complete": True,
                  "complete": False, "has_more": True, "next_offset": 1, "context_id": "b" * 64}}
        with patch.object(BridgeClient, "_request", return_value=native) as request:
            self.assertEqual(self.call({})["structuredContent"], native)
            request.assert_called_once_with("GET", "/api/v1/database/state", query={
                "offset": "0", "limit": "50", "expected_context": ""}, version="E3D21")
            request.reset_mock()
            self.call({"offset": 1, "limit": 200, "expected_context": "b" * 64})
            request.assert_called_once_with("GET", "/api/v1/database/state", query={
                "offset": "1", "limit": "200", "expected_context": "b" * 64}, version="E3D21")

    def test_partial_reads_and_context_failures_preserve_native_diagnostics(self):
        for code in ("read_failed", "context_mismatch", "database_count_limit", "consistency_unverified"):
            response = {"ok": True, "result": {"success": False, "complete": False, "page_complete": False,
                        "databases": [{"number": 3, "is_read_only": None}], "next_offset": None,
                        "read_errors": [{"path": "databases[0]", "code": code, "message": "native diagnostic"}]}}
            with self.subTest(code=code), patch.object(BridgeClient, "_request", return_value=response):
                actual = self.call({})
                self.assertTrue(actual["isError"])
                self.assertEqual(actual["structuredContent"], response)

    def test_mock_and_other_versions_never_contact_the_adapter(self):
        for settings, code in ((Settings(mode="mock", version="E3D21"), "native_unavailable"),
                               (Settings(mode="bridge", version="E3D31"), "unsupported_version")):
            with self.subTest(code=code), patch.object(BridgeClient, "_request") as request:
                response = self.call({}, settings)
                self.assertTrue(response["isError"])
                result = response["structuredContent"]["result"]
                self.assertFalse(result["available"])
                self.assertIsNone(result["total_count"])
                self.assertEqual(result["read_errors"][0]["code"], code)
                request.assert_not_called()

    def test_transport_fault_never_retries_or_adds_mutation_uncertainty(self):
        diagnostics = {"state": {"recovery_required": True, "database_ready": False}}
        with patch.object(BridgeClient, "_request", side_effect=BridgeError("closed", transport_error=True,
                          diagnostics=diagnostics)) as request, patch.object(BridgeClient, "command") as command, \
                patch.object(BridgeClient, "capture_view") as capture:
            result = self.call({})
            self.assertTrue(result["isError"])
            self.assertEqual(result["structuredContent"]["state"], diagnostics["state"])
            self.assertNotIn("outcome_uncertain", json.dumps(result))
            request.assert_called_once()
            command.assert_not_called()
            capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()

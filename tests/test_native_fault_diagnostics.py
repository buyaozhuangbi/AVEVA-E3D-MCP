from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.protocol import McpServer


class NativeFaultDiagnosticsTests(unittest.TestCase):
    def test_native_failure_survives_http_and_mcp_without_replaying_request(self):
        client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        state = {"busy": False, "database_ready": False, "recovery_required": True,
                 "native_database_fault": {"has_fault": True, "code": 538}}
        body = json.dumps({"ok": False, "error": "Native fault", "state": state,
                           "unrelated": "not forwarded"}).encode()
        error = HTTPError("http://127.0.0.1:8766", 500, "failure", {}, io.BytesIO(body))
        server = McpServer("test", "1")
        server.add_tool("read", "test", {"type": "object"}, lambda _: client.project())
        with patch("urllib.request.urlopen", side_effect=error) as request:
            response = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                         "params": {"name": "read", "arguments": {}}})["result"]
        self.assertTrue(response["isError"])
        self.assertEqual(response["structuredContent"]["state"], state)
        self.assertNotIn("unrelated", response["structuredContent"])
        request.assert_called_once()

    def test_dismissal_never_clears_a_native_fault_or_unknown_health(self):
        client = BridgeClient(Settings(mode="bridge", bridge_timeout=0.2))
        for state, settled in (({"busy": False}, False),
                               ({"busy": False, "database_ready": False, "recovery_required": True}, False),
                               ({"busy": False, "database_ready": True, "recovery_required": False}, True)):
            with self.subTest(state=state), patch.object(client, "_request", return_value={"ok": True}), \
                    patch.object(client, "_wait_for_idle", return_value={"state": state}):
                result = client.cancel_command()
                self.assertEqual(result["settled"], settled)
                self.assertTrue(result["command_idle"])
                self.assertEqual(result["final_state"], state)

    def test_unstructured_errors_retain_legacy_wire_format(self):
        server = McpServer("test", "1")
        def fail(_):
            raise BridgeError("plain failure")
        server.add_tool("read", "test", {"type": "object"}, fail)
        result = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                   "params": {"name": "read"}})["result"]
        self.assertTrue(result["isError"])
        self.assertNotIn("structuredContent", result)


if __name__ == "__main__":
    unittest.main()

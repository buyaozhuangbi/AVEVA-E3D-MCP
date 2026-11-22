from __future__ import annotations

import json
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class ViewClippingStateToolTests(unittest.TestCase):
    def _rpc(self, server, method: str, params: dict) -> dict:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        output = StringIO()
        with patch("sys.stdout", output):
            server.serve([request + "\n"])
        response = json.loads(output.getvalue())
        self.assertNotIn("error", response, response)
        return response["result"]

    def _call(self, server, arguments: dict) -> dict:
        return self._rpc(server, "tools/call", {
            "name": "e3d_view_clipping_state", "arguments": arguments,
        })

    def test_tool_is_read_only_and_has_no_arguments(self) -> None:
        server = create_server(Settings(mode="bridge", allow_commands=False))
        tools = {tool["name"]: tool for tool in self._rpc(server, "tools/list", {})["tools"]}
        tool = tools["e3d_view_clipping_state"]
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertEqual(tool["inputSchema"], {
            "type": "object", "properties": {}, "additionalProperties": False,
        })

    def test_arguments_are_rejected_before_native_read(self) -> None:
        server = create_server(Settings(mode="bridge", allow_commands=False))
        with patch.object(BridgeClient, "view_clipping_state") as read:
            response = self._call(server, {"include_planes": True})
        self.assertTrue(response["isError"])
        read.assert_not_called()

    def test_partial_native_result_is_preserved_as_mcp_error(self) -> None:
        server = create_server(Settings(mode="bridge", allow_commands=False))
        payload = {"ok": True, "result": {
            "success": False,
            "available": True,
            "complete": False,
            "status": "partial",
            "clipping_enabled": True,
            "box_data": None,
            "box_faces": [{"face": "W", "defined": None, "active": None}],
            "read_errors": {"box_data": "SDK getter failed"},
            "view_unchanged": True,
        }}
        with patch.object(BridgeClient, "view_clipping_state", return_value=payload) as read:
            response = self._call(server, {})
        read.assert_called_once_with()
        self.assertTrue(response["isError"])
        self.assertEqual(response["structuredContent"], payload)

    def test_mock_mode_reports_unavailable_without_fabricating_clipping(self) -> None:
        server = create_server(Settings(mode="mock", version="E3D21"))
        response = self._call(server, {})
        self.assertTrue(response["isError"])
        envelope = response["structuredContent"]
        result = envelope["result"]
        self.assertFalse(result["success"])
        self.assertFalse(result["available"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["error_code"], "native_unavailable")
        self.assertEqual(envelope["version"], "E3D21")
        self.assertIsNone(result["clipping_enabled"])
        self.assertEqual(result["box_faces"], [])
        self.assertIn("mock mode", result["side_effects"])

    def test_bridge_route_uses_selected_version(self) -> None:
        client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.view_clipping_state(version="E3D21")
        request.assert_called_once_with(
            "GET", "/api/v1/view/clipping-state", version="E3D21",
        )

    def test_other_versions_report_explicit_unavailability_without_http(self) -> None:
        client = BridgeClient(Settings(mode="bridge", version="E3D31"))
        with patch.object(client, "_request") as request:
            response = client.view_clipping_state(version="E3D31")
        request.assert_not_called()
        result = response["result"]
        self.assertEqual(response["version"], "E3D31")
        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(result["source"], "unsupported")
        self.assertEqual(result["error_code"], "unsupported_version")
        self.assertFalse(result["available"])
        self.assertIn("E3D31 is unsupported", result["side_effects"])
        self.assertNotIn("mock mode", result["side_effects"])

    def test_default_version_fails_closed_without_http(self) -> None:
        client = BridgeClient(Settings(mode="bridge"))
        with patch.object(client, "_request") as request:
            response = client.view_clipping_state()
        request.assert_not_called()
        result = response["result"]
        self.assertEqual(response["version"], "default")
        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(result["error_code"], "unsupported_version")


if __name__ == "__main__":
    unittest.main()

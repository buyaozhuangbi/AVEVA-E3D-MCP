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


class PanelMeshToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(Settings(mode="bridge", allow_commands=False))

    def _rpc(self, method: str, params: dict) -> dict:
        output = StringIO()
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        with patch("sys.stdout", output):
            self.server.serve([request + "\n"])
        response = json.loads(output.getvalue())
        self.assertNotIn("error", response, response)
        return response["result"]

    def _call(self, arguments: dict) -> dict:
        return self._rpc("tools/call", {"name": "e3d_panel_mesh", "arguments": arguments})

    def test_tool_schema_is_read_only_with_explicit_coordinate_space(self) -> None:
        tools = {row["name"]: row for row in self._rpc("tools/list", {})["tools"]}
        tool = tools["e3d_panel_mesh"]
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertTrue(tool["annotations"]["idempotentHint"])
        self.assertEqual(tool["inputSchema"]["required"], ["refno"])
        self.assertEqual(set(tool["inputSchema"]["properties"]), {"refno", "coordinate_space"})
        self.assertEqual(tool["inputSchema"]["properties"]["coordinate_space"]["enum"], ["panel_local", "WORLD"])

    def test_reads_panel_once_and_attaches_mesh(self) -> None:
        geometry = {"success": True, "complete": True, "topology_valid": True, "loops": []}
        native = {"ok": True, "version": "E3D21", "result": geometry}
        mesh = {"success": True, "available": True, "complete": True, "triangles": [[0, 1, 2]]}
        with patch.object(BridgeClient, "panel_geometry", return_value=native) as read, \
                patch("aveva_e3d_mcp.tools.derive_panel_mesh", return_value=mesh) as derive:
            result = self._call({"refno": " =1/2 "})
        self.assertFalse(result.get("isError", False), result)
        self.assertEqual(result["structuredContent"]["result"]["derived_mesh"], mesh)
        self.assertTrue(result["structuredContent"]["result"]["available"])
        read.assert_called_once_with("=1/2", include_world=False)
        derive.assert_called_once_with(geometry, coordinate_space="panel_local")

    def test_world_request_uses_native_world_positions_in_same_panel_read(self) -> None:
        geometry = {"success": True, "complete": True, "topology_valid": True,
                    "world_positions_requested": True, "world_positions_complete": True, "loops": []}
        native = {"ok": True, "version": "E3D21", "result": geometry}
        mesh = {"success": True, "available": True, "complete": True, "coordinate_space": "WORLD"}
        with patch.object(BridgeClient, "panel_geometry", return_value=native) as read, \
                patch("aveva_e3d_mcp.tools.derive_panel_mesh", return_value=mesh) as derive:
            result = self._call({"refno": "=1/2", "coordinate_space": "WORLD"})
        self.assertFalse(result.get("isError", False), result)
        self.assertTrue(result["structuredContent"]["result"]["available"])
        read.assert_called_once_with("=1/2", include_world=True)
        derive.assert_called_once_with(geometry, coordinate_space="WORLD")

    def test_bridge_failure_is_forwarded_without_derivation(self) -> None:
        failure = {"ok": False, "error": "bridge unavailable"}
        with patch.object(BridgeClient, "panel_geometry", return_value=failure) as read, \
                patch("aveva_e3d_mcp.tools.derive_panel_mesh") as derive:
            result = self._call({"refno": "/PANEL"})
        self.assertTrue(result.get("isError", False), result)
        self.assertEqual(result["structuredContent"], failure)
        read.assert_called_once_with("/PANEL", include_world=False)
        derive.assert_not_called()

    def test_rejects_unknown_controls_before_native_read(self) -> None:
        with patch.object(BridgeClient, "panel_geometry") as read:
            result = self._call({"refno": "/PANEL", "include_world": True})
        self.assertTrue(result.get("isError", False), result)
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()

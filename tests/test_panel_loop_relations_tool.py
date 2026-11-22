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


class PanelLoopRelationsToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(Settings(mode="bridge", allow_commands=False))

    def _rpc(self, method: str, params: dict) -> dict:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        output = StringIO()
        with patch("sys.stdout", output):
            self.server.serve([request + "\n"])
        response = json.loads(output.getvalue())
        self.assertNotIn("error", response, response)
        return response["result"]

    def _call(self, arguments: dict) -> dict:
        return self._rpc("tools/call", {"name": "e3d_panel_loop_relations", "arguments": arguments})

    def test_tool_is_explicit_read_only_and_has_a_closed_schema(self) -> None:
        tools = {row["name"]: row for row in self._rpc("tools/list", {})["tools"]}
        tool = tools["e3d_panel_loop_relations"]
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertTrue(tool["annotations"]["idempotentHint"])
        self.assertEqual(tool["inputSchema"]["required"], ["refno"])
        self.assertEqual(set(tool["inputSchema"]["properties"]), {"refno"})
        self.assertFalse(tool["inputSchema"]["additionalProperties"])

    def test_adds_analysis_to_one_native_read_without_mutating_its_evidence(self) -> None:
        geometry = {"success": True, "complete": True, "element": {"refno": "=12/34"}, "loops": []}
        response = {"ok": True, "version": "E3D21", "result": geometry}
        analysis = {"complete": True, "status": "no_pairs", "pair_count": 0, "pairs": []}
        with patch.object(BridgeClient, "panel_geometry", return_value=response) as read, \
                patch("aveva_e3d_mcp.tools.analyze_panel_loop_relations", return_value=analysis) as analyze:
            result = self._call({"refno": " =12/34 "})
        self.assertFalse(result.get("isError", False), result)
        read.assert_called_once_with("=12/34")
        analyze.assert_called_once_with(geometry)
        structured = result["structuredContent"]
        self.assertEqual(structured["result"]["loop_relationships"], analysis)
        self.assertEqual(structured["result"]["loops"], [])
        self.assertNotIn("loop_relationships", geometry)

    def test_forwards_native_failure_without_fabricating_an_analysis(self) -> None:
        failure = {"ok": False, "error": "bridge unavailable"}
        with patch.object(BridgeClient, "panel_geometry", return_value=failure) as read, \
                patch("aveva_e3d_mcp.tools.analyze_panel_loop_relations") as analyze:
            result = self._call({"refno": "/PANEL"})
        self.assertTrue(result.get("isError", False))
        read.assert_called_once_with("/PANEL")
        analyze.assert_not_called()

    def test_partial_geometry_keeps_native_failure_and_partial_pair_evidence(self) -> None:
        geometry = {"success": False, "complete": False, "topology_valid": None,
                    "partial": True, "loops": [{"loop_index": 0}]}
        response = {"ok": True, "version": "E3D21", "result": geometry}
        analysis = {"available": True, "complete": False, "status": "partial",
                    "known_pair_count": 1, "unknown_pair_count": 2,
                    "pairs": [{"relation": "disjoint", "complete": True}]}
        with patch.object(BridgeClient, "panel_geometry", return_value=response), \
                patch("aveva_e3d_mcp.tools.analyze_panel_loop_relations", return_value=analysis):
            result = self._call({"refno": "=12/34"})
        self.assertTrue(result.get("isError", False), result)
        structured = result["structuredContent"]["result"]
        self.assertFalse(structured["success"])
        self.assertFalse(structured["complete"])
        self.assertIsNone(structured["topology_valid"])
        self.assertEqual(structured["loop_relationships"]["pairs"][0]["relation"], "disjoint")

    def test_rejects_unknown_arguments_before_native_read(self) -> None:
        with patch.object(BridgeClient, "panel_geometry") as read:
            result = self._call({"refno": "/PANEL", "include_world": True})
        self.assertTrue(result.get("isError", False))
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()

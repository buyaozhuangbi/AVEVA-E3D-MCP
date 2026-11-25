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


class PrimitiveMeshToolTests(unittest.TestCase):
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
        return self._rpc("tools/call", {"name": "e3d_primitive_mesh", "arguments": arguments})

    def test_tool_is_read_only_explicit_bounded_and_discoverable(self) -> None:
        tools = {row["name"]: row for row in self._rpc("tools/list", {})["tools"]}
        self.assertEqual(len(tools), 126)
        tool = tools["e3d_primitive_mesh"]
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertTrue(tool["annotations"]["idempotentHint"])
        self.assertFalse(tool["annotations"]["destructiveHint"])
        schema = tool["inputSchema"]
        self.assertEqual(schema["required"], ["refno"])
        self.assertEqual(set(schema["properties"]), {"refno", "radial_segments", "coordinate_space", "chord_tolerance_mm"})
        self.assertEqual(schema["properties"]["chord_tolerance_mm"]["type"], "number")
        self.assertEqual(schema["properties"]["chord_tolerance_mm"]["exclusiveMinimum"], 0)
        self.assertNotIn("default", schema["properties"]["chord_tolerance_mm"])
        self.assertNotIn("chord_tolerance_mm", schema["required"])
        self.assertEqual(schema["properties"]["radial_segments"]["minimum"], 12)
        self.assertEqual(schema["properties"]["radial_segments"]["maximum"], 128)
        self.assertEqual(schema["properties"]["radial_segments"]["default"], 48)
        self.assertEqual(schema["properties"]["coordinate_space"]["enum"], ["primitive_local", "WORLD"])
        self.assertEqual(schema["properties"]["coordinate_space"]["default"], "primitive_local")
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("DISH with RADI=0", tool["description"])
        self.assertIn("nonzero RADI is rejected", tool["description"])

    def test_reads_native_primitive_once_and_attaches_derived_mesh(self) -> None:
        geometry = {"success": True, "complete": True, "shape": "BOX", "negative": False,
                    "negative_children": [], "dimensions": {"XLEN": 10, "YLEN": 20, "ZLEN": 30}}
        native = {"ok": True, "version": "E3D21", "result": geometry}
        mesh = {"success": True, "available": True, "coordinate_frame": "primitive-local", "triangles": [[0, 1, 2]]}
        with patch.object(BridgeClient, "primitive_geometry", return_value=native) as read, \
                patch("aveva_e3d_mcp.tools.derive_primitive_mesh", return_value=mesh) as derive, \
                patch.object(BridgeClient, "update_primitive") as update, \
                patch.object(BridgeClient, "capture_view") as capture:
            result = self._call({"refno": " =1/2 ", "radial_segments": 64})
        self.assertFalse(result.get("isError", False), result)
        structured = result["structuredContent"]
        self.assertEqual(structured["version"], "E3D21")
        self.assertEqual(structured["result"]["source_geometry"]["shape"], "BOX")
        self.assertEqual(structured["result"]["derived_mesh"], mesh)
        self.assertTrue(structured["result"]["available"])
        read.assert_called_once_with("=1/2")
        derive.assert_called_once_with(geometry, radial_segments=64, coordinate_space="primitive_local")
        update.assert_not_called()
        capture.assert_not_called()

    def test_dish_request_is_read_only_and_derives_from_one_native_read(self) -> None:
        geometry = {"success": True, "complete": True, "shape": "DISH", "negative": False,
                    "negative_children": [], "children": [],
                    "dimensions": {"DIAM": 600, "HEIG": 150, "RADI": 0}}
        native = {"ok": True, "version": "E3D21", "result": geometry}
        mesh = {"success": True, "available": True, "closed_manifold": True,
                "native_tessellation": False, "cuts_applied": False, "triangles": [[0, 1, 2]]}
        with patch.object(BridgeClient, "primitive_geometry", return_value=native) as read, \
                patch("aveva_e3d_mcp.tools.derive_primitive_mesh", return_value=mesh) as derive, \
                patch.object(BridgeClient, "update_primitive") as update, \
                patch.object(BridgeClient, "capture_view") as capture:
            result = self._call({"refno": "=1/54", "radial_segments": 24})
        self.assertFalse(result.get("isError", False), result)
        attached = result["structuredContent"]["result"]
        self.assertTrue(attached["available"])
        self.assertEqual(attached["source_geometry"], geometry)
        self.assertEqual(attached["derived_mesh"], mesh)
        read.assert_called_once_with("=1/54")
        derive.assert_called_once_with(geometry, radial_segments=24, coordinate_space="primitive_local")
        update.assert_not_called()
        capture.assert_not_called()

    def test_bridge_failure_is_forwarded_without_mesh_claim(self) -> None:
        failure = {"ok": False, "error": "bridge unavailable", "retry_safe": True}
        with patch.object(BridgeClient, "primitive_geometry", return_value=failure) as read, \
                patch("aveva_e3d_mcp.tools.derive_primitive_mesh") as derive:
            result = self._call({"refno": "/BOX"})
        self.assertTrue(result.get("isError", False), result)
        self.assertEqual(result["structuredContent"], failure)
        read.assert_called_once_with("/BOX")
        derive.assert_not_called()

    def test_partial_native_result_keeps_native_and_mesh_failure_evidence(self) -> None:
        geometry = {"success": False, "complete": False, "shape": "CYLI", "negative": False,
                    "negative_children": [], "read_errors": {"DIAM": "missing"}}
        native = {"ok": True, "version": "E3D21", "result": geometry}
        failure = {"success": False, "available": False, "reason": "incomplete_native_geometry",
                   "vertices": [], "triangles": []}
        with patch.object(BridgeClient, "primitive_geometry", return_value=native), \
                patch("aveva_e3d_mcp.tools.derive_primitive_mesh", return_value=failure):
            result = self._call({"refno": "=1/2"})
        self.assertTrue(result.get("isError", False), result)
        structured = result["structuredContent"]["result"]
        self.assertFalse(structured["success"])
        self.assertFalse(structured["complete"])
        self.assertEqual(structured["source_geometry"]["read_errors"], {"DIAM": "missing"})
        self.assertEqual(structured["derived_mesh"], failure)

    def test_rejects_bad_references_segments_and_unknown_controls_before_native_read(self) -> None:
        invalid = [
            {}, {"refno": "CE"}, {"refno": "/BOX", "radial_segments": 11},
            {"refno": "/BOX", "radial_segments": 129}, {"refno": "/BOX", "radial_segments": True},
            {"refno": "/BOX", "coordinate_space": "OWNER"},
            {"refno": "/BOX", "extra": 1},
        ]
        with patch.object(BridgeClient, "primitive_geometry") as read:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self._call(arguments).get("isError", False))
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()

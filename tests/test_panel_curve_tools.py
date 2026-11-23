import base64
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.panel_curve_workflow import PanelCurveReadError
from aveva_e3d_mcp.tools import create_server


class PanelCurveToolTests(unittest.TestCase):
    def setUp(self):
        self.server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))

    def dispatch(self, method, params):
        return self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})["result"]

    def call(self, name, arguments):
        return self.dispatch("tools/call", {"name": name, "arguments": arguments})

    def test_both_read_only_tools_are_discoverable_with_distinct_coordinate_defaults(self):
        tools = {row["name"]: row for row in self.dispatch("tools/list", {})["tools"]}
        self.assertEqual(len(tools), 119)
        for name, default in (("e3d_panel_curve_boundary", "panel_local"),
                              ("e3d_project_panel_curve_drawing", "WORLD")):
            tool = tools[name]
            self.assertTrue(tool["annotations"]["readOnlyHint"])
            self.assertEqual(tool["inputSchema"]["required"], ["refno"])
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
            self.assertEqual(tool["inputSchema"]["properties"]["coordinate_space"]["default"], default)

    def test_invalid_inputs_are_rejected_before_context_or_native_reads(self):
        cases = [{"refno": ""}, {"refno": "CE"}, {"coordinate_space": "local"},
                 {"chord_tolerance_mm": True}, {"chord_tolerance_mm": None},
                 {"chord_tolerance_mm": 0}, {"chord_tolerance_mm": 10 ** 400},
                 {"chord_tolerance_mm": float("nan")}, {"chord_tolerance_mm": "0.1"}, {"surprise": 1}]
        for name in ("e3d_panel_curve_boundary", "e3d_project_panel_curve_drawing"):
            for overrides in cases:
                with self.subTest(name=name, overrides=overrides), patch("aveva_e3d_mcp.tools.read_panel_curve_source") as read:
                    response = self.call(name, {"refno": "=1/2", **overrides})
                    self.assertTrue(response.get("isError", False), response)
                    read.assert_not_called()
        for overrides in ({"views": [[]]}, {"views": ["TOP", "TOP"]}, {"views": "TOP"},
                          {"include_svg": 1}, {"include_dxf": "true"}, {"preview_width": True},
                          {"preview_width": 2048, "preview_height": 2048}):
            with self.subTest(overrides=overrides), patch("aveva_e3d_mcp.tools.read_panel_curve_source") as read:
                response = self.call("e3d_project_panel_curve_drawing", {"refno": "=1/2", **overrides})
                self.assertTrue(response.get("isError", False), response)
                read.assert_not_called()

    def test_failed_native_observations_preserve_evidence_and_return_no_image(self):
        evidence = {"atomic_snapshot": False, "native_reads": 1,
                    "native_geometry": {"complete": False, "read_errors": {"P0": "unavailable"}}}
        with patch("aveva_e3d_mcp.tools.read_panel_curve_source", side_effect=PanelCurveReadError("source_changed", evidence)):
            response = self.call("e3d_project_panel_curve_drawing", {"refno": "=1/2"})
        self.assertTrue(response.get("isError", False))
        result = response["structuredContent"]["result"]
        self.assertEqual(result["source_evidence"], evidence)
        self.assertIsNone(result["drawing"])
        self.assertFalse(any(row["type"] == "image" for row in response["content"]))

    def test_read_tool_forwards_precision_and_preserves_native_false(self):
        evidence = {"resolved_refno": "=1/2", "context": {"before": {"version": "E3D21"}}}
        boundary = {"success": True, "available": True, "complete": True,
                    "native_boundary_points_complete": False, "native_existence_verified": False}
        with patch("aveva_e3d_mcp.tools.read_panel_curve_source", return_value=({}, evidence)) as read, \
             patch("aveva_e3d_mcp.panel_curve_boundary.derive_panel_curve_boundary", return_value=deepcopy(boundary)) as derive:
            response = self.call("e3d_panel_curve_boundary", {"refno": " =1/2 ", "chord_tolerance_mm": 0.02})
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(read.call_args.args[1], "=1/2")
        self.assertEqual(read.call_args.kwargs["coordinate_space"], "panel_local")
        derive.assert_called_once_with({}, coordinate_space="panel_local", chord_tolerance_mm=0.02)
        self.assertEqual(response["structuredContent"]["result"]["curve_boundary"], boundary)
        self.assertFalse(response["structuredContent"]["result"]["atomic_snapshot"])
        self.assertFalse(any(row["type"] == "image" for row in response["content"]))

    def test_drawing_returns_same_response_png_and_optional_exports(self):
        evidence = {"resolved_refno": "=1/2", "context": {"before": {"version": "E3D21"}}}
        boundary = {"success": True, "available": True, "complete": True}
        drawing = {"svg": "<svg/>", "dxf": "LINE", "native_complete": False}
        png = b"native-test-image-payload"
        with patch("aveva_e3d_mcp.tools.read_panel_curve_source", return_value=({}, evidence)), \
             patch("aveva_e3d_mcp.panel_curve_boundary.derive_panel_curve_boundary", return_value=boundary), \
             patch("aveva_e3d_mcp.panel_curve_drawing.build_panel_curve_drawing", return_value=drawing) as build, \
             patch("aveva_e3d_mcp.panel_curve_drawing.render_panel_curve_png", return_value=png) as render:
            response = self.call("e3d_project_panel_curve_drawing", {"refno": "=1/2", "views": ["TOP"],
                                                                   "include_svg": True, "include_dxf": True})
        self.assertFalse(response.get("isError", False), response)
        build.assert_called_once_with(boundary, views=["TOP"], include_svg=True, include_dxf=True)
        render.assert_called_once_with(drawing, width=1200, height=440)
        image = next(row for row in response["content"] if row["type"] == "image")
        self.assertEqual(base64.b64decode(image["data"]), png)
        self.assertEqual(response["structuredContent"]["result"]["drawing"], drawing)

    def test_curve_rejection_cannot_fall_back_to_control_polygon(self):
        evidence = {"resolved_refno": "=1/2", "context": {"before": {"version": "E3D21"}}}
        boundary = {"success": False, "available": False, "complete": False, "errors": ["import_tagged"]}
        with patch("aveva_e3d_mcp.tools.read_panel_curve_source", return_value=({}, evidence)), \
             patch("aveva_e3d_mcp.panel_curve_boundary.derive_panel_curve_boundary", return_value=boundary), \
             patch("aveva_e3d_mcp.tools.build_panel_boundary_drawing") as control:
            response = self.call("e3d_project_panel_curve_drawing", {"refno": "=1/2"})
        self.assertTrue(response.get("isError", False))
        self.assertIsNone(response["structuredContent"]["result"]["drawing"])
        self.assertEqual(response["structuredContent"]["result"]["curve_boundary"], boundary)
        control.assert_not_called()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import base64
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp import tools as tools_module
from aveva_e3d_mcp.tools import create_server


class ProjectElementDrawingToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))

    def rpc(self, method: str, params: dict) -> dict:
        return self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})["result"]

    def call(self, **args: object) -> dict:
        return self.rpc("tools/call", {"name": "e3d_project_element_drawing", "arguments": args})

    @staticmethod
    def geometry(refno: str = "=1/2", name: str = "/BOX", shape: str = "BOX") -> dict:
        dimensions = {"XLEN": 10, "YLEN": 20, "ZLEN": 30}
        if shape == "CYLI":
            dimensions = {"DIAM": 10, "HEIG": 30}
        return {
            "success": True, "complete": True, "shape": shape, "negative": False,
            "negative_children": [], "children": [], "read_errors": {},
            "element": {"refno": refno, "type": shape, "name": name},
            "dimensions": dimensions,
            "frame": {"world": {"position": [0, 0, 0], "orientation": {
                "x_axis": [1, 0, 0], "y_axis": [0, 1, 0], "z_axis": [0, 0, 1],
            }}},
        }

    def test_catalog_and_box_returns_bounded_three_view_svg_without_writes(self) -> None:
        catalog = {row["name"]: row for row in self.rpc("tools/list", {})["tools"]}
        tool = catalog["e3d_project_element_drawing"]
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertEqual(tool["inputSchema"]["properties"]["views"]["default"], ["FRONT", "TOP", "SIDE"])
        self.assertEqual(tool["inputSchema"]["properties"]["radial_segments"]["maximum"], 99)
        self.assertTrue(tool["inputSchema"]["properties"]["hidden_line_removal"]["default"])
        self.assertFalse(tool["inputSchema"]["properties"]["show_hidden_lines"]["default"])
        self.assertFalse(tool["inputSchema"]["properties"]["include_svg"]["default"])
        self.assertFalse(tool["inputSchema"]["properties"]["include_dxf"]["default"])
        self.assertEqual(tool["inputSchema"]["properties"]["preview_width"]["default"], 1200)
        self.assertEqual(tool["inputSchema"]["properties"]["preview_height"]["default"], 440)

        with patch.object(BridgeClient, "primitive_geometry", return_value={
                "ok": True, "version": "E3D21", "result": self.geometry()}) as read, \
                patch.object(BridgeClient, "update_primitive") as update, \
                patch.object(BridgeClient, "capture_view") as capture:
            response = self.call(refno="=1/2", expected_type="BOX")

        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertTrue(result["success"], result)
        self.assertEqual(result["resolved_refno"], "=1/2")
        drawing = result["drawing"]
        self.assertEqual([view["name"] for view in drawing["views"]], ["FRONT", "TOP", "SIDE"])
        self.assertEqual(drawing["source_bounds"]["dimensions"], [10.0, 20.0, 30.0])
        image = next(item for item in response["content"] if item["type"] == "image")
        self.assertEqual(image["mimeType"], "image/png")
        png = base64.b64decode(image["data"])
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertNotIn("svg", drawing)
        self.assertTrue(drawing["hidden_line_removal"])
        self.assertTrue(drawing["occlusion_checked"])
        self.assertFalse(drawing["hole_semantics_inferred"])
        self.assertNotIn("source_geometry", result)
        self.assertNotIn("derived_mesh", result)
        self.assertNotIn("triangle_normals", str(result))
        read.assert_called_once_with("=1/2")
        update.assert_not_called()
        capture.assert_not_called()

    def test_svg_can_be_opted_into_without_changing_png_preview(self) -> None:
        with patch.object(BridgeClient, "primitive_geometry", return_value={
                "ok": True, "version": "E3D21", "result": self.geometry()}):
            response = self.call(refno="=1/2", expected_type="BOX", include_svg=True,
                                 hidden_line_removal=False)
        result = response["structuredContent"]["result"]
        self.assertIn("svg", result["drawing"])
        self.assertTrue(result["drawing"]["svg"].startswith("<svg"))
        image = next(item for item in response["content"] if item["type"] == "image")
        self.assertEqual(image["mimeType"], "image/png")

    def test_dxf_can_be_opted_into_as_r2000_mm_text(self) -> None:
        with patch.object(BridgeClient, "primitive_geometry", return_value={
                "ok": True, "version": "E3D21", "result": self.geometry()}):
            response = self.call(refno="=1/2", expected_type="BOX", include_dxf=True)
        result = response["structuredContent"]["result"]
        dxf = result["drawing"].get("dxf")
        self.assertIsInstance(dxf, str)
        self.assertIn("9\n$ACADVER\n1\nAC1015", dxf)
        self.assertIn("9\n$INSUNITS\n70\n4", dxf)
        self.assertNotIn("dxf", response["structuredContent"]["result"].get("source_mesh", {}))

    def test_hidden_line_flag_reaches_classified_preview_without_extra_native_reads(self) -> None:
        geometry = self.geometry()
        geometry["frame"]["world"]["orientation"] = {
            "x_axis": [.6, .8, 0], "y_axis": [-.8, .6, 0], "z_axis": [0, 0, 1],
        }
        with patch.object(BridgeClient, "primitive_geometry", return_value={
                "ok": True, "version": "E3D21", "result": geometry}) as read:
            response = self.call(refno="=1/2", expected_type="BOX", show_hidden_lines=True,
                                 include_svg=True, include_dxf=True, views=["FRONT"])
        self.assertFalse(response.get("isError", False), response)
        drawing = response["structuredContent"]["result"]["drawing"]
        self.assertTrue(drawing["show_hidden_lines"])
        self.assertTrue(drawing["occlusion_checked"])
        self.assertTrue(any(line["visibility"] == "hidden" for line in drawing["views"][0]["lines"]))
        self.assertIn('stroke-dasharray="6 4"', drawing["svg"])
        self.assertIn("2\nE3D_HIDDEN", drawing["dxf"])
        self.assertIn("8\nFRONT_HIDDEN", drawing["dxf"])
        self.assertTrue(any(item["type"] == "image" for item in response["content"]))
        read.assert_called_once_with("=1/2")

    def test_preview_dimensions_are_configurable_and_reported_in_png(self) -> None:
        with patch.object(BridgeClient, "primitive_geometry", return_value={
                "ok": True, "version": "E3D21", "result": self.geometry()}):
            response = self.call(refno="=1/2", expected_type="BOX", preview_width=800, preview_height=600)
        self.assertFalse(response.get("isError", False), response)
        image = next(item for item in response["content"] if item["type"] == "image")
        png = base64.b64decode(image["data"])
        self.assertEqual(int.from_bytes(png[16:20], "big"), 800)
        self.assertEqual(int.from_bytes(png[20:24], "big"), 600)

    def test_preview_pixel_budget_is_checked_before_native_read(self) -> None:
        with patch.object(BridgeClient, "primitive_geometry") as read:
            for args in (
                {"preview_width": 2048, "preview_height": 2048},
                {"preview_width": 65.0},
                {"preview_height": True},
                {"preview_width": 63},
            ):
                with self.subTest(args=args):
                    response = self.call(refno="=1/2", expected_type="BOX", **args)
                    self.assertTrue(response.get("isError", False), response)
        read.assert_not_called()

    def test_cylinder_resolution_changes_only_derived_linework_and_is_bounded(self) -> None:
        with patch.object(BridgeClient, "primitive_geometry", return_value={
                "ok": True, "version": "E3D21", "result": self.geometry(shape="CYLI")}) as read:
            low = self.call(refno="=1/2", expected_type="CYLI", radial_segments=12, views=["TOP"])
            high = self.call(refno="=1/2", expected_type="CYLI", radial_segments=24, views=["TOP"])
        self.assertFalse(low.get("isError", False), low)
        self.assertFalse(high.get("isError", False), high)
        low_result = low["structuredContent"]["result"]
        high_result = high["structuredContent"]["result"]
        self.assertLess(low_result["drawing"]["segment_count"], high_result["drawing"]["segment_count"])
        self.assertEqual(read.call_count, 2)

    def test_identity_mismatch_and_negative_geometry_fail_without_drawing(self) -> None:
        mismatched = self.geometry(refno="=1/9")
        with patch.object(BridgeClient, "primitive_geometry", return_value={
                "ok": True, "version": "E3D21", "result": mismatched}) as read:
            response = self.call(refno="=1/2", expected_type="BOX")
        self.assertTrue(response.get("isError", False))
        result = response["structuredContent"]["result"]
        self.assertEqual(result["error_code"], "element_reference_mismatch")
        self.assertIsNone(result["drawing"])
        self.assertNotIn("vertices", str(result))
        read.assert_called_once_with("=1/2")

        negative = self.geometry()
        negative["negative"] = True
        with patch.object(BridgeClient, "primitive_geometry", return_value={
                "ok": True, "version": "E3D21", "result": negative}):
            rejected = self.call(refno="=1/2", expected_type="BOX")
        self.assertTrue(rejected.get("isError", False))
        self.assertIsNone(rejected["structuredContent"]["result"]["drawing"])

    def test_mesh_derivation_failure_preserves_specific_error_without_partial_svg(self) -> None:
        with patch.object(BridgeClient, "primitive_geometry", return_value={
                "ok": True, "version": "E3D21", "result": self.geometry()}), \
                patch.object(tools_module, "derive_primitive_mesh", return_value={
                    "success": False, "available": False, "complete": True,
                    "errors": ["unsupported_native_shape_parameters"],
                }):
            response = self.call(refno="=1/2", expected_type="BOX")
        self.assertTrue(response.get("isError", False))
        result = response["structuredContent"]["result"]
        self.assertEqual(result["error_code"], "unsupported_native_shape_parameters")
        self.assertEqual(result["source_mesh"]["errors"], ["unsupported_native_shape_parameters"])
        self.assertIsNone(result["drawing"])

    def test_invalid_options_are_rejected_before_native_read(self) -> None:
        with patch.object(BridgeClient, "primitive_geometry") as read, \
                patch.object(BridgeClient, "panel_geometry") as panel:
            for args in (
                {"refno": "=1/2"},
                {"refno": "=1/2", "expected_type": "PANEL"},
                {"refno": "=1/2", "expected_type": "BOX", "views": ["FRONT", "FRONT"]},
                {"refno": "=1/2", "expected_type": "BOX", "feature_angle_degrees": 181},
                {"refno": "=1/2", "expected_type": "BOX", "radial_segments": 24},
                {"refno": "=1/2", "expected_type": "CYLI", "radial_segments": 100},
                {"refno": "=1/2", "expected_type": "BOX", "hidden_line_removal": "false"},
                {"refno": "=1/2", "expected_type": "BOX", "show_hidden_lines": "true"},
                {"refno": "=1/2", "expected_type": "BOX", "show_hidden_lines": True, "hidden_line_removal": False},
                {"refno": "=1/2", "expected_type": "BOX", "include_svg": "true"},
                {"refno": "=1/2", "expected_type": "BOX", "extra": True},
            ):
                with self.subTest(args=args):
                    self.assertTrue(self.call(**args).get("isError", False))
        read.assert_not_called()
        panel.assert_not_called()


if __name__ == "__main__":
    unittest.main()

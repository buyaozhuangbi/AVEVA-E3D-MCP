"""Read-only element-section MCP contracts and native-failure isolation."""

from __future__ import annotations

import base64
import copy
import math
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp import tools as tools_module
from aveva_e3d_mcp.tools import create_server
from tests import test_project_element_drawing_tool as element_fixtures
from tests import test_panel_mesh as panel_fixtures


class SectionElementDrawingToolTests(unittest.TestCase):
    TOOL = "e3d_section_element_drawing"
    PLANE = {"origin": [0, 0, 0], "normal": [0, 0, 1]}

    def setUp(self):
        self.server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))
        for name in ("primitive_geometry", "panel_geometry", "health", "project", "capture_view",
                     "update_primitive", "update_panel", "command", "_request"):
            patcher = patch.object(BridgeClient, name)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        self.primitive_geometry.return_value = self.envelope()

    @staticmethod
    def envelope(geometry=None):
        return {"ok": True, "version": "E3D21", "result": geometry or
                element_fixtures.ProjectElementDrawingToolTests.geometry()}

    def rpc(self, method, params):
        return self.server._dispatch({"jsonrpc": "2.0", "id": 7, "method": method, "params": params})["result"]

    def call(self, **overrides):
        arguments = {"refno": "=1/2", "expected_type": "BOX", "plane": copy.deepcopy(self.PLANE), **overrides}
        return self.rpc("tools/call", {"name": self.TOOL, "arguments": arguments})

    def assert_no_auxiliary_calls(self, *, allow_panel=False):
        for name in (("panel_geometry",) if not allow_panel else ()) + ("health", "project", "capture_view", "update_primitive",
                     "update_panel", "command", "_request"):
            getattr(self, name).assert_not_called()

    @staticmethod
    def png(response):
        block = next(row for row in response["content"] if row["type"] == "image")
        if block["mimeType"] != "image/png":
            raise AssertionError(block["mimeType"])
        return base64.b64decode(block["data"], validate=True)

    def test_catalog_exposes_closed_readonly_plane_and_shared_export_settings(self):
        catalog = {row["name"]: row for row in self.rpc("tools/list", {})["tools"]}
        entry = catalog[self.TOOL]
        self.assertTrue(entry["annotations"]["readOnlyHint"])
        schema = entry["inputSchema"]
        self.assertEqual(set(schema["required"]), {"refno", "expected_type", "plane"})
        properties = schema["properties"]
        self.assertEqual(set(properties["expected_type"]["enum"]), {"PANE", "BOX", "CYLI", "CONE", "DISH"})
        self.assertEqual(properties["coordinate_space"]["default"], "WORLD")
        self.assertEqual(set(properties["coordinate_space"]["enum"]), {"local", "WORLD"})
        plane = properties["plane"]
        self.assertFalse(plane["additionalProperties"])
        self.assertEqual(set(plane["required"]), {"origin", "normal"})
        self.assertEqual(set(plane["properties"]), {"origin", "normal", "up"})
        for vector in plane["properties"].values():
            self.assertEqual(vector["type"], "array")
            self.assertEqual((vector["minItems"], vector["maxItems"]), (3, 3))
            self.assertEqual(vector["items"]["type"], "number")
        projected_sheet = copy.deepcopy(catalog["e3d_project_element_drawing"]["inputSchema"]["properties"]["sheet"])
        self.assertEqual(projected_sheet["properties"].pop("projection_method")["enum"],
                         ["first_angle", "third_angle"])
        self.assertEqual(properties["sheet"], projected_sheet)
        self.assertEqual(properties["radial_segments"]["minimum"], 12)
        self.assertEqual(properties["radial_segments"]["maximum"], 99)
        self.assertEqual(properties["preview_width"]["default"], 1200)
        self.assertEqual(properties["preview_height"]["default"], 440)
        self.assertFalse(properties["include_svg"]["default"])
        self.assertFalse(properties["include_dxf"]["default"])
        for option in ("views", "feature_angle_degrees", "hidden_line_removal", "show_hidden_lines",
                       "dry_run", "mesh_detail", "fit", "update"):
            self.assertNotIn(option, properties)
        self.primitive_geometry.assert_not_called()
        self.assert_no_auxiliary_calls()

    def test_invalid_plane_vectors_and_unknown_controls_fail_before_any_native_call(self):
        planes = [None, False, [], {}, {"origin": [0, 0, 0]}, {"normal": [0, 0, 1]},
                  {**self.PLANE, "extra": 0}, {**self.PLANE, "normal": [0, 0, 0]},
                  {**self.PLANE, "up": [0, 0, 0]}, {**self.PLANE, "up": [0, 0, 2]}]
        for field in ("origin", "normal", "up"):
            for value in ("0,0,0", [0, 0], [0, 0, 1, 2], [False, 0, 1], ["1", 0, 1],
                          [math.nan, 0, 1], [0, math.inf, 1], [0, 0, -math.inf]):
                planes.append({**self.PLANE, field: value})
        invalid = [{"plane": plane} for plane in planes]
        invalid.extend([
            {"refno": "CE"}, {"expected_type": "PANEL"}, {"coordinate_space": "OWNER"},
            {"radial_segments": 12}, {"expected_type": "PANE", "radial_segments": 48},
            {"expected_type": "CYLI", "radial_segments": 11},
            {"expected_type": "CYLI", "radial_segments": 100},
            {"expected_type": "CYLI", "radial_segments": 48.0},
            {"expected_type": "CYLI", "radial_segments": True},
            {"include_svg": "true"}, {"include_dxf": 1},
            {"preview_width": 63}, {"preview_height": True}, {"preview_width": 800.0},
            {"preview_width": 2048, "preview_height": 2048},
            {"sheet": None}, {"sheet": {"paper_size": "A2"}},
            {"sheet": {"scale_denominator": 0}}, {"sheet": {"title": "中文"}},
            {"sheet": {"unknown": True}}, {"views": ["FRONT"]},
            {"feature_angle_degrees": 30}, {"hidden_line_removal": True}, {"show_hidden_lines": False},
            {"dry_run": True}, {"mesh_detail": "full"}, {"fit": "all"}, {"update": True},
        ])
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                response = self.call(**arguments)
                self.assertTrue(response.get("isError", False), response)
        for missing in ("refno", "expected_type", "plane"):
            arguments = {"refno": "=1/2", "expected_type": "BOX", "plane": self.PLANE}
            del arguments[missing]
            with self.subTest(missing=missing):
                response = self.rpc("tools/call", {"name": self.TOOL, "arguments": arguments})
                self.assertTrue(response.get("isError", False), response)
        self.primitive_geometry.assert_not_called()
        self.assert_no_auxiliary_calls()

    def test_box_section_returns_true_10_by_20_dimensions_and_bounded_default_png(self):
        response = self.call()
        self.assertFalse(response.get("isError", False), response)
        metadata = response["structuredContent"]
        self.assertTrue(metadata["ok"])
        result = metadata["result"]
        for flag in ("success", "available", "complete"):
            self.assertTrue(result[flag])
        self.assertEqual(result["requested_refno"], "=1/2")
        self.assertEqual(result["resolved_refno"], "=1/2")
        self.assertEqual(result["resolved_type"], "BOX")
        self.assertEqual(result["coordinate_space"], "WORLD")
        self.assertTrue(result["section"]["intersection"])
        self.assertTrue(result["section"]["complete"])
        section = result["section"]
        self.assertEqual(section["coordinate_unit"], "mm")
        self.assertEqual(section["area_unit"], "mm2")
        self.assertEqual(section["length_unit"], "mm")
        self.assertEqual(section["counts"]["vertices"], 8)
        self.assertEqual(section["counts"]["segments"], 8)
        self.assertEqual(section["counts"]["loops"], 1)
        self.assertEqual(len(section["vertices"]), 8)
        self.assertEqual(len(section["segments"]), 8)
        loop = section["loops"][0]
        self.assertEqual(loop["vertices"][0], loop["vertices"][-1])
        self.assertEqual(len(loop["segment_indices"]), 8)
        self.assertTrue(loop["area_available"])
        self.assertTrue(loop["perimeter_available"])
        self.assertAlmostEqual(abs(loop["signed_area"]), 200.0)
        self.assertAlmostEqual(loop["perimeter"], 60.0)
        for segment in section["segments"]:
            self.assertEqual(len(segment["vertices"]), 2)
            self.assertTrue(segment["source_triangles"])
        self.assertGreater(result["source_mesh"]["counts"]["vertices"], 0)
        drawing = result["drawing"]
        self.assertEqual(drawing["coordinate_unit"], "mm")
        self.assertEqual([view["name"] for view in drawing["views"]], ["SECTION"])
        self.assertEqual(drawing["views"][0]["dimensions"], {"width": 10.0, "height": 20.0, "unit": "mm"})
        self.assertNotIn("svg", drawing)
        self.assertNotIn("dxf", drawing)
        self.assertNotIn("source_geometry", result)
        self.assertNotIn("derived_mesh", result)
        png = self.png(response)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual((int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")), (1200, 440))
        self.primitive_geometry.assert_called_once_with("=1/2")
        self.assert_no_auxiliary_calls()

    def test_local_and_default_world_planes_use_the_requested_coordinate_frame(self):
        geometry = element_fixtures.ProjectElementDrawingToolTests.geometry()
        geometry["frame"]["world"]["position"] = [100, 200, 300]
        self.primitive_geometry.return_value = self.envelope(geometry)
        local = self.call(coordinate_space="local")
        world = self.call(plane={"origin": [100, 200, 300], "normal": [0, 0, 1]})
        outside = self.call()
        for response, space in ((local, "local"), (world, "WORLD")):
            self.assertFalse(response.get("isError", False), response)
            result = response["structuredContent"]["result"]
            self.assertEqual(result["coordinate_space"], space)
            self.assertTrue(result["section"]["intersection"])
            self.assertEqual(result["drawing"]["views"][0]["dimensions"],
                             {"width": 10.0, "height": 20.0, "unit": "mm"})
        self.assertFalse(outside.get("isError", False), outside)
        self.assertFalse(outside["structuredContent"]["result"]["section"]["intersection"])
        self.assertEqual(self.primitive_geometry.call_count, 3)
        self.assert_no_auxiliary_calls()

    def test_up_vector_controls_section_axes_and_normal_magnitude_is_normalized(self):
        response = self.call(plane={"origin": [0, 0, 0], "normal": [0, 0, 2], "up": [1, 0, 0]})
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"]["result"]["drawing"]["views"][0]["dimensions"],
                         {"width": 20.0, "height": 10.0, "unit": "mm"})
        self.primitive_geometry.assert_called_once_with("=1/2")
        self.assert_no_auxiliary_calls()

    def test_finite_nonzero_normal_scaling_does_not_change_section_geometry(self):
        sections = []
        for magnitude in (1e-200, 1.0, 1e200):
            with self.subTest(magnitude=magnitude):
                response = self.call(plane={"origin": [0, 0, 0], "normal": [0, 0, magnitude]})
                self.assertFalse(response.get("isError", False), response)
                result = response["structuredContent"]["result"]
                self.assertTrue(result["section"]["intersection"])
                sections.append(result["section"])
        self.assertEqual(sections[0]["vertices"], sections[1]["vertices"])
        self.assertEqual(sections[1]["vertices"], sections[2]["vertices"])
        self.assertEqual(self.primitive_geometry.call_count, 3)
        self.assert_no_auxiliary_calls()

    def test_curved_section_resolution_changes_derived_polygon_without_extra_reads(self):
        geometry = element_fixtures.ProjectElementDrawingToolTests.geometry(shape="CYLI")
        self.primitive_geometry.return_value = self.envelope(geometry)
        low = self.call(expected_type="CYLI", radial_segments=12)
        high = self.call(expected_type="CYLI", radial_segments=24)
        for response in (low, high):
            self.assertFalse(response.get("isError", False), response)
            self.assertTrue(response["structuredContent"]["result"]["section"]["intersection"])
        first = low["structuredContent"]["result"]
        second = high["structuredContent"]["result"]
        self.assertLess(first["source_mesh"]["counts"]["vertices"], second["source_mesh"]["counts"]["vertices"])
        self.assertLess(first["section"]["counts"]["segments"], second["section"]["counts"]["segments"])
        self.assertEqual(first["drawing"]["views"][0]["dimensions"], second["drawing"]["views"][0]["dimensions"])
        self.assertEqual(self.primitive_geometry.call_count, 2)
        self.assert_no_auxiliary_calls()

    def test_panel_uses_one_native_world_position_read_and_preserves_long_type_resolution(self):
        geometry = panel_fixtures._native(points=[[0, 0], [10, 0], [10, 20], [0, 20]])
        geometry["world_positions_requested"] = True
        geometry["world_positions_complete"] = True
        for vertex in geometry["loops"][0]["vertices"]:
            x, y, _ = vertex["position"]
            vertex["position_world_mm"] = [x + 100, y + 200, 300]
            vertex["world_position_complete"] = True
        self.panel_geometry.return_value = self.envelope(geometry)
        response = self.call(expected_type="PANE", plane={"origin": [100, 200, 301], "normal": [0, 0, 1]})
        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["resolved_type"], "PANE")
        self.assertTrue(result["section"]["intersection"])
        self.assertEqual(result["drawing"]["views"][0]["dimensions"], {"width": 10.0, "height": 20.0, "unit": "mm"})
        self.panel_geometry.assert_called_once_with("=1/2", include_world=True)
        self.primitive_geometry.assert_not_called()
        self.assert_no_auxiliary_calls(allow_panel=True)

    def test_section_sheet_svg_and_dxf_share_scale_without_changing_model_dimensions(self):
        response = self.call(include_svg=True, include_dxf=True, preview_width=800, preview_height=600,
                             sheet={"paper_size": "A4", "orientation": "portrait", "scale_denominator": 2,
                                    "title": "PUMP_SECTION", "drawing_number": "SEC-001"})
        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        drawing = result["drawing"]
        self.assertTrue(result["section"]["intersection"])
        self.assertEqual(drawing["views"][0]["dimensions"], {"width": 10.0, "height": 20.0, "unit": "mm"})
        self.assertEqual(drawing["sheet"]["scale_denominator"], 2.0)
        self.assertEqual(drawing["sheet"]["paper_size_mm"], [210.0, 297.0])
        self.assertIn("10 MM", [text["text"] for text in drawing["sheet"]["texts"]])
        self.assertIn("20 MM", [text["text"] for text in drawing["sheet"]["texts"]])
        svg = ET.fromstring(drawing["svg"])
        self.assertEqual(svg.attrib["width"], "210mm")
        self.assertEqual(svg.attrib["height"], "297mm")
        self.assertIn("8\nSECTION_OUTLINE", drawing["dxf"])
        self.assertIn("9\n$INSUNITS\n70\n4", drawing["dxf"])
        png = self.png(response)
        self.assertEqual((int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")), (800, 600))
        self.primitive_geometry.assert_called_once_with("=1/2")
        self.assert_no_auxiliary_calls()

    def test_plain_section_svg_and_dxf_export_without_printable_sheet(self):
        response = self.call(include_svg=True, include_dxf=True)
        self.assertFalse(response.get("isError", False), response)
        drawing = response["structuredContent"]["result"]["drawing"]
        self.assertNotIn("sheet", drawing)
        self.assertTrue(drawing["svg"].startswith("<svg"))
        self.assertIn("SECTION", drawing["svg"])
        self.assertIn("8\nSECTION_OUTLINE", drawing["dxf"])
        self.assertIn("9\n$ACADVER\n1\nAC1015", drawing["dxf"])
        self.assertIn("9\n$INSUNITS\n70\n4", drawing["dxf"])
        self.assertTrue(self.png(response).startswith(b"\x89PNG\r\n\x1a\n"))
        self.primitive_geometry.assert_called_once_with("=1/2")
        self.assert_no_auxiliary_calls()

    def test_no_intersection_is_a_complete_success_without_drawing_image_or_exports(self):
        response = self.call(plane={"origin": [0, 0, 100], "normal": [0, 0, 1]},
                             include_svg=True, include_dxf=True, sheet={})
        self.assertFalse(response.get("isError", False), response)
        self.assertTrue(response["structuredContent"]["ok"])
        result = response["structuredContent"]["result"]
        for flag in ("success", "available", "complete"):
            self.assertTrue(result[flag])
        self.assertFalse(result["section"]["intersection"])
        self.assertIsNone(result["drawing"])
        self.assertFalse(any(row["type"] == "image" for row in response["content"]))
        self.assertEqual(result["resolved_refno"], "=1/2")
        self.assertGreater(result["source_mesh"]["counts"]["triangles"], 0)
        self.primitive_geometry.assert_called_once_with("=1/2")
        self.assert_no_auxiliary_calls()

    def test_coplanar_cut_fails_with_preserved_section_and_source_evidence(self):
        response = self.call(plane={"origin": [0, 0, 15], "normal": [0, 0, 1]}, include_svg=True, include_dxf=True)
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertFalse(result["success"])
        self.assertFalse(result["available"])
        self.assertIsNone(result["drawing"])
        self.assertIsInstance(result["section"], dict)
        self.assertFalse(result["section"]["success"])
        self.assertFalse(result["section"]["available"])
        for field in ("vertices", "segments", "loops"):
            self.assertEqual(result["section"][field], [])
        self.assertTrue(result["section"]["diagnostics"])
        self.assertIsInstance(result["error_code"], str)
        self.assertEqual(result["error_code"], "section_coplanar_triangles_unsupported")
        self.assertEqual(result["resolved_refno"], "=1/2")
        self.assertGreater(result["source_mesh"]["counts"]["triangles"], 0)
        self.assertFalse(any(row["type"] == "image" for row in response["content"]))
        self.primitive_geometry.assert_called_once_with("=1/2")
        self.assert_no_auxiliary_calls()

    def test_impossible_paper_scale_retains_successful_section_when_export_fails(self):
        response = self.call(sheet={"paper_size": "A4", "scale_denominator": .001}, include_svg=True, include_dxf=True)
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["error_code"], "drawing_export_failed")
        self.assertIn("drawing_sheet_scale_does_not_fit", result["export_error"])
        self.assertTrue(result["section"]["intersection"])
        self.assertTrue(result["section"]["complete"])
        self.assertIsNone(result["drawing"])
        self.assertEqual(result["resolved_refno"], "=1/2")
        self.assertFalse(any(row["type"] == "image" for row in response["content"]))
        self.primitive_geometry.assert_called_once_with("=1/2")
        self.assert_no_auxiliary_calls()

    def test_native_failure_is_retained_verbatim_without_retry_recovery_or_more_reads(self):
        native_failure = {"ok": False, "version": "E3D21", "error_code": "native_database_fault",
                          "error": "Read failed", "native": {"request_id": "original-fault", "status": 503},
                          "state": {"recovery_required": True, "native_database_fault": {"has_fault": True}}}
        self.primitive_geometry.return_value = copy.deepcopy(native_failure)
        response = self.call(include_svg=True, include_dxf=True, sheet={})
        self.assertTrue(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"], native_failure)
        self.assertFalse(any(row["type"] == "image" for row in response["content"]))
        self.primitive_geometry.assert_called_once_with("=1/2")
        self.assert_no_auxiliary_calls()

    def test_derived_mesh_failure_keeps_original_error_and_has_no_section_image(self):
        with patch.object(tools_module, "derive_primitive_mesh", return_value={
                "success": False, "available": False, "complete": True,
                "errors": ["unsupported_native_shape_parameters"]}):
            response = self.call()
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["error_code"], "unsupported_native_shape_parameters")
        self.assertEqual(result["source_mesh"]["errors"], ["unsupported_native_shape_parameters"])
        self.assertIsNone(result["drawing"])
        self.assertIsNone(result["section"])
        self.assertFalse(any(row["type"] == "image" for row in response["content"]))
        self.primitive_geometry.assert_called_once_with("=1/2")
        self.assert_no_auxiliary_calls()

    def test_open_source_mesh_cannot_be_presented_as_a_valid_closed_section(self):
        mesh = tools_module.derive_primitive_mesh(self.envelope()["result"], coordinate_space="WORLD")
        mesh["triangles"] = mesh["triangles"][:-1]
        mesh["counts"]["triangles"] -= 1
        with patch.object(tools_module, "derive_primitive_mesh", return_value=mesh):
            response = self.call(include_svg=True, include_dxf=True)
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertFalse(result["success"])
        self.assertFalse(result["available"])
        self.assertIsNone(result["drawing"])
        self.assertIsInstance(result["section"], dict)
        self.assertFalse(result["section"]["success"])
        self.assertFalse(result["section"]["available"])
        for field in ("vertices", "segments", "loops"):
            self.assertEqual(result["section"][field], [])
        self.assertIsInstance(result["section"]["validation"], dict)
        self.assertFalse(result["section"]["validation"]["valid"])
        self.assertIsInstance(result["error_code"], str)
        self.assertGreater(result["source_mesh"]["counts"]["vertices"], 0)
        self.assertFalse(any(row["type"] == "image" for row in response["content"]))
        self.primitive_geometry.assert_called_once_with("=1/2")
        self.assert_no_auxiliary_calls()

    def test_wrong_native_target_is_rejected_without_section_or_mesh_array_leak(self):
        geometry = element_fixtures.ProjectElementDrawingToolTests.geometry(refno="=1/9")
        self.primitive_geometry.return_value = self.envelope(geometry)
        response = self.call()
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["error_code"], "element_reference_mismatch")
        self.assertEqual(result["requested_refno"], "=1/2")
        self.assertEqual(result["resolved_refno"], "=1/9")
        self.assertIsNone(result["drawing"])
        self.assertIsNone(result["section"])
        self.assertNotIn("derived_mesh", result)
        self.assertFalse(any(row["type"] == "image" for row in response["content"]))
        self.primitive_geometry.assert_called_once_with("=1/2")
        self.assert_no_auxiliary_calls()


if __name__ == "__main__":
    unittest.main()

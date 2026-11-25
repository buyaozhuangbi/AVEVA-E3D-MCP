"""MCP opt-in physical projection and aligned sheet integration contracts."""
from __future__ import annotations

import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp import tools as production
from tests import test_drawing_sheet_tool as fixtures


class ProjectionSheetToolTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DrawingSheetToolTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def call(self, tool="e3d_project_element_drawing", **options):
        return self.fixture.call(tool, **options)

    @staticmethod
    def result(response):
        if response.get("isError"):
            raise AssertionError(response)
        return response["structuredContent"]["result"]

    def test_only_two_projection_tools_discover_optional_projection_method(self):
        tools = {row["name"]: row for row in self.fixture.rpc("tools/list", {})["tools"]}
        self.assertEqual(len(tools), 126)
        for name in ("e3d_project_element_drawing", "e3d_project_assembly_drawing"):
            prop = tools[name]["inputSchema"]["properties"]["sheet"]["properties"]["projection_method"]
            self.assertEqual(prop["enum"], ["first_angle", "third_angle"])
            self.assertNotIn("default", prop)
            self.assertTrue(tools[name]["annotations"]["readOnlyHint"])
        for name in ("e3d_section_element_drawing", "e3d_section_assembly_drawing"):
            self.assertNotIn("projection_method", tools[name]["inputSchema"]["properties"]["sheet"]["properties"])

    def test_invalid_method_is_rejected_before_any_context_or_native_reads(self):
        with patch.object(BridgeClient, "primitive_geometry") as read, patch.object(BridgeClient, "panel_geometry") as panel:
            for tool in self.fixture.TOOLS:
                for method in (None, True, 1, [], {}, "third", "THIRD_ANGLE", "legacy"):
                    with self.subTest(tool=tool, method=method):
                        response = self.call(tool, sheet={"projection_method": method})
                        self.assertTrue(response.get("isError"), response)
            read.assert_not_called()
            panel.assert_not_called()
        self.fixture.health.assert_not_called()
        self.fixture.project.assert_not_called()

    def test_incomplete_view_sets_fail_before_native_reads(self):
        with patch.object(BridgeClient, "primitive_geometry") as read:
            for tool in self.fixture.TOOLS:
                for method in ("first_angle", "third_angle"):
                    for views in (["FRONT"], ["FRONT", "TOP"], ["TOP", "SIDE"]):
                        response = self.call(tool, views=views, sheet={"projection_method": method})
                        self.assertTrue(response.get("isError"), response)
                        self.assertIn("projection_method_requires_all_three_views", str(response))
            read.assert_not_called()
        self.fixture.health.assert_not_called()
        self.fixture.project.assert_not_called()

    def test_section_methods_rejected_before_any_native_context(self):
        for name in ("e3d_section_element_drawing", "e3d_section_assembly_drawing"):
            arguments = {"plane": {"origin": [0, 0, 0], "normal": [0, 0, 1]},
                         "sheet": {"projection_method": "third_angle"}}
            target = {"refno": "=1/2", "expected_type": "BOX"}
            arguments.update(target if "element" in name else {"targets": [target]})
            with patch.object(BridgeClient, "primitive_geometry") as read, patch.object(BridgeClient, "panel_geometry") as panel:
                response = self.fixture.rpc("tools/call", {"name": name, "arguments": arguments})
                self.assertTrue(response.get("isError"), response)
                read.assert_not_called()
                panel.assert_not_called()
        self.fixture.health.assert_not_called()
        self.fixture.project.assert_not_called()

    def test_both_tools_recompute_physical_basis_and_return_all_formats(self):
        for tool in self.fixture.TOOLS:
            for method in ("first_angle", "third_angle"):
                with self.subTest(tool=tool, method=method), \
                        patch.object(BridgeClient, "primitive_geometry", return_value=self.fixture.envelope()) as read, \
                        patch.object(BridgeClient, "capture_view") as capture, \
                        patch.object(BridgeClient, "update_primitive") as write, \
                        patch.object(production, "generate_orthographic_drawing", wraps=production.generate_orthographic_drawing) as projector:
                    result = self.result(self.call(tool, sheet={"paper_size": "A4", "projection_method": method},
                                                   include_svg=True, include_dxf=True))
                    drawing = result["drawing"]
                    self.assertEqual(projector.call_args.kwargs["projection_convention"], "right_handed")
                    self.assertEqual(drawing["projection_convention"], "right_handed")
                    self.assertEqual(drawing["views"][0]["toward_viewer"], [0, -1, 0])
                    self.assertEqual(drawing["sheet"]["projection_method"], method)
                    self.assertEqual(drawing["source_bounds"]["dimensions"], [10, 20, 30])
                    self.assertIn("<svg", drawing["svg"])
                    self.assertIn("FRONT_OUTLINE", drawing["dxf"])
                    self.assertEqual(drawing["sheet"]["model_coordinate_unit"], "mm")
                    read.assert_called_once_with("=1/2")
                    capture.assert_not_called()
                    write.assert_not_called()

    def test_first_and_third_retain_same_physical_views_and_swap_positions(self):
        results = []
        with patch.object(BridgeClient, "primitive_geometry", return_value=self.fixture.envelope()):
            for method in ("first_angle", "third_angle"):
                results.append(self.result(self.call(sheet={"projection_method": method}))["drawing"])
        self.assertEqual(results[0]["views"], results[1]["views"])
        for drawing, method in zip(results, ("first_angle", "third_angle")):
            views = {row["name"]: row for row in drawing["sheet"]["views"]}
            f, t, s = (views[name]["paper_bounds_mm"] for name in ("FRONT", "TOP", "SIDE"))
            self.assertEqual(f["min"][0], t["min"][0])
            self.assertEqual(f["min"][1], s["min"][1])
            if method == "third_angle":
                self.assertGreater(t["min"][1], f["max"][1])
                self.assertGreater(s["min"][0], f["max"][0])
            else:
                self.assertLess(t["max"][1], f["min"][1])
                self.assertLess(s["max"][0], f["min"][0])

    def test_reordered_request_preserves_output_order_and_alignment(self):
        with patch.object(BridgeClient, "primitive_geometry", return_value=self.fixture.envelope()):
            drawing = self.result(self.call(views=["SIDE", "FRONT", "TOP"],
                sheet={"projection_method": "third_angle"}))["drawing"]
        self.assertEqual([view["name"] for view in drawing["views"]], ["SIDE", "FRONT", "TOP"])
        self.assertEqual([view["name"] for view in drawing["sheet"]["views"]], ["SIDE", "FRONT", "TOP"])

    def test_projection_mode_is_not_silently_injected_into_legacy_requests(self):
        with patch.object(BridgeClient, "primitive_geometry", return_value=self.fixture.envelope()), \
                patch.object(production, "generate_orthographic_drawing", wraps=production.generate_orthographic_drawing) as projector:
            drawing = self.result(self.call(sheet={}))["drawing"]
            self.assertNotIn("projection_convention", projector.call_args.kwargs)
            self.assertNotIn("projection_convention", drawing)
            self.assertNotIn("projection_method", drawing["sheet"]["options"])
            self.assertNotIn("projection_method", drawing["sheet"])
            self.assertEqual(drawing["views"][0]["toward_viewer"], [0, 1, 0])

    def test_render_failure_retains_source_without_partial_drawing_or_image(self):
        for tool in self.fixture.TOOLS:
            with patch.object(BridgeClient, "primitive_geometry", return_value=self.fixture.envelope()):
                response = self.call(tool, sheet={"projection_method": "third_angle", "scale_denominator": .001})
            self.assertTrue(response.get("isError"), response)
            result = response["structuredContent"]["result"]
            self.assertEqual(result["error_code"], "drawing_export_failed")
            self.assertIsNone(result["drawing"])
            self.assertFalse(any(item["type"] == "image" for item in response["content"]))
            if tool.endswith("assembly_drawing"):
                self.assertEqual(result["sources"][0]["resolved_refno"], "=1/2")
                self.assertTrue(result["sources"][0]["success"])
            else:
                self.assertEqual(result["resolved_refno"], "=1/2")


if __name__ == "__main__":
    unittest.main()

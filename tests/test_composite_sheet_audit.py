"""Independent composite-pipeline read-count and retained-evidence checks."""
from __future__ import annotations

import copy
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp import tools as production
from tests import test_panel_mesh as panel_fixtures
from tests import test_section_assembly_drawing_tool as fixtures


class CompositeSheetIndependentAuditTests(unittest.TestCase):
    TOOL = "e3d_compose_drawing_sheet"

    def setUp(self):
        self.fixture = fixtures.SectionAssemblyDrawingToolTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def call(self, **options):
        return self.fixture.rpc("tools/call", {"name": self.TOOL, "arguments": {
            "targets": [self.fixture.target(), self.fixture.target("=1/3")],
            "plane": copy.deepcopy(self.fixture.PLANE), "sheet": {}, **options}})

    def read(self, geometries, **options):
        self.fixture.primitive_geometry.side_effect = self.fixture.envelopes(copy.deepcopy(geometries))
        return self.call(**options)

    def success(self, response):
        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertTrue(all(result.get(key) is True for key in ("success", "available", "complete")), result)
        self.assertEqual(sum(row["type"] == "image" for row in response["content"]), 1)
        return result

    def failed(self, response):
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertIs(result["success"], False)
        self.assertIsNone(result["drawing"])
        self.fixture.assert_no_image(response)
        self.fixture.assert_no_writes()
        return result

    def complete_source_reads_once(self, count=2):
        self.assertEqual(self.fixture.primitive_geometry.call_count, count)
        self.assertEqual(self.fixture.health.call_count, 2)
        self.assertEqual(self.fixture.project.call_count, 2)
        self.fixture.assert_no_writes()

    def reset_read_mocks(self):
        for name in ("primitive_geometry", "panel_geometry", "health", "project"):
            getattr(self.fixture, name).reset_mock()

    @staticmethod
    def selector(section, axis="horizontal"):
        coordinate = 0 if axis == "horizontal" else 1
        vertices = section["vertices"]
        first = min(range(len(vertices)), key=lambda i: vertices[i][coordinate])
        second = max(range(len(vertices)), key=lambda i: vertices[i][coordinate])
        return {"expected_section_revision": section["dimension_revision"], "items": [
            {"first_vertex": first, "second_vertex": second, "axis": axis}]}

    def test_sources_read_once_and_identical_combined_arrays_feed_both_algorithms(self):
        with patch.object(production, "generate_orthographic_drawing", wraps=production.generate_orthographic_drawing) as project, \
                patch.object(production, "slice_triangle_mesh", wraps=production.slice_triangle_mesh) as section:
            result = self.success(self.call(show_hidden_lines=True))
        self.assertEqual(project.call_count, 1)
        self.assertEqual(section.call_count, 1)
        for index in (0, 1):
            self.assertIs(project.call_args.args[index], section.call_args.args[index])
        self.assertEqual(project.call_args.kwargs["projection_convention"], "right_handed")
        self.assertEqual([row.args[0] for row in self.fixture.primitive_geometry.call_args_list], ["=1/2", "=1/3"])
        self.complete_source_reads_once()
        self.assertEqual([view["name"] for view in result["drawing"]["views"]], ["FRONT", "TOP", "SIDE", "SECTION"])
        self.assertNotIn("vertices", result["source_mesh"])
        self.assertNotIn("triangles", result["source_mesh"])
        for source in result["sources"]:
            self.assertNotIn("vertices", source["source_mesh"])
            self.assertNotIn("triangles", source["source_mesh"])
        self.assertFalse(result["source_mesh"]["boolean_union_applied"])

    def test_tool_discovery_declares_fixed_four_views_and_default_third_angle(self):
        catalog = {row["name"]: row for row in self.fixture.rpc("tools/list", {})["tools"]}
        tool = catalog[self.TOOL]
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        schema = tool["inputSchema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), {"targets", "plane", "sheet"})
        self.assertEqual(schema["properties"]["coordinate_space"]["enum"], ["WORLD"])
        self.assertEqual(schema["properties"]["sheet"]["properties"]["projection_method"]["default"], "third_angle")
        self.assertEqual(set(schema["properties"]["sheet"]["properties"]["projection_method"]["enum"]),
                         {"first_angle", "third_angle"})
        self.assertNotIn("views", schema["properties"])
        self.assertNotIn("views", schema["properties"]["sheet"]["properties"])
        self.assertEqual(schema["properties"]["targets"]["maxItems"], 16)
        self.fixture.health.assert_not_called()
        self.fixture.project.assert_not_called()
        self.fixture.primitive_geometry.assert_not_called()

    def test_mixed_pane_and_box_are_each_read_once_without_second_section_read(self):
        geometry = panel_fixtures._native(points=[[0, 0], [10, 0], [10, 20], [0, 20]])
        geometry["element"]["refno"] = "=1/3"
        geometry["world_positions_requested"] = geometry["world_positions_complete"] = True
        for vertex in geometry["loops"][0]["vertices"]:
            x, y, _ = vertex["position"]
            vertex["position_world_mm"] = [x + 30, y, -1]
            vertex["world_position_complete"] = True
        self.fixture.panel_geometry.return_value = self.fixture.envelopes([geometry])[0]
        result = self.success(self.read([self.fixture.geometry()], targets=[
            self.fixture.target(), self.fixture.target("=1/3", "PANE")]))
        self.fixture.primitive_geometry.assert_called_once_with("=1/2")
        self.fixture.panel_geometry.assert_called_once_with("=1/3", include_world=True)
        self.assertEqual([row["resolved_type"] for row in result["sources"]], ["BOX", "PANE"])
        self.complete_source_reads_once(1)

    def test_invalid_options_rejected_before_reading_any_boundary_or_source(self):
        invalid = [
            {"sheet": None}, {"plane": None}, {"coordinate_space": "local"},
            {"views": ["FRONT", "TOP", "SIDE", "SECTION"]},
            {"plane": {"origin": [0, 0, 0], "normal": [0, 0, 0]}},
            {"plane": {"origin": [math.inf, 0, 0], "normal": [0, 0, 1]}},
            {"sheet": {"projection_method": "legacy"}}, {"sheet": {"scale_denominator": 0}},
            {"targets": []}, {"targets": [self.fixture.target()] * 17},
            {"targets": [self.fixture.target(), self.fixture.target("=0001/02")]},
            {"hatch": {"spacing_mm": 0}},
            {"dimensions": {"expected_section_revision": "invalid", "items": []}},
            {"include_svg": 1}, {"preview_width": True},
        ]
        for options in invalid:
            with self.subTest(options=options):
                response = self.call(**options)
                self.assertTrue(response.get("isError", False), response)
        for name in ("health", "project", "primitive_geometry", "panel_geometry"):
            getattr(self.fixture, name).assert_not_called()
        self.fixture.assert_no_writes()

    def test_native_fault_stops_once_and_retains_unread_sources(self):
        fault = {"ok": False, "version": "E3D21", "error_code": "native_database_fault",
                 "error": "original native read failure", "native_diagnostics": {"request_id": "same-fault"}}
        self.fixture.primitive_geometry.side_effect = [fault]
        result = self.failed(self.call(targets=[self.fixture.target(), self.fixture.target("=1/3"), self.fixture.target("=1/4")]))
        self.assertEqual(result["sources"][0]["read_error"], fault)
        self.assertEqual([row["read_status"] for row in result["sources"]][1:], ["not_read", "not_read"])
        self.assertIsNone(result["context"]["after"])
        self.assertIsNone(result["section"])
        self.fixture.primitive_geometry.assert_called_once_with("=1/2")
        self.assertEqual(self.fixture.health.call_count, 1)
        self.assertEqual(self.fixture.project.call_count, 1)

    def test_later_identity_mismatch_preserves_first_and_unread_third_source(self):
        pair = self.fixture.separated_pair()
        pair[1]["element"]["refno"] = "=1/9"
        result = self.failed(self.read(pair, targets=[self.fixture.target(), self.fixture.target("=1/3"), self.fixture.target("=1/4")]))
        self.assertTrue(result["sources"][0]["success"])
        self.assertEqual(result["sources"][1]["error_code"], "element_reference_mismatch")
        self.assertEqual(result["sources"][1]["resolved_refno"], "=1/9")
        self.assertEqual(result["sources"][2]["read_status"], "not_read")
        self.assertIsNone(result["section"])
        self.assertEqual(self.fixture.primitive_geometry.call_count, 2)

    def test_changed_boundary_rejects_before_running_either_algorithm(self):
        changed = copy.deepcopy(self.fixture.ready)
        changed["session_id"] = "new-session"
        self.fixture.health.side_effect = [self.fixture.ready, changed]
        with patch.object(production, "generate_orthographic_drawing") as project, \
                patch.object(production, "slice_triangle_mesh") as section:
            result = self.failed(self.call())
        self.assertEqual(result["error_code"], "assembly_context_changed")
        self.assertIs(result["context"]["boundary_agreement"], False)
        self.assertTrue(all(source["success"] for source in result["sources"]))
        project.assert_not_called()
        section.assert_not_called()
        self.complete_source_reads_once()

    def test_oblique_section_retains_plane_coordinates_and_physical_basis(self):
        plane = {"origin": [1, 2, 3], "normal": [1, 2, 3], "up": [0, 0, 1]}
        result = self.success(self.call(plane=plane))
        drawing, section = result["drawing"], result["section"]
        views = {view["name"]: view for view in drawing["views"]}
        self.assertEqual(views["FRONT"]["coordinate_frame"], "WORLD coordinates; millimetres")
        self.assertEqual(views["SECTION"]["coordinate_frame"], "section plane coordinates; millimetres")
        self.assertNotEqual(views["FRONT"]["coordinate_frame"], views["SECTION"]["coordinate_frame"])
        for key, basis in (("horizontal_axis", "right"), ("vertical_axis", "up"), ("toward_viewer", "normal")):
            self.assertEqual(views["SECTION"][key], section["plane"][basis])
        self.assertEqual(views["SECTION"]["origin"], plane["origin"])
        placed = {view["name"]: view for view in drawing["sheet"]["views"]}
        denominators = [view["transform"]["scale_denominator"] for view in placed.values()]
        self.assertEqual(denominators, [drawing["sheet"]["scale_denominator"]] * 4)
        self.assertEqual(placed["SECTION"]["model_bounds"], section["bounds"])
        self.complete_source_reads_once()

    def test_stale_selectors_keep_current_section_sources_and_zero_images(self):
        geometries = self.fixture.separated_pair()
        first = self.success(self.read(geometries))
        request = self.selector(first["section"])
        response = self.read(geometries, plane={"origin": [0, 0, 1], "normal": [0, 0, 1]}, dimensions=request)
        result = self.failed(response)
        self.assertEqual(result["dimensions"]["error_code"], "section_dimension_revision_mismatch")
        self.assertTrue(result["section"]["success"])
        self.assertNotEqual(result["section"]["dimension_revision"], first["section"]["dimension_revision"])
        self.assertEqual(result["dimensions"]["current_section_revision"], result["section"]["dimension_revision"])
        self.assertTrue(all(source["success"] for source in result["sources"]))
        self.assertEqual(result["dimensions"]["items"], [])
        self.assertEqual(self.fixture.primitive_geometry.call_count, 4)

    def test_same_contours_changed_source_revision_reject_selectors(self):
        geometries = self.fixture.separated_pair()
        geometries[0]["revision"], geometries[1]["revision"] = "box-A-1", "box-B-1"
        first = self.success(self.read(geometries))
        request = self.selector(first["section"])
        geometries[1]["revision"] = "box-B-2"
        result = self.failed(self.read(geometries, dimensions=request))
        self.assertEqual(result["dimensions"]["error_code"], "section_dimension_revision_mismatch")
        self.assertEqual(result["section"]["vertices"], first["section"]["vertices"])
        self.assertNotEqual(result["section"]["dimension_revision"], first["section"]["dimension_revision"])
        self.assertTrue(all(source["success"] for source in result["sources"]))

    def test_hatch_overlap_rejection_preserves_resolved_dimensions_and_no_partial_drawing(self):
        geometries = [self.fixture.geometry(), self.fixture.geometry(refno="=1/3", position=[5, 0, 0])]
        first = self.success(self.read(geometries))
        request = self.selector(first["section"])
        result = self.failed(self.read(geometries, dimensions=request, hatch={"spacing_mm": 5, "angle_degrees": 0}))
        self.assertTrue(result["section"]["success"])
        self.assertTrue(result["dimensions"]["success"])
        self.assertEqual(result["dimensions"]["items"][0]["value_mm"], 15)
        self.assertFalse(result["hatching"]["success"])
        self.assertEqual(result["hatching"]["lines"], [])
        self.assertTrue(all(source["success"] for source in result["sources"]))

    def test_tangent_section_failure_keeps_all_reads_without_image(self):
        result = self.failed(self.call(plane={"origin": [0, 0, 15], "normal": [0, 0, 1]}))
        self.assertFalse(result["section"]["success"])
        self.assertEqual(result["section"]["vertices"], [])
        self.assertTrue(all(source["success"] for source in result["sources"]))
        self.complete_source_reads_once()

    def test_projection_failure_retains_source_evidence_and_does_not_run_section(self):
        with patch.object(production, "generate_orthographic_drawing", return_value={
                "success": False, "available": False, "complete": True, "error_code": "projection_budget_exhausted"}) as project, \
                patch.object(production, "slice_triangle_mesh") as section:
            result = self.failed(self.call())
        self.assertEqual(result["error_code"], "projection_budget_exhausted")
        self.assertEqual(result["projection"]["error_code"], "projection_budget_exhausted")
        self.assertIsNone(result["section"])
        project.assert_called_once()
        section.assert_not_called()
        self.complete_source_reads_once()

    def test_projection_incomplete_result_is_retained_without_calling_section(self):
        with patch.object(production, "generate_orthographic_drawing", return_value={
                "success": False, "available": False, "complete": True,
                "error_code": "projection_visibility_budget_exceeded", "views": [], "partial": False}) as project, \
                patch.object(production, "slice_triangle_mesh") as section:
            result = self.failed(self.call())
        self.assertEqual(result["error_code"], "projection_visibility_budget_exceeded")
        self.assertEqual(result["projection"]["error_code"], "projection_visibility_budget_exceeded")
        self.assertTrue(result["projection"]["complete"])
        self.assertIsNone(result["section"])
        project.assert_called_once()
        section.assert_not_called()
        self.complete_source_reads_once()

    def test_section_phase_exception_retains_successful_projection(self):
        with patch.object(production, "slice_triangle_mesh", side_effect=RuntimeError("section failed")):
            result = self.failed(self.call())
        self.assertEqual(result["error_code"], "composition_section_failed")
        self.assertTrue(result["projection"]["success"])
        self.assertIsNone(result["section"])
        self.assertEqual(result["error_type"], "RuntimeError")
        self.complete_source_reads_once()

    def test_empty_section_is_explicit_and_keeps_the_projection(self):
        result = self.failed(self.call(plane={"origin": [0, 0, 1000], "normal": [0, 0, 1]}))
        self.assertEqual(result["error_code"], "composition_section_no_intersection")
        self.assertTrue(result["projection"]["success"])
        self.assertTrue(result["section"]["success"])
        self.assertFalse(result["section"]["intersection"])
        self.assertEqual(result["section"]["segments"], [])
        self.complete_source_reads_once()

    def test_export_failure_retains_projection_section_hatch_and_resolved_dimensions(self):
        geometries = self.fixture.separated_pair()
        initial = self.success(self.read(geometries))
        dimensions = self.selector(initial["section"])
        self.reset_read_mocks()
        with patch.object(production, "_render_drawing_outputs", side_effect=ValueError("paper export failed")):
            result = self.failed(self.read(geometries, dimensions=dimensions,
                                           hatch={"spacing_mm": 5, "angle_degrees": 0}))
        self.assertEqual(result["error_code"], "drawing_export_failed")
        self.assertTrue(result["projection"]["success"])
        self.assertTrue(result["section"]["success"])
        self.assertTrue(result["dimensions"]["success"])
        self.assertTrue(result["hatching"]["success"])
        self.assertIsNone(result["drawing"])
        self.complete_source_reads_once()

    def test_successful_hatch_and_dimensions_use_the_single_shared_section(self):
        geometries = self.fixture.separated_pair()
        initial = self.success(self.read(geometries))
        dimensions = self.selector(initial["section"])
        self.reset_read_mocks()
        result = self.success(self.read(geometries, dimensions=dimensions,
                                        hatch={"spacing_mm": 5, "angle_degrees": 0}))
        self.assertTrue(result["section"]["success"])
        self.assertTrue(result["dimensions"]["success"])
        self.assertTrue(result["hatching"]["success"])
        section_view = next(v for v in result["drawing"]["views"] if v["name"] == "SECTION")
        self.assertEqual(sum(line["kind"] == "hatch" for line in section_view["lines"]),
                         len(result["hatching"]["lines"]))
        self.assertEqual(result["drawing"]["sheet"]["linear_dimensions"][0]["value_mm"],
                         result["dimensions"]["items"][0]["value_mm"])
        self.assertIs(result["drawing"]["hatching_applied"], True)
        self.complete_source_reads_once()


if __name__ == "__main__":
    unittest.main()

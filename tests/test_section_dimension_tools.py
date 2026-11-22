"""Native-read, stale-selector and paper-render contracts for section dimensions."""
from __future__ import annotations

import copy
import hashlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp import tools as tool_module
from tests import test_section_hatching_tool as fixtures
from tests import test_project_assembly_drawing_tool as assembly_fixtures


class SectionDimensionToolTests(unittest.TestCase):
    TOOL = "e3d_section_element_drawing"

    def setUp(self):
        fixtures.SectionHatchingToolTests.setUp(self)

    rpc = fixtures.SectionHatchingToolTests.rpc
    call = fixtures.SectionHatchingToolTests.call
    assert_only_primitive_reads = fixtures.SectionHatchingToolTests.assert_only_primitive_reads
    png = staticmethod(fixtures.SectionHatchingToolTests.png)

    def prepare(self):
        response = self.call()
        self.assertFalse(response.get("isError", False), response)
        section = response["structuredContent"]["result"]["section"]
        points = section["vertices"]
        first, second = points.index([-5.0, -10.0]), points.index([5.0, 10.0])
        self.primitive_geometry.reset_mock()
        return {"expected_section_revision": section["dimension_revision"], "items": [
            {"first_vertex": first, "second_vertex": second, "axis": "horizontal"},
            {"first_vertex": first, "second_vertex": second, "axis": "vertical"}]}

    def assert_failure_evidence(self, response, reason):
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["error_code"], "section_dimensions_failed")
        self.assertEqual(result["dimensions"]["error_code"], reason)
        self.assertEqual(result["dimensions"]["items"], [])
        self.assertTrue(result["section"]["success"])
        self.assertTrue(result["source"]["success"])
        self.assertIsNone(result["drawing"])
        self.assertFalse(any(block["type"] == "image" for block in response["content"]))
        self.assert_only_primitive_reads()

    def test_discovery_exposes_dimensions_only_on_section_tools(self):
        catalog = {row["name"]: row for row in self.rpc("tools/list", {})["tools"]}
        for name in (self.TOOL, "e3d_section_assembly_drawing"):
            schema = catalog[name]["inputSchema"]["properties"]["dimensions"]
            self.assertEqual(set(schema["required"]), {"expected_section_revision", "items"})
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual((schema["properties"]["items"]["minItems"], schema["properties"]["items"]["maxItems"]), (1, 8))
        for name in ("e3d_project_element_drawing", "e3d_project_assembly_drawing"):
            self.assertNotIn("dimensions", catalog[name]["inputSchema"]["properties"])
        self.assert_only_primitive_reads(0)

    def test_invalid_structure_or_missing_sheet_fails_before_native_read(self):
        item = {"first_vertex": 0, "second_vertex": 1, "axis": "horizontal"}
        valid = {"expected_section_revision": "a" * 64, "items": [item]}
        invalid = [None, {}, {"expected_section_revision": "bad", "items": [item]},
                   {**valid, "extra": 1}, {**valid, "items": []}, {**valid, "items": [item] * 9},
                   {**valid, "items": [{**item, "first_vertex": True}]},
                   {**valid, "items": [{**item, "side": "left"}]},
                   {**valid, "items": [{**item, "second_vertex": 0}]}]
        for dimensions in invalid:
            with self.subTest(dimensions=dimensions):
                self.assertTrue(self.call(dimensions=dimensions, sheet={}).get("isError", False))
        self.assertTrue(self.call(dimensions=valid).get("isError", False))
        self.assert_only_primitive_reads(0)

    def test_revision_is_stable_without_adding_unrequested_dimensions_or_changing_old_png(self):
        first, second = self.call(), self.call()
        a, b = [r["structuredContent"]["result"] for r in (first, second)]
        self.assertRegex(a["section"]["dimension_revision"], r"^[0-9a-f]{64}$")
        self.assertEqual(a["section"]["dimension_revision"], b["section"]["dimension_revision"])
        self.assertNotIn("dimensions", a)
        self.assertNotIn("linear_dimensions", a["drawing"]["views"][0])
        self.assertEqual(hashlib.sha256(self.png(first)).hexdigest(), fixtures.SectionHatchingToolTests.OLD_BOX_PNG_SHA256)
        self.assert_only_primitive_reads(2)

    def test_actual_dimensions_keep_model_values_and_share_paper_scene(self):
        dimensions = self.prepare()
        before = copy.deepcopy(dimensions)
        response = self.call(dimensions=dimensions, sheet={"paper_size": "A4", "scale_denominator": 2},
                             include_svg=True, include_dxf=True)
        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(dimensions, before)
        self.assertEqual([item["value_mm"] for item in result["dimensions"]["items"]], [10, 20])
        self.assertEqual([item["side"] for item in result["dimensions"]["items"]], ["top", "right"])
        view = result["drawing"]["views"][0]
        self.assertEqual(view["linear_dimensions"], result["dimensions"]["items"])
        scene = result["drawing"]["sheet"]
        labels = [item for item in scene["texts"] if "dimension_index" in item]
        self.assertEqual([item["text"] for item in labels], ["10 MM", "20 MM"])
        self.assertTrue(all(item["role"] == "dimension" for item in labels))
        self.assertIn('data-role="dimension"', result["drawing"]["svg"])
        self.assertIn("SECTION_DIMENSION", result["drawing"]["dxf"])
        self.assertTrue(self.png(response).startswith(b"\x89PNG\r\n\x1a\n"))
        self.assert_only_primitive_reads()

    def test_stale_geometry_rejects_old_vertex_selectors(self):
        dimensions = self.prepare()
        self.primitive_geometry.return_value["result"]["dimensions"]["XLEN"] = 12
        response = self.call(dimensions=dimensions, sheet={})
        self.assert_failure_evidence(response, "section_dimension_revision_mismatch")
        self.assertNotEqual(response["structuredContent"]["result"]["section"]["dimension_revision"], dimensions["expected_section_revision"])

    def test_changed_plane_rejects_old_vertex_selectors(self):
        dimensions = self.prepare()
        response = self.call(dimensions=dimensions, sheet={}, plane={"origin": [0, 0, 1], "normal": [0, 0, 1]})
        self.assert_failure_evidence(response, "section_dimension_revision_mismatch")

    def test_out_of_bounds_index_keeps_completed_geometry(self):
        dimensions = self.prepare()
        dimensions["items"][0]["second_vertex"] = 999
        self.assert_failure_evidence(self.call(dimensions=dimensions, sheet={}), "section_dimension_vertex_out_of_bounds")

    def test_zero_projected_axis_is_not_replaced_by_spatial_distance(self):
        dimensions = self.prepare()
        # The bottom left and upper left are distinct vertices but share u.
        section = self.call()["structuredContent"]["result"]["section"]
        self.primitive_geometry.reset_mock()
        dimensions["items"] = [{"first_vertex": section["vertices"].index([-5, -10]),
                                 "second_vertex": section["vertices"].index([-5, 10]), "axis": "horizontal"}]
        self.assert_failure_evidence(self.call(dimensions=dimensions, sheet={}), "section_dimension_zero_axis_distance")

    def test_render_failure_retains_resolved_dimension_evidence_without_image(self):
        dimensions = self.prepare()
        response = self.call(dimensions=dimensions, sheet={"scale_denominator": .001})
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["error_code"], "drawing_export_failed")
        self.assertTrue(result["dimensions"]["success"])
        self.assertEqual(len(result["dimensions"]["items"]), 2)
        self.assertIsNone(result["drawing"])
        self.assertFalse(any(item["type"] == "image" for item in response["content"]))
        self.assert_only_primitive_reads()

    def test_unavailable_annotation_revision_does_not_reject_unannotated_section(self):
        with patch.object(tool_module, "section_dimension_revision", side_effect=ValueError("outside_annotation_bounds")):
            response = self.call()
        self.assertFalse(response.get("isError", False), response)
        section = response["structuredContent"]["result"]["section"]
        self.assertIsNone(section["dimension_revision"])
        self.assertEqual(section["dimension_revision_error"], "outside_annotation_bounds")
        self.assertTrue(self.png(response))
        self.assert_only_primitive_reads()

    def test_assembly_can_dimension_between_vertices_of_distinct_sources(self):
        fixture = assembly_fixtures.ProjectAssemblyDrawingToolTests
        self.health.return_value = {"ok": True, "connected": True, "version": "E3D21", "session_id": "dim-session",
            "state": {"database_ready": True, "busy": False, "recovery_required": False,
                      "native_database_fault": {"has_fault": False}}}
        self.project.return_value = {"ok": True, "version": "E3D21", "project": {
            "name": "APS", "mdb": "ALL", "connected": True, "recovery_only": False}}
        geometry = [fixture.geometry(), fixture.geometry(refno="=1/3", name="/SECOND", position=[30, 0, 0])]
        arguments = {"targets": [fixture.target(), fixture.target("=1/3")],
                     "plane": {"origin": [0, 0, 0], "normal": [0, 0, 1]}}
        def invoke(**extra):
            self.primitive_geometry.side_effect = fixture.envelopes(geometry)
            return self.rpc("tools/call", {"name": "e3d_section_assembly_drawing", "arguments": {**arguments, **extra}})
        initial = invoke()["structuredContent"]["result"]
        section = initial["section"]
        first, second = section["vertices"].index([-5, -10]), section["vertices"].index([35, 10])
        dimensions = {"expected_section_revision": section["dimension_revision"], "items": [
            {"first_vertex": first, "second_vertex": second, "axis": "horizontal"}]}
        response = invoke(dimensions=dimensions, sheet={"paper_size": "A4"}, include_svg=True, include_dxf=True)
        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["dimensions"]["items"][0]["value_mm"], 40)
        self.assertEqual([member["refno"] for member in result["section"]["source_sections"]], ["=1/2", "=1/3"])
        self.assertEqual(result["section"]["dimension_revision"], section["dimension_revision"])
        self.assertIn("40 MM", [item["text"] for item in result["drawing"]["sheet"]["texts"] if "dimension_index" in item])
        self.assertTrue(result["context"]["boundary_agreement"])
        self.assertEqual(self.primitive_geometry.call_count, 4)
        for name in ("panel_geometry", "capture_view", "update_primitive", "update_panel", "command", "_request"):
            getattr(self, name).assert_not_called()


if __name__ == "__main__":
    unittest.main()

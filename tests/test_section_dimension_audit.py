"""Independent assembly provenance and failure-path dimension checks."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tests import test_section_assembly_drawing_tool as fixtures


class SectionDimensionIntegrationAuditTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SectionAssemblyDrawingToolTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def read(self, geometries, **options):
        self.fixture.primitive_geometry.side_effect = self.fixture.envelopes(copy.deepcopy(geometries))
        return self.fixture.call(**options)

    @staticmethod
    def request(section, first, second, axis="horizontal"):
        return {"expected_section_revision": section["dimension_revision"], "items": [
            {"first_vertex": first, "second_vertex": second, "axis": axis}]}

    @staticmethod
    def extremes(section):
        return (min(range(len(section["vertices"])), key=lambda index: section["vertices"][index][0]),
                max(range(len(section["vertices"])), key=lambda index: section["vertices"][index][0]))

    def test_same_geometry_changed_source_revision_rejects_stale_dimension_request(self):
        geometries = self.fixture.separated_pair()
        geometries[0]["revision"], geometries[1]["revision"] = "source-A-1", "source-B-1"
        first = self.read(geometries)
        self.assertFalse(first.get("isError", False), first)
        section = first["structuredContent"]["result"]["section"]
        request = self.request(section, *self.extremes(section))
        geometries[1]["revision"] = "source-B-2"
        second = self.read(geometries, dimensions=request, sheet={})
        self.assertTrue(second.get("isError", False), second)
        result = second["structuredContent"]["result"]
        self.assertEqual(result["dimensions"]["error_code"], "section_dimension_revision_mismatch")
        self.assertNotEqual(result["section"]["dimension_revision"], section["dimension_revision"])
        self.assertEqual(result["section"]["vertices"], section["vertices"])
        self.assertTrue(all(source["success"] for source in result["sources"]))
        self.assertIsNone(result["drawing"])

    def test_dimension_can_measure_between_explicit_vertices_from_two_sources(self):
        geometries = self.fixture.separated_pair()
        first = self.read(geometries)
        section = first["structuredContent"]["result"]["section"]
        selected = self.extremes(section)
        response = self.read(geometries, dimensions=self.request(section, *selected),
                             sheet={"paper_size": "A4", "scale_denominator": 1}, include_svg=True, include_dxf=True)
        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["dimensions"]["items"][0]["value_mm"], 40)
        self.assertEqual(result["drawing"]["sheet"]["linear_dimensions"][0]["value_mm"], 40)
        self.assertEqual([row["refno"] for row in result["section"]["source_sections"]], ["=1/2", "=1/3"])
        self.assertTrue(any(item["type"] == "image" for item in response["content"]))
        self.fixture.assert_no_writes()

    def test_coincident_vertices_from_different_sources_do_not_invent_axis_distance(self):
        geometries = [self.fixture.geometry(), self.fixture.geometry(refno="=1/3", name="/COINCIDENT")]
        first = self.read(geometries)
        self.assertFalse(first.get("isError", False), first)
        section = first["structuredContent"]["result"]["section"]
        vertex_groups = []
        for source in section["source_sections"]:
            vertex_groups.append({vertex for index in source["segment_indices"]
                                  for vertex in section["segments"][index]["vertices"]})
        pair = next((a, b) for a in vertex_groups[0] for b in vertex_groups[1]
                    if section["vertices"][a] == section["vertices"][b])
        self.assertNotEqual(*pair)
        response = self.read(geometries, dimensions=self.request(section, *pair), sheet={})
        result = response["structuredContent"]["result"]
        self.assertTrue(response.get("isError", False), response)
        self.assertEqual(result["dimensions"]["error_code"], "section_dimension_zero_axis_distance")
        self.assertTrue(result["section"]["success"])
        self.assertIsNone(result["drawing"])

    def test_requested_hatch_failure_retains_resolved_dimension_and_source_evidence(self):
        geometries = [self.fixture.geometry(), self.fixture.geometry(refno="=1/3", position=[5, 0, 0])]
        first = self.read(geometries)
        section = first["structuredContent"]["result"]["section"]
        response = self.read(geometries, dimensions=self.request(section, *self.extremes(section)), sheet={},
                             hatch={"spacing_mm": 5, "angle_degrees": 0})
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["error_code"], "section_hatching_failed")
        self.assertTrue(result["dimensions"]["success"])
        self.assertEqual(result["dimensions"]["items"][0]["value_mm"], 15)
        self.assertTrue(result["section"]["success"])
        self.assertTrue(all(source["success"] for source in result["sources"]))
        self.assertEqual(result["hatching"]["lines"], [])
        self.assertIsNone(result["drawing"])
        self.fixture.assert_no_image(response)

    def test_assembly_missing_sheet_is_rejected_before_context_and_geometry(self):
        dimensions = {"expected_section_revision": "a" * 64, "items": [
            {"first_vertex": 0, "second_vertex": 1, "axis": "horizontal"}]}
        response = self.fixture.call(dimensions=dimensions)
        self.assertTrue(response.get("isError", False), response)
        for name in ("health", "project", "primitive_geometry", "panel_geometry"):
            getattr(self.fixture, name).assert_not_called()


if __name__ == "__main__":
    unittest.main()

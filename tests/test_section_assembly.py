from __future__ import annotations

import copy
import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.drawing_assembly import WORLD_FRAME, combine_drawing_meshes
from aveva_e3d_mcp.mesh_section import slice_triangle_mesh
from aveva_e3d_mcp.section_assembly import annotate_section_diagnostics, annotate_section_sources
from aveva_e3d_mcp.section_hatching import build_section_hatching


BOX_VERTICES = [
    [-1, -2, -3], [1, -2, -3], [1, 2, -3], [-1, 2, -3],
    [-1, -2, 3], [1, -2, 3], [1, 2, 3], [-1, 2, 3],
]
BOX_TRIANGLES = [
    [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
    [0, 1, 5], [0, 5, 4], [3, 7, 6], [3, 6, 2],
    [0, 4, 7], [0, 7, 3], [1, 2, 6], [1, 6, 5],
]
Z_PLANE = {"origin": [0, 0, 0], "normal": [0, 0, 1]}


def source(index, transform=lambda x, y, z: [x, y, z], vertices=BOX_VERTICES, triangles=BOX_TRIANGLES):
    refno = f"=1/{index + 1}"
    return {"success": True, "available": True, "complete": True, "coordinate_space": "WORLD",
            "resolved_refno": refno, "resolved_type": "BOX", "derived_mesh": {
                "success": True, "available": True, "complete": True, "refno": refno,
                "coordinate_frame": WORLD_FRAME, "coordinate_unit": "mm", "native_tessellation": False,
                "cuts_applied": False, "vertices": [transform(*point) for point in vertices], "triangles": copy.deepcopy(triangles)}}


def assembly_section(*sources, plane=Z_PLANE):
    merged = combine_drawing_meshes(list(sources))
    if not merged["success"]:
        raise AssertionError(merged)
    section = slice_triangle_mesh(merged["vertices"], merged["triangles"], plane=plane)
    section["coordinate_unit"] = "mm"
    return section, merged["source_ranges"]


class SectionAssemblyTests(unittest.TestCase):
    def test_disjoint_sources_map_global_and_local_triangle_indices_without_mutation(self):
        section, ranges = assembly_section(source(0), source(1, lambda x, y, z: [x + 5, y, z]))
        before = copy.deepcopy((section, ranges))
        result = annotate_section_sources(section, ranges)
        self.assertEqual((section, ranges), before)
        self.assertEqual(len(result["source_sections"]), 2)
        for source_index, row in enumerate(result["source_sections"]):
            self.assertEqual(row["source_index"], source_index)
            self.assertEqual(row["refno"], f"=1/{source_index + 1}")
            self.assertTrue(row["intersection"])
            self.assertEqual(len(row["segment_indices"]), 8)
            self.assertEqual(len(row["loop_indices"]), 1)
            for index in row["segment_indices"]:
                segment = result["segments"][index]
                self.assertEqual(segment["source_index"], source_index)
                self.assertEqual(segment["refno"], row["refno"])
                self.assertEqual(segment["source_triangle_indices"],
                                 [triangle - 12 * source_index for triangle in segment["source_triangles"]])
                self.assertEqual(segment["source_triangles"], section["segments"][index]["source_triangles"])
            self.assertEqual(result["loops"][row["loop_indices"][0]]["source_index"], source_index)
        result["vertices"][0][0] = 999
        result["segments"][0]["source_triangles"].append(999)
        result["plane"]["origin"][0] = 999
        self.assertEqual((section, ranges), before)

    def test_miss_sources_are_retained_and_all_miss_succeeds(self):
        for plane, expected in ((Z_PLANE, [True, False]),
                                ({"origin": [0, 0, 20], "normal": [0, 0, 1]}, [False, False])):
            section, ranges = assembly_section(source(0), source(1, lambda x, y, z: [x + 5, y, z + 10]), plane=plane)
            result = annotate_section_sources(section, ranges)
            self.assertEqual([row["intersection"] for row in result["source_sections"]], expected)
            for row, hit in zip(result["source_sections"], expected):
                if not hit:
                    self.assertEqual(row["segment_indices"], [])
                    self.assertEqual(row["loop_indices"], [])

    def test_overlapping_contact_coincident_and_nested_shells_remain_separate_sources(self):
        transforms = [lambda x, y, z: [x + 1, y, z], lambda x, y, z: [x + 2, y, z],
                      lambda x, y, z: [x, y, z], lambda x, y, z: [x * .5, y * .5, z * .5],
                      lambda x, y, z: [(x - y) / math.sqrt(2), (x + y) / math.sqrt(2), z]]
        for index, transform in enumerate(transforms):
            with self.subTest(index=index):
                section, ranges = assembly_section(source(0), source(1, transform))
                result = annotate_section_sources(section, ranges)
                self.assertEqual(len(result["loops"]), 2)
                self.assertEqual({loop["source_index"] for loop in result["loops"]}, {0, 1})
                hatching = build_section_hatching(result, {"spacing_mm": .5, "angle_degrees": 0})
                self.assertEqual(hatching["success"], index == 3)
                if index != 3:
                    self.assertEqual(hatching["lines"], [])

    def test_coplanar_source_makes_whole_section_fail_and_cannot_be_annotated(self):
        section, ranges = assembly_section(source(0), source(1, lambda x, y, z: [x + 5, y, z + 3]))
        self.assertFalse(section["success"])
        self.assertEqual(section["error_code"], "section_coplanar_triangles_unsupported")
        self.assertEqual(section["segments"], [])
        with self.assertRaisesRegex(ValueError, "successful_complete_section_required"):
            annotate_section_sources(section, ranges)

    def test_plane_aligned_edge_preserves_both_local_triangle_sources(self):
        vertices = [[-1, -1, -1], [1, -1, -1], [0, 1, -1], [0, 0, 1]]
        triangles = [[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]]
        section, ranges = assembly_section(source(0), source(1, vertices=vertices, triangles=triangles),
                                           plane={"origin": [0, 0, 0], "normal": [1, 0, 0]})
        result = annotate_section_sources(section, ranges)
        segments = [segment for segment in result["segments"] if segment["source_index"] == 1 and len(segment["source_triangles"]) == 2]
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["source_triangles"], [14, 15])
        self.assertEqual(segments[0]["source_triangle_indices"], [2, 3])

    def test_segment_triangles_must_resolve_to_one_source_and_within_ranges(self):
        section, ranges = assembly_section(source(0), source(1, lambda x, y, z: [x + 5, y, z]))
        for triangles, error in (([4, 16], "segment_crosses_sources"), ([-1], "invalid_source_triangles"),
                                 ([24], "invalid_source_triangles"), ([True], "invalid_source_triangles"),
                                 ([4, 4], "invalid_source_triangles"), ([], "invalid_source_triangles")):
            altered = copy.deepcopy(section)
            altered["segments"][0]["source_triangles"] = triangles
            with self.subTest(triangles=triangles), self.assertRaisesRegex(ValueError, error):
                annotate_section_sources(altered, ranges)

    def test_single_loop_cannot_mix_source_segments(self):
        section, ranges = assembly_section(source(0), source(1, lambda x, y, z: [x + 5, y, z]))
        section["segments"][section["loops"][0]["segment_indices"][0]]["source_triangles"] = [16]
        with self.assertRaisesRegex(ValueError, "loop_crosses_sources"):
            annotate_section_sources(section, ranges)

    def test_ranges_are_bounded_contiguous_unique_and_match_counts(self):
        section, ranges = assembly_section(source(0), source(1, lambda x, y, z: [x + 5, y, z]))
        mutations = [lambda r: r[0].__setitem__("source_index", True),
                     lambda r: r[0].__setitem__("source_index", 0.0),
                     lambda r: r[0].__setitem__("source_index", 1),
                     lambda r: r[1].__setitem__("refno", "=01/01"),
                     lambda r: r[1].__setitem__("resolved_type", "ZONE"),
                     lambda r: r[1].__setitem__("triangle_range", [11, 24]),
                     lambda r: r[1].__setitem__("triangle_range", [13, 24]),
                     lambda r: r[1].__setitem__("triangle_range", [12, 12]),
                     lambda r: r[1].__setitem__("triangle_range", [12, 20001]),
                     lambda r: r[0].__setitem__("triangle_count", True),
                     lambda r: r[0].__setitem__("triangle_count", 10)]
        for mutation in mutations:
            altered = copy.deepcopy(ranges)
            mutation(altered)
            with self.subTest(ranges=altered), self.assertRaises(ValueError):
                annotate_section_sources(section, altered)
        for invalid in (None, [], ranges * 9):
            with self.assertRaises(ValueError):
                annotate_section_sources(section, invalid)
        section["counts"]["source_triangles"] -= 1
        with self.assertRaisesRegex(ValueError, "source_triangle_count_mismatch"):
            annotate_section_sources(section, ranges)

    def test_bad_loop_coverage_or_directions_fail_without_input_mutation(self):
        section, ranges = assembly_section(source(0))
        mutations = [lambda s: s["loops"][0]["segment_indices"].__setitem__(1, s["loops"][0]["segment_indices"][0]),
                     lambda s: s["loops"][0]["vertices"].pop(),
                     lambda s: s["segments"][0]["vertices"].reverse(),
                     lambda s: s["vertices"].append([1, 2]),
                     lambda s: s["segments"].append(copy.deepcopy(s["segments"][0])),
                     lambda s: s.__setitem__("intersection", False)]
        for mutation in mutations:
            altered = copy.deepcopy(section)
            mutation(altered)
            before = copy.deepcopy(altered)
            with self.assertRaises(ValueError):
                annotate_section_sources(altered, ranges)
            self.assertEqual(altered, before)

    def test_segment_limit_is_enforced(self):
        section, ranges = assembly_section(source(0))
        with patch("aveva_e3d_mcp.section_assembly.MAX_SECTION_SEGMENTS", 7), self.assertRaises(ValueError):
            annotate_section_sources(section, ranges)


class SectionDiagnosticSourceTests(unittest.TestCase):
    def test_actual_second_source_coplanar_triangle_keeps_global_and_maps_local(self):
        section, ranges = assembly_section(source(0), source(1, lambda x, y, z: [x + 5, y, z + 3]))
        before = copy.deepcopy((section, ranges))
        result = annotate_section_diagnostics(section, ranges)
        self.assertEqual(result["error_code"], "section_coplanar_triangles_unsupported")
        self.assertFalse(result["success"])
        self.assertEqual(result["vertices"], [])
        self.assertEqual(result["segments"], [])
        for diagnostic in result["diagnostics"]:
            self.assertTrue(diagnostic["source_mapping_available"])
            self.assertGreaterEqual(diagnostic["triangle"], 12)
            self.assertEqual(diagnostic["source_evidence"], [{"source_index": 1, "refno": "=1/2",
                                                               "local_triangle_indices": [diagnostic["triangle"] - 12]}])
        self.assertEqual((section, ranges), before)
        result["diagnostics"][0]["source_evidence"][0]["local_triangle_indices"].append(999)
        self.assertEqual((section, ranges), before)

    def test_actual_second_source_tangent_vertex_maps_source_vertex(self):
        section, ranges = assembly_section(source(0), source(1, lambda x, y, z: [x + 1, y + 2, z + 3]),
                                           plane={"origin": [0, 0, 0], "normal": [1, 1, 1]})
        self.assertEqual(section["error_code"], "section_tangent_vertices_unsupported")
        result = annotate_section_diagnostics(section, ranges)
        diagnostic = result["diagnostics"][0]
        self.assertEqual(diagnostic["vertex"], 8)
        self.assertEqual(diagnostic["source_evidence"], [{"source_index": 1, "refno": "=1/2", "local_vertex_indices": [0]}])

    def test_actual_tangent_edge_maps_both_vertex_and_triangle_indices(self):
        section, ranges = assembly_section(source(0), source(1, lambda x, y, z: [x + 1, y + 2, z]),
                                           plane={"origin": [0, 0, 0], "normal": [1, 1, 0]})
        self.assertEqual(section["error_code"], "section_tangent_edges_unsupported")
        result = annotate_section_diagnostics(section, ranges)
        diagnostic = result["diagnostics"][0]
        self.assertEqual(diagnostic["source_evidence"], [{"source_index": 1, "refno": "=1/2",
                                                         "local_vertex_indices": [0, 4],
                                                         "local_triangle_indices": [5, 8]}])
        self.assertEqual(diagnostic["edge"], section["diagnostics"][0]["edge"])
        self.assertEqual(diagnostic["source_triangles"], section["diagnostics"][0]["source_triangles"])

    def test_edge_evidence_can_reference_two_sources_without_merging_identities(self):
        section, ranges = assembly_section(source(0), source(1, lambda x, y, z: [x + 5, y, z]))
        section.update(success=False, available=False, error_code="section_tangent_edges_unsupported",
                       diagnostics=[{"code": "tangent_plane_edge", "edge": [0, 8], "source_triangles": [5, 17]}])
        result = annotate_section_diagnostics(section, ranges)
        self.assertEqual(result["diagnostics"][0]["source_evidence"], [
            {"source_index": 0, "refno": "=1/1", "local_vertex_indices": [0], "local_triangle_indices": [5]},
            {"source_index": 1, "refno": "=1/2", "local_vertex_indices": [0], "local_triangle_indices": [5]}])
        self.assertFalse(result["success"])
        self.assertEqual(result["segments"], section["segments"])

    def test_unknown_bad_and_result_vertex_indices_are_never_guessed(self):
        section, ranges = assembly_section(source(0))
        section["diagnostics"] = [
            {"code": "unknown", "vertex": 0}, {"code": "near_plane_vertex", "vertex": 8},
            {"code": "near_plane_vertex", "vertex": True}, {"code": "coplanar_triangle", "triangle": -1},
            {"code": "tangent_plane_edge", "edge": [0, 1], "source_triangles": [5, 12]},
            {"code": "tangent_plane_edge", "edge": [0], "source_triangles": [5]},
            {"loop": 0, "code": "section_loop_metric_not_representable"}, None]
        before = copy.deepcopy(section)
        result = annotate_section_diagnostics(section, ranges)
        for diagnostic in result["diagnostics"][:-1]:
            self.assertFalse(diagnostic["source_mapping_available"])
            self.assertEqual(diagnostic["source_evidence"], [])
            self.assertIsInstance(diagnostic["source_mapping_reason"], str)
        self.assertIsNone(result["diagnostics"][-1])
        self.assertEqual(section, before)
        section.update(error_code="section_contour_not_degree_two", diagnostics=[{"vertex": 0, "degree": 4}])
        result = annotate_section_diagnostics(section, ranges)
        self.assertEqual(result["diagnostics"][0]["source_mapping_reason"], "section_vertex_indices_are_not_source_vertices")

    def test_invalid_ranges_do_not_crash_or_alter_original_status(self):
        section, ranges = assembly_section(source(0))
        section.update(success=False, available=False, error_code="section_coplanar_triangles_unsupported",
                       diagnostics=[{"code": "coplanar_triangle", "triangle": 0}])
        for bad_ranges in (None, [], [None], [{**ranges[0], "vertex_range": [0, 10001]}],
                           [{**ranges[0], "triangle_range": [1, 12]}]):
            with self.subTest(ranges=bad_ranges):
                result = annotate_section_diagnostics(section, bad_ranges)
                self.assertFalse(result["success"])
                self.assertEqual(result["error_code"], section["error_code"])
                self.assertEqual(result["diagnostics"][0]["source_mapping_reason"], "invalid_source_ranges")
                self.assertEqual(result["diagnostics"][0]["source_evidence"], [])

    def test_unnamed_source_triangle_diagnostic_and_unknown_diagnostic_shape(self):
        section, ranges = assembly_section(source(0))
        section.update(error_code="section_triangle_intersection_ambiguous", diagnostics=[{"triangle": 5}])
        result = annotate_section_diagnostics(section, ranges)
        self.assertEqual(result["diagnostics"][0]["source_evidence"][0]["local_triangle_indices"], [5])
        section["diagnostics"] = None
        self.assertEqual(annotate_section_diagnostics(section, ranges), section)

    def test_range_totals_must_match_observed_source_counts_before_attribution(self):
        section, ranges = assembly_section(source(0))
        section.update(diagnostics=[{"code": "coplanar_triangle", "triangle": 0}])
        ranges[0]["triangle_range"][1] += 1
        ranges[0]["triangle_count"] += 1
        result = annotate_section_diagnostics(section, ranges)
        self.assertFalse(result["diagnostics"][0]["source_mapping_available"])
        self.assertEqual(result["diagnostics"][0]["source_mapping_reason"], "source_range_count_mismatch")


if __name__ == "__main__":
    unittest.main()

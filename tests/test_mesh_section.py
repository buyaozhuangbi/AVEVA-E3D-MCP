from __future__ import annotations

import copy
import json
import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.mesh_section import normalize_section_plane, slice_triangle_mesh


BOX_VERTICES = [
    [-1, -2, -3], [1, -2, -3], [1, 2, -3], [-1, 2, -3],
    [-1, -2, 3], [1, -2, 3], [1, 2, 3], [-1, 2, 3],
]
BOX_TRIANGLES = [
    [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
    [0, 1, 5], [0, 5, 4], [3, 7, 6], [3, 6, 2],
    [0, 4, 7], [0, 7, 3], [1, 2, 6], [1, 6, 5],
]
TETRA_VERTICES = [[-1, -1, -1], [1, -1, -1], [0, 1, -1], [0, 0, 1]]
TETRA_TRIANGLES = [[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]]
Z_PLANE = {"origin": [0, 0, 0], "normal": [0, 0, 1]}


class SectionPlaneTests(unittest.TestCase):
    def test_default_and_explicit_basis_are_orthonormal(self):
        default = normalize_section_plane(Z_PLANE)
        self.assertEqual(default["right"], [1.0, 0.0, 0.0])
        self.assertEqual(default["up"], [0.0, 1.0, 0.0])
        self.assertEqual(default["up_source"], "default_Y")
        for normal, up in (([1, 2, 3], [5, 7, 9]), ([0, 0, 1], [1, 0, 0])):
            with self.subTest(normal=normal, up=up):
                basis = normalize_section_plane({"origin": [1, 2, 3], "normal": normal, "up": up})
                vectors = [basis[name] for name in ("right", "up", "normal")]
                for vector in vectors:
                    self.assertAlmostEqual(math.hypot(*vector), 1)
                for i, left in enumerate(vectors):
                    for right in vectors[i + 1:]:
                        self.assertAlmostEqual(sum(a * b for a, b in zip(left, right)), 0)

    def test_huge_and_subnormal_normal_and_up_are_supported_without_mutation(self):
        for factor in (1e308, 5e-324):
            plane = {"origin": [1, 2, 3], "normal": [0, 0, factor], "up": [factor, 0, 0]}
            before = copy.deepcopy(plane)
            basis = normalize_section_plane(plane)
            self.assertEqual(basis["normal"], [0.0, 0.0, 1.0])
            self.assertEqual(basis["up"], [1.0, 0.0, 0.0])
            self.assertEqual(plane, before)

    def test_plane_validation_rejects_bad_keys_types_and_parallel_up(self):
        bad_planes = [None, [], {}, {"origin": [0, 0, 0]}, {**Z_PLANE, "extra": True},
                      {**Z_PLANE, "origin": [0, 0]}, {**Z_PLANE, "origin": [True, 0, 0]},
                      {**Z_PLANE, "origin": [1e9 + 1, 0, 0]},
                      {**Z_PLANE, "normal": [0, 0, 0]}, {**Z_PLANE, "normal": [0, math.inf, 1]},
                      {**Z_PLANE, "normal": [0, math.nan, 1]}, {**Z_PLANE, "normal": [0, "1", 1]},
                      {**Z_PLANE, "up": [0, 0, 0]}, {**Z_PLANE, "up": [0, 0, -1]},
                      {**Z_PLANE, "up": [1e-14, 0, 1]}]
        for plane in bad_planes:
            with self.subTest(plane=plane), self.assertRaises(ValueError):
                normalize_section_plane(plane)


class MeshSectionTests(unittest.TestCase):
    def call(self, vertices=BOX_VERTICES, triangles=BOX_TRIANGLES, plane=Z_PLANE):
        return slice_triangle_mesh(vertices, triangles, plane=plane)

    def assertEmptyFailure(self, result, code):
        self.assertFalse(result["success"], result)
        self.assertFalse(result["available"])
        self.assertEqual(result["error_code"], code)
        self.assertIsNone(result["intersection"])
        for key in ("vertices", "segments", "loops"):
            self.assertEqual(result[key], [])
        self.assertIsNone(result["bounds"])

    def test_box_cut_has_closed_oriented_loop_and_source_faces(self):
        result = self.call()
        self.assertTrue(result["success"], result)
        self.assertTrue(result["intersection"])
        self.assertTrue(result["complete"])
        self.assertEqual(result["counts"], {"source_vertices": 8, "source_triangles": 12,
                                            "vertices": 8, "segments": 8, "loops": 1})
        self.assertEqual(result["bounds"], {"min": [-1.0, -2.0], "max": [1.0, 2.0]})
        loop = result["loops"][0]
        self.assertEqual(loop["vertices"][0], loop["vertices"][-1])
        self.assertEqual(sorted(loop["segment_indices"]), list(range(8)))
        self.assertAlmostEqual(loop["signed_area"], 8)
        self.assertAlmostEqual(loop["perimeter"], 12)
        self.assertEqual(sorted(segment["source_triangles"][0] for segment in result["segments"]), list(range(4, 12)))
        for i, segment_index in enumerate(loop["segment_indices"]):
            self.assertEqual(result["segments"][segment_index]["vertices"], loop["vertices"][i:i + 2])
        self.assertFalse(result["hole_semantics_inferred"])
        self.assertFalse(result["boolean_operations_applied"])
        self.assertFalse(result["geometric_self_intersection_checked"])

    def test_oblique_cut_has_analytic_area_and_perimeter(self):
        result = self.call(plane={"origin": [0, 0, 0], "normal": [1, 1, 0]})
        self.assertTrue(result["success"], result)
        self.assertAlmostEqual(result["loops"][0]["signed_area"], 12 * math.sqrt(2))
        self.assertAlmostEqual(result["loops"][0]["perimeter"], 12 + 4 * math.sqrt(2))

    def test_exact_vertex_crossing_survives_normalization_roundoff(self):
        result = self.call(plane={"origin": [0, 0, 0], "normal": [1, 1, 1]})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["counts"]["loops"], 1)
        tetra = self.call(TETRA_VERTICES, TETRA_TRIANGLES,
                          {"origin": [-1, -1, -1], "normal": [1, -1, 1]})
        self.assertTrue(tetra["success"], tetra)
        self.assertEqual(tetra["counts"]["segments"], 3)

    def test_plane_edge_shared_by_opposite_triangles_keeps_both_sources(self):
        result = self.call(TETRA_VERTICES, TETRA_TRIANGLES, {"origin": [0, 0, 0], "normal": [1, 0, 0]})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["counts"]["segments"], 3)
        self.assertEqual([segment["source_triangles"] for segment in result["segments"] if len(segment["source_triangles"]) == 2], [[2, 3]])

    def test_coplanar_tangent_edge_and_tangent_vertex_fail_without_partial_geometry(self):
        cases = [({"origin": [0, 0, 3], "normal": [0, 0, 1]}, "section_coplanar_triangles_unsupported"),
                 ({"origin": [1, 2, 0], "normal": [1, 1, 0]}, "section_tangent_edges_unsupported"),
                 ({"origin": [1, 2, 3], "normal": [1, 1, 1]}, "section_tangent_vertices_unsupported")]
        for plane, code in cases:
            with self.subTest(code=code):
                self.assertEmptyFailure(self.call(plane=plane), code)

    def test_near_vertex_and_tiny_loop_are_not_silently_snapped_or_lost(self):
        plane = {"origin": [1 - 1e-13, 2, 3], "normal": [1, 1, 1]}
        self.assertEmptyFailure(self.call(plane=plane), "section_plane_vertex_relation_ambiguous")

    def test_miss_succeeds_with_explicit_empty_intersection(self):
        result = self.call(plane={"origin": [0, 0, 4], "normal": [0, 0, 1]})
        self.assertTrue(result["success"], result)
        self.assertFalse(result["intersection"])
        self.assertEqual(result["vertices"], [])
        self.assertEqual(result["segments"], [])
        self.assertEqual(result["loops"], [])
        self.assertIsNone(result["bounds"])

    def test_invalid_open_inward_and_over_limit_meshes_fail_closed(self):
        for vertices, triangles in ((BOX_VERTICES, BOX_TRIANGLES[:-1]),
                                    (BOX_VERTICES, [face[::-1] for face in BOX_TRIANGLES]),
                                    (BOX_VERTICES, [[0, 1, 999]]),
                                    (BOX_VERTICES + [[0, 0, 0]] * 10000, BOX_TRIANGLES),
                                    (BOX_VERTICES, BOX_TRIANGLES * 2000), (None, None)):
            with self.subTest(vertices_count=len(vertices) if vertices else None):
                self.assertEmptyFailure(self.call(vertices, triangles), "closed_valid_outward_mesh_required")

    def test_separate_and_coincident_shells_are_never_coordinate_welded(self):
        for translation in (0, 5):
            vertices = BOX_VERTICES + [[x + translation, y, z] for x, y, z in BOX_VERTICES]
            triangles = BOX_TRIANGLES + [[index + 8 for index in face] for face in BOX_TRIANGLES]
            result = self.call(vertices, triangles)
            self.assertTrue(result["success"], result)
            self.assertEqual(result["counts"]["loops"], 2)
            self.assertEqual(result["counts"]["vertices"], 16)
            self.assertTrue(all(loop["signed_area"] > 0 for loop in result["loops"]))

    def test_projection_rotations_translation_and_scale_preserve_metrics(self):
        for factor in (1e-100, 1, 1e100):
            result = self.call([[value * factor for value in point] for point in BOX_VERTICES])
            self.assertTrue(result["success"], result)
            self.assertAlmostEqual(result["loops"][0]["signed_area"] / (factor * factor), 8)
            self.assertAlmostEqual(result["loops"][0]["perimeter"] / factor, 12)
        translated = self.call([[x + 10, y + 20, z + 30] for x, y, z in BOX_VERTICES],
                               plane={"origin": [10, 20, 30], "normal": [0, 0, 1], "up": [1, 0, 0]})
        self.assertTrue(translated["success"], translated)
        self.assertEqual(translated["bounds"], {"min": [-2.0, -1.0], "max": [2.0, 1.0]})
        self.assertAlmostEqual(translated["loops"][0]["signed_area"], 8)

    def test_metric_overflow_is_unavailable_without_losing_geometry_or_json_safety(self):
        result = self.call([[value * 1e160 for value in point] for point in BOX_VERTICES])
        self.assertTrue(result["success"], result)
        self.assertEqual(result["counts"]["segments"], 8)
        loop = result["loops"][0]
        self.assertFalse(loop["area_available"])
        self.assertIsNone(loop["signed_area"])
        self.assertTrue(loop["perimeter_available"])
        self.assertEqual(result["diagnostics"][0]["code"], "section_loop_metric_not_representable")
        json.dumps(result, allow_nan=False)

    def test_segment_budget_fails_with_no_partial_loops(self):
        with patch("aveva_e3d_mcp.mesh_section.MAX_SECTION_SEGMENTS", 7):
            self.assertEmptyFailure(self.call(), "section_segment_limit_exceeded")

    def test_deterministic_json_safe_result_and_inputs_are_not_mutated(self):
        before = copy.deepcopy((BOX_VERTICES, BOX_TRIANGLES, Z_PLANE))
        first, second = self.call(), self.call()
        self.assertEqual(first, second)
        self.assertEqual(json.dumps(first, allow_nan=False), json.dumps(second, allow_nan=False))
        self.assertEqual((BOX_VERTICES, BOX_TRIANGLES, Z_PLANE), before)

    def test_invalid_plane_failure_has_no_geometry(self):
        self.assertEmptyFailure(self.call(plane={"origin": [0, 0, 0], "normal": [0, 0, 0]}), "zero_section_normal")


if __name__ == "__main__":
    unittest.main()

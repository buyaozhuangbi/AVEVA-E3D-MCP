from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.triangle_mesh_validation import validate_triangle_mesh


VERTICES = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]]
TETRA = [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]


class TriangleMeshValidationTests(unittest.TestCase):
    def test_outward_tetrahedron_is_valid_and_reports_signed_volume(self):
        result = validate_triangle_mesh(VERTICES, TETRA)
        self.assertTrue(result["valid"], result["findings"])
        self.assertTrue(result["checks"]["closed_manifold"])
        self.assertTrue(result["checks"]["orientation_outward"])
        self.assertEqual(result["components"][0]["orientation"], "outward")
        self.assertAlmostEqual(result["components"][0]["signed_volume"], 1.0 / 6.0)

    def test_inward_tetrahedron_is_closed_but_not_valid(self):
        inward = [[a, c, b] for a, b, c in TETRA]
        result = validate_triangle_mesh(VERTICES, inward)
        self.assertTrue(result["checks"]["closed_manifold"])
        self.assertTrue(result["checks"]["winding_consistent"])
        self.assertEqual(result["components"][0]["orientation"], "inward")
        self.assertAlmostEqual(result["components"][0]["signed_volume"], -1.0 / 6.0)
        self.assertFalse(result["valid"])

    def test_open_surface_reports_boundary_and_unknown_volume(self):
        result = validate_triangle_mesh(VERTICES, TETRA[:-1])
        self.assertFalse(result["checks"]["closed_manifold"])
        self.assertEqual(result["components"][0]["signed_volume"], None)
        self.assertEqual(result["components"][0]["orientation"], "unknown")
        self.assertIn("boundary_edges_present", {item["code"] for item in result["findings"]})

    def test_nonmanifold_edge_and_bow_tie_vertex_are_reported(self):
        edge = validate_triangle_mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1]],
                                      [[0, 1, 2], [1, 0, 3], [0, 1, 4]])
        self.assertFalse(edge["checks"]["edge_manifold"])
        self.assertIn("nonmanifold_edges_present", {item["code"] for item in edge["findings"]})

        vertices = VERTICES + [[-1, 0, 0], [0, -1, 0], [0, 0, -1]]
        faces = TETRA + [[0, 5, 4], [0, 4, 6], [0, 6, 5], [4, 5, 6]]
        bow_tie = validate_triangle_mesh(vertices, faces)
        self.assertFalse(bow_tie["checks"]["vertex_manifold"])
        self.assertIn("nonmanifold_vertex_link", {item["code"] for item in bow_tie["findings"]})

    def test_shared_edge_same_direction_duplicate_and_degenerate_faces(self):
        same_direction = validate_triangle_mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, -1, 0]],
                                                [[0, 1, 2], [0, 1, 3]])
        self.assertFalse(same_direction["checks"]["winding_consistent"])
        self.assertIn("inconsistent_shared_edge_winding", {item["code"] for item in same_direction["findings"]})

        duplicate = validate_triangle_mesh(VERTICES, TETRA + [TETRA[0]])
        self.assertFalse(duplicate["checks"]["duplicate_faces"])
        self.assertIn("duplicate_triangle", {item["code"] for item in duplicate["findings"]})

        degenerate = validate_triangle_mesh([[0, 0, 0], [1, 0, 0], [2, 0, 0]], [[0, 1, 2]])
        self.assertFalse(degenerate["checks"]["nondegenerate_triangles"])
        self.assertIn("degenerate_or_near_degenerate_triangle",
                      {item["code"] for item in degenerate["findings"]})

    def test_invalid_coordinates_and_indices_fail_without_exception(self):
        for invalid in (float("nan"), float("inf"), -float("inf"), True, 10**10000):
            result = validate_triangle_mesh([[invalid, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]])
            self.assertFalse(result["checks"]["finite_coordinates"])
        for index in (-1, 4, 1.0, True):
            result = validate_triangle_mesh(VERTICES, [[0, 1, index]])
            self.assertFalse(result["checks"]["valid_indices"])
        result = validate_triangle_mesh([[0, 0], [1, 0], [0, 1]], [[0, 1, 2]])
        self.assertFalse(result["checks"]["finite_coordinates"])

    def test_components_are_measured_separately_without_volume_cancellation(self):
        vertices = VERTICES + [[4 + x, y, z] for x, y, z in VERTICES]
        triangles = TETRA + [[a + 4, c + 4, b + 4] for a, b, c in TETRA]
        result = validate_triangle_mesh(vertices, triangles)
        self.assertEqual(len(result["components"]), 2)
        self.assertEqual([component["orientation"] for component in result["components"]], ["outward", "inward"])
        self.assertAlmostEqual(result["components"][0]["signed_volume"], 1 / 6)
        self.assertAlmostEqual(result["components"][1]["signed_volume"], -1 / 6)
        self.assertFalse(result["valid"])

    def test_relative_area_threshold_is_scale_invariant(self):
        for scale in (1e-110, 1e-6, 1.0, 1e6, 1e110):
            points = [[x * scale, y * scale, z * scale] for x, y, z in VERTICES]
            result = validate_triangle_mesh(points, TETRA)
            self.assertTrue(result["checks"]["nondegenerate_triangles"])
            self.assertEqual(result["components"][0]["orientation"], "outward")
            if scale < 1e-100 or scale > 1e100:
                self.assertIsNone(result["components"][0]["signed_volume"])
                self.assertFalse(result["components"][0]["volume_available"])
                self.assertTrue(result["valid"])
            else:
                self.assertTrue(result["components"][0]["volume_available"])

    def test_representable_volume_does_not_overflow_in_intermediate_scale_cube(self):
        scale = 1e103
        points = [[x * scale, y * scale, z * scale] for x, y, z in VERTICES]
        result = validate_triangle_mesh(points, TETRA)
        component = result["components"][0]
        self.assertEqual(component["orientation"], "outward")
        self.assertTrue(component["volume_available"])
        self.assertTrue(math.isclose(component["signed_volume"], 1.6666666666666667e308, rel_tol=5e-15))

    def test_mesh_limits_are_enforced(self):
        points = [[float(index), 0.0, 0.0] for index in range(10001)]
        result = validate_triangle_mesh(points, [[0, 1, 2]])
        self.assertFalse(result["complete"])
        self.assertIn("mesh_limit_exceeded", {item["code"] for item in result["findings"]})


if __name__ == "__main__":
    unittest.main()

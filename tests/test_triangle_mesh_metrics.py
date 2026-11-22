from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.triangle_mesh_metrics import measure_triangle_mesh


class TriangleMeshMetricsTests(unittest.TestCase):
    def test_tetrahedron_bounds_and_surface_area(self) -> None:
        vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]]
        triangles = [[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]]
        result = measure_triangle_mesh(vertices, triangles)
        expected = (3 + math.sqrt(3)) / 2
        self.assertTrue(result["available"])
        self.assertTrue(result["valid"])
        self.assertEqual(result["bounds"], {"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0]})
        self.assertAlmostEqual(result["surface_area"], expected)
        self.assertEqual(result["area_unit"], "coordinate_unit^2; not inferred")

    def test_open_mesh_remains_measurable_but_validation_is_invalid(self) -> None:
        result = measure_triangle_mesh([[4, 5, 6], [6, 5, 6], [4, 8, 6]], [[0, 1, 2]])
        self.assertTrue(result["available"])
        self.assertFalse(result["valid"])
        self.assertEqual(result["surface_area"], 3.0)
        self.assertEqual(result["bounds"]["min"], [4.0, 5.0, 6.0])

    def test_invalid_indices_or_degenerate_faces_have_no_measurements(self) -> None:
        cases = [
            ([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 3]]),
            ([[0, 0, 0], [1, 0, 0], [2, 0, 0]], [[0, 1, 2]]),
            ([[0, 0, 0], [1, 0, 0], [0, float("nan"), 0]], [[0, 1, 2]]),
        ]
        for vertices, triangles in cases:
            with self.subTest(vertices=vertices, triangles=triangles):
                result = measure_triangle_mesh(vertices, triangles)
                self.assertFalse(result["available"])
                self.assertIsNone(result["bounds"])
                self.assertIsNone(result["surface_area"])

    def test_surface_area_scaling_does_not_overflow_intermediate_square(self) -> None:
        vertices = [[0, 0, 0], [1e150, 0, 0], [0, 1e150, 0]]
        result = measure_triangle_mesh(vertices, [[0, 1, 2]])
        self.assertTrue(result["available"])
        self.assertAlmostEqual(result["surface_area"] / 1e300, 0.5)

    def test_unrepresentable_area_is_unavailable(self) -> None:
        result = measure_triangle_mesh([[0, 0, 0], [1e200, 0, 0], [0, 1e200, 0]], [[0, 1, 2]])
        self.assertFalse(result["available"])
        self.assertIsNone(result["surface_area"])


if __name__ == "__main__":
    unittest.main()

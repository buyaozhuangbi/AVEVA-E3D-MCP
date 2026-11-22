from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.triangle_mesh_visibility import projected_edge_visibility


class ProjectedEdgeVisibilityTests(unittest.TestCase):
    def test_back_edge_is_hidden_by_nearer_projected_triangle(self):
        vertices = [
            [0, 0, 0], [1, 0, 0],  # edge behind
            [0, 0, 1], [1, 0, 1], [.5, 1, 1],  # front triangle covers the edge
        ]
        result = projected_edge_visibility(vertices, [[2, 3, 4]], [{"vertices": [0, 1]}])
        self.assertTrue(result["success"], result)
        self.assertEqual(result["edges"][0]["visible_intervals"], [])
        self.assertEqual(result["edges"][0]["hidden_intervals"], [[0.0, 1.0]])

    def test_partial_occluder_splits_edge_at_expected_parameters(self):
        vertices = [
            [0, 0, 0], [1, 0, 0],  # edge behind
            [.25, 0, 1], [.75, 0, 1], [.5, 1, 1],  # occludes only the center half
        ]
        result = projected_edge_visibility(vertices, [[2, 3, 4]], [{"vertices": [0, 1]}])
        self.assertTrue(result["success"], result)
        intervals = result["edges"][0]["hidden_intervals"]
        self.assertEqual(len(intervals), 1)
        self.assertAlmostEqual(intervals[0][0], .25)
        self.assertAlmostEqual(intervals[0][1], .75)
        visible = result["edges"][0]["visible_intervals"]
        self.assertEqual(len(visible), 2)
        self.assertEqual(visible[0][0], 0.0)
        self.assertAlmostEqual(visible[0][1], .25)
        self.assertAlmostEqual(visible[1][0], .75)
        self.assertEqual(visible[1][1], 1.0)

    def test_coplanar_equal_depth_triangle_does_not_hide(self):
        vertices = [
            [0, 0, 0], [1, 0, 0],
            [0, 0, 0], [1, 0, 0], [.5, 1, 0],
        ]
        result = projected_edge_visibility(vertices, [[2, 3, 4]], [{"vertices": [0, 1]}])
        self.assertTrue(result["success"], result)
        self.assertEqual(result["edges"][0]["visible_intervals"], [[0.0, 1.0]])
        self.assertEqual(result["edges"][0]["hidden_intervals"], [])

    def test_deep_scene_range_does_not_collapse_projection_plane(self):
        vertices = [
            [0, 0, 0], [1, 0, 0],
            [0, 0, 1e12], [1, 0, 1e12], [.5, 1, 1e12],
        ]
        result = projected_edge_visibility(vertices, [[2, 3, 4]], [{"vertices": [0, 1]}])
        self.assertTrue(result["success"], result)
        self.assertEqual(result["edges"][0]["visible_intervals"], [])
        self.assertEqual(result["edges"][0]["hidden_intervals"], [[0.0, 1.0]])

    def test_incident_source_faces_are_excluded_from_occlusion_tests(self):
        vertices = [[0, 0, 0], [1, 0, 0], [.5, 1, 1]]
        result = projected_edge_visibility(vertices, [[0, 1, 2]], [{"vertices": [0, 1]}])
        self.assertTrue(result["success"], result)
        self.assertEqual(result["edges"][0]["visible_intervals"], [[0.0, 1.0]])
        self.assertEqual(result["counts"]["edge_triangle_tests"], 0)

    def test_nonincident_triangle_shared_endpoint_has_no_false_visible_stub(self):
        for reverse in (False, True):
            vertices = [[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 1, 1]]
            edge = [1, 0] if reverse else [0, 1]
            result = projected_edge_visibility(vertices, [[0, 2, 3]], [{"vertices": edge}])
            with self.subTest(reverse=reverse):
                self.assertTrue(result["success"], result)
                self.assertEqual(result["edges"][0]["visible_intervals"], [])
                self.assertEqual(result["edges"][0]["hidden_intervals"], [[0.0, 1.0]])

    def test_unshared_narrow_visible_interval_is_preserved(self):
        vertices = [[0, 0, 0], [1, 0, 0],
                    [1e-8, 0, 1], [1, 0, 1], [1, 1, 1]]
        result = projected_edge_visibility(vertices, [[2, 3, 4]], [{"vertices": [0, 1]}])
        self.assertTrue(result["success"], result)
        visible = result["edges"][0]["visible_intervals"]
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0][0], 0.0)
        self.assertGreater(visible[0][1], 0)
        self.assertLess(visible[0][1], 1e-7)

    def test_budget_exhaustion_returns_no_partial_edge_intervals(self):
        vertices = [[0, 0, 0], [1, 0, 0],
                    [0, 0, 1], [1, 0, 1], [.5, 1, 1],
                    [0, 0, 1], [1, 0, 1], [.5, 1, 1]]
        result = projected_edge_visibility(vertices, [[2, 3, 4], [5, 6, 7]], [{"vertices": [0, 1]}],
                                           max_node_visits=1, max_triangle_tests=1)
        self.assertFalse(result["success"])
        self.assertFalse(result["available"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["error_code"], "visibility_work_budget_exceeded")
        self.assertEqual(result["edges"], [])

    def test_output_interval_limit_returns_no_partial_results(self):
        vertices = [[0, 0, 0], [1, 0, 0]]
        triangles = []
        for left, right in ((.1, .3), (.4, .6), (.7, .9)):
            first, second, apex = len(vertices), len(vertices) + 1, len(vertices) + 2
            vertices.extend([[left, -1, 1], [right, -1, 1], [(left + right) / 2, 1, 1]])
            triangles.append([first, second, apex])
        result = projected_edge_visibility(vertices, triangles, [{"vertices": [0, 1]}],
                                           max_output_intervals=2)
        self.assertFalse(result["success"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["error_code"], "visibility_output_interval_limit_exceeded")
        self.assertEqual(result["edges"], [])

    def test_invalid_indices_and_budgets_are_rejected(self):
        vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 1]]
        invalid = projected_edge_visibility(vertices, [[0, 1, 2]], [{"vertices": [0, 9]}])
        self.assertFalse(invalid["success"])
        self.assertEqual(invalid["error_code"], "edge_must_have_two_distinct_valid_vertex_indices")
        budget = projected_edge_visibility(vertices, [[0, 1, 2]], [{"vertices": [0, 1]}],
                                            max_node_visits=0)
        self.assertFalse(budget["success"])
        self.assertEqual(budget["error_code"], "invalid_visibility_work_budget")


if __name__ == "__main__":
    unittest.main()

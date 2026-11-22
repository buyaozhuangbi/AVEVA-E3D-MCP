"""Independent asymmetric depth witnesses for opt-in orthographic conventions.

The two closed boxes are separate along Y and overlap only part of their XZ
projections. Their unequal extrema make an accidental horizontal mirror visible.
No native E3D geometry, bridge calls or sheet layout implementation is used.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp import orthographic_drawing as module


BOX_FACES = [
    [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
    [0, 1, 5], [0, 5, 4], [3, 7, 6], [3, 6, 2],
    [0, 4, 7], [0, 7, 3], [1, 2, 6], [1, 6, 5],
]
FRAME = "independent asymmetric WORLD-mm fixture"
# Captured from the pre-change default generator with all three views and
# hidden lines shown, before projection_convention was introduced.
LEGACY_RESULT_SHA256 = "b74380dbc1900be64a684316c89107c914631aed67c5c5a988b55afee2c0ff24"


def box(low, high):
    x, y, z = low
    X, Y, Z = high
    return [[x, y, z], [X, y, z], [X, Y, z], [x, Y, z],
            [x, y, Z], [X, y, Z], [X, Y, Z], [x, Y, Z]]


def fixture():
    # A is nearer when looking from -Y; B is nearer from +Y.
    # A right edge (X=2,Z=0) is inside B's projected rectangle.
    # B left edge (X=-1,Z=0) is inside A's projected rectangle.
    vertices = box([-4, -5, -2], [2, -3, 2]) + box([-1, 3, -1], [5, 6, 3])
    faces = deepcopy(BOX_FACES) + [[index + 8 for index in face] for face in BOX_FACES]
    return vertices, faces


def cross(first, second):
    return [first[1]*second[2] - first[2]*second[1],
            first[2]*second[0] - first[0]*second[2],
            first[0]*second[1] - first[1]*second[0]]


def at_point(view, point):
    """Read classifications at a strict segment interior, away from joins."""
    found = []
    for line in view["lines"]:
        start, end = line["start"], line["end"]
        dx, dy = end[0] - start[0], end[1] - start[1]
        square = dx*dx + dy*dy
        assert square > 0
        parameter = ((point[0] - start[0])*dx + (point[1] - start[1])*dy) / square
        distance = abs((point[0] - start[0])*dy - (point[1] - start[1])*dx) / math.sqrt(square)
        if 1e-8 < parameter < 1 - 1e-8 and distance < 1e-8:
            found.append(line["visibility"])
    return set(found)


def line_geometry(view):
    return {tuple(sorted((tuple(row["start"]), tuple(row["end"])))) for row in view["lines"]}


class ProjectionConventionAuditTests(unittest.TestCase):
    def call(self, **options):
        vertices, triangles = fixture()
        return module.generate_orthographic_drawing(
            vertices, triangles, coordinate_frame=FRAME, **{"show_hidden_lines": True, **options})

    def view(self, result, name):
        self.assertTrue(result["success"], result)
        self.assertTrue(result["available"], result)
        self.assertTrue(result["complete"], result)
        return next(view for view in result["views"] if view["name"] == name)

    def assert_no_partial(self, result):
        self.assertFalse(result["success"], result)
        self.assertFalse(result["available"], result)
        self.assertEqual(result["views"], [])
        self.assertNotIn("svg", result)
        self.assertNotIn("dxf", result)
        self.assertNotIn("segment_count", result)

    def test_default_and_explicit_legacy_preserve_entire_old_result_and_omit_new_key(self):
        default, explicit = self.call(), self.call(projection_convention="legacy")
        self.assertEqual(default, explicit)
        self.assertNotIn("projection_convention", default)
        self.assertTrue(default["success"], default)
        encoded = json.dumps(default, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        self.assertEqual(hashlib.sha256(encoded).hexdigest(), LEGACY_RESULT_SHA256)

    def test_three_optin_bases_are_orthogonal_unit_and_right_handed(self):
        result = self.call(projection_convention="right_handed")
        self.assertEqual(result["projection_convention"], "right_handed")
        expected = {"FRONT": ([1, 0, 0], [0, 0, 1], [0, -1, 0]),
                    "TOP": ([1, 0, 0], [0, 1, 0], [0, 0, 1]),
                    "SIDE": ([0, 1, 0], [0, 0, 1], [1, 0, 0])}
        for name, axes in expected.items():
            with self.subTest(view=name):
                view = self.view(result, name)
                actual = tuple(view[key] for key in ("horizontal_axis", "vertical_axis", "toward_viewer"))
                self.assertEqual(actual, axes)
                self.assertEqual(cross(actual[0], actual[1]), actual[2])
                for axis in actual:
                    self.assertEqual(sum(value*value for value in axis), 1)
                for first, second in ((0, 1), (1, 2), (2, 0)):
                    self.assertEqual(sum(a*b for a, b in zip(actual[first], actual[second])), 0)

    def test_asymmetric_front_depth_reverses_two_independent_hidden_visible_witnesses(self):
        old = self.view(self.call(), "FRONT")
        new = self.view(self.call(projection_convention="right_handed"), "FRONT")
        self.assertEqual(old["toward_viewer"], [0, 1, 0])
        self.assertEqual(new["toward_viewer"], [0, -1, 0])
        for point, old_state, new_state in (([2, 0], "hidden", "visible"), ([-1, 0], "visible", "hidden")):
            with self.subTest(point=point):
                self.assertEqual(at_point(old, point), {old_state})
                self.assertEqual(at_point(new, point), {new_state})
        # A mirror would change these asymmetric horizontal bounds to [-5,4].
        expected_bounds = {"min": [-4.0, -2.0], "max": [5.0, 3.0]}
        self.assertEqual(old["bounds"], expected_bounds)
        self.assertEqual(new["bounds"], expected_bounds)
        self.assertEqual(old["horizontal_axis"], new["horizontal_axis"])
        self.assertEqual(old["vertical_axis"], new["vertical_axis"])

    def test_default_visible_only_front_actually_changes_which_object_edge_is_present(self):
        old = self.view(self.call(show_hidden_lines=False), "FRONT")
        new = self.view(self.call(projection_convention="right_handed", show_hidden_lines=False), "FRONT")
        self.assertEqual(at_point(old, [2, 0]), set())
        self.assertEqual(at_point(new, [2, 0]), {"visible"})
        self.assertEqual(at_point(old, [-1, 0]), {"visible"})
        self.assertEqual(at_point(new, [-1, 0]), set())

    def test_top_and_side_geometry_and_visibility_remain_exactly_equal(self):
        for show_hidden in (False, True):
            old, new = self.call(show_hidden_lines=show_hidden), self.call(
                projection_convention="right_handed", show_hidden_lines=show_hidden)
            for name in ("TOP", "SIDE"):
                with self.subTest(show_hidden=show_hidden, view=name):
                    self.assertEqual(self.view(old, name), self.view(new, name))

    def test_without_occlusion_all_three_views_keep_the_same_unmirrored_line_coordinates(self):
        old = self.call(hidden_line_removal=False, show_hidden_lines=False)
        new = self.call(projection_convention="right_handed", hidden_line_removal=False, show_hidden_lines=False)
        self.assertFalse(new["occlusion_checked"])
        for name in ("FRONT", "TOP", "SIDE"):
            with self.subTest(view=name):
                self.assertEqual(line_geometry(self.view(old, name)), line_geometry(self.view(new, name)))

    def test_invalid_conventions_fail_before_mesh_work_without_partial_output(self):
        for convention in (None, True, 1, [], {}, "", "RIGHT_HANDED", "first_angle", "third_angle", "unknown"):
            with self.subTest(convention=convention), patch.object(module, "validate_triangle_mesh") as validation, \
                    patch.object(module, "projected_edge_visibility") as visibility:
                result = self.call(projection_convention=convention)
                self.assert_no_partial(result)
                self.assertEqual(result["error_code"], "invalid_projection_convention")
                validation.assert_not_called()
                visibility.assert_not_called()

    def test_optin_work_and_interval_limits_fail_without_returning_completed_views(self):
        for options, error in (({"max_node_visits": 1}, "visibility_work_budget_exceeded"),
                               ({"max_triangle_tests": 1}, "visibility_work_budget_exceeded"),
                               ({"max_output_intervals": 1}, "visibility_output_interval_limit_exceeded")):
            with self.subTest(options=options):
                result = self.call(projection_convention="right_handed", **options)
                self.assert_no_partial(result)
                self.assertFalse(result["complete"])
                self.assertEqual(result["error_code"], error)
        front = self.view(self.call(projection_convention="right_handed", views=("FRONT",)), "FRONT")
        # FRONT fits; the total limit is exceeded only when a later view joins.
        with patch.object(module, "MAX_TOTAL_DRAWING_SEGMENTS", front["line_count"] + 1):
            result = self.call(projection_convention="right_handed")
        self.assert_no_partial(result)
        self.assertFalse(result["complete"])
        self.assertEqual(result["error_code"], "drawing_segment_limit_exceeded")

    def test_optin_does_not_mutate_input_or_promote_native_boolean_claims(self):
        vertices, triangles = fixture()
        original = deepcopy((vertices, triangles))
        result = module.generate_orthographic_drawing(vertices, triangles, projection_convention="right_handed")
        self.assertTrue(result["success"], result)
        self.assertEqual((vertices, triangles), original)
        for flag in ("native_tessellation", "cuts_applied", "hole_semantics_inferred"):
            self.assertFalse(result[flag])


if __name__ == "__main__":
    unittest.main()

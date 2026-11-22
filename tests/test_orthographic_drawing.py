from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.orthographic_drawing import (
    _validated_curve_primitives,
    generate_orthographic_drawing,
)


BOX_VERTICES = [
    [-1, -2, -3], [1, -2, -3], [1, 2, -3], [-1, 2, -3],
    [-1, -2, 3], [1, -2, 3], [1, 2, 3], [-1, 2, 3],
]
BOX_TRIANGLES = [
    [0, 2, 1], [0, 3, 2],       # bottom (-Z)
    [4, 5, 6], [4, 6, 7],       # top (+Z)
    [0, 1, 5], [0, 5, 4],       # front (-Y)
    [3, 7, 6], [3, 6, 2],       # back (+Y)
    [0, 4, 7], [0, 7, 3],       # left (-X)
    [1, 2, 6], [1, 6, 5],       # right (+X)
]


class OrthographicDrawingTests(unittest.TestCase):
    def call(self, vertices=BOX_VERTICES, triangles=BOX_TRIANGLES, **kwargs):
        return generate_orthographic_drawing(vertices, triangles, **kwargs)

    def test_box_produces_three_views_with_correct_dimensions_and_silhouettes(self):
        result = self.call(coordinate_frame="WORLD coordinates", coordinate_unit="mm")
        self.assertTrue(result["success"], result)
        self.assertTrue(result["complete"])
        self.assertFalse(result["native_tessellation"])
        self.assertTrue(result["hidden_line_removal"])
        self.assertTrue(result["occlusion_checked"])
        self.assertFalse(result["cuts_applied"])
        self.assertFalse(result["hole_semantics_inferred"])
        views = {view["name"]: view for view in result["views"]}
        self.assertEqual(set(views), {"FRONT", "TOP", "SIDE"})
        self.assertEqual(views["FRONT"]["bounds"], {"min": [-1.0, -3.0], "max": [1.0, 3.0]})
        self.assertEqual(views["TOP"]["bounds"], {"min": [-1.0, -2.0], "max": [1.0, 2.0]})
        self.assertEqual(views["SIDE"]["bounds"], {"min": [-2.0, -3.0], "max": [2.0, 3.0]})
        self.assertEqual(result["source_bounds"]["dimensions"], [2.0, 4.0, 6.0])
        # Face diagonals and depth-parallel edges collapse/suppress, leaving
        # the four outline segments in each orthographic projection.
        for view in views.values():
            self.assertEqual(view["line_count"], 4)
            self.assertTrue(all(segment["kind"] == "silhouette" for segment in view["lines"]))
            self.assertTrue(view["visibility"]["success"])
        self.assertEqual(len({view["display_scale_px_per_mm"] for view in views.values()}), 1)
        self.assertEqual(views["FRONT"]["dimensions"], {"width": 2.0, "height": 6.0, "unit": "mm"})

    def test_svg_is_deterministic_and_contains_only_generated_vector_linework(self):
        first = self.call(coordinate_frame="WORLD coordinates", coordinate_unit="mm", hidden_line_removal=False)
        second = self.call(coordinate_frame="WORLD coordinates", coordinate_unit="mm", hidden_line_removal=False)
        self.assertEqual(first["svg"], second["svg"])
        self.assertIn('<svg xmlns="http://www.w3.org/2000/svg"', first["svg"])
        self.assertIn('data-view="FRONT"', first["svg"])
        self.assertIn('data-view="TOP"', first["svg"])
        self.assertIn('data-view="SIDE"', first["svg"])
        self.assertIn("FRONT", first["svg"])
        self.assertIn("2.000000", first["svg"])
        self.assertIn("6.000000 mm", first["svg"])
        self.assertIn("hidden lines, cuts and holes are not resolved", first["svg"])

    def test_single_view_uses_full_svg_width(self):
        result = self.call(views=("FRONT",))
        self.assertTrue(result["success"], result)
        path_line = next(line for line in result["svg"].splitlines() if '<path class="silhouette"' in line)
        first_x = float(path_line.split('M ', 1)[1].split()[0])
        self.assertGreater(first_x, 400)

    def test_visibility_budget_exhaustion_fails_closed_without_drawing(self):
        result = self.call(max_node_visits=1)
        self.assertFalse(result["success"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["error_code"], "visibility_work_budget_exceeded")
        self.assertEqual(result["views"], [])
        self.assertNotIn("svg", result)

    def test_hidden_lines_are_explicit_dashed_and_preserve_default_visible_linework(self):
        outer = [[x * 2, y * 2, z * 2] for x, y, z in BOX_VERTICES]
        inner = [[x * .5, y * .5, z * .5] for x, y, z in BOX_VERTICES]
        triangles = BOX_TRIANGLES + [[index + 8 for index in face] for face in BOX_TRIANGLES]
        default = self.call(outer + inner, triangles, views=("FRONT",))
        shown = self.call(outer + inner, triangles, views=("FRONT",), show_hidden_lines=True)
        self.assertTrue(shown["success"], shown)
        self.assertFalse(default["show_hidden_lines"])
        self.assertTrue(shown["show_hidden_lines"])
        lines = shown["views"][0]["lines"]
        visible = [line for line in lines if line["visibility"] == "visible"]
        hidden = [line for line in lines if line["visibility"] == "hidden"]
        self.assertEqual(visible, default["views"][0]["lines"])
        self.assertEqual(len(hidden), 4)
        self.assertEqual(shown["views"][0]["visibility"]["hidden_edge_count"], 4)
        self.assertEqual(shown["views"][0]["visibility"]["visible_edge_count"], 4)
        self.assertIn('class="hidden" stroke="#718096" stroke-dasharray="6 4"', shown["svg"])
        self.assertEqual(shown["segment_count"], len(visible) + len(hidden))

    def test_hidden_output_stays_bounded_and_no_partial_drawing_is_returned(self):
        with patch("aveva_e3d_mcp.orthographic_drawing.MAX_TOTAL_DRAWING_SEGMENTS", 3):
            result = self.call(show_hidden_lines=True)
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "drawing_segment_limit_exceeded")
        self.assertEqual(result["views"], [])

    def test_rotated_box_has_no_depth_tolerance_endpoint_slivers(self):
        cosine, sine = .5, math.sqrt(3) / 2
        rotated = [[x * cosine - y * sine, x * sine + y * cosine, z] for x, y, z in BOX_VERTICES]
        result = self.call(rotated, views=("FRONT",), show_hidden_lines=True)
        self.assertTrue(result["success"], result)
        lines = result["views"][0]["lines"]
        self.assertTrue(any(line["visibility"] == "hidden" for line in lines))
        for line in lines:
            self.assertEqual(line["parameter_interval"], [0.0, 1.0])

    def test_requested_single_view_and_feature_threshold_are_reported(self):
        result = self.call(views=("FRONT",), feature_angle_degrees=45)
        self.assertTrue(result["success"], result)
        self.assertEqual([view["name"] for view in result["views"]], ["FRONT"])
        self.assertEqual(result["feature_angle_degrees"], 45)

    def test_open_inward_or_invalid_meshes_fail_closed(self):
        open_mesh = self.call(triangles=BOX_TRIANGLES[:-1])
        self.assertFalse(open_mesh["success"])
        self.assertEqual(open_mesh["error_code"], "closed_valid_outward_mesh_required")
        self.assertEqual(open_mesh["views"], [])

        tetra_vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]]
        tetra_faces = [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]
        inward = [[a, c, b] for a, b, c in tetra_faces]
        inward_mesh = self.call(tetra_vertices, inward)
        self.assertFalse(inward_mesh["success"])
        self.assertEqual(inward_mesh["error_code"], "closed_valid_outward_mesh_required")

        bad_index = self.call(triangles=[[0, 1, 999]])
        self.assertFalse(bad_index["success"])
        self.assertEqual(bad_index["error_code"], "closed_valid_outward_mesh_required")

    def test_invalid_views_threshold_and_metadata_are_rejected(self):
        for kwargs, error in (
            ({"views": ()}, "unsupported_orthographic_views"),
            ({"views": ("FRONT", "FRONT")}, "duplicate_orthographic_view"),
            ({"views": ("ISOMETRIC",)}, "unsupported_orthographic_views"),
            ({"feature_angle_degrees": math.inf}, "invalid_feature_angle"),
            ({"feature_angle_degrees": -1}, "invalid_feature_angle"),
            ({"coordinate_unit": ""}, "coordinate_metadata_required"),
            ({"hidden_line_removal": "false"}, "invalid_hidden_line_removal"),
            ({"show_hidden_lines": "true"}, "invalid_show_hidden_lines"),
            ({"show_hidden_lines": True, "hidden_line_removal": False}, "hidden_line_display_requires_removal"),
        ):
            with self.subTest(kwargs=kwargs):
                result = self.call(**kwargs)
                self.assertFalse(result["success"])
                self.assertEqual(result["error_code"], error)

    def test_ellipse_major_axis_source_accepts_reversed_direction_only(self):
        major_radius, minor_radius = 4.0, 2.0
        count = 32
        points = [(major_radius * math.cos(2.0 * math.pi * index / count),
                   minor_radius * math.sin(2.0 * math.pi * index / count))
                  for index in range(count)]
        lines = [{"start": points[index], "end": points[(index + 1) % count],
                  "kind": "feature", "visibility": "visible"} for index in range(count)]
        analytic_source = {
            "shape": "CYLI", "refno": "=1/2", "center": (0.0, 0.0),
            "source_radius_mm": major_radius, "axial_station_mm": 0.0,
            "major_axis": (-major_radius, 0.0),
            "major_radius_mm": major_radius, "minor_radius_mm": minor_radius,
        }
        curve = {
            "type": "ellipse", "center": (0.0, 0.0),
            "major_axis": (major_radius, 0.0),
            "major_radius": major_radius, "minor_radius": minor_radius,
            "kind": "feature", "visibility": "visible",
            "analytic_source": analytic_source,
            "source_line_indices": list(range(count)),
        }

        validated = _validated_curve_primitives({"lines": lines, "curve_primitives": [curve]})
        self.assertEqual(len(validated), 1)
        self.assertEqual(validated[0]["major_axis"], (major_radius, 0.0))

        curve["major_axis"] = (0.0, major_radius)
        with self.assertRaisesRegex(ValueError, "invalid_curve_source_section"):
            _validated_curve_primitives({"lines": lines, "curve_primitives": [curve]})

    def test_dish_arc_axial_domain_includes_between_vertex_extrema(self):
        radius = 10.0
        angles = [math.radians(85.0 + 10.0 * index / 9.0) for index in range(10)]
        points = [(radius * math.cos(angle), radius * math.sin(angle)) for angle in angles]
        lines = [{"start": points[index], "end": points[index + 1],
                  "kind": "silhouette", "visibility": "visible"} for index in range(9)]
        curve = {
            "type": "arc", "center": (0.0, 0.0), "radius": radius,
            "start_angle_degrees": 85.0, "end_angle_degrees": 95.0,
            "kind": "silhouette", "visibility": "visible",
            "source_line_indices": list(range(len(lines))),
            "analytic_source": {
                "shape": "DISH", "refno": "=1/1", "source_kind": "spherical_silhouette_arc",
                "center": (0.0, 0.0), "source_radius_mm": radius,
                "sphere_center_station_mm": 0.0, "axial_domain_mm": [0.0, 9.9995],
                "axis_vertical_sign": 1.0,
            },
        }

        with self.assertRaisesRegex(ValueError, "invalid_curve_fit"):
            _validated_curve_primitives({"lines": lines, "curve_primitives": [curve]})

        curve["analytic_source"]["axial_domain_mm"][1] = radius
        self.assertEqual(len(_validated_curve_primitives(
            {"lines": lines, "curve_primitives": [curve]})), 1)

        # An equal shift preserves the span, perimeter, and axial domain, but
        # the rendered arc no longer meets the two verified source endpoints.
        for start_shift, end_shift in ((1.0, 1.0), (0.1, 0.0), (0.0, -0.1)):
            with self.subTest(start_shift=start_shift, end_shift=end_shift):
                shifted = dict(curve, start_angle_degrees=85.0 + start_shift,
                               end_angle_degrees=95.0 + end_shift)
                with self.assertRaisesRegex(ValueError, "invalid_curve_fit"):
                    _validated_curve_primitives({"lines": lines, "curve_primitives": [shifted]})


if __name__ == "__main__":
    unittest.main()

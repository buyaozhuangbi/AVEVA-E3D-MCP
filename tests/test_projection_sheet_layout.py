from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.projection_sheet_layout import plan_projection_sheet


BASES = {"FRONT": ([1, 0, 0], [0, 0, 1], [0, -1, 0]),
         "TOP": ([1, 0, 0], [0, 1, 0], [0, 0, 1]),
         "SIDE": ([0, 1, 0], [0, 0, 1], [1, 0, 0])}


def fixture(x=(20, 120), y=(-30, 170), z=(40, 340)):
    axes = {"FRONT": (x, z), "TOP": (x, y), "SIDE": (y, z)}
    raw = {"success": True, "available": True, "complete": True, "projection_convention": "right_handed",
           "coordinate_frame": "WORLD coordinates; millimetres", "coordinate_unit": "mm", "views": []}
    for name in ("FRONT", "TOP", "SIDE"):
        horizontal, vertical, toward = BASES[name]
        first, second = axes[name]
        minimum, maximum = [first[0], second[0]], [first[1], second[1]]
        raw["views"].append({"name": name, "horizontal_axis": horizontal[:], "vertical_axis": vertical[:],
                             "toward_viewer": toward[:], "bounds": {"min": minimum, "max": maximum},
                             "coordinate_frame": raw["coordinate_frame"], "coordinate_unit": "mm"})
    return raw


def prepared(raw):
    return [{"name": row["name"], "minimum": row["bounds"]["min"][:], "maximum": row["bounds"]["max"][:],
             "width": row["bounds"]["max"][0] - row["bounds"]["min"][0],
             "height": row["bounds"]["max"][1] - row["bounds"]["min"][1], "lines": [], "linear_dimensions": None}
            for row in raw["views"]]


class ProjectionSheetLayoutTests(unittest.TestCase):
    def plan(self, raw=None, method="third_angle", paper=(297, 210), coordinate_decimal_places=6, **options):
        raw = raw if raw is not None else fixture()
        return plan_projection_sheet(prepared(raw), raw, {"projection_method": method, **options}, *paper,
                                     coordinate_decimal_places=coordinate_decimal_places)

    def test_default_plan_bytes_are_unchanged_and_precision_is_call_local(self):
        raw = fixture()
        options = {"projection_method": "third_angle"}
        default = plan_projection_sheet(prepared(raw), raw, options, 297, 210)
        encoded = json.dumps(default, separators=(",", ":")).encode()
        self.assertEqual(hashlib.sha256(encoded).hexdigest(),
                         "ac1a5e24c60f6d0a03ae1df04a56e2df413e6ffa68233a085a72af6381558a7d")
        self.assertEqual(default, self.plan(coordinate_decimal_places=6))
        self.plan(coordinate_decimal_places=15)
        self.assertEqual(default, self.plan())

    def test_coordinate_precision_requires_a_strict_integer_from_six_to_fifteen(self):
        class IntegerSubclass(int):
            pass

        for value in (True, False, None, "15", 6.0, 15.0, 5, 16, [], {}, math.nan, math.inf, IntegerSubclass(15)):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "^invalid_coordinate_decimal_places$"):
                self.plan(coordinate_decimal_places=value)
        baseline = self.plan()
        for value in range(6, 16):
            with self.subTest(value=value):
                self.assertEqual(self.plan(coordinate_decimal_places=value), baseline)

    def test_selected_precision_preserves_overall_100_mm_extent_at_billion_scale(self):
        raw = fixture(x=(0, 80), y=(0, 80), z=(0, 100))
        for method in ("first_angle", "third_angle"):
            for precision in (9, 12, 15):
                with self.subTest(method=method, precision=precision):
                    plan = self.plan(raw, method=method, scale_denominator=1e9, coordinate_decimal_places=precision)
                    front = plan["views"]["FRONT"]
                    low = front["paper_origin_mm"][1]
                    high = low + 100 / plan["scale_denominator"]
                    self.assertNotEqual(format(low, f".{precision}f"), format(high, f".{precision}f"))
                    self.assertAlmostEqual(high - low, 1e-7, delta=3e-14)
            with self.subTest(method=method), self.assertRaisesRegex(ValueError, "^projection_sheet_geometry_below_paper_precision$"):
                self.plan(raw, method=method, scale_denominator=1e9)

    def test_maximum_precision_still_rejects_actual_float_collapse(self):
        raw = fixture(x=(0, 80), y=(0, 80), z=(0, 100))
        for method in ("first_angle", "third_angle"):
            with self.subTest(method=method), self.assertRaisesRegex(ValueError, "^projection_sheet_geometry_below_paper_precision$"):
                self.plan(raw, method=method, scale_denominator=1e20, coordinate_decimal_places=15)

    def _material_box_drawing(self, dimensions):
        from aveva_e3d_mcp.material_assembly_drawing import combine_material_drawing_meshes, generate_material_assembly_drawing
        from test_material_assembly_drawing import parameter_source

        source = parameter_source(dimensions=dimensions)
        combined = combine_material_drawing_meshes([source])
        self.assertTrue(combined["success"], combined.get("error_code"))
        drawing = generate_material_assembly_drawing(combined, projection_convention="right_handed")
        self.assertTrue(drawing["success"], drawing.get("error_code"))
        return drawing

    def test_complete_parameter_material_builder_preserves_100_mm_vertical_dimension(self):
        from aveva_e3d_mcp.drawing_sheet import build_drawing_sheet
        from aveva_e3d_mcp.material_assembly_sheet import build_material_assembly_sheet

        drawing = self._material_box_drawing({"XLEN": 80, "YLEN": 80, "ZLEN": 100})
        original = copy.deepcopy(drawing)
        for method in ("first_angle", "third_angle"):
            options = {"projection_method": method, "scale_denominator": 1e9}
            with self.subTest(method=method), self.assertRaisesRegex(ValueError, "^projection_sheet_geometry_below_paper_precision$"):
                build_drawing_sheet(drawing, options)
            scene = build_drawing_sheet(drawing, options, coordinate_decimal_places=15)
            material = build_material_assembly_sheet(drawing, options)
            self.assertTrue(scene["complete"])
            self.assertTrue(material["complete"])
            self.assertEqual(material["serialization_precision"]["coordinate_decimal_places"], 15)
            self.assertEqual(scene["dimension_label_resolution_mm"], 1e-6)
            height = scene["paper"]["height_mm"]
            encoded = lambda point: tuple(float(format(value, ".15f")) for value in point)
            for stroke in scene["strokes"]:
                start, end = stroke["start"], stroke["end"]
                self.assertNotEqual(encoded(start), encoded(end))
                self.assertNotEqual(encoded((start[0], height - start[1])), encoded((end[0], height - end[1])))
            geometry = [stroke for stroke in material["strokes"] if "source_line_index" in stroke]
            self.assertEqual(len(geometry), drawing["segment_count"])
            front = next(view for view in scene["views"] if view["name"] == "FRONT")
            bounds = front["paper_bounds_mm"]
            self.assertAlmostEqual(bounds["max"][1] - bounds["min"][1], 1e-7, delta=3e-14)
            labels = [text for text in scene["texts"] if text["role"] == "dimension" and text["view_name"] == "FRONT"]
            vertical = next(text for text in labels if text["dimension_axis"] == "height")
            self.assertEqual(vertical["text"], "100 MM")
            self.assertEqual(vertical["dimension_value_mm"], 100)
            self.assertEqual(vertical["rotation"], 90)
        self.assertEqual(drawing, original)

    def test_high_precision_preserves_real_dimension_label_cell_gate(self):
        from aveva_e3d_mcp.drawing_sheet import build_drawing_sheet
        from aveva_e3d_mcp.material_assembly_sheet import build_material_assembly_sheet

        drawing = self._material_box_drawing({"XLEN": 100, "YLEN": 100, "ZLEN": 100})
        for method in ("first_angle", "third_angle"):
            options = {"projection_method": method, "scale_denominator": 1e9}
            plan = plan_projection_sheet(prepared(drawing), drawing, options, 420, 297, coordinate_decimal_places=15)
            front = plan["views"]["FRONT"]
            label_half_width = (6 * len("100 MM") - 1) / 7 * 2.2 / 2
            label_right = front["paper_origin_mm"][0] + 100 / 1e9 / 2 + label_half_width
            self.assertGreater(label_right, front["cell_bounds_mm"]["max"][0] - 1)
            with self.subTest(method=method), self.assertRaisesRegex(ValueError, "^projection_sheet_label_outside_cell$"):
                build_drawing_sheet(drawing, options, coordinate_decimal_places=15)
            with self.subTest(method=method), self.assertRaisesRegex(ValueError, "^projection_sheet_label_outside_cell$"):
                build_material_assembly_sheet(drawing, options)

    def test_third_angle_places_top_above_front_and_right_side_to_right(self):
        plan = self.plan()
        front, top, side = (plan["views"][name] for name in ("FRONT", "TOP", "SIDE"))
        self.assertEqual(plan["scale_denominator"], 10)
        self.assertEqual(front["cell_bounds_mm"]["min"][0], top["cell_bounds_mm"]["min"][0])
        self.assertEqual(front["cell_bounds_mm"]["min"][1], side["cell_bounds_mm"]["min"][1])
        self.assertAlmostEqual(top["cell_bounds_mm"]["min"][1] - front["cell_bounds_mm"]["max"][1], 8)
        self.assertAlmostEqual(side["cell_bounds_mm"]["min"][0] - front["cell_bounds_mm"]["max"][0], 8)
        self.assertEqual(plan["side_view"], "right")

    def test_first_angle_places_top_below_front_and_right_side_to_left(self):
        plan = self.plan(method="first_angle")
        front, top, side = (plan["views"][name] for name in ("FRONT", "TOP", "SIDE"))
        self.assertEqual(front["cell_bounds_mm"]["min"][0], top["cell_bounds_mm"]["min"][0])
        self.assertEqual(front["cell_bounds_mm"]["min"][1], side["cell_bounds_mm"]["min"][1])
        self.assertAlmostEqual(front["cell_bounds_mm"]["min"][1] - top["cell_bounds_mm"]["max"][1], 8)
        self.assertAlmostEqual(front["cell_bounds_mm"]["min"][0] - side["cell_bounds_mm"]["max"][0], 8)

    def test_shared_axis_coordinates_are_exactly_aligned_for_nonzero_model_datums(self):
        for method in ("first_angle", "third_angle"):
            plan = self.plan(method=method)
            views, scale = plan["views"], plan["scale_denominator"]
            def project(name, point):
                view = views[name]
                return [view["paper_origin_mm"][axis] + (point[axis] - view["canonical_model_bounds"]["min"][axis]) / scale
                        for axis in (0, 1)]
            for x in (20, 66.25, 120):
                self.assertEqual(project("FRONT", [x, 50])[0], project("TOP", [x, 25])[0])
            for z in (40, 123.75, 340):
                self.assertEqual(project("FRONT", [65, z])[1], project("SIDE", [25, z])[1])

    def test_cells_have_fixed_paper_margins_and_group_is_centered_in_body(self):
        plan = self.plan()
        minimum = [min(row["cell_bounds_mm"]["min"][axis] for row in plan["views"].values()) for axis in (0, 1)]
        maximum = [max(row["cell_bounds_mm"]["max"][axis] for row in plan["views"].values()) for axis in (0, 1)]
        self.assertAlmostEqual((minimum[0] + maximum[0]) / 2, 297 / 2)
        self.assertAlmostEqual((minimum[1] + maximum[1]) / 2, (50 + 200) / 2)
        for row in plan["views"].values():
            low, high, origin = row["cell_bounds_mm"]["min"], row["cell_bounds_mm"]["max"], row["paper_origin_mm"]
            self.assertEqual([origin[i] - low[i] for i in (0, 1)], [16, 16])
            for axis, margin in ((0, 6), (1, 12)):
                model = row["canonical_model_bounds"]
                far = origin[axis] + (model["max"][axis] - model["min"][axis]) / plan["scale_denominator"]
                self.assertAlmostEqual(high[axis] - far, margin)
            self.assertAlmostEqual(high[1] - row["heading_position_mm"][1], 5)

    def test_scale_is_common_auto_one_two_five_or_explicit_without_partial_fit(self):
        self.assertEqual(self.plan(paper=(420, 297))["scale_denominator"], 5)
        self.assertEqual(self.plan(fixture(x=(0, 1), y=(0, 1), z=(0, 1)))["scale_denominator"], 1)
        self.assertEqual(self.plan(scale_denominator=7)["scale_denominator"], 7)
        for denominator in (0, -1, True, math.inf, math.nan, 2):
            with self.subTest(denominator=denominator), self.assertRaises(ValueError):
                self.plan(scale_denominator=denominator)

    def test_source_and_prepared_order_do_not_change_named_layout_or_inputs(self):
        raw = fixture()
        views = prepared(raw)
        options = {"projection_method": "first_angle", "title": "EXAMPLE"}
        before = copy.deepcopy((views, raw, options))
        first = plan_projection_sheet(views, raw, options, 297, 210)
        raw["views"].reverse()
        second = plan_projection_sheet(list(reversed(views)), raw, options, 297, 210)
        self.assertEqual(first, second)
        raw["views"].reverse()
        self.assertEqual((views, raw, options), before)

    def test_near_equal_shared_bounds_have_explicit_union_and_same_canonical_datum(self):
        raw = fixture()
        raw["views"][1]["bounds"]["min"][0] += 5e-8
        raw["views"][1]["bounds"]["max"][0] += 5e-8
        plan = self.plan(raw)
        x = plan["canonical_axis_bounds_model_mm"]["X"]
        self.assertTrue(x["canonicalized"])
        self.assertEqual(x["min"], 20)
        self.assertEqual(x["max"], 120 + 5e-8)
        self.assertGreaterEqual(x["tolerance_mm"], 5e-8)
        self.assertEqual(plan["views"]["FRONT"]["canonical_model_bounds"]["min"][0],
                         plan["views"]["TOP"]["canonical_model_bounds"]["min"][0])
        self.assertEqual(plan["views"]["FRONT"]["paper_origin_mm"][0], plan["views"]["TOP"]["paper_origin_mm"][0])

    def test_mismatched_shared_x_y_or_z_bounds_are_rejected(self):
        for view_index, axis in ((1, 0), (2, 0), (2, 1)):
            raw = fixture()
            raw["views"][view_index]["bounds"]["max"][axis] += 1
            with self.subTest(view=view_index, axis=axis), self.assertRaisesRegex(ValueError, "shared_axis_bounds_mismatch"):
                self.plan(raw)

    def test_legacy_front_direction_and_unproved_coordinate_frame_are_rejected(self):
        for mutation, error in ((lambda d: d.pop("projection_convention"), "right_handed_drawing_required"),
                                (lambda d: d["views"][0].__setitem__("toward_viewer", [0, 1, 0]), "view_basis_mismatch"),
                                (lambda d: d["views"][0].__setitem__("horizontal_axis", [True, 0, 0]), "view_basis_mismatch"),
                                (lambda d: d["views"][1].__setitem__("coordinate_frame", "other local frame"), "shared_model_frame_required"),
                                (lambda d: d["views"][1].__setitem__("coordinate_unit", "cm"), "shared_model_frame_required"),
                                (lambda d: d.pop("coordinate_frame"), "shared_model_frame_required")):
            raw = fixture()
            mutation(raw)
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                self.plan(raw)

    def test_exact_three_unique_projection_views_are_required(self):
        for names in (("FRONT", "TOP"), ("FRONT", "TOP", "TOP"), ("FRONT", "TOP", "SECTION")):
            raw = fixture()
            raw["views"] = raw["views"][:len(names)]
            for row, name in zip(raw["views"], names):
                row["name"] = name
            with self.subTest(names=names), self.assertRaisesRegex(ValueError, "three_views_required"):
                self.plan(raw)

    def test_degenerate_unrepresentable_and_untrusted_prepared_geometry_fails(self):
        for raw in (fixture(x=(0, 0)), fixture(z=(1, 1))):
            with self.assertRaisesRegex(ValueError, "degenerate_view"):
                self.plan(raw)
        with self.assertRaisesRegex(ValueError, "below_paper_precision"):
            self.plan(scale_denominator=1e20)
        with self.assertRaisesRegex(ValueError, "content_area_unavailable"):
            self.plan(paper=(70, 124))
        raw = fixture()
        views = prepared(raw)
        views[0]["width"] += 1
        with self.assertRaisesRegex(ValueError, "prepared_bounds_mismatch"):
            plan_projection_sheet(views, raw, {"projection_method": "third_angle"}, 297, 210)
        views = prepared(raw)
        views[0]["linear_dimensions"] = []
        with self.assertRaisesRegex(ValueError, "section_dimensions_unsupported"):
            plan_projection_sheet(views, raw, {"projection_method": "third_angle"}, 297, 210)

    def test_large_negative_and_translated_model_bounds_remain_finite(self):
        raw = fixture(x=(-1e9, -1e9 + 100), y=(1e9 - 200, 1e9), z=(-400, -100))
        plan = self.plan(raw)
        self.assertEqual(plan["scale_denominator"], 10)
        for view in plan["views"].values():
            self.assertTrue(all(math.isfinite(value) for value in view["paper_origin_mm"]))


if __name__ == "__main__":
    unittest.main()

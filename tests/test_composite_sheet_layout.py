"""Four-cell projection/section layout and independent plane-frame contracts."""
from __future__ import annotations

from copy import deepcopy
import itertools
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.composite_sheet_layout import plan_composite_sheet
from aveva_e3d_mcp.mesh_section import normalize_section_plane
from tests.test_projection_sheet_exports import physical_drawing


def fixture(section_size=(600, 400), section_minimum=(-500, 125)):
    drawing = physical_drawing()
    plane = normalize_section_plane({"origin": [123, -456, 789], "normal": [1, 2, 3], "up": [0, 0, 1]})
    drawing["section_plane"] = plane
    drawing["views"].append({
        "name": "SECTION", "coordinate_unit": "mm", "coordinate_frame": "section plane coordinates; millimetres",
        "origin": plane["origin"][:], "horizontal_axis": plane["right"][:],
        "vertical_axis": plane["up"][:], "toward_viewer": plane["normal"][:],
        "bounds": {"min": list(section_minimum),
                   "max": [a + b for a, b in zip(section_minimum, section_size)]},
        "dimensions": {"width": section_size[0], "height": section_size[1], "unit": "mm"},
        "lines": [],
    })
    return drawing


def prepared(drawing):
    return [{"name": view["name"], "minimum": list(view["bounds"]["min"]),
             "maximum": list(view["bounds"]["max"]),
             "width": view["bounds"]["max"][0] - view["bounds"]["min"][0],
             "height": view["bounds"]["max"][1] - view["bounds"]["min"][1], "lines": [],
             "linear_dimensions": view.get("linear_dimensions")}
            for view in drawing["views"]]


def project(plan, name, point):
    view = plan["views"][name]
    return [view["paper_origin_mm"][axis] + (point[axis] - view["canonical_model_bounds"]["min"][axis]) /
            plan["scale_denominator"] for axis in (0, 1)]


class CompositeSheetLayoutTests(unittest.TestCase):
    def plan(self, drawing=None, method="third_angle", paper=(420, 297), margins=None, **options):
        drawing = fixture() if drawing is None else drawing
        return plan_composite_sheet(prepared(drawing), drawing, {"projection_method": method, **options},
                                    *paper, section_margins=margins)

    def test_both_arrangements_fill_four_cells_without_changing_physical_directions(self):
        for method in ("first_angle", "third_angle"):
            plan = self.plan(method=method)
            cells = {name: view["cell_bounds_mm"] for name, view in plan["views"].items()}
            front, top, side, section = [cells[name] for name in ("FRONT", "TOP", "SIDE", "SECTION")]
            self.assertEqual(front["min"][0], top["min"][0])
            self.assertEqual(side["min"][0], section["min"][0])
            self.assertEqual(front["min"][1], side["min"][1])
            self.assertEqual(top["min"][1], section["min"][1])
            if method == "third_angle":
                self.assertGreater(top["min"][1], front["max"][1])
                self.assertGreater(side["min"][0], front["max"][0])
            else:
                self.assertLess(top["max"][1], front["min"][1])
                self.assertLess(side["max"][0], front["min"][0])
            self.assertEqual(plan["side_view"], "right")
            self.assertEqual(plan["section_plane"]["origin"], [123, -456, 789])

    def test_shared_world_datums_are_exact_while_section_center_remains_independent(self):
        for method in ("first_angle", "third_angle"):
            plan = self.plan(method=method)
            for x in (100, 345.5, 900):
                self.assertEqual(project(plan, "FRONT", [x, 50])[0], project(plan, "TOP", [x, -200])[0])
            for z in (50, 239.75, 550):
                self.assertEqual(project(plan, "FRONT", [100, z])[1], project(plan, "SIDE", [-200, z])[1])
            section = plan["views"]["SECTION"]
            self.assertEqual(section["canonical_model_bounds"], {"min": [-500, 125], "max": [100, 525]})
            margins = section["cell_margins_mm"]
            self.assertAlmostEqual(margins["left"] - 16, margins["right"] - 6)
            self.assertAlmostEqual(margins["bottom"] - 16, margins["top"] - 12)

    def test_section_translation_does_not_affect_scale_world_anchors_or_paper_position(self):
        original = self.plan()
        moved = self.plan(fixture(section_minimum=(999_998_000, -999_998_000)))
        self.assertEqual(original["scale_denominator"], moved["scale_denominator"])
        for name in ("FRONT", "TOP", "SIDE"):
            self.assertEqual(original["views"][name], moved["views"][name])
        self.assertEqual(original["views"]["SECTION"]["paper_origin_mm"], moved["views"]["SECTION"]["paper_origin_mm"])

    def test_shared_axes_keep_canonical_union_with_slightly_different_raw_bounds(self):
        drawing = fixture()
        top = drawing["views"][1]
        top["bounds"]["min"][0] -= 1e-8
        top["bounds"]["max"][0] += 1e-8
        plan = self.plan(drawing)
        self.assertTrue(plan["canonical_axis_bounds_model_mm"]["X"]["canonicalized"])
        self.assertEqual(project(plan, "FRONT", [555, 50])[0], project(plan, "TOP", [555, -200])[0])

    def test_scale_accounts_for_section_geometry_in_both_columns_and_rows(self):
        base = self.plan()
        large = self.plan(fixture(section_size=(4000, 3500)))
        self.assertGreater(large["scale_denominator"], base["scale_denominator"])
        self.assertEqual(base["scale_denominator"], 10)
        self.assertEqual(large["scale_denominator"], 50)
        explicit = self.plan(scale_denominator=11)
        self.assertEqual(explicit["scale_denominator"], 11)
        with self.assertRaisesRegex(ValueError, "scale_does_not_fit"):
            self.plan(fixture(section_size=(4000, 3500)), scale_denominator=10)

    def test_all_standard_papers_keep_group_centered_and_cells_separated(self):
        for method, paper in itertools.product(("first_angle", "third_angle"), ((420, 297), (297, 420), (297, 210), (210, 297))):
            with self.subTest(method=method, paper=paper):
                plan = self.plan(method=method, paper=paper)
                cells = [view["cell_bounds_mm"] for view in plan["views"].values()]
                low = [min(cell["min"][axis] for cell in cells) for axis in (0, 1)]
                high = [max(cell["max"][axis] for cell in cells) for axis in (0, 1)]
                self.assertAlmostEqual((low[0] + high[0]) / 2, paper[0] / 2)
                self.assertAlmostEqual((low[1] + high[1]) / 2, (50 + paper[1] - 10) / 2)
                self.assertGreaterEqual(low[0], 10)
                self.assertGreaterEqual(low[1], 50)
                self.assertLessEqual(high[0], paper[0] - 10)
                self.assertLessEqual(high[1], paper[1] - 10)
                for first, second in itertools.combinations(cells, 2):
                    self.assertTrue(any(first["max"][axis] + 8 <= second["min"][axis] + 1e-9 or
                                        second["max"][axis] + 8 <= first["min"][axis] + 1e-9 for axis in (0, 1)))

    def test_custom_section_lanes_expand_shared_column_and_row_margins(self):
        margins = {"left": 49, "right": 25, "bottom": 61, "top": 37}
        for method in ("first_angle", "third_angle"):
            plan = self.plan(method=method, margins=margins)
            for side, expected in margins.items():
                self.assertGreaterEqual(plan["views"]["SECTION"]["cell_margins_mm"][side] + 1e-9, expected)
            self.assertGreaterEqual(plan["views"]["SIDE"]["cell_margins_mm"]["left"], 49)
            self.assertGreaterEqual(plan["views"]["TOP"]["cell_margins_mm"]["bottom"], 61)
            self.assertEqual(project(plan, "FRONT", [100, 50])[0], project(plan, "TOP", [100, -200])[0])
            self.assertEqual(project(plan, "FRONT", [100, 50])[1], project(plan, "SIDE", [-200, 50])[1])

    def test_dimension_lanes_must_be_explicit_and_paper_margins_cannot_disappear(self):
        drawing = fixture()
        drawing["views"][-1]["linear_dimensions"] = [{"axis": "horizontal", "side": "top"}]
        with self.assertRaisesRegex(ValueError, "dimension_margins_required"):
            self.plan(drawing)
        self.plan(drawing, margins={"left": 16, "right": 6, "bottom": 16, "top": 25})
        for margins in ({}, {"left": 16, "right": 6, "bottom": 16, "top": 12, "extra": 1},
                        {"left": 15, "right": 6, "bottom": 16, "top": 12},
                        {"left": True, "right": 6, "bottom": 16, "top": 12},
                        {"left": math.inf, "right": 6, "bottom": 16, "top": 12}):
            with self.subTest(margins=margins), self.assertRaises(ValueError):
                self.plan(margins=margins)
        with self.assertRaisesRegex(ValueError, "content_area_unavailable"):
            self.plan(margins={"left": 1000, "right": 6, "bottom": 16, "top": 12})

    def test_raw_and_prepared_orders_are_irrelevant_and_inputs_are_not_mutated_or_aliased(self):
        drawing = fixture()
        views = prepared(drawing)
        options = {"projection_method": "first_angle"}
        margins = {"left": 16, "right": 6, "bottom": 16, "top": 12}
        before = deepcopy((drawing, views, options, margins))
        plan = plan_composite_sheet(views, drawing, options, 420, 297, margins)
        self.assertEqual((drawing, views, options, margins), before)
        drawing["views"].reverse()
        self.assertEqual(plan, plan_composite_sheet(list(reversed(views)), drawing, options, 420, 297, margins))
        drawing["views"].reverse()
        plan["section_plane"]["origin"][0] = 999
        plan["views"]["SECTION"]["canonical_model_bounds"]["min"][0] = 999
        plan["section_margins_mm"]["left"] = 999
        self.assertEqual((drawing, views, options, margins), before)

    def test_section_mm_origin_and_exact_declared_axes_are_required(self):
        for mutate, error in (
            (lambda d: d.pop("section_plane"), "section_plane_required"),
            (lambda d: d["views"][-1].update(coordinate_unit="cm"), "section_frame_required"),
            (lambda d: d["views"][-1].update(coordinate_frame=d["coordinate_frame"]), "section_frame_required"),
            (lambda d: d["views"][-1].update(origin=[0, 0, 0]), "origin_mismatch"),
            (lambda d: d["views"][-1].update(horizontal_axis=[1, 0, 0]), "axes_mismatch"),
            (lambda d: d["views"][-1].update(vertical_axis=[False, 0, 1]), "invalid_number"),
            (lambda d: d.update(coordinate_frame="unverified model frame"), "world_millimetres_required"),
        ):
            drawing = fixture()
            mutate(drawing)
            before = deepcopy(drawing)
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                self.plan(drawing)
            self.assertEqual(drawing, before)

    def test_plane_frame_is_orthonormal_right_handed_even_if_view_axes_match_it(self):
        for field, value in (("right", [2, 0, 0]), ("up", [1, 0, 0]), ("normal", [0, 0, -1])):
            drawing = fixture()
            drawing["section_plane"] = {"origin": [0, 0, 0], "right": [1, 0, 0], "up": [0, 1, 0], "normal": [0, 0, 1]}
            drawing["section_plane"][field] = value
            plane, view = drawing["section_plane"], drawing["views"][-1]
            view.update(origin=plane["origin"], horizontal_axis=plane["right"], vertical_axis=plane["up"], toward_viewer=plane["normal"])
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "frame_required"):
                self.plan(drawing)

    def test_four_unique_views_and_existing_three_view_physical_checks_are_required(self):
        for mutate, error in (
            (lambda d: d["views"].pop(), "four_views_required"),
            (lambda d: d["views"][-1].update(name="TOP"), "four_views_required"),
            (lambda d: d["views"][0].update(toward_viewer=[0, 1, 0]), "view_basis_mismatch"),
            (lambda d: d["views"][1]["bounds"]["min"].__setitem__(0, 101), "shared_axis_bounds_mismatch"),
            (lambda d: d["views"][2].update(coordinate_frame="other frame"), "shared_model_frame_required"),
        ):
            drawing = fixture()
            mutate(drawing)
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                self.plan(drawing)

    def test_degenerate_section_untrusted_preparation_and_paper_precision_fail_closed(self):
        for size in ((0, 30), (30, 0), (-1, 30)):
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, "degenerate_section_view"):
                self.plan(fixture(section_size=size))
        drawing = fixture()
        views = prepared(drawing)
        views[-1]["width"] += 1
        with self.assertRaisesRegex(ValueError, "prepared_section_bounds_mismatch"):
            plan_composite_sheet(views, drawing, {"projection_method": "third_angle"}, 420, 297)
        for denominator in (0, -1, True, math.inf, math.nan):
            with self.subTest(denominator=denominator), self.assertRaises(ValueError):
                self.plan(scale_denominator=denominator)
        with self.assertRaisesRegex(ValueError, "precision"):
            self.plan(scale_denominator=1e15)
        with self.assertRaises(ValueError):
            self.plan(paper=(40, 60))


if __name__ == "__main__":
    unittest.main()

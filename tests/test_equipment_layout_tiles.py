"""Fixed-scale coverage, true POS ownership and bounded equipment-page checks."""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aveva_e3d_mcp.drawing_sheet_render import (
    SHEET_TEXT_CHARACTERS, rasterize_drawing_sheet_png, render_drawing_sheet_svg,
    validate_drawing_sheet_scene,
)
from aveva_e3d_mcp.equipment_layout_drawing import build_equipment_layout_drawing
from aveva_e3d_mcp.equipment_layout_tiles import (
    build_equipment_layout_tiles, normalize_equipment_layout_tile_sheet_options,
    normalize_equipment_layout_tile_targets, normalize_equipment_layout_tile_tiling_options,
)
from aveva_e3d_mcp.equipment_outline_sheet import normalize_equipment_outline_targets
from aveva_e3d_mcp.orthographic_dxf import export_drawing_sheet_dxf
from test_equipment_layout_sheet import native_pose, text_box
from test_equipment_outline_sheet import material_drawing
from test_material_assembly_drawing import CSG_ENGINE_AVAILABLE, csg_source, parameter_source
from test_primitive_csg_mesh import cut


MODULE = "aveva_e3d_mcp.equipment_layout_tiles"
FOOTER = "SELECTED PARAMETER GEOMETRY / NOT WHOLE EQUIPMENT"
NATIVE_SCOPE = "PARAMETER ASSEMBLY - NATIVE CUT SCOPE UNVERIFIED"
SHEET = {"size": "A3", "scale_denominator": 1}


def poses(positions):
    return build_equipment_layout_drawing([native_pose(index, position=position) for index, position in enumerate(positions)])


def geometry(scene):
    return [stroke for stroke in scene["strokes"] if stroke.get("annotation_kind") == "equipment_material_outline"]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def independent_clip(line, bounds):
    """Split at rectangle crossings, then admit intervals by their midpoint."""
    start = list(map(Fraction, line["start"]))
    delta = [Fraction(last) - first for first, last in zip(start, line["end"])]
    low, high = [list(map(Fraction, bounds[key])) for key in ("min", "max")]
    crossings = {Fraction(0), Fraction(1)}
    for axis in (0, 1):
        if delta[axis]:
            for boundary in (low[axis], high[axis]):
                value = (boundary - start[axis]) / delta[axis]
                if 0 <= value <= 1:
                    crossings.add(value)
    ordered = sorted(crossings)
    intervals = []
    for first, last in zip(ordered, ordered[1:]):
        midpoint = (first + last) / 2
        if all(low[axis] <= start[axis] + delta[axis] * midpoint <= high[axis] for axis in (0, 1)):
            intervals.append((first, last))
    if not intervals:
        return None
    first, last = intervals[0][0], intervals[-1][1]
    return ([float(first), float(last)],
            [[float(start[axis] + delta[axis] * value) for axis in (0, 1)] for value in (first, last)])


class EquipmentLayoutTilesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = material_drawing([
            parameter_source("=1/1001", position=(0, 0, 0)),
            parameter_source("=1/1002", position=(25, 0, 80)),
        ])
        cls.templates = {index: next(line for line in cls.original["views"][0]["lines"] if line["source_index"] == index)
                         for index in (0, 1)}
        cls.pose_drawing = poses([(0, 0, 5), (25, 0, 85)])

    def drawing(self, definitions, *, minimum=(0, 0), maximum=(800, 300)):
        result = deepcopy(self.original)
        view = result["views"][0]
        view["bounds"] = {"min": list(map(float, minimum)), "max": list(map(float, maximum))}
        view["dimensions"] = {"width": maximum[0] - minimum[0], "height": maximum[1] - minimum[1], "unit": "mm"}
        view["lines"] = []
        for definition in definitions:
            start, end, *extras = definition
            values = extras[0] if extras else {}
            index = values.get("source_index", 0)
            view["lines"].append({**deepcopy(self.templates[index]), "start": list(map(float, start)),
                                  "end": list(map(float, end)), "kind": "silhouette", "visibility": "visible", **values})
        result["segment_count"] = len(view["lines"])
        return result

    def result(self, drawing=None, pose_drawing=None, groups=None, sheet=None, tiling=None, **kwargs):
        drawing = self.original if drawing is None else drawing
        pose_drawing = self.pose_drawing if pose_drawing is None else pose_drawing
        groups = [[0], [1]] if groups is None else groups
        sheet = SHEET if sheet is None else sheet
        before = deepcopy((drawing, pose_drawing, groups, sheet, tiling, kwargs))
        result = build_equipment_layout_tiles(drawing, pose_drawing, groups, sheet, tiling, **kwargs)
        self.assertEqual((drawing, pose_drawing, groups, sheet, tiling, kwargs), before)
        return result

    def assert_readable(self, scene):
        validate_drawing_sheet_scene(scene)
        boxes = [text_box(item) for item in scene["texts"]]
        for index, first in enumerate(boxes):
            for second in boxes[index + 1:]:
                self.assertFalse(min(first[2], second[2]) > max(first[0], second[0]) and
                                 min(first[3], second[3]) > max(first[1], second[1]))
        for item in scene["texts"]:
            self.assertTrue(set(item["text"]) <= SHEET_TEXT_CHARACTERS)
            if item.get("annotation_kind") in {"equipment_callout", "equipment_coordinate_row"}:
                self.assertGreaterEqual(item["height"], 2.25)
        self.assertIn(FOOTER, [item["text"] for item in scene["texts"]])
        self.assertIn(NATIVE_SCOPE, [item["text"] for item in scene["texts"]])
        for flag in ("whole_equipment_geometry_verified", "explicit_source_membership_verified", "equipment_extents_verified",
                     "extents_included", "native_draft_sheet", "engineering_dimensions_certified", "plant_north_verified"):
            self.assertIs(scene[flag], False)
        self.assertEqual(scene["serialization_precision"]["coordinate_decimal_places"], 15)
        self.assertFalse([stroke for stroke in scene["strokes"] if stroke["role"] == "dimension"])
        self.assertTrue(json.dumps(scene, allow_nan=False))

    def assert_exact_coverage(self, drawing, result):
        originals = drawing["views"][0]["lines"]
        manifest = result["manifest"]
        for index, original in enumerate(originals):
            intervals = []
            for page in manifest["pages"]:
                actual = [line for line in page["lines"] if line["global_line_index"] == index]
                expected = independent_clip(original, page["bounds"])
                self.assertEqual(len(actual), 0 if expected is None else 1)
                if expected is not None:
                    self.assertEqual(actual[0]["source_interval"], expected[0])
                    self.assertEqual([actual[0]["start"], actual[0]["end"]], expected[1])
                    intervals.append(tuple(map(Fraction, actual[0]["source_interval"])))
            cursor = Fraction(0)
            for first, last in sorted(intervals):
                self.assertLessEqual(first, cursor)
                cursor = max(cursor, last)
            self.assertEqual(cursor, 1)
        coverage = manifest["coverage"]
        for flag in ("complete", "exact_rational_grid_verified", "emitted_float_grid_verified",
                     "exact_source_interval_union_verified", "returned_source_interval_union_verified",
                     "equipment_origin_coverage_verified"):
            self.assertIs(coverage[flag], True)
        self.assertEqual(coverage["gap_tolerance"], 0)
        self.assertEqual(coverage["covered_line_count"], len(originals))
        self.assertFalse(coverage["visibility_recomputed"])

    def test_target_normalizer_accepts_64_equipment_128_sources_and_keeps_old_limit(self):
        maximum = [{"refno": f"/P{index}", "sources": [{"refno": f"=1/{1000 + index * 2 + sub}",
                    "expected_type": "BOX", "geometry_mode": "parameter_mesh"} for sub in (0, 1)]} for index in range(64)]
        result = normalize_equipment_layout_tile_targets(maximum)
        self.assertEqual(result, maximum)
        self.assertEqual(normalize_equipment_layout_tile_targets(result), result)
        with self.assertRaisesRegex(ValueError, "one_to_16"):
            normalize_equipment_outline_targets(maximum[:17])
        invalid = [None, [], True, [None], maximum + [maximum[0]],
                   [{**maximum[0], "extra": True}], [{"refno": "/P", "sources": []}],
                   [maximum[0], {**maximum[1], "refno": "/p0"}],
                   [{**maximum[0], "refno": "=1/10"}, {**maximum[1], "refno": "=001/010"}],
                   [maximum[0], {**maximum[1], "sources": maximum[0]["sources"]}],
                   [{"refno": "/P", "sources": [{"refno": f"=1/{index}", "expected_type": "BOX",
                       "geometry_mode": "parameter_mesh"} for index in range(129)]}]]
        for value in invalid:
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                normalize_equipment_layout_tile_targets(value)

    def test_required_fixed_scale_and_sheet_options_are_strict_and_idempotent(self):
        normalized = normalize_equipment_layout_tile_sheet_options({"scale_denominator": 5})
        self.assertEqual(normalized, {"size": "A3", "title": "EQUIPMENT LAYOUT TILES",
                                     "drawing_number": "E3D-EQUIPMENT-TILES", "scale_denominator": 5})
        self.assertEqual(normalize_equipment_layout_tile_sheet_options(normalized), normalized)
        invalid = [None, {}, [], {"scale_denominator": 5, "orientation": "portrait"},
                   {"scale_denominator": 5, "equipment_per_page": 16}, {"scale_denominator": 5, "size": "A2"},
                   {"scale_denominator": 5, "title": "中文"}, {"scale_denominator": 5, "title": "T" * 49},
                   *[{"scale_denominator": value} for value in (False, 0, -1, math.inf, math.nan, 10**500, 1e-12, 1e100)]]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_equipment_layout_tile_sheet_options(value)

    def test_all_A3_A4_pages_use_fixed_reserved_viewport_and_exact_scale(self):
        drawing = self.drawing([((0, 10), (800, 270)), ((400, 0), (400, 300), {"source_index": 1})])
        for size, viewport, scale in (("A3", (361, 186), 1), ("A4", (238, 101.4), 2.5)):
            with self.subTest(size=size):
                result = self.result(drawing=drawing, sheet={"size": size, "scale_denominator": scale}, tiling={"overlap_mm": 0})
                manifest = result["manifest"]
                self.assertEqual(tuple(manifest["viewport_mm"].values()), viewport)
                self.assertEqual(manifest["tile_span_model_mm"], [value * scale for value in viewport])
                for page, scene in zip(manifest["pages"], result["sheets"]):
                    transform = scene["views"][0]["transform"]
                    self.assertEqual(scene["scale_denominator"], scale)
                    self.assertEqual(scene["table_capacity"], 2)
                    for stroke, fragment in zip(geometry(scene), page["lines"]):
                        for endpoint in ("start", "end"):
                            expected = [float(Fraction(transform["paper_origin_mm"][axis]) +
                                (Fraction(fragment[endpoint][axis]) - Fraction(transform["model_min"][axis])) / Fraction(scale)) for axis in (0, 1)]
                            self.assertEqual(stroke[endpoint], expected)
                    self.assert_readable(scene)
                self.assert_exact_coverage(drawing, result)

    def test_overlap_preflight_uses_actual_table_reserved_viewport(self):
        for size, threshold in (("A3", 93), ("A4", 50.7)):
            with self.subTest(size=size):
                for overlap in (threshold, threshold + 1):
                    with self.assertRaisesRegex(ValueError, "overlap_exceeds_viewport_limit"):
                        self.result(sheet={"size": size, "scale_denominator": 10}, tiling={"overlap_mm": overlap})
                accepted = self.result(sheet={"size": size, "scale_denominator": 10},
                                       tiling={"overlap_mm": math.nextafter(threshold, 0)})
                self.assertEqual(accepted["manifest"]["page_count"], 1)

    def test_tiling_preflight_uses_the_total_count_and_requires_no_geometry_or_pose(self):
        for size, count, threshold in (("A3", 1, 95.5), ("A3", 2, 93), ("A3", 16, 58), ("A3", 64, 58),
                                       ("A4", 1, 52.6), ("A4", 2, 50.7), ("A4", 16, 24.1), ("A4", 64, 24.1)):
            with self.subTest(size=size, count=count), \
                    patch(MODULE + "._material_sources", side_effect=AssertionError("material inspected")), \
                    patch(MODULE + "._prepare_rows", side_effect=AssertionError("pose inspected")):
                sheet = {"size": size, "scale_denominator": 10}
                self.assertEqual(normalize_equipment_layout_tile_tiling_options(sheet, None, count),
                                 {"overlap_mm": 10, "max_pages": 24})
                normalized = normalize_equipment_layout_tile_tiling_options(sheet,
                    {"overlap_mm": math.nextafter(threshold, 0), "max_pages": 8}, count)
                self.assertEqual(normalize_equipment_layout_tile_tiling_options(sheet, normalized, count), normalized)
                with self.assertRaisesRegex(ValueError, "overlap_exceeds_viewport_limit"):
                    normalize_equipment_layout_tile_tiling_options(sheet, {"overlap_mm": threshold}, count)
        for count in (None, False, True, 0, -1, 65, 1.0, "1"):
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, "one_to_64"):
                normalize_equipment_layout_tile_tiling_options(SHEET, None, count)

    def test_single_equipment_reserves_one_table_row_and_gains_plot_area_on_every_page(self):
        drawing = self.drawing([((0, 0), (700, 360))], maximum=(700, 360))
        for size, viewport, page_count, plot_bottom in (("A3", (361, 191), 4, 64),
                                                       ("A4", (238, 105.2), 12, 62.8)):
            with self.subTest(size=size):
                result = self.result(drawing=drawing, pose_drawing=poses([(20, 20, 0)]), groups=[[0, 1]],
                                     sheet={"size": size, "scale_denominator": 1}, tiling={"overlap_mm": 0})
                manifest = result["manifest"]
                self.assertEqual(manifest["table_capacity"], 1)
                self.assertEqual(tuple(manifest["viewport_mm"].values()), viewport)
                self.assertEqual(manifest["page_count"], page_count)
                self.assertEqual(manifest["plot_bounds_mm"]["min"], [14, plot_bottom])
                for scene in result["sheets"]:
                    self.assertEqual(scene["table_capacity"], 1)
                    self.assertLessEqual(scene["equipment_count"], 1)
                    self.assertEqual(scene["views"][0]["plot_bounds_mm"], manifest["plot_bounds_mm"])
                    table_lines = [stroke for stroke in scene["strokes"] if stroke["role"] == "title_block"
                                   and stroke["start"][1] < plot_bottom]
                    self.assertEqual(len(table_lines), 3)
                    self.assert_readable(scene)
                self.assert_exact_coverage(drawing, result)

    def test_crossing_lines_match_independent_rectangle_oracle_and_zero_gap_union(self):
        drawing = self.drawing([
            ((10, 50), (790, 50), {"visibility": "hidden", "parameter_interval": [.2, .7]}),
            ((120, 10), (120, 290), {"kind": "feature", "source_index": 1}),
            ((0, 0), (800, 300)), ((800, 0), (0, 300)),
        ])
        for overlap in (0, 10):
            with self.subTest(overlap=overlap):
                result = self.result(drawing=drawing, tiling={"overlap_mm": overlap})
                self.assert_exact_coverage(drawing, result)
                for page, scene in zip(result["manifest"]["pages"], result["sheets"]):
                    for fragment, stroke in zip(page["lines"], geometry(scene)):
                        index = fragment["global_line_index"]
                        original = drawing["views"][0]["lines"][index]
                        for key in ("source_index", "source_refno", "source_geometry_mode", "edge", "visibility"):
                            self.assertEqual(fragment[key], original[key])
                            self.assertEqual(stroke[key], original[key])
                        self.assertEqual(stroke["role"], original["kind"])
                        self.assertEqual(stroke["global_line_index"], index)
                        self.assertEqual(stroke["source_line_index"], index)
                        self.assertEqual(stroke["source_interval"], fragment["source_interval"])
                        self.assertEqual(stroke.get("parameter_interval"), original.get("parameter_interval"))
                        self.assertEqual(stroke["equipment_id"], f"E{original['source_index'] + 1:03d}")

    def test_closed_shared_edges_and_POS_corner_are_repeated_on_all_neighbors(self):
        drawing = self.drawing([((0, 186), (722, 186)), ((361, 0), (361, 372), {"source_index": 1})], maximum=(722, 372))
        result = self.result(drawing=drawing, pose_drawing=poses([(361, 186, 100), (361, 186, 200)]), tiling={"overlap_mm": 0})
        pages = result["manifest"]["pages"]
        self.assertEqual([page["id"] for page in pages], ["TOP-R01-C01", "TOP-R01-C02", "TOP-R02-C01", "TOP-R02-C02"])
        self.assertEqual(pages[0]["bounds"], {"min": [0, 186], "max": [361, 372]})
        self.assertEqual(pages[0]["neighbors"], {"top": None, "bottom": pages[2]["id"], "left": None, "right": pages[1]["id"]})
        self.assertTrue(all(page["origin_equipment_indices"] == [0, 1] for page in pages))
        self.assertTrue(all(len(scene["callouts"]) == 2 for scene in result["sheets"]))
        for index in (0, 1):
            intervals = sorted(line["source_interval"] for page in pages for line in page["lines"] if line["global_line_index"] == index)
            self.assertEqual(intervals, [[0, .5], [0, .5], [.5, 1], [.5, 1]])
            self.assertEqual(result["manifest"]["equipment_source_map"][f"E{index + 1:03d}"]["origin_page_ids"], [page["id"] for page in pages])
        self.assert_exact_coverage(drawing, result)

    def test_point_contacts_do_not_create_strokes_and_blank_grid_pages_are_retained(self):
        drawing = self.drawing([((0, 0), (361, 186))], maximum=(722, 372))
        result = self.result(drawing=drawing, pose_drawing=poses([(20, 20, 0), (30, 20, 0)]), tiling={"overlap_mm": 0})
        self.assertEqual(result["manifest"]["page_count"], 4)
        self.assertEqual(result["manifest"]["coverage"]["point_touch_count"], 3)
        self.assertEqual(sum(page["blank"] for page in result["manifest"]["pages"]), 3)
        for scene in result["sheets"]:
            if scene["blank"]:
                self.assertEqual(scene["source_rows"], [])
                self.assertEqual(scene["callouts"], [])
                self.assertEqual(geometry(scene), [])
                self.assertIn("NO PROJECTED EDGES OR EQUIPMENT ORIGINS", [text["text"] for text in scene["texts"]])
            self.assert_readable(scene)
        self.assert_exact_coverage(drawing, result)

    def test_overlap_repeats_real_strips_and_POS_inside_overlap(self):
        drawing = self.drawing([((0, 50), (700, 50))], maximum=(700, 100))
        result = self.result(drawing=drawing, pose_drawing=poses([(355, 50, 0), (355, 50, 0)]))
        manifest = result["manifest"]
        self.assertEqual(manifest["tile_step_model_mm"], [351, 176])
        fragments = [line for page in manifest["pages"] for line in page["lines"]]
        self.assertEqual(fragments[0]["end"], [355, 50])
        self.assertEqual(fragments[1]["start"], [345, 50])
        self.assertGreater(fragments[0]["source_interval"][1], fragments[1]["source_interval"][0])
        self.assertTrue(all(page["origin_equipment_indices"] == [0, 1] for page in manifest["pages"]))
        self.assert_exact_coverage(drawing, result)

    def test_grid_balances_unused_edges_without_changing_scale_or_losing_source_intervals(self):
        drawing = self.drawing([((0, 0), (800, 300)), ((0, 0), (10, 10), {"source_index": 1})])
        result = self.result(drawing=drawing, pose_drawing=poses([(0, 0, 0), (10, 10, 0)]))
        manifest = result["manifest"]
        self.assertEqual((manifest["columns"], manifest["rows"]), (3, 2))
        self.assertEqual(manifest["tile_span_model_mm"], [361, 186])
        self.assertEqual(manifest["tile_step_model_mm"], [351, 176])
        self.assertEqual(manifest["grid_origin"], [-131.5, -31])
        padding = manifest["grid_placement"]["outer_padding_model_mm"]
        self.assertEqual(padding, {"left": 131.5, "right": 131.5, "bottom": 31, "top": 31})
        self.assertIs(manifest["grid_placement"]["model_geometry_translated"], False)
        self.assertIs(manifest["grid_placement"]["scale_preserved"], True)
        self.assert_exact_coverage(drawing, result)

    def test_grid_preserves_a_fitting_central_source_instead_of_cutting_it_at_center(self):
        drawing = self.drawing([
            ((-225, -225), (225, -225)), ((225, -225), (225, 225)),
            ((225, 225), (-225, 225)), ((-225, 225), (-225, -225)),
            ((-815, -700), (2000, 700), {"source_index": 1}),
        ], minimum=(-815, -700), maximum=(2000, 700))
        result = self.result(drawing=drawing, pose_drawing=poses([(0, 0, 0)]), groups=[[0, 1]], sheet={"scale_denominator": 5})
        manifest = result["manifest"]
        self.assertEqual((manifest["columns"], manifest["rows"], manifest["page_count"]), (2, 2, 4))
        self.assertEqual(manifest["scale_denominator"], 5)
        self.assertEqual(manifest["grid_origin"], [-1187.5, -720])
        containing = [page for page in manifest["pages"] if
            all(page["bounds"]["min"][axis] <= -225 and 225 <= page["bounds"]["max"][axis] for axis in (0, 1))]
        self.assertEqual(len(containing), 1)
        self.assertEqual([line["source_interval"] for line in containing[0]["lines"] if line["source_index"] == 0], [[0, 1]] * 4)
        self.assertEqual(manifest["grid_placement"]["axis_decisions"][1]["split_projected_source_count"], 0)
        self.assertEqual(manifest["grid_placement"]["axis_decisions"][1]["centered_origin"], -930)
        for scene in result["sheets"]:
            self.assert_readable(scene)
        self.assert_exact_coverage(drawing, result)

    def test_model_and_paper_mm_scale_is_uniform_in_xy_and_has_a_true_size_calibration_bar(self):
        drawing = self.drawing([
            ((1000, 500), (1250, 500)),
            ((1000, 500), (1000, 750), {"source_index": 1}),
        ], minimum=(1000, 500), maximum=(1250, 750))
        result = self.result(drawing=drawing, pose_drawing=poses([(1000, 500, 100)]),
                             groups=[[0, 1]], sheet={"scale_denominator": 5})
        self.assertEqual(result["manifest"]["page_count"], 1)
        scene = result["sheets"][0]
        for stroke in geometry(scene):
            self.assertEqual(math.dist(stroke["start"], stroke["end"]), 50)
        bar = scene["paper_scale_bar"]
        self.assertEqual(math.dist(bar["start_mm"], bar["end_mm"]), 50)
        self.assertEqual(bar["model_length_mm"], 250)
        self.assertIs(bar["model_feature_dimension"], False)
        baselines = [stroke for stroke in scene["strokes"] if stroke.get("bar_part") == "baseline"]
        self.assertEqual(len(baselines), 1)
        self.assertEqual(baselines[0]["start"], bar["start_mm"])
        self.assertEqual(baselines[0]["end"], bar["end_mm"])
        svg = ET.fromstring(render_drawing_sheet_svg(scene, coordinate_decimal_places=15))
        paths = svg.findall("{http://www.w3.org/2000/svg}path")
        for index, stroke in enumerate(scene["strokes"]):
            if stroke in geometry(scene) or stroke.get("bar_part") == "baseline":
                tokens = paths[index].get("d").split()
                start, end = [float(tokens[1]), float(tokens[2])], [float(tokens[4]), float(tokens[5])]
                self.assertEqual(math.dist(start, end), 50)
        self.assert_readable(scene)

    def test_far_origins_expand_planning_bounds_without_changing_geometry_or_faking_anchors(self):
        drawing = self.drawing([((0, 10), (700, 10))], maximum=(700, 50))
        positions = [(10, 400, 123.456), (20, 400, 234.567)]
        datum = {"label": "GRID A", "origin_mm": [-100, 350, 120]}
        result = self.result(drawing=drawing, pose_drawing=poses(positions), datum=datum, tiling={"overlap_mm": 0})
        manifest = result["manifest"]
        self.assertEqual(manifest["geometry_bounds"], drawing["views"][0]["bounds"])
        self.assertEqual(manifest["planning_bounds"], {"min": [0, 0], "max": [700, 400]})
        self.assertEqual(manifest["global_bounds"], manifest["planning_bounds"])
        self.assertEqual(manifest["page_count"], 6)
        geometry_only = [scene for scene in result["sheets"] if scene["counts"]["geometry_strokes"] and not scene["callouts"]]
        origin_only = [scene for scene in result["sheets"] if scene["callouts"] and scene["blank_geometry"]]
        self.assertEqual(len(geometry_only), 2)
        self.assertEqual(len(origin_only), 1)
        for scene in geometry_only:
            self.assertEqual(scene["equipment_ids"], ["E001"])
            self.assertFalse([stroke for stroke in scene["strokes"] if stroke.get("annotation_kind") in {"equipment_origin_symbol", "equipment_leader"}])
            row = scene["source_rows"][0]
            self.assertEqual((row["id"], row["location_marker"], row["page_relation"]), ("E001", "G", "geometry_only"))
            self.assertFalse(row["origin_in_tile"])
            self.assertTrue(row["geometry_in_tile"])
            self.assertTrue(row["table_row_text"].endswith("  G"))
        for scene in origin_only:
            self.assertEqual(scene["equipment_ids"], ["E001", "E002"])
            self.assertTrue(all(row["location_marker"] == "P" and row["page_relation"] == "origin_only" for row in scene["source_rows"]))
            transform = scene["views"][0]["transform"]
            for callout in scene["callouts"]:
                position = positions[callout["equipment_index"]]
                expected = [float(Fraction(transform["paper_origin_mm"][axis]) +
                    (Fraction(position[axis]) - Fraction(transform["model_min"][axis])) / Fraction(scene["scale_denominator"])) for axis in (0, 1)]
                self.assertEqual(callout["anchor_mm"], expected)
            row = scene["source_rows"][0]
            self.assertEqual(row["table_values"]["dx_mm"], "110.000")
            self.assertEqual(row["table_values"]["dy_mm"], "50.000")
            self.assertEqual(row["table_values"]["del_mm"], "3.456")
        self.assertTrue(any(scene["blank"] for scene in result["sheets"]))
        self.assert_exact_coverage(drawing, result)

    def test_global_occlusion_visibility_and_original_mesh_edge_intervals_survive(self):
        originals = self.original["views"][0]["lines"]
        self.assertTrue(any(line["visibility"] == "hidden" for line in originals))
        result = self.result(sheet={"scale_denominator": .5})
        for scene in result["sheets"]:
            for stroke in geometry(scene):
                original = originals[stroke["global_line_index"]]
                for key in ("edge", "source_index", "visibility", "parameter_interval"):
                    self.assertEqual(stroke.get(key), original.get(key))
        self.assert_exact_coverage(self.original, result)

    def test_projection_mesh_combination_and_visibility_are_not_repeated(self):
        with patch("aveva_e3d_mcp.material_assembly_drawing.generate_material_assembly_drawing", side_effect=AssertionError("projection repeated")), \
                patch("aveva_e3d_mcp.material_assembly_drawing.combine_material_drawing_meshes", side_effect=AssertionError("combination repeated")):
            result = self.result()
        self.assert_exact_coverage(self.original, result)

    def test_64_equipment_distribute_across_pages_with_stable_IDs_and_origin_coverage(self):
        positions = [(10 + index % 8 * 30, 10 + index // 8 * 150, index) for index in range(64)]
        drawing = material_drawing([parameter_source(f"=1/{2000 + index}", position=position,
            dimensions={"XLEN": 20, "YLEN": 20, "ZLEN": 10}) for index, position in enumerate(positions)])
        result = self.result(drawing=drawing, pose_drawing=poses(positions), groups=[[index] for index in range(64)])
        manifest = result["manifest"]
        self.assertEqual(manifest["equipment_count"], 64)
        self.assertEqual(manifest["source_count"], 64)
        self.assertEqual(manifest["table_capacity"], 16)
        self.assertLessEqual(manifest["page_count"], 24)
        self.assertTrue(all(page["equipment_count"] <= 16 for page in manifest["pages"]))
        self.assertEqual(set(manifest["equipment_source_map"]), {f"E{index + 1:03d}" for index in range(64)})
        for index in range(64):
            mapping = manifest["equipment_source_map"][f"E{index + 1:03d}"]
            self.assertTrue(mapping["origin_page_ids"])
            self.assertEqual(mapping["source_indices"], [index])
        self.assertTrue(any(row["id"] == "E064" for scene in result["sheets"] for row in scene["source_rows"]))
        for scene in result["sheets"]:
            self.assert_readable(scene)
        self.assert_exact_coverage(drawing, result)

    def test_sixteen_coincident_origins_and_table_rows_fit_both_papers(self):
        drawing = material_drawing([parameter_source(f"=1/{3000 + index}", position=(index * 10, 0, 0)) for index in range(16)])
        pose_drawing = poses([(0, 0, 0)] * 16)
        for size in ("A3", "A4"):
            with self.subTest(size=size):
                result = self.result(drawing=drawing, pose_drawing=pose_drawing, groups=[[index] for index in range(16)],
                                     sheet={"size": size, "scale_denominator": 10})
                self.assertEqual(result["manifest"]["page_count"], 1)
                scene = result["sheets"][0]
                self.assertEqual(scene["equipment_count"], 16)
                self.assertEqual(len(scene["callouts"]), 16)
                self.assertEqual(scene["source_rows"][-1]["id"], "E016")
                self.assertEqual(len({tuple(callout["position_mm"]) for callout in scene["callouts"]}), 16)
                self.assertEqual(len({tuple(callout["anchor_mm"]) for callout in scene["callouts"]}), 1)
                self.assert_readable(scene)

    def test_seventeen_relevant_equipment_fail_whole_result_before_sheets(self):
        drawing = material_drawing([parameter_source(f"=1/{4000 + index}", position=(index * 5, 0, 0)) for index in range(17)])
        with patch(MODULE + "._sheet", side_effect=AssertionError("partial sheet escaped")):
            with self.assertRaisesRegex(ValueError, "page_equipment_table_limit_exceeded"):
                self.result(drawing=drawing, pose_drawing=poses([(0, 0, 0)] * 17), groups=[[index] for index in range(17)],
                            sheet={"scale_denominator": 100})

    def test_source_assignment_must_be_total_unique_nonempty_integer_partition(self):
        for groups in (None, [], [[0, 1]], [[0], []], [[0], [0, 1]], [[0], [True]], [[0], [2]], [[0], [-1]], [[0], [1.0]]):
            with self.subTest(groups=groups), self.assertRaises(ValueError):
                build_equipment_layout_tiles(self.original, self.pose_drawing, groups, SHEET)

    def test_incomplete_nonworld_bad_orientation_duplicate_or_non_EQUI_pose_is_refused(self):
        faults = (lambda value: value.update(complete=False), lambda value: value.update(coordinate_frame="OWNER"),
                  lambda value: value.update(coordinate_unit="m"), lambda value: value.update(extents_included=True),
                  lambda value: value["equipment"][0].update(orientation_available=False),
                  lambda value: value["equipment"][0].update(position=None),
                  lambda value: value["equipment"][0].update(position=[math.nan, 0, 0]),
                  lambda value: value["equipment"][0].update(resolved_type="BOX"),
                  lambda value: value["equipment"][0]["orientation"].update(x_axis=[2, 0, 0]),
                  lambda value: value["equipment"][0]["orientation"].update(z_axis=[0, 0, -1]),
                  lambda value: value["equipment"][1].update(resolved_refno=value["equipment"][0]["resolved_refno"]))
        for index, fault in enumerate(faults):
            pose_drawing = deepcopy(self.pose_drawing)
            fault(pose_drawing)
            with self.subTest(fault=index), self.assertRaises(ValueError):
                self.result(pose_drawing=pose_drawing)

    def test_invalid_material_projection_source_edge_bounds_and_flags_are_refused(self):
        faults = (lambda value: value.update(complete=False), lambda value: value.update(projection_convention="legacy"),
                  lambda value: value.update(native_cut_application_verified=True), lambda value: value.pop("geometry_sha256"),
                  lambda value: value["views"][0].update(name="FRONT"),
                  lambda value: value["views"][0].update(vertical_axis=[0, -1, 0]),
                  lambda value: value["views"].append(deepcopy(value["views"][0])),
                  lambda value: value["views"][0]["lines"][0].update(source_index=True),
                  lambda value: value["views"][0]["lines"][0].update(source_refno="=9/9"),
                  lambda value: value["views"][0]["lines"][0].update(source_geometry_mode="native"),
                  lambda value: value["views"][0]["lines"][0].update(edge=[0, 9999]),
                  lambda value: value["views"][0]["lines"][0].update(start=[math.inf, 0]),
                  lambda value: value["views"][0]["lines"][0].update(start=[-1e9, 0]),
                  lambda value: value["views"][0]["lines"][0].update(end=value["views"][0]["lines"][0]["start"]),
                  lambda value: value.update(segment_count=True), lambda value: value.update(show_hidden_lines=False))
        for index, fault in enumerate(faults):
            drawing = deepcopy(self.original)
            fault(drawing)
            with self.subTest(fault=index), self.assertRaises(ValueError):
                self.result(drawing=drawing)

    def test_empty_projected_linework_and_out_of_range_model_bounds_are_refused(self):
        with self.assertRaisesRegex(ValueError, "nonempty_TOP_linework_required"):
            self.result(drawing=self.drawing([]))
        drawing = self.drawing([((0, 0), (1, 1))], maximum=(1e9 + 1, 100))
        with self.assertRaisesRegex(ValueError, "model_coordinate_limit_exceeded"):
            self.result(drawing=drawing)

    def test_page_line_work_and_fragment_budgets_are_atomic(self):
        drawing = self.drawing([((0, 10), (800, 10))])
        for target, value, pattern in (("MAX_MATERIAL_TILE_LINE_TESTS", 1, "line_work_limit_exceeded"),
                                       ("MAX_MATERIAL_TILE_CLIP_SEGMENTS", 1, "clip_segment_limit_exceeded"),
                                       ("MAX_MATERIAL_TILE_SOURCE_LINES", 0, "source_line_limit_exceeded")):
            with self.subTest(target=target), patch(MODULE + "." + target, value), \
                    patch(MODULE + "._sheet", side_effect=AssertionError("partial sheet escaped")):
                with self.assertRaisesRegex(ValueError, pattern):
                    self.result(drawing=drawing)
        for tiling in ({"max_pages": 1}, {"max_pages": 25}, {"max_pages": True}, {"rows": 1}, {"overlap_mm": -1}, {"overlap_mm": math.nan}):
            with self.subTest(tiling=tiling), self.assertRaises(ValueError):
                self.result(drawing=drawing, tiling=tiling)

    def test_exact_interval_proof_rejects_any_positive_missing_fragment(self):
        from aveva_e3d_mcp.equipment_layout_tiles import _clip_interval
        drawing = self.drawing([((0, 10), (722, 10))], maximum=(722, 100))
        def shrink(start, delta, minimum, maximum):
            interval = _clip_interval(start, delta, minimum, maximum)
            if interval and minimum[0] > 0:
                first, last = interval
                return first + Fraction(1, 10**18), last
            return interval
        with patch(MODULE + "._clip_interval", side_effect=shrink), \
                patch(MODULE + "._sheet", side_effect=AssertionError("partial sheet escaped")):
            with self.assertRaisesRegex(ValueError, "source_coverage_gap"):
                self.result(drawing=drawing, tiling={"overlap_mm": 0})

    def test_nonzero_line_below_paper_precision_is_rejected_without_silent_drop(self):
        drawing = self.drawing([((0, 0), (100, 100)), ((0, 0), (math.nextafter(0, 1), 0))], maximum=(100, 100))
        with self.assertRaisesRegex(ValueError, "geometry_below_precision"):
            self.result(drawing=drawing, sheet={"scale_denominator": 10000})

    def test_hashes_bind_global_classification_positions_partition_sheet_and_pages(self):
        result = self.result()
        manifest = result["manifest"]
        self.assertNotIn("explicit_source_membership_verified", manifest)
        self.assertIn("caller in the containing result", manifest["source_membership_proof_scope"])
        self.assertIs(result["explicit_source_membership_verified"], False)
        view = self.original["views"][0]
        axes = {key: view[key] for key in ("horizontal_axis", "vertical_axis", "toward_viewer")}
        self.assertEqual(manifest["source_proof_sha256"], digest(manifest["source_proof"]))
        self.assertEqual(manifest["global_linework_sha256"], digest({"schema": "material-global-linework-v1",
            "geometry_sha256": self.original["geometry_sha256"], "projection_convention": "right_handed", "view_name": "TOP",
            **axes, "global_bounds": view["bounds"], "lines": view["lines"]}))
        self.assertEqual(manifest["manifest_sha256"], digest({"schema": "equipment-layout-tile-manifest-v1", "manifest": {
            key: value for key, value in manifest.items() if key not in {"source_proof", "manifest_sha256"}}}))
        same = self.result(sheet=normalize_equipment_layout_tile_sheet_options(SHEET), tiling={"max_pages": 24, "overlap_mm": 10})
        self.assertEqual(result, same)
        changed = self.result(sheet={**SHEET, "title": "OTHER TITLE"})["manifest"]
        self.assertNotEqual(manifest["manifest_sha256"], changed["manifest_sha256"])
        self.assertEqual(manifest["global_linework_sha256"], changed["global_linework_sha256"])
        changed_pose = deepcopy(self.pose_drawing)
        changed_pose["equipment"][0]["position"][2] += 1
        changed = self.result(pose_drawing=changed_pose)["manifest"]
        self.assertNotEqual(manifest["position_proof_sha256"], changed["position_proof_sha256"])
        self.assertNotEqual(manifest["manifest_sha256"], changed["manifest_sha256"])
        self.assertEqual(manifest["global_linework_sha256"], changed["global_linework_sha256"])
        changed = self.result(groups=[[1], [0]])["manifest"]
        self.assertNotEqual(manifest["position_proof_sha256"], changed["position_proof_sha256"])
        self.assertNotEqual(manifest["manifest_sha256"], changed["manifest_sha256"])

    def test_shared_proof_is_concentrated_in_manifest_and_outputs_do_not_alias_inputs(self):
        drawing, pose_drawing, groups = deepcopy(self.original), deepcopy(self.pose_drawing), [[0], [1]]
        result = self.result(drawing=drawing, pose_drawing=pose_drawing, groups=groups)
        manifest = result["manifest"]
        for page, scene in zip(manifest["pages"], result["sheets"]):
            for key in ("source_ranges", "source_proof", "equipment_source_map", "equipment_source_indices"):
                self.assertNotIn(key, page)
                self.assertNotIn(key, scene)
            for key in ("source_proof_sha256", "geometry_sha256", "global_linework_sha256", "manifest_sha256"):
                self.assertEqual(scene[key], manifest[key])
        result["sheets"][0]["source_rows"][0]["position"][0] = 999
        manifest["source_proof"]["source_ranges"][0]["vertex_range"][0] = 999
        manifest["equipment_source_indices"][0][0] = 999
        self.assertEqual(drawing, self.original)
        self.assertEqual(pose_drawing, self.pose_drawing)
        self.assertEqual(groups, [[0], [1]])

    def test_png_svg_dxf_keep_all_annotations_strokes_and_15_decimal_coordinates(self):
        for size in ("A3", "A4"):
            with self.subTest(size=size):
                result = self.result(sheet={"size": size, "scale_denominator": 2})
                for scene in result["sheets"]:
                    self.assert_readable(scene)
                    svg = ET.fromstring(render_drawing_sheet_svg(scene, coordinate_decimal_places=15))
                    paths = svg.findall("{http://www.w3.org/2000/svg}path")
                    labels = svg.findall("{http://www.w3.org/2000/svg}text")
                    self.assertEqual(len(paths), len(scene["strokes"]))
                    self.assertEqual([item.text for item in labels], [item["text"] for item in scene["texts"]])
                    dxf = export_drawing_sheet_dxf(scene, coordinate_decimal_places=15)
                    self.assertIn(FOOTER, dxf)
                    self.assertIn(NATIVE_SCOPE, dxf)
                    self.assertNotIn("_DIMENSION", dxf)
                    self.assertRegex(dxf, r"\d+\.\d{15}")
                    png = rasterize_drawing_sheet_png(scene, width=1000, height=707)
                    self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
                    self.assertEqual(struct.unpack(">II", png[16:24]), (1000, 707))


@unittest.skipUnless(CSG_ENGINE_AVAILABLE, "Mesh64 integration runtime is required.")
class EquipmentLayoutTilesMaterialTests(unittest.TestCase):
    def test_cavity_and_empty_source_keep_shared_proof_and_equipment_partition(self):
        empty = csg_source("=1/5002", cutters=[cut("BOX", {"XLEN": 200, "YLEN": 200, "ZLEN": 100})])
        drawing = material_drawing([csg_source("=1/5001"), empty, parameter_source("=1/5003", position=(200, 0, 0))])
        result = build_equipment_layout_tiles(drawing, poses([(0, 0, 0), (200, 0, 0)]), [[0, 1], [2]], SHEET)
        manifest = result["manifest"]
        self.assertTrue(manifest["source_proof"]["cuts_applied"])
        self.assertEqual(manifest["source_proof"]["source_ranges"], drawing["source_ranges"])
        self.assertEqual(manifest["source_proof"]["material_boundary_validation"], drawing["material_boundary_validation"])
        self.assertTrue(manifest["source_proof"]["source_ranges"][1]["empty"])
        self.assertEqual(manifest["equipment_source_map"]["E001"]["source_indices"], [0, 1])
        self.assertFalse(any(stroke["source_index"] == 1 for scene in result["sheets"] for stroke in geometry(scene)))
        with self.assertRaisesRegex(ValueError, "no_nonempty_material"):
            build_equipment_layout_tiles(drawing, poses([(0, 0, 0), (200, 0, 0)]), [[0, 2], [1]], SHEET)
        all_empty = material_drawing([empty])
        with self.assertRaisesRegex(ValueError, "nonempty_material_assembly_drawing_required"):
            build_equipment_layout_tiles(all_empty, poses([(0, 0, 0)]), [[0]], SHEET)


if __name__ == "__main__":
    unittest.main()

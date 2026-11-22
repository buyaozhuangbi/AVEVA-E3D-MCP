"""Geometry, coverage and resource boundaries for fixed-scale material tiles."""

from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aveva_e3d_mcp.material_assembly_drawing import combine_material_drawing_meshes, generate_material_assembly_drawing
from aveva_e3d_mcp.material_drawing_tiles import normalize_material_tiling_options, plan_and_clip_material_tiles
import aveva_e3d_mcp.material_drawing_tiles as tiles
from test_material_assembly_drawing import CSG_ENGINE_AVAILABLE, csg_source, parameter_source
from test_primitive_csg_mesh import cut


MODULE = "aveva_e3d_mcp.material_drawing_tiles"
SHEET = {"paper_size": "A4", "orientation": "landscape", "scale_denominator": 1}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def projected(sources=None, name="TOP"):
    combined = combine_material_drawing_meshes(sources or [
        parameter_source("=1/11", dimensions={"XLEN": 600, "YLEN": 200, "ZLEN": 40}),
        parameter_source("=1/12", dimensions={"XLEN": 10, "YLEN": 10, "ZLEN": 10}, position=(40, 30, 30)),
    ])
    if not combined.get("success"):
        raise AssertionError(combined)
    drawing = generate_material_assembly_drawing(combined, views=[name], projection_convention="right_handed",
                                                 show_hidden_lines=True)
    if not drawing.get("success"):
        raise AssertionError(drawing)
    return drawing


def fragments(manifest, index):
    return [(page, line) for page in manifest["pages"] for line in page["lines"] if line["global_line_index"] == index]


class MaterialDrawingTilesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = projected()
        cls.templates = {index: next(line for line in cls.original["views"][0]["lines"] if line["source_index"] == index)
                         for index in (0, 1)}

    def drawing(self, definitions, *, minimum=(0, 0), maximum=(400, 180)):
        drawing = deepcopy(self.original)
        view = drawing["views"][0]
        view["bounds"] = {"min": [float(value) for value in minimum], "max": [float(value) for value in maximum]}
        view["dimensions"] = {"width": maximum[0] - minimum[0], "height": maximum[1] - minimum[1], "unit": "mm"}
        view["lines"] = []
        for definition in definitions:
            start, end, *extras = definition
            values = extras[0] if extras else {}
            source_index = values.get("source_index", 0)
            view["lines"].append({**deepcopy(self.templates[source_index]),
                                  "start": [float(value) for value in start], "end": [float(value) for value in end],
                                  "visibility": "visible", "kind": "silhouette", **values})
        drawing["segment_count"] = len(view["lines"])
        return drawing

    def test_normalization_is_strict_and_does_not_mutate_options(self):
        self.assertEqual(normalize_material_tiling_options(None), {"overlap_mm": 10.0, "max_pages": 24})
        options = {"overlap_mm": 0, "max_pages": 4}
        self.assertEqual(normalize_material_tiling_options(options), {"overlap_mm": 0.0, "max_pages": 4})
        self.assertEqual(options, {"overlap_mm": 0, "max_pages": 4})
        for options in (False, [], "auto", {"rows": 2}, {"overlap_mm": True}, {"overlap_mm": None},
                        {"overlap_mm": -1}, {"overlap_mm": math.nan}, {"overlap_mm": math.inf},
                        {"overlap_mm": 10**1000}, {"max_pages": True}, {"max_pages": 0}, {"max_pages": 25},
                        {"max_pages": 4.0}, {"max_pages": "4"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                normalize_material_tiling_options(options)

    def test_explicit_scale_single_view_and_viewport_overlap_preflight(self):
        drawing = self.drawing([((0, 0), (100, 80))], maximum=(100, 80))
        for sheet in (None, {}, {"projection_method": "third_angle", "scale_denominator": 1},
                      {"scale_denominator": True}, {"scale_denominator": 0}, {"scale_denominator": math.inf}):
            with self.subTest(sheet=sheet), self.assertRaises(ValueError):
                plan_and_clip_material_tiles(drawing, sheet)
        with self.assertRaisesRegex(ValueError, "overlap_exceeds_viewport_limit"):
            plan_and_clip_material_tiles(drawing, SHEET, {"overlap_mm": 58})
        accepted = plan_and_clip_material_tiles(drawing, SHEET, {"overlap_mm": math.nextafter(58, 0)})
        self.assertEqual(accepted["viewport_mm"], {"width": 247, "height": 116})
        self.assertEqual(accepted["page_count"], 1)
        for paper, orientation, expected in (("A3", "landscape", (370, 203)), ("A3", "portrait", (247, 326)),
                                             ("A4", "portrait", (160, 203))):
            manifest = plan_and_clip_material_tiles(drawing, {"paper_size": paper, "orientation": orientation,
                                                               "scale_denominator": 2})
            self.assertEqual(tuple(manifest["viewport_mm"].values()), expected)
            self.assertEqual(manifest["tile_span_model_mm"], [value * 2 for value in expected])

    def test_crossing_lines_keep_global_visibility_edge_source_and_exact_intersections(self):
        drawing = self.drawing([
            ((10, 50), (390, 50), {"visibility": "hidden", "parameter_interval": [0.2, 0.7]}),
            ((120, 10), (120, 170), {"kind": "feature", "source_index": 1}),
            ((0, 0), (400, 180)),
        ])
        before = deepcopy(drawing)
        manifest = plan_and_clip_material_tiles(drawing, SHEET, {"overlap_mm": 0})
        self.assertEqual(drawing, before)
        self.assertEqual((manifest["columns"], manifest["rows"], manifest["page_count"]), (2, 2, 4))
        self.assertEqual(manifest["grid_bounds"], {"min": [0, 0], "max": [494, 232]})
        horizontal = {page["column_index"]: line for page, line in fragments(manifest, 0)}
        self.assertEqual(horizontal[0]["start"], [10, 50])
        self.assertEqual(horizontal[0]["end"], [247, 50])
        self.assertEqual(horizontal[1]["start"], [247, 50])
        self.assertEqual(horizontal[1]["end"], [390, 50])
        self.assertEqual(horizontal[0]["source_interval"], [0, 237 / 380])
        self.assertEqual(horizontal[1]["source_interval"], [237 / 380, 1])
        vertical = {page["row_index"]: line for page, line in fragments(manifest, 1)}
        self.assertEqual(vertical[0]["start"], [120, 116])
        self.assertEqual(vertical[1]["end"], [120, 116])
        self.assertEqual(len(fragments(manifest, 2)), 3)
        for page in manifest["pages"]:
            self.assertNotIn("source_proof", page)
            self.assertEqual(page["segment_count"], len(page["lines"]))
            self.assertEqual([page["bounds"]["max"][axis] - page["bounds"]["min"][axis] for axis in (0, 1)], [247, 116])
            for line in page["lines"]:
                original = drawing["views"][0]["lines"][line["global_line_index"]]
                for key in ("source_index", "source_refno", "source_geometry_mode", "edge", "kind", "visibility"):
                    self.assertEqual(line[key], original[key])
                if "parameter_interval" in original:
                    self.assertEqual(line["parameter_interval"], original["parameter_interval"])
                self.assertLess(*line["source_interval"])
                for point in (line["start"], line["end"]):
                    self.assertTrue(all(page["bounds"]["min"][axis] <= point[axis] <= page["bounds"]["max"][axis]
                                        for axis in (0, 1)))
        self.assertFalse(manifest["coverage"]["visibility_recomputed"])
        self.assertEqual(manifest["coverage"]["covered_line_count"], drawing["segment_count"])
        self.assertEqual(manifest["coverage"]["gap_tolerance"], 0)

    def test_page_order_neighbors_constant_spans_and_shared_boundary_duplication(self):
        drawing = self.drawing([((0, 116), (494, 116)), ((247, 0), (247, 232))], maximum=(494, 232))
        manifest = plan_and_clip_material_tiles(drawing, SHEET, {"overlap_mm": 0})
        pages = manifest["pages"]
        self.assertEqual([page["id"] for page in pages], ["TOP-R01-C01", "TOP-R01-C02", "TOP-R02-C01", "TOP-R02-C02"])
        self.assertEqual(pages[0]["bounds"], {"min": [0, 116], "max": [247, 232]})
        self.assertEqual(pages[0]["neighbors"], {"top": None, "bottom": pages[2]["id"], "left": None, "right": pages[1]["id"]})
        self.assertEqual(pages[3]["neighbors"], {"top": pages[1]["id"], "bottom": None, "left": pages[2]["id"], "right": None})
        for index in (0, 1):
            self.assertEqual(len(fragments(manifest, index)), 4)
            self.assertEqual(sorted(line["source_interval"] for _, line in fragments(manifest, index)),
                             [[0, 0.5], [0, 0.5], [0.5, 1], [0.5, 1]])
        self.assertIn("Closed rectangles", manifest["boundary_policy"])

    def test_corner_point_contacts_do_not_create_zero_length_or_invented_edges(self):
        drawing = self.drawing([((0, 0), (247, 116))], maximum=(494, 232))
        manifest = plan_and_clip_material_tiles(drawing, SHEET, {"overlap_mm": 0})
        self.assertEqual(len(fragments(manifest, 0)), 1)
        self.assertEqual(manifest["coverage"]["point_touch_count"], 3)
        page, line = fragments(manifest, 0)[0]
        self.assertEqual((page["row_index"], page["column_index"]), (1, 0))
        self.assertEqual(line["source_interval"], [0, 1])
        self.assertEqual(line["edge"], drawing["views"][0]["lines"][0]["edge"])
        self.assertEqual(sum(page["segment_count"] == 0 for page in manifest["pages"]), 3)

    def test_overlap_repeats_the_real_model_strip_and_blank_pages_remain_present(self):
        drawing = self.drawing([((0, 50), (400, 50), {"visibility": "hidden"})])
        manifest = plan_and_clip_material_tiles(drawing, SHEET)
        self.assertEqual(manifest["tile_step_model_mm"], [237, 106])
        self.assertEqual(manifest["grid_bounds"], {"min": [0, 0], "max": [484, 222]})
        actual = {page["column_index"]: line for page, line in fragments(manifest, 0)}
        self.assertEqual(actual[0]["end"], [247, 50])
        self.assertEqual(actual[1]["start"], [237, 50])
        self.assertGreater(actual[0]["source_interval"][1], actual[1]["source_interval"][0])
        self.assertEqual(sum(page["segment_count"] == 0 for page in manifest["pages"]), 2)
        blank = plan_and_clip_material_tiles(self.drawing([]), SHEET, {"overlap_mm": 0})
        self.assertEqual(blank["page_count"], 4)
        self.assertTrue(all(page["lines"] == [] and page["segment_count"] == 0 for page in blank["pages"]))
        self.assertTrue(blank["coverage"]["complete"])
        self.assertFalse(blank["empty"])

    def test_global_hash_binds_classified_lines_and_manifest_hash_binds_normalized_plan(self):
        drawing = self.drawing([((0, 50), (400, 50))])
        manifest = plan_and_clip_material_tiles(drawing, SHEET)
        same = plan_and_clip_material_tiles(drawing, {"scale_denominator": 1, "orientation": "landscape", "paper_size": "A4"},
                                             {"max_pages": 24, "overlap_mm": 10})
        self.assertEqual(manifest, same)
        axes = {key: drawing["views"][0][key] for key in ("horizontal_axis", "vertical_axis", "toward_viewer")}
        self.assertEqual(manifest["global_linework_sha256"], digest({
            "schema": "material-global-linework-v1", "geometry_sha256": drawing["geometry_sha256"],
            "projection_convention": "right_handed", "view_name": "TOP", **axes,
            "global_bounds": drawing["views"][0]["bounds"], "lines": drawing["views"][0]["lines"]}))
        self.assertEqual(manifest["source_proof_sha256"], digest(manifest["source_proof"]))
        self.assertEqual(manifest["manifest_sha256"], digest({"schema": "material-tile-manifest-v1", "manifest": {
            key: value for key, value in manifest.items() if key not in {"source_proof", "manifest_sha256"}}}))
        retitled = plan_and_clip_material_tiles(drawing, {**SHEET, "title": "ANOTHER WINDOW SET"})
        self.assertNotEqual(retitled["manifest_sha256"], manifest["manifest_sha256"])
        self.assertEqual(retitled["global_linework_sha256"], manifest["global_linework_sha256"])
        drawing["views"][0]["lines"][0]["visibility"] = "hidden"
        hidden = plan_and_clip_material_tiles(drawing, SHEET)
        self.assertNotEqual(hidden["global_linework_sha256"], manifest["global_linework_sha256"])

    def test_a_positive_parameter_gap_smaller_than_float_rounding_still_fails_coverage(self):
        drawing = self.drawing([((0, 50), (400, 50))])
        original_clip = tiles._clip_interval
        def with_tiny_gap(start, delta, minimum, maximum):
            result = original_clip(start, delta, minimum, maximum)
            if result is not None and maximum[0] == 247 and minimum[1] == 0:
                return result[0], result[1] - Fraction(1, 10**30)
            return result
        with patch(f"{MODULE}._clip_interval", side_effect=with_tiny_gap):
            with self.assertRaisesRegex(ValueError, "source_coverage_gap:line=0"):
                plan_and_clip_material_tiles(drawing, SHEET, {"overlap_mm": 0})
        with patch(f"{MODULE}._clip_interval", return_value=None):
            with self.assertRaisesRegex(ValueError, "source_coverage_gap:line=0"):
                plan_and_clip_material_tiles(drawing, SHEET)

    def test_resource_limits_fail_before_returning_a_partial_manifest(self):
        drawing = self.drawing([((0, 50), (400, 50))])
        with self.assertRaisesRegex(ValueError, "page_limit_exceeded"):
            plan_and_clip_material_tiles(drawing, SHEET, {"max_pages": 3})
        many_pages = self.drawing([], maximum=(247 * 25, 100))
        with self.assertRaisesRegex(ValueError, "page_limit_exceeded"):
            plan_and_clip_material_tiles(many_pages, SHEET, {"overlap_mm": 0})
        too_many_lines = deepcopy(drawing)
        too_many_lines["views"][0]["lines"] *= 20_001
        too_many_lines["segment_count"] = 20_001
        with self.assertRaisesRegex(ValueError, "source_line_limit_exceeded"):
            plan_and_clip_material_tiles(too_many_lines, SHEET)
        with patch(f"{MODULE}.MAX_MATERIAL_TILE_LINE_TESTS", 3):
            with self.assertRaisesRegex(ValueError, "line_work_limit_exceeded"):
                plan_and_clip_material_tiles(drawing, SHEET)
        with patch(f"{MODULE}.MAX_MATERIAL_TILE_CLIP_SEGMENTS", 1):
            with self.assertRaisesRegex(ValueError, "clip_segment_limit_exceeded"):
                plan_and_clip_material_tiles(drawing, SHEET)

    def test_tiny_nonzero_line_is_retained_and_rounding_a_true_corner_fragment_fails_closed(self):
        end = math.nextafter(100, math.inf)
        drawing = self.drawing([((100, 50), (end, 50)), ((120, 40), (120 + 4.5e-9, 40))])
        manifest = plan_and_clip_material_tiles(drawing, SHEET, {"overlap_mm": 0})
        for index in (0, 1):
            self.assertEqual(len(fragments(manifest, index)), 1)
            line = fragments(manifest, index)[0][1]
            self.assertNotEqual(line["start"], line["end"])
            self.assertEqual(line["start"], drawing["views"][0]["lines"][index]["start"])
            self.assertEqual(line["end"], drawing["views"][0]["lines"][index]["end"])
        grazing = self.drawing([((0, 232), (494, math.nextafter(0.0, math.inf)))], maximum=(494, 232))
        with self.assertRaisesRegex(ValueError, "clip_below_float_precision"):
            plan_and_clip_material_tiles(grazing, SHEET, {"overlap_mm": 0})

    def test_reported_span_equality_fits_but_one_ulp_more_requires_another_page(self):
        span = float(Fraction(247) * Fraction(0.1))
        fitting = self.drawing([], maximum=(span, 1))
        manifest = plan_and_clip_material_tiles(fitting, {**SHEET, "scale_denominator": 0.1}, {"overlap_mm": 0})
        self.assertEqual(manifest["columns"], 1)
        over = self.drawing([], maximum=(math.nextafter(span, math.inf), 1))
        enlarged = plan_and_clip_material_tiles(over, {**SHEET, "scale_denominator": 0.1}, {"overlap_mm": 0})
        self.assertEqual(enlarged["columns"], 2)
        self.assertEqual(enlarged["pages"][0]["bounds"]["max"][0], enlarged["pages"][1]["bounds"]["min"][0])
        offset = self.drawing([], minimum=(1e8, -1e8), maximum=(1e8 + 70, -1e8 + 10))
        offset_plan = plan_and_clip_material_tiles(offset, {**SHEET, "scale_denominator": 0.1}, {"overlap_mm": 0})
        for left, right in zip(offset_plan["pages"], offset_plan["pages"][1:]):
            self.assertEqual(left["bounds"]["max"][0], right["bounds"]["min"][0])
        collapsed = self.drawing([], minimum=(1e8, 1e8), maximum=(1e8, 1e8))
        with self.assertRaisesRegex(ValueError, "unrepresentable_grid_bounds"):
            plan_and_clip_material_tiles(collapsed, {**SHEET, "scale_denominator": 1e-15})

    def test_malformed_projection_source_and_bounds_fail_without_repairing_linework(self):
        original = self.drawing([((0, 0), (400, 180))])
        for fault in ("legacy", "two_views", "axes", "outside", "degenerate", "source_bool", "source_ref", "edge", "count", "scope"):
            drawing = deepcopy(original)
            if fault == "legacy": drawing["projection_convention"] = "legacy"
            elif fault == "two_views": drawing["views"].append(deepcopy(drawing["views"][0]))
            elif fault == "axes": drawing["views"][0]["toward_viewer"] = [0, 0, -1]
            elif fault == "outside": drawing["views"][0]["lines"][0]["end"][0] = math.nextafter(400, math.inf)
            elif fault == "degenerate": drawing["views"][0]["lines"][0]["end"] = [0, 0]
            elif fault == "source_bool": drawing["views"][0]["lines"][0]["source_index"] = True
            elif fault == "source_ref": drawing["views"][0]["lines"][0]["source_refno"] = "=9/999"
            elif fault == "edge": drawing["views"][0]["lines"][0]["edge"] = [0, 9999]
            elif fault == "count": drawing["segment_count"] = True
            else: drawing["scope"] = "NATIVE GEOMETRY"
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                plan_and_clip_material_tiles(drawing, SHEET)

    def test_actual_globally_projected_material_lines_have_complete_interval_unions_and_shared_proof(self):
        manifest = plan_and_clip_material_tiles(self.original, SHEET)
        self.assertEqual(manifest["page_count"], 6)
        self.assertEqual(manifest["source_proof"]["source_ranges"], self.original["source_ranges"])
        self.assertEqual(manifest["geometry_sha256"] if "geometry_sha256" in manifest else
                         manifest["source_proof"]["geometry_sha256"], self.original["geometry_sha256"])
        for index in range(self.original["segment_count"]):
            ranges = sorted(line["source_interval"] for _, line in fragments(manifest, index))
            cursor = 0
            for low, high in ranges:
                self.assertLessEqual(low, cursor)
                cursor = max(cursor, high)
            self.assertEqual(cursor, 1)
        manifest["source_proof"]["source_ranges"][0]["refno"] = "changed copy"
        self.assertEqual(self.original["source_ranges"][0]["refno"], "=1/11")


@unittest.skipUnless(CSG_ENGINE_AVAILABLE, "Install the geometry extra for cavity/empty-source proof tests.")
class MaterialDrawingTilesCavityTests(unittest.TestCase):
    def test_cavity_and_empty_source_proofs_stay_shared_and_all_empty_is_rejected(self):
        cavity = csg_source("=1/501", cutters=[cut("BOX", {"XLEN": 20, "YLEN": 20, "ZLEN": 10})])
        empty = csg_source("=1/502", cutters=[cut("BOX", {"XLEN": 200, "YLEN": 200, "ZLEN": 100})])
        drawing = projected([cavity, empty, parameter_source("=1/503", position=(150, 0, 0))])
        manifest = plan_and_clip_material_tiles(drawing, SHEET)
        self.assertFalse(manifest["source_proof"]["validation"]["valid"])
        self.assertEqual(manifest["source_proof"]["material_boundary_validation"], drawing["material_boundary_validation"])
        self.assertTrue(manifest["source_proof"]["source_ranges"][1]["empty"])
        self.assertNotIn(1, {line["source_index"] for page in manifest["pages"] for line in page["lines"]})
        self.assertTrue(all("source_ranges" not in page and "source_proof" not in page for page in manifest["pages"]))
        all_empty = projected([empty])
        with self.assertRaisesRegex(ValueError, "nonempty_material_assembly_drawing_required"):
            plan_and_clip_material_tiles(all_empty, SHEET)


if __name__ == "__main__":
    unittest.main()

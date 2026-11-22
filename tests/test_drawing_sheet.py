from __future__ import annotations

import copy
import json
import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.drawing_sheet import build_drawing_sheet, normalize_sheet_options


def sample_drawing(width=1000.0, height=600.0, *, names=("FRONT", "TOP", "SIDE"), offset=(20.0, -30.0)):
    xmin, ymin = offset
    points = [(xmin, ymin), (xmin + width, ymin), (xmin + width, ymin + height), (xmin, ymin + height)]
    lines = [{"start": list(points[index]), "end": list(points[(index + 1) % 4]), "kind": "silhouette"}
             for index in range(4) if points[index] != points[(index + 1) % 4]]
    return {
        "success": True, "complete": True, "coordinate_unit": "mm", "coordinate_frame": "WORLD coordinates; millimetres",
        "show_hidden_lines": False,
        "views": [{"name": name, "bounds": {"min": [xmin, ymin], "max": [xmin + width, ymin + height]},
                   "dimensions": {"width": width, "height": height, "unit": "mm"},
                   "lines": copy.deepcopy(lines)} for name in names],
    }


class DrawingSheetTests(unittest.TestCase):
    def test_options_are_normalized_without_touching_inputs(self):
        raw = {"title": "  Pump_A-1 / detail  ", "drawing_number": " a-2:rev+1 "}
        before = copy.deepcopy(raw)
        options = normalize_sheet_options(raw)
        self.assertEqual(raw, before)
        self.assertEqual(options, {"paper_size": "A3", "orientation": "landscape",
                                   "title": "PUMP_A-1 / DETAIL", "drawing_number": "A-2:REV+1"})
        self.assertNotIn("scale_denominator", options)
        self.assertEqual(normalize_sheet_options(options), options)

    def test_invalid_options_fail_without_geometry(self):
        for options in (None, [], {"extra": 1}, {"paper_size": "A0"}, {"orientation": "LANDSCAPE"},
                        {"title": "泵"}, {"title": "PUMP\n"}, {"title": ""}, {"title": "T" * 121},
                        {"title": "PUMP?"}, {"drawing_number": "A\\B"}, {"drawing_number": "N" * 41},
                        *({"scale_denominator": value} for value in (0, -1, math.inf, math.nan, True, "10", None))):
            with self.subTest(options=options), self.assertRaises(ValueError):
                normalize_sheet_options(options)

    def test_auto_fit_has_known_scale_and_exact_geometry_transform(self):
        # A3 landscape: three cells give 99 1/3 mm geometry width; the 1000 mm
        # source therefore requires >1:10, selecting the next1/2/5 scale1:20.
        drawing = sample_drawing(width=1000, height=600)
        before = copy.deepcopy(drawing)
        scene = build_drawing_sheet(drawing, {})
        self.assertEqual(scene["paper_size_mm"], [420.0, 297.0])
        self.assertEqual(scene["coordinate_space"], "paper")
        self.assertEqual(scene["coordinate_unit"], "mm")
        self.assertEqual(scene["model_coordinate_unit"], "mm")
        self.assertEqual(scene["scale_denominator"], 20.0)
        self.assertEqual(scene["scale_mode"], "auto_fit")
        self.assertFalse(scene["native_draft_sheet"])
        self.assertFalse(scene["engineering_dimensions_certified"])
        self.assertEqual(drawing, before)
        first = scene["views"][0]
        self.assertEqual(first["model_bounds"], {"min": [20.0, -30.0], "max": [1020.0, 570.0]})
        self.assertEqual(first["transform"]["paper_mm_per_model_mm"], .05)
        geometry = [stroke for stroke in scene["strokes"] if stroke["role"] == "silhouette" and stroke["view_name"] == "FRONT"]
        self.assertEqual(len(geometry), 4)
        self.assertEqual(geometry[0]["start"], first["paper_bounds_mm"]["min"])
        self.assertAlmostEqual(geometry[0]["end"][0] - geometry[0]["start"][0], 50.0)
        self.assertEqual([stroke["source_line_index"] for stroke in geometry], [0, 1, 2, 3])
        dimensions = [text for text in scene["texts"] if text["role"] == "dimension" and text["view_name"] == "FRONT"]
        self.assertEqual([text["text"] for text in dimensions], ["1000 MM", "600 MM"])
        self.assertEqual([text["dimension_value_mm"] for text in dimensions], [1000.0, 600.0])
        self.assertEqual(scene["dimension_label_resolution_mm"], .000001)
        self.assertIn("no first-angle or third-angle", scene["layout_policy"])

    def test_explicit_scale_controls_all_views_and_preserves_dimension_values(self):
        drawing = sample_drawing(width=200, height=100)
        scene = build_drawing_sheet(drawing, {"scale_denominator": 10, "title": "pump"})
        self.assertEqual(scene["scale_mode"], "explicit")
        self.assertEqual(scene["options"]["title"], "PUMP")
        self.assertEqual({view["transform"]["scale_denominator"] for view in scene["views"]}, {10.0})
        for view in scene["views"]:
            self.assertAlmostEqual(view["paper_bounds_mm"]["max"][0] - view["paper_bounds_mm"]["min"][0], 20)
            self.assertAlmostEqual(view["paper_bounds_mm"]["max"][1] - view["paper_bounds_mm"]["min"][1], 10)
        self.assertTrue(any(text["text"] == "SCALE 1:10" for text in scene["texts"]))
        self.assertEqual({text["dimension_value_mm"] for text in scene["texts"] if text["role"] == "dimension"}, {200, 100})
        with self.assertRaisesRegex(ValueError, "drawing_sheet_scale_does_not_fit"):
            build_drawing_sheet(drawing, {"scale_denominator": 1})

    def test_every_paper_orientation_and_view_count_remains_inside_printable_border(self):
        for paper in ("A4", "A3"):
            for orientation in ("portrait", "landscape"):
                for names in (("FRONT",), ("TOP", "SIDE"), ("SIDE", "FRONT", "TOP")):
                    for width, height in ((1000, 20), (20, 1000), (1000, 1000), (0, 100), (100, 0), (0, 0)):
                        with self.subTest(paper=paper, orientation=orientation, names=names, width=width, height=height):
                            scene = build_drawing_sheet(sample_drawing(width, height, names=names), {
                                "paper_size": paper, "orientation": orientation,
                                "title": "PUMP / GENERAL_ARRANGEMENT", "drawing_number": "P-100:REV+1",
                            })
                            paper_width, paper_height = scene["paper_size_mm"]
                            self.assertEqual([view["name"] for view in scene["views"]], list(names))
                            for stroke in scene["strokes"]:
                                for x, y in (stroke["start"], stroke["end"]):
                                    self.assertGreaterEqual(x, 10 - 1e-8)
                                    self.assertLessEqual(x, paper_width - 10 + 1e-8)
                                    self.assertGreaterEqual(y, 10 - 1e-8)
                                    self.assertLessEqual(y, paper_height - 10 + 1e-8)
                            for text in scene["texts"]:
                                glyph_width = (6 * len(text["text"]) - 1) / 7 * text["height"]
                                hx, hy = ((text["height"] / 2, glyph_width / 2) if text["rotation"] == 90 else
                                          (glyph_width / 2, text["height"] / 2))
                                x, y = text["position"]
                                self.assertGreaterEqual(x - hx, 10 - 1e-8)
                                self.assertLessEqual(x + hx, paper_width - 10 + 1e-8)
                                self.assertGreaterEqual(y - hy, 10 - 1e-8)
                                self.assertLessEqual(y + hy, paper_height - 10 + 1e-8)
                            self.assertEqual(scene["counts"]["strokes"], len(scene["strokes"]))
                            self.assertEqual(scene["counts"]["texts"], len(scene["texts"]))

    def test_zero_extents_skip_degenerate_dimension_axes(self):
        for width, height in ((0, 20), (20, 0), (0, 0)):
            with self.subTest(width=width, height=height):
                scene = build_drawing_sheet(sample_drawing(width, height, names=("FRONT",)), {})
                labels = [text for text in scene["texts"] if text["role"] == "dimension"]
                self.assertEqual(len(labels), int(width > 0) + int(height > 0))
                self.assertTrue(all(stroke["start"] != stroke["end"] for stroke in scene["strokes"]))

    def test_hidden_geometry_and_source_ranges_preserve_original_view_line_indexes(self):
        drawing = sample_drawing(names=("FRONT",))
        drawing["show_hidden_lines"] = True
        drawing["views"][0]["lines"][0]["visibility"] = "hidden"
        scene = build_drawing_sheet(drawing, {})
        view = scene["views"][0]
        start, end = view["stroke_range"]
        self.assertTrue(all(row["view_name"] == "FRONT" for row in scene["strokes"][start:end]))
        hidden = [stroke for stroke in scene["strokes"] if stroke["visibility"] == "hidden"]
        self.assertEqual(len(hidden), 1)
        self.assertEqual(hidden[0]["source_line_index"], 0)
        self.assertEqual(hidden[0]["role"], "silhouette")
        self.assertEqual(scene["texts"][view["text_range"][0]]["text"], "FRONT")

    def test_malformed_geometry_metadata_and_lines_fail_closed(self):
        original = sample_drawing(names=("FRONT",))
        cases = []
        for key, value in (("success", False), ("complete", False), ("coordinate_unit", "cm"), ("views", [])):
            cases.append({**original, key: value})
        for mutate in (
            lambda row: row["dimensions"].update(width=999),
            lambda row: row["bounds"].update(min=[math.nan, 0]),
            lambda row: row["lines"][0].update(visibility="hidden"),
            lambda row: row["lines"][0].update(kind=[]),
            lambda row: row["lines"][0].update(end=[1e10, 0]),
            lambda row: row["lines"][0].update(start=row["lines"][0]["end"]),
        ):
            candidate = copy.deepcopy(original)
            mutate(candidate["views"][0])
            cases.append(candidate)
        cases.append({**original, "views": [original["views"][0], original["views"][0]]})
        for candidate in cases:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                build_drawing_sheet(candidate, {})

    def test_output_and_source_work_are_bounded(self):
        drawing = sample_drawing(names=("FRONT",))
        drawing["views"][0]["lines"] *= 5001
        with self.assertRaisesRegex(ValueError, "drawing_sheet_line_limit_exceeded"):
            build_drawing_sheet(drawing, {})
        with patch("aveva_e3d_mcp.drawing_sheet.MAX_SHEET_STROKES", 3):
            with self.assertRaisesRegex(ValueError, "drawing_sheet_output_limit_exceeded"):
                build_drawing_sheet(sample_drawing(names=("FRONT",)), {})

    def test_tolerated_outside_segment_cannot_silently_disappear_when_clamped(self):
        drawing = sample_drawing(width=1000, height=600, names=("FRONT",))
        # Relative bounds tolerance is 1e-6 mm here. Both distinct endpoints
        # are admitted in the left tolerance strip, but clamping would map
        # them to one boundary point and silently remove the source line.
        drawing["views"][0]["lines"] = [{
            "start": [20 - 5e-7, 0], "end": [20 - 2e-7, 0], "kind": "feature",
        }]
        with self.assertRaisesRegex(ValueError, "drawing_sheet_geometry_collapsed_by_bounds_tolerance"):
            build_drawing_sheet(drawing, {})

        # An endpoint rounding outside the boundary is still admitted when
        # clamping leaves a real, representable line into the view interior.
        drawing["views"][0]["lines"][0]["end"] = [30, 0]
        scene = build_drawing_sheet(drawing, {})
        geometry = [stroke for stroke in scene["strokes"] if stroke["role"] == "feature"]
        self.assertEqual(len(geometry), 1)
        self.assertEqual(geometry[0]["source_line_index"], 0)
        self.assertAlmostEqual(geometry[0]["end"][0] - geometry[0]["start"][0],
                               10.0 / scene["scale_denominator"])

    def test_unrepresentable_scales_or_dimensions_do_not_silently_collapse_geometry(self):
        drawing = sample_drawing(width=1, height=1, names=("FRONT",))
        for denominator in (1e308, 1e12):
            with self.subTest(denominator=denominator), self.assertRaisesRegex(ValueError, "drawing_sheet_(scale_label_too_long|geometry_below_precision)"):
                build_drawing_sheet(drawing, {"scale_denominator": denominator})
        with self.assertRaisesRegex(ValueError, "drawing_sheet_dimension_below_label_precision"):
            build_drawing_sheet(sample_drawing(width=1e-8, height=1), {})

    def test_long_title_and_number_are_fitted_without_truncation_and_scene_is_deterministic_json(self):
        drawing = sample_drawing()
        options = {"paper_size": "A4", "orientation": "portrait", "title": "T" * 120, "drawing_number": "N" * 40}
        first = build_drawing_sheet(drawing, options)
        second = build_drawing_sheet(drawing, options)
        self.assertEqual(first, second)
        self.assertTrue(json.dumps(first, allow_nan=False))
        title = next(text for text in first["texts"] if text["role"] == "title")
        self.assertEqual(title["text"], "T" * 120)
        self.assertLess(title["height"], 4.0)
        self.assertGreaterEqual(title["height"], 1.5)


if __name__ == "__main__":
    unittest.main()

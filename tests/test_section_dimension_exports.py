"""Physical placement and interchange of resolved section linear dimensions."""

from __future__ import annotations

import io
import math
import sys
import unittest
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    import ezdxf
except ImportError:
    ezdxf = None

from aveva_e3d_mcp.drawing_sheet import build_drawing_sheet
from aveva_e3d_mcp.drawing_sheet_render import rasterize_drawing_sheet_png, render_drawing_sheet_svg
from aveva_e3d_mcp.orthographic_dxf import export_drawing_sheet_dxf
from tests.test_section_drawing_exports import section_drawing


OPTIONS = {"paper_size": "A4", "orientation": "landscape", "scale_denominator": 2,
           "title": "SECTION DIMENSIONS", "drawing_number": "P-DIM-001"}


def dimension(start, end, axis, side, first=0, second=1):
    return {"first_vertex": first, "second_vertex": second, "start": list(start), "end": list(end),
            "axis": axis, "side": side,
            "value_mm": abs(end[0 if axis == "horizontal" else 1] - start[0 if axis == "horizontal" else 1])}


def dimensioned(items=None):
    drawing = section_drawing()
    drawing["views"][0]["linear_dimensions"] = items if items is not None else [
        dimension([11, 22], [21, 22], "horizontal", "top", 0, 1),
        dimension([21, 22], [21, 52], "vertical", "right", 1, 2),
    ]
    return drawing


def text_box(item):
    text_width = (6 * len(item["text"]) - 1) / 7 * item["height"]
    width, height = (text_width, item["height"]) if item["rotation"] == 0 else (item["height"], text_width)
    x, y = item["position"]
    return [x - width / 2, y - height / 2, x + width / 2, y + height / 2]


class SectionDimensionExportTests(unittest.TestCase):
    def test_model_value_and_vertex_trace_survive_actual_paper_transform(self):
        drawing = dimensioned()
        before = deepcopy(drawing)
        scene = build_drawing_sheet(drawing, OPTIONS)
        self.assertEqual(drawing, before)
        self.assertEqual(scene["views"][0]["model_dimensions_mm"], {"width": 10.0, "height": 30.0})
        self.assertEqual(scene["views"][0]["model_bounds"], drawing["views"][0]["bounds"])
        transform = scene["views"][0]["transform"]
        for index, (placed, original) in enumerate(zip(scene["linear_dimensions"], drawing["views"][0]["linear_dimensions"])):
            for field in ("first_vertex", "second_vertex", "start", "end", "value_mm", "axis", "side"):
                self.assertEqual(placed[field], original[field])
            self.assertEqual(placed["dimension_index"], index)
            for key in ("start", "end"):
                expected = [transform["paper_origin_mm"][axis] +
                            (original[key][axis] - transform["model_min"][axis]) / 2 for axis in (0, 1)]
                self.assertEqual(placed["paper_" + key + "_mm"], expected)
            self.assertAlmostEqual(math.dist(placed["dimension_line_start_mm"], placed["dimension_line_end_mm"]),
                                   original["value_mm"] / 2)
            strokes = scene["strokes"][slice(*placed["stroke_range"])]
            labels = scene["texts"][slice(*placed["text_range"])]
            self.assertEqual(len(strokes), 7)
            self.assertEqual(len(labels), 1)
            self.assertTrue(all(item["dimension_index"] == index and item["role"] == "dimension" for item in strokes + labels))
            self.assertEqual(labels[0]["text"], f"{original['value_mm']:g} MM")
            self.assertEqual(labels[0]["height"], 2.2)
            self.assertEqual(labels[0]["position"], placed["label_position_mm"])
        # The existing overall size dimensions stay on bottom/left at 7 mm.
        origin = scene["views"][0]["paper_bounds_mm"]["min"]
        old_labels = [item for item in scene["texts"] if item["role"] == "dimension" and "dimension_index" not in item]
        self.assertEqual(next(item for item in old_labels if item["dimension_axis"] == "width")["position"][1], origin[1] - 11)
        self.assertEqual(next(item for item in old_labels if item["dimension_axis"] == "height")["position"][0], origin[0] - 11)

    def test_four_side_lanes_follow_input_order_and_keep_labels_in_content_cells(self):
        items = []
        for index, side in enumerate(("top", "right", "bottom", "left", "top", "right", "bottom", "left")):
            axis = "horizontal" if side in {"top", "bottom"} else "vertical"
            start, end = ([11, 22], [21, 52])
            items.append(dimension(start, end, axis, side, index, index + 20))
        scene = build_drawing_sheet(dimensioned(items), {**OPTIONS, "scale_denominator": 1})
        self.assertEqual([item["lane_index"] for item in scene["linear_dimensions"]], [0, 0, 0, 0, 1, 1, 1, 1])
        self.assertEqual([item["lane_offset_mm"] for item in scene["linear_dimensions"]], [7, 7, 19, 19, 19, 19, 31, 31])
        labels = [item for item in scene["texts"] if "dimension_index" in item]
        boxes = [text_box(item) for item in labels]
        for box in boxes:
            self.assertGreaterEqual(box[0], 11)
            self.assertLessEqual(box[2], 286)
            self.assertGreaterEqual(box[1], 51)
            self.assertLessEqual(box[3], 188)
        for index, box in enumerate(boxes):
            for other in boxes[index + 1:]:
                self.assertFalse(min(box[2], other[2]) > max(box[0], other[0]) and
                                 min(box[3], other[3]) > max(box[1], other[1]))
        for row in scene["strokes"]:
            if "dimension_index" in row:
                self.assertTrue(all(11 <= point[0] <= 286 and 51 <= point[1] <= 188 for point in (row["start"], row["end"])))

    def test_same_side_eight_dimensions_reserve_space_and_extreme_lane_count_fails(self):
        horizontal = [dimension([11, 22], [21, 22], "horizontal", "bottom", index, index + 10) for index in range(8)]
        scene = build_drawing_sheet(dimensioned(horizontal), {"paper_size": "A3"})
        self.assertEqual(len(scene["linear_dimensions"]), 8)
        self.assertEqual(scene["linear_dimensions"][-1]["lane_offset_mm"], 103)
        self.assertEqual(len([item for item in scene["texts"] if "dimension_index" in item]), 8)
        with self.assertRaisesRegex(ValueError, "linear_dimension_label_line_collision"):
            build_drawing_sheet(dimensioned(horizontal), {"paper_size": "A4"})
        crowded = dimensioned(horizontal)
        for row in crowded["views"][0]["lines"]:
            for key in ("start", "end"):
                row[key][1] = 22 + (row[key][1] - 22) * 100
        crowded["views"][0]["bounds"]["max"][1] = 3022
        crowded["views"][0]["dimensions"]["height"] = 3000
        with self.assertRaisesRegex(ValueError, "scale_does_not_fit"):
            build_drawing_sheet(crowded, {"paper_size": "A4", "scale_denominator": 1})

    def test_short_paper_span_uses_visible_outward_arrows_and_subprecision_rejects(self):
        scene = build_drawing_sheet(dimensioned([dimension([11, 22], [11.001, 52], "horizontal", "top")]), OPTIONS)
        placed = scene["linear_dimensions"][0]
        self.assertEqual(placed["arrow_direction"], "outward")
        arrows = [row for row in scene["strokes"] if row.get("dimension_part") == "arrow"]
        self.assertEqual(len(arrows), 4)
        self.assertTrue(all(math.dist(row["start"], row["end"]) > 2 for row in arrows))
        dimension_line = next(row for row in scene["strokes"] if row.get("dimension_part") == "line")
        self.assertAlmostEqual(math.dist(dimension_line["start"], dimension_line["end"]), 4.0005)
        self.assertEqual(next(row for row in scene["texts"] if "dimension_index" in row)["text"], "0.001 MM")
        tiny = dimensioned([dimension([11, 22], [11.000001, 52], "horizontal", "top")])
        with self.assertRaisesRegex(ValueError, "below_paper_precision"):
            build_drawing_sheet(tiny, {**OPTIONS, "scale_denominator": 100})

    def test_adjacent_top_and_right_long_labels_at_corner_reject_unresolved_overlap(self):
        items = [dimension([20.999999, 52], [21, 52], "horizontal", "top", 0, 1),
                 dimension([21, 51.999999], [21, 52], "vertical", "right", 1, 2)]
        with self.assertRaisesRegex(ValueError, "linear_dimension_label_collision"):
            build_drawing_sheet(dimensioned(items), {**OPTIONS, "scale_denominator": 1})

    def test_invalid_linear_dimension_contract_does_not_modify_input(self):
        base = dimension([11, 22], [21, 22], "horizontal", "top")
        invalid = [None, [], [base] * 9]
        for field, value in (("axis", []), ("side", []), ("axis", "diagonal"), ("side", "left"),
                             ("first_vertex", True), ("second_vertex", 0), ("value_mm", 11),
                             ("value_mm", math.nan), ("start", [10, 22])):
            invalid.append([{**base, field: value}])
        invalid.append([{**base, "unknown": 1}])
        for items in invalid:
            drawing = dimensioned(items)
            if items is None:
                drawing["views"][0]["linear_dimensions"] = None
            before = deepcopy(drawing)
            with self.subTest(items=items), self.assertRaises(ValueError):
                build_drawing_sheet(drawing, OPTIONS)
            # NaN is not equal to itself; repr preserves the mutation evidence.
            self.assertEqual(repr(drawing), repr(before))
        wrong_view = dimensioned()
        wrong_view["views"][0]["name"] = "FRONT"
        with self.assertRaisesRegex(ValueError, "invalid_sheet_linear_dimensions"):
            build_drawing_sheet(wrong_view, OPTIONS)

    def test_shared_three_formats_contain_all_dimension_geometry_and_true_labels(self):
        scene = build_drawing_sheet(dimensioned(), OPTIONS)
        svg = ET.fromstring(render_drawing_sheet_svg(scene))
        paths = svg.findall('{http://www.w3.org/2000/svg}path[@data-role="dimension"]')
        self.assertEqual(len(paths), len([row for row in scene["strokes"] if row["role"] == "dimension"]))
        strings = [row.text for row in svg.findall('{http://www.w3.org/2000/svg}text[@data-role="dimension"]')]
        self.assertEqual(strings.count("10 MM"), 2)
        self.assertEqual(strings.count("30 MM"), 2)
        png = rasterize_drawing_sheet_png(scene, width=1000, height=700)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn("SECTION_DIMENSION", export_drawing_sheet_dxf(scene))


@unittest.skipIf(ezdxf is None, "optional independent ezdxf parser is not installed")
class SectionDimensionCadInteropTests(unittest.TestCase):
    def test_dimension_endpoints_and_true_labels_match_common_scene_after_normal_roundtrip(self):
        scene = build_drawing_sheet(dimensioned(), OPTIONS)
        document = ezdxf.read(io.StringIO(export_drawing_sheet_dxf(scene)))
        audit = document.audit()
        self.assertEqual((audit.errors, audit.fixes), ([], []))
        dimension_lines = list(document.modelspace().query('LINE[layer=="SECTION_DIMENSION"]'))
        expected_lines = [row for row in scene["strokes"] if row["role"] == "dimension"]
        self.assertEqual(len(dimension_lines), len(expected_lines))
        for actual, expected in zip(dimension_lines, expected_lines):
            for point, key in ((actual.dxf.start, "start"), (actual.dxf.end, "end")):
                for left, right in zip(point, [*expected[key], 0]):
                    self.assertAlmostEqual(left, right, places=6)
        labels = list(document.modelspace().query('TEXT[layer=="SECTION_DIMENSION"]'))
        self.assertEqual([row.dxf.text for row in labels], ["10 MM", "30 MM", "10 MM", "30 MM"])
        self.assertEqual([row.dxf.rotation for row in labels], [0, 90, 0, 90])
        for actual, expected in zip(labels, (row for row in scene["texts"] if row["role"] == "dimension")):
            self.assertAlmostEqual(actual.dxf.height, expected["height"], places=6)
            self.assertAlmostEqual(actual.dxf.align_point.x, expected["position"][0], places=6)
            self.assertAlmostEqual(actual.dxf.align_point.y, expected["position"][1], places=6)
        output = io.StringIO()
        document.write(output)
        reread = ezdxf.read(io.StringIO(output.getvalue()))
        self.assertEqual((reread.audit().errors, reread.audit().fixes), ([], []))
        self.assertEqual(tuple(reread.header["$LIMMAX"]), (297, 210))
        self.assertEqual([(tuple(row.dxf.start), tuple(row.dxf.end)) for row in dimension_lines],
                         [(tuple(row.dxf.start), tuple(row.dxf.end))
                          for row in reread.modelspace().query('LINE[layer=="SECTION_DIMENSION"]')])


if __name__ == "__main__":
    unittest.main()

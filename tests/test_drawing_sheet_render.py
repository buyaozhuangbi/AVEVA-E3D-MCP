from __future__ import annotations

import copy
import hashlib
import struct
import sys
import unittest
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.drawing_sheet_render import (
    rasterize_drawing_sheet_png,
    render_drawing_sheet_svg,
    validate_drawing_sheet_scene,
)


def scene():
    return {
        "success": True, "available": True, "complete": True,
        "coordinate_space": "paper", "coordinate_unit": "mm", "model_coordinate_unit": "mm",
        "paper": {"size": "A4", "orientation": "landscape", "width_mm": 297, "height_mm": 210},
        "strokes": [
            {"start": [10, 20], "end": [40, 20], "role": "silhouette", "view_name": "FRONT", "visibility": "visible"},
            {"start": [10, 40], "end": [40, 40], "role": "feature", "view_name": "FRONT", "visibility": "hidden"},
            {"start": [10, 10], "end": [40, 10], "role": "dimension", "view_name": "FRONT", "visibility": "visible"},
        ],
        "texts": [
            {"position": [100, 100], "text": "PUMP_A-1", "height": 7, "rotation": 0, "role": "title", "view_name": None},
            {"position": [200, 100], "text": "600 MM", "height": 7, "rotation": 90, "role": "dimension", "view_name": "FRONT"},
        ],
    }


def tiny_paper_scene():
    sample = scene()
    sample["texts"] = []
    sample["scale_denominator"] = 50
    sample["strokes"] = [
        {"start": [60.0 + 10 * index, 60.0], "end": [60.0 + 10 * index + model_length / 50, 60.0],
         "role": "silhouette", "visibility": "hidden", "view_name": "FRONT"}
        for index, model_length in enumerate((5.7e-10, 1.2e-9, 2.8e-9, 4.5e-9))
    ]
    return sample


def decode_png(png):
    """Decode this renderer's dependency-free RGB/filter-zero PNG output."""
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    cursor, compressed = 8, bytearray()
    width = height = None
    while cursor < len(png):
        length = struct.unpack(">I", png[cursor:cursor + 4])[0]
        kind, data = png[cursor + 4:cursor + 8], png[cursor + 8:cursor + 8 + length]
        assert struct.unpack(">I", png[cursor + 8 + length:cursor + 12 + length])[0] == zlib.crc32(kind + data) & 0xFFFFFFFF
        if kind == b"IHDR":
            width, height = struct.unpack(">II", data[:8])
        elif kind == b"IDAT":
            compressed.extend(data)
        cursor += length + 12
    raw = zlib.decompress(compressed)
    assert all(raw[y * (1 + width * 3)] == 0 for y in range(height))
    pixels = b"".join(raw[y * (1 + width * 3) + 1:(y + 1) * (1 + width * 3)] for y in range(height))

    def pixel(x, y):
        offset = (y * width + x) * 3
        return tuple(pixels[offset:offset + 3])

    return width, height, pixel


class DrawingSheetRenderTests(unittest.TestCase):
    def test_default_svg_and_png_preserve_existing_bytes(self):
        sample = scene()
        svg = render_drawing_sheet_svg(sample)
        self.assertEqual(hashlib.sha256(svg.encode()).hexdigest(),
                         "6166bcbbb6b8df55ea3ec871df219f41547be6a18da2b02eedafbbdcea66ac5c")
        self.assertEqual(svg, render_drawing_sheet_svg(sample, coordinate_decimal_places=6))
        png = rasterize_drawing_sheet_png(sample, width=500, height=500)
        self.assertEqual(hashlib.sha256(png).hexdigest(),
                         "79a1c4a16676795d3c095aa676b9de2b396349910c31908657f9359049b4b886")
        with self.assertRaises(TypeError):
            rasterize_drawing_sheet_png(sample, coordinate_decimal_places=15)

    def test_fifteen_places_preserve_scaled_tiny_hidden_svg_intervals(self):
        sample = tiny_paper_scene()
        original = copy.deepcopy(sample)
        document = ET.fromstring(render_drawing_sheet_svg(sample, coordinate_decimal_places=15))
        paths = document.findall("{http://www.w3.org/2000/svg}path")
        self.assertEqual(len(paths), 4)
        for path, stroke in zip(paths, sample["strokes"]):
            values = path.attrib["d"].split()
            start = tuple(float(value) for value in values[1:3])
            end = tuple(float(value) for value in values[4:6])
            self.assertNotEqual(start, end)
            self.assertEqual(path.attrib["stroke-dasharray"], "2 1")
            self.assertEqual(path.attrib["data-role"], "silhouette")
            self.assertGreater(end[0] - start[0], 0)
            self.assertAlmostEqual(end[0] - start[0], stroke["end"][0] - stroke["start"][0], delta=1e-14)
        self.assertEqual(sample, original)

    def test_default_svg_keeps_legacy_rounding_and_high_precision_does_not_leak(self):
        sample = tiny_paper_scene()
        baseline = render_drawing_sheet_svg(scene())
        legacy = render_drawing_sheet_svg(sample)
        paths = ET.fromstring(legacy).findall("{http://www.w3.org/2000/svg}path")
        for path in paths:
            values = path.attrib["d"].split()
            self.assertEqual(values[1:3], values[4:6])
        sample["serialization_precision"] = {"coordinate_decimal_places": 15}
        self.assertEqual(render_drawing_sheet_svg(sample), legacy)
        render_drawing_sheet_svg(sample, coordinate_decimal_places=15)
        self.assertEqual(render_drawing_sheet_svg(sample), legacy)
        self.assertEqual(render_drawing_sheet_svg(scene()), baseline)

    def test_high_precision_svg_rejects_quantization_in_paper_xy(self):
        sample = tiny_paper_scene()
        sample["strokes"] = [{"start": [0, 0], "end": [2e-16, 0], "role": "silhouette",
                              "visibility": "hidden", "view_name": "FRONT"}]
        with self.assertRaisesRegex(ValueError, "^sheet_geometry_below_svg_precision$"):
            render_drawing_sheet_svg(sample, coordinate_decimal_places=15)

    def test_high_precision_svg_checks_actual_flipped_y_coordinates(self):
        sample = tiny_paper_scene()
        delta = 1e-14
        self.assertNotEqual(format(0, ".15f"), format(delta, ".15f"))
        self.assertEqual(210.0 - delta, 210.0)
        sample["strokes"] = [{"start": [60, 0], "end": [60, delta], "role": "silhouette",
                              "visibility": "hidden", "view_name": "FRONT"}]
        with self.assertRaisesRegex(ValueError, "sheet_geometry_below_svg_precision_after_y_flip"):
            render_drawing_sheet_svg(sample, coordinate_decimal_places=15)

    def test_svg_precision_requires_strict_integer_from_six_to_fifteen(self):
        for value in (True, False, None, "15", 6.0, 15.0, 5, 16, [], {}, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "invalid_coordinate_decimal_places"):
                render_drawing_sheet_svg(scene(), coordinate_decimal_places=value)
        self.assertTrue(render_drawing_sheet_svg(scene(), coordinate_decimal_places=15).startswith("<svg"))

    def test_higher_svg_precision_retains_existing_section_hatch_guard(self):
        sample = tiny_paper_scene()
        sample["strokes"] = [{**sample["strokes"][0], "role": "hatch", "view_name": "SECTION", "visibility": "visible"}]
        with self.assertRaisesRegex(ValueError, "section_hatch_output_geometry_below_precision"):
            render_drawing_sheet_svg(sample, coordinate_decimal_places=15)

    def test_svg_retains_physical_paper_geometry_and_annotation_coordinates(self):
        original = scene()
        saved = copy.deepcopy(original)
        svg = render_drawing_sheet_svg(original)
        document = ET.fromstring(svg)
        self.assertEqual(document.attrib["width"], "297mm")
        self.assertEqual(document.attrib["height"], "210mm")
        self.assertEqual(document.attrib["viewBox"], "0 0 297 210")
        paths = document.findall("{http://www.w3.org/2000/svg}path")
        self.assertEqual(paths[0].attrib["d"], "M 10 190 L 40 190")
        self.assertEqual(paths[1].attrib["stroke-dasharray"], "2 1")
        self.assertEqual(paths[1].attrib["d"], "M 10 170 L 40 170")
        texts = document.findall("{http://www.w3.org/2000/svg}text")
        self.assertEqual(texts[0].text, "PUMP_A-1")
        self.assertEqual((texts[0].attrib["x"], texts[0].attrib["y"], texts[0].attrib["textLength"]), ("100", "110", "47"))
        self.assertEqual(texts[1].attrib["transform"], "rotate(-90 200 110)")
        self.assertEqual(texts[1].text, "600 MM")
        self.assertEqual(original, saved)

    def test_png_letterboxes_full_page_without_distorting_physical_coordinates(self):
        sample = scene()
        png = rasterize_drawing_sheet_png(sample, width=500, height=500)
        width, height, pixel = decode_png(png)
        self.assertEqual((width, height), (500, 500))
        self.assertEqual(pixel(250, 0), (235, 238, 241))
        self.assertEqual(pixel(250, 80), (255, 255, 255))
        scale = min((500 - 2) / 297, (500 - 2) / 210)
        ox, oy = (500 - 297 * scale) / 2, (500 - 210 * scale) / 2
        # A known model outline is at sheet y=20, converted using exactly the
        # same isotropic paper scale as the full page.
        x, y = round(ox + 25 * scale), round(oy + 190 * scale)
        self.assertEqual(pixel(x, y), (23, 33, 43))
        self.assertEqual(pixel(x, y - 4), (255, 255, 255))

    def test_hidden_dash_and_gap_use_paper_millimetres(self):
        width, height, pixel = decode_png(rasterize_drawing_sheet_png(scene(), width=1200, height=900))
        scale = min((width - 2) / 297, (height - 2) / 210)
        ox, oy = (width - 297 * scale) / 2, (height - 210 * scale) / 2
        y = round(oy + 170 * scale)
        self.assertEqual(pixel(round(ox + 11 * scale), y), (113, 128, 150))
        self.assertEqual(pixel(round(ox + 12.5 * scale), y), (255, 255, 255))
        self.assertEqual(pixel(round(ox + 13.5 * scale), y), (113, 128, 150))

    def test_png_text_and_underscore_are_rendered_deterministically_without_fallback(self):
        sample = scene()
        first = rasterize_drawing_sheet_png(sample, width=1200, height=900)
        self.assertEqual(first, rasterize_drawing_sheet_png(sample, width=1200, height=900))
        changed = copy.deepcopy(sample)
        changed["texts"][0]["text"] = "PUMP A-1"
        self.assertNotEqual(first, rasterize_drawing_sheet_png(changed, width=1200, height=900))
        changed = copy.deepcopy(sample)
        changed["texts"][1]["rotation"] = 0
        self.assertNotEqual(first, rasterize_drawing_sheet_png(changed, width=1200, height=900))

    def test_failed_scene_unsupported_glyphs_injection_and_outside_geometry_are_rejected(self):
        cases = []
        for field, value in (("success", False), ("complete", None)):
            changed = scene(); changed[field] = value; cases.append(changed)
        for text in ("中文", "PUMP?", "PUMP_a", "<SCRIPT>", "A\nB"):
            changed = scene(); changed["texts"][0]["text"] = text; cases.append(changed)
        changed = scene(); changed["strokes"][0]["start"] = [-1, 20]; cases.append(changed)
        changed = scene(); changed["texts"][0]["position"] = [0, 0]; cases.append(changed)
        changed = scene(); changed["texts"][0]["height"] = float("inf"); cases.append(changed)
        changed = scene(); changed["paper"]["width_mm"] = 210; cases.append(changed)
        for field, value in (("coordinate_space", "WORLD"), ("coordinate_unit", "cm"), ("model_coordinate_unit", None)):
            changed = scene(); changed[field] = value; cases.append(changed)
        for invalid in cases:
            with self.subTest(invalid=invalid):
                for render in (render_drawing_sheet_svg, rasterize_drawing_sheet_png):
                    with self.assertRaises(ValueError):
                        render(invalid)

    def test_dimensions_output_work_and_scene_array_limits_are_bounded(self):
        for dimensions in ((63, 300), (2049, 300), (300, True), (300.0, 300)):
            with self.subTest(dimensions=dimensions), self.assertRaisesRegex(ValueError, "invalid_image_dimensions"):
                rasterize_drawing_sheet_png(scene(), width=dimensions[0], height=dimensions[1])
        with self.assertRaisesRegex(ValueError, "image_pixel_limit_exceeded"):
            rasterize_drawing_sheet_png(scene(), width=2048, height=2048)
        oversized = scene(); oversized["strokes"] *= 7334
        with self.assertRaisesRegex(ValueError, "sheet_stroke_limit"):
            validate_drawing_sheet_scene(oversized)
        oversized = scene(); oversized["texts"] *= 65
        with self.assertRaisesRegex(ValueError, "sheet_text_limit"):
            validate_drawing_sheet_scene(oversized)

    def test_generated_three_view_sheet_drives_both_renderers(self):
        from aveva_e3d_mcp.drawing_sheet import build_drawing_sheet
        from aveva_e3d_mcp.orthographic_drawing import generate_orthographic_drawing
        from tests.test_orthographic_drawing import BOX_TRIANGLES, BOX_VERTICES
        drawing = generate_orthographic_drawing(BOX_VERTICES, BOX_TRIANGLES)
        sheet = build_drawing_sheet(drawing, {"title": "Pump_A-1", "drawing_number": "A/001"})
        svg = render_drawing_sheet_svg(sheet)
        self.assertIn("PUMP_A-1", svg)
        self.assertIn("NO A/001", svg)
        self.assertIn('data-role="dimension"', svg)
        self.assertTrue(rasterize_drawing_sheet_png(sheet).startswith(b"\x89PNG\r\n\x1a\n"))


if __name__ == "__main__":
    unittest.main()

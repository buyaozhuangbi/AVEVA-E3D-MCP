from __future__ import annotations

import struct
import sys
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.orthographic_png import rasterize_orthographic_png


def sample_views():
    return [{
        "name": "FRONT",
        "bounds": {"min": [0.0, 0.0], "max": [10.0, 6.0]},
        "dimensions": {"width": 10.0, "height": 6.0, "unit": "mm"},
        "display_scale_px_per_mm": 12.0,
        "lines": [
            {"start": [0.0, 0.0], "end": [10.0, 0.0], "kind": "silhouette"},
            {"start": [10.0, 0.0], "end": [10.0, 6.0], "kind": "feature"},
            {"start": [0.0, 6.0], "end": [10.0, 6.0], "kind": "feature", "visibility": "hidden"},
        ],
    }]


class OrthographicPngTests(unittest.TestCase):
    def test_png_has_signature_valid_ihdr_and_crc(self):
        png = rasterize_orthographic_png(sample_views(), width=320, height=180)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        length = struct.unpack(">I", png[8:12])[0]
        self.assertEqual(png[12:16], b"IHDR")
        self.assertEqual(length, 13)
        self.assertEqual(struct.unpack(">2I", png[16:24]), (320, 180))
        self.assertEqual(png[24:29], bytes((8, 2, 0, 0, 0)))
        stored_crc = struct.unpack(">I", png[29:33])[0]
        self.assertEqual(stored_crc, zlib.crc32(png[12:29]) & 0xFFFFFFFF)
        self.assertTrue(png.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82"))

    def test_rendering_is_deterministic_and_contains_image_data(self):
        first = rasterize_orthographic_png(sample_views(), width=320, height=180)
        second = rasterize_orthographic_png(sample_views(), width=320, height=180)
        self.assertEqual(first, second)
        self.assertIn(b"IDAT", first)

    def test_changed_labels_and_linework_change_rendered_png(self):
        original = sample_views()
        changed_label = sample_views()
        changed_label[0]["name"] = "TOP"
        changed_line = sample_views()
        changed_line[0]["lines"][0]["end"] = [7.0, 0.0]
        png = rasterize_orthographic_png(original, width=320, height=180)
        self.assertNotEqual(png, rasterize_orthographic_png(changed_label, width=320, height=180))
        self.assertNotEqual(png, rasterize_orthographic_png(changed_line, width=320, height=180))

    def test_equal_scale_panels_are_supported_and_ordered(self):
        three_views = []
        for name, dx, dy in (("FRONT", 10, 6), ("TOP", 10, 4), ("SIDE", 4, 6)):
            three_views.append({
                "name": name,
                "bounds": {"min": [0, 0], "max": [dx, dy]},
                "dimensions": {"width": dx, "height": dy, "unit": "mm"},
                "lines": [{"start": [0, 0], "end": [dx, dy], "kind": "silhouette"}],
            })
        png = rasterize_orthographic_png(three_views)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_malformed_views_and_lines_fail_closed(self):
        for views in (None, [], [{"name": "UNKNOWN"}], [{"name": "FRONT", "bounds": {}}]):
            with self.subTest(views=views), self.assertRaises(ValueError):
                rasterize_orthographic_png(views)
        malformed = sample_views()
        malformed[0]["lines"][0]["end"] = [float("nan"), 0]
        with self.assertRaisesRegex(ValueError, "invalid_line"):
            rasterize_orthographic_png(malformed)
        outside = sample_views()
        outside[0]["lines"][0]["end"] = [11, 0]
        with self.assertRaisesRegex(ValueError, "line_outside_bounds"):
            rasterize_orthographic_png(outside)

    def test_dimension_labels_must_match_projection_bounds(self):
        mismatched = sample_views()
        mismatched[0]["dimensions"]["width"] = 11.0
        with self.assertRaisesRegex(ValueError, "dimensions_bounds_mismatch"):
            rasterize_orthographic_png(mismatched)

        close_enough = sample_views()
        close_enough[0]["dimensions"]["width"] = 10.0 + 5e-10
        close_enough[0]["dimensions"]["height"] = 6.0 - 5e-10
        self.assertTrue(rasterize_orthographic_png(close_enough).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_image_dimensions_pixels_and_segment_count_are_bounded(self):
        for dims in ((63, 100), (2049, 100), (100, 2049)):
            with self.subTest(dims=dims), self.assertRaisesRegex(ValueError, "invalid_image_dimensions"):
                rasterize_orthographic_png(sample_views(), width=dims[0], height=dims[1])
        with self.assertRaisesRegex(ValueError, "image_pixel_limit_exceeded"):
            rasterize_orthographic_png(sample_views(), width=2048, height=2048)
        too_many = sample_views()
        too_many[0]["lines"] = [too_many[0]["lines"][0]] * 20_001
        with self.assertRaisesRegex(ValueError, "drawing_segment_limit_exceeded"):
            rasterize_orthographic_png(too_many)


if __name__ == "__main__":
    unittest.main()

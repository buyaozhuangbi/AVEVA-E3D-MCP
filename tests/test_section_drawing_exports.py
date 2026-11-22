"""Section-view exports and byte-compatible ordinary drawing defaults."""

from __future__ import annotations

import hashlib
import io
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
from aveva_e3d_mcp.drawing_sheet_render import (
    rasterize_drawing_sheet_png,
    render_drawing_sheet_svg,
    validate_drawing_sheet_scene,
)
from aveva_e3d_mcp.orthographic_drawing import _make_svg, generate_orthographic_drawing
from aveva_e3d_mcp.orthographic_dxf import export_drawing_sheet_dxf, export_orthographic_dxf
from aveva_e3d_mcp.orthographic_png import rasterize_orthographic_png


def section_drawing(name: str = "SECTION") -> dict:
    corners = ((11.0, 22.0), (21.0, 22.0), (21.0, 52.0), (11.0, 52.0))
    return {
        "success": True, "complete": True, "coordinate_unit": "mm",
        "coordinate_frame": "test model mm", "hidden_line_removal": True,
        "show_hidden_lines": False,
        "views": [{
            "name": name, "coordinate_unit": "mm",
            "bounds": {"min": [11.0, 22.0], "max": [21.0, 52.0]},
            "dimensions": {"width": 10.0, "height": 30.0, "unit": "mm"},
            "lines": [{"kind": "silhouette", "visibility": "visible",
                       "start": list(point), "end": list(corners[(index + 1) % 4])}
                      for index, point in enumerate(corners)],
        }],
    }


SHEET_OPTIONS = {"paper_size": "A4", "orientation": "landscape", "scale_denominator": 2,
                 "title": "PUMP SECTION", "drawing_number": "P-001"}


class SectionDrawingExportTests(unittest.TestCase):
    def test_section_uses_existing_plain_svg_png_and_dxf_paths(self):
        drawing = section_drawing()
        svg = _make_svg(deepcopy(drawing["views"]), footer="PARAMETER MESH SECTION")
        root = ET.fromstring(svg)
        ns = {"s": "http://www.w3.org/2000/svg"}
        self.assertEqual(root.find("s:g/s:g", ns).attrib["data-view"], "SECTION")
        self.assertEqual(len(root.findall(".//s:path", ns)), 4)
        self.assertEqual(root.findall("s:text", ns)[-1].text, "PARAMETER MESH SECTION")
        png = rasterize_orthographic_png(drawing["views"], width=800, height=500)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual((int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")),
                         (800, 500))
        self.assertEqual(png, rasterize_orthographic_png(drawing["views"], width=800, height=500))
        dxf = export_orthographic_dxf(drawing)
        self.assertIn("SECTION_OUTLINE", dxf)
        self.assertIn("SECTION_DIMENSION", dxf)
        self.assertIn("10.000000 mm", dxf)
        self.assertIn("30.000000 mm", dxf)
        self.assertNotIn("SECTION_HIDDEN", dxf)

    def test_section_sheet_uses_paper_mm_and_true_model_dimensions(self):
        scene = build_drawing_sheet(section_drawing(), SHEET_OPTIONS)
        self.assertEqual(scene["views"][0]["name"], "SECTION")
        self.assertEqual(scene["scale_denominator"], 2)
        self.assertEqual(scene["views"][0]["model_dimensions_mm"], {"width": 10.0, "height": 30.0})
        labels = {row["text"] for row in scene["texts"]}
        self.assertTrue({"SECTION", "10 MM", "30 MM", "SCALE 1:2"}.issubset(labels))
        validated = validate_drawing_sheet_scene(scene)
        self.assertTrue(any(row["view_name"] == "SECTION" for row in validated["strokes"]))
        svg = ET.fromstring(render_drawing_sheet_svg(scene))
        self.assertEqual(svg.attrib["width"], "297mm")
        self.assertEqual(svg.attrib["height"], "210mm")
        png = rasterize_drawing_sheet_png(scene, width=1000, height=700)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        dxf = export_drawing_sheet_dxf(scene)
        self.assertIn("SECTION_OUTLINE", dxf)
        self.assertIn("SECTION_DIMENSION", dxf)
        self.assertIn("9\n$INSUNITS\n70\n4", dxf)

    def test_exporter_view_count_duplicates_and_unknown_name_guards_remain(self):
        for names in (("SECTION", "SECTION"), ("ISOMETRIC",), ("FRONT", "TOP", "SIDE", "SECTION")):
            drawing = section_drawing()
            drawing["views"] = [section_drawing(name)["views"][0] for name in names]
            with self.subTest(names=names):
                with self.assertRaises(ValueError):
                    rasterize_orthographic_png(drawing["views"])
                with self.assertRaises(ValueError):
                    export_orthographic_dxf(drawing)
                with self.assertRaises(ValueError):
                    build_drawing_sheet(drawing, SHEET_OPTIONS)
        scene = build_drawing_sheet(section_drawing(), SHEET_OPTIONS)
        scene["strokes"][-1]["view_name"] = "ISOMETRIC"
        with self.assertRaisesRegex(ValueError, "invalid_sheet_view_name"):
            validate_drawing_sheet_scene(scene)

    def test_custom_svg_footer_is_escaped_and_empty_override_is_respected(self):
        views = section_drawing()["views"]
        custom = "SECTION <A&B> \"mesh\""
        svg = _make_svg(deepcopy(views), hidden_line_removal=True, show_hidden_lines=True, footer=custom)
        root = ET.fromstring(svg)
        self.assertEqual(root.findall("{http://www.w3.org/2000/svg}text")[-1].text, custom)
        self.assertIn("&lt;A&amp;B&gt;", svg)
        self.assertNotIn("occluded portions are dashed", svg)
        empty = ET.fromstring(_make_svg(deepcopy(views), footer=""))
        self.assertIsNone(empty.findall("{http://www.w3.org/2000/svg}text")[-1].text)
        with self.assertRaisesRegex(ValueError, "invalid_svg_footer"):
            _make_svg(deepcopy(views), footer=False)

    def test_existing_ordinary_and_sheet_export_bytes_are_unchanged(self):
        # Captured before adding SECTION support. These compare complete output,
        # including all three old SVG footer branches and paper scene output.
        drawing = section_drawing("FRONT")
        footer_hashes = (
            (False, False, "3a4a75ac402b3561e9e2ededd3e8e60ad07e19cf3529ebde9a8353c5b75f91eb"),
            (True, False, "3731ff2e36cb5fac70acd3dd5ce4865f3117e0e9182d0ece2d03409f21b3990f"),
            (True, True, "e7de3bb2415eddfd1db97fab0c93d9498c6d6580b37345f1d306bfb1193b4d60"),
        )
        for hidden, shown, expected in footer_hashes:
            with self.subTest(hidden=hidden, shown=shown):
                svg = _make_svg(deepcopy(drawing["views"]), hidden_line_removal=hidden, show_hidden_lines=shown)
                self.assertEqual(hashlib.sha256(svg.encode()).hexdigest(), expected)
                self.assertEqual(svg, _make_svg(deepcopy(drawing["views"]), hidden_line_removal=hidden,
                                               show_hidden_lines=shown, footer=None))
        scene = build_drawing_sheet(drawing, SHEET_OPTIONS)
        exports = (
            (rasterize_orthographic_png(drawing["views"], width=800, height=500),
             "05de848903a8c5e2046b7dec8bdaad09e2241d046a3153130d07da533208a724"),
            (export_orthographic_dxf(drawing).encode("ascii"),
             "5cd184d48fae280786ae65595bee39d5893b248b568c1f2f4b7dc8c2111ec047"),
            (render_drawing_sheet_svg(scene).encode(),
             "0879df8dbfec467eb0381f3bebe208cae4d5e272d7b455b7a1f97784a54b373f"),
            (rasterize_drawing_sheet_png(scene, width=1000, height=700),
             "54d3c44178499da68cff5810e4413625e2dcdb236b86109ccbecab845e1e27b7"),
            (export_drawing_sheet_dxf(scene).encode("ascii"),
             "30fadab62be722573e88700d568fec2da1c0e16ec8579389592a70f6ebbeaa5f"),
        )
        for data, expected in exports:
            self.assertEqual(hashlib.sha256(data).hexdigest(), expected)

    def test_section_is_not_an_input_projection_for_orthographic_generator(self):
        result = generate_orthographic_drawing([], [], views=["SECTION"])
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "unsupported_orthographic_views")
        self.assertEqual(result["views"], [])


@unittest.skipIf(ezdxf is None, "optional ezdxf parser is not installed")
class SectionDrawingDxfInteropTests(unittest.TestCase):
    def parse(self, data):
        document = ezdxf.read(io.StringIO(data))
        audit = document.audit()
        self.assertEqual(audit.errors, [])
        self.assertEqual(audit.fixes, [])
        self.assertEqual(document.units, 4)
        return document

    def test_plain_section_uses_model_mm_and_section_layers(self):
        document = self.parse(export_orthographic_dxf(section_drawing()))
        outlines = list(document.modelspace().query('LINE[layer=="SECTION_OUTLINE"]'))
        self.assertEqual(sorted((line.dxf.end - line.dxf.start).magnitude for line in outlines),
                         [10.0, 10.0, 30.0, 30.0])
        labels = list(document.modelspace().query('TEXT[layer=="SECTION_DIMENSION"]'))
        self.assertEqual({row.dxf.text for row in labels}, {"10.000000 mm", "30.000000 mm"})

    def test_sheet_section_scales_geometry_and_roundtrips_without_repair(self):
        scene = build_drawing_sheet(section_drawing(), SHEET_OPTIONS)
        document = self.parse(export_drawing_sheet_dxf(scene))
        outlines = list(document.modelspace().query('LINE[layer=="SECTION_OUTLINE"]'))
        self.assertEqual(sorted((line.dxf.end - line.dxf.start).magnitude for line in outlines),
                         [5.0, 5.0, 15.0, 15.0])
        labels = list(document.modelspace().query('TEXT[layer=="SECTION_DIMENSION"]'))
        self.assertEqual({row.dxf.text for row in labels}, {"10 MM", "30 MM"})
        self.assertEqual(next(row for row in labels if row.dxf.text == "30 MM").dxf.rotation, 90.0)
        serialized = io.StringIO()
        document.write(serialized)
        reread = self.parse(serialized.getvalue())
        self.assertEqual(tuple(reread.header["$LIMMAX"]), (297.0, 210.0))
        self.assertEqual(len(reread.modelspace()), len(scene["strokes"]) + len(scene["texts"]))


if __name__ == "__main__":
    unittest.main()

"""Section hatch line presentation, physical units and export isolation."""

from __future__ import annotations

import io
import math
import struct
import sys
import unittest
import xml.etree.ElementTree as ET
import zlib
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    import ezdxf
except ImportError:
    ezdxf = None

from aveva_e3d_mcp.drawing_sheet import build_drawing_sheet
from aveva_e3d_mcp.drawing_sheet_render import (
    rasterize_drawing_sheet_png, render_drawing_sheet_svg, validate_drawing_sheet_scene,
)
from aveva_e3d_mcp.orthographic_drawing import _make_svg
from aveva_e3d_mcp.orthographic_dxf import export_drawing_sheet_dxf, export_orthographic_dxf
from aveva_e3d_mcp.orthographic_png import rasterize_orthographic_png
from aveva_e3d_mcp.section_hatch_precision import validate_section_hatch_precision
from tests.test_section_drawing_exports import SHEET_OPTIONS, section_drawing


def hatched_drawing():
    drawing = section_drawing()
    drawing["hatching_applied"] = True
    # Deliberately append after the outlines, including a line on their upper
    # edge: paint order must preserve the dark boundary regardless of input.
    drawing["views"][0]["lines"].extend([
        {"kind": "hatch", "visibility": "visible", "start": [11.0, 37.0], "end": [21.0, 37.0]},
        {"kind": "hatch", "visibility": "visible", "start": [11.0, 22.0], "end": [21.0, 52.0]},
        {"kind": "hatch", "visibility": "visible", "start": [11.0, 52.0], "end": [21.0, 52.0]},
    ])
    return drawing


def contour_drawing(contours, hatches, *, bounds=None):
    drawing = section_drawing()
    lines = []
    points = [point for contour in contours for point in contour]
    for contour in contours:
        for a, b in zip(contour, contour[1:] + contour[:1]):
            lines.append({"kind": "silhouette", "visibility": "visible", "start": list(a), "end": list(b)})
    lines.extend({"kind": "hatch", "visibility": "visible", "start": list(a), "end": list(b)} for a, b in hatches)
    bounds = bounds or {"min": [min(point[axis] for point in points) for axis in (0, 1)],
                        "max": [max(point[axis] for point in points) for axis in (0, 1)]}
    drawing["views"][0].update(lines=lines, bounds=bounds,
                               dimensions={"width": bounds["max"][0] - bounds["min"][0],
                                           "height": bounds["max"][1] - bounds["min"][1], "unit": "mm"})
    drawing["hatching_applied"] = True
    return drawing


def direct_sheet_scene(drawing):
    # Keep the physical paper contract but avoid the layout builder so direct
    # SVG/DXF scene consumers must independently check their encoded topology.
    return {"success": True, "complete": True, "coordinate_space": "paper",
            "coordinate_unit": "mm", "model_coordinate_unit": "mm", "scale_denominator": 1,
            "paper": {"size": "A4", "orientation": "landscape", "width_mm": 297, "height_mm": 210},
            "strokes": [{"role": line["kind"], "start": line["start"], "end": line["end"],
                         "view_name": "SECTION", "visibility": "visible"} for line in drawing["views"][0]["lines"]],
            "texts": []}


def rgb_pixels(png):
    """Decode this dependency-free encoder's unfiltered RGB PNG for pixel QA."""
    width, height = struct.unpack(">II", png[16:24])
    compressed = bytearray()
    position = 8
    while position < len(png):
        length = int.from_bytes(png[position:position + 4], "big")
        kind = png[position + 4:position + 8]
        if kind == b"IDAT":
            compressed.extend(png[position + 8:position + 8 + length])
        position += length + 12
    raw = zlib.decompress(compressed)
    row_size = width * 3 + 1
    pixels = []
    for y in range(height):
        row = raw[y * row_size:(y + 1) * row_size]
        if row[0] != 0:
            raise AssertionError("unexpected PNG scanline filter")
        pixels.extend(tuple(row[index:index + 3]) for index in range(1, len(row), 3))
    return pixels


class SectionHatchingExportTests(unittest.TestCase):
    def test_plain_svg_uses_light_solid_thin_hatch_and_paints_outline_last(self):
        drawing = hatched_drawing()
        svg = ET.fromstring(_make_svg(deepcopy(drawing["views"]), footer="NONZERO CONTOUR FILL"))
        paths = svg.findall(".//{http://www.w3.org/2000/svg}path")
        self.assertEqual([path.attrib["class"] for path in paths], ["hatch"] * 3 + ["silhouette"] * 4)
        for path in paths[:3]:
            self.assertEqual(path.attrib["stroke"], "#b0b0b0")
            self.assertEqual(path.attrib["stroke-width"], "0.6")
            self.assertNotIn("stroke-dasharray", path.attrib)
        hatch_xy = [float(token) for token in paths[0].attrib["d"].replace("M", "").replace("L", "").split()]
        outline_xy = [[float(token) for token in path.attrib["d"].replace("M", "").replace("L", "").split()]
                      for path in paths[3:]]
        xs = [value for row in outline_xy for value in row[0::2]]
        ys = [value for row in outline_xy for value in row[1::2]]
        self.assertEqual(hatch_xy[0], min(xs))
        self.assertEqual(hatch_xy[2], max(xs))
        self.assertAlmostEqual(hatch_xy[1], (min(ys) + max(ys)) / 2, places=5)
        self.assertEqual(hatch_xy[1], hatch_xy[3])

    def test_plain_and_sheet_png_show_gray_fill_without_overwriting_dark_outline(self):
        drawing = hatched_drawing()
        contour_only = deepcopy(drawing)
        contour_only["views"][0]["lines"] = contour_only["views"][0]["lines"][:4]
        scene, reference_scene = (build_drawing_sheet(value, SHEET_OPTIONS) for value in (drawing, contour_only))
        pairs = (
            (rasterize_orthographic_png(contour_only["views"], width=800, height=500),
             rasterize_orthographic_png(drawing["views"], width=800, height=500)),
            (rasterize_drawing_sheet_png(reference_scene, width=1000, height=700),
             rasterize_drawing_sheet_png(scene, width=1000, height=700)),
        )
        for before_png, after_png in pairs:
            before, after = rgb_pixels(before_png), rgb_pixels(after_png)
            self.assertGreater(sum(pixel == (176, 176, 176) for pixel in after), 10)
            self.assertTrue(all(new == (23, 33, 43) for old, new in zip(before, after) if old == (23, 33, 43)))
            self.assertTrue(all(new in {old, (176, 176, 176)} for old, new in zip(before, after)))

    def test_sheet_hatch_coordinates_follow_true_contour_bounds_at_scale(self):
        drawing = hatched_drawing()
        scene = build_drawing_sheet(drawing, SHEET_OPTIONS)
        self.assertEqual(scene["views"][0]["model_bounds"], drawing["views"][0]["bounds"])
        self.assertEqual(scene["views"][0]["model_dimensions_mm"], {"width": 10.0, "height": 30.0})
        hatches = [stroke for stroke in scene["strokes"] if stroke["role"] == "hatch"]
        self.assertEqual(len(hatches), 3)
        transform = scene["views"][0]["transform"]
        for stroke in hatches:
            source = drawing["views"][0]["lines"][stroke["source_line_index"]]
            for endpoint in ("start", "end"):
                expected = [transform["paper_origin_mm"][axis] +
                            (source[endpoint][axis] - transform["model_min"][axis]) / 2 for axis in range(2)]
                self.assertEqual(stroke[endpoint], expected)
        svg = ET.fromstring(render_drawing_sheet_svg(scene))
        paths = svg.findall("{http://www.w3.org/2000/svg}path")
        self.assertEqual([path.attrib["data-role"] for path in paths[:3]], ["hatch"] * 3)
        for path, stroke in zip(paths, hatches):
            self.assertEqual(path.attrib["stroke-width"], "0.15")
            self.assertEqual(path.attrib["stroke"], "#b0b0b0")
            self.assertEqual(path.attrib["data-view"], "SECTION")
            self.assertNotIn("stroke-dasharray", path.attrib)
            xy = [float(token) for token in path.attrib["d"].replace("M", "").replace("L", "").split()]
            for actual, expected in zip(xy, [stroke["start"][0], 210 - stroke["start"][1],
                                            stroke["end"][0], 210 - stroke["end"][1]]):
                self.assertAlmostEqual(actual, expected, places=6)
        labels = {row["text"] for row in scene["texts"]}
        self.assertTrue({"10 MM", "30 MM", "SCALE 1:2"}.issubset(labels))
        self.assertIn("NONZERO CONTOUR FILL / NOT NATIVE DRAFT / MODEL MM", labels)

    def test_nonsection_views_and_hidden_hatch_are_rejected_in_all_formats(self):
        for name in ("FRONT", "TOP", "SIDE"):
            drawing = hatched_drawing()
            drawing["views"][0]["name"] = name
            self.assert_rejected(drawing)
        drawing = hatched_drawing()
        drawing["show_hidden_lines"] = True
        drawing["views"][0]["lines"][-1]["visibility"] = "hidden"
        self.assert_rejected(drawing)
        scene = build_drawing_sheet(hatched_drawing(), SHEET_OPTIONS)
        hatch_index = next(index for index, stroke in enumerate(scene["strokes"]) if stroke["role"] == "hatch")
        for view, visibility in (("FRONT", "visible"), (None, "visible"), ("SECTION", "hidden")):
            bad = deepcopy(scene)
            bad["strokes"][hatch_index].update(view_name=view, visibility=visibility)
            for renderer in (validate_drawing_sheet_scene, render_drawing_sheet_svg,
                             rasterize_drawing_sheet_png, export_drawing_sheet_dxf):
                with self.subTest(view=view, visibility=visibility, renderer=renderer.__name__), self.assertRaises(ValueError):
                    renderer(bad)

    def assert_rejected(self, drawing):
        calls = (
            lambda: _make_svg(deepcopy(drawing["views"])),
            lambda: rasterize_orthographic_png(drawing["views"]),
            lambda: export_orthographic_dxf(drawing),
            lambda: build_drawing_sheet(drawing, SHEET_OPTIONS),
        )
        for function in calls:
            with self.assertRaises(ValueError):
                function()

    def test_hatch_cannot_extend_source_bounds_or_disappear_below_export_precision(self):
        drawing = hatched_drawing()
        drawing["views"][0]["lines"][-1]["end"] = [22.0, 52.0]
        for exporter in (lambda value: rasterize_orthographic_png(value["views"]),
                         export_orthographic_dxf, lambda value: build_drawing_sheet(value, SHEET_OPTIONS)):
            with self.assertRaisesRegex(ValueError, "line_outside_bounds"):
                exporter(drawing)
        tiny = section_drawing()
        tiny["views"][0]["lines"].append({"kind": "hatch", "visibility": "visible",
                                         "start": [15, 35], "end": [15 + 1e-9, 35]})
        with self.assertRaisesRegex(ValueError, "section_hatch_output_geometry_below_precision"):
            export_orthographic_dxf(tiny)
        with self.assertRaisesRegex(ValueError, "section_hatch_output_geometry_below_precision"):
            build_drawing_sheet(tiny, SHEET_OPTIONS)

    def test_two_separated_rectangles_cannot_lose_positive_gap_at_vector_precision(self):
        gap = 1e-10
        first = [[0, 0], [10, 0], [10, 10], [0, 10]]
        second = [[10 + gap, 0], [20 + gap, 0], [20 + gap, 10], [10 + gap, 10]]
        drawing = contour_drawing([first, second], [([0, 5], [10, 5]), ([10 + gap, 5], [20 + gap, 5])])
        scene = direct_sheet_scene(drawing)
        operations = (
            lambda: _make_svg(deepcopy(drawing["views"])),
            lambda: export_orthographic_dxf(drawing),
            lambda: build_drawing_sheet(drawing, {"paper_size": "A4", "scale_denominator": 1}),
            lambda: render_drawing_sheet_svg(scene),
            lambda: export_drawing_sheet_dxf(scene),
        )
        for operation in operations:
            with self.assertRaisesRegex(ValueError, "section_hatch_output_contour_topology_changed"):
                operation()

    def test_diagonal_gap_cannot_cross_after_rounding_with_all_endpoints_distinct(self):
        a = [.807255305308022, 1.3088167820157]
        b = [8.426730939630776, 8.533986912782616]
        c = [4.157344266406027, 4.773114123754777]
        d = [12.02763156086629, 12.236116488948761]
        outer = [[0, 0], [100, 0], [100, 100], [0, 100]]

        def drawing_at_scale(factor):
            point = lambda p: [50 + value * 1e-6 / factor for value in p]
            lower = [a, [a[0] + 100, a[1] - 100], [b[0] + 100, b[1] - 100], b]
            upper = [c, d, [d[0] - 100, d[1] + 100], [c[0] - 100, c[1] + 100]]
            return contour_drawing([outer, list(map(point, lower)), list(map(point, upper))], [([10, 10], [20, 10])])

        ordinary = drawing_at_scale(1)
        svg_drawing = drawing_at_scale(3.5)  # The default SVG's 100-mm view is 350 pixels high.
        scene = direct_sheet_scene(ordinary)
        operations = (
            lambda: _make_svg(deepcopy(svg_drawing["views"])),
            lambda: export_orthographic_dxf(ordinary),
            lambda: build_drawing_sheet(ordinary, {"paper_size": "A4", "scale_denominator": 1}),
            lambda: render_drawing_sheet_svg(scene),
            lambda: export_drawing_sheet_dxf(scene),
        )
        for operation in operations:
            with self.assertRaisesRegex(ValueError, "section_hatch_output_contour_topology_changed"):
                operation()

    def test_new_hatch_contacts_and_overlaps_fail_without_contour_collapse(self):
        outer = [[0, 0], [10, 0], [10, 10], [0, 10]]
        for hatches in (
                [([1, 5], [4.99999999, 5]), ([5.00000001, 5], [9, 5])],
                [([1, 5], [9, 5]), ([1, 5.00000001], [9, 5.00000001])]):
            drawing = contour_drawing([outer], hatches)
            with self.assertRaisesRegex(ValueError, "section_hatch_output_fill_topology_changed"):
                validate_section_hatch_precision(drawing["views"][0]["lines"])

    def test_precision_budget_is_bounded_and_contour_subdivisions_remain_allowed(self):
        drawing = hatched_drawing()
        original = drawing["views"][0]["lines"][0]
        drawing["views"][0]["lines"][:1] = [
            {**original, "end": [16, 22]}, {**original, "start": [16, 22]}]
        validate_section_hatch_precision(drawing["views"][0]["lines"])
        with patch("aveva_e3d_mcp.section_hatch_precision.MAX_PRECISION_CHECKS", 1):
            with self.assertRaisesRegex(ValueError, "section_hatch_precision_work_budget_exceeded"):
                validate_section_hatch_precision(drawing["views"][0]["lines"])


@unittest.skipIf(ezdxf is None, "optional ezdxf parser is not installed")
class SectionHatchingDxfInteropTests(unittest.TestCase):
    def parse(self, data):
        document = ezdxf.read(io.StringIO(data))
        audit = document.audit()
        self.assertEqual(audit.errors, [])
        self.assertEqual(audit.fixes, [])
        self.assertEqual(document.units, 4)
        layer = document.layers.get("SECTION_HATCH")
        self.assertEqual(layer.dxf.color, 8)
        self.assertEqual(layer.dxf.linetype, "CONTINUOUS")
        self.assertNotIn("SECTION_HIDDEN", document.layers)
        self.assertNotIn("E3D_HIDDEN", document.linetypes)
        self.assertEqual(len(document.modelspace().query("HATCH")), 0)
        hatches = list(document.modelspace().query('LINE[layer=="SECTION_HATCH"]'))
        self.assertEqual(len(hatches), 3)
        self.assertTrue(all(line.dxf.linetype == "BYLAYER" for line in hatches))
        self.assertEqual(len(document.modelspace().query('LINE[layer=="SECTION_FEATURE"]')), 0)
        return document, hatches

    def test_plain_hatch_is_editable_linework_at_true_model_mm(self):
        document, hatches = self.parse(export_orthographic_dxf(hatched_drawing()))
        self.assertEqual(sorted((line.dxf.end - line.dxf.start).magnitude for line in hatches),
                         sorted([10.0, math.hypot(10.0, 30.0), 10.0]))
        outlines = list(document.modelspace().query('LINE[layer=="SECTION_OUTLINE"]'))
        origin = [min(point[axis] for line in outlines for point in (line.dxf.start, line.dxf.end)) for axis in range(2)]
        for line, source in zip(hatches, hatched_drawing()["views"][0]["lines"][4:]):
            for actual, expected in ((line.dxf.start, source["start"]), (line.dxf.end, source["end"])):
                self.assertEqual(tuple(actual), (origin[0] + expected[0] - 11, origin[1] + expected[1] - 22, 0))

    def test_sheet_hatch_geometry_and_model_dimension_labels_survive_save(self):
        scene = build_drawing_sheet(hatched_drawing(), SHEET_OPTIONS)
        document, hatches = self.parse(export_drawing_sheet_dxf(scene))
        for line, stroke in zip(hatches, (row for row in scene["strokes"] if row["role"] == "hatch")):
            for actual, expected in ((line.dxf.start, stroke["start"]), (line.dxf.end, stroke["end"])):
                for left, right in zip(actual, [*expected, 0]):
                    self.assertAlmostEqual(left, right, places=6)
        labels = {row.dxf.text for row in document.modelspace().query('TEXT[layer=="SECTION_DIMENSION"]')}
        self.assertEqual(labels, {"10 MM", "30 MM"})
        saved = io.StringIO()
        document.write(saved)
        reread, saved_hatches = self.parse(saved.getvalue())
        self.assertEqual(tuple(reread.header["$LIMMAX"]), (297.0, 210.0))
        self.assertEqual([tuple(line.dxf.start) for line in hatches], [tuple(line.dxf.start) for line in saved_hatches])


if __name__ == "__main__":
    unittest.main()

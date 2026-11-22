"""Actual combined parameter-mesh sections through all shared drawing exports.

These checks stay below the live handler: every contour is cut from closed
WORLD/mm BOX meshes, and CAD loops are reconstructed from parsed LINE endpoints
instead of trusting the section's loop count or a renderer's metadata.
"""

from __future__ import annotations

import io
import math
import struct
import sys
import unittest
import xml.etree.ElementTree as ET
import zlib
from collections import Counter
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    import ezdxf
except ImportError:
    ezdxf = None

from aveva_e3d_mcp.drawing_assembly import WORLD_FRAME, combine_drawing_meshes
from aveva_e3d_mcp.drawing_sheet import build_drawing_sheet
from aveva_e3d_mcp.drawing_sheet_render import rasterize_drawing_sheet_png, render_drawing_sheet_svg
from aveva_e3d_mcp.mesh_section import slice_triangle_mesh
from aveva_e3d_mcp.orthographic_dxf import export_drawing_sheet_dxf, export_orthographic_dxf
from aveva_e3d_mcp.orthographic_png import rasterize_orthographic_png
from aveva_e3d_mcp.primitive_mesh import derive_primitive_mesh
from aveva_e3d_mcp.section_hatching import build_section_hatching
from aveva_e3d_mcp.tools import _section_drawing


PLANE = {"origin": [0, 0, 0], "normal": [0, 0, 1]}
SHEET = {"paper_size": "A4", "orientation": "landscape", "scale_denominator": 2,
         "title": "ASSEMBLY SECTION", "drawing_number": "ASM-SEC-001"}
SOURCES = (
    ("=10/101", (0, 0, 0), (10, 20, 30)),
    ("=20/202", (40, 0, 0), (20, 10, 30)),
    ("=30/303", (0, 50, 0), (6, 8, 12)),
)


def source(refno, position, dimensions):
    native = {
        "success": True, "complete": True, "shape": "BOX", "negative": False,
        "negative_children": [], "children": [], "read_errors": {},
        "element": {"refno": refno, "type": "BOX", "name": "/TEST"},
        "dimensions": dict(zip(("XLEN", "YLEN", "ZLEN"), dimensions)),
        "frame": {"world": {"position": list(position), "orientation": {
            "x_axis": [1, 0, 0], "y_axis": [0, 1, 0], "z_axis": [0, 0, 1]}}},
    }
    mesh = derive_primitive_mesh(native, coordinate_space="WORLD")
    if not mesh["success"]:
        raise AssertionError(mesh)
    return {"success": True, "available": True, "complete": True, "coordinate_space": "WORLD",
            "requested_refno": refno, "resolved_refno": refno, "resolved_type": "BOX", "derived_mesh": mesh}


def combined_section(specifications=SOURCES):
    combined = combine_drawing_meshes([source(*specification) for specification in specifications])
    if not combined["success"]:
        raise AssertionError(combined)
    section = slice_triangle_mesh(combined["vertices"], combined["triangles"], plane=PLANE)
    if not section["success"]:
        raise AssertionError(section)
    section.update(coordinate_unit="mm", area_unit="mm2", length_unit="mm",
                   source_coordinate_frame=WORLD_FRAME, source_coordinate_space="WORLD")
    return combined, section


def source_for_segment(segment, combined):
    matches = [row for row in combined["source_ranges"] if all(
        row["triangle_range"][0] <= index < row["triangle_range"][1] for index in segment["source_triangles"])]
    if len(matches) != 1:
        raise AssertionError({"segment": segment, "matches": matches})
    return matches[0]


def png_pixels(png):
    width, height = struct.unpack(">II", png[16:24])
    offset, chunks = 8, []
    while offset < len(png):
        length = int.from_bytes(png[offset:offset + 4], "big")
        if png[offset + 4:offset + 8] == b"IDAT":
            chunks.append(png[offset + 8:offset + 8 + length])
        offset += length + 12
    raw = zlib.decompress(b"".join(chunks))
    row_size = width * 3 + 1
    pixels = bytearray()
    for row in range(height):
        data = raw[row * row_size:(row + 1) * row_size]
        if data[0] != 0:
            raise AssertionError("unexpected scanline filter")
        pixels.extend(data[1:])
    return width, height, pixels


def cad_loops(lines):
    edges = [((line.dxf.start.x, line.dxf.start.y), (line.dxf.end.x, line.dxf.end.y)) for line in lines]
    outgoing, incoming = {}, Counter()
    for a, b in edges:
        if a in outgoing:
            raise AssertionError("coordinate welding or duplicate directed endpoint")
        outgoing[a] = b
        incoming[b] += 1
    if set(outgoing) != set(incoming) or any(count != 1 for count in incoming.values()):
        raise AssertionError("CAD contour does not have directed degree two")
    unseen, result = set(outgoing), []
    for start in outgoing:
        if start not in unseen:
            continue
        current, loop = start, [start]
        while True:
            if current not in unseen:
                raise AssertionError("CAD contour does not close independently")
            unseen.remove(current)
            current = outgoing[current]
            loop.append(current)
            if current == start:
                break
        result.append(loop)
    return result


def loop_metrics(loop, denominator=1):
    area = sum(a[0] * b[1] - a[1] * b[0] for a, b in zip(loop, loop[1:])) / 2
    perimeter = sum(math.dist(a, b) for a, b in zip(loop, loop[1:]))
    return round(area * denominator ** 2, 6), round(perimeter * denominator, 6)


class AssemblySectionExportTests(unittest.TestCase):
    def test_real_combined_contours_keep_global_source_ranges_and_native_mm(self):
        combined, section = combined_section()
        self.assertEqual(combined["counts"], {"sources": 3, "vertices": 24, "triangles": 36})
        self.assertEqual(section["counts"]["loops"], 3)
        self.assertEqual(section["counts"]["segments"], 24)
        self.assertEqual(section["bounds"], {"min": [-5.0, -10.0], "max": [50.0, 54.0]})
        self.assertEqual([row["triangle_range"] for row in combined["source_ranges"]],
                         [[0, 12], [12, 24], [24, 36]])
        drawing = _section_drawing(section, combined)
        view = drawing["views"][0]
        self.assertEqual(view["dimensions"], {"width": 55.0, "height": 64.0, "unit": "mm"})
        source_counts = Counter()
        for line, segment in zip(view["lines"], section["segments"]):
            self.assertEqual(line["source_triangles"], segment["source_triangles"])
            self.assertEqual(line["start"], section["vertices"][segment["vertices"][0]])
            self.assertEqual(line["end"], section["vertices"][segment["vertices"][1]])
            origin = source_for_segment(segment, combined)
            source_counts[origin["source_index"]] += 1
        self.assertEqual(source_counts, {0: 8, 1: 8, 2: 8})
        per_source_metrics = {}
        for loop in section["loops"]:
            indices = {source_for_segment(section["segments"][index], combined)["source_index"]
                       for index in loop["segment_indices"]}
            self.assertEqual(len(indices), 1)
            per_source_metrics[indices.pop()] = (round(loop["signed_area"], 6), round(loop["perimeter"], 6))
        self.assertEqual(per_source_metrics, {0: (200.0, 60.0), 1: (200.0, 60.0), 2: (48.0, 28.0)})
        for flag in ("vertices_welded", "boolean_union_applied", "nested_shell_semantics_inferred"):
            self.assertFalse(combined[flag])
        self.assertFalse(section["hole_semantics_inferred"])

    def test_all_plain_and_sheet_formats_render_all_three_discrete_contours(self):
        combined, section = combined_section()
        drawing = _section_drawing(section, combined)
        svg = ET.fromstring(drawing["svg"])
        self.assertEqual(len(svg.findall(".//{http://www.w3.org/2000/svg}path")), 24)
        self.assertEqual(len(svg.findall('.//{http://www.w3.org/2000/svg}g[@data-view="SECTION"]')), 1)
        self.assertIn("SECTION_OUTLINE", export_orthographic_dxf(drawing))
        png = rasterize_orthographic_png(drawing["views"], width=1200, height=700)
        width, height, pixels = png_pixels(png)
        self.assertEqual((width, height), (1200, 700))
        ink = {index for index in range(width * height) if pixels[index * 3:index * 3 + 3] == bytes((23, 33, 43))
               and index // width >= 48}
        component_boxes = []
        while ink:
            start = ink.pop()
            stack, component = [start], [start]
            while stack:
                current = stack.pop()
                x, y = current % width, current // width
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        neighbor = (y + dy) * width + x + dx
                        if 0 <= x + dx < width and 0 <= y + dy < height and neighbor in ink:
                            ink.remove(neighbor)
                            stack.append(neighbor)
                            component.append(neighbor)
            xs, ys = [value % width for value in component], [value // width for value in component]
            if max(xs) - min(xs) > 10 and max(ys) - min(ys) > 10:
                component_boxes.append((min(xs), min(ys), max(xs), max(ys)))
        self.assertEqual(len(component_boxes), 3, component_boxes)
        scene = build_drawing_sheet(drawing, SHEET)
        self.assertEqual(len([row for row in scene["strokes"] if row["role"] == "silhouette"]), 24)
        sheet_svg = ET.fromstring(render_drawing_sheet_svg(scene))
        self.assertEqual(sheet_svg.attrib["width"], "297mm")
        self.assertEqual(sheet_svg.attrib["height"], "210mm")
        self.assertEqual(len(sheet_svg.findall('.//{http://www.w3.org/2000/svg}path[@data-role="silhouette"]')), 24)
        self.assertTrue(rasterize_drawing_sheet_png(scene, width=1000, height=700).startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn("SECTION_OUTLINE", export_drawing_sheet_dxf(scene))

    def test_disconnected_hatching_has_no_cross_source_gap_bridges(self):
        combined, section = combined_section()
        hatching = build_section_hatching(section, {"spacing_mm": 2, "angle_degrees": 0, "phase_mm": 1})
        self.assertTrue(hatching["success"], hatching)
        self.assertEqual(len(hatching["lines"]), 19)
        owners = Counter()
        for line in hatching["lines"]:
            midpoint = [(line["start"][axis] + line["end"][axis]) / 2 for axis in (0, 1)]
            candidates = []
            for index, (_, center, dimensions) in enumerate(SOURCES):
                if all(center[axis] - dimensions[axis] / 2 <= midpoint[axis] <= center[axis] + dimensions[axis] / 2
                       for axis in (0, 1)):
                    candidates.append(index)
                    self.assertEqual(line["end"][0] - line["start"][0], dimensions[0])
            self.assertEqual(len(candidates), 1)
            owners[candidates[0]] += 1
        self.assertEqual(owners, {0: 10, 1: 5, 2: 4})
        drawing = _section_drawing(section, combined, hatching=hatching)
        svg = ET.fromstring(drawing["svg"])
        self.assertEqual(len(svg.findall('.//{http://www.w3.org/2000/svg}path[@class="hatch"]')), 19)
        self.assertIn("SECTION_HATCH", export_orthographic_dxf(drawing))
        scene = build_drawing_sheet(drawing, SHEET)
        self.assertEqual(len([row for row in scene["strokes"] if row["role"] == "hatch"]), 19)
        self.assertIn("NONZERO CONTOUR FILL", render_drawing_sheet_svg(scene))
        self.assertIn("SECTION_HATCH", export_drawing_sheet_dxf(scene))
        _, _, pixels = png_pixels(rasterize_drawing_sheet_png(scene, width=1000, height=700))
        self.assertGreater(sum(pixels[index:index + 3] == bytes((176, 176, 176))
                               for index in range(0, len(pixels), 3)), 50)

    def test_crossing_or_coincident_source_sections_render_without_hatch_and_reject_fill(self):
        for position in ((5, 5, 0), (0, 0, 0)):
            specifications = (SOURCES[0], ("=20/202", position, (10, 20, 30)))
            combined, section = combined_section(specifications)
            self.assertEqual(section["counts"]["loops"], 2)
            self.assertEqual(section["counts"]["segments"], 16)
            before = deepcopy(section)
            drawing = _section_drawing(section, combined)
            self.assertEqual(len(ET.fromstring(drawing["svg"]).findall(".//{http://www.w3.org/2000/svg}path")), 16)
            self.assertTrue(rasterize_orthographic_png(drawing["views"]).startswith(b"\x89PNG"))
            self.assertIn("SECTION_OUTLINE", export_orthographic_dxf(drawing))
            scene = build_drawing_sheet(drawing, SHEET)
            self.assertTrue(render_drawing_sheet_svg(scene).startswith("<svg"))
            self.assertIn("SECTION_OUTLINE", export_drawing_sheet_dxf(scene))
            hatching = build_section_hatching(section, {"spacing_mm": 2})
            self.assertFalse(hatching["success"], hatching)
            self.assertEqual(hatching["lines"], [])
            self.assertEqual(hatching["error_code"], "section_hatch_crossing_or_touching_contours")
            self.assertEqual(section, before)

    def test_same_winding_nested_sources_are_filled_without_claiming_holes_or_union(self):
        combined, section = combined_section((("=10/101", (0, 0, 0), (40, 40, 30)),
                                              ("=20/202", (0, 0, 0), (10, 10, 30))))
        self.assertEqual(section["counts"]["loops"], 2)
        hatching = build_section_hatching(section, {"spacing_mm": 2, "angle_degrees": 0, "phase_mm": 1})
        self.assertTrue(hatching["success"], hatching)
        center = [line for line in hatching["lines"] if line["start"][1] == 1]
        self.assertEqual([(line["start"], line["end"]) for line in center], [([-20.0, 1.0], [20.0, 1.0])])
        self.assertEqual(hatching["loop_winding"], [1, 1])
        self.assertFalse(hatching["hole_semantics_inferred"])
        self.assertFalse(hatching["boolean_operations_applied"])
        self.assertFalse(combined["boolean_union_applied"])
        drawing = _section_drawing(section, combined, hatching=hatching)
        self.assertIn("SECTION_HATCH", export_orthographic_dxf(drawing))
        self.assertIn("SECTION_HATCH", export_drawing_sheet_dxf(build_drawing_sheet(drawing, SHEET)))


@unittest.skipIf(ezdxf is None, "optional independent ezdxf parser is not installed")
class AssemblySectionCadInteropTests(unittest.TestCase):
    def parse(self, data):
        document = ezdxf.read(io.StringIO(data))
        audit = document.audit()
        self.assertEqual(audit.errors, [])
        self.assertEqual(audit.fixes, [])
        self.assertEqual(document.units, 4)
        return document

    def assert_native_loops(self, document, denominator):
        contours = list(document.modelspace().query('LINE[layer=="SECTION_OUTLINE"]'))
        self.assertEqual(len(contours), 24)
        loops = cad_loops(contours)
        self.assertEqual(len(loops), 3)
        self.assertEqual(sorted(loop_metrics(loop, denominator) for loop in loops),
                         [(48.0, 28.0), (200.0, 60.0), (200.0, 60.0)])
        return contours

    def test_plain_cad_loops_and_global_provenance_agree_with_actual_source_coordinates(self):
        combined, section = combined_section()
        drawing = _section_drawing(section, combined)
        document = self.parse(export_orthographic_dxf(drawing))
        lines = self.assert_native_loops(document, 1)
        # Infer the translation once from the first independently parsed LINE.
        # No source's WORLD origin may be recentered independently in the CAD.
        first = section["vertices"][section["segments"][0]["vertices"][0]]
        offset = [lines[0].dxf.start[axis] - first[axis] for axis in (0, 1)]
        for line, segment in zip(lines, section["segments"]):
            origin = source_for_segment(segment, combined)
            self.assertEqual(origin["refno"], SOURCES[origin["source_index"]][0])
            for actual, index in ((line.dxf.start, segment["vertices"][0]), (line.dxf.end, segment["vertices"][1])):
                self.assertEqual([actual[axis] - offset[axis] for axis in (0, 1)], section["vertices"][index])

    def test_scaled_sheet_cad_preserves_three_native_loops_hatches_and_normal_roundtrip(self):
        combined, section = combined_section()
        hatching = build_section_hatching(section, {"spacing_mm": 2, "angle_degrees": 0, "phase_mm": 1})
        drawing = _section_drawing(section, combined, hatching=hatching)
        scene = build_drawing_sheet(drawing, SHEET)
        document = self.parse(export_drawing_sheet_dxf(scene))
        self.assert_native_loops(document, 2)
        layer = document.layers.get("SECTION_HATCH")
        self.assertEqual(layer.dxf.color, 8)
        self.assertEqual(layer.dxf.linetype, "CONTINUOUS")
        hatches = list(document.modelspace().query('LINE[layer=="SECTION_HATCH"]'))
        self.assertEqual(len(hatches), 19)
        self.assertEqual(Counter(round((line.dxf.end - line.dxf.start).magnitude, 6) for line in hatches),
                         {5.0: 10, 10.0: 5, 3.0: 4})
        transform = scene["views"][0]["transform"]
        for line, original in zip(hatches, hatching["lines"]):
            for actual, key in ((line.dxf.start, "start"), (line.dxf.end, "end")):
                recovered = [(actual[axis] - transform["paper_origin_mm"][axis]) * 2 + transform["model_min"][axis]
                             for axis in (0, 1)]
                for left, right in zip(recovered, original[key]):
                    self.assertAlmostEqual(left, right, places=6)
        saved = io.StringIO()
        document.write(saved)
        reread = self.parse(saved.getvalue())
        self.assert_native_loops(reread, 2)
        self.assertEqual(tuple(reread.header["$LIMMAX"]), (297.0, 210.0))
        self.assertEqual([(tuple(line.dxf.start), tuple(line.dxf.end)) for line in hatches],
                         [(tuple(line.dxf.start), tuple(line.dxf.end))
                          for line in reread.modelspace().query('LINE[layer=="SECTION_HATCH"]')])


if __name__ == "__main__":
    unittest.main()

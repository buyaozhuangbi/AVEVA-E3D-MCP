"""Curve drawings must project bounded samples, retaining native evidence."""

from __future__ import annotations

from copy import deepcopy
import json
import math
import struct
import sys
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.panel_curve_boundary import derive_panel_curve_boundary
from aveva_e3d_mcp.panel_curve_drawing import build_panel_curve_drawing, render_panel_curve_png
from test_panel_curve_boundary import (
    ROOT, WORLD_AXES, WORLD_TRANSLATION, combine_native, install_unset_existence,
    install_world, l_corner_native, rectangle_native,
)


AXES = {"FRONT": (0, 2), "TOP": (0, 1), "SIDE": (1, 2)}
SVG_NS = {"s": "http://www.w3.org/2000/svg"}


def dxf_records(text):
    """Read ASCII group codes independently of the production DXF exporter."""
    lines = text.splitlines()
    if len(lines) % 2:
        raise AssertionError("DXF group-code/value pairs are incomplete")
    pairs = [(int(lines[i].strip()), lines[i+1].strip()) for i in range(0, len(lines), 2)]
    records = []
    current = None
    for code, value in pairs:
        if code == 0:
            current = {"kind": value, "groups": {}}
            records.append(current)
        elif current is not None:
            current["groups"].setdefault(code, []).append(value)
    return pairs, records


def png_chunks(data):
    cursor = 8
    chunks = []
    while cursor < len(data):
        size = struct.unpack(">I", data[cursor:cursor+4])[0]
        kind = data[cursor+4:cursor+8]
        payload = data[cursor+8:cursor+8+size]
        crc = struct.unpack(">I", data[cursor+8+size:cursor+12+size])[0]
        if crc != zlib.crc32(kind+payload) & 0xffffffff:
            raise AssertionError("PNG chunk CRC mismatch")
        chunks.append((kind, payload))
        cursor += size + 12
    if cursor != len(data):
        raise AssertionError("PNG chunks do not consume the complete payload")
    return chunks


class PanelCurveDrawingTests(unittest.TestCase):
    def boundary(self, native=None, **options):
        result = derive_panel_curve_boundary(native if native is not None else rectangle_native(), **options)
        self.assertTrue(result["success"], result)
        return result

    def test_top_view_is_exactly_the_sampled_ring_with_one_closing_edge(self):
        boundary = self.boundary()
        before = deepcopy(boundary)
        drawing = build_panel_curve_drawing(boundary, views=["TOP"])
        view = drawing["views"][0]
        points = boundary["loops"][0]["sampled_points"]
        expected = [(a[:2], b[:2]) for a, b in zip(points, points[1:]+points[:1])]
        self.assertEqual([(line["start"], line["end"]) for line in view["lines"]], expected)
        self.assertEqual(view["line_count"], len(points))
        self.assertEqual(drawing["segment_count"], len(points))
        self.assertGreater(view["line_count"], 8)
        self.assertEqual(view["bounds"], {"min": [0, 0], "max": [100, 60]})
        self.assertEqual(view["dimensions"], {"width": 100, "height": 60, "unit": "mm"})
        self.assertFalse(any(line["start"] == [0, 0] or line["end"] == [0, 0] for line in view["lines"]),
                         "The rounded corner must not be replaced by its PAVE pole.")
        self.assertEqual(boundary, before)

    def test_all_world_views_use_native_frame_and_independent_axis_projections(self):
        boundary = self.boundary(coordinate_space="WORLD")
        drawing = build_panel_curve_drawing(boundary, views=["FRONT", "TOP", "SIDE"])
        self.assertEqual(drawing["coordinate_frame"], boundary["coordinate_frame"])
        self.assertEqual(drawing["coordinate_space"], "WORLD")
        self.assertEqual(drawing["coordinate_unit"], "mm")
        points = boundary["loops"][0]["sampled_points"]
        for view in drawing["views"]:
            first, second = AXES[view["name"]]
            expected = []
            for index, (a, b) in enumerate(zip(points, points[1:]+points[:1])):
                start, end = [a[first], a[second]], [b[first], b[second]]
                if math.dist(start, end) > 1e-8:
                    expected.append((start, end, index))
            self.assertEqual([(l["start"], l["end"], l["sampled_edge_index"]) for l in view["lines"]], expected)
            self.assertEqual(view["bounds"]["min"], [min(p[first] for p in points), min(p[second] for p in points)])
            self.assertEqual(view["bounds"]["max"], [max(p[first] for p in points), max(p[second] for p in points)])
        self.assertEqual(drawing["segment_count"], sum(v["line_count"] for v in drawing["views"]))

    def test_edge_on_view_reports_dropped_zero_length_edges(self):
        boundary = self.boundary(rectangle_native(radius=0))
        drawing = build_panel_curve_drawing(boundary, views=["FRONT"])
        self.assertEqual(drawing["views"][0]["line_count"], 2)
        self.assertEqual(drawing["views"][0]["dropped_projected_edges"], 2)
        self.assertEqual(drawing["views"][0]["dropped_projected_edge_counts"],
                         {"zero_length": 2, "numerical_residue": 0})
        self.assertEqual(drawing["sampled_edge_count"], 4)

    def test_numerical_edge_on_residue_is_reported_and_removed_from_every_export(self):
        axes = ((1, 0, 1e-14), (0, 1, 3e-14), (-1e-14, -3e-14, 1))
        native = install_world(rectangle_native(radius=0), axes, (0, 0, 0))
        boundary = self.boundary(native, coordinate_space="WORLD")
        drawing = build_panel_curve_drawing(boundary, include_svg=True, include_dxf=True)
        self.assertEqual(drawing["projection_degenerate_tolerance_mm"], 1e-8)
        self.assertEqual(drawing["segment_count"], 8)
        for view in drawing["views"]:
            self.assertEqual(view["projection_degenerate_tolerance_mm"], 1e-8)
            drops = 0 if view["name"] == "TOP" else 2
            self.assertEqual(view["line_count"], 4-drops)
            self.assertEqual(view["dropped_projected_edge_counts"],
                             {"zero_length": 0, "numerical_residue": drops})
            self.assertEqual(len(view["dropped_projected_edge_details"]), drops)
            for detail in view["dropped_projected_edge_details"]:
                self.assertEqual(detail["reason"], "numerical_residue")
                self.assertGreater(detail["projected_length_mm"], 0)
                self.assertLessEqual(detail["projected_length_mm"], 1e-8)
                self.assertEqual(detail["loop_index"], 0)
                self.assertIn(detail["sampled_edge_index"], range(4))
        _, records = dxf_records(drawing["dxf"])
        self.assertEqual(sum(record["kind"] == "LINE" for record in records), 8)
        root = ET.fromstring(drawing["svg"])
        self.assertEqual(len(root.findall(".//s:line", SVG_NS)), 8)
        self.assertTrue(render_panel_curve_png(drawing, width=640, height=360).startswith(b"\x89PNG"))

    def test_genuine_submicron_projection_is_retained_and_dxf_fails_explicitly(self):
        angle = 5e-9
        axes = ((1, 0, 0), (0, math.cos(angle), math.sin(angle)),
                (0, -math.sin(angle), math.cos(angle)))
        boundary = self.boundary(install_world(rectangle_native(radius=0), axes, (0, 0, 0)),
                                 coordinate_space="WORLD")
        drawing = build_panel_curve_drawing(boundary, views=["FRONT"])
        view = drawing["views"][0]
        self.assertEqual(view["line_count"], 4)
        self.assertEqual(view["dropped_projected_edges"], 0)
        short = [math.dist(line["start"], line["end"]) for line in view["lines"]
                 if math.dist(line["start"], line["end"]) < 1e-6]
        self.assertEqual(len(short), 2)
        self.assertTrue(all(length > 1e-8 for length in short))
        with self.assertRaisesRegex(ValueError, "precision"):
            build_panel_curve_drawing(boundary, views=["FRONT"], include_dxf=True)

    def test_sampled_world_extents_are_labelled_and_stay_within_the_chord_bound(self):
        drawing = build_panel_curve_drawing(self.boundary(coordinate_space="WORLD"))
        differences = []
        # A rounded rectangle is an 80x40 rectangle Minkowski-summed with an
        # R10 disk. Its support function gives exact extrema for any WORLD
        # axis, independently of the implementation's sample angles.
        inner_corners = ((10, 10), (90, 10), (90, 50), (10, 50))
        for view in drawing["views"]:
            self.assertEqual(view["dimension_label"], "SAMPLED EXTENT")
            self.assertIn("sampled chord approximation", view["bounds_semantics"])
            self.assertIn("analytic extrema", view["bounds_semantics"])
            self.assertEqual(view["bounds_coordinate_error_bound_mm"], drawing["chord_tolerance_mm"])
            self.assertEqual(view["extent_error_bound_mm"], 2*drawing["chord_tolerance_mm"])
            for projected_axis, world_axis in enumerate(AXES[view["name"]]):
                a, b = WORLD_AXES[0][world_axis], WORLD_AXES[1][world_axis]
                projected = [WORLD_TRANSLATION[world_axis]+a*x+b*y for x, y in inner_corners]
                disk_support = 10*math.hypot(a, b)
                exact_min, exact_max = min(projected)-disk_support, max(projected)+disk_support
                low = view["bounds"]["min"][projected_axis]-exact_min
                high = exact_max-view["bounds"]["max"][projected_axis]
                for gap in (low, high):
                    self.assertGreaterEqual(gap, -1e-6)
                    self.assertLessEqual(gap, drawing["chord_tolerance_mm"]+1e-6)
                    differences.append(gap)
        self.assertGreater(max(differences), 1e-5, "An unsampled analytic extremum must not be called an exact extent.")

    def test_multiple_loops_are_closed_separately_without_connectors_or_hole_roles(self):
        native = combine_native(rectangle_native(), rectangle_native(30, 20, 2, origin=(35, 20)))
        boundary = self.boundary(native)
        drawing = build_panel_curve_drawing(boundary, views=["TOP"])
        self.assertEqual(drawing["loop_count"], 2)
        self.assertEqual([loop["role"] for loop in drawing["loops"]], ["not inferred"]*2)
        for line in drawing["views"][0]["lines"]:
            ring = boundary["loops"][line["loop_index"]]["sampled_points"]
            index = line["sampled_edge_index"]
            self.assertEqual(line["start"], ring[index][:2])
            self.assertEqual(line["end"], ring[(index+1) % len(ring)][:2])
        self.assertEqual(drawing["segment_count"], sum(len(loop["sampled_points"]) for loop in boundary["loops"]))
        self.assertFalse(drawing["hole_semantics_inferred"])

    def test_analytic_counts_and_native_false_flags_survive_successful_drawing(self):
        boundary = self.boundary(install_unset_existence(rectangle_native()))
        drawing = build_panel_curve_drawing(boundary, views=["TOP"])
        self.assertTrue(drawing["success"])
        self.assertEqual(drawing["curve_counts"], {key: boundary[key] for key in
                         ("loop_count", "segment_count", "line_count", "arc_count", "sampled_edge_count")})
        self.assertEqual(drawing["arc_count"], 4)
        self.assertEqual(drawing["curve_line_count"], 4)
        self.assertIs(drawing["native_boundary_points_complete"], False)
        self.assertIs(drawing["native_boundary_points_read_complete"], False)
        self.assertIs(drawing["native_existence_verified"], False)
        self.assertEqual(drawing["source_diagnostics"], boundary["source_diagnostics"])
        self.assertEqual(drawing["source"], boundary["source"])
        drawing["source"]["revision"] = "changed-drawing-only"
        self.assertEqual(boundary["source"]["revision"], "oracle-revision")

    def test_drawing_explicitly_states_approximation_and_unresolved_semantics(self):
        drawing = build_panel_curve_drawing(self.boundary(), views=["TOP"])
        self.assertEqual(drawing["approximation"], "bounded-chord polyline")
        self.assertEqual(drawing["chord_tolerance_mm"], 0.1)
        for key in ("native_tessellation", "rendered_boundary", "cuts_applied", "thickness_resolved",
                    "hole_semantics_inferred", "hidden_line_removal", "occlusion_checked", "visibility_classified"):
            self.assertIs(drawing[key], False, key)
        self.assertIn("APPROX", drawing["footer"].upper())
        self.assertIn("0.1", drawing["footer"])
        self.assertNotIn("svg", drawing)
        self.assertNotIn("dxf", drawing)

    def test_svg_contains_all_chords_at_one_scale_across_world_views(self):
        drawing = build_panel_curve_drawing(self.boundary(coordinate_space="WORLD"), include_svg=True)
        root = ET.fromstring(drawing["svg"])
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
        self.assertFalse(root.findall(".//s:path", SVG_NS), "Exported geometry is chord linework, not an exact arc path.")
        self.assertFalse(root.findall(".//s:circle", SVG_NS))
        ratios = []
        for view, group in zip(drawing["views"], root.findall("s:g", SVG_NS)):
            lines = group.findall("s:line", SVG_NS)
            self.assertEqual(len(lines), view["line_count"])
            for model, element in zip(view["lines"], lines):
                pixel_length = math.hypot(float(element.attrib["x2"])-float(element.attrib["x1"]),
                                          float(element.attrib["y2"])-float(element.attrib["y1"]))
                ratios.append(pixel_length/math.dist(model["start"], model["end"]))
        self.assertEqual(len(ratios), drawing["segment_count"])
        self.assertLess(max(ratios)-min(ratios), 1e-5)
        text = " ".join(root.itertext()).upper()
        self.assertIn("APPROX", text)
        self.assertIn("SAMPLED EXTENT", text)
        self.assertIn("0.1", text)

    def test_dxf_is_mm_chord_linework_with_exact_lengths_and_no_arc_entities(self):
        drawing = build_panel_curve_drawing(self.boundary(l_corner_native()), views=["TOP"], include_dxf=True)
        pairs, records = dxf_records(drawing["dxf"])
        self.assertIn((9, "$INSUNITS"), pairs)
        units_index = pairs.index((9, "$INSUNITS"))
        self.assertEqual(pairs[units_index+1], (70, "4"))
        self.assertFalse(any(record["kind"] in {"ARC", "CIRCLE", "SPLINE"} for record in records))
        lines = [record["groups"] for record in records if record["kind"] == "LINE"]
        self.assertEqual(len(lines), drawing["segment_count"])
        lengths = sorted(math.hypot(float(g[11][0])-float(g[10][0]), float(g[21][0])-float(g[20][0])) for g in lines)
        expected = sorted(math.dist(line["start"], line["end"]) for line in drawing["views"][0]["lines"])
        for observed, model in zip(lengths, expected):
            self.assertAlmostEqual(observed, model, delta=2e-6)
        labels = " ".join(value for record in records if record["kind"] == "TEXT"
                          for value in record["groups"].get(1, []))
        self.assertIn("APPROX", labels.upper())
        self.assertIn("0.1", labels)

    def test_png_encodes_nonempty_rgb_preview_of_the_same_drawing(self):
        drawing = build_panel_curve_drawing(self.boundary(), views=["TOP"])
        before = deepcopy(drawing)
        png = render_panel_curve_png(drawing, width=640, height=360)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        chunks = png_chunks(png)
        ihdr = next(payload for kind, payload in chunks if kind == b"IHDR")
        self.assertEqual(struct.unpack(">2I", ihdr[:8]), (640, 360))
        self.assertEqual(ihdr[8:], bytes((8, 2, 0, 0, 0)))
        raw = zlib.decompress(b"".join(payload for kind, payload in chunks if kind == b"IDAT"))
        self.assertEqual(len(raw), 360*(1+640*3))
        self.assertIn(bytes((23, 33, 43)), raw)
        self.assertEqual(chunks[-1][0], b"IEND")
        self.assertEqual(drawing, before)

    def test_invalid_views_export_switches_and_preview_dimensions_are_rejected(self):
        boundary = self.boundary()
        for views in ([], ["TOP", "TOP"], ["top"], ["TOP", "SIDE", "FRONT", "TOP"], "TOP", [None]):
            with self.subTest(views=views), self.assertRaises(ValueError):
                build_panel_curve_drawing(boundary, views=views)
        for name in ("include_svg", "include_dxf"):
            for value in (1, "true", None):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    build_panel_curve_drawing(boundary, **{name: value})
        drawing = build_panel_curve_drawing(boundary, views=["TOP"])
        for width, height in ((63, 360), (640, 2049), (2048, 2048), (True, 360), (640.5, 360)):
            with self.subTest(width=width, height=height), self.assertRaises(ValueError):
                render_panel_curve_png(drawing, width=width, height=height)

    def test_failed_raw_or_malformed_boundary_never_falls_back_to_poles(self):
        for corruption in ("failure", "source", "unit", "not_closed", "duplicate_close", "count", "nan"):
            with self.subTest(corruption=corruption):
                boundary = self.boundary()
                if corruption == "failure":
                    boundary["success"] = False
                elif corruption == "source":
                    boundary["boundary_source"] = "raw PAVE control polygon"
                elif corruption == "unit":
                    boundary["coordinate_unit"] = "m"
                elif corruption == "not_closed":
                    boundary["loops"][0]["closed"] = False
                elif corruption == "duplicate_close":
                    boundary["loops"][0]["sampled_points"].append(boundary["loops"][0]["sampled_points"][0][:])
                elif corruption == "count":
                    boundary["arc_count"] += 1
                else:
                    boundary["loops"][0]["sampled_points"][0][0] = float("nan")
                with self.assertRaises(ValueError):
                    build_panel_curve_drawing(boundary)
        with self.assertRaises(ValueError):
            build_panel_curve_drawing(rectangle_native())

    def test_projection_line_budget_applies_to_all_requested_views_together(self):
        boundary = self.boundary(rectangle_native(10000, 6000, 1000),
                                 coordinate_space="WORLD", chord_tolerance_mm=0.0001)
        self.assertLessEqual(boundary["sampled_edge_count"], 12000)
        self.assertGreater(boundary["sampled_edge_count"]*3, 12000)
        with self.assertRaisesRegex(ValueError, "limit"):
            build_panel_curve_drawing(boundary, views=["FRONT", "TOP", "SIDE"])

    def test_saved_r4_default_three_views_export_without_false_complete_or_precision_fallback(self):
        path = ROOT / "artifacts" / "pave-boundary-points-readback-20261120-r4.json"
        if not path.exists():
            self.skipTest("The saved native r4 receipt is optional.")
        receipt = json.loads(path.read_text(encoding="utf-8-sig"))
        native = deepcopy(receipt["geometry_observations"]["frad20.boundary_WORLD"])
        for loop in native["loops"]:
            for vertex in loop["vertices"]:
                vertex["curve_import_descriptor"] = {"checked": True, "tagged": False,
                    "source": "synthetic test qualification; not native DESP proof"}
        boundary = self.boundary(native)
        drawing = build_panel_curve_drawing(boundary, include_svg=True, include_dxf=True)
        self.assertEqual([view["name"] for view in drawing["views"]], ["FRONT", "TOP", "SIDE"])
        self.assertIs(drawing["native_boundary_points_complete"], False)
        self.assertIs(drawing["native_existence_verified"], False)
        _, records = dxf_records(drawing["dxf"])
        self.assertEqual(sum(record["kind"] == "LINE" for record in records), drawing["segment_count"])
        root = ET.fromstring(drawing["svg"])
        self.assertEqual(len(root.findall(".//s:line", SVG_NS)), drawing["segment_count"])
        self.assertTrue(render_panel_curve_png(drawing, width=640, height=360).startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()

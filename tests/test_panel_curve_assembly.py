"""Independent geometry and provenance checks for paged WORLD curve overlays."""

from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.panel_curve_assembly import compose_panel_curve_pages
from aveva_e3d_mcp.panel_curve_boundary import derive_panel_curve_boundary
from aveva_e3d_mcp.panel_curve_drawing import render_panel_curve_png
from test_panel_curve_boundary import (
    combine_native, install_unset_existence, install_world, rectangle_native,
)
from test_panel_curve_drawing import dxf_records, png_chunks


IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
AXES = {"FRONT": (0, 2), "TOP": (0, 1), "SIDE": (1, 2)}
SVG_NS = {"s": "http://www.w3.org/2000/svg"}
NATIVE_FLAGS = ("native_boundary_points_complete", "native_boundary_points_read_complete",
                "native_existence_verified")


def panel_native(index=0, *, origin=(0.0, 0.0), width=100.0, height=60.0,
                 radius=10.0, nested=False, unknown_existence=False, axes=IDENTITY):
    """Explicit independent rounded rectangles with unique native identities."""
    native = rectangle_native(width, height, radius, origin=origin)
    if nested:
        native = combine_native(native, rectangle_native(30, 20, 2,
            origin=(origin[0] + 35, origin[1] + 20)))
    refno = f"=1/{10 + index}"
    native["element"].update(refno=refno, name=f"/ASSEMBLY-PANEL-{index}")
    native["coordinate_frame"] = refno
    for li, loop in enumerate(native["loops"]):
        loop["element"]["refno"] = f"=2/{index * 100 + li + 1}"
        for vi, vertex in enumerate(loop["vertices"]):
            vertex["refno"] = f"=3/{index * 10000 + li * 100 + vi + 1}"
            if vertex["fillet_radius_mm"]:
                vertex["boundary_points"]["panel_local"]["frame_refno"] = refno
    install_world(native, axes, (0.0, 0.0, 0.0))
    if unknown_existence:
        install_unset_existence(native)
    return native


def panel_boundary(index=0, *, tolerance=0.1, **options):
    boundary = derive_panel_curve_boundary(panel_native(index, **options),
        coordinate_space="WORLD", chord_tolerance_mm=tolerance)
    if not boundary["success"]:
        raise AssertionError(boundary)
    return boundary


def reidentify(boundary, index):
    result = deepcopy(boundary)
    result["source"]["refno"] = f"=10/{index + 1}"
    result["source"]["name"] = f"/BUDGET-{index}"
    return result


class PanelCurveAssemblyTests(unittest.TestCase):
    def test_rounded_sources_keep_all_chords_without_control_polygon_fallback(self):
        boundaries = [panel_boundary(0), panel_boundary(1, origin=(200, 0))]
        before = deepcopy(boundaries)
        result = compose_panel_curve_pages(boundaries, views=["TOP"])
        self.assertTrue(result["success"])
        self.assertTrue(result["available"])
        self.assertTrue(result["complete"])
        self.assertEqual(result["source_count"], 2)
        drawing = result["pages"][0]["drawing"]
        self.assertEqual(drawing["drawing_kind"], "native PANE assembly definition-curve approximation")
        self.assertEqual(drawing["coordinate_space"], "WORLD")
        self.assertEqual(drawing["coordinate_unit"], "mm")
        self.assertEqual(drawing["arc_count"], 8)
        self.assertEqual(drawing["curve_line_count"], 8)
        lines = drawing["views"][0]["lines"]
        self.assertEqual(len(lines), sum(b["sampled_edge_count"] for b in boundaries))
        self.assertGreater(len(lines), 16)
        for line in lines:
            source = boundaries[line["source_index"]]
            ring = source["loops"][line["source_loop_index"]]["sampled_points"]
            edge = line["sampled_edge_index"]
            self.assertEqual(line["start"], ring[edge][:2])
            self.assertEqual(line["end"], ring[(edge + 1) % len(ring)][:2])
        poles = {(x, y) for x in (0, 100, 200, 300) for y in (0, 60)}
        self.assertFalse(any(tuple(line[endpoint]) in poles for line in lines for endpoint in ("start", "end")))
        self.assertEqual(boundaries, before)

    def test_global_bounds_dimensions_and_svg_scale_are_identical_across_pages(self):
        boundaries = [panel_boundary(0), panel_boundary(1, origin=(4000, -600), width=300, height=150),
                      panel_boundary(2, origin=(-700, 1300), width=80, height=40, radius=5)]
        result = compose_panel_curve_pages(boundaries, panels_per_page=1, include_svg=True)
        self.assertEqual(result["page_count"], 3)
        points = [p for boundary in boundaries for loop in boundary["loops"] for p in loop["sampled_points"]]
        scales = []
        for page in result["pages"]:
            drawing = page["drawing"]
            root = ET.fromstring(drawing["svg"])
            scales.append(float(root.attrib["data-shared-scale"]))
            for view in drawing["views"]:
                a, b = AXES[view["name"]]
                expected = {"min": [min(p[a] for p in points), min(p[b] for p in points)],
                            "max": [max(p[a] for p in points), max(p[b] for p in points)]}
                self.assertEqual(view["bounds"], expected)
                self.assertEqual(view["dimensions"], {"width": expected["max"][0] - expected["min"][0],
                    "height": expected["max"][1] - expected["min"][1], "unit": "mm"})
                self.assertIn("SAMPLED EXTENT", view["dimension_label"])
                self.assertEqual(view["bounds_coordinate_error_bound_mm"], 0.1)
                self.assertEqual(view["extent_error_bound_mm"], 0.2)
                local = [p for loop in boundaries[page["source_indices"][0]]["loops"] for p in loop["sampled_points"]]
                self.assertEqual(view["source_bounds"], {
                    "min": [min(p[a] for p in local), min(p[b] for p in local)],
                    "max": [max(p[a] for p in local), max(p[b] for p in local)]})
            self.assertIn("APPROX", " ".join(root.itertext()).upper())
        self.assertEqual(scales, [scales[0]] * 3)
        self.assertGreater(scales[0], 0)

    def test_pagination_keeps_global_source_and_loop_indices_with_local_loop_mapping(self):
        boundaries = [panel_boundary(0, nested=True), panel_boundary(1, origin=(200, 0)),
                      panel_boundary(2, origin=(400, 0), nested=True)]
        result = compose_panel_curve_pages(boundaries, panels_per_page=2, views=["TOP"], include_svg=True)
        self.assertEqual([page["page_number"] for page in result["pages"]], [1, 2])
        self.assertEqual([page["source_range"] for page in result["pages"]], [[0, 2], [2, 3]])
        self.assertEqual([page["source_indices"] for page in result["pages"]], [[0, 1], [2]])
        self.assertEqual([page["source_count"] for page in result["pages"]], [2, 1])
        expected_global = {(0, 0): 0, (0, 1): 1, (1, 0): 2, (2, 0): 3, (2, 1): 4}
        observed = set()
        for page in result["pages"]:
            drawing = page["drawing"]
            for loop in drawing["loops"]:
                source_index, local_index = loop["source_index"], loop["source_loop_index"]
                source_loop = boundaries[source_index]["loops"][local_index]
                self.assertEqual(loop["loop_index"], expected_global[source_index, local_index])
                self.assertEqual(loop["source_refno"], boundaries[source_index]["source"]["refno"])
                self.assertEqual(loop["source_loop_refno"], source_loop["source_refno"])
                self.assertEqual(loop["role"], "not inferred")
                observed.add(loop["loop_index"])
            root = ET.fromstring(drawing["svg"])
            geometry = root.findall(".//s:line", SVG_NS)
            for line, element in zip(drawing["views"][0]["lines"], geometry):
                self.assertEqual(line["loop_index"], expected_global[line["source_index"], line["source_loop_index"]])
                for key in ("source_index", "source_refno", "source_loop_index", "source_loop_refno"):
                    self.assertEqual(element.attrib["data-" + key.replace("_", "-")], str(line[key]))
        self.assertEqual(observed, set(range(5)))
        self.assertEqual([source["source_index"] for source in result["sources"]], [0, 1, 2])

    def test_native_false_unknown_and_diagnostics_remain_independent_of_success(self):
        boundaries = [panel_boundary(0, unknown_existence=True), panel_boundary(1, origin=(200, 0))]
        before = deepcopy(boundaries)
        result = compose_panel_curve_pages(boundaries, panels_per_page=1, views=["TOP"])
        for index, source in enumerate(result["sources"]):
            for key in NATIVE_FLAGS:
                self.assertIs(source[key], boundaries[index][key])
            self.assertEqual(source["source"], boundaries[index]["source"])
            self.assertEqual(source["source_diagnostics"], boundaries[index]["source_diagnostics"])
            self.assertEqual(source["diagnostics"], boundaries[index]["diagnostics"])
        self.assertIs(result["sources"][0]["native_boundary_points_complete"], False)
        self.assertIs(result["sources"][0]["native_existence_verified"], False)
        for page in result["pages"]:
            drawing = page["drawing"]
            self.assertTrue(drawing["success"])
            for flag in ("native_tessellation", "rendered_boundary", "cuts_applied", "thickness_resolved",
                         "hole_semantics_inferred", "hidden_line_removal", "occlusion_checked", "csg_evaluated"):
                self.assertIs(drawing[flag], False, flag)
        result["sources"][0]["source_diagnostics"]["boundary_points_errors"]["new"] = "result only"
        self.assertEqual(boundaries, before)

    def test_touching_crossing_and_coincident_panels_overlay_without_boolean_union(self):
        for origin in ((100, 0), (50, 20), (0, 0)):
            with self.subTest(origin=origin):
                boundaries = [panel_boundary(0, radius=0), panel_boundary(1, radius=0, origin=origin)]
                drawing = compose_panel_curve_pages(boundaries, views=["TOP"])["pages"][0]["drawing"]
                self.assertEqual(drawing["segment_count"], 8)
                self.assertEqual(drawing["loop_count"], 2)
                self.assertEqual({line["source_index"] for line in drawing["views"][0]["lines"]}, {0, 1})
                self.assertFalse(drawing["cuts_applied"])
                self.assertFalse(drawing["csg_evaluated"])

    def test_all_pages_export_same_chords_as_svg_dxf_and_png(self):
        result = compose_panel_curve_pages([panel_boundary(0), panel_boundary(1, origin=(200, 0))],
            panels_per_page=1, views=["TOP"], include_svg=True, include_dxf=True)
        for page in result["pages"]:
            drawing = page["drawing"]
            _, records = dxf_records(drawing["dxf"])
            self.assertEqual(sum(record["kind"] == "LINE" for record in records), drawing["segment_count"])
            self.assertFalse(any(record["kind"] in {"ARC", "CIRCLE", "SPLINE"} for record in records))
            self.assertEqual(len(ET.fromstring(drawing["svg"]).findall(".//s:line", SVG_NS)), drawing["segment_count"])
            preview = render_panel_curve_png(drawing, width=640, height=360)
            self.assertEqual(preview[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(png_chunks(preview)[-1][0], b"IEND")

    def test_dxf_sidecar_ranges_identify_actual_line_entities_in_each_view_and_page(self):
        boundaries = [panel_boundary(0, nested=True), panel_boundary(1, origin=(300, 200), width=180),
                      panel_boundary(2, origin=(-200, 400), width=70, height=40, radius=5)]
        result = compose_panel_curve_pages(boundaries, panels_per_page=2, include_dxf=True)
        for page in result["pages"]:
            drawing = page["drawing"]
            sidecar = drawing["dxf_source_attribution"]
            self.assertFalse(sidecar["embedded"])
            _, records = dxf_records(drawing["dxf"])
            entities = [record["groups"] for record in records if record["kind"] == "LINE"]
            ranges = list(sidecar["ranges"])
            self.assertEqual(len(ranges), len(drawing["views"]) * page["source_count"])
            cursor = 0
            for view in drawing["views"]:
                view_cursor = 0
                for source_index in page["source_indices"]:
                    attribution = ranges.pop(0)
                    expected = [line for line in view["lines"] if line["source_index"] == source_index]
                    self.assertEqual(attribution["view"], view["name"])
                    self.assertEqual(attribution["source_index"], source_index)
                    self.assertEqual(attribution["source_refno"], boundaries[source_index]["source"]["refno"])
                    self.assertEqual(attribution["line_entity_range"], [cursor, cursor + len(expected)])
                    self.assertEqual(attribution["view_line_range"], [view_cursor, view_cursor + len(expected)])
                    for groups, line in zip(entities[cursor:cursor + len(expected)], expected):
                        # Translation into separated DXF views must preserve
                        # each ordered vector and model millimetre length.
                        vector = (float(groups[11][0]) - float(groups[10][0]),
                                  float(groups[21][0]) - float(groups[20][0]))
                        for axis in range(2):
                            self.assertAlmostEqual(vector[axis], line["end"][axis] - line["start"][axis], delta=2e-6)
                    cursor += len(expected)
                    view_cursor += len(expected)
                self.assertEqual(view_cursor, view["line_count"])
            self.assertEqual(cursor, len(entities))
            self.assertEqual(cursor, drawing["segment_count"])

    def test_dropped_projection_residues_retain_global_and_local_source_mapping(self):
        axes = ((1.0, 0.0, 1e-14), (0.0, 1.0, 3e-14), (-1e-14, -3e-14, 1.0))
        boundaries = [panel_boundary(0, radius=0, axes=axes, nested=True),
                      panel_boundary(1, origin=(300, 200), radius=0, axes=axes)]
        result = compose_panel_curve_pages(boundaries, panels_per_page=1, include_svg=True, include_dxf=True)
        loop_offset = 0
        for source_index, page in enumerate(result["pages"]):
            boundary, drawing = boundaries[source_index], page["drawing"]
            observed_drops = 0
            for view in drawing["views"]:
                first, second = AXES[view["name"]]
                expected = {}
                for local_index, loop in enumerate(boundary["loops"]):
                    ring = loop["sampled_points"]
                    for edge_index, (a, b) in enumerate(zip(ring, ring[1:] + ring[:1])):
                        length = math.dist([a[first], a[second]], [b[first], b[second]])
                        if length <= 1e-8:
                            expected[local_index, edge_index] = length
                details = view["dropped_projected_edge_details"]
                self.assertEqual(len(details), len(expected))
                self.assertEqual(view["dropped_projected_edges"], len(expected))
                self.assertEqual(view["line_count"] + len(expected), boundary["sampled_edge_count"])
                for detail in details:
                    local_index = detail["source_loop_index"]
                    key = (local_index, detail["sampled_edge_index"])
                    self.assertEqual(detail["source_index"], source_index)
                    self.assertEqual(detail["source_refno"], boundary["source"]["refno"])
                    self.assertEqual(detail["source_loop_refno"], boundary["loops"][local_index]["source_refno"])
                    self.assertEqual(detail["loop_index"], loop_offset + local_index)
                    self.assertEqual(detail["projected_length_mm"], expected[key])
                    self.assertEqual(detail["reason"], "zero_length" if expected[key] == 0 else "numerical_residue")
                    self.assertFalse(any(line["source_loop_index"] == local_index and
                        line["sampled_edge_index"] == key[1] for line in view["lines"]))
                observed_drops += len(details)
            self.assertGreater(observed_drops, 0)
            self.assertEqual(drawing["sampled_edge_count"], boundary["sampled_edge_count"])
            _, records = dxf_records(drawing["dxf"])
            self.assertEqual(sum(record["kind"] == "LINE" for record in records), drawing["segment_count"])
            self.assertEqual(len(ET.fromstring(drawing["svg"]).findall(".//s:line", SVG_NS)), drawing["segment_count"])
            loop_offset += boundary["loop_count"]

    def test_page_size_accepts_every_integer_one_through_128(self):
        boundaries = [panel_boundary(0, radius=0)]
        for size in (1, 2, 3, 16, 31, 32, 64, 127, 128):
            with self.subTest(size=size):
                self.assertEqual(compose_panel_curve_pages(boundaries, panels_per_page=size)["page_count"], 1)
        for size in (0, 129, -1, True, 1.5, "16", None):
            with self.subTest(size=size), self.assertRaises(ValueError):
                compose_panel_curve_pages(boundaries, panels_per_page=size)

    def test_input_count_world_frame_tolerance_and_numeric_identity_gates(self):
        boundary = panel_boundary()
        for invalid in ([], [reidentify(boundary, i) for i in range(129)], "PANE", None, [None]):
            with self.subTest(kind=type(invalid).__name__), self.assertRaises(ValueError):
                compose_panel_curve_pages(invalid)
        for fault in ("local", "unit", "frame", "empty_frame", "tolerance", "nonfinite_tolerance", "reference",
                      "alias_duplicate", "failure", "raw_polygon"):
            with self.subTest(fault=fault):
                other = panel_boundary(1, origin=(200, 0))
                if fault == "local": other["coordinate_space"] = "panel_local"
                elif fault == "unit": other["coordinate_unit"] = "m"
                elif fault == "frame": other["coordinate_frame"] = "=2/0"
                elif fault == "empty_frame": other["coordinate_frame"] = ""
                elif fault == "tolerance": other["chord_tolerance_mm"] = 0.2
                elif fault == "nonfinite_tolerance": other["chord_tolerance_mm"] = float("nan")
                elif fault == "reference": other["source"]["refno"] = "/UNRESOLVED"
                elif fault == "alias_duplicate": other["source"]["refno"] = "=01/010"
                elif fault == "failure": other["success"] = False
                else: other["boundary_source"] = "raw PAVE control polygon"
                with self.assertRaises(ValueError):
                    compose_panel_curve_pages([boundary, other])

    def test_invalid_views_and_export_switches_are_refused_before_partial_pages(self):
        boundary = panel_boundary()
        for views in ([], ["TOP", "TOP"], ["top"], "TOP", [None]):
            with self.subTest(views=views), self.assertRaises(ValueError):
                compose_panel_curve_pages([boundary], views=views)
        for flag in ("include_svg", "include_dxf"):
            for value in (1, "true", None):
                with self.subTest(flag=flag, value=value), self.assertRaises(ValueError):
                    compose_panel_curve_pages([boundary], **{flag: value})

    def test_analytic_segment_budget_is_global_even_when_every_source_has_its_own_page(self):
        boundary = panel_boundary(radius=0)
        loop = deepcopy(boundary["loops"][0])
        boundary["loops"] = []
        for index in range(32):
            item = deepcopy(loop)
            item["loop_index"] = index
            item["source_refno"] = f"=20/{index + 1}"
            boundary["loops"].append(item)
        for key in ("loop_count", "segment_count", "line_count", "arc_count", "sampled_edge_count"):
            boundary[key] *= 32
        self.assertEqual(boundary["segment_count"], 128)
        with self.assertRaisesRegex(ValueError, "assembly_curve_segment_limit_exceeded"):
            compose_panel_curve_pages([reidentify(boundary, i) for i in range(33)],
                panels_per_page=1, views=["TOP"])

    def test_sampled_edge_budget_is_global_across_small_pages(self):
        boundary = panel_boundary(width=10000, height=6000, radius=1000, tolerance=0.0001)
        self.assertLessEqual(boundary["sampled_edge_count"], 12000)
        self.assertGreater(2 * boundary["sampled_edge_count"], 12000)
        with self.assertRaisesRegex(ValueError, "assembly_curve_sample_limit_exceeded"):
            compose_panel_curve_pages([reidentify(boundary, 0), reidentify(boundary, 1)],
                panels_per_page=1, views=["TOP"])

    def test_projected_line_budget_covers_all_pages_and_all_requested_views(self):
        from test_panel_curve_boundary import WORLD_AXES
        boundary = panel_boundary(width=10000, height=6000, radius=1000,
                                  tolerance=0.0005, axes=WORLD_AXES)
        self.assertLess(2 * boundary["sampled_edge_count"], 12000)
        self.assertGreater(6 * boundary["sampled_edge_count"], 12000)
        with self.assertRaisesRegex(ValueError, "assembly_curve_projected_line_limit_exceeded"):
            compose_panel_curve_pages([reidentify(boundary, 0), reidentify(boundary, 1)], panels_per_page=1)


if __name__ == "__main__":
    unittest.main()

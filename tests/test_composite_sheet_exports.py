"""Four-view projection/section sheet placement, annotations and CAD exports."""
from __future__ import annotations

from copy import deepcopy
import io
import itertools
import math
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    import ezdxf
except ImportError:
    ezdxf = None

from aveva_e3d_mcp.drawing_sheet import build_drawing_sheet
from aveva_e3d_mcp.drawing_sheet_render import render_drawing_sheet_svg, rasterize_drawing_sheet_png
from aveva_e3d_mcp.orthographic_dxf import export_drawing_sheet_dxf
from tests.test_projection_sheet_exports import physical_drawing, project, text_box


def composite_drawing(*, hatch=True, dimensions=False):
    drawing = physical_drawing(hidden=True)
    drawing["sheet_composition"] = "orthographic_section"
    plane = {"origin": [100, 200, 300], "right": [.6, .8, 0], "up": [0, 0, 1], "normal": [.8, -.6, 0]}
    drawing["section_plane"] = plane
    points = [[-50, -40], [150, -40], [150, 80], [-50, 80]]
    lines = [{"kind": "silhouette", "start": list(point), "end": list(points[(index + 1) % 4])}
             for index, point in enumerate(points)]
    if hatch:
        lines += [{"kind": "hatch", "start": [-50, y], "end": [150, y]} for y in (-20, 0, 20, 40, 60)]
    section = {"name": "SECTION", "coordinate_unit": "mm", "coordinate_frame": "section plane coordinates; millimetres",
               "origin": list(plane["origin"]), "horizontal_axis": list(plane["right"]), "vertical_axis": list(plane["up"]),
               "toward_viewer": list(plane["normal"]), "bounds": {"min": [-50, -40], "max": [150, 80]},
               "dimensions": {"width": 200, "height": 120, "unit": "mm"}, "lines": lines}
    if dimensions:
        section["linear_dimensions"] = [
            {"first_vertex": 0, "second_vertex": 1, "start": [-50, -40], "end": [150, -40],
             "axis": "horizontal", "side": "top", "value_mm": 200},
            {"first_vertex": 1, "second_vertex": 2, "start": [150, -40], "end": [150, 80],
             "axis": "vertical", "side": "right", "value_mm": 120},
        ]
    drawing["views"].append(section)
    drawing["hatching_applied"] = hatch
    return drawing


class CompositeSheetExportTests(unittest.TestCase):
    def test_four_cells_share_one_scale_and_only_orthographic_datums_are_aligned(self):
        for method in ("first_angle", "third_angle"):
            drawing = composite_drawing()
            before = deepcopy(drawing)
            scene = build_drawing_sheet(drawing, {"projection_method": method, "scale_denominator": 10})
            self.assertEqual(drawing, before)
            self.assertEqual([view["name"] for view in scene["views"]], ["FRONT", "TOP", "SIDE", "SECTION"])
            views = {view["name"]: view for view in scene["views"]}
            self.assertEqual({view["transform"]["scale_denominator"] for view in scene["views"]}, {10})
            self.assertEqual(project(views["FRONT"], [555, 50])[0], project(views["TOP"], [555, -200])[0])
            self.assertEqual(project(views["FRONT"], [100, 333])[1], project(views["SIDE"], [-200, 333])[1])
            self.assertTrue(views["SECTION"]["independent_section_plane"])
            self.assertEqual(views["SECTION"]["section_plane"], drawing["section_plane"])
            self.assertEqual(scene["section_plane"], drawing["section_plane"])
            self.assertIn("not a retained cut solid", scene["section_scope"])
            front, top, side, section = [views[name]["cell_bounds_mm"] for name in ("FRONT", "TOP", "SIDE", "SECTION")]
            if method == "third_angle":
                self.assertGreater(section["min"][0], top["max"][0])
                self.assertGreater(section["min"][1], side["max"][1])
            else:
                self.assertLess(section["max"][0], top["min"][0])
                self.assertLess(section["max"][1], side["min"][1])
            for view, raw in zip(scene["views"], drawing["views"]):
                geometry = [stroke for stroke in scene["strokes"][slice(*view["stroke_range"])] if "source_line_index" in stroke]
                self.assertEqual([stroke["source_line_index"] for stroke in geometry], list(range(len(raw["lines"]))))
                for stroke, source in zip(geometry, raw["lines"]):
                    self.assertEqual(stroke["start"], project(view, source["start"]))
                    self.assertEqual(stroke["end"], project(view, source["end"]))

    def test_section_local_dimensions_preserve_vertices_values_ranges_and_lanes(self):
        drawing = composite_drawing(dimensions=True)
        before = deepcopy(drawing)
        for method in ("first_angle", "third_angle"):
            scene = build_drawing_sheet(drawing, {"projection_method": method, "scale_denominator": 10})
            self.assertEqual(drawing, before)
            section = next(view for view in scene["views"] if view["name"] == "SECTION")
            self.assertEqual(len(scene["linear_dimensions"]), 2)
            for item, original in zip(scene["linear_dimensions"], drawing["views"][-1]["linear_dimensions"]):
                for key in ("first_vertex", "second_vertex", "start", "end", "axis", "side", "value_mm"):
                    self.assertEqual(item[key], original[key])
                self.assertEqual(item["paper_start_mm"], project(section, original["start"]))
                self.assertEqual(item["paper_end_mm"], project(section, original["end"]))
                self.assertEqual(item["lane_offset_mm"], 7)
                self.assertEqual(item["lane_index"], 0)
                self.assertAlmostEqual(math.dist(item["dimension_line_start_mm"], item["dimension_line_end_mm"]), original["value_mm"] / 10)
                strokes = scene["strokes"][slice(*item["stroke_range"])]
                labels = scene["texts"][slice(*item["text_range"])]
                self.assertEqual(len(strokes), 7)
                self.assertEqual(len(labels), 1)
                self.assertTrue(all(stroke["view_name"] == "SECTION" and stroke["dimension_index"] == item["dimension_index"] for stroke in strokes))
                self.assertEqual(labels[0]["text"], f"{original['value_mm']:g} MM")
                self.assertEqual(labels[0]["height"], 2.2)

    def test_short_vertical_section_dimensions_fit_inside_left_and_right_cells(self):
        drawing = composite_drawing(dimensions=False)
        section = drawing["views"][-1]
        section["linear_dimensions"] = [
            {"first_vertex": 0, "second_vertex": 1, "start": [150, -40], "end": [150, 0],
             "axis": "vertical", "side": "right", "value_mm": 40},
            {"first_vertex": 2, "second_vertex": 3, "start": [-50, -40], "end": [-50, -20],
             "axis": "vertical", "side": "left", "value_mm": 20},
        ]
        scene = build_drawing_sheet(drawing, {"projection_method": "third_angle", "paper_size": "A3"})
        section_cell = next(view["cell_bounds_mm"] for view in scene["views"] if view["name"] == "SECTION")
        for item in scene["texts"]:
            if item.get("role") == "dimension" and item.get("view_name") == "SECTION":
                box = text_box(item)
                self.assertGreaterEqual(box[0], section_cell["min"][0] + 1 - 1e-8)
                self.assertLessEqual(box[2], section_cell["max"][0] - 1 + 1e-8)
                self.assertEqual(item["height"], 2.2)

    def test_all_papers_and_orientations_keep_annotations_inside_their_own_cell(self):
        for method, paper, orientation in itertools.product(("first_angle", "third_angle"), ("A4", "A3"), ("portrait", "landscape")):
            scene = build_drawing_sheet(composite_drawing(dimensions=True),
                {"projection_method": method, "paper_size": paper, "orientation": orientation})
            for view in scene["views"]:
                cell = view["cell_bounds_mm"]
                for stroke in scene["strokes"][slice(*view["stroke_range"])]:
                    for point in (stroke["start"], stroke["end"]):
                        self.assertTrue(all(cell["min"][i] + 1 - 1e-8 <= point[i] <= cell["max"][i] - 1 + 1e-8 for i in (0, 1)))
                for text in scene["texts"][slice(*view["text_range"])]:
                    box = text_box(text)
                    self.assertGreaterEqual(box[0], cell["min"][0] + 1 - 1e-8)
                    self.assertGreaterEqual(box[1], cell["min"][1] + 1 - 1e-8)
                    self.assertLessEqual(box[2], cell["max"][0] - 1 + 1e-8)
                    self.assertLessEqual(box[3], cell["max"][1] - 1 + 1e-8)

    def test_hatch_precision_scope_excludes_all_other_projection_contours(self):
        scene = build_drawing_sheet(composite_drawing(), {"projection_method": "third_angle"})
        from aveva_e3d_mcp.section_hatch_precision import validate_section_hatch_precision
        observations = []
        def independent_scope(lines, **options):
            rows = list(lines)
            observations.append(rows)
            self.assertTrue(rows)
            self.assertTrue(all(row["view_name"] == "SECTION" for row in rows))
            return validate_section_hatch_precision(rows, **options)
        with patch("aveva_e3d_mcp.drawing_sheet_render.validate_section_hatch_precision", side_effect=independent_scope):
            render_drawing_sheet_svg(scene)
        with patch("aveva_e3d_mcp.orthographic_dxf.validate_section_hatch_precision", side_effect=independent_scope):
            export_drawing_sheet_dxf(scene)
        self.assertEqual(len(observations), 2)
        self.assertTrue(any(row["kind"] == "hatch" for row in observations[0]))

    def test_svg_png_and_dxf_keep_all_geometry_section_hatch_and_plane_identification(self):
        scene = build_drawing_sheet(composite_drawing(dimensions=True), {"projection_method": "first_angle"})
        svg = ET.fromstring(render_drawing_sheet_svg(scene))
        namespace = {"s": "http://www.w3.org/2000/svg"}
        paths, texts = svg.findall("s:path", namespace), svg.findall("s:text", namespace)
        self.assertEqual(len(paths), len(scene["strokes"]))
        self.assertEqual(len(texts), len(scene["texts"]))
        self.assertEqual(len([path for path in paths if path.attrib["data-role"] == "hatch"]), 5)
        self.assertTrue(all(path.attrib["data-view"] == "SECTION" for path in paths if path.attrib["data-role"] == "hatch"))
        self.assertTrue(any("SECTION WORLD O" in (text.text or "") for text in texts))
        self.assertTrue(any("FULL MODEL VIEWS + INDEPENDENT SECTION" in (text.text or "") for text in texts))
        for path, stroke in zip(paths, sorted(scene["strokes"], key=lambda item: item["role"] != "hatch")):
            data = path.attrib["d"].split()
            expected = [stroke["start"][0], scene["paper_size_mm"][1] - stroke["start"][1], stroke["end"][0], scene["paper_size_mm"][1] - stroke["end"][1]]
            for actual, value in zip([float(data[i]) for i in (1, 2, 4, 5)], expected):
                self.assertAlmostEqual(actual, value, places=6)
        png = rasterize_drawing_sheet_png(scene, width=1400, height=990)
        self.assertEqual(struct.unpack(">II", png[16:24]), (1400, 990))
        dxf = export_drawing_sheet_dxf(scene)
        self.assertIn("SECTION_HATCH", dxf)
        for name in ("FRONT", "TOP", "SIDE", "SECTION"):
            self.assertIn(name + "_DIMENSION", dxf)

    def test_section_hatch_gap_or_vector_collapse_fails_before_returning_scene(self):
        drawing = composite_drawing()
        drawing["views"][-1]["lines"][-1] = {"kind": "hatch", "start": [-50, 60], "end": [150.00002, 60]}
        with self.assertRaisesRegex(ValueError, "(line_outside_bounds|precision|fill)"):
            build_drawing_sheet(drawing, {"projection_method": "third_angle"})
        drawing = composite_drawing(hatch=False)
        drawing["views"][-1]["lines"].append({"kind": "feature", "start": [0, 0], "end": [1e-10, 0]})
        with self.assertRaisesRegex(ValueError, "precision"):
            build_drawing_sheet(drawing, {"projection_method": "third_angle"})

    def test_invalid_plane_views_or_missing_method_reject_without_mutation(self):
        for mutation in (
            lambda drawing: drawing.pop("section_plane"),
            lambda drawing: drawing["views"].pop(),
            lambda drawing: drawing["section_plane"].update(normal=[0, 0, 0]),
            lambda drawing: drawing["views"][-1].update(origin=[0, 0, 0]),
            lambda drawing: drawing["views"][-1].update(coordinate_frame=drawing["coordinate_frame"]),
            lambda drawing: drawing["views"][0].update(toward_viewer=[0, 1, 0]),
        ):
            drawing = composite_drawing()
            mutation(drawing)
            before = deepcopy(drawing)
            with self.assertRaises(ValueError):
                build_drawing_sheet(drawing, {"projection_method": "third_angle"})
            self.assertEqual(drawing, before)
        with self.assertRaisesRegex(ValueError, "composite_sheet_projection_method_required"):
            build_drawing_sheet(composite_drawing(), {})
        with self.assertRaisesRegex(ValueError, "scale_does_not_fit"):
            build_drawing_sheet(composite_drawing(), {"projection_method": "third_angle", "scale_denominator": .1})

    def test_four_views_without_explicit_composition_still_fail_legacy_validation(self):
        drawing = composite_drawing()
        drawing.pop("sheet_composition")
        with self.assertRaisesRegex(ValueError, "invalid_views"):
            build_drawing_sheet(drawing, {})


@unittest.skipIf(ezdxf is None, "optional independent ezdxf parser is not installed")
class CompositeSheetCadInteropTests(unittest.TestCase):
    def test_normal_cad_read_and_roundtrip_preserve_four_view_entities_and_labels(self):
        for method in ("first_angle", "third_angle"):
            scene = build_drawing_sheet(composite_drawing(dimensions=True), {"projection_method": method})
            document = ezdxf.read(io.StringIO(export_drawing_sheet_dxf(scene)))
            auditor = document.audit()
            self.assertEqual((auditor.errors, auditor.fixes), ([], []))
            self.assertEqual(document.units, 4)
            self.assertEqual(tuple(document.header["$LIMMAX"]), tuple(scene["paper_size_mm"]))
            entities = list(document.modelspace())
            self.assertEqual(len(entities), len(scene["strokes"]) + len(scene["texts"]))
            for entity, stroke in zip(entities, scene["strokes"]):
                self.assertEqual(entity.dxftype(), "LINE")
                for field in ("start", "end"):
                    for actual, expected in zip(getattr(entity.dxf, field), [*stroke[field], 0]):
                        self.assertAlmostEqual(actual, expected, places=6)
            labels = entities[len(scene["strokes"]):]
            self.assertEqual([entity.dxf.text for entity in labels], [text["text"] for text in scene["texts"]])
            self.assertEqual(len(list(document.modelspace().query('LINE[layer=="SECTION_HATCH"]'))), 5)
            buffer = io.StringIO()
            document.write(buffer)
            reread = ezdxf.read(io.StringIO(buffer.getvalue()))
            audit = reread.audit()
            self.assertEqual((audit.errors, audit.fixes), ([], []))
            for actual, original in zip(reread.modelspace(), entities):
                self.assertEqual((actual.dxftype(), actual.dxf.layer), (original.dxftype(), original.dxf.layer))
                if actual.dxftype() == "LINE":
                    self.assertEqual(tuple(actual.dxf.start), tuple(original.dxf.start))
                    self.assertEqual(tuple(actual.dxf.end), tuple(original.dxf.end))
                else:
                    self.assertEqual(actual.dxf.text, original.dxf.text)
                    self.assertEqual(tuple(actual.dxf.align_point), tuple(original.dxf.align_point))
                    self.assertEqual(actual.dxf.height, original.dxf.height)
                    self.assertEqual(actual.dxf.rotation, original.dxf.rotation)


if __name__ == "__main__":
    unittest.main()

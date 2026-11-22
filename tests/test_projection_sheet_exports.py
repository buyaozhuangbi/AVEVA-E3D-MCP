"""Physical alignment and common SVG/PNG/DXF scenes for opt-in projection sheets."""
from __future__ import annotations

from copy import deepcopy
import io
import itertools
import json
import math
from pathlib import Path
import struct
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    import ezdxf
except ImportError:
    ezdxf = None

from aveva_e3d_mcp.drawing_sheet import build_drawing_sheet, normalize_sheet_options
from aveva_e3d_mcp.drawing_sheet_render import render_drawing_sheet_svg, rasterize_drawing_sheet_png
from aveva_e3d_mcp.orthographic_dxf import export_drawing_sheet_dxf


BASES = {
    "FRONT": ([1, 0, 0], [0, 0, 1], [0, -1, 0]),
    "TOP": ([1, 0, 0], [0, 1, 0], [0, 0, 1]),
    "SIDE": ([0, 1, 0], [0, 0, 1], [1, 0, 0]),
}


def physical_drawing(names=("FRONT", "TOP", "SIDE"), lengths=(800, 300, 500), hidden=False):
    ranges = [(100, 100 + lengths[0]), (-200, -200 + lengths[1]), (50, 50 + lengths[2])]
    result = {"success": True, "available": True, "complete": True, "coordinate_unit": "mm",
              "coordinate_frame": "WORLD coordinates; millimetres", "projection_convention": "right_handed",
              "show_hidden_lines": hidden, "views": []}
    axes = {"FRONT": (0, 2), "TOP": (0, 1), "SIDE": (1, 2)}
    for name in names:
        first, second = axes[name]
        (x0, x1), (y0, y1) = ranges[first], ranges[second]
        corners = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
        lines = [{"start": corners[index], "end": corners[(index + 1) % 4], "kind": "silhouette"}
                 for index in range(4) if corners[index] != corners[(index + 1) % 4]]
        if x1 != x0 and y1 != y0:
            lines.append({"start": [x0, y0], "end": [x0 + (x1 - x0) * .25, y0 + (y1 - y0) * .7], "kind": "feature"})
        if hidden and y1 != y0:
            lines.append({"start": [x0, (y0 + y1) / 2], "end": [x1, (y0 + y1) / 2],
                          "kind": "feature", "visibility": "hidden"})
        result["views"].append({"name": name, "bounds": {"min": [x0, y0], "max": [x1, y1]},
            "dimensions": {"width": x1 - x0, "height": y1 - y0, "unit": "mm"},
            "coordinate_frame": result["coordinate_frame"], "coordinate_unit": "mm", "lines": lines,
            **dict(zip(("horizontal_axis", "vertical_axis", "toward_viewer"), deepcopy(BASES[name])))})
    return result


def project(view, point):
    transform = view["transform"]
    return [transform["paper_origin_mm"][i] + (point[i] - transform["model_min"][i]) /
            transform["scale_denominator"] for i in (0, 1)]


def text_box(item):
    width = max(0, 6 * len(item["text"]) - 1) * item["height"] / 7
    dx, dy = (item["height"], width) if item["rotation"] == 90 else (width, item["height"])
    x, y = item["position"]
    return [x - dx / 2, y - dy / 2, x + dx / 2, y + dy / 2]


class ProjectionSheetExportTests(unittest.TestCase):
    def test_projection_option_is_explicit_and_normalization_is_idempotent(self):
        self.assertNotIn("projection_method", normalize_sheet_options({}))
        for method in ("first_angle", "third_angle"):
            raw = {"projection_method": method}
            normalized = normalize_sheet_options(raw)
            self.assertEqual(normalized["projection_method"], method)
            self.assertEqual(normalize_sheet_options(normalized), normalized)
            self.assertEqual(raw, {"projection_method": method})
        for value in (None, False, 1, [], {}, "FIRST_ANGLE", "isometric"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "invalid_sheet_projection_method"):
                normalize_sheet_options({"projection_method": value})

    def test_both_arrangements_preserve_physical_axes_input_order_and_source_indexes(self):
        for method, names in itertools.product(("first_angle", "third_angle"), itertools.permutations(BASES)):
            drawing = physical_drawing(names, hidden=True)
            before = deepcopy(drawing)
            options = {"projection_method": method, "title": "Physical projection"}
            scene = build_drawing_sheet(drawing, options)
            self.assertEqual(drawing, before)
            self.assertEqual(options, {"projection_method": method, "title": "Physical projection"})
            self.assertEqual([view["name"] for view in scene["views"]], list(names))
            views = {view["name"]: view for view in scene["views"]}
            front, top, side = [views[name]["cell_bounds_mm"] for name in ("FRONT", "TOP", "SIDE")]
            if method == "third_angle":
                self.assertGreater(top["min"][1], front["max"][1])
                self.assertGreater(side["min"][0], front["max"][0])
            else:
                self.assertLess(top["max"][1], front["min"][1])
                self.assertLess(side["max"][0], front["min"][0])
            for view, source in zip(scene["views"], drawing["views"]):
                for field, expected in zip(("horizontal_axis", "vertical_axis", "toward_viewer"), BASES[view["name"]]):
                    self.assertEqual(view[field], expected)
                geometry = [line for line in scene["strokes"][slice(*view["stroke_range"])] if "source_line_index" in line]
                self.assertEqual([line["source_line_index"] for line in geometry], list(range(len(source["lines"]))))
                for line, original in zip(geometry, source["lines"]):
                    self.assertEqual(line["start"], project(view, original["start"]))
                    self.assertEqual(line["end"], project(view, original["end"]))
                    self.assertEqual(line["visibility"], original.get("visibility", "visible"))
            self.assertFalse(scene["native_draft_sheet"])
            self.assertFalse(scene["engineering_dimensions_certified"])
            self.assertIn("no complete ISO certification", scene["projection_standard_scope"])

    def test_shared_model_coordinates_have_exact_common_paper_datums_and_one_scale(self):
        for method in ("first_angle", "third_angle"):
            scene = build_drawing_sheet(physical_drawing(), {"projection_method": method, "scale_denominator": 10})
            views = {view["name"]: view for view in scene["views"]}
            self.assertEqual({view["transform"]["scale_denominator"] for view in scene["views"]}, {10})
            for x in (100, 555, 900):
                self.assertEqual(project(views["FRONT"], [x, 50])[0], project(views["TOP"], [x, -200])[0])
            for z in (50, 333, 550):
                self.assertEqual(project(views["FRONT"], [100, z])[1], project(views["SIDE"], [-200, z])[1])
            for view in scene["views"]:
                for axis, extent in enumerate(view["model_dimensions_mm"].values()):
                    self.assertAlmostEqual(view["paper_bounds_mm"]["max"][axis] - view["paper_bounds_mm"]["min"][axis], extent / 10)

    def test_all_paper_orientations_keep_entire_view_annotation_inside_its_cell(self):
        for method, paper, orientation in itertools.product(("first_angle", "third_angle"), ("A3", "A4"), ("landscape", "portrait")):
            scene = build_drawing_sheet(physical_drawing(), {"projection_method": method, "paper_size": paper, "orientation": orientation})
            for view in scene["views"]:
                cell = view["cell_bounds_mm"]
                self.assertGreaterEqual(cell["min"][0], 10)
                self.assertGreaterEqual(cell["min"][1], 50)
                for item in scene["strokes"][slice(*view["stroke_range"])]:
                    for point in (item["start"], item["end"]):
                        for axis in (0, 1):
                            self.assertGreaterEqual(point[axis], cell["min"][axis] + 1 - 1e-8)
                            self.assertLessEqual(point[axis], cell["max"][axis] - 1 + 1e-8)
                for item in scene["texts"][slice(*view["text_range"])]:
                    box = text_box(item)
                    self.assertGreaterEqual(box[0], cell["min"][0] + 1 - 1e-8)
                    self.assertGreaterEqual(box[1], cell["min"][1] + 1 - 1e-8)
                    self.assertLessEqual(box[2], cell["max"][0] - 1 + 1e-8)
                    self.assertLessEqual(box[3], cell["max"][1] - 1 + 1e-8)
            self.assertTrue(json.dumps(scene, allow_nan=False))

    def test_overall_dimensions_keep_true_model_values_and_visible_short_arrows(self):
        drawing = physical_drawing(lengths=(40, 20, 30))
        scene = build_drawing_sheet(drawing, {"projection_method": "third_angle", "scale_denominator": 10})
        for view in scene["views"]:
            dimensions = [item for item in scene["texts"][slice(*view["text_range"])] if item["role"] == "dimension"]
            self.assertEqual(len(dimensions), 2)
            self.assertEqual([item["dimension_value_mm"] for item in dimensions], list(view["model_dimensions_mm"].values()))
            self.assertTrue(all(item["arrow_direction"] == "outward" and item["height"] == 2.2 for item in dimensions))
            self.assertEqual([item["text"] for item in dimensions], [f"{value:g} MM" for value in view["model_dimensions_mm"].values()])
            arrows = [line for line in scene["strokes"][slice(*view["stroke_range"])] if line.get("dimension_part") == "arrow"]
            self.assertEqual(len(arrows), 8)
            self.assertTrue(all(math.dist(line["start"], line["end"]) > 2 for line in arrows))

    def test_svg_png_and_dxf_use_same_complete_paper_scene(self):
        for method in ("first_angle", "third_angle"):
            scene = build_drawing_sheet(physical_drawing(hidden=True), {"projection_method": method, "paper_size": "A4"})
            svg = ET.fromstring(render_drawing_sheet_svg(scene))
            namespace = {"s": "http://www.w3.org/2000/svg"}
            paths, texts = svg.findall("s:path", namespace), svg.findall("s:text", namespace)
            self.assertEqual(len(paths), len(scene["strokes"]))
            self.assertEqual([item.text for item in texts], [item["text"] for item in scene["texts"]])
            height = scene["paper_size_mm"][1]
            for node, line in zip(paths, scene["strokes"]):
                values = node.attrib["d"].split()
                for actual, expected in zip([float(values[index]) for index in (1, 2, 4, 5)],
                        [line["start"][0], height - line["start"][1], line["end"][0], height - line["end"][1]]):
                    self.assertAlmostEqual(actual, expected, places=6)
            png = rasterize_drawing_sheet_png(scene, width=1000, height=700)
            self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", png[16:24]), (1000, 700))
            dxf = export_drawing_sheet_dxf(scene)
            for name in BASES:
                self.assertIn(name + "_DIMENSION", dxf)
                self.assertIn(name + "_HIDDEN", dxf)
            self.assertIn(method.replace("_", " ").upper(), dxf)

    def test_invalid_physical_views_and_section_dimensions_fail_without_mutating_input(self):
        base = physical_drawing()
        cases = []
        for mutate in (
            lambda drawing: drawing.pop("projection_convention"),
            lambda drawing: drawing["views"].pop(),
            lambda drawing: drawing["views"][0].update(name="SECTION"),
            lambda drawing: drawing["views"][0].update(name=[]),
            lambda drawing: drawing["views"][0].update(toward_viewer=[0, 1, 0]),
            lambda drawing: drawing["views"][0].update(horizontal_axis=[True, 0, 0]),
            lambda drawing: drawing["views"][0].update(linear_dimensions=[]),
            lambda drawing: drawing["views"][0].update(coordinate_frame="different frame"),
        ):
            drawing = deepcopy(base)
            mutate(drawing)
            cases.append(drawing)
        for drawing in cases:
            before = deepcopy(drawing)
            with self.subTest(drawing=drawing), self.assertRaises(ValueError):
                build_drawing_sheet(drawing, {"projection_method": "first_angle"})
            self.assertEqual(drawing, before)

    def test_unshared_bounds_nonfitting_scales_degeneracy_and_precision_fail_closed(self):
        drawing = physical_drawing()
        top = drawing["views"][1]
        for point in top["bounds"].values():
            point[0] += 1
        for line in top["lines"]:
            # Some rectangle endpoints share list objects, so replace each point.
            for field in ("start", "end"):
                line[field] = [line[field][0] + 1, line[field][1]]
        with self.assertRaisesRegex(ValueError, "projection_sheet_shared_axis_bounds_mismatch"):
            build_drawing_sheet(drawing, {"projection_method": "third_angle"})
        with self.assertRaisesRegex(ValueError, "projection_sheet_scale_does_not_fit"):
            build_drawing_sheet(physical_drawing(), {"projection_method": "third_angle", "scale_denominator": .1})
        with self.assertRaisesRegex(ValueError, "projection_sheet_degenerate_view"):
            build_drawing_sheet(physical_drawing(lengths=(0, 30, 50)), {"projection_method": "first_angle"})
        with self.assertRaisesRegex(ValueError, "projection_sheet_.*precision"):
            build_drawing_sheet(physical_drawing(), {"projection_method": "first_angle", "scale_denominator": 1e12})

    def test_default_has_no_new_scene_fields_and_ignores_unrequested_projection_metadata(self):
        drawing = physical_drawing()
        old = build_drawing_sheet(drawing, {})
        drawing.pop("projection_convention")
        for view in drawing["views"]:
            for key in ("horizontal_axis", "vertical_axis", "toward_viewer"):
                view.pop(key)
        self.assertEqual(old, build_drawing_sheet(drawing, {}))
        self.assertNotIn("projection_method", old)
        self.assertNotIn("projection_method", old["options"])
        self.assertTrue(all("cell_bounds_mm" not in view for view in old["views"]))

    def test_close_shared_bounds_use_common_canonical_datum_without_dropping_source_lines(self):
        drawing = physical_drawing()
        top = drawing["views"][1]
        for point in top["bounds"].values():
            point[0] += 1e-10
        top["dimensions"]["width"] = top["bounds"]["max"][0] - top["bounds"]["min"][0]
        for line in top["lines"]:
            for field in ("start", "end"):
                line[field] = [line[field][0] + 1e-10, line[field][1]]
        scene = build_drawing_sheet(drawing, {"projection_method": "third_angle"})
        self.assertTrue(scene["canonical_axis_bounds_model_mm"]["X"]["canonicalized"])
        views = {view["name"]: view for view in scene["views"]}
        self.assertEqual(project(views["FRONT"], [555, 50])[0], project(views["TOP"], [555, -200])[0])
        geometry = [line for line in scene["strokes"][slice(*views["TOP"]["stroke_range"])] if "source_line_index" in line]
        self.assertEqual(len(geometry), len(top["lines"]))
        self.assertEqual(geometry[0]["start"], project(views["TOP"], top["lines"][0]["start"]))

    def test_unrepresentable_label_boxes_fail_instead_of_clipping_or_shrinking(self):
        drawing = physical_drawing(lengths=(.001, .002, .003))
        before = deepcopy(drawing)
        with self.assertRaisesRegex(ValueError, "projection_sheet_(label_outside_cell|annotation_does_not_fit|label_collision)"):
            build_drawing_sheet(drawing, {"projection_method": "third_angle", "paper_size": "A4"})
        self.assertEqual(drawing, before)


@unittest.skipIf(ezdxf is None, "optional independent ezdxf parser is not installed")
class ProjectionSheetCadInteropTests(unittest.TestCase):
    def test_normal_cad_parse_and_roundtrip_preserve_every_entity_in_both_arrangements(self):
        for method in ("first_angle", "third_angle"):
            scene = build_drawing_sheet(physical_drawing(hidden=True), {"projection_method": method})
            document = ezdxf.read(io.StringIO(export_drawing_sheet_dxf(scene)))
            auditor = document.audit()
            self.assertEqual((auditor.errors, auditor.fixes), ([], []))
            self.assertEqual(document.units, 4)
            self.assertEqual(tuple(document.header["$LIMMAX"]), tuple(scene["paper_size_mm"]))
            entities = list(document.modelspace())
            self.assertEqual(len(entities), len(scene["strokes"]) + len(scene["texts"]))
            for actual, expected in zip(entities, scene["strokes"]):
                self.assertEqual(actual.dxftype(), "LINE")
                for field in ("start", "end"):
                    for a, b in zip(getattr(actual.dxf, field), [*expected[field], 0]):
                        self.assertAlmostEqual(a, b, places=6)
            labels = entities[len(scene["strokes"]):]
            self.assertEqual([text.dxf.text for text in labels], [text["text"] for text in scene["texts"]])
            for actual, expected in zip(labels, scene["texts"]):
                self.assertEqual((actual.dxf.halign, actual.dxf.valign), (1, 2))
                self.assertEqual(actual.dxf.rotation, expected["rotation"])
                self.assertAlmostEqual(actual.dxf.height, expected["height"], places=6)
            stream = io.StringIO()
            document.write(stream)
            reread = ezdxf.read(io.StringIO(stream.getvalue()))
            second_audit = reread.audit()
            self.assertEqual((second_audit.errors, second_audit.fixes), ([], []))
            self.assertEqual([(item.dxftype(), item.dxf.layer) for item in reread.modelspace()],
                             [(item.dxftype(), item.dxf.layer) for item in entities])
            for actual, original in zip(reread.modelspace(), entities):
                if actual.dxftype() == "LINE":
                    self.assertEqual(tuple(actual.dxf.start), tuple(original.dxf.start))
                    self.assertEqual(tuple(actual.dxf.end), tuple(original.dxf.end))
                else:
                    self.assertEqual(tuple(actual.dxf.insert), tuple(original.dxf.insert))
                    self.assertEqual(tuple(actual.dxf.align_point), tuple(original.dxf.align_point))
                    self.assertEqual(actual.dxf.text, original.dxf.text)


if __name__ == "__main__":
    unittest.main()

"""Independent datum and final-coordinate precision audits for projection sheets."""
from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.drawing_sheet import build_drawing_sheet
from aveva_e3d_mcp.drawing_sheet_render import render_drawing_sheet_svg
from tests.test_projection_sheet_exports import physical_drawing


def translated_drawing():
    drawing = physical_drawing()
    offsets = {
        "FRONT": (-999999000.0, -999999000.0),
        "TOP": (-999999000.0, 999999000.0),
        "SIDE": (999999000.0, -999999000.0),
    }
    for view in drawing["views"]:
        offset = offsets[view["name"]]
        for key in ("min", "max"):
            view["bounds"][key] = [a + b for a, b in zip(view["bounds"][key], offset)]
        for line in view["lines"]:
            for key in ("start", "end"):
                line[key] = [a + b for a, b in zip(line[key], offset)]
    return drawing


class ProjectionSheetIndependentAuditTests(unittest.TestCase):
    def test_actual_feature_vertices_share_datums_after_large_signed_translation_and_canonicalization(self):
        drawing = translated_drawing()
        by_name = {view["name"]: view for view in drawing["views"]}
        # Each change is inside the explicitly admitted four-ULP bound
        # tolerance, and extends rather than clips the original linework.
        for name, axis, steps in (("TOP", 0, 1), ("SIDE", 0, 1), ("SIDE", 1, 2)):
            view = by_name[name]
            for key, direction in (("min", -math.inf), ("max", math.inf)):
                for _ in range(steps):
                    view["bounds"][key][axis] = math.nextafter(view["bounds"][key][axis], direction)
            view["dimensions"] = {
                "width": view["bounds"]["max"][0] - view["bounds"]["min"][0],
                "height": view["bounds"]["max"][1] - view["bounds"]["min"][1], "unit": "mm",
            }
        x, y, z = -999998666.75, 999998974.625, -999998833.375
        vertices = {"FRONT": ([x, z], [x + 20, z + 30]),
                    "TOP": ([x, y], [x + 20, y + 40]),
                    "SIDE": ([y, z], [y + 40, z + 30])}
        for name, (start, end) in vertices.items():
            by_name[name]["lines"].append({"start": start, "end": end, "kind": "feature"})
        before = deepcopy(drawing)
        for method in ("first_angle", "third_angle"):
            with self.subTest(method=method):
                scene = build_drawing_sheet(drawing, {"projection_method": method, "scale_denominator": 10})
                self.assertEqual(drawing, before)
                self.assertTrue(all(row["canonicalized"] for row in scene["canonical_axis_bounds_model_mm"].values()))
                actual = {}
                for view in scene["views"]:
                    source = by_name[view["name"]]
                    lines = [line for line in scene["strokes"][slice(*view["stroke_range"])]
                             if "source_line_index" in line]
                    self.assertEqual(len(lines), len(source["lines"]))
                    actual[view["name"]] = lines[-1]["start"]
                    self.assertEqual(view["model_bounds"], source["bounds"])
                    labels = [item for item in scene["texts"][slice(*view["text_range"])]
                              if item["role"] == "dimension"]
                    self.assertEqual([item["dimension_value_mm"] for item in labels],
                                     [source["dimensions"]["width"], source["dimensions"]["height"]])
                # Check actual generated line vertices, not a separately
                # reconstructed transform or metadata alone.
                self.assertEqual(actual["FRONT"][0], actual["TOP"][0])
                self.assertEqual(actual["FRONT"][1], actual["SIDE"][1])

    def test_final_svg_y_reflection_cannot_silently_collapse_an_admitted_feature(self):
        drawing = physical_drawing()
        drawing["views"][0]["lines"].append({
            "kind": "feature", "start": [500, 50.00000500000006],
            "end": [500, 50.000005000000066],
        })
        # These model vertices produced distinct .6f bottom-left paper
        # coordinates but identical final SVG coordinates after Y reflection.
        # Reject at scene construction or final serialization, never emit a
        # successful drawing containing a zero-length replacement path.
        with self.assertRaisesRegex(ValueError, "precision"):
            scene = build_drawing_sheet(drawing, {"projection_method": "third_angle", "scale_denominator": 10})
            render_drawing_sheet_svg(scene)

    def test_small_representable_feature_survives_final_svg_coordinate_conversion(self):
        drawing = physical_drawing()
        drawing["views"][0]["lines"].append({
            "kind": "feature", "start": [500, 50.000005], "end": [500, 50.001005],
        })
        scene = build_drawing_sheet(drawing, {"projection_method": "third_angle", "scale_denominator": 10})
        svg = ET.fromstring(render_drawing_sheet_svg(scene))
        paths = [node for node in svg if node.attrib.get("data-view") == "FRONT"
                 and node.attrib.get("data-role") == "feature"]
        self.assertEqual(len(paths), 2)
        values = paths[-1].attrib["d"].split()
        start = [float(values[index]) for index in (1, 2)]
        end = [float(values[index]) for index in (4, 5)]
        self.assertNotEqual(start, end)
        # Independent rounding of two final endpoints permits at most one
        # micrometre of length error in this vertical paper segment.
        self.assertAlmostEqual(math.dist(start, end), .0001, delta=1.01e-6)


if __name__ == "__main__":
    unittest.main()

"""Independent composite-sheet datum and section-frame adversarial checks."""
from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.drawing_sheet import build_drawing_sheet
from tests.test_composite_sheet_exports import composite_drawing
def translate_world(drawing, delta):
    axes = {"FRONT": (0, 2), "TOP": (0, 1), "SIDE": (1, 2)}
    for view in drawing["views"]:
        if view["name"] == "SECTION":
            continue
        offset = [delta[axis] for axis in axes[view["name"]]]
        for key in ("min", "max"):
            view["bounds"][key] = [a + b for a, b in zip(view["bounds"][key], offset)]
        view["dimensions"] = {
            "width": view["bounds"]["max"][0] - view["bounds"]["min"][0],
            "height": view["bounds"]["max"][1] - view["bounds"]["min"][1], "unit": "mm",
        }
        for line in view["lines"]:
            for key in ("start", "end"):
                line[key] = [a + b for a, b in zip(line[key], offset)]
    plane = drawing["section_plane"]
    plane["origin"] = [a + b for a, b in zip(plane["origin"], delta)]
    section = next(view for view in drawing["views"] if view["name"] == "SECTION")
    section["origin"] = plane["origin"][:]


class CompositeSheetPrecisionAuditTests(unittest.TestCase):
    def test_real_scene_feature_vertices_keep_exact_shared_datums_at_large_signed_world_offsets(self):
        drawing = composite_drawing(hatch=False)
        delta = [-999_999_000.0, 999_999_000.0, -999_999_000.0]
        translate_world(drawing, delta)
        by_name = {view["name"]: view for view in drawing["views"]}
        x, y, z = -999_998_345.625, 999_998_975.25, -999_998_777.875
        endpoints = {
            "FRONT": ([x, z], [x + 11.25, z + 17.5]),
            "TOP": ([x, y], [x + 11.25, y + 31.125]),
            "SIDE": ([y, z], [y + 31.125, z + 17.5]),
        }
        for name, (start, end) in endpoints.items():
            by_name[name]["lines"].append({"kind": "feature", "start": start, "end": end})
        before = deepcopy(drawing)
        for method in ("first_angle", "third_angle"):
            with self.subTest(method=method):
                scene = build_drawing_sheet(drawing, {"projection_method": method})
                self.assertEqual(drawing, before)
                placed = {view["name"]: view for view in scene["views"]}
                actual = {}
                for name in endpoints:
                    row = placed[name]
                    geometry = [stroke for stroke in scene["strokes"][slice(*row["stroke_range"])]
                                if stroke.get("source_line_index") == len(by_name[name]["lines"]) - 1]
                    self.assertEqual(len(geometry), 1)
                    actual[name] = geometry[0]
                self.assertEqual(actual["FRONT"]["start"][0], actual["TOP"]["start"][0])
                self.assertEqual(actual["FRONT"]["end"][0], actual["TOP"]["end"][0])
                self.assertEqual(actual["FRONT"]["start"][1], actual["SIDE"]["start"][1])
                self.assertEqual(actual["FRONT"]["end"][1], actual["SIDE"]["end"][1])
                self.assertEqual(len({row["transform"]["scale_denominator"] for row in placed.values()}), 1)

    def test_section_nonorthogonal_and_left_handed_frames_reject_even_when_raw_axes_match_plane(self):
        mutations = (
            ("nonunit", lambda plane: plane.update(right=[1.2, 1.6, 0])),
            ("left_handed", lambda plane: plane.update(normal=[-0.8, 0.6, 0])),
            ("nonorthogonal", lambda plane: plane.update(up=[0.1, 0, 1])),
        )
        for name, mutate in mutations:
            drawing = composite_drawing(hatch=False)
            plane = drawing["section_plane"]
            mutate(plane)
            section = next(view for view in drawing["views"] if view["name"] == "SECTION")
            section.update(origin=plane["origin"][:], horizontal_axis=plane["right"][:],
                           vertical_axis=plane["up"][:], toward_viewer=plane["normal"][:])
            before = deepcopy(drawing)
            with self.subTest(frame=name), self.assertRaisesRegex(ValueError, "frame_required"):
                build_drawing_sheet(drawing, {"projection_method": "third_angle"})
            self.assertEqual(drawing, before)

    def test_signed_zero_and_near_plane_datum_differences_do_not_reorder_views_or_dimensions(self):
        drawing = composite_drawing(dimensions=True)
        section = next(view for view in drawing["views"] if view["name"] == "SECTION")
        # Each change is a representable near-equal end point, inside the
        # explicit shared-axis tolerance and retained by its canonical union.
        top = next(view for view in drawing["views"] if view["name"] == "TOP")
        top["bounds"]["min"][0] = math.nextafter(top["bounds"]["min"][0], -math.inf)
        top["bounds"]["max"][0] = math.nextafter(top["bounds"]["max"][0], math.inf)
        top["dimensions"]["width"] = top["bounds"]["max"][0] - top["bounds"]["min"][0]
        before = deepcopy(drawing)
        scene = build_drawing_sheet(drawing, {"projection_method": "third_angle", "scale_denominator": 10})
        self.assertEqual(drawing, before)
        self.assertTrue(scene["canonical_axis_bounds_model_mm"]["X"]["canonicalized"])
        section_result = next(row for row in scene["views"] if row["name"] == "SECTION")
        self.assertEqual(section_result["model_bounds"]["min"], section["bounds"]["min"])
        self.assertEqual(len(scene["linear_dimensions"]), 2)
        self.assertTrue(all(math.isfinite(value) for stroke in scene["strokes"]
                            for key in ("start", "end") for value in stroke[key]))


if __name__ == "__main__":
    unittest.main()

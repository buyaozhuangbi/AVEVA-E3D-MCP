"""Independent equipment-frame angle oracles and actual paper export checks."""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import struct
import sys
import unittest
import xml.etree.ElementTree as ET
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.drawing_sheet_render import (
    rasterize_drawing_sheet_png,
    render_drawing_sheet_svg,
    validate_drawing_sheet_scene,
)
from aveva_e3d_mcp.nozzle_axis_sheet import build_nozzle_axis_sheets
from aveva_e3d_mcp.nozzle_orientation_drawing import build_nozzle_orientation_drawing
from aveva_e3d_mcp.nozzle_orientation_sheet import build_nozzle_orientation_sheets
from aveva_e3d_mcp.orthographic_dxf import export_drawing_sheet_dxf


def equipment_pose(*, position=None, axes=None):
    return {"success": True, "available": True, "complete": True,
            "requested_refno": "/V-100", "resolved_refno": "=1/100", "element_name": "/V-100",
            "coordinate_frame": "WORLD", "coordinate_unit": "mm",
            "position": list(position or [1000, 2000, 3000]),
            "orientation": deepcopy(axes or {"x_axis": [1, 0, 0], "y_axis": [0, 1, 0], "z_axis": [0, 0, 1]}),
            "native_frame": {"requested": "WORLD", "type": "WORL", "refno": "=1/0"}}


def world_vector(local, pose):
    axes = pose["orientation"]
    return [math.fsum(local[index] * axes[axis][component] for index, axis in enumerate(("x_axis", "y_axis", "z_axis")))
            for component in range(3)]


def nozzle_drawing(points=None, directions=None, *, pose=None, world=False):
    pose = pose or equipment_pose()
    points = points or [[100, 0, 10], [0, 100, -10]]
    directions = directions or [[0, 1, 0]] * len(points)
    records = []
    for index, (point, direction) in enumerate(zip(points, directions)):
        position = list(point) if world else [origin + delta for origin, delta in zip(pose["position"], world_vector(point, pose))]
        direction = list(direction) if world else world_vector(direction, pose)
        ref = f"/V-100/N{index + 1}"
        records.append({"refno": ref, "native": {"ok": True, "version": "E3D21", "result": {
            "success": True, "complete": True,
            "element": {"refno": f"=1/{index + 200}", "name": ref, "type": "NOZZ"},
            "frame": deepcopy(pose["native_frame"]),
            "points": [{"number": 1, "complete": True,
                        "position": {"available": True, "unit": "mm", **dict(zip(("x", "y", "z"), position))},
                        "direction": {"available": True, **dict(zip(("x", "y", "z"), direction))}}]}}})
    drawing, png = build_nozzle_orientation_drawing(records, width=320, height=240)
    if not drawing["complete"] or png is None:
        raise AssertionError("Fixture must contain complete native-contract WORLD geometry")
    return drawing


def many_points(count):
    return [[700 * math.cos(index * math.tau / count), 700 * math.sin(index * math.tau / count), (index % 5 - 2) * 140]
            for index in range(count)]


def dxf_entities(document):
    lines = document.splitlines()
    records, current, active = [], None, False
    for code, value in zip(lines[0::2], lines[1::2]):
        if code == "2" and value == "ENTITIES":
            active = True
        elif active and code == "0":
            if current is not None:
                records.append(current)
            if value == "ENDSEC":
                break
            current = {"kind": value}
        elif active and current is not None:
            current.setdefault(code, value)
    return records


def png_pixels(png):
    width, height = struct.unpack(">II", png[16:24])
    offset, compressed = 8, bytearray()
    while offset < len(png):
        size = struct.unpack(">I", png[offset:offset + 4])[0]
        if png[offset + 4:offset + 8] == b"IDAT":
            compressed.extend(png[offset + 8:offset + 8 + size])
        offset += size + 12
    data = zlib.decompress(compressed)
    stride = width * 3 + 1
    if any(data[row * stride] != 0 for row in range(height)):
        raise AssertionError("Fixture renderer should use unfiltered RGB scanlines")
    return width, height, lambda x, y: tuple(data[y * stride + 1 + x * 3:y * stride + 1 + x * 3 + 3])


class NozzleAxisSheetTests(unittest.TestCase):
    def build(self, drawing=None, pose=None, options=None, **axes):
        return build_nozzle_axis_sheets(drawing or nozzle_drawing(), pose or equipment_pose(), options or {},
                                        axial_axis=axes.get("axial_axis", "Z"), zero_axis=axes.get("zero_axis", "X"))

    def test_position_angles_and_pdir_angles_are_independent_with_signed_axial_coordinates(self):
        points = [[100, 0, 5], [0, 100, -20], [-100, 0, 30], [0, -100, -40], [0, 0, 50]]
        directions = [[0, 1, 0], [-1, 0, 1], [0, -1, 0], [1, 0, 0], [0, 0, -1]]
        drawing = nozzle_drawing(points, directions)
        scene, = self.build(drawing)
        rows = scene["source_rows"]
        self.assertEqual([row["position_angle_deg"] for row in rows], [0, 90, 180, 270, None])
        self.assertEqual([row["direction_azimuth_deg"] for row in rows], [90, 180, 270, 0, None])
        self.assertEqual([row["axial_mm"] for row in rows], [5, -20, 30, -40, 50])
        self.assertEqual([row["radius_mm"] for row in rows], [100, 100, 100, 100, 0])
        for row, tilt in zip(rows, [0, 45, 0, 0, -90]):
            self.assertAlmostEqual(row["direction_tilt_deg"], tilt)
        self.assertEqual(rows[0]["bearing_deg"], 0)  # WORLD PDIR bearing stays distinct from both new angle fields.
        self.assertEqual(rows[-1]["table_values"]["position_angle_deg"], "NA")
        self.assertEqual(rows[-1]["table_values"]["direction_azimuth_deg"], "NA")
        self.assertFalse(rows[-1]["position_angle_available"])
        self.assertFalse(rows[-1]["direction_azimuth_available"])
        self.assertEqual(scene["table"]["columns"], ["ID", "REF", "PPOINT", "R MM", "ANG DEG", "AXIAL MM", "DIR DEG", "TILT DEG"])
        for row, source in zip(rows, drawing["nozzles"]):
            for field in ("position", "direction", "refno", "resolved_refno", "point_number", "bearing_deg", "tilt_deg"):
                self.assertEqual(row[field], source[field])

    def test_rotated_equipment_frame_oracle_uses_local_positive_y_and_returns_180_degrees(self):
        pose = equipment_pose(position=[1000, 500, 100],
                              axes={"x_axis": [-1, 0, 0], "y_axis": [0, -1, 0], "z_axis": [0, 0, 1]})
        pose.update(requested_refno="/VESSEL-101", resolved_refno="=1/101", element_name="/VESSEL-101")
        offsets = [[0, radius, axial] for radius, height in ((508, 800), (4340, 700), (4570, 700)) for axial in (height, -height)]
        points = [[pose["position"][axis] + delta[axis] for axis in range(3)] for delta in offsets]
        directions = [[0, 0, 1 if delta[2] > 0 else -1] for delta in offsets]
        drawing = nozzle_drawing(points, directions, pose=pose, world=True)
        scene, = self.build(drawing, pose, axial_axis="Z", zero_axis="Y")
        self.assertEqual(scene["equipment_frame"]["basis_world"], {"u": [0, -1, 0], "v": [1, 0, 0], "n": [0, 0, 1]})
        self.assertEqual([row["position_angle_deg"] for row in scene["source_rows"]], [180] * 6)
        self.assertEqual([row["radius_mm"] for row in scene["source_rows"]], [508, 508, 4340, 4340, 4570, 4570])
        self.assertEqual([row["axial_mm"] for row in scene["source_rows"]], [800, -800, 700, -700, 700, -700])
        self.assertEqual([row["direction_azimuth_deg"] for row in scene["source_rows"]], [None] * 6)
        self.assertEqual([row["direction_tilt_deg"] for row in scene["source_rows"]], [90, -90] * 3)

    def test_all_six_axis_pairs_follow_right_hand_cross_product_under_rotated_world_pose(self):
        pose = equipment_pose(axes={"x_axis": [0, 1, 0], "y_axis": [0, 0, 1], "z_axis": [1, 0, 0]})
        drawing = nozzle_drawing([[10, 20, 30]], [[3, 4, 12]], pose=pose)
        components = {"X": (10, 3), "Y": (20, 4), "Z": (30, 12)}
        expected = {("Z", "X"): ("Y", 1), ("Z", "Y"): ("X", -1),
                    ("X", "Y"): ("Z", 1), ("X", "Z"): ("Y", -1),
                    ("Y", "X"): ("Z", -1), ("Y", "Z"): ("X", 1)}
        for (axial, zero), (remaining, sign) in expected.items():
            with self.subTest(axial=axial, zero=zero):
                scene, = self.build(drawing, pose, axial_axis=axial, zero_axis=zero)
                row, = scene["source_rows"]
                u, v, h = components[zero][0], sign * components[remaining][0], components[axial][0]
                self.assertEqual(row["equipment_coordinates_mm"], {"u": u, "v": v, "h": h})
                self.assertAlmostEqual(row["position_angle_deg"], math.degrees(math.atan2(v, u)) % 360)
                self.assertAlmostEqual(row["radius_mm"], math.hypot(u, v))
                du, dv, dh = components[zero][1], sign * components[remaining][1], components[axial][1]
                self.assertAlmostEqual(row["direction_azimuth_deg"], math.degrees(math.atan2(dv, du)) % 360)
                self.assertAlmostEqual(row["direction_tilt_deg"], math.degrees(math.atan2(dh, math.hypot(du, dv))))
                self.assertEqual(scene["equipment_frame"]["v_axis_sign"], sign)

    def test_rigid_world_rotation_and_translation_preserve_local_point_and_direction_results(self):
        a, b = .43, -.71
        axes = {"x_axis": [math.cos(a) * math.cos(b), math.sin(a) * math.cos(b), -math.sin(b)],
                "y_axis": [-math.sin(a), math.cos(a), 0],
                "z_axis": [math.cos(a) * math.sin(b), math.sin(a) * math.sin(b), math.cos(b)]}
        rotated = equipment_pose(position=[1000, 500, 100], axes=axes)
        points, directions = [[250, -140, 80], [-500, 250, -300]], [[3, 4, 12], [-2, 7, -5]]
        original, = self.build(nozzle_drawing(points, directions))
        transformed, = self.build(nozzle_drawing(points, directions, pose=rotated), rotated)
        for before, after in zip(original["source_rows"], transformed["source_rows"]):
            for field in ("radius_mm", "position_angle_deg", "axial_mm", "direction_azimuth_deg", "direction_tilt_deg"):
                self.assertAlmostEqual(before[field], after[field], places=7)

    def test_zero_radius_and_axial_direction_have_separate_availability(self):
        drawing = nozzle_drawing([[0, 0, 100], [100, 0, 0]], [[1, 0, 0], [0, 0, 1]])
        scene, = self.build(drawing)
        first, second = scene["source_rows"]
        self.assertIsNone(first["position_angle_deg"])
        self.assertEqual(first["direction_azimuth_deg"], 0)
        self.assertEqual(second["position_angle_deg"], 0)
        self.assertIsNone(second["direction_azimuth_deg"])
        radial = [stroke for stroke in scene["strokes"] if stroke.get("view_name") == "TOP" and stroke.get("annotation_kind") == "point_direction_symbol"]
        self.assertEqual({stroke["source_index"] for stroke in radial}, {0})
        for invalid in ([0, 0, 0], [float("nan"), 1, 0], [float("inf"), 0, 0]):
            malformed = deepcopy(drawing)
            malformed["nozzles"][0]["direction"].update(dict(zip(("x", "y", "z"), invalid)))
            with self.assertRaises(ValueError):
                self.build(malformed)

    def test_pose_requires_complete_identity_world_units_and_right_handed_unit_axes(self):
        valid = equipment_pose()
        mutations = [lambda p: p.update(success=False), lambda p: p.update(complete=1), lambda p: p.update(available=False),
                     lambda p: p.update(coordinate_frame="OWNER"), lambda p: p.update(coordinate_unit="m"),
                     lambda p: p.update(element_name="/OTHER"), lambda p: p.update(resolved_refno="/V-100"),
                     lambda p: p.update(position=[True, 0, 0]), lambda p: p.update(position=[1e10, 0, 0]),
                     lambda p: p.update(position=[float("nan"), 0, 0]), lambda p: p.update(position=[10**1000, 0, 0]),
                     lambda p: p["native_frame"].update(requested="OWNER"), lambda p: p["native_frame"].update(refno="=1/2"),
                     lambda p: p["orientation"].update(x_axis=[2, 0, 0]), lambda p: p["orientation"].update(y_axis=[1, 0, 0]),
                     lambda p: p["orientation"].update(z_axis=[0, 0, -1]), lambda p: p["orientation"].pop("x_axis")]
        for index, mutate in enumerate(mutations):
            pose = deepcopy(valid)
            mutate(pose)
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.build(pose=pose)
        for axial, zero in (("X", "X"), ("x", "Y"), (None, "X"), ([], "X"), ("Z", 1)):
            with self.subTest(axial=axial, zero=zero), self.assertRaises(ValueError):
                self.build(axial_axis=axial, zero_axis=zero)
        numeric = deepcopy(valid)
        numeric.update(requested_refno="=001/0100", element_name=None)
        self.build(pose=numeric)

    def test_source_rows_require_identity_complete_units_and_the_same_native_world_frame(self):
        valid = nozzle_drawing()
        mutations = [lambda d: d.update(available=False), lambda d: d.update(complete=False),
                     lambda d: d["nozzles"][0].update(native_frame=None),
                     lambda d: d["nozzles"][0]["native_frame"].update(refno="=2/0"),
                     lambda d: d["nozzles"][0]["position"].update(unit="m"),
                     lambda d: d["nozzles"][0].update(resolved_refno=None),
                     lambda d: d["nozzles"][0].update(element_name="/WRONG"),
                     lambda d: d["nozzles"][0].update(direction_available=False)]
        for index, mutate in enumerate(mutations):
            drawing = deepcopy(valid)
            mutate(drawing)
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.build(drawing)
        duplicate = deepcopy(valid)
        for key in ("refno", "resolved_refno", "element_name", "point_number"):
            duplicate["nozzles"][1][key] = duplicate["nozzles"][0][key]
        with self.assertRaisesRegex(ValueError, "duplicate_resolved_point"):
            self.build(duplicate)

    def test_options_preserve_nozzle_limits_and_reject_datum_or_unknown_fields(self):
        for options in (None, [], {"datum": {"label": "X", "origin_mm": [0, 0, 0]}}, {"size": "A0"},
                        {"points_per_page": True}, {"points_per_page": 32}, {"unknown": True}, {"title": "bad\ntitle"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                build_nozzle_axis_sheets(nozzle_drawing(), equipment_pose(), options, axial_axis="Z", zero_axis="X")
        scene, = self.build()
        self.assertEqual(scene["options"]["title"], "NOZZLE AXIS ORIENTATION")
        self.assertFalse(scene["equipment_frame"]["physical_center_verified"])
        self.assertFalse(scene["equipment_frame"]["physical_centerline_verified"])
        self.assertFalse(scene["equipment_frame"]["nozzle_ownership_verified"])

    def test_every_page_shares_scale_bounds_origin_and_transforms_in_a3_a4_at_8_or_16_points(self):
        drawing = nozzle_drawing(many_points(17))
        for size in ("A3", "A4"):
            for capacity in (8, 16):
                with self.subTest(size=size, capacity=capacity):
                    scenes = self.build(drawing, options={"size": size, "points_per_page": capacity})
                    self.assertEqual(len(scenes), math.ceil(17 / capacity))
                    self.assertEqual([row["id"] for scene in scenes for row in scene["source_rows"]], [f"N{index + 1:03d}" for index in range(17)])
                    self.assertEqual(len({scene["scale_denominator"] for scene in scenes}), 1)
                    for scene in scenes:
                        validate_drawing_sheet_scene(scene)
                        self.assertEqual(scene["shared_layout"], scenes[0]["shared_layout"])
                        self.assertEqual(scene["table"]["row_capacity"], capacity)
                        self.assertEqual([view["semantic_name"] for view in scene["views"]], ["RADIAL", "AXIAL_ELEVATION"])
                        for view, first in zip(scene["views"], scenes[0]["views"]):
                            self.assertEqual(view["transform"], first["transform"])
                            self.assertEqual(view["model_bounds"], first["model_bounds"])
                            self.assertEqual(view["equipment_origin_paper_mm"], first["equipment_origin_paper_mm"])
                            # The fixed angle/axis keys have their own gutter;
                            # native point marks, PDIR arrows and leaders stay
                            # clear even when a sixteen-point page is dense.
                            source_x = [point[0] for stroke in scene["strokes"]
                                        if stroke.get("view_name") == view["name"] and "source_index" in stroke
                                        for point in (stroke["start"], stroke["end"])]
                            self.assertGreater(min(source_x), view["legend_center_paper_mm"][0] + 14.5)
                        self.assertEqual({callout["source_index"] for callout in scene["callouts"]}, set(scene["source_indices"]))
                        self.assertEqual({item["text"] for item in scene["texts"] if item.get("annotation_kind") == "angle_reference_label"},
                                         {"0 +U", "90 +V", "180", "270"})
                        render_drawing_sheet_svg(scene)
                        export_drawing_sheet_dxf(scene)

    def test_global_bounds_include_native_origin_and_callout_anchors_are_local_projections(self):
        scene, = self.build(nozzle_drawing([[100, 200, 300], [400, 500, 600]]))
        rows = {row["source_index"]: row for row in scene["source_rows"]}
        for view in scene["views"]:
            self.assertEqual(view["model_bounds"]["min"], [0, 0])
            for callout in view["callouts"]:
                coordinates = rows[callout["source_index"]]["equipment_coordinates_mm"]
                values = [coordinates["u"], coordinates[view["vertical_axis"]]]
                expected = [view["paper_origin_mm"][axis] + (values[axis] - view["model_bounds"]["min"][axis]) / scene["scale_denominator"] for axis in (0, 1)]
                self.assertEqual(callout["anchor_mm"], expected)

    def test_svg_dxf_and_png_export_the_same_radial_markers_compass_and_schedule(self):
        for size in ("A3", "A4"):
            scene, = self.build(options={"size": size})
            svg = ET.fromstring(render_drawing_sheet_svg(scene))
            paths = svg.findall("{http://www.w3.org/2000/svg}path")
            entities = dxf_entities(export_drawing_sheet_dxf(scene))
            lines = [record for record in entities if record["kind"] == "LINE"]
            self.assertEqual(len(paths), len(scene["strokes"]))
            self.assertEqual(len(lines), len(scene["strokes"]))
            for stroke, path, line in zip(scene["strokes"], paths, lines):
                expected = [*stroke["start"], *stroke["end"]]
                for actual, target in zip([float(line[key]) for key in ("10", "20", "11", "21")], expected):
                    self.assertAlmostEqual(actual, target, places=6)
                parts = path.attrib["d"].split()
                for actual, target in zip(map(float, (parts[1], parts[2], parts[4], parts[5])),
                                          [expected[0], scene["paper"]["height_mm"] - expected[1], expected[2], scene["paper"]["height_mm"] - expected[3]]):
                    self.assertAlmostEqual(actual, target, places=6)
            expected_text = [item["text"] for item in scene["texts"]]
            self.assertEqual([item.text for item in svg.findall("{http://www.w3.org/2000/svg}text")], expected_text)
            self.assertEqual([item["1"] for item in entities if item["kind"] == "TEXT"], expected_text)
            width, height, pixel = png_pixels(rasterize_drawing_sheet_png(scene, width=1400, height=990))
            paper_w, paper_h = scene["paper_size_mm"]
            scale = min((width - 2) / paper_w, (height - 2) / paper_h)
            ox, oy = (width - paper_w * scale) / 2, (height - paper_h * scale) / 2
            for point in [callout["anchor_mm"] for callout in scene["callouts"]] + [view["equipment_origin_paper_mm"] for view in scene["views"]]:
                px, py = round(ox + point[0] * scale), round(oy + (paper_h - point[1]) * scale)
                self.assertTrue(any(pixel(px + dx, py + dy) == (23, 33, 43) for dx in (-1, 0, 1) for dy in (-1, 0, 1)))

    def test_large_coordinates_long_refs_and_maximum_metadata_fit_supported_papers(self):
        pose = equipment_pose(position=[-1e9, -1e9, -1e9])
        long_ref = "/" + "设备" * 60
        pose.update(requested_refno=long_ref, element_name=long_ref)
        drawing = nozzle_drawing([[1e9, 1e9, 1e9], [1e9 - 10, 1e9 - 20, 1e9 - 30]], pose=pose, world=True)
        drawing["nozzles"][0].update(refno=long_ref + "/N1", element_name=long_ref + "/N1")
        for size in ("A3", "A4"):
            scene, = self.build(drawing, pose, {"size": size, "title": "T" * 48, "drawing_number": "D" * 32})
            self.assertTrue(scene["equipment_reference_display_abbreviated"])
            self.assertTrue(scene["source_rows"][0]["reference_display_abbreviated"])
            self.assertAlmostEqual(scene["source_rows"][0]["radius_mm"], math.hypot(2e9, 2e9))
            render_drawing_sheet_svg(scene)
            export_drawing_sheet_dxf(scene)

    def test_inputs_pages_and_world_legacy_output_remain_independent(self):
        drawing, pose, options = nozzle_drawing(many_points(9)), equipment_pose(), {"size": "A4"}
        original = deepcopy((drawing, pose, options))
        legacy_before = json.dumps(build_nozzle_orientation_sheets(drawing, {"size": "A4"}), sort_keys=True)
        scenes = self.build(drawing, pose, options)
        final_before = deepcopy(scenes[-1])
        scenes[0]["equipment_frame"]["basis_world"]["u"][0] = 8
        scenes[0]["equipment_frame"]["native_pose"]["position"][0] = 9
        scenes[0]["source_rows"][0]["equipment_frame"]["basis_world"]["v"][0] = 10
        scenes[0]["source_rows"][0]["position"]["x"] = 11
        scenes[0]["views"][0]["model_bounds"]["min"][0] = 12
        self.assertEqual((drawing, pose, options), original)
        self.assertEqual(scenes[-1], final_before)
        self.assertEqual(json.dumps(build_nozzle_orientation_sheets(drawing, {"size": "A4"}), sort_keys=True), legacy_before)

    def test_unserializable_native_pose_evidence_is_rejected_instead_of_returning_partial_scenes(self):
        pose = equipment_pose()
        pose["extra"] = float("nan")
        with self.assertRaisesRegex(ValueError, "evidence_not_serializable"):
            self.build(pose=pose)

    def test_128_points_keep_global_ids_and_129_points_exceed_the_source_limit(self):
        scenes = self.build(nozzle_drawing(many_points(128)), options={"size": "A4", "points_per_page": 16})
        self.assertEqual(len(scenes), 8)
        self.assertEqual([row["id"] for scene in scenes for row in scene["source_rows"]],
                         [f"N{index + 1:03d}" for index in range(128)])
        with self.assertRaisesRegex(ValueError, "point_limit"):
            self.build(nozzle_drawing(many_points(129)))


if __name__ == "__main__":
    unittest.main()

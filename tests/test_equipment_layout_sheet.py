"""Independent position, annotation and export checks for equipment sheets."""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import struct
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.equipment_layout_drawing import build_equipment_layout_drawing
from aveva_e3d_mcp.equipment_layout_sheet import (
    build_equipment_layout_sheets, normalize_equipment_layout_datum, normalize_equipment_layout_sheet_options,
)
from aveva_e3d_mcp.drawing_sheet_render import (
    rasterize_drawing_sheet_png, render_drawing_sheet_svg, validate_drawing_sheet_scene,
)
from aveva_e3d_mcp.orthographic_dxf import export_drawing_sheet_dxf


IDENTITY = {"x_axis": [1, 0, 0], "y_axis": [0, 1, 0], "z_axis": [0, 0, 1]}
FOOTER = "LOCATION/POSE ONLY - NO EQUIPMENT ENVELOPE"


def native_pose(index, *, position=None, orientation=None):
    ref = f"=1/{100 + index}"
    return {"requested_refno": f"/VESSEL-{101 + index}", "refno": ref,
            "element": {"refno": ref, "name": f"/VESSEL-{101 + index}", "type": "EQUI"},
            "measurements": {"spatial": {"success": True, "complete": True, "result": {
                "success": True, "complete": True,
                "position": {"available": True, "unit": "mm", **dict(zip("xyz", position or
                    [1000 + index * 12500, 1008 + index * 2500, 900 + index * 1000]))},
                "orientation": {"available": True, **deepcopy(orientation or IDENTITY)},
            }}}}


def equipment_drawing(count=3, *, spacing=None, coincident=False):
    rows = []
    for index in range(count):
        position = ([123, -456, 789] if coincident else
                    [1000 + index * spacing, 1008 + index * spacing * 1.3, 900 + index * spacing * .4]
                    if spacing is not None else None)
        rows.append(native_pose(index, position=position))
    drawing = build_equipment_layout_drawing(rows)
    if drawing.get("success") is not True or drawing.get("complete") is not True:
        raise AssertionError(drawing)
    return drawing


def text_box(item):
    half_width = max(0, 6 * len(item["text"]) - 1) * item["height"] / 14
    x, y = item["position"]
    return [x - half_width, y - item["height"] / 2, x + half_width, y + item["height"] / 2]


class EquipmentLayoutSheetTests(unittest.TestCase):
    maxDiff = 2000

    def scenes(self, drawing=None, options=None, **kwargs):
        drawing = equipment_drawing() if drawing is None else drawing
        options = {} if options is None else options
        before, original = deepcopy(drawing), deepcopy(options)
        scenes = build_equipment_layout_sheets(drawing, options, **kwargs)
        self.assertEqual(drawing, before)
        self.assertEqual(options, original)
        return scenes

    def assert_annotations(self, scene):
        normalized = validate_drawing_sheet_scene(scene)
        self.assertEqual(len(normalized["strokes"]), len(scene["strokes"]))
        self.assertEqual(len(normalized["texts"]), len(scene["texts"]))
        boxes = [text_box(item) for item in scene["texts"]]
        for index, first in enumerate(boxes):
            for second in boxes[index + 1:]:
                self.assertFalse(min(first[2], second[2]) > max(first[0], second[0]) and
                                 min(first[3], second[3]) > max(first[1], second[1]))
        for item in scene["texts"]:
            if item.get("annotation_kind") in {"equipment_callout", "equipment_coordinate_row"}:
                self.assertGreaterEqual(item["height"], 2.25)
        self.assertEqual(scene["footer"], FOOTER)
        self.assertIn(FOOTER, [item["text"] for item in scene["texts"]])
        self.assertFalse(scene["plant_north_verified"])
        self.assertFalse(scene["extents_included"])
        self.assertFalse(scene["engineering_dimensions_certified"])
        self.assertFalse(scene["native_draft_sheet"])
        self.assertFalse([stroke for stroke in scene["strokes"] if stroke["role"] in {"silhouette", "dimension"}])
        self.assertTrue(json.dumps(scene, allow_nan=False))

    def test_options_and_datum_defaults_are_explicit_idempotent_and_isolated(self):
        options = normalize_equipment_layout_sheet_options({})
        self.assertEqual(options, {"size": "A3", "title": "EQUIPMENT LOCATION", "drawing_number": "E3D-EQUIPMENT", "equipment_per_page": 8})
        self.assertEqual(normalize_equipment_layout_sheet_options(options), options)
        datum = normalize_equipment_layout_datum()
        self.assertEqual(datum, {"label": "WORLD ORIGIN", "origin_mm": [0, 0, 0]})
        self.assertEqual(normalize_equipment_layout_datum(datum), datum)
        datum["origin_mm"][0] = 20
        self.assertEqual(normalize_equipment_layout_datum()["origin_mm"], [0, 0, 0])

    def test_options_reject_unknown_sheet_geometry_text_and_boolean_capacity(self):
        invalid = [None, [], True, {"size": "A2"}, {"size": True}, {"equipment_per_page": True},
                   {"equipment_per_page": 8.0}, {"equipment_per_page": 32}, {"scale_denominator": 1},
                   {"orientation": "portrait"}, {"title": ""}, {"title": "T" * 49},
                   {"title": "BAD\nTITLE"}, {"title": "中文"}, {"drawing_number": "D" * 33}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_equipment_layout_sheet_options(value)

    def test_explicit_datum_is_a_bounded_world_coordinate_and_never_an_inferred_center(self):
        original = {"label": "Grid A1", "origin_mm": [500, 600, 700]}
        saved = deepcopy(original)
        scene, = self.scenes(datum=original)
        self.assertEqual(original, saved)
        self.assertEqual(scene["datum"], {"label": "GRID A1", "origin_mm": saved["origin_mm"]})
        self.assertIn("DATUM GRID A1", next(item["text"] for item in scene["texts"] if item["text"].startswith("DATUM ")))
        self.assertEqual(scene["source_rows"][0]["relative_position_mm"], [500, 408, 200])
        default, = self.scenes()
        self.assertEqual(default["datum"]["origin_mm"], [0, 0, 0])
        self.assertNotEqual(default["datum"]["origin_mm"], default["position_bounds_world_mm"]["min"])

    def test_invalid_datum_values_are_refused(self):
        cases = [{}, True, [], {"origin_mm": [0, 0, 0]}, {"label": "A"},
                 {"label": "A", "origin_mm": [0, 0]}, {"label": "A", "origin_mm": [False, 0, 0]},
                 {"label": "A", "origin_mm": [float("nan"), 0, 0]},
                 {"label": "A", "origin_mm": [float("inf"), 0, 0]},
                 {"label": "A", "origin_mm": [1e9 + 1, 0, 0]},
                 {"label": "A", "origin_mm": [10 ** 500, 0, 0]},
                 {"label": "A\nB", "origin_mm": [0, 0, 0]},
                 {"label": "A", "origin_mm": [0, 0, 0], "coordinate_frame": "OWNER"}]
        for value in cases:
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                normalize_equipment_layout_datum(value)

    def test_input_and_every_source_must_be_complete_available_world_millimetres(self):
        drawing = equipment_drawing()
        for field in ("success", "available", "complete"):
            for value in (False, None, 1):
                candidate = deepcopy(drawing)
                candidate[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.scenes(candidate)
        for field in ("position_available", "orientation_available", "read_success", "read_complete"):
            for value in (False, None, 1):
                candidate = deepcopy(drawing)
                candidate["equipment"][1][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.scenes(candidate)
        for mutate in (lambda d: d.update(coordinate_frame="OWNER"), lambda d: d.update(coordinate_unit="m"),
                       lambda d: d.update(extents_included=True),
                       lambda d: d["equipment"][0].update(coordinate_frame="OWNER"),
                       lambda d: d["equipment"][0].update(coordinate_unit="m")):
            candidate = deepcopy(drawing)
            mutate(candidate)
            with self.assertRaises(ValueError):
                self.scenes(candidate)

    def test_missing_nonfinite_boolean_and_out_of_range_coordinates_are_refused(self):
        for position in (None, [0, 0], [0, 0, False], [0, 0, float("nan")],
                         [0, 0, float("inf")], [0, 0, 1e9 + 1], [0, 0, 10 ** 500]):
            candidate = equipment_drawing()
            candidate["equipment"][1]["position"] = position
            with self.subTest(position_type=type(position).__name__), self.assertRaises(ValueError):
                self.scenes(candidate)

    def test_orientation_requires_finite_orthogonal_unit_axes_and_right_handedness(self):
        cases = [None, {}, {**IDENTITY, "x_axis": [2, 0, 0]}, {**IDENTITY, "x_axis": [False, 0, 0]},
                 {**IDENTITY, "x_axis": [float("nan"), 0, 0]}, {**IDENTITY, "y_axis": [1, 0, 0]},
                 {**IDENTITY, "z_axis": [0, 0, -1]}]
        for axes in cases:
            candidate = equipment_drawing()
            candidate["equipment"][1]["orientation"] = axes
            with self.subTest(axes=axes), self.assertRaises(ValueError):
                self.scenes(candidate)

    def test_equipment_limit_identity_aliases_and_source_indices_are_strict(self):
        for count in (0, 257):
            with self.assertRaises(ValueError):
                self.scenes(equipment_drawing(count) if count else {**equipment_drawing(), "equipment": []})
        for mutate in (lambda rows: rows[1].update(source_index=True), lambda rows: rows[1].update(source_index=0),
                       lambda rows: rows[1].update(resolved_refno="=0001/00100"),
                       lambda rows: rows[1].update(requested_refno=None)):
            candidate = equipment_drawing()
            mutate(candidate["equipment"])
            with self.assertRaises(ValueError):
                self.scenes(candidate)

    def test_world_positions_and_relative_table_values_refer_to_same_source(self):
        scene, = self.scenes(datum={"label": "DATUM A", "origin_mm": [500, 600, 700]})
        self.assertEqual(scene["source_rows"][0]["table_values"], {
            "id": "E001", "device": "/VESSEL-101", "x_mm": "1000.000", "y_mm": "1008.000", "z_mm": "900.000",
            "dx_mm": "500.000", "dy_mm": "408.000", "del_mm": "200.000"})
        by_source = {row["source_index"]: row for row in scene["source_rows"]}
        for item in scene["texts"]:
            if item.get("annotation_kind") == "equipment_coordinate_row":
                row = by_source[item["source_index"]]
                self.assertEqual(item["text"], row["table_row_text"])
                self.assertIn(row["id"], item["text"])
        self.assert_annotations(scene)

    def test_rounding_is_limited_to_labels_and_extreme_datum_deltas_fit_table(self):
        drawing = equipment_drawing(1)
        drawing["equipment"][0]["position"] = [-1e9, -0.0000002, 0.123456789]
        scene, = self.scenes(drawing, datum={"label": "BOUNDARY", "origin_mm": [1e9, 1e9, -1e9]})
        row = scene["source_rows"][0]
        self.assertEqual(row["position"], drawing["equipment"][0]["position"])
        self.assertEqual(row["table_values"]["dx_mm"], "-2000000000.000")
        self.assertEqual(row["table_values"]["y_mm"], "0.000")
        self.assertEqual(row["table_values"]["z_mm"], "0.123")
        self.assertEqual(scene["table_coordinate_resolution_mm"], .001)
        self.assert_annotations(scene)

    def test_nine_equipment_paginate_without_renumbering_and_keep_global_bounds_and_transforms(self):
        scenes = self.scenes(equipment_drawing(9), {"equipment_per_page": 8})
        self.assertEqual([scene["page_number"] for scene in scenes], [1, 2])
        self.assertEqual([scene["equipment_count"] for scene in scenes], [8, 1])
        self.assertEqual([row["id"] for scene in scenes for row in scene["source_rows"]], [f"E{index + 1:03d}" for index in range(9)])
        self.assertEqual(scenes[0]["global_model_bounds"], scenes[1]["global_model_bounds"])
        self.assertEqual(scenes[0]["scale_denominator"], scenes[1]["scale_denominator"])
        for first, second in zip(scenes[0]["views"], scenes[1]["views"]):
            self.assertEqual(first["transform"], second["transform"])
            self.assertEqual(first["model_bounds"], second["model_bounds"])
        for scene in scenes:
            allowed = set(scene["source_indices"])
            self.assertTrue(all(stroke["source_index"] in allowed for stroke in scene["strokes"] if stroke["role"] == "feature"))
            self.assert_annotations(scene)

    def test_all_256_rows_keep_stable_ids_and_one_global_scale(self):
        scenes = self.scenes(equipment_drawing(256), {"equipment_per_page": 16})
        self.assertEqual(len(scenes), 16)
        self.assertEqual([row["source_index"] for scene in scenes for row in scene["source_rows"]], list(range(256)))
        self.assertEqual(scenes[-1]["source_rows"][-1]["id"], "E256")
        self.assertEqual(len({scene["scale_denominator"] for scene in scenes}), 1)
        for scene in scenes:
            self.assert_annotations(scene)

    def test_projected_positions_use_one_world_x_datum_for_top_front(self):
        drawing = equipment_drawing()
        scene, = self.scenes(drawing)
        source = {row["source_index"]: row for row in scene["source_rows"]}
        for view in scene["views"]:
            vertical = 1 if view["name"] == "TOP" else 2
            for symbol in view["symbols"]:
                position, transform = source[symbol["source_index"]]["position"], view["transform"]
                expected = [transform["paper_origin_mm"][axis] +
                            (position[0 if axis == 0 else vertical] - transform["model_min"][axis]) / scene["scale_denominator"]
                            for axis in (0, 1)]
                self.assertEqual(symbol["anchor_mm"], expected)
        top, front = scene["views"]
        self.assertEqual(top["transform"]["model_min"][0], front["transform"]["model_min"][0])
        self.assertEqual(top["toward_viewer"], [0, 0, 1])
        self.assertEqual(front["toward_viewer"], [0, -1, 0])

    def test_paper_pose_symbols_keep_size_while_position_scale_changes(self):
        scenes = [self.scenes(equipment_drawing(spacing=spacing))[0] for spacing in (12500, 1250000)]
        self.assertNotEqual(scenes[0]["scale_denominator"], scenes[1]["scale_denominator"])
        for scene in scenes:
            for stroke in scene["strokes"]:
                if stroke.get("annotation_kind") == "equipment_origin_symbol":
                    self.assertAlmostEqual(math.dist(stroke["start"], stroke["end"]), 1.6)
                elif stroke.get("annotation_kind") == "equipment_axis_symbol" and stroke["symbol_part"] == "shaft":
                    self.assertAlmostEqual(math.dist(stroke["start"], stroke["end"]), 6)
            self.assert_annotations(scene)

    def test_rotated_axes_have_true_projected_directions_and_tick_legend(self):
        angle = math.radians(37)
        axes = {"x_axis": [math.cos(angle), math.sin(angle), 0], "y_axis": [-math.sin(angle), math.cos(angle), 0], "z_axis": [0, 0, 1]}
        drawing = build_equipment_layout_drawing([native_pose(0, orientation=axes)])
        scene, = self.scenes(drawing)
        for view in scene["views"]:
            vertical = 1 if view["name"] == "TOP" else 2
            shafts = [stroke for stroke in scene["strokes"] if stroke.get("view_name") == view["name"] and stroke.get("symbol_part") == "shaft"]
            for shaft in shafts:
                axis = axes[shaft["axis_name"].lower() + "_axis"]
                self.assertAlmostEqual(shaft["end"][0] - shaft["start"][0], axis[0] * 6)
                self.assertAlmostEqual(shaft["end"][1] - shaft["start"][1], axis[vertical] * 6)
                ticks = [stroke for stroke in scene["strokes"] if stroke.get("view_name") == view["name"] and
                         stroke.get("axis_name") == shaft["axis_name"] and stroke.get("symbol_part") == "tick"]
                self.assertEqual(len(ticks), {"X": 1, "Y": 2, "Z": 3}[shaft["axis_name"]])
        self.assertEqual(scene["pose_axis_tick_legend"], {"X": 1, "Y": 2, "Z": 3})

    def test_end_on_axes_are_recorded_without_zero_length_symbols(self):
        scene, = self.scenes(equipment_drawing(1))
        self.assertEqual(scene["views"][0]["symbols"][0]["omitted_projected_axes"], ["z_axis"])
        self.assertEqual(scene["views"][1]["symbols"][0]["omitted_projected_axes"], ["y_axis"])
        self.assertTrue(all(stroke["start"] != stroke["end"] for stroke in scene["strokes"]))

    def test_coincident_equipment_retain_distinct_callouts_and_complete_source_mapping(self):
        scene, = self.scenes(equipment_drawing(8, coincident=True))
        self.assertEqual(len(scene["callouts"]), 16)
        for view in ("TOP", "FRONT"):
            callouts = [callout for callout in scene["callouts"] if callout["view_name"] == view]
            self.assertEqual(len({tuple(callout["anchor_mm"]) for callout in callouts}), 1)
            self.assertEqual(len({tuple(callout["position_mm"]) for callout in callouts}), 8)
        source = {row["source_index"]: row for row in scene["source_rows"]}
        for stroke in scene["strokes"]:
            if stroke["role"] == "feature":
                row = source[stroke["source_index"]]
                for key in ("id", "requested_refno", "resolved_refno"):
                    self.assertEqual(stroke[key], row[key])
        self.assert_annotations(scene)

    def test_long_and_non_ascii_names_are_explicit_aliases_with_full_references_retained(self):
        drawing = equipment_drawing()
        drawing["equipment"][0]["element_name"] = "/设备-101"
        drawing["equipment"][1]["element_name"] = "/" + "LONG" * 15
        scene, = self.scenes(drawing)
        for index in (0, 1):
            row = scene["source_rows"][index]
            self.assertEqual(row["reference_display"], f"SRC{index + 1:03d}")
            self.assertTrue(row["reference_display_abbreviated"])
            self.assertEqual(row["element_name"], drawing["equipment"][index]["element_name"])
            self.assertEqual(scene["reference_map"][row["id"]]["element_name"], row["element_name"])
        scene["source_rows"][0]["orientation"]["x_axis"][0] = 99
        self.assertEqual(drawing["equipment"][0]["orientation"]["x_axis"][0], 1)

    def test_dbref_device_names_use_supported_visible_aliases_and_keep_full_mapping(self):
        native = native_pose(0)
        native["requested_refno"] = "=1/10"
        native["refno"] = "=1/10"
        native["element"].update(refno="=1/10", name="=1/10")
        scene, = self.scenes(build_equipment_layout_drawing([native]))
        self.assertEqual(scene["source_rows"][0]["table_values"]["device"], "SRC001")
        self.assertEqual(scene["reference_map"]["E001"], {
            "requested_refno": "=1/10", "resolved_refno": "=1/10", "element_name": "=1/10",
            "reference_display": "SRC001", "reference_display_abbreviated": True})
        self.assert_annotations(scene)

    def test_missing_legacy_linework_does_not_change_pose_derived_sheets(self):
        drawing = equipment_drawing()
        expected = self.scenes(drawing)
        drawing["views"] = []
        drawing.pop("segment_count")
        self.assertEqual(self.scenes(drawing), expected)

    def test_all_paper_capacities_export_same_readable_png_svg_dxf_scene(self):
        for size in ("A3", "A4"):
            for capacity in (8, 16):
                with self.subTest(size=size, capacity=capacity):
                    scene, = self.scenes(equipment_drawing(capacity), {"size": size, "equipment_per_page": capacity})
                    self.assert_annotations(scene)
                    svg = ET.fromstring(render_drawing_sheet_svg(scene))
                    paths = svg.findall("{http://www.w3.org/2000/svg}path")
                    labels = svg.findall("{http://www.w3.org/2000/svg}text")
                    self.assertEqual(len(paths), len(scene["strokes"]))
                    self.assertEqual([node.text for node in labels], [item["text"] for item in scene["texts"]])
                    dxf = export_drawing_sheet_dxf(scene)
                    self.assertIn(FOOTER, dxf)
                    self.assertIn("TOP_FEATURE", dxf)
                    self.assertIn("FRONT_FEATURE", dxf)
                    self.assertNotIn("_OUTLINE", dxf)
                    self.assertNotIn("_DIMENSION", dxf)
                    png = rasterize_drawing_sheet_png(scene, width=800, height=566)
                    self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
                    self.assertEqual(struct.unpack(">II", png[16:24]), (800, 566))


if __name__ == "__main__":
    unittest.main()

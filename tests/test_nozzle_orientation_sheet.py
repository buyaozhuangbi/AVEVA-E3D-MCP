"""Traceable nozzle callouts and printable-scene export checks."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    import ezdxf
except ImportError:
    ezdxf = None

from aveva_e3d_mcp.drawing_sheet_render import (
    rasterize_drawing_sheet_png,
    render_drawing_sheet_svg,
    validate_drawing_sheet_scene,
)
from aveva_e3d_mcp.nozzle_orientation_drawing import build_nozzle_orientation_drawing
from aveva_e3d_mcp.nozzle_orientation_sheet import (
    build_nozzle_orientation_sheets,
    normalize_nozzle_datum,
    normalize_nozzle_sheet_options,
)
from aveva_e3d_mcp.orthographic_dxf import export_drawing_sheet_dxf


def nozzle_drawing(count: int = 2, *, coincident_plan: bool = False,
                   include_native_frame: bool = True) -> dict:
    """Use the existing builder and the native contract, without aggregate available."""
    records = []
    for index in range(count):
        position = [1000.0 + index * 1500.0,
                    1008.0 + index * 2000.0,
                    900.0 + index * 150.0]
        direction = ([0.0, 0.0, 1.0] if index == 0 else
                     [math.sqrt(0.5), math.sqrt(0.5), 0.0])
        refno = f"/V-101/N{index + 1}"
        point_number = 1
        if coincident_plan:
            position[0:2] = [1000.0, 1008.0]
            direction = [0.0, 0.0, 1.0 if index % 2 == 0 else -1.0]
            refno, point_number = "/V-101/N1", index + 1
        record = {"refno": refno, "native": {
            "ok": True, "version": "E3D21", "result": {
                "success": True, "complete": True,
                "element": {"refno": f"=2000000004/{46 + index}", "type": "NOZZ", "name": refno},
                "frame": {"refno": "=2000000002/0", "type": "WORL", "name": "/*", "requested": "WORLD"},
                "requested_point_numbers": [], "point_numbers": [point_number],
                "points": [{"number": point_number, "complete": True,
                            "exists": {"available": True, "value": True},
                            "position": {"available": True, "unit": "mm",
                                         **dict(zip(("x", "y", "z"), position))},
                            "direction": {"available": True,
                                          **dict(zip(("x", "y", "z"), direction))}}],
                "errors": {}, "ce_unchanged": True,
            },
        }}
        if coincident_plan and records:
            existing = records[0]["native"]["result"]
            existing["point_numbers"].append(point_number)
            existing["points"].extend(record["native"]["result"]["points"])
        else:
            records.append(record)
    if not include_native_frame:
        for record in records:
            record["native"]["result"].pop("frame")
    drawing, png = build_nozzle_orientation_drawing(records)
    if not drawing["success"] or png is None:
        raise AssertionError("Native-contract fixture must produce a complete drawing")
    return drawing


def large_layout_drawing(spacing_mm: float) -> dict:
    records = []
    for index, direction in enumerate(([1.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0])):
        refno = f"/PLANT-100/N{index + 1}"
        records.append({"refno": refno, "native": {"ok": True, "result": {
            "success": True, "complete": True,
            "element": {"refno": f"=2000000004/{46 + index}", "type": "NOZZ", "name": refno},
            "frame": {"refno": "=2000000002/0", "type": "WORL", "name": "/*", "requested": "WORLD"},
            "points": [{"number": 1,
                        "position": {"available": True, "unit": "mm",
                                     "x": 1000.0 + index * spacing_mm,
                                     "y": 1008.0 + index * spacing_mm * 1.3,
                                     "z": 900.0 + index * spacing_mm * 0.4},
                        "direction": {"available": True, **dict(zip(("x", "y", "z"), direction))}}],
        }}})
    drawing, png = build_nozzle_orientation_drawing(records)
    if not drawing["success"] or png is None:
        raise AssertionError("Large native layout fixture must produce a complete drawing")
    return drawing


class NozzleOrientationSheetTests(unittest.TestCase):
    def assert_inside_paper(self, scene: dict) -> None:
        # The shared validator checks full glyph footprints, including rotated
        # text, instead of accepting a baseline that happens to be inside.
        normalized = validate_drawing_sheet_scene(scene)
        width, height = scene["paper"]["width_mm"], scene["paper"]["height_mm"]
        self.assertEqual(len(normalized["strokes"]), len(scene["strokes"]))
        self.assertEqual(len(normalized["texts"]), len(scene["texts"]))
        for stroke in scene["strokes"]:
            for x, y in (stroke["start"], stroke["end"]):
                self.assertTrue(math.isfinite(x) and math.isfinite(y))
                self.assertGreaterEqual(x, -1e-7)
                self.assertLessEqual(x, width + 1e-7)
                self.assertGreaterEqual(y, -1e-7)
                self.assertLessEqual(y, height + 1e-7)

    def test_default_options_are_explicit_and_normalization_is_idempotent(self) -> None:
        options = {}
        normalized = normalize_nozzle_sheet_options(options)
        self.assertEqual(normalized, {
            "size": "A3", "title": "NOZZLE ORIENTATION",
            "drawing_number": "E3D-NOZZLE", "points_per_page": 8,
        })
        self.assertEqual(normalize_nozzle_sheet_options(normalized), normalized)
        self.assertEqual(options, {})

    def test_supported_paper_and_page_sizes_accept_bounded_titles(self) -> None:
        for size in ("A3", "A4"):
            for per_page in (8, 16):
                with self.subTest(size=size, points_per_page=per_page):
                    options = {"size": size, "points_per_page": per_page,
                               "title": "T" * 48, "drawing_number": "D" * 32}
                    saved = copy.deepcopy(options)
                    normalized = normalize_nozzle_sheet_options(options)
                    self.assertEqual(normalized["size"], size)
                    self.assertEqual(normalized["points_per_page"], per_page)
                    self.assertEqual(normalized["title"], options["title"])
                    self.assertEqual(normalized["drawing_number"], options["drawing_number"])
                    self.assertEqual(options, saved)

    def test_invalid_options_fail_before_a_scene_is_built(self) -> None:
        cases = [None, [], False, {"unexpected": True}, {"paper_size": "A3"},
                 {"orientation": "portrait"}, {"scale_denominator": 2},
                 {"size": "A2"}, {"size": "a3"}, {"size": True},
                 {"title": "T" * 49}, {"title": ""}, {"title": "A\nB"},
                 {"title": "中文"}, {"title": False},
                 {"drawing_number": "D" * 33}, {"drawing_number": "D\n1"},
                 {"points_per_page": 0}, {"points_per_page": 9},
                 {"points_per_page": True}, {"points_per_page": 8.0},
                 {"points_per_page": "8"}]
        for options in cases:
            with self.subTest(options=options), self.assertRaises(ValueError):
                normalize_nozzle_sheet_options(options)

    def test_source_rows_keep_world_values_and_stable_ids_across_paper_sizes(self) -> None:
        drawing = nozzle_drawing()
        original = copy.deepcopy(drawing)
        for size in ("A3", "A4"):
            with self.subTest(size=size):
                options = {"size": size}
                scene, = build_nozzle_orientation_sheets(drawing, options)
                self.assertEqual(scene["source_indices"], [0, 1])
                self.assertEqual([row["id"] for row in scene["source_rows"]], ["N001", "N002"])
                self.assertEqual(scene["model_coordinate_frame"], "WORLD")
                self.assertEqual(scene["model_coordinate_unit"], "mm")
                self.assertFalse(scene["native_draft_sheet"])
                for actual, source in zip(scene["source_rows"], drawing["nozzles"]):
                    for field in ("source_index", "refno", "point_number", "position", "direction",
                                  "bearing_deg", "bearing_available", "tilt_deg"):
                        self.assertEqual(actual[field], source[field])
                    self.assertIsNot(actual["position"], source["position"])
                    self.assertIsNot(actual["direction"], source["direction"])
                self.assertIsNone(scene["source_rows"][0]["bearing_deg"])
                self.assertFalse(scene["source_rows"][0]["bearing_available"])
                self.assertAlmostEqual(scene["source_rows"][1]["bearing_deg"], 45.0)
                self.assertEqual(scene["source_rows"][0]["table_values"]["azimuth_deg"], "NA")
                self.assertEqual(options, {"size": size})
                self.assert_inside_paper(scene)
        self.assertEqual(drawing, original)

    def test_matching_native_world_frame_is_retained_on_each_page(self) -> None:
        drawing = nozzle_drawing(9)
        scenes = build_nozzle_orientation_sheets(drawing, {})
        self.assertEqual(len(scenes), 2)
        for scene in scenes:
            self.assertEqual(scene["native_world_frame_refno"], "=2000000002/0")
            self.assertTrue(scene["native_world_frame_complete"])
            self.assertTrue(scene["complete"])
            self.assertTrue(all(row["native_frame"]["refno"] == scene["native_world_frame_refno"]
                                for row in scene["source_rows"]))

    def test_one_missing_native_frame_only_marks_its_page_frame_evidence_incomplete(self) -> None:
        for omitted in (True, False):
            with self.subTest(omitted=omitted):
                drawing = nozzle_drawing(9)
                if omitted:
                    drawing["nozzles"][0].pop("native_frame")
                else:
                    drawing["nozzles"][0]["native_frame"] = None
                scenes = build_nozzle_orientation_sheets(drawing, {})
                self.assertEqual([scene["native_world_frame_complete"] for scene in scenes], [False, True])
                self.assertTrue(all(scene["complete"] for scene in scenes))
                self.assertEqual([scene["native_world_frame_refno"] for scene in scenes],
                                 ["=2000000002/0", "=2000000002/0"])

    def test_builder_without_native_frame_keeps_mock_geometry_compatible(self) -> None:
        drawing = nozzle_drawing(include_native_frame=False)
        self.assertTrue(all(row["native_frame"] is None for row in drawing["nozzles"]))
        scene, = build_nozzle_orientation_sheets(drawing, {})
        self.assertTrue(scene["complete"])
        self.assertFalse(scene["native_world_frame_complete"])
        self.assertIsNone(scene["native_world_frame_refno"])
        self.assertEqual(scene["source_indices"], [0, 1])

    def test_different_native_world_frames_are_rejected_even_across_pages(self) -> None:
        drawing = nozzle_drawing(9)
        drawing["nozzles"][8]["native_frame"]["refno"] = "=2000000006/0"
        with self.assertRaisesRegex(ValueError, "native_world_frame_mismatch"):
            build_nozzle_orientation_sheets(drawing, {})

    def test_native_frame_type_and_requested_coordinate_contradictions_are_rejected(self) -> None:
        for field, value in (("type", "EQUI"), ("requested", "OWNER")):
            with self.subTest(field=field, value=value):
                drawing = nozzle_drawing()
                drawing["nozzles"][0]["native_frame"][field] = value
                with self.assertRaisesRegex(ValueError, "native_world_frame_required"):
                    build_nozzle_orientation_sheets(drawing, {})

    def test_coincident_points_retain_distinct_ids_point_numbers_and_sources(self) -> None:
        drawing = nozzle_drawing(coincident_plan=True)
        scene, = build_nozzle_orientation_sheets(drawing, {})
        self.assertEqual([row["refno"] for row in scene["source_rows"]], ["/V-101/N1"] * 2)
        self.assertEqual([row["point_number"] for row in scene["source_rows"]], [1, 2])
        self.assertEqual([row["id"] for row in scene["source_rows"]], ["N001", "N002"])
        self.assertEqual([row["tilt_deg"] for row in scene["source_rows"]], [90.0, -90.0])
        top_callouts = next(view for view in scene["views"] if view["name"] == "TOP")["callouts"]
        self.assertEqual(top_callouts[0]["anchor_mm"], top_callouts[1]["anchor_mm"])
        self.assertNotEqual(top_callouts[0]["position_mm"], top_callouts[1]["position_mm"])
        for view_name in ("TOP", "FRONT"):
            source_strokes = [stroke for stroke in scene["strokes"]
                              if stroke.get("view_name") == view_name and stroke.get("annotation_kind") in
                              {"point_position_symbol", "point_direction_symbol"}]
            self.assertEqual({stroke["source_index"] for stroke in source_strokes}, {0, 1})
        self.assert_inside_paper(scene)

    def test_table_values_and_callouts_correspond_to_the_same_native_points(self) -> None:
        drawing = nozzle_drawing()
        scene, = build_nozzle_orientation_sheets(drawing, {})
        rows = {row["source_index"]: row for row in scene["source_rows"]}
        for row in rows.values():
            values = row["table_values"]
            self.assertEqual(values["id"], row["id"])
            self.assertEqual(values["ref"], row["reference_display"])
            self.assertEqual(values["point_number"], str(row["point_number"]))
            for axis in ("x", "y", "z"):
                self.assertAlmostEqual(float(values[f"{axis}_mm"]), row["position"][axis])
            self.assertAlmostEqual(float(values["tilt_deg"]), row["tilt_deg"])
            if row["bearing_available"]:
                self.assertAlmostEqual(float(values["azimuth_deg"]), row["bearing_deg"])
            else:
                self.assertEqual(values["azimuth_deg"], "NA")
            table_row, = [item for item in scene["texts"]
                          if item.get("annotation_kind") == "point_table_row" and
                          item.get("source_index") == row["source_index"]]
            self.assertEqual(table_row["text"], row["table_row_text"])
        for view in scene["views"]:
            self.assertEqual({callout["source_index"] for callout in view["callouts"]}, set(rows))
            vertical = "y" if view["name"] == "TOP" else "z"
            for callout in view["callouts"]:
                row = rows[callout["source_index"]]
                self.assertEqual(callout["id"], row["id"])
                self.assertEqual(callout["point_number"], row["point_number"])
                native_point = [row["position"]["x"], row["position"][vertical]]
                expected = [view["paper_origin_mm"][axis] +
                            (native_point[axis] - view["model_bounds"]["min"][axis]) /
                            scene["scale_denominator"] for axis in (0, 1)]
                for actual, wanted in zip(callout["anchor_mm"], expected):
                    self.assertAlmostEqual(actual, wanted)
                label, = [item for item in scene["texts"] if item.get("view_name") == view["name"] and
                           item.get("source_index") == row["source_index"] and item["text"] == row["id"]]
                self.assertEqual(label["position"], callout["position_mm"])
                self.assertTrue(any(line.get("annotation_kind") == "point_leader" and
                                    line.get("view_name") == view["name"] and
                                    line.get("source_index") == row["source_index"] and
                                    line["start"] == callout["anchor_mm"] for line in scene["strokes"]))

    def test_large_layout_symbols_keep_paper_dimensions_when_the_model_scale_changes(self) -> None:
        denominators = set()
        for spacing_mm in (12_500.0, 125_000.0):
            drawing = large_layout_drawing(spacing_mm)
            for size in ("A3", "A4"):
                with self.subTest(spacing_mm=spacing_mm, size=size):
                    scene, = build_nozzle_orientation_sheets(drawing, {"size": size})
                    denominators.add(scene["scale_denominator"])
                    self.assertEqual(scene["direction_symbol_length_mm"], 8.0)
                    self.assertIs(scene["direction_symbols_to_model_scale"], False)
                    rows = {row["source_index"]: row for row in scene["source_rows"]}
                    for view in scene["views"]:
                        callouts = {item["source_index"]: item for item in view["callouts"]}
                        vertical = "y" if view["name"] == "TOP" else "z"
                        for source_index, row in rows.items():
                            expected = [view["paper_origin_mm"][axis] +
                                        (row["position"][coordinate] - view["model_bounds"]["min"][axis]) /
                                        scene["scale_denominator"]
                                        for axis, coordinate in enumerate(("x", vertical))]
                            for actual, wanted in zip(callouts[source_index]["anchor_mm"], expected):
                                self.assertAlmostEqual(actual, wanted)
                        for source_index in (0, 1):
                            symbols = [stroke for stroke in scene["strokes"]
                                       if stroke.get("annotation_kind") == "point_direction_symbol" and
                                       stroke.get("source_index") == source_index and
                                       stroke.get("view_name") == view["name"]]
                            self.assertTrue(symbols)
                            shaft = max(symbols, key=lambda line: math.dist(line["start"], line["end"]))
                            self.assertAlmostEqual(math.dist(shaft["start"], shaft["end"]), 8.0)
                            self.assertEqual(shaft["start"], callouts[source_index]["anchor_mm"])
                            self.assertAlmostEqual(shaft["end"][0] - shaft["start"][0], 8.0)
                            self.assertAlmostEqual(shaft["end"][1], shaft["start"][1])
                            markers = [stroke for stroke in scene["strokes"]
                                       if stroke.get("annotation_kind") == "point_position_symbol" and
                                       stroke.get("source_index") == source_index and
                                       stroke.get("view_name") == view["name"]]
                            self.assertEqual(len(markers), 2)
                            self.assertEqual({(abs(marker["end"][0] - marker["start"][0]) > 1e-8,
                                               abs(marker["end"][1] - marker["start"][1]) > 1e-8)
                                              for marker in markers}, {(True, False), (False, True)})
                            for marker in markers:
                                self.assertAlmostEqual(math.dist(marker["start"], marker["end"]), 1.8)
                                for axis in (0, 1):
                                    self.assertAlmostEqual((marker["start"][axis] + marker["end"][axis]) / 2,
                                                           callouts[source_index]["anchor_mm"][axis])
                    self.assert_inside_paper(scene)
        self.assertGreater(len(denominators), 1)

    def test_view_parallel_pdir_retains_position_and_callout_without_a_direction_symbol(self) -> None:
        scene, = build_nozzle_orientation_sheets(large_layout_drawing(12_500.0), {})
        top, = [view for view in scene["views"] if view["name"] == "TOP"]
        callout, = [item for item in top["callouts"] if item["source_index"] == 2]
        direction = [stroke for stroke in scene["strokes"]
                     if stroke.get("annotation_kind") == "point_direction_symbol" and
                     stroke.get("source_index") == 2 and stroke.get("view_name") == "TOP"]
        markers = [stroke for stroke in scene["strokes"]
                   if stroke.get("annotation_kind") == "point_position_symbol" and
                   stroke.get("source_index") == 2 and stroke.get("view_name") == "TOP"]
        self.assertEqual(direction, [])
        self.assertEqual(len(markers), 2)
        self.assertEqual(callout["id"], "N003")
        for marker in markers:
            self.assertAlmostEqual(math.dist(marker["start"], marker["end"]), 1.8)
            for axis in (0, 1):
                self.assertAlmostEqual((marker["start"][axis] + marker["end"][axis]) / 2,
                                       callout["anchor_mm"][axis])
        source = scene["source_rows"][2]
        self.assertEqual(source["direction"], {"available": True, "x": 0.0, "y": 0.0, "z": 1.0})
        self.assertFalse(source["bearing_available"])
        self.assertIsNone(source["bearing_deg"])
        self.assert_inside_paper(scene)

    def test_seventeen_points_paginate_without_renumbering_or_cross_page_linework(self) -> None:
        drawing = nozzle_drawing(17)
        scenes = build_nozzle_orientation_sheets(drawing, {})
        expected_indices = [list(range(8)), list(range(8, 16)), [16]]
        self.assertEqual(len(scenes), 3)
        self.assertEqual([scene["source_indices"] for scene in scenes], expected_indices)
        self.assertEqual([scene["page_number"] for scene in scenes], [1, 2, 3])
        self.assertTrue(all(scene["page_count"] == 3 for scene in scenes))
        self.assertEqual([row["id"] for scene in scenes for row in scene["source_rows"]],
                         [f"N{index + 1:03d}" for index in range(17)])
        for scene, selected in zip(scenes, expected_indices):
            self.assertEqual({view["name"] for view in scene["views"]}, {"TOP", "FRONT"})
            self.assertGreater(scene["scale_denominator"], 0)
            self.assertEqual({view["transform"]["scale_denominator"] for view in scene["views"]},
                             {scene["scale_denominator"]})
            for view in scene["views"]:
                lines = [stroke for stroke in scene["strokes"]
                         if stroke.get("view_name") == view["name"] and stroke.get("annotation_kind") in
                         {"point_position_symbol", "point_direction_symbol"}]
                self.assertEqual({line["source_index"] for line in lines}, set(selected))
            self.assert_inside_paper(scene)

    def test_all_paper_and_capacity_combinations_export_valid_shared_scenes(self) -> None:
        drawing = nozzle_drawing(17)
        for size in ("A3", "A4"):
            for per_page in (8, 16):
                with self.subTest(size=size, points_per_page=per_page):
                    scenes = build_nozzle_orientation_sheets(drawing, {
                        "size": size, "points_per_page": per_page,
                        "title": "NOZZLE POSITION AND DIRECTION", "drawing_number": "N-101/001",
                    })
                    self.assertEqual(len(scenes), math.ceil(17 / per_page))
                    for scene in scenes:
                        self.assert_inside_paper(scene)
                        svg = render_drawing_sheet_svg(scene)
                        document = ET.fromstring(svg)
                        self.assertEqual(document.attrib["width"], f"{scene['paper']['width_mm']:g}mm")
                        self.assertEqual(document.attrib["height"], f"{scene['paper']['height_mm']:g}mm")
                        png = rasterize_drawing_sheet_png(scene, width=1000, height=750)
                        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
                        dxf = export_drawing_sheet_dxf(scene)
                        self.assertIn("$ACADVER\n1\nAC1015", dxf)
                        self.assertIn("$INSUNITS\n70\n4", dxf)
                        self.assertEqual(dxf.count("\n0\nLINE\n"), len(scene["strokes"]))
                        self.assertEqual(dxf.count("\n0\nTEXT\n"), len(scene["texts"]))

    def test_incomplete_empty_or_unavailable_native_drawing_is_rejected(self) -> None:
        drawing = nozzle_drawing()
        invalid = []
        for key, value in (("success", False), ("complete", False), ("available", False),
                           ("coordinate_frame", "OWNER"), ("coordinate_unit", "cm"),
                           ("nozzles", []), ("views", [])):
            changed = copy.deepcopy(drawing)
            changed[key] = value
            invalid.append((key, changed))
        for field in ("position", "direction"):
            changed = copy.deepcopy(drawing)
            changed["nozzles"][0][field] = {"available": False}
            invalid.append((f"missing_{field}", changed))
        changed = copy.deepcopy(drawing)
        changed["nozzles"][0].pop("point_number")
        invalid.append(("missing_point_number", changed))
        for case, changed in invalid:
            with self.subTest(case=case), self.assertRaises(ValueError):
                build_nozzle_orientation_sheets(changed, {})

    def test_missing_or_ambiguous_line_sources_are_rejected(self) -> None:
        drawing = nozzle_drawing()
        invalid = []
        changed = copy.deepcopy(drawing)
        changed["views"][0]["lines"][0].pop("source_index")
        invalid.append(("missing_source", changed))
        changed = copy.deepcopy(drawing)
        changed["views"][0]["lines"][0]["source_index"] = 99
        invalid.append(("unexpected_source", changed))
        changed = copy.deepcopy(drawing)
        changed["nozzles"][1]["source_index"] = 0
        invalid.append(("duplicate_row_source", changed))
        changed = copy.deepcopy(drawing)
        changed["views"][0]["lines"] = [line for line in changed["views"][0]["lines"]
                                            if line["source_index"] == 0]
        invalid.append(("missing_source_linework", changed))
        for case, changed in invalid:
            with self.subTest(case=case), self.assertRaises(ValueError):
                build_nozzle_orientation_sheets(changed, {})

    def test_reference_abbreviation_keeps_the_complete_source_refno(self) -> None:
        drawing = nozzle_drawing()
        refs = ["=2000000004/46", "/VERY-LONG-MODEL-NAME/" + "NOZZLE_" * 20]
        for row, refno in zip(drawing["nozzles"], refs):
            row["refno"] = refno
        scene, = build_nozzle_orientation_sheets(drawing, {})
        self.assertEqual([row["refno"] for row in scene["source_rows"]], refs)
        self.assertTrue(all(isinstance(row["reference_display"], str) and row["reference_display"]
                            for row in scene["source_rows"]))
        self.assertTrue(all(row["reference_display_abbreviated"] for row in scene["source_rows"]))
        self.assert_inside_paper(scene)

    @unittest.skipIf(ezdxf is None, "optional ezdxf parser is not installed")
    def test_dxf_passes_independent_reader_audit_without_repairs(self) -> None:
        for scene in build_nozzle_orientation_sheets(nozzle_drawing(17), {"points_per_page": 16}):
            document = ezdxf.read(io.StringIO(export_drawing_sheet_dxf(scene)))
            audit = document.audit()
            self.assertEqual(audit.errors, [])
            self.assertEqual(audit.fixes, [])
            self.assertEqual(document.units, 4)
            self.assertEqual(len(document.modelspace()), len(scene["strokes"]) + len(scene["texts"]))


class NozzleDatumSheetTests(unittest.TestCase):
    def test_datum_normalization_is_strict_bounded_and_nonmutating(self) -> None:
        source = {"label": " site datum ", "origin_mm": [-0.0, 125.25, -1e9]}
        before = copy.deepcopy(source)
        datum = normalize_nozzle_datum(source)
        self.assertEqual(datum, {"label": "SITE DATUM", "origin_mm": [0.0, 125.25, -1e9]})
        self.assertEqual(normalize_nozzle_datum(datum), datum)
        options = normalize_nozzle_sheet_options({"datum": source})
        self.assertEqual(options["datum"], datum)
        self.assertEqual(normalize_nozzle_sheet_options(options), options)
        self.assertEqual(source, before)
        self.assertIsNot(datum["origin_mm"], source["origin_mm"])
        cases = [None, [], True, {}, {"label": "D"}, {"origin_mm": [0, 0, 0]},
                 {"label": "D", "origin_mm": [0, 0, 0], "axis": "X"},
                 {"label": "", "origin_mm": [0, 0, 0]}, {"label": "D" * 33, "origin_mm": [0, 0, 0]},
                 {"label": "D\nX", "origin_mm": [0, 0, 0]}, {"label": "中文", "origin_mm": [0, 0, 0]},
                 {"label": "D", "origin_mm": [0, 0]}, {"label": "D", "origin_mm": [0, 0, 0, 0]},
                 {"label": "D", "origin_mm": [True, 0, 0]}, {"label": "D", "origin_mm": ["0", 0, 0]},
                 {"label": "D", "origin_mm": [math.inf, 0, 0]}, {"label": "D", "origin_mm": [0, math.nan, 0]},
                 {"label": "D", "origin_mm": [0, 0, 1e9 + 1]}]
        for invalid in cases:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                normalize_nozzle_datum(invalid)
            with self.subTest(internal=invalid), self.assertRaises(ValueError):
                normalize_nozzle_sheet_options({"datum": invalid})

    def test_without_datum_preserves_default_scene_svg_dxf_and_png_bytes(self) -> None:
        expected = {
            "A3": ("b857aed486b2a364dfe17d4091e32caddc4445be3c7bb8ab1f2ac22257dc7d09",
                   "90c41c55f005c5e7ff2012d41c9c619f8fcbc9cf719304c22378d6eec20f94f3",
                   "c4bca3e31e3604e502351b8f5b13dfafb9b4c4492ecfc0add3d255dfc9a58601",
                   "39d3436d4944e43bfe0f58037fff7aa2cf01cd5ac44e0efb62e77f6684d27779"),
            "A4": ("2599be725eb4116dc828148839988d496e30d1f21869f7963b3bddd41fcfa698",
                   "b011b5fb0538f1081b23f47d01e4a872b83b7b537423e81c9b40c25a343acb0d",
                   "8946102caf6f27c0f7b88fe3f04946dd496a84ee8a265369ac53dfc0496d0e2c",
                   "562f79fff5bff17565dbe032f3c9d7101f950bfcf0b2736d1732533cd00baf33"),
        }
        drawing = nozzle_drawing()
        build_nozzle_orientation_sheets(drawing, {"datum": {"label": "D", "origin_mm": [0, 0, 0]}})
        for size, hashes in expected.items():
            with self.subTest(size=size):
                scene, = build_nozzle_orientation_sheets(drawing, {"size": size})
                outputs = (json.dumps(scene, separators=(",", ":")).encode(), render_drawing_sheet_svg(scene).encode(),
                           export_drawing_sheet_dxf(scene).encode(), rasterize_drawing_sheet_png(scene, width=1000, height=707))
                self.assertEqual(tuple(hashlib.sha256(data).hexdigest() for data in outputs), hashes)
                self.assertNotIn("datum", scene)
        data = json.dumps(build_nozzle_orientation_sheets(nozzle_drawing(17), {}), separators=(",", ":")).encode()
        self.assertEqual(hashlib.sha256(data).hexdigest(), "266a1aa252d5b0cd50c2fa7988887f59b128928913a2eb5761d2304488f6fd1a")

    def test_signed_offsets_and_elevation_preserve_original_world_rows_and_angles(self) -> None:
        drawing = nozzle_drawing()
        datum = {"label": "LOCAL D0", "origin_mm": [1750, 2008, 975]}
        scene, = build_nozzle_orientation_sheets(drawing, {"datum": datum})
        self.assertEqual(scene["table"]["columns"], ["ID", "REF", "PPOINT", "DX MM", "DY MM", "DEL MM", "AZ DEG", "TILT DEG"])
        for row, source, expected in zip(scene["source_rows"], drawing["nozzles"], ([-750, -1000, -75], [750, 1000, 75])):
            self.assertEqual(row["relative_position_mm"], expected)
            self.assertEqual([float(row["table_values"][key]) for key in ("dx_mm", "dy_mm", "del_mm")], expected)
            for key in ("position", "direction", "bearing_deg", "bearing_available", "tilt_deg", "refno", "source_index"):
                self.assertEqual(row[key], source[key])
        self.assertEqual(scene["source_rows"][0]["table_values"]["azimuth_deg"], "NA")
        self.assertEqual(scene["source_rows"][1]["table_values"]["azimuth_deg"], "45.00")
        texts = " ".join(item["text"] for item in scene["texts"])
        self.assertIn("USER DECLARED WORLD DATUM LOCAL D0", texts)
        self.assertIn("X0 1750.000 Y0 2008.000 Z0 975.000 MM", texts)
        self.assertIn("NOT EQUIPMENT AXIS OR CENTER", texts)
        self.assertIn("AZ 0 +Y TO +X / TILT +Z / NA VERTICAL", texts)
        self.assertFalse(scene["datum"]["equipment_axis_verified"])
        self.assertFalse(scene["datum"]["equipment_center_verified"])

    def test_common_world_extents_scale_and_transforms_span_every_page(self) -> None:
        drawing = nozzle_drawing(17)
        datum = {"label": "SHARED D0", "origin_mm": [1000, 1008, 900]}
        for size in ("A3", "A4"):
            for capacity in (8, 16):
                with self.subTest(size=size, capacity=capacity):
                    scenes = build_nozzle_orientation_sheets(drawing, {"size": size, "points_per_page": capacity, "datum": datum})
                    self.assertEqual([index for scene in scenes for index in scene["source_indices"]], list(range(17)))
                    self.assertEqual([row["id"] for scene in scenes for row in scene["source_rows"]], [f"N{i + 1:03d}" for i in range(17)])
                    self.assertEqual({scene["scale_denominator"] for scene in scenes}, {scenes[0]["scale_denominator"]})
                    for scene in scenes:
                        self.assertEqual(scene["datum"], scenes[0]["datum"])
                        self.assertEqual(scene["shared_layout"]["table_rows"], capacity)
                        self.assertEqual(scene["table"]["bounds_mm"], scenes[0]["table"]["bounds_mm"])
                        for view, first in zip(scene["views"], scenes[0]["views"]):
                            self.assertEqual(view["model_bounds"], first["model_bounds"])
                            self.assertEqual(view["transform"], first["transform"])
                            self.assertEqual(view["model_bounds"]["min"][0], 1000)
                            self.assertEqual(view["model_bounds"]["max"][0], 25000)
                            for callout in view["callouts"]:
                                source = drawing["nozzles"][callout["source_index"]]
                                vertical = "y" if view["name"] == "TOP" else "z"
                                expected = [view["paper_origin_mm"][axis] + (source["position"][coordinate] - view["model_bounds"]["min"][axis]) /
                                            scene["scale_denominator"] for axis, coordinate in enumerate(("x", vertical))]
                                self.assertEqual(callout["anchor_mm"], expected)
                        selected = set(scene["source_indices"])
                        self.assertTrue(all(stroke.get("source_index", next(iter(selected))) in selected for stroke in scene["strokes"]))

    def test_declaring_a_datum_never_moves_or_rotates_native_position_and_pdir_symbols(self) -> None:
        drawing = nozzle_drawing()
        original, = build_nozzle_orientation_sheets(drawing, {})
        for origin in ([0, 0, 0], [-1e9, 1e9, -1e9]):
            with self.subTest(origin=origin):
                scene, = build_nozzle_orientation_sheets(drawing, {"datum": {"label": "D", "origin_mm": origin}})
                self.assertEqual(scene["strokes"], original["strokes"])
                self.assertEqual(scene["views"], original["views"])
                self.assertEqual(scene["callouts"], original["callouts"])
                self.assertEqual(scene["orientation_convention"], drawing["orientation_convention"])

    def test_datum_pages_share_svg_dxf_and_png_position_evidence(self) -> None:
        from test_drawing_sheet_render import decode_png

        def entities(document):
            rows = document.splitlines()
            records, current, active = [], None, False
            for code, value in zip(rows[0::2], rows[1::2]):
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

        drawing = nozzle_drawing(9)
        for scene in build_nozzle_orientation_sheets(drawing, {"datum": {"label": "D0", "origin_mm": [1750, 2008, 975]}}):
            root = ET.fromstring(render_drawing_sheet_svg(scene))
            paths = root.findall("{http://www.w3.org/2000/svg}path")
            self.assertEqual(len(paths), len(scene["strokes"]))
            records = entities(export_drawing_sheet_dxf(scene))
            lines = [record for record in records if record["kind"] == "LINE"]
            self.assertEqual(len(lines), len(scene["strokes"]))
            for stroke, path, line in zip(scene["strokes"], paths, lines):
                values = path.attrib["d"].split()
                expected = [*stroke["start"], *stroke["end"]]
                actual = [float(line[key]) for key in ("10", "20", "11", "21")]
                for got, wanted in zip(actual, expected):
                    self.assertAlmostEqual(got, wanted, places=6)
                for got, wanted in zip(map(float, (values[1], values[2], values[4], values[5])),
                                       (expected[0], scene["paper"]["height_mm"] - expected[1], expected[2], scene["paper"]["height_mm"] - expected[3])):
                    self.assertAlmostEqual(got, wanted, places=6)
            svg_texts = [node.text for node in root.findall("{http://www.w3.org/2000/svg}text")]
            dxf_texts = [record["1"] for record in records if record["kind"] == "TEXT"]
            self.assertEqual(svg_texts, [item["text"] for item in scene["texts"]])
            self.assertEqual(dxf_texts, svg_texts)
            width, height, pixel = decode_png(rasterize_drawing_sheet_png(scene, width=1400, height=990))
            paper_w, paper_h = scene["paper_size_mm"]
            scale = min((width - 2) / paper_w, (height - 2) / paper_h)
            ox, oy = (width - paper_w * scale) / 2, (height - paper_h * scale) / 2
            for callout in scene["callouts"]:
                x, y = callout["anchor_mm"]
                px, py = round(ox + x * scale), round(oy + (paper_h - y) * scale)
                self.assertTrue(any(pixel(px + dx, py + dy) == (23, 33, 43) for dx in (-1, 0, 1) for dy in (-1, 0, 1)))

    def test_extreme_datum_values_and_long_label_fit_all_paper_capacities(self) -> None:
        drawing = nozzle_drawing(17)
        for size in ("A3", "A4"):
            for capacity in (8, 16):
                scenes = build_nozzle_orientation_sheets(drawing, {"size": size, "points_per_page": capacity,
                    "datum": {"label": "D" * 32, "origin_mm": [-1e9, -1e9, -1e9]}})
                for scene in scenes:
                    validate_drawing_sheet_scene(scene)
                    for row in scene["source_rows"]:
                        self.assertEqual(len(row["relative_position_mm"]), 3)
                    render_drawing_sheet_svg(scene)
                    export_drawing_sheet_dxf(scene)

    def test_datum_and_world_inputs_are_not_mutated_or_aliased_across_pages(self) -> None:
        drawing = nozzle_drawing(9)
        options = {"datum": {"label": "D0", "origin_mm": [1750, 2008, 975]}}
        before = copy.deepcopy((drawing, options))
        scenes = build_nozzle_orientation_sheets(drawing, options)
        last_before = copy.deepcopy(scenes[-1])
        first = scenes[0]
        first["datum"]["origin_mm"][0] = 1
        first["options"]["datum"]["origin_mm"][0] = 2
        first["source_rows"][0]["relative_position_mm"][0] = 3
        first["source_rows"][0]["position"]["x"] = 4
        first["views"][0]["model_bounds"]["min"][0] = 5
        self.assertEqual((drawing, options), before)
        self.assertEqual(scenes[-1], last_before)


if __name__ == "__main__":
    unittest.main()

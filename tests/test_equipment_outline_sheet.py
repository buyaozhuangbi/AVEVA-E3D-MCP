"""Global outline, equipment attribution and readable POS-sheet checks."""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aveva_e3d_mcp.drawing_sheet_render import (
    SHEET_TEXT_CHARACTERS, rasterize_drawing_sheet_png, render_drawing_sheet_svg,
    validate_drawing_sheet_scene,
)
from aveva_e3d_mcp.equipment_layout_drawing import build_equipment_layout_drawing
from aveva_e3d_mcp.equipment_outline_sheet import (
    build_equipment_outline_sheet, normalize_equipment_outline_sheet_options,
    normalize_equipment_outline_targets,
)
from aveva_e3d_mcp.material_assembly_drawing import combine_material_drawing_meshes, generate_material_assembly_drawing
from aveva_e3d_mcp.orthographic_dxf import export_drawing_sheet_dxf
from test_equipment_layout_sheet import native_pose, text_box
from test_material_assembly_drawing import CSG_ENGINE_AVAILABLE, csg_source, parameter_source
from test_primitive_csg_mesh import cut


FOOTER = "SELECTED PARAMETER GEOMETRY / NOT WHOLE EQUIPMENT"
NATIVE_SCOPE = "PARAMETER ASSEMBLY - NATIVE CUT SCOPE UNVERIFIED"


def material_drawing(sources=None, **kwargs):
    combined = combine_material_drawing_meshes(sources or [
        parameter_source("=1/1001", position=(100, 200, 300)),
        parameter_source("=1/1002", dimensions={"XLEN": 30, "YLEN": 20, "ZLEN": 25}, position=(300, 150, 300)),
        parameter_source("=1/1003", dimensions={"XLEN": 80, "YLEN": 60, "ZLEN": 20}, position=(600, 200, 300)),
    ])
    if not combined["success"]:
        raise AssertionError(combined)
    drawing = generate_material_assembly_drawing(combined, views=["TOP"], projection_convention="right_handed",
                                                 show_hidden_lines=True, **kwargs)
    if not drawing["success"]:
        raise AssertionError(drawing)
    return drawing


def poses(count=2, *, coincident=False):
    rows = [native_pose(index, position=([100, 200, 300] if coincident else [100 + index * 500, 200, 300]))
            for index in range(count)]
    return build_equipment_layout_drawing(rows)


def geometry(scene):
    return [stroke for stroke in scene["strokes"] if stroke.get("annotation_kind") == "equipment_material_outline"]


class _EquipmentOutlineAssertions:
    @classmethod
    def setUpClass(cls):
        cls.drawing = material_drawing()
        cls.poses = poses()
        cls.groups = [[0, 1], [2]]

    def scene(self, drawing=None, pose_drawing=None, groups=None, options=None, **kwargs):
        drawing = self.drawing if drawing is None else drawing
        pose_drawing = self.poses if pose_drawing is None else pose_drawing
        groups = self.groups if groups is None else groups
        options = {} if options is None else options
        originals = deepcopy((drawing, pose_drawing, groups, options, kwargs))
        result = build_equipment_outline_sheet(drawing, pose_drawing, groups, options, **kwargs)
        self.assertEqual((drawing, pose_drawing, groups, options, kwargs), originals)
        return result

    def assert_readable(self, scene):
        validate_drawing_sheet_scene(scene)
        boxes = [text_box(item) for item in scene["texts"]]
        for index, first in enumerate(boxes):
            for second in boxes[index + 1:]:
                self.assertFalse(min(first[2], second[2]) > max(first[0], second[0]) and
                                 min(first[3], second[3]) > max(first[1], second[1]))
        for item in scene["texts"]:
            self.assertTrue(set(item["text"]) <= SHEET_TEXT_CHARACTERS)
            if item.get("annotation_kind") in {"equipment_coordinate_row", "equipment_callout"}:
                self.assertGreaterEqual(item["height"], 2.25)
        self.assertEqual(scene["footer"], FOOTER)
        visible = [item["text"] for item in scene["texts"]]
        self.assertIn(FOOTER, visible)
        self.assertIn(NATIVE_SCOPE, visible)
        self.assertFalse(scene["whole_equipment_geometry_verified"])
        self.assertFalse(scene["explicit_source_membership_verified"])
        self.assertFalse(scene["equipment_extents_verified"])
        self.assertFalse(scene["native_draft_sheet"])
        self.assertFalse(scene["engineering_dimensions_certified"])
        self.assertEqual(scene["serialization_precision"]["coordinate_decimal_places"], 15)
        self.assertTrue(json.dumps(scene, allow_nan=False))


class EquipmentOutlineSheetTests(_EquipmentOutlineAssertions, unittest.TestCase):

    def test_grouped_targets_are_strict_unique_and_globally_bounded(self):
        groups = [{"refno": "/PUMP-101", "sources": [{"refno": "=1/1001", "expected_type": "BOX", "geometry_mode": "parameter_mesh"}]}]
        self.assertEqual(normalize_equipment_outline_targets(groups), groups)
        invalid = [None, [], [None], [{"refno": "/P1", "sources": []}],
                   [{**groups[0], "expected_type": "EQUI"}], groups * 2,
                   [{**groups[0], "refno": "=1/10"}, {**groups[0], "refno": "=001/010"}],
                   [{**groups[0], "refno": "/P1"}, {**groups[0], "refno": "/P2"}],
                   [{"refno": "/P1", "sources": [{"refno": "=1/1", "expected_type": "EQUI", "geometry_mode": "parameter_mesh"}]}],
                   [{"refno": f"/P{i}", "sources": [{"refno": f"=1/{i}", "expected_type": "BOX", "geometry_mode": "parameter_mesh"}]} for i in range(17)],
                   [{"refno": "/P1", "sources": [{"refno": f"=1/{i}", "expected_type": "BOX", "geometry_mode": "parameter_mesh"} for i in range(129)]}]]
        for value in invalid:
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                normalize_equipment_outline_targets(value)
        maximum = [{"refno": "/P1", "sources": [{"refno": f"=1/{i}", "expected_type": "BOX", "geometry_mode": "parameter_mesh"} for i in range(128)]}]
        self.assertEqual(len(normalize_equipment_outline_targets(maximum)[0]["sources"]), 128)

    def test_sheet_options_are_idempotent_and_reject_invalid_scale_or_layout_controls(self):
        result = normalize_equipment_outline_sheet_options({})
        self.assertEqual(result, {"size": "A3", "title": "EQUIPMENT OUTLINE / TOP", "drawing_number": "E3D-EQUIP-OUTLINE"})
        self.assertEqual(normalize_equipment_outline_sheet_options(result), result)
        self.assertEqual(normalize_equipment_outline_sheet_options({"scale_denominator": 10})["scale_denominator"], 10)
        invalid = [None, [], True, {"size": "A2"}, {"paper_size": "A4"}, {"equipment_per_page": 8},
                   {"title": "BAD\nTITLE"}, {"title": "中文"}, {"orientation": "portrait"},
                   *[{"scale_denominator": value} for value in (False, 0, -1, float("inf"), float("nan"), 10 ** 500)]]
        for value in invalid:
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                normalize_equipment_outline_sheet_options(value)

    def test_material_outline_is_retained_once_with_source_edge_and_equipment_mapping(self):
        scene = self.scene()
        actual, expected = geometry(scene), self.drawing["views"][0]["lines"]
        self.assertEqual(len(actual), len(expected))
        self.assertTrue(any(stroke["role"] == "silhouette" for stroke in actual))
        transform = scene["views"][0]["transform"]
        for index, (stroke, line) in enumerate(zip(actual, expected)):
            self.assertEqual(stroke["source_line_index"], index)
            self.assertEqual(stroke["global_line_index"], index)
            self.assertEqual(stroke["source_interval"], [0, 1])
            for key in ("source_index", "source_refno", "source_geometry_mode", "edge", "visibility"):
                self.assertEqual(stroke[key], line[key])
            self.assertEqual(stroke["role"], line["kind"])
            owner = 0 if line["source_index"] in (0, 1) else 1
            self.assertEqual(stroke["equipment_index"], owner)
            self.assertEqual(stroke["equipment_id"], f"E{owner + 1:03d}")
            self.assertEqual(stroke["equipment_refno"], self.poses["equipment"][owner]["resolved_refno"])
            for endpoint in ("start", "end"):
                expected_point = [transform["paper_origin_mm"][axis] +
                                  (line[endpoint][axis] - transform["model_min"][axis]) / scene["scale_denominator"]
                                  for axis in (0, 1)]
                self.assertEqual(stroke[endpoint], expected_point)
        self.assertEqual(scene["source_ranges"], self.drawing["source_ranges"])
        self.assertEqual(scene["geometry_sha256"], self.drawing["geometry_sha256"])
        self.assertEqual(scene["equipment_source_map"]["E001"]["source_indices"], [0, 1])
        self.assertEqual([row["equipment_index"] for row in scene["source_equipment_map"]], [0, 0, 1])
        self.assertEqual(scene["coverage"]["input_line_count"], len(expected))
        self.assertFalse(scene["coverage"]["visibility_recomputed"])
        self.assertFalse(scene["coverage"]["cropping_applied"])
        self.assert_readable(scene)

    def test_sheet_does_not_project_or_derive_any_source_again(self):
        with patch("aveva_e3d_mcp.material_assembly_drawing.generate_material_assembly_drawing", side_effect=AssertionError("projection repeated")), \
                patch("aveva_e3d_mcp.material_assembly_drawing.combine_material_drawing_meshes", side_effect=AssertionError("combination repeated")):
            scene = self.scene()
        self.assertEqual(scene["counts"]["geometry_strokes"], self.drawing["segment_count"])

    def test_bounds_include_far_equipment_origins_without_inventing_source_envelopes(self):
        pose_drawing = poses()
        pose_drawing["equipment"][0]["position"] = [-1000, 4000, 123.456]
        scene = self.scene(pose_drawing=pose_drawing, datum={"label": "GRID A", "origin_mm": [-900, 3500, 120]})
        self.assertEqual(scene["global_model_bounds"]["min"][0], -1000)
        self.assertEqual(scene["global_model_bounds"]["max"][1], 4000)
        self.assertEqual(scene["geometry_model_bounds"], self.drawing["views"][0]["bounds"])
        row = scene["source_rows"][0]
        self.assertEqual(row["position"], [-1000, 4000, 123.456])
        self.assertEqual(row["table_values"]["dx_mm"], "-100.000")
        self.assertEqual(row["table_values"]["dy_mm"], "500.000")
        self.assertEqual(row["table_values"]["del_mm"], "3.456")
        self.assertEqual(row["material_source_indices"], [0, 1])
        self.assertEqual(scene["datum"], {"label": "GRID A", "origin_mm": [-900, 3500, 120]})
        self.assert_readable(scene)

    def test_every_callout_is_anchored_to_the_same_pos_used_in_coordinate_table(self):
        scene = self.scene()
        transform = scene["views"][0]["transform"]
        for callout in scene["callouts"]:
            row = scene["source_rows"][callout["equipment_index"]]
            expected = [transform["paper_origin_mm"][axis] +
                        (row["position"][axis] - transform["model_min"][axis]) / scene["scale_denominator"] for axis in (0, 1)]
            self.assertEqual(callout["anchor_mm"], expected)
            self.assertEqual(callout["equipment_id"], row["id"])
            label = next(item for item in scene["texts"] if item.get("equipment_index") == callout["equipment_index"]
                         and item.get("annotation_kind") == "equipment_coordinate_row")
            self.assertEqual(label["text"], row["table_row_text"])
        view = scene["views"][0]
        self.assertEqual(view["horizontal_axis"], [1, 0, 0])
        self.assertEqual(view["vertical_axis"], [0, 1, 0])
        self.assertEqual(view["toward_viewer"], [0, 0, 1])

    def test_all_sources_must_be_assigned_once_and_every_equipment_has_material(self):
        for groups in (None, [], [[0, 1, 2]], [[0, 1], []], [[0, 1], [1, 2]], [[0], [2]],
                       [[0, 1], [True]], [[0, 1], [3]], [[0.0, 1], [2]], [[-1, 1], [2]]):
            if groups is None:
                with self.assertRaises(ValueError):
                    build_equipment_outline_sheet(self.drawing, self.poses, None, {})
            else:
                with self.subTest(groups=groups), self.assertRaises(ValueError):
                    self.scene(groups=groups)

    def test_incomplete_or_invalid_world_pose_blocks_all_outlines(self):
        for mutate in (lambda value: value.update(complete=False),
                       lambda value: value.update(coordinate_frame="OWNER"),
                       lambda value: value["equipment"][1].update(orientation_available=False),
                       lambda value: value["equipment"][1].update(position=None),
                       lambda value: value["equipment"][1].update(resolved_type="BOX")):
            candidate = deepcopy(self.poses)
            mutate(candidate)
            with self.assertRaises(ValueError):
                self.scene(pose_drawing=candidate)

    def test_wrong_view_axes_line_source_or_material_scope_is_refused(self):
        faults = (lambda value: value.update(complete=False), lambda value: value.update(projection_convention="legacy"),
                  lambda value: value["views"][0].update(name="FRONT"),
                  lambda value: value["views"][0].update(vertical_axis=[0, -1, 0]),
                  lambda value: value["views"][0]["lines"][0].update(source_refno="=9/9"),
                  lambda value: value["views"][0]["lines"][0].update(source_index=True),
                  lambda value: value["views"][0]["lines"][0].update(edge=[999, 1000]),
                  lambda value: value.update(footer="COMPLETE NATIVE EQUIPMENT"),
                  lambda value: value.pop("geometry_sha256"))
        for mutate in faults:
            candidate = deepcopy(self.drawing)
            mutate(candidate)
            with self.assertRaises(ValueError):
                self.scene(drawing=candidate)

    def test_global_visibility_survives_between_different_equipment_groups(self):
        drawing = material_drawing([
            parameter_source("=1/1001", position=(0, 0, 0)),
            parameter_source("=1/1002", position=(25, 0, 80)),
        ])
        self.assertTrue(any(line["visibility"] == "hidden" for line in drawing["views"][0]["lines"]))
        scene = self.scene(drawing=drawing, groups=[[0], [1]])
        self.assertEqual([(line["source_index"], line["visibility"], line.get("parameter_interval")) for line in geometry(scene)],
                         [(line["source_index"], line["visibility"], line.get("parameter_interval")) for line in drawing["views"][0]["lines"]])

    def test_coincident_origins_keep_separate_equipment_labels_and_source_assignments(self):
        scene = self.scene(pose_drawing=poses(coincident=True))
        self.assertEqual(scene["callouts"][0]["anchor_mm"], scene["callouts"][1]["anchor_mm"])
        self.assertNotEqual(scene["callouts"][0]["position_mm"], scene["callouts"][1]["position_mm"])
        self.assertEqual([row["id"] for row in scene["source_rows"]], ["E001", "E002"])
        self.assert_readable(scene)

    def test_requested_scale_fits_or_fails_before_any_partial_scene(self):
        scene = self.scene(options={"scale_denominator": 100})
        self.assertEqual(scene["scale_denominator"], 100)
        self.assertEqual(scene["scale_mode"], "explicit")
        with self.assertRaisesRegex(ValueError, "explicit_scale_does_not_fit"):
            self.scene(options={"scale_denominator": .001})
        with self.assertRaisesRegex(ValueError, "scale_label"):
            self.scene(options={"scale_denominator": 1e100})

    def test_long_non_ascii_and_dbref_names_are_mapped_to_supported_table_aliases(self):
        candidate = deepcopy(self.poses)
        candidate["equipment"][0]["element_name"] = "/中文设备"
        candidate["equipment"][1]["element_name"] = "=1/101"
        scene = self.scene(pose_drawing=candidate)
        for index in range(2):
            identifier = f"E{index + 1:03d}"
            self.assertEqual(scene["equipment_source_map"][identifier]["element_name"], candidate["equipment"][index]["element_name"])
            self.assertEqual(scene["equipment_source_map"][identifier]["reference_display"], f"SRC{index + 1:03d}")
        self.assert_readable(scene)

    def test_sixteen_equipment_fit_A3_and_overfilled_A4_fails_explicitly(self):
        drawing = material_drawing([parameter_source(f"=1/{1000 + index}", position=(index * 150, 0, 0)) for index in range(16)])
        scene = self.scene(drawing=drawing, pose_drawing=poses(16), groups=[[index] for index in range(16)])
        self.assertEqual(scene["equipment_count"], 16)
        self.assertEqual(scene["page_count"], 1)
        self.assertEqual(scene["source_rows"][-1]["id"], "E016")
        self.assert_readable(scene)
        with self.assertRaisesRegex(ValueError, "single_page_callouts_do_not_fit"):
            self.scene(drawing=drawing, pose_drawing=poses(16), groups=[[index] for index in range(16)], options={"size": "A4"})

    def test_source_line_budget_is_checked_before_paper_generation(self):
        drawing = deepcopy(self.drawing)
        drawing["views"][0]["lines"] = [drawing["views"][0]["lines"][0]] * 20001
        drawing["segment_count"] = 20001
        with self.assertRaisesRegex(ValueError, "source_line_limit_exceeded"):
            self.scene(drawing=drawing)

    def test_a_nonzero_source_interval_below_paper_precision_is_never_silently_dropped(self):
        drawing = deepcopy(self.drawing)
        base = drawing["views"][0]["lines"][0]
        stub = deepcopy(base)
        changing = next(axis for axis in (0, 1) if base["start"][axis] != base["end"][axis])
        stub["end"] = list(stub["start"])
        stub["end"][changing] = math.nextafter(base["start"][changing], base["end"][changing])
        drawing["views"][0]["lines"].append(stub)
        drawing["segment_count"] += 1
        with self.assertRaisesRegex(ValueError, "geometry_below_precision"):
            self.scene(drawing=drawing, options={"scale_denominator": 10000})

    def test_all_paper_exports_retain_readable_annotations_and_geometry(self):
        for size in ("A3", "A4"):
            with self.subTest(size=size):
                scene = self.scene(options={"size": size})
                self.assert_readable(scene)
                svg = ET.fromstring(render_drawing_sheet_svg(scene, coordinate_decimal_places=15))
                paths = svg.findall("{http://www.w3.org/2000/svg}path")
                labels = svg.findall("{http://www.w3.org/2000/svg}text")
                self.assertEqual(len(paths), len(scene["strokes"]))
                self.assertEqual([item.text for item in labels], [item["text"] for item in scene["texts"]])
                dxf = export_drawing_sheet_dxf(scene, coordinate_decimal_places=15)
                self.assertIn(FOOTER, dxf)
                self.assertIn(NATIVE_SCOPE, dxf)
                self.assertIn("TOP_OUTLINE", dxf)
                self.assertNotIn("_DIMENSION", dxf)
                self.assertRegex(dxf, r"\d+\.\d{15}")
                png = rasterize_drawing_sheet_png(scene, width=1000, height=707)
                self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
                self.assertEqual(struct.unpack(">II", png[16:24]), (1000, 707))

    def test_output_mapping_and_source_pose_evidence_do_not_share_mutable_input(self):
        drawing, pose_drawing, groups = deepcopy(self.drawing), deepcopy(self.poses), deepcopy(self.groups)
        scene = self.scene(drawing=drawing, pose_drawing=pose_drawing, groups=groups)
        scene["source_ranges"][0]["vertex_range"][0] = 999
        scene["source_rows"][0]["position"][0] = 999
        scene["equipment_source_indices"][0][0] = 999
        self.assertEqual(drawing, self.drawing)
        self.assertEqual(pose_drawing, self.poses)
        self.assertEqual(groups, self.groups)


@unittest.skipUnless(CSG_ENGINE_AVAILABLE, "Mesh64 integration runtime is required.")
class EquipmentOutlineMaterialTests(_EquipmentOutlineAssertions, unittest.TestCase):
    def test_csg_hole_keeps_material_boundary_and_group_mapping(self):
        drawing = material_drawing([csg_source("=1/1001")])
        scene = self.scene(drawing=drawing, pose_drawing=poses(1), groups=[[0]])
        self.assertTrue(scene["cuts_applied"])
        self.assertEqual(scene["source_ranges"], drawing["source_ranges"])
        self.assertEqual(scene["source_ranges"][0]["geometry_mode"], "owner_member_difference")
        self.assertEqual(len(geometry(scene)), drawing["segment_count"])
        self.assert_readable(scene)

    def test_each_equipment_must_have_nonempty_material_and_empty_sources_keep_identity(self):
        empty = csg_source("=1/1002", cutters=[cut("BOX", {"XLEN": 200, "YLEN": 200, "ZLEN": 100})])
        drawing = material_drawing([parameter_source("=1/1001"), empty])
        with self.assertRaisesRegex(ValueError, "no_nonempty_material"):
            self.scene(drawing=drawing, groups=[[0], [1]])
        scene = self.scene(drawing=drawing, pose_drawing=poses(1), groups=[[0, 1]])
        self.assertEqual(scene["source_count"], 2)
        self.assertTrue(scene["source_ranges"][1]["empty"])
        self.assertEqual(scene["equipment_source_map"]["E001"]["source_indices"], [0, 1])
        self.assertFalse(any(stroke["source_index"] == 1 for stroke in geometry(scene)))


if __name__ == "__main__":
    unittest.main()

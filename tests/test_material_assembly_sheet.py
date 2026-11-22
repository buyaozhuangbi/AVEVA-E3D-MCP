"""Paper layout, source attribution and endpoint precision for material sheets."""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aveva_e3d_mcp.drawing_sheet import build_drawing_sheet, normalize_sheet_options
from aveva_e3d_mcp.material_assembly_drawing import combine_material_drawing_meshes, generate_material_assembly_drawing
from aveva_e3d_mcp.material_assembly_sheet import build_material_assembly_sheet
from aveva_e3d_mcp.projection_sheet import build_projection_sheet
from test_material_assembly_drawing import CSG_ENGINE_AVAILABLE, csg_source, parameter_source
from test_primitive_csg_mesh import cut


FOOTER = "PARAMETER ASSEMBLY - NATIVE CUT SCOPE UNVERIFIED"


def material_drawing(sources=None):
    combined = combine_material_drawing_meshes(sources or [
        parameter_source("=1/101", dimensions={"XLEN": 100, "YLEN": 80, "ZLEN": 40}),
        parameter_source("=1/102", dimensions={"XLEN": 20, "YLEN": 30, "ZLEN": 15}, position=(60, 65, 40)),
    ])
    if not combined.get("success"):
        raise AssertionError(combined)
    drawing = generate_material_assembly_drawing(combined, projection_convention="right_handed", show_hidden_lines=True)
    if not drawing.get("success"):
        raise AssertionError(drawing)
    return combined, drawing


def geometry(scene, name=None):
    return [stroke for stroke in scene["strokes"] if "source_line_index" in stroke
            and (name is None or stroke["view_name"] == name)]


class MaterialAssemblySheetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combined, cls.drawing = material_drawing()

    def test_declared_projection_arrangements_align_actual_geometry_datums_and_keep_attribution(self):
        for method in ("first_angle", "third_angle"):
            with self.subTest(method=method):
                original = deepcopy(self.drawing)
                scene = build_material_assembly_sheet(self.drawing, {"projection_method": method, "scale_denominator": 10})
                self.assertEqual(self.drawing, original)
                self.assertEqual(scene["projection_convention"], "right_handed")
                self.assertEqual(scene["projection_method"], method)
                self.assertEqual(scene["geometry_sha256"], self.combined["geometry_sha256"])
                self.assertEqual(scene["source_ranges"], self.combined["source_ranges"])
                self.assertEqual(len(geometry(scene)), self.drawing["segment_count"])
                source_views = {view["name"]: view for view in self.drawing["views"]}
                actual_bounds = {}
                for name, source_view in source_views.items():
                    actual = geometry(scene, name)
                    self.assertEqual({stroke["source_line_index"] for stroke in actual}, set(range(len(source_view["lines"]))))
                    for stroke in actual:
                        line = source_view["lines"][stroke["source_line_index"]]
                        for key in ("source_index", "source_refno", "source_geometry_mode", "visibility"):
                            self.assertEqual(stroke[key], line[key])
                    points = [point for stroke in actual for point in (stroke["start"], stroke["end"])]
                    actual_bounds[name] = [[min(point[axis] for point in points), max(point[axis] for point in points)]
                                           for axis in (0, 1)]
                self.assertEqual(actual_bounds["FRONT"][0], actual_bounds["TOP"][0])
                self.assertEqual(actual_bounds["FRONT"][1], actual_bounds["SIDE"][1])
                if method == "third_angle":
                    self.assertGreater(actual_bounds["TOP"][1][0], actual_bounds["FRONT"][1][1])
                    self.assertGreater(actual_bounds["SIDE"][0][0], actual_bounds["FRONT"][0][1])
                else:
                    self.assertLess(actual_bounds["TOP"][1][1], actual_bounds["FRONT"][1][0])
                    self.assertLess(actual_bounds["SIDE"][0][1], actual_bounds["FRONT"][0][0])
                self.assertFalse(scene["source_attribution"]["vector_source_metadata_embedded"])
                self.assertNotIn("engine", scene)

    def test_footer_text_fits_all_supported_papers_and_does_not_claim_engineering_accuracy(self):
        for size in ("A3", "A4"):
            for orientation in ("landscape", "portrait"):
                for method in ("first_angle", "third_angle"):
                    with self.subTest(size=size, orientation=orientation, method=method):
                        scene = build_material_assembly_sheet(self.drawing, {
                            "paper_size": size, "orientation": orientation, "projection_method": method,
                            "title": "PUMP MATERIAL VIEWS", "drawing_number": "M-001"})
                        disclaimer = [text for text in scene["texts"] if text["role"] == "disclaimer"]
                        self.assertEqual(len(disclaimer), 1)
                        self.assertIn(FOOTER, disclaimer[0]["text"])
                        self.assertIn("NOT NATIVE DRAFT", disclaimer[0]["text"])
                        self.assertIn("FIRST ANGLE" if method == "first_angle" else "THIRD ANGLE", disclaimer[0]["text"])
                        text = disclaimer[0]
                        half = (6 * len(text["text"]) - 1) / 7 * text["height"] / 2
                        self.assertGreaterEqual(text["position"][0] - half, 10)
                        self.assertLessEqual(text["position"][0] + half, scene["paper"]["width_mm"] - 10)
                        self.assertEqual(scene["footer"], FOOTER)
                        self.assertEqual(scene["serialization_precision"]["coordinate_decimal_places"], 15)
                        self.assertFalse(scene["serialization_precision"]["engineering_accuracy_claim"])
                        self.assertEqual(scene["dimension_label_resolution_mm"], 1e-6)
                        self.assertFalse(scene["engineering_dimensions_certified"])

    def test_tiny_hidden_source_interval_survives_paper_scale_instead_of_being_dropped(self):
        drawing = deepcopy(self.drawing)
        view = drawing["views"][0]
        base = next(line for line in view["lines"] if math.dist(line["start"], line["end"]) > 1)
        stub = deepcopy(base)
        length = math.dist(base["start"], base["end"])
        fraction = 4.498929229157511e-9 / length
        stub.update(end=[base["start"][axis] + (base["end"][axis] - base["start"][axis]) * fraction for axis in (0, 1)],
                    visibility="hidden", parameter_interval=[0, fraction])
        view["lines"].append(stub)
        drawing["segment_count"] += 1
        with self.assertRaisesRegex(ValueError, "precision"):
            build_drawing_sheet(drawing, {"projection_method": "third_angle", "scale_denominator": 10})
        scene = build_material_assembly_sheet(drawing, {"projection_method": "third_angle", "scale_denominator": 10})
        result = next(stroke for stroke in geometry(scene, view["name"])
                      if stroke["source_line_index"] == len(view["lines"]) - 1)
        self.assertEqual(result["visibility"], "hidden")
        self.assertEqual(result["source_index"], stub["source_index"])
        self.assertEqual(len(geometry(scene)), drawing["segment_count"])
        encoded = lambda point: tuple(float(format(value, ".15f")) for value in point)
        self.assertNotEqual(encoded(result["start"]), encoded(result["end"]))
        height = scene["paper"]["height_mm"]
        self.assertNotEqual(encoded((result["start"][0], height - result["start"][1])),
                            encoded((result["end"][0], height - result["end"][1])))
        self.assertEqual(encoded((result["start"][0], result["start"][1])), encoded(result["start"]))
        self.assertAlmostEqual(math.dist(result["start"], result["end"]), math.dist(stub["start"], stub["end"]) / 10,
                               delta=5e-14)

    def test_layout_rejects_unrepresentable_interval_without_silently_removing_a_line(self):
        drawing = deepcopy(self.drawing)
        view = drawing["views"][0]
        base = next(line for line in view["lines"] if line["start"][1] != 0 and line["start"][1] != line["end"][1])
        stub = deepcopy(base)
        stub["end"] = [stub["start"][0], math.nextafter(stub["start"][1], base["end"][1])]
        view["lines"].append(stub)
        drawing["segment_count"] += 1
        with self.assertRaisesRegex(ValueError, "precision"):
            build_material_assembly_sheet(drawing, {"projection_method": "third_angle", "scale_denominator": 100})

    def test_legacy_generator_and_default_scenes_remain_exactly_equal_to_explicit_defaults(self):
        implicit = generate_material_assembly_drawing(self.combined, show_hidden_lines=True)
        explicit = generate_material_assembly_drawing(self.combined, show_hidden_lines=True, projection_convention="legacy")
        self.assertEqual(implicit, explicit)
        self.assertNotIn("projection_convention", implicit)
        self.assertEqual(implicit["views"][0]["toward_viewer"], [0, 1, 0])
        self.assertEqual(self.drawing["views"][0]["toward_viewer"], [0, -1, 0])
        for options in ({}, {"projection_method": "third_angle"}):
            one = build_drawing_sheet(self.drawing, options)
            two = build_drawing_sheet(self.drawing, options, coordinate_decimal_places=6)
            self.assertEqual(json.dumps(one, separators=(",", ":")), json.dumps(two, separators=(",", ":")))
        normalized = normalize_sheet_options({"projection_method": "first_angle"})
        self.assertEqual(build_projection_sheet(self.drawing, normalized),
                         build_projection_sheet(self.drawing, normalized, coordinate_decimal_places=6))

    def test_precision_option_is_strict_and_malformed_projection_or_attribution_is_rejected(self):
        for value in (None, True, False, 5, 16, 6.0, "15", {}, []):
            for builder in (build_drawing_sheet, build_projection_sheet):
                with self.subTest(builder=builder.__name__, value=value), self.assertRaisesRegex(ValueError, "invalid_coordinate_decimal_places"):
                    builder(self.drawing, normalize_sheet_options({"projection_method": "third_angle"}), coordinate_decimal_places=value)
        with self.assertRaisesRegex(ValueError, "projection_method_required"):
            build_material_assembly_sheet(self.drawing, {})
        for fault in ("legacy", "missing_view", "view_name", "axes", "line_ref", "line_index", "line_mode",
                      "line_kind", "range_mode", "range_mode_type", "hash", "whole_engine", "missing_proof"):
            drawing = deepcopy(self.drawing)
            if fault == "legacy": drawing.pop("projection_convention")
            elif fault == "missing_view": drawing["views"].pop()
            elif fault == "view_name": drawing["views"][0]["name"] = []
            elif fault == "axes": drawing["views"][0]["toward_viewer"] = [0, 1, 0]
            elif fault == "line_ref": drawing["views"][0]["lines"][0]["source_refno"] = "=9/999"
            elif fault == "line_index": drawing["views"][0]["lines"][0]["source_index"] = True
            elif fault == "line_mode": drawing["views"][0]["lines"][0]["source_geometry_mode"] = "owner_member_difference"
            elif fault == "line_kind": drawing["views"][0]["lines"][0]["kind"] = []
            elif fault == "range_mode": drawing["source_ranges"][0]["cuts_applied"] = True
            elif fault == "range_mode_type": drawing["source_ranges"][0]["geometry_mode"] = []
            elif fault == "hash": drawing["source_ranges"][0]["source_mesh_sha256"] = "0" * 64
            elif fault == "missing_proof": drawing.pop("geometry_hash_scope")
            else: drawing["engine"] = {"name": "manifold3d"}
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                build_material_assembly_sheet(drawing, {"projection_method": "third_angle"})
        for value in (True, None, "first_angle"):
            result = generate_material_assembly_drawing(self.combined, projection_convention=value)
            self.assertFalse(result["success"])
            self.assertEqual(result["error_code"], "invalid_projection_convention")
            self.assertEqual(result["views"], [])


@unittest.skipUnless(CSG_ENGINE_AVAILABLE, "Install the geometry extra for material CSG sheet tests.")
class MaterialAssemblySheetCavityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cavity = csg_source("=1/501", cutters=[cut("BOX", {"XLEN": 20, "YLEN": 20, "ZLEN": 10})])
        cls.empty = csg_source("=1/502", cutters=[cut("BOX", {"XLEN": 200, "YLEN": 200, "ZLEN": 100})])

    def test_cavity_and_empty_source_proofs_remain_unchanged_in_printable_scene(self):
        combined, drawing = material_drawing([self.cavity, self.empty, parameter_source("=1/503", position=(150, 0, 0))])
        scene = build_material_assembly_sheet(drawing, {"projection_method": "third_angle"})
        self.assertTrue(scene["cuts_applied"])
        self.assertTrue(scene["source_parameter_boolean_evaluated"])
        self.assertFalse(scene["validation"]["valid"])
        self.assertEqual(scene["validation"], combined["validation"])
        self.assertEqual(scene["material_boundary_validation"], combined["material_boundary_validation"])
        self.assertEqual(scene["source_ranges"], combined["source_ranges"])
        self.assertTrue(scene["source_ranges"][1]["empty"])
        self.assertNotIn(1, {stroke["source_index"] for stroke in geometry(scene)})
        self.assertNotIn("engine", scene)
        scene["source_ranges"][0]["engine"]["version"] = "changed-copy"
        self.assertNotEqual(scene["source_ranges"][0]["engine"], drawing["source_ranges"][0]["engine"])

    def test_all_empty_drawing_keeps_right_handed_metadata_and_is_rejected_for_sheet(self):
        combined, drawing = material_drawing([self.empty])
        self.assertTrue(combined["empty"])
        self.assertEqual(drawing["projection_convention"], "right_handed")
        self.assertEqual(drawing["views"], [])
        with self.assertRaisesRegex(ValueError, "nonempty_material_assembly_drawing_required"):
            build_material_assembly_sheet(drawing, {"projection_method": "third_angle"})


if __name__ == "__main__":
    unittest.main()

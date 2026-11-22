"""Independent depth/basis, provenance and paper-export outline checks."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.drawing_sheet_render import rasterize_drawing_sheet_png, render_drawing_sheet_svg
from aveva_e3d_mcp.material_assembly_drawing import combine_material_drawing_meshes
from aveva_e3d_mcp.nozzle_axis_sheet import build_nozzle_axis_sheets
from aveva_e3d_mcp.nozzle_outline_sheet import NozzleOutlineScaleDoesNotFit, build_nozzle_outline_sheets
from aveva_e3d_mcp.orthographic_dxf import export_drawing_sheet_dxf
from aveva_e3d_mcp.primitive_mesh import derive_primitive_mesh
from test_material_assembly_drawing import csg_source, material_source
from test_nozzle_axis_sheet import dxf_entities, equipment_pose, many_points, nozzle_drawing, png_pixels, world_vector
from test_primitive_csg_mesh import CSG_ENGINE_AVAILABLE, csg_native, cut, sync_target


MODULE = "aveva_e3d_mcp.nozzle_outline_sheet"


def material_box(pose, refno="=1/10", *, center=(0, 0, 0), dimensions=(400, 200, 100)):
    native = csg_native("BOX", dict(zip(("XLEN", "YLEN", "ZLEN"), dimensions)), cutters=[],
        position=[a + b for a, b in zip(pose["position"], world_vector(center, pose))],
        axes=[pose["orientation"][name] for name in ("x_axis", "y_axis", "z_axis")])
    native["element"].update(refno=refno, name=f"/OUTLINE-{refno.split('/')[1]}")
    native["owner_member_refnos"] = [refno]
    sync_target(native)
    mesh = derive_primitive_mesh(native, coordinate_space="WORLD", radial_segments=24)
    if mesh.get("success") is not True:
        raise AssertionError(mesh)
    return material_source(native, mesh, "parameter_mesh")


def combined_boxes(pose, *specs):
    result = combine_material_drawing_meshes([material_box(pose, f"=1/{10 + index}", **spec)
                                            for index, spec in enumerate(specs or [{}])])
    if result.get("success") is not True:
        raise AssertionError(result)
    return result


def rotated_pose():
    # Arbitrary 3D proper rotation, independently formed as Rz(31) Ry(23).
    a, b = math.radians(31), math.radians(23)
    return equipment_pose(position=[-400, 700, 1500], axes={
        "x_axis": [math.cos(a) * math.cos(b), math.sin(a) * math.cos(b), -math.sin(b)],
        "y_axis": [-math.sin(a), math.cos(a), 0],
        "z_axis": [math.cos(a) * math.sin(b), math.sin(a) * math.sin(b), math.cos(b)]})


def projection_lines(result, name, source_index=None, visibility="visible"):
    view = next(item for item in result["projection"]["views"] if item["name"] == name)
    return [line for line in view["lines"] if (source_index is None or line["source_index"] == source_index)
            and line["visibility"] == visibility]


class NozzleOutlineSheetTests(unittest.TestCase):
    def build(self, pose=None, material=None, drawing=None, options=None, **kwargs):
        pose = pose or equipment_pose()
        return build_nozzle_outline_sheets(drawing or nozzle_drawing(pose=pose), pose,
            material or combined_boxes(pose), options or {},
            axial_axis=kwargs.pop("axial_axis", "Z"), zero_axis=kwargs.pop("zero_axis", "X"), **kwargs)

    def test_world_arrays_hash_and_material_proof_stay_world_while_projection_is_explicitly_local(self):
        pose = rotated_pose()
        material = combined_boxes(pose)
        drawing = nozzle_drawing(pose=pose)
        before = deepcopy((material, drawing, pose))
        result = self.build(pose, material, drawing)
        projection, scene = result["projection"], result["sheets"][0]
        self.assertEqual((material, drawing, pose), before)
        self.assertEqual(projection["source_material"], material)
        self.assertEqual(projection["geometry_sha256"], material["geometry_sha256"])
        self.assertEqual(projection["source_material"]["coordinate_space"], "WORLD")
        self.assertEqual(projection["coordinate_space"], "equipment local projection")
        self.assertEqual([view["name"] for view in projection["views"]], ["RADIAL", "AXIAL_ELEVATION"])
        self.assertEqual([view["name"] for view in scene["views"]], ["TOP", "FRONT"])
        self.assertTrue(all(view["renderer_view_identifier_only"] for view in scene["views"]))
        self.assertEqual(scene["model_coordinate_frame"], "native equipment local axes")
        self.assertFalse(scene["whole_equipment_geometry_verified"])
        self.assertFalse(scene["explicit_source_membership_verified"])
        self.assertFalse(scene["equipment_frame"]["nozzle_ownership_verified"])
        self.assertFalse(scene["equipment_frame"]["physical_center_verified"])
        self.assertFalse(scene["native_cut_application_verified"])
        self.assertTrue(any("SELECTED PARAMETER OUTLINE" in text["text"] for text in scene["texts"]))
        for row, source in zip(scene["source_rows"], drawing["nozzles"]):
            self.assertEqual(row["position"], source["position"])
            self.assertEqual(row["direction"], source["direction"])

    def test_all_six_axis_pairs_project_an_arbitrary_rotated_equipment_with_correct_bounds(self):
        pose = rotated_pose()
        material = combined_boxes(pose, {"center": (10, 20, 30), "dimensions": (200, 120, 80)})
        components = {"X": (10, 100), "Y": (20, 60), "Z": (30, 40)}
        for axial, zero, remaining, sign in (("Z", "X", "Y", 1), ("Z", "Y", "X", -1),
                ("X", "Y", "Z", 1), ("X", "Z", "Y", -1),
                ("Y", "X", "Z", -1), ("Y", "Z", "X", 1)):
            with self.subTest(axial=axial, zero=zero):
                result = self.build(pose, material, axial_axis=axial, zero_axis=zero)
                for view, vertical in zip(result["projection"]["views"], (remaining, axial)):
                    for axis, name in enumerate((zero, vertical)):
                        center, radius = components[name]
                        if view["name"] == "RADIAL" and axis == 1:
                            center *= sign
                        self.assertAlmostEqual(view["bounds"]["min"][axis], center - radius, places=9)
                        self.assertAlmostEqual(view["bounds"]["max"][axis], center + radius, places=9)
                    horizontal, up, toward = (view[key] for key in
                        ("horizontal_axis_world", "vertical_axis_world", "toward_viewer_world"))
                    cross = [horizontal[1] * up[2] - horizontal[2] * up[1],
                             horizontal[2] * up[0] - horizontal[0] * up[2],
                             horizontal[0] * up[1] - horizontal[1] * up[0]]
                    for expected, actual in zip(cross, toward):
                        self.assertAlmostEqual(actual, expected, places=12)
                self.assertTrue(any(0 < abs(value) < 1 for value in result["projection"]["basis_world"]["u"]))

    def test_radial_visibility_uses_positive_n_and_hides_a_different_source_globally(self):
        for pose in (equipment_pose(), rotated_pose()):
            with self.subTest(pose=pose["position"]):
                material = combined_boxes(pose,
                    {"center": (0, 0, 10), "dimensions": (20, 20, 2)},
                    {"center": (0, 0, -10), "dimensions": (10, 10, 2)})
                result = self.build(pose, material)
                self.assertTrue(projection_lines(result, "RADIAL", 0))
                self.assertEqual(projection_lines(result, "RADIAL", 1), [])
                self.assertTrue(projection_lines(result, "AXIAL_ELEVATION", 1))
                no_removal = self.build(pose, material, hidden_line_removal=False)
                self.assertTrue(projection_lines(no_removal, "RADIAL", 1))
                dashed = self.build(pose, material, show_hidden_lines=True)
                self.assertTrue(projection_lines(dashed, "RADIAL", 1, "hidden"))
                self.assertTrue(all(view["visibility"]["success"] for view in result["projection"]["views"]))

    def test_axial_visibility_uses_negative_v_instead_of_legacy_front_depth(self):
        for pose in (equipment_pose(), rotated_pose()):
            material = combined_boxes(pose,
                {"center": (0, -10, 0), "dimensions": (20, 2, 20)},
                {"center": (0, 10, 0), "dimensions": (10, 2, 10)})
            result = self.build(pose, material)
            self.assertTrue(projection_lines(result, "AXIAL_ELEVATION", 0))
            self.assertEqual(projection_lines(result, "AXIAL_ELEVATION", 1), [])
            self.assertTrue(projection_lines(result, "RADIAL", 1))
            self.assertEqual(result["projection"]["views"][1]["toward_viewer_world"],
                             [-value for value in result["projection"]["basis_world"]["v"]])

    def test_partial_global_occlusion_retains_real_edge_intervals_and_paper_source_mapping(self):
        pose = equipment_pose()
        material = combined_boxes(pose,
            {"center": (-5, 0, 10), "dimensions": (10, 30, 2)},
            {"center": (0, 0, 0), "dimensions": (30, 20, 2)})
        result = self.build(pose, material)
        partial = [line for line in projection_lines(result, "RADIAL", 1) if line["parameter_interval"] != [0, 1]]
        self.assertTrue(partial)
        scene = result["sheets"][0]
        geometry = [stroke for stroke in scene["strokes"] if stroke.get("annotation_kind") == "equipment_material_outline"]
        self.assertEqual(len(geometry), result["projection"]["segment_count"])
        last_geometry = max(scene["strokes"].index(stroke) for stroke in geometry)
        first_point = min(index for index, stroke in enumerate(scene["strokes"]) if stroke.get("annotation_kind") == "point_position_symbol")
        self.assertLess(last_geometry, first_point)
        for stroke in geometry:
            view = next(view for view in result["projection"]["views"] if view["name"] == stroke["semantic_view"])
            line = view["lines"][stroke["source_line_index"]]
            for field in ("edge", "source_edge", "source_refno", "source_geometry_mode", "source_mesh_sha256", "parameter_interval", "incident_faces", "visibility"):
                self.assertEqual(stroke[field], line[field])
            self.assertEqual(stroke["model_start"], line["start"])
            self.assertEqual(stroke["model_end"], line["end"])
        self.assertEqual(scene["coverage"]["input_line_count"], len(geometry))
        self.assertFalse(scene["coverage"]["cropping_applied"])
        self.assertFalse(scene["coverage"]["visibility_recomputed"])

    def test_coincident_source_boundaries_keep_each_source_attribution_without_welding(self):
        pose = equipment_pose()
        material = combined_boxes(pose, {}, {})
        result = self.build(pose, material)
        for name in ("RADIAL", "AXIAL_ELEVATION"):
            self.assertTrue(projection_lines(result, name, 0))
            self.assertTrue(projection_lines(result, name, 1))
        self.assertFalse(result["projection"]["vertices_welded"])
        self.assertFalse(result["projection"]["boolean_union_applied"])

    def test_world_rigid_transform_preserves_local_shapes_visibility_and_nozzle_coordinates(self):
        specs = ({"center": (-5, 0, 10), "dimensions": (10, 30, 2)},
                 {"center": (0, 0, 0), "dimensions": (30, 20, 2)})
        first_pose, second_pose = equipment_pose(position=[0, 0, 0]), rotated_pose()
        first = self.build(first_pose, combined_boxes(first_pose, *specs))
        second = self.build(second_pose, combined_boxes(second_pose, *specs))
        for view, transformed in zip(first["projection"]["views"], second["projection"]["views"]):
            self.assertEqual(view["line_count"], transformed["line_count"])
            for line, changed in zip(view["lines"], transformed["lines"]):
                for field in ("edge", "kind", "source_index", "visibility"):
                    self.assertEqual(line[field], changed[field])
                for field in ("start", "end", "parameter_interval"):
                    for a, b in zip(line[field], changed[field]):
                        self.assertAlmostEqual(a, b, places=9)
        for row, transformed in zip(first["sheets"][0]["source_rows"], second["sheets"][0]["source_rows"]):
            for field in ("u", "v", "h"):
                self.assertAlmostEqual(row["equipment_coordinates_mm"][field], transformed["equipment_coordinates_mm"][field], places=9)

    def test_geometry_that_collapses_at_fifteen_paper_decimals_is_refused(self):
        pose = equipment_pose(position=[0, 0, 0])
        material = combined_boxes(pose, {"dimensions": (1e-8, 1e-8, 1e-8)})
        drawing = nozzle_drawing([[1e9, 1e9, 1e9]], pose=pose)
        with self.assertRaisesRegex(ValueError, "geometry_below_precision"):
            self.build(pose, material, drawing)

    def profile_fixture(self, pose=None):
        pose = pose or equipment_pose(position=[0, 0, 0])
        points = [[0, 0, 800], [0, 0, 700], [150, 0, -800], [150, 0, -700]]
        drawing = nozzle_drawing(points, [[0, 0, 1], [0, 0, -1], [0, 0, -1], [0, 0, 1]], pose=pose)
        profiles = []
        for index, (outer, neck, thickness) in enumerate(((210, 89, 29), (254, 114, 32))):
            refno, name = f"=1/{200 + index}", f"/V-100/N{index + 1}"
            for number, row in enumerate(drawing["nozzles"][index * 2:index * 2 + 2], 1):
                row.update(resolved_refno=refno, refno=name, element_name=name, point_number=number)
            profiles.append({"resolved_refno": refno, "axis_start_point_number": 2,
                "axis_end_point_number": 1, "stations": [{"distance_mm": d, "outer_diameter_mm": od}
                    for d, od in ((0, neck), (100 - thickness, neck), (100 - thickness, outer), (100, outer))],
                "dimension_evidence": {"source": "native catalogue", "scope": "bare catalogue excludes insulation",
                    "catalogue_parameters": [80 if index == 0 else 100, "FBD", outer, thickness, neck]},
                "nominal_bore_mm": 80 if index == 0 else 100})
        return pose, drawing, profiles

    def test_profiles_draw_actual_neck_and_flange_outer_diameters_with_one_label_per_nozzle(self):
        pose, drawing, profiles = self.profile_fixture()
        result = self.build(pose=pose, drawing=drawing, nozzle_profiles=profiles)
        projection, scene = result["projection"], result["sheets"][0]
        self.assertEqual(len(scene["source_rows"]), 4)
        self.assertEqual([row["id"] for row in scene["nozzle_rows"]], ["N1", "N2"])
        self.assertEqual(len(scene["callouts"]), 4)
        self.assertTrue(scene["native_point_labels_grouped_by_resolved_nozzle"])
        self.assertFalse(scene["fixed_length_direction_arrows"])
        self.assertFalse(scene["radial_origin_rays"])
        self.assertFalse(any(stroke.get("annotation_kind") in {"position_radius", "point_direction_symbol", "point_leader"}
                             for stroke in scene["strokes"]))
        self.assertEqual(scene["counts"]["material_sources"], 1)
        self.assertEqual(len(scene["outline_source_ranges"]), 3)
        self.assertEqual(scene["outline_source_ranges"][1]["source_kind"], "nozzle_profile")
        self.assertTrue(any(stroke.get("source_kind") == "nozzle_profile" for stroke in scene["strokes"]))
        self.assertIn("source_index indexes outline_source_ranges", scene["source_attribution"]["source_line_index_scope"])
        self.assertIn("original selected material", scene["source_attribution"]["selected_material_membership_scope"])
        self.assertTrue(any("MAIN GEOMETRY TO SCALE" in item["text"] for item in scene["texts"]))
        self.assertFalse(any("SYMBOLS NOT TO SCALE" in item["text"] for item in scene["texts"]))
        for profile, diameter in zip(projection["nozzle_profiles"], (210, 254)):
            self.assertFalse(profile["bore_used_as_outer_diameter"])
            vertices = profile["derived_mesh"]["vertices"]
            self.assertAlmostEqual(max(point[0] for point in vertices) - min(point[0] for point in vertices), diameter)
            self.assertEqual(profile["axis_length_mm"], 100)
            self.assertTrue(profile["derived_mesh"]["validation"]["valid"])
        for row, raw in zip(scene["source_rows"], drawing["nozzles"]):
            for field in ("position", "direction", "point_number", "resolved_refno"):
                self.assertEqual(row[field], raw[field])

    def test_profiles_and_material_use_one_real_engineering_scale_in_both_views(self):
        pose, drawing, profiles = self.profile_fixture()
        scene = self.build(pose=pose, drawing=drawing, nozzle_profiles=profiles,
            options={"scale_denominator": 25})["sheets"][0]
        self.assertEqual(scene["scale_denominator"], 25)
        self.assertEqual(scene["scale_mode"], "explicit")
        for stroke in scene["strokes"]:
            if stroke.get("annotation_kind") == "equipment_material_outline":
                self.assertAlmostEqual(math.dist(stroke["start"], stroke["end"]),
                                       math.dist(stroke["model_start"], stroke["model_end"]) / 25, places=12)
        for size in ("A3", "A4"):
            scene = self.build(pose=pose, drawing=drawing, nozzle_profiles=profiles, options={"size": size})["sheets"][0]
            denominator = scene["scale_denominator"]
            self.assertTrue(any(abs(denominator / 10 ** exponent - value) < 1e-12
                                for exponent in range(-6, 10) for value in (1, 2, 2.5, 5)))
        for size in ("A3", "A4"):
            with self.subTest(size=size):
                options = {"size": size, "scale_denominator": 1}
                with self.assertRaisesRegex(NozzleOutlineScaleDoesNotFit, "explicit_scale_does_not_fit") as error:
                    self.build(pose=pose, drawing=drawing, nozzle_profiles=profiles, options=options)
                diagnostic = error.exception.diagnostics
                minimum = diagnostic["minimum_scale_denominator"]
                recommended = diagnostic["recommended_scale_denominator"]
                self.assertEqual(diagnostic["requested_scale_denominator"], 1)
                self.assertGreater(minimum, 1)
                self.assertGreaterEqual(recommended, minimum)
                options["scale_denominator"] = recommended
                retried = self.build(pose=pose, drawing=drawing, nozzle_profiles=profiles, options=options)
                automatic = self.build(pose=pose, drawing=drawing, nozzle_profiles=profiles, options={"size": size})
                for page, auto_page in zip(retried["sheets"], automatic["sheets"], strict=True):
                    self.assertEqual(page["scale_denominator"], recommended)
                    self.assertEqual(page["strokes"], auto_page["strokes"])
                    self.assertEqual(page["coverage"]["input_line_count"], page["counts"]["geometry_strokes"])
        for invalid in (None, True, 0, -1, math.nan):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.build(pose=pose, drawing=drawing, nozzle_profiles=profiles, options={"scale_denominator": invalid})
        with self.assertRaises(ValueError):
            build_nozzle_axis_sheets(drawing, pose, {"scale_denominator": 25}, axial_axis="Z", zero_axis="X")

    def test_fully_occluded_nozzle_is_marked_only_in_the_affected_view(self):
        pose, drawing, profiles = self.profile_fixture()
        material = combined_boxes(pose, {"dimensions": (700, 700, 100)})

        def build(**kwargs):
            return self.build(pose, material, drawing, nozzle_profiles=profiles, **kwargs)

        def get_view(result, name):
            return next(view for view in result["projection"]["views"] if view["name"] == name)

        def observations(result, name):
            return {row["nozzle_label"]: row for row in get_view(result, name)["nozzle_profile_visibility"]}

        solid, dashed, unchecked = build(), build(show_hidden_lines=True), build(hidden_line_removal=False)
        radial = observations(solid, "RADIAL")
        axial = observations(solid, "AXIAL_ELEVATION")
        self.assertEqual(radial["N1"]["status"], "visible")
        self.assertEqual(radial["N2"]["status"], "fully_occluded")
        self.assertGreater(radial["N2"]["candidate_edge_count"], 0)
        self.assertEqual(radial["N2"]["candidate_edge_count"], radial["N2"]["fully_hidden_edge_count"])
        self.assertEqual(radial["N2"]["visible_stroke_count"], 0)
        self.assertEqual(axial["N2"]["status"], "visible")
        self.assertEqual(radial, observations(dashed, "RADIAL"))
        no_check = observations(unchecked, "RADIAL")["N2"]
        self.assertEqual(no_check["status"], "not_evaluated")
        self.assertFalse(no_check["occlusion_checked"])
        self.assertIsNone(no_check["hidden_interval_count"])
        index = radial["N2"]["source_index"]
        self.assertEqual(projection_lines(solid, "RADIAL", index), [])
        self.assertEqual(projection_lines(solid, "RADIAL", index, "hidden"), [])
        self.assertEqual(len(projection_lines(dashed, "RADIAL", index, "hidden")), radial["N2"]["hidden_interval_count"])

        scene = solid["sheets"][0]
        labels = {(item["semantic_view"], item["nozzle_id"]): item["label_text"] for item in scene["callouts"]}
        self.assertEqual(labels, {("RADIAL", "N1"): "N1", ("RADIAL", "N2"): "N2 HID",
                                  ("AXIAL_ELEVATION", "N1"): "N1", ("AXIAL_ELEVATION", "N2"): "N2"})
        svg = render_drawing_sheet_svg(scene)
        self.assertIn(">N2 HID</text>", svg)
        self.assertIn("HID: OCCLUDED", svg)
        self.assertIn("N2 HID", export_drawing_sheet_dxf(scene))
        self.assertTrue(rasterize_drawing_sheet_png(scene, width=800, height=570).startswith(b"\x89PNG"))

    def test_profiles_preserve_groups_across_pages_and_repeat_full_global_background(self):
        pose, drawing, profiles = self.profile_fixture()
        result = self.build(pose=pose, drawing=drawing, nozzle_profiles=profiles, options={"points_per_page": 8})
        scene = result["sheets"][0]
        self.assertTrue(scene["coverage"]["all_selected_material_repeated_on_each_page"])
        self.assertEqual(scene["coverage"]["input_line_count"], scene["counts"]["geometry_strokes"])
        geometries = [s for s in scene["strokes"] if s.get("source_kind") == "nozzle_profile"]
        for stroke in geometries:
            source = scene["outline_source_ranges"][stroke["source_index"]]
            self.assertEqual(stroke["source_refno"], source["refno"])
            self.assertTrue(all(source["vertex_range"][0] <= v < source["vertex_range"][1] for v in stroke["edge"]))
        self.assertEqual(scene["source_proof_sha256"], result["projection"]["source_proof_sha256"])

    def test_profile_dimensions_need_independent_evidence_and_exact_native_axis_endpoints(self):
        pose, drawing, profiles = self.profile_fixture()
        faults = (lambda value: value[0].pop("dimension_evidence"),
                  lambda value: value[0].update(resolved_refno="=1/999"),
                  lambda value: value[0].update(axis_start_point_number=1),
                  lambda value: value[0]["stations"][-1].update(distance_mm=101),
                  lambda value: value[0]["stations"][0].update(outer_diameter_mm=0),
                  lambda value: value[0]["stations"][1].update(distance_mm=-1),
                  lambda value: value.pop(), lambda value: value.append(deepcopy(value[0])))
        for change in faults:
            altered = deepcopy(profiles)
            change(altered)
            with self.assertRaises(ValueError):
                self.build(pose=pose, drawing=drawing, nozzle_profiles=altered)
        bad_direction = deepcopy(drawing)
        bad_direction["nozzles"][0]["direction"].update(x=1, y=0, z=0)
        with self.assertRaises(ValueError):
            self.build(pose=pose, drawing=bad_direction, nozzle_profiles=profiles)

    def test_profiles_are_invariant_under_arbitrary_world_rigid_rotation(self):
        first_pose, first_drawing, first_profiles = self.profile_fixture()
        second_pose, second_drawing, second_profiles = self.profile_fixture(rotated_pose())
        first = self.build(first_pose, drawing=first_drawing, nozzle_profiles=first_profiles)
        second = self.build(second_pose, drawing=second_drawing, nozzle_profiles=second_profiles)
        self.assertEqual(first["sheets"][0]["scale_denominator"], second["sheets"][0]["scale_denominator"])
        for view, other in zip(first["projection"]["views"], second["projection"]["views"]):
            for key in ("min", "max"):
                for a, b in zip(view["bounds"][key], other["bounds"][key]):
                    self.assertAlmostEqual(a, b, places=9)
            # Native WORLD-to-basis roundoff can retain another coincident
            # source edge at a depth boundary. Compare the geometric trace,
            # rather than requiring identical multiplicity of those edges.
            def point_distance(point, line):
                a, b = line["start"], line["end"]
                delta = [b[axis] - a[axis] for axis in (0, 1)]
                parameter = max(0, min(1, sum((point[axis] - a[axis]) * delta[axis] for axis in (0, 1)) / sum(value * value for value in delta)))
                return math.dist(point, [a[axis] + delta[axis] * parameter for axis in (0, 1)])
            for source, target in ((view, other), (other, view)):
                for line in source["lines"]:
                    candidates = [item for item in target["lines"] if item["source_index"] == line["source_index"]]
                    for parameter in (0, .25, .5, .75, 1):
                        point = [line["start"][axis] + (line["end"][axis] - line["start"][axis]) * parameter for axis in (0, 1)]
                        self.assertLess(min(point_distance(point, item) for item in candidates), 1e-8)

    def test_profile_exports_retain_every_physical_stroke_at_fifteen_decimals(self):
        pose, drawing, profiles = self.profile_fixture()
        scene = self.build(pose=pose, drawing=drawing, nozzle_profiles=profiles)["sheets"][0]
        svg = ET.fromstring(render_drawing_sheet_svg(scene, coordinate_decimal_places=15))
        dxf = dxf_entities(export_drawing_sheet_dxf(scene, coordinate_decimal_places=15))
        self.assertEqual(len(svg.findall("{http://www.w3.org/2000/svg}path")), len(scene["strokes"]))
        self.assertEqual(len([item for item in dxf if item["kind"] == "LINE"]), len(scene["strokes"]))
        self.assertTrue(rasterize_drawing_sheet_png(scene, width=1400, height=990).startswith(b"\x89PNG"))

    def test_profiles_adapt_to_a_declared_chord_bound_without_changing_their_dimensions(self):
        pose, drawing, profiles = self.profile_fixture()
        result = self.build(pose=pose, drawing=drawing, nozzle_profiles=profiles, chord_tolerance_mm=.5,
                            options={"scale_denominator": 25})
        for profile, evidence in zip(result["projection"]["nozzle_profiles"], result["sheets"][0]["nozzle_profile_approximation"]):
            self.assertEqual(profile["radial_segments"] % 4, 0)
            self.assertLessEqual(profile["max_radial_chord_error_mm"], .5)
            self.assertLessEqual(evidence["max_paper_chord_error_mm"], .02)
            radius = max(station["outer_diameter_mm"] for station in profile["stations"]) / 2
            self.assertEqual(profile["max_radial_chord_error_mm"], radius * (1 - math.cos(math.pi / profile["radial_segments"])))
        with self.assertRaisesRegex(ValueError, "chord_tolerance_exceeds_segment_limit"):
            self.build(pose=pose, drawing=drawing, nozzle_profiles=profiles, chord_tolerance_mm=1e-5)
        for invalid in (0, -1, True, math.nan):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.build(pose=pose, drawing=drawing, nozzle_profiles=profiles, chord_tolerance_mm=invalid)

    def test_geometry_points_and_native_pos_share_one_plan_on_every_page(self):
        pose = equipment_pose()
        material = combined_boxes(pose, {"center": (800, -300, 200), "dimensions": (400, 600, 500)})
        drawing = nozzle_drawing([[-1000, 0, -600], *many_points(17)], pose=pose)
        for size in ("A3", "A4"):
            for capacity in (8, 16):
                result = self.build(pose, material, drawing, {"size": size, "points_per_page": capacity})
                first = result["sheets"][0]
                radial_bounds = first["shared_layout"]["model_bounds"]["TOP"]
                self.assertEqual([radial_bounds["min"][0], radial_bounds["max"][0]], [-1000.0, 1000.0])
                self.assertAlmostEqual(radial_bounds["min"][1], -700 * math.cos(math.pi / 34), places=9)
                self.assertAlmostEqual(radial_bounds["max"][1], 700 * math.cos(math.pi / 34), places=9)
                self.assertEqual(first["shared_layout"]["model_bounds"]["FRONT"]["min"], [-1000.0, -600.0])
                self.assertEqual(first["shared_layout"]["model_bounds"]["FRONT"]["max"], [1000.0, 450.0])
                for page in result["sheets"]:
                    self.assertEqual(page["shared_layout"], first["shared_layout"])
                    self.assertEqual(page["scale_denominator"], first["scale_denominator"])
                    for view, first_view in zip(page["views"], first["views"]):
                        self.assertEqual(view["transform"], first_view["transform"])
                        self.assertEqual(view["geometry_stroke_range"], first_view["geometry_stroke_range"])
                    self.assertTrue(page["coverage"]["all_selected_material_repeated_on_each_page"])

    def test_visibility_budget_failure_does_not_return_partial_projection_or_sheets(self):
        with patch(f"{MODULE}.projected_edge_visibility", return_value={"success": False,
                "available": False, "complete": False, "error_code": "visibility_work_budget_exceeded"}):
            with self.assertRaisesRegex(ValueError, "visibility_work_budget_exceeded"):
                self.build()

    def test_invalid_material_frame_hash_and_visibility_options_are_refused(self):
        pose = equipment_pose()
        material = combined_boxes(pose)
        for change in (lambda value: value.update(coordinate_space="LOCAL"),
                       lambda value: value["vertices"][0].__setitem__(0, 123),
                       lambda value: value["source_ranges"][0].update(source_mesh_sha256="0" * 64)):
            invalid = deepcopy(material)
            change(invalid)
            with self.assertRaises(ValueError):
                self.build(pose, invalid)
        for options in ({"feature_angle_degrees": True}, {"feature_angle_degrees": math.nan},
                        {"hidden_line_removal": 1}, {"show_hidden_lines": 1},
                        {"hidden_line_removal": False, "show_hidden_lines": True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.build(**options)

    def test_projection_cannot_be_reused_under_another_pose_or_with_changed_bounds_lines_or_sources(self):
        pose = equipment_pose()
        drawing = nozzle_drawing(pose=pose)
        projection = self.build(pose=pose, drawing=drawing)["projection"]
        faults = (lambda value: value["views"][0]["bounds"]["max"].__setitem__(0, 1),
                  lambda value: value["views"][0]["lines"][0]["end"].__setitem__(0, 1),
                  lambda value: value["views"][0]["lines"][0].update(source_refno="=1/999"),
                  lambda value: value["views"][1].update(toward_viewer_world=[0, 1, 0]),
                  lambda value: value["source_material"]["vertices"][0].__setitem__(0, 1))
        for change in faults:
            invalid = deepcopy(projection)
            change(invalid)
            with self.assertRaises(ValueError):
                build_nozzle_axis_sheets(drawing, pose, {}, axial_axis="Z", zero_axis="X", outline_projection=invalid)
        other = deepcopy(pose)
        other["position"][0] += 10
        with self.assertRaisesRegex(ValueError, "projection_required"):
            build_nozzle_axis_sheets(drawing, other, {}, axial_axis="Z", zero_axis="X", outline_projection=projection)

    @unittest.skipUnless(CSG_ENGINE_AVAILABLE, "Mesh64 geometry extra is required")
    def test_verified_inward_cavity_shell_receipt_is_preserved_without_outward_reinterpretation(self):
        source = csg_source(cutters=[cut(dimensions={"DIAM": 20, "HEIG": 20})])
        material = combine_material_drawing_meshes([source])
        self.assertTrue(material["material_boundary_validation"]["cavity_shells"])
        pose = equipment_pose(position=[0, 0, 0])
        result = self.build(pose, material)
        self.assertEqual(result["projection"]["source_material"], material)
        self.assertEqual(result["sheets"][0]["material_boundary_validation"], material["material_boundary_validation"])
        self.assertTrue(result["projection"]["cuts_applied"])
        self.assertFalse(result["projection"]["native_cut_application_verified"])

    @unittest.skipUnless(CSG_ENGINE_AVAILABLE, "Mesh64 geometry extra is required")
    def test_all_empty_material_cannot_supply_a_background_outline(self):
        material = combine_material_drawing_meshes([csg_source(cutters=[cut("BOX",
            {"XLEN": 200, "YLEN": 200, "ZLEN": 200})])])
        self.assertTrue(material["empty"])
        with self.assertRaisesRegex(ValueError, "nonempty_material_required"):
            self.build(material=material)

    def test_svg_dxf_and_png_include_the_same_background_without_losing_geometry(self):
        for size in ("A3", "A4"):
            scene = self.build(options={"size": size})["sheets"][0]
            svg = ET.fromstring(render_drawing_sheet_svg(scene, coordinate_decimal_places=15))
            paths = svg.findall("{http://www.w3.org/2000/svg}path")
            lines = [record for record in dxf_entities(export_drawing_sheet_dxf(scene, coordinate_decimal_places=15)) if record["kind"] == "LINE"]
            self.assertEqual(len(paths), len(scene["strokes"]))
            self.assertEqual(len(lines), len(scene["strokes"]))
            for stroke, path, line in zip(scene["strokes"], paths, lines):
                expected = [*stroke["start"], *stroke["end"]]
                for actual, target in zip([float(line[key]) for key in ("10", "20", "11", "21")], expected):
                    self.assertAlmostEqual(actual, target, places=12)
                parts = path.attrib["d"].split()
                for actual, target in zip(map(float, (parts[1], parts[2], parts[4], parts[5])),
                        [expected[0], scene["paper"]["height_mm"] - expected[1], expected[2], scene["paper"]["height_mm"] - expected[3]]):
                    self.assertAlmostEqual(actual, target, places=12)
            width, height, pixel = png_pixels(rasterize_drawing_sheet_png(scene, width=1400, height=990))
            paper_w, paper_h = scene["paper_size_mm"]
            scale = min((width - 2) / paper_w, (height - 2) / paper_h)
            ox, oy = (width - paper_w * scale) / 2, (height - paper_h * scale) / 2
            for stroke in scene["strokes"][:scene["counts"]["geometry_strokes"]]:
                point = [(a + b) / 2 for a, b in zip(stroke["start"], stroke["end"])]
                px, py = round(ox + point[0] * scale), round(oy + (paper_h - point[1]) * scale)
                self.assertTrue(any(pixel(px + dx, py + dy) == (23, 33, 43) for dx in (-1, 0, 1) for dy in (-1, 0, 1)))

    def test_outline_inputs_pages_and_default_axis_result_are_independent(self):
        pose = equipment_pose()
        material = combined_boxes(pose)
        drawing = nozzle_drawing(many_points(9), pose=pose)
        default = build_nozzle_axis_sheets(drawing, pose, {}, axial_axis="Z", zero_axis="X")
        result = self.build(pose, material, drawing)
        result["projection"]["source_material"]["vertices"][0][0] = 999
        result["sheets"][0]["source_ranges"][0]["refno"] = "=1/999"
        self.assertEqual(result["sheets"][1]["source_ranges"][0]["refno"], "=1/10")
        self.assertNotEqual(material["vertices"][0][0], 999)
        self.assertEqual(build_nozzle_axis_sheets(drawing, pose, {}, axial_axis="Z", zero_axis="X"), default)

    def test_point_only_scene_svg_dxf_png_match_prechange_bytes(self):
        # These hashes were recorded before adding the optional outline path.
        expected = {
            "A3-0": ["9fb01bf39278efeb8de07806cb779b9689e2a9180357b308e124796cbf1bb9d9", "015eade4d397d0b1abc425760bfa60b05a53b7c3a0bb386ed7edad5458df088a", "654ed76d3061c8fb51c73fa322315ea7cb0903d94f3ed96dc6b78fdfe5bf3165", "db6a083314578a224af5616dddd09e878c428ae0407630d8371c76eacad4bce7"],
            "A4-0": ["65dfe27eaf23eaf2e986f22f0d56edfc67323f03a98ffac7eb36d02067535182", "642b1d6fbae4c39c29c15601e4866f259aa8f214999f485908c84868e60840c6", "d2f0f4c679a1b32fb7e21ae7bd280e78826604582da387d8f301ae1c61bededf", "66ed144c487d45bd496e77b3d6ae679bfa634b4c33e9ed299ea1e6a4d1fa7b92"],
            "A4-1": ["a4f24b3af43933a1617564b4ab3bf6484790fbe44f3ed464e7b30c747cb26314", "06a030516c112bbc07f9d0365a53bb738de8e9e2068ae313a9fdbafb8b6ce76c", "355412bbe712c1ae05d2497a713e7373a55347fcf690b8e0c15f6b12b05ec2cc", "07ffd6c8bc8ee75f812ca8e63170829185832a94ff9527b30c9fc2eedf2d319d"],
            "A4-2": ["4d3ea0511cf48095debdf850d4da7284042f6899aee05a574bda66cf4f9870e1", "bb94a149f3da3236291a13cdb2f3c65acc40d756319f74290186f99b616dafbc", "57accdc983fb042cce8c1e882432e0d937ec894251dcc8c8c26b57c1f5e72ee1", "82283b0949ae57a57a21897ae09606cc953a8533117fadd83478e88543735472"]}
        for size, count in (("A3", 2), ("A4", 17)):
            pose = equipment_pose()
            scenes = build_nozzle_axis_sheets(nozzle_drawing(many_points(count), pose=pose), pose,
                {"size": size}, axial_axis="Z", zero_axis="X")
            for index, scene in enumerate(scenes):
                values = [json.dumps(scene, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(),
                    render_drawing_sheet_svg(scene).encode(), export_drawing_sheet_dxf(scene).encode(),
                    rasterize_drawing_sheet_png(scene)]
                self.assertEqual([hashlib.sha256(value).hexdigest() for value in values], expected[f"{size}-{index}"])


if __name__ == "__main__":
    unittest.main()

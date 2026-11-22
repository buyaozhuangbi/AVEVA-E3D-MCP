"""CSG projection accepts material cavity shells through its dedicated contract."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import inspect
import json
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.orthographic_drawing import _validated_curve_primitives, generate_orthographic_drawing
from aveva_e3d_mcp.orthographic_dxf import export_orthographic_dxf
from aveva_e3d_mcp.orthographic_png import rasterize_orthographic_png
from aveva_e3d_mcp.primitive_csg_drawing import (
    _ProjectedEndpointIndex,
    _fitted_projected_circles,
    _verified_curved_sources,
    generate_primitive_csg_drawing,
)
from aveva_e3d_mcp.primitive_csg_mesh import csg_geometry_sha256, derive_primitive_csg_mesh
from test_panel_curve_drawing import dxf_records, png_chunks
from test_primitive_csg_mesh import CSG_ENGINE_AVAILABLE, csg_native, cut


MODULE = "aveva_e3d_mcp.primitive_csg_drawing"
SVG_NS = {"s": "http://www.w3.org/2000/svg"}


class PrimitiveCsgDrawingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not CSG_ENGINE_AVAILABLE:
            raise unittest.SkipTest("Install the geometry extra for Mesh64 integration tests.")
        cls.through = derive_primitive_csg_mesh(csg_native(), radial_segments=24)
        cls.cavity = derive_primitive_csg_mesh(csg_native(dimensions={"XLEN": 20, "YLEN": 20, "ZLEN": 20},
            cutters=[cut("BOX", {"XLEN": 4, "YLEN": 4, "ZLEN": 4})]), radial_segments=24)
        cls.empty = derive_primitive_csg_mesh(csg_native(cutters=[cut("BOX", {"XLEN": 200, "YLEN": 200, "ZLEN": 100})]),
            radial_segments=24)
        cls.dish_through = derive_primitive_csg_mesh(
            csg_native("DISH", {"DIAM": 100.0, "HEIG": 25.0, "RADI": 0.0},
                       cutters=[cut("CYLI", {"DIAM": 20.0, "HEIG": 100.0})]),
            radial_segments=24)
        for mesh in (cls.through, cls.cavity, cls.empty, cls.dish_through):
            if mesh.get("success") is not True:
                raise AssertionError(mesh)

    def assert_failed(self, result):
        self.assertFalse(result["success"], result)
        self.assertFalse(result["available"])
        self.assertEqual(result["views"], [])
        self.assertNotIn("svg", result)
        self.assertNotIn("dxf", result)
        self.assertTrue(result["error_code"])

    def test_through_hole_projects_material_boundary_and_preserves_narrow_claims(self):
        before = deepcopy(self.through)
        drawing = generate_primitive_csg_drawing(self.through, views=["TOP"])
        self.assertTrue(drawing["success"], drawing)
        self.assertTrue(drawing["complete"])
        self.assertEqual(drawing["drawing_kind"], "parameter-derived CSG mesh linework")
        self.assertEqual(drawing["coordinate_space"], "WORLD")
        self.assertEqual(drawing["coordinate_unit"], "mm")
        self.assertEqual(drawing["geometry_sha256"], self.through["geometry_sha256"])
        self.assertEqual(drawing["source"], {"refno": self.through["refno"], "revision": self.through["revision"]})
        self.assertTrue(drawing["cuts_applied"])
        self.assertTrue(drawing["csg_evaluated"])
        self.assertTrue(drawing["material_boundary_validated"])
        self.assertFalse(drawing["native_tessellation"])
        self.assertFalse(drawing["native_cut_application_verified"])
        self.assertFalse(drawing["native_csg_evaluated"])
        self.assertEqual(drawing["footer"], "PARAMETER CSG - NATIVE CUT SCOPE UNVERIFIED")
        self.assertIn(drawing["footer"], " ".join(ET.fromstring(drawing["svg"]).itertext()))
        view = drawing["views"][0]
        self.assertEqual(view["bounds"], {"min": [-50, -40], "max": [50, 40]})
        rim = [line for line in view["lines"] if all(abs(math.hypot(*line[key]) - 10) < 1e-7 for key in ("start", "end"))]
        self.assertEqual(len(rim), 24)
        self.assertTrue(all(line["visibility"] == "visible" for line in rim))
        self.assertEqual(self.through, before)

    def test_closed_projected_ring_uses_analytic_source_in_svg_png_and_dxf(self):
        drawing = generate_primitive_csg_drawing(self.through, views=["TOP"])
        self.assertTrue(drawing["success"], drawing)
        view = drawing["views"][0]
        curves = view.get("curve_primitives", [])
        self.assertEqual(len(curves), 1)
        curve = curves[0]
        self.assertEqual(curve["type"], "circle")
        self.assertGreater(curve["edge_count"], 8)
        self.assertGreater(curve["fit_max_chord_error_mm"], 0.0)
        self.assertAlmostEqual(curve["radius"], 10.0, places=7)
        self.assertEqual(tuple(curve["center"]), curve["analytic_source"]["center"])
        self.assertEqual(curve["radius"], curve["analytic_source"]["source_radius_mm"])
        self.assertEqual(curve["analytic_source"]["shape"], "CYLI")
        self.assertEqual(curve["analytic_source"]["refno"], self.through["source_operands"][1]["refno"])
        self.assertEqual(view["bounds"], view["mesh_edge_bounds"])
        self.assertEqual(len(view["lines"]), drawing["segment_count"])

        svg = ET.fromstring(drawing["svg"])
        self.assertEqual(len(svg.findall(".//s:circle", SVG_NS)), 1)
        self.assertEqual(len(svg.findall(".//s:path", SVG_NS)),
                         drawing["segment_count"] - curve["edge_count"])
        self.assertEqual(drawing["rendered_entity_count"],
                         drawing["segment_count"] - curve["edge_count"] + 1)

        dxf = export_orthographic_dxf(drawing, include_dimensions=False)
        _, records = dxf_records(dxf)
        circles = [record for record in records if record["kind"] == "CIRCLE"]
        self.assertEqual(len(circles), 1)
        self.assertEqual(circles[0]["groups"][8], ["TOP_OUTLINE"])
        self.assertEqual(circles[0]["groups"][10], ["110.000000"])
        self.assertEqual(circles[0]["groups"][20], ["100.000000"])
        self.assertEqual(circles[0]["groups"][40], ["10.000000"])
        self.assertEqual(sum(record["kind"] == "LINE" for record in records),
                         drawing["segment_count"] - curve["edge_count"])
        without_presentation_curve = deepcopy(drawing["views"])
        without_presentation_curve[0].pop("curve_primitives")
        self.assertNotEqual(
            rasterize_orthographic_png(drawing["views"], width=640, height=360),
            rasterize_orthographic_png(without_presentation_curve, width=640, height=360),
        )

    def test_tilted_cylinder_ring_uses_the_same_analytic_ellipse_in_svg_png_and_dxf(self):
        angle = math.radians(30.0)
        axes = ((math.cos(angle), 0.0, -math.sin(angle)),
                (0.0, 1.0, 0.0),
                (math.sin(angle), 0.0, math.cos(angle)))
        native = csg_native("CYLI", {"DIAM": 100.0, "HEIG": 40.0},
                            cutters=[cut(dimensions={"DIAM": 20.0, "HEIG": 100.0})], axes=axes)
        mesh = derive_primitive_csg_mesh(native, radial_segments=48)
        self.assertTrue(mesh["success"], mesh.get("diagnostics"))
        drawing = generate_primitive_csg_drawing(mesh, views=["TOP"])
        self.assertTrue(drawing["success"], drawing)
        view = drawing["views"][0]
        curves = view.get("curve_primitives", [])
        self.assertEqual(len(curves), 1)
        curve = curves[0]
        self.assertEqual(curve["type"], "ellipse")
        self.assertEqual(curve["analytic_source"]["refno"], mesh["source_operands"][1]["refno"])
        self.assertAlmostEqual(curve["major_radius"], 10.0, places=7)
        self.assertAlmostEqual(curve["minor_radius"], 10.0 * math.cos(angle), places=7)
        self.assertAlmostEqual(math.hypot(*curve["major_axis"]), curve["major_radius"], places=9)
        self.assertEqual(len(view["lines"]), drawing["segment_count"])
        self.assertEqual(drawing["rendered_entity_count"], drawing["segment_count"] - curve["edge_count"] + 1)

        svg = ET.fromstring(drawing["svg"])
        ellipse = svg.find(".//s:ellipse", SVG_NS)
        self.assertIsNotNone(ellipse)
        self.assertAlmostEqual(float(ellipse.attrib["rx"]), curve["major_radius"] * view["display_scale_px_per_mm"], places=5)
        self.assertAlmostEqual(float(ellipse.attrib["ry"]), curve["minor_radius"] * view["display_scale_px_per_mm"], places=5)
        self.assertEqual(len(svg.findall(".//s:path", SVG_NS)), drawing["segment_count"] - curve["edge_count"])

        without_curve = deepcopy(drawing["views"])
        without_curve[0].pop("curve_primitives")
        self.assertNotEqual(rasterize_orthographic_png(drawing["views"], width=640, height=360),
                            rasterize_orthographic_png(without_curve, width=640, height=360))

        _, records = dxf_records(export_orthographic_dxf(drawing, include_dimensions=False))
        ellipses = [record for record in records if record["kind"] == "ELLIPSE"]
        self.assertEqual(len(ellipses), 1)
        groups = ellipses[0]["groups"]
        self.assertEqual(groups[8], ["TOP_OUTLINE"])
        self.assertAlmostEqual(math.hypot(float(groups[11][0]), float(groups[21][0])),
                               curve["major_radius"], places=6)
        self.assertAlmostEqual(float(groups[40][0]), curve["minor_radius"] / curve["major_radius"], places=6)
        self.assertEqual(groups[41], ["0.000000"])
        self.assertEqual(groups[42], ["6.283185"])
        self.assertEqual(sum(record["kind"] == "LINE" for record in records),
                         drawing["segment_count"] - curve["edge_count"])

    def test_dish_spherical_silhouette_arcs_are_refined_in_svg_png_and_dxf(self):
        drawing = generate_primitive_csg_drawing(
            self.dish_through, views=["FRONT", "TOP", "SIDE"])
        self.assertTrue(drawing["success"], drawing)
        self.assertEqual(drawing["curve_refinement"]["mode"], "source_verified_projected_curve_fit")
        self.assertIn("arc", drawing["curve_refinement"]["curve_types"])
        views = {view["name"]: view for view in drawing["views"]}
        projected_arcs = []
        for name in ("FRONT", "SIDE"):
            arcs = [curve for curve in views[name].get("curve_primitives", [])
                    if curve["type"] == "arc"]
            self.assertEqual(len(arcs), 2, name)
            for arc in arcs:
                self.assertEqual(arc["analytic_source"]["shape"], "DISH")
                self.assertEqual(arc["analytic_source"]["refno"], self.dish_through["refno"])
                span = arc["end_angle_degrees"] - arc["start_angle_degrees"]
                self.assertGreater(span, 0.0)
                self.assertLess(span, math.degrees(2.0 * math.acos(37.5 / 62.5)))
                self.assertAlmostEqual(arc["radius"], arc["analytic_source"]["source_radius_mm"], places=8)
                projected_arcs.append((name, arc))

        tampered = deepcopy(views["FRONT"])
        tampered["curve_primitives"] = [deepcopy(projected_arcs[0][1])]
        tampered["curve_primitives"][0]["end_angle_degrees"] = (
            tampered["curve_primitives"][0]["start_angle_degrees"] + 120.0)
        with self.assertRaisesRegex(ValueError, "invalid_curve_source_section"):
            _validated_curve_primitives(tampered)

        tampered["curve_primitives"] = [deepcopy(projected_arcs[0][1])]
        tampered["curve_primitives"][0]["analytic_source"]["axis_vertical_sign"] *= -1.0
        with self.assertRaisesRegex(ValueError, "invalid_curve_fit"):
            _validated_curve_primitives(tampered)

        svg = ET.fromstring(drawing["svg"])
        self.assertEqual(len(svg.findall('.//s:path[@data-curve="arc"]', SVG_NS)), 4)
        self.assertEqual(drawing["rendered_entity_count"],
                         drawing["segment_count"] - drawing["curve_refinement"]["source_edge_count"]
                         + drawing["curve_refinement"]["curve_count"])
        without_curves = deepcopy(drawing["views"])
        for view in without_curves:
            view.pop("curve_primitives", None)
        self.assertNotEqual(rasterize_orthographic_png(drawing["views"], width=900, height=500),
                            rasterize_orthographic_png(without_curves, width=900, height=500))

        _, records = dxf_records(export_orthographic_dxf(drawing, include_dimensions=False))
        arcs = [record for record in records if record["kind"] == "ARC"]
        circles = [record for record in records if record["kind"] == "CIRCLE"]
        self.assertEqual(len(arcs), 4)
        self.assertTrue(circles)
        self.assertTrue(all(record["groups"][40] for record in arcs))
        self.assertEqual(sum(record["kind"] == "LINE" for record in records),
                         drawing["segment_count"] - drawing["curve_refinement"]["source_edge_count"])

    def test_side_on_dish_rotation_preserves_cut_silhouette_arcs(self):
        angle = math.radians(30.0)
        axis_x, axis_z = math.sin(angle), math.cos(angle)
        mesh = derive_primitive_csg_mesh(csg_native(
            "DISH", {"DIAM": 100.0, "HEIG": 25.0, "RADI": 0.0},
            cutters=[cut("CYLI", {"DIAM": 20.0, "HEIG": 100.0})],
            axes=((axis_z, 0.0, -axis_x), (0.0, 1.0, 0.0), (axis_x, 0.0, axis_z))),
            radial_segments=24)
        self.assertTrue(mesh["success"], mesh)
        drawing = generate_primitive_csg_drawing(mesh, views=["FRONT", "TOP"])
        self.assertTrue(drawing["success"], drawing)
        front, top = drawing["views"]
        arcs = [curve for curve in front.get("curve_primitives", []) if curve["type"] == "arc"]
        self.assertEqual(len(arcs), 2)
        self.assertFalse(any(curve["type"] == "arc" for curve in top.get("curve_primitives", [])))
        for arc in arcs:
            axis = arc["analytic_source"]["projected_axis_unit"]
            self.assertAlmostEqual(axis[0], axis_x)
            self.assertAlmostEqual(axis[1], axis_z)
        self.assertEqual(len(ET.fromstring(drawing["svg"]).findall('.//s:path[@data-curve="arc"]', SVG_NS)), 2)
        _, records = dxf_records(export_orthographic_dxf(drawing, include_dimensions=False))
        self.assertEqual(sum(item["kind"] == "ARC" for item in records), 2)
        self.assertTrue(rasterize_orthographic_png(drawing["views"], width=900, height=500).startswith(b"\x89PNG"))

        tampered = deepcopy(front)
        source = next(curve["analytic_source"] for curve in tampered["curve_primitives"] if curve["type"] == "arc")
        source["projected_axis_unit"] = [-axis_x, -axis_z]
        with self.assertRaisesRegex(ValueError, "invalid_curve_fit"):
            _validated_curve_primitives(tampered)

    def test_dish_curve_fit_fails_closed_when_source_digest_is_changed(self):
        tampered = deepcopy(self.dish_through)
        tampered["source_geometry_sha256"] = "0" * 64
        drawing = generate_primitive_csg_drawing(tampered, views=["FRONT", "SIDE"])
        self.assertTrue(drawing["success"], drawing)
        self.assertEqual(drawing["curve_refinement"]["curve_count"], 0)
        self.assertTrue(all("curve_primitives" not in view for view in drawing["views"]))

    def test_ambiguous_analytic_sources_keep_the_original_projected_edges(self):
        drawing = generate_primitive_csg_drawing(self.through, views=["TOP"])
        sources = _verified_curved_sources(self.through)
        self.assertEqual(len(sources), 1)
        duplicate = deepcopy(sources[0])
        duplicate["refno"] = str(duplicate["refno"]) + "/duplicate"
        self.assertEqual(_fitted_projected_circles(
            drawing["views"][0], mesh_vertices=self.through["vertices"],
            curved_sources=[*sources, duplicate]), [])

    def test_ordinary_dxf_does_not_convert_csg_circle_metadata(self):
        drawing = generate_primitive_csg_drawing(self.through, views=["TOP"])
        drawing["drawing_kind"] = "parameter-derived approximate mesh linework"
        _, records = dxf_records(export_orthographic_dxf(drawing, include_dimensions=False))
        self.assertFalse(any(record["kind"] == "CIRCLE" for record in records))
        self.assertEqual(sum(record["kind"] == "LINE" for record in records), drawing["segment_count"])

    def test_near_circular_polygon_is_not_smoothed_without_a_matching_source_section(self):
        count, radius = 10, 5.0
        points = [(radius * math.cos(2 * math.pi * index / count),
                   radius * math.sin(2 * math.pi * index / count)) for index in range(count)]
        lines = [{"start": points[index], "end": points[(index + 1) % count],
                  "kind": "feature", "visibility": "visible"} for index in range(count)]
        view = {"name": "TOP", "coordinate_unit": "mm", "lines": lines}
        mesh_vertices = [[x, y, 0.0] for x, y in points]
        sources = _verified_curved_sources(self.through)

        self.assertEqual(len(sources), 1)
        self.assertEqual(_fitted_projected_circles(
            view, mesh_vertices=mesh_vertices, curved_sources=sources), [])

    def test_mutable_tessellation_receipt_cannot_widen_source_radius_matching(self):
        tampered = deepcopy(self.through)
        tampered["source_operands"][1]["tessellation"] = {"max_surface_deviation_mm": 1000.0}
        sources = _verified_curved_sources(tampered)
        count, radius = 12, 10.5
        points = [(radius * math.cos(2 * math.pi * index / count),
                   radius * math.sin(2 * math.pi * index / count)) for index in range(count)]
        view = {"name": "TOP", "coordinate_unit": "mm", "lines": [
            {"start": points[index], "end": points[(index + 1) % count],
             "kind": "feature", "visibility": "visible"} for index in range(count)]}
        self.assertEqual(_fitted_projected_circles(
            view, mesh_vertices=[[x, y, 0.0] for x, y in points], curved_sources=sources), [])

    def test_unrepresentable_verified_source_dimension_keeps_mesh_projection_available(self):
        tampered = deepcopy(self.through)
        source = tampered["source_geometry"]
        huge = 10 ** 1000
        source["negative_children"][0]["dimensions"]["DIAM"] = huge
        source["target_geometry"]["negative_children"][0]["dimensions"]["DIAM"] = huge
        tampered["source_geometry_sha256"] = hashlib.sha256(
            json.dumps(source, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()

        self.assertEqual(_verified_curved_sources(tampered), [])
        drawing = generate_primitive_csg_drawing(tampered, views=["TOP"])
        self.assertTrue(drawing["success"], drawing)
        self.assertEqual(drawing["curve_refinement"]["curve_count"], 0)

    def test_cone_ring_maps_to_its_world_axial_station(self):
        native = csg_native("CYLI", {"DIAM": 100.0, "HEIG": 40.0}, cutters=[
            cut("CONE", {"DBOT": 20.0, "DTOP": 40.0, "HEIG": 80.0})
        ])
        mesh = derive_primitive_csg_mesh(native, radial_segments=24)
        self.assertTrue(mesh["success"], mesh.get("diagnostics"))
        drawing = generate_primitive_csg_drawing(mesh, views=["TOP"])
        self.assertTrue(drawing["success"], drawing)
        cone_rings = [curve["analytic_source"] for curve in drawing["views"][0].get("curve_primitives", [])
                      if curve["analytic_source"]["shape"] == "CONE"]
        self.assertTrue(cone_rings)
        self.assertTrue(any(abs(source["axial_station_mm"] - 20.0) < 1e-6
                            and abs(source["source_radius_mm"] - 17.5) < 1e-6
                            for source in cone_rings))

    def test_curve_source_rows_must_match_the_digest_verified_native_operands(self):
        tampered = deepcopy(self.through)
        tampered["source_operands"][1]["shape"] = "CONE"
        self.assertEqual(_verified_curved_sources(tampered), [])
        drawing = generate_primitive_csg_drawing(tampered, views=["TOP"])
        self.assertTrue(drawing["success"], drawing)
        self.assertEqual(drawing["curve_refinement"]["curve_count"], 0)
        self.assertNotIn("curve_primitives", drawing["views"][0])

    def test_rotated_scaled_cylinder_uses_analytic_source_projection_envelope(self):
        angle = math.radians(11.25)
        for scale in (1.0, 1.000008):
            axes = ((math.cos(angle), math.sin(angle), 0.0),
                    (-math.sin(angle), math.cos(angle), 0.0),
                    (0.0, 0.0, 1.0))
            native = csg_native("CYLI", {"DIAM": 200.0 * scale, "HEIG": 40.0},
                cutters=[cut(dimensions={"DIAM": 20.0, "HEIG": 80.0})], axes=axes)
            mesh = derive_primitive_csg_mesh(native, radial_segments=48)
            self.assertTrue(mesh["success"], mesh.get("diagnostics"))
            mesh_x = [point[0] for point in mesh["vertices"]]
            self.assertLess(max(mesh_x) - min(mesh_x), 200.0 * scale - 0.1)

            drawing = generate_primitive_csg_drawing(mesh, views=["TOP"])
            self.assertTrue(drawing["success"], drawing)
            view = drawing["views"][0]
            self.assertAlmostEqual(view["bounds"]["min"][0], -100.0 * scale, places=9)
            self.assertAlmostEqual(view["bounds"]["max"][0], 100.0 * scale, places=9)
            self.assertAlmostEqual(view["bounds"]["min"][1], -100.0 * scale, places=9)
            self.assertAlmostEqual(view["bounds"]["max"][1], 100.0 * scale, places=9)
            self.assertEqual(view["dimensions"], {"width": 200.0 * scale, "height": 200.0 * scale, "unit": "mm"})
            self.assertTrue(drawing["source_parameter_envelope_applied"])
            self.assertIn("not the post-Boolean material extent", drawing["projection_bounds_scope"])
            self.assertIn("VIEW BOUNDS INCLUDE CONSERVATIVE SOURCE-PARENT ENVELOPE", drawing["svg"])

    def test_tighter_operand_tolerance_refines_circular_csg_linework(self):
        native = csg_native("CYLI", {"DIAM": 400.0, "HEIG": 100.0},
            cutters=[cut(dimensions={"DIAM": 40.0, "HEIG": 120.0})])
        fixed = derive_primitive_csg_mesh(native, radial_segments=48)
        refined = derive_primitive_csg_mesh(native, radial_segments=48, chord_tolerance_mm=0.25)
        self.assertTrue(fixed["success"], fixed)
        self.assertTrue(refined["success"], refined)
        fixed_root = fixed["source_operands"][0]
        refined_root = refined["source_operands"][0]
        self.assertNotIn("tessellation", fixed_root)
        self.assertEqual(refined_root["tessellation"]["radial_segments"], 64)
        self.assertLessEqual(refined_root["tessellation"]["max_surface_deviation_mm"], 0.25)
        fixed_drawing = generate_primitive_csg_drawing(fixed, views=["TOP"])
        refined_drawing = generate_primitive_csg_drawing(refined, views=["TOP"])
        self.assertTrue(fixed_drawing["success"], fixed_drawing)
        self.assertTrue(refined_drawing["success"], refined_drawing)
        self.assertGreater(refined_drawing["segment_count"], fixed_drawing["segment_count"])

    def test_rotated_cone_uses_larger_end_radius_and_source_digest_blocks_tampered_envelope(self):
        angle = math.radians(11.25)
        axes = ((math.cos(angle), math.sin(angle), 0.0),
                (-math.sin(angle), math.cos(angle), 0.0), (0.0, 0.0, 1.0))
        native = csg_native("CONE", {"DBOT": 180.0, "DTOP": 120.0, "HEIG": 40.0},
            cutters=[cut(dimensions={"DIAM": 20.0, "HEIG": 80.0})], axes=axes)
        mesh = derive_primitive_csg_mesh(native, radial_segments=48)
        self.assertTrue(mesh["success"], mesh.get("diagnostics"))
        drawing = generate_primitive_csg_drawing(mesh, views=["TOP"])
        self.assertTrue(drawing["success"], drawing)
        self.assertAlmostEqual(drawing["views"][0]["dimensions"]["width"], 180.0, places=9)
        self.assertAlmostEqual(drawing["views"][0]["dimensions"]["height"], 180.0, places=9)

        tampered = deepcopy(mesh)
        tampered["source_geometry"]["dimensions"]["DBOT"] = 220.0
        fallback = generate_primitive_csg_drawing(tampered, views=["TOP"])
        self.assertTrue(fallback["success"], fallback)
        self.assertNotIn("source_parameter_envelope_applied", fallback)
        self.assertLess(fallback["views"][0]["dimensions"]["width"], 180.0)

    def test_parent_envelope_is_rejected_when_boolean_removes_the_parent_side(self):
        native = csg_native("CYLI", {"DIAM": 200.0, "HEIG": 100.0}, cutters=[
            cut("BOX", {"XLEN": 100.0, "YLEN": 300.0, "ZLEN": 200.0}, position=(100.0, 0.0, 0.0)),
        ])
        mesh = derive_primitive_csg_mesh(native, radial_segments=48)
        self.assertTrue(mesh["success"], mesh.get("diagnostics"))
        drawing = generate_primitive_csg_drawing(mesh, views=["TOP"])
        self.assertTrue(drawing["success"], drawing)
        view = drawing["views"][0]
        self.assertLess(view["bounds"]["max"][0], 60.0)
        self.assertNotIn("source_parameter_envelope_applied", drawing)
        self.assertNotIn("VIEW BOUNDS INCLUDE CONSERVATIVE SOURCE-PARENT ENVELOPE", drawing["svg"])

    def test_cavity_inward_finding_is_preserved_and_old_strict_entry_point_still_refuses(self):
        before = deepcopy(self.cavity)
        old = generate_orthographic_drawing(self.cavity["vertices"], self.cavity["triangles"])
        self.assertFalse(old["success"])
        self.assertEqual(old["error_code"], "closed_valid_outward_mesh_required")
        self.assertNotIn("_material_boundary_prevalidated", inspect.signature(generate_orthographic_drawing).parameters)
        drawing = generate_primitive_csg_drawing(self.cavity, views=["TOP"], show_hidden_lines=True)
        self.assertTrue(drawing["success"], drawing)
        self.assertFalse(drawing["validation"]["valid"])
        self.assertFalse(drawing["validation"]["checks"]["orientation_outward"])
        self.assertEqual(drawing["validation"], self.cavity["validation"])
        self.assertIn("component_winding_is_inward", [finding["code"] for finding in drawing["validation"]["findings"]])
        self.assertEqual(self.cavity, before)

    def test_hidden_cavity_lines_are_optional_and_do_not_turn_into_visible_material(self):
        default = generate_primitive_csg_drawing(self.cavity, views=["TOP"])
        shown = generate_primitive_csg_drawing(self.cavity, views=["TOP"], show_hidden_lines=True)
        self.assertTrue(default["success"], default)
        self.assertTrue(shown["success"], shown)
        visible = [line for line in shown["views"][0]["lines"] if line["visibility"] == "visible"]
        hidden = [line for line in shown["views"][0]["lines"] if line["visibility"] == "hidden"]
        self.assertEqual(visible, default["views"][0]["lines"])
        self.assertGreaterEqual(len(hidden), 4)
        self.assertTrue(all(max(abs(value) for key in ("start", "end") for value in line[key]) <= 2 + 1e-8 for line in hidden))
        self.assertIn('stroke-dasharray="6 4"', shown["svg"])

    def test_legal_empty_material_is_successful_without_views_or_fake_parent_outline(self):
        with patch(f"{MODULE}._generate_orthographic_drawing_from_validated_mesh") as projector:
            drawing = generate_primitive_csg_drawing(self.empty)
        self.assertTrue(drawing["success"], drawing)
        self.assertTrue(drawing["available"])
        self.assertTrue(drawing["complete"])
        self.assertTrue(drawing["empty"])
        self.assertEqual(drawing["views"], [])
        self.assertEqual(drawing["segment_count"], 0)
        self.assertNotIn("svg", drawing)
        self.assertFalse(drawing["native_cut_application_verified"])
        projector.assert_not_called()

    def test_png_and_dxf_export_the_same_cavity_projection(self):
        drawing = generate_primitive_csg_drawing(self.cavity, show_hidden_lines=True)
        self.assertTrue(drawing["success"], drawing)
        dxf = export_orthographic_dxf(drawing, include_dimensions=False)
        _, records = dxf_records(dxf)
        self.assertFalse(any(record["kind"] == "CIRCLE" for record in records))
        self.assertEqual(sum(record["kind"] == "LINE" for record in records),
                         drawing["segment_count"])
        png = rasterize_orthographic_png(drawing["views"], width=640, height=360, footer=drawing["footer"])
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(png_chunks(png)[-1][0], b"IEND")

    def test_unvalidated_native_claims_frames_engine_and_material_provenance_are_rejected(self):
        for fault in ("success", "material", "cuts", "native_shape", "native_tessellation", "space", "unit", "frame",
                      "engine_precision", "engine_status", "engine_version", "provenance", "allowed_findings", "cavity_list", "validation"):
            with self.subTest(fault=fault):
                mesh = deepcopy(self.cavity)
                if fault == "success": mesh["success"] = False
                elif fault == "material": mesh["material_boundary_validated"] = False
                elif fault == "cuts": mesh["cuts_applied"] = False
                elif fault == "native_shape": mesh["native_cut_application_verified"] = True
                elif fault == "native_tessellation": mesh["native_tessellation"] = True
                elif fault == "space": mesh["coordinate_space"] = "primitive_local"
                elif fault == "unit": mesh["coordinate_unit"] = "m"
                elif fault == "frame": mesh["coordinate_frame"] = "SDK native (unverified)"
                elif fault == "engine_precision": mesh["engine"]["numeric_precision"] = "float32"
                elif fault == "engine_status": mesh["engine"]["status"] = "NotManifold"
                elif fault == "engine_version": mesh["engine"]["version"] = ""
                elif fault == "provenance": mesh["material_boundary_validation"]["provenance"] = "caller says valid"
                elif fault == "allowed_findings": mesh["material_boundary_validation"]["allowed_findings"].append("boundary_edges_present")
                elif fault == "cavity_list": mesh["material_boundary_validation"]["cavity_shells"] = []
                else: mesh["validation"]["valid"] = True
                with patch(f"{MODULE}._generate_orthographic_drawing_from_validated_mesh") as projector:
                    self.assert_failed(generate_primitive_csg_drawing(mesh))
                projector.assert_not_called()

    def test_hash_mismatch_and_rehashed_open_or_reversed_geometry_fail_closed(self):
        for fault in ("missing_hash", "changed_vertex", "open_rehashed", "reversed_rehashed", "nonfinite"):
            with self.subTest(fault=fault):
                mesh = deepcopy(self.through)
                if fault == "missing_hash": del mesh["geometry_sha256"]
                elif fault == "changed_vertex": mesh["vertices"][0][0] += 1
                elif fault == "open_rehashed":
                    mesh["triangles"].pop()
                    mesh["geometry_sha256"] = csg_geometry_sha256(mesh["vertices"], mesh["triangles"])
                elif fault == "reversed_rehashed":
                    mesh["triangles"] = [[a, c, b] for a, b, c in mesh["triangles"]]
                    mesh["geometry_sha256"] = csg_geometry_sha256(mesh["vertices"], mesh["triangles"])
                else: mesh["vertices"][0][0] = float("nan")
                self.assert_failed(generate_primitive_csg_drawing(mesh))

    def test_empty_state_requires_empty_arrays_and_the_dedicated_engine_validation(self):
        for fault in ("engine", "material", "validation", "array", "false_empty"):
            with self.subTest(fault=fault):
                mesh = deepcopy(self.empty)
                if fault == "engine": mesh["engine"]["empty"] = False
                elif fault == "material": mesh["material_boundary_validation"]["empty"] = False
                elif fault == "validation": mesh["validation"]["findings"] = [{"code": "unknown"}]
                elif fault == "array":
                    mesh["vertices"] = [[0, 0, 0]]
                    mesh["geometry_sha256"] = csg_geometry_sha256(mesh["vertices"], mesh["triangles"])
                else: mesh["empty"] = False
                self.assert_failed(generate_primitive_csg_drawing(mesh))

    def test_projection_resource_failure_returns_no_partial_views_or_svg(self):
        with patch("aveva_e3d_mcp.orthographic_drawing.MAX_TOTAL_DRAWING_SEGMENTS", 3):
            result = generate_primitive_csg_drawing(self.cavity, show_hidden_lines=True)
        self.assert_failed(result)
        self.assertEqual(result["error_code"], "drawing_segment_limit_exceeded")


class PrimitiveCsgDrawingAdmissionTests(unittest.TestCase):
    """These refusal checks run even when the optional engine is absent."""

    def test_endpoint_index_joins_two_ulp_rounding_drift(self):
        base = (1_000_000_000_000.0, -2_000_000_000_000.0)
        drifted = (base[0], base[1])
        for axis, direction in ((0, math.inf), (1, -math.inf)):
            values = list(drifted)
            for _ in range(2):
                values[axis] = math.nextafter(values[axis], direction)
            drifted = tuple(values)
        index = _ProjectedEndpointIndex([base, drifted])

        self.assertEqual(index.add(base), index.add(drifted))

    def test_endpoint_index_keeps_local_precision_when_bucket_scale_is_large(self):
        index = _ProjectedEndpointIndex([(0.0, 0.0),
                                         (1_000_000_000_000.0, 1_000_000_000_000.0)])

        origin = index.add((0.0, 0.0))
        distinct = index.add((0.0005, 0.0))

        self.assertNotEqual(origin, distinct)

    def test_large_world_projection_maps_endpoints_with_ulp_rounding_drift(self):
        origin = (1_000_000_000_000.0, -2_000_000_000_000.0, 0.0)
        radius, count = 10.0, 16
        mesh_vertices = []
        lines = []
        for index in range(count):
            angle = 2.0 * math.pi * index / count
            next_angle = 2.0 * math.pi * ((index + 1) % count) / count
            point = [origin[0] + radius * math.cos(angle),
                     origin[1] + radius * math.sin(angle), origin[2]]
            next_point = [origin[0] + radius * math.cos(next_angle),
                          origin[1] + radius * math.sin(next_angle), origin[2]]
            mesh_vertices.append(point)
            direction = 1 if index % 2 == 0 else -1

            def nudge(value, sign):
                for _ in range(2):
                    value = math.nextafter(value, math.inf if sign > 0 else -math.inf)
                return value

            start = [nudge(point[0], direction), nudge(point[1], -direction)]
            end = [nudge(next_point[0], direction), nudge(next_point[1], -direction)]
            lines.append({"start": start, "end": end, "kind": "feature", "visibility": "visible"})

        source = {"refno": "/test/cylinder", "shape": "CYLI",
                  "dimensions": {"DIAM": radius * 2.0, "HEIG": 100.0},
                  "origin": list(origin),
                  "axes": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]}
        view = {"name": "TOP", "coordinate_unit": "mm", "lines": lines}

        self.assertTrue(all(
            (round(vertex[0], 9), round(vertex[1], 9)) !=
            (round(line["start"][0], 9), round(line["start"][1], 9))
            for vertex, line in zip(mesh_vertices, lines)
        ))
        self.assertTrue(all(
            (round(lines[index]["end"][0], 9), round(lines[index]["end"][1], 9)) !=
            (round(lines[(index + 1) % count]["start"][0], 9),
             round(lines[(index + 1) % count]["start"][1], 9))
            for index in range(count)
        ))
        curves = _fitted_projected_circles(view, mesh_vertices=mesh_vertices, curved_sources=[source])

        self.assertEqual(len(curves), 1)
        self.assertEqual(curves[0]["analytic_source"]["refno"], source["refno"])
        self.assertAlmostEqual(curves[0]["radius"], radius)

    def test_invalid_projection_controls_stop_before_projector(self):
        for options in ({"views": []}, {"views": ["TOP", "TOP"]}, {"views": "TOP"}, {"views": ["top"]},
                        {"feature_angle_degrees": True}, {"feature_angle_degrees": float("nan")},
                        {"feature_angle_degrees": -1}, {"feature_angle_degrees": 181},
                        {"hidden_line_removal": 1}, {"show_hidden_lines": "true"},
                        {"hidden_line_removal": False, "show_hidden_lines": True}):
            with self.subTest(options=options), patch(f"{MODULE}._generate_orthographic_drawing_from_validated_mesh") as projector:
                result = generate_primitive_csg_drawing({}, **options)
                self.assertFalse(result["success"], result)
                self.assertEqual(result["views"], [])
                self.assertNotIn("svg", result)
                projector.assert_not_called()

    def test_missing_failed_or_native_geometry_cannot_reach_projector(self):
        for mesh in (None, [], {}, {"success": False}, csg_native()):
            with self.subTest(mesh_type=type(mesh).__name__):
                with patch(f"{MODULE}._generate_orthographic_drawing_from_validated_mesh") as projector:
                    result = generate_primitive_csg_drawing(mesh)
                self.assertFalse(result["success"], result)
                self.assertEqual(result["error_code"], "successful_material_csg_mesh_required")
                self.assertEqual(result["views"], [])
                self.assertNotIn("svg", result)
                projector.assert_not_called()


if __name__ == "__main__":
    unittest.main()

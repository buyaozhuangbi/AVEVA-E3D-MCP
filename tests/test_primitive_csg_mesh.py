"""Independent material-volume and admission oracles for parameter CSG meshes."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aveva_e3d_mcp import primitive_csg_mesh as csg_mesh
from aveva_e3d_mcp import primitive_csg_worker as csg_worker
from aveva_e3d_mcp.primitive_csg_mesh import CSGWorkerError, csg_geometry_sha256, derive_primitive_csg_mesh
from aveva_e3d_mcp.primitive_mesh import derive_primitive_mesh
from aveva_e3d_mcp.triangle_mesh_validation import validate_triangle_mesh


MODULE = "aveva_e3d_mcp.primitive_csg_mesh"
IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
CSG_ENGINE_AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("manifold3d", "numpy"))
requires_csg_engine = unittest.skipUnless(CSG_ENGINE_AVAILABLE, "Install the geometry extra for Mesh64 integration tests.")


def orientation(axes=IDENTITY):
    return {name: list(axis) for name, axis in zip(("x_axis", "y_axis", "z_axis"), axes)}


def mapped(point, axes=IDENTITY, position=(0.0, 0.0, 0.0)):
    return [math.fsum((position[i], *(point[j] * axes[j][i] for j in range(3)))) for i in range(3)]


def composed_axes(parent, child):
    return tuple(tuple(mapped(axis, parent)) for axis in child)


def cut(shape="CYLI", dimensions=None, *, position=(0.0, 0.0, 0.0), axes=IDENTITY):
    return {"shape": shape, "dimensions": dimensions or {"DIAM": 20.0, "HEIG": 60.0},
            "position": position, "axes": axes}


def csg_native(shape="BOX", dimensions=None, *, cutters=None, position=(0.0, 0.0, 0.0), axes=IDENTITY):
    """Build the actual bridge wrapper, retaining independent WORLD/owner poses."""
    root_ref = "=1/10"
    root_frame = {"storage_frame_refno": "=1/1",
        "native": {"position": list(position), "orientation": orientation(axes)},
        "world": {"position": list(position), "orientation": orientation(axes)}, "relative_to_owner": None}
    children = []
    for index, item in enumerate(cutters if cutters is not None else [cut()]):
        child_ref = f"=1/{20 + index}"
        child_frame = {"storage_frame_refno": root_ref,
            "native": {"position": list(item["position"]), "orientation": orientation(item["axes"])},
            "world": {"position": mapped(item["position"], axes, position),
                      "orientation": orientation(composed_axes(axes, item["axes"]))},
            "relative_to_owner": {"position": list(item["position"]), "orientation": orientation(item["axes"])}}
        children.append({"element": {"refno": child_ref,
                "type": {"CYLI": "NCYL", "BOX": "NBOX", "CONE": "NCON"}.get(item["shape"], "NDIS"),
                "name": f"/CUT-{index}"}, "shape": item["shape"], "negative": True, "is_negative": True,
            "unit": "mm", "dimensions": deepcopy(item["dimensions"]), "frame": child_frame,
            "negative_children": [], "member_refnos": [], "complete": True,
            "read_errors": {}, "edit_reasons": []})
    node = {"element": {"refno": root_ref, "type": shape, "name": "/CSG-PARENT"},
        "shape": shape, "negative": False, "is_negative": False, "unit": "mm",
        "dimensions": deepcopy(dimensions or {"XLEN": 100.0, "YLEN": 80.0, "ZLEN": 40.0}),
        "frame": root_frame, "negative_children": children, "member_refnos": [c["element"]["refno"] for c in children],
        "complete": True, "read_errors": {}, "edit_reasons": []}
    return {"success": True, "complete": True, "partial": False,
        **{key: deepcopy(node[key]) for key in ("element", "shape", "negative", "is_negative", "unit", "dimensions", "frame", "negative_children")},
        "target_geometry": node, "negative_owner": None, "owner": {"refno": "=1/1", "type": "EQUI"},
        "owner_member_refnos": [root_ref], "editable": True, "edit_reasons": [],
        "revision": "a" * 64, "read_errors": {},
        "native_cut_scope": "Native application scope unavailable; owner membership is retained."}


def sync_target(native):
    """Update duplicated native fields after a deliberate fixture mutation."""
    for key in ("element", "shape", "negative", "is_negative", "unit", "dimensions", "frame", "negative_children"):
        native["target_geometry"][key] = deepcopy(native[key])
    return native


def polygon_area(radius, count):
    return count * radius * radius * math.sin(2 * math.pi / count) / 2


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def dot(a, b):
    return math.fsum(x * y for x, y in zip(a, b))


def signed_volume(vertices, triangles):
    anchor = vertices[0]
    terms = []
    for triangle in triangles:
        a, b, c = ([vertices[index][axis] - anchor[axis] for axis in range(3)] for index in triangle)
        terms.append(dot(a, cross(b, c)) / 6)
    return math.fsum(terms)


def world_volume_roundoff(vertices, triangles):
    """Bound volume drift when local geometry is rounded to WORLD floats."""
    areas = []
    for triangle in triangles:
        a, b, c = (vertices[index] for index in triangle)
        areas.append(math.hypot(*cross([b[i] - a[i] for i in range(3)],
                                      [c[i] - a[i] for i in range(3)])) / 2)
    # A boundary displacement changes volume by surface area times distance.
    # Four coordinate ULPs conservatively cover three axes and arithmetic.
    coordinate_ulp = max(math.ulp(value) for point in vertices for value in point)
    return 4 * math.fsum(areas) * coordinate_ulp


def box_worker_receipt():
    """Independent worker fixture for a BOX with a disjoint BOX cutter."""
    vertices = [[x, y, z] for z in (-20, 20) for x, y in ((-50, -40), (50, -40), (50, 40), (-50, 40))]
    triangles = [[0, 3, 2], [0, 2, 1], [4, 5, 6], [4, 6, 7],
                 [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
                 [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]
    return {"success": True, "empty": False, "vertices": vertices, "triangles": triangles,
        "gross_polyhedral_volume_mm3": 320000, "net_volume_mm3": 320000,
        "engine": {"name": "manifold3d", "version": "test-fixture", "numpy_version": "test-fixture",
            "numeric_precision": "float64", "mesh_api": "Mesh64", "operation": "Subtract", "status": "NoError",
            "empty": False, "operand_count": 2, "input_vertices": 16, "input_triangles": 24}}


def component_signed_volumes(mesh):
    """Find connected face components independently of the production validator."""
    uses = defaultdict(list)
    for index, face in enumerate(mesh["triangles"]):
        for vertex in face:
            uses[vertex].append(index)
    pending = set(range(len(mesh["triangles"])))
    volumes = []
    while pending:
        todo, found = [next(iter(pending))], []
        while todo:
            index = todo.pop()
            if index not in pending:
                continue
            pending.remove(index)
            face = mesh["triangles"][index]
            found.append(face)
            todo.extend(neighbor for vertex in face for neighbor in uses[vertex] if neighbor in pending)
        volumes.append(signed_volume(mesh["vertices"], found))
    return sorted(volumes)


def material_contains(mesh, point):
    """Solid-angle winding of oriented material boundaries, including voids."""
    angles = []
    for triangle in mesh["triangles"]:
        a, b, c = ([mesh["vertices"][index][axis] - point[axis] for axis in range(3)] for index in triangle)
        la, lb, lc = math.hypot(*a), math.hypot(*b), math.hypot(*c)
        denominator = la * lb * lc + dot(a, b) * lc + dot(b, c) * la + dot(c, a) * lb
        angles.append(2 * math.atan2(dot(a, cross(b, c)), denominator))
    return abs(math.fsum(angles)) > 2 * math.pi


class PrimitiveCsgMeshTests(unittest.TestCase):
    def accepted(self, native=None, *, segments=32):
        if not CSG_ENGINE_AVAILABLE:
            self.skipTest("Install the geometry extra for Mesh64 integration tests.")
        native = csg_native() if native is None else native
        before = deepcopy(native)
        mesh = derive_primitive_csg_mesh(native, radial_segments=segments)
        self.assertTrue(mesh["success"], mesh)
        self.assertTrue(mesh["available"])
        self.assertTrue(mesh["complete"])
        self.assertTrue(mesh["material_boundary_validated"])
        self.assertTrue(mesh["cuts_applied"])
        self.assertFalse(mesh["native_tessellation"])
        self.assertFalse(mesh["native_cut_application_verified"])
        self.assertEqual(mesh["coordinate_space"], "WORLD")
        self.assertEqual(mesh["coordinate_unit"], "mm")
        self.assertEqual(mesh["coordinate_frame"], "WORLD coordinates; millimetres")
        self.assertEqual(mesh["geometry_sha256"], csg_geometry_sha256(mesh["vertices"], mesh["triangles"]))
        self.assertEqual(mesh["counts"], {"vertices": len(mesh["vertices"]), "triangles": len(mesh["triangles"])})
        self.assertEqual(len(mesh["triangle_normals"]), len(mesh["triangles"]))
        self.assertEqual(native, before)
        self.assertAlmostEqual(signed_volume(mesh["vertices"], mesh["triangles"]), mesh["net_volume_mm3"],
            delta=max(1e-5, mesh["net_volume_mm3"] * 1e-10,
                      world_volume_roundoff(mesh["vertices"], mesh["triangles"])))
        edges = Counter()
        directions = Counter()
        for triangle, normal in zip(mesh["triangles"], mesh["triangle_normals"]):
            self.assertEqual(len(set(triangle)), 3)
            self.assertAlmostEqual(math.hypot(*normal), 1, places=9)
            a, b, c = (mesh["vertices"][index] for index in triangle)
            geometric = cross([b[i] - a[i] for i in range(3)], [c[i] - a[i] for i in range(3)])
            self.assertGreater(dot(geometric, normal), 0)
            for left, right in zip(triangle, triangle[1:] + triangle[:1]):
                edge = tuple(sorted((left, right)))
                edges[edge] += 1
                directions[edge] += 1 if left < right else -1
        self.assertEqual(set(edges.values()), {2})
        self.assertEqual(set(directions.values()), {0})
        json.dumps(mesh, allow_nan=False)
        return mesh

    def refused(self, native, **options):
        mesh = derive_primitive_csg_mesh(native, **options)
        self.assertFalse(mesh["success"], mesh)
        self.assertFalse(mesh["available"])
        self.assertFalse(mesh["complete"])
        self.assertTrue(mesh["errors"])
        self.assertEqual(mesh["vertices"], [])
        self.assertEqual(mesh["triangles"], [])
        self.assertEqual(mesh["triangle_normals"], [])
        self.assertFalse(mesh["native_cut_application_verified"])
        return mesh

    def test_box_through_cylinder_uses_intersected_height_and_polygonal_area(self):
        mesh = self.accepted()
        expected_removed = polygon_area(10, 32) * 40
        self.assertAlmostEqual(mesh["gross_polyhedral_volume_mm3"], 320000, places=6)
        self.assertAlmostEqual(mesh["removed_volume_mm3"], expected_removed, places=6)
        self.assertAlmostEqual(mesh["net_volume_mm3"], 320000 - expected_removed, places=6)
        self.assertFalse(material_contains(mesh, (0, 0, 0)))
        self.assertTrue(material_contains(mesh, (30, 0, 0)))
        self.assertGreater(2 * math.pi * 100 * 20 - expected_removed, 0)

    def test_box_blind_hole_keeps_its_floor_and_trims_overhanging_cutter(self):
        mesh = self.accepted(csg_native(cutters=[cut(dimensions={"DIAM": 20, "HEIG": 20}, position=(0, 0, 15))]))
        self.assertAlmostEqual(mesh["removed_volume_mm3"], polygon_area(10, 32) * 15, places=6)
        self.assertTrue(material_contains(mesh, (0, 0, 0)), "The blind-hole floor must retain material below z=5.")
        self.assertFalse(material_contains(mesh, (0, 0, 10)))
        self.assertTrue(material_contains(mesh, (15, 0, 10)))

    def test_coaxial_cylinder_difference_matches_regular_polygon_annulus(self):
        for count in (16, 48):
            with self.subTest(radial_segments=count):
                mesh = self.accepted(csg_native("CYLI", {"DIAM": 100, "HEIG": 40},
                    cutters=[cut(dimensions={"DIAM": 40, "HEIG": 80})]), segments=count)
                expected = (polygon_area(50, count) - polygon_area(20, count)) * 40
                self.assertAlmostEqual(mesh["net_volume_mm3"], expected, places=6)
                self.assertAlmostEqual(mesh["removed_volume_mm3"], polygon_area(20, count) * 40, places=6)
                self.assertLess(mesh["net_volume_mm3"], math.pi * (50**2 - 20**2) * 40)

    def test_rotated_nbox_slot_changes_the_actual_material_footprint(self):
        angle = math.pi / 4
        axes = ((math.cos(angle), math.sin(angle), 0), (-math.sin(angle), math.cos(angle), 0), (0, 0, 1))
        mesh = self.accepted(csg_native(cutters=[cut("BOX", {"XLEN": 20, "YLEN": 10, "ZLEN": 60}, axes=axes)]))
        self.assertAlmostEqual(mesh["removed_volume_mm3"], 20 * 10 * 40, places=6)
        self.assertFalse(material_contains(mesh, (7, 6, 0)))
        self.assertTrue(material_contains(mesh, (9, 0, 0)))

    def test_ncon_frustum_cut_uses_the_two_intersection_plane_radii(self):
        mesh = self.accepted(csg_native(dimensions={"XLEN": 100, "YLEN": 100, "ZLEN": 60},
            cutters=[cut("CONE", {"DBOT": 40, "DTOP": 20, "HEIG": 80})]))
        low, high, height = 18.75, 11.25, 60
        expected = 32 * math.sin(2 * math.pi / 32) / 2 * height / 3 * (low**2 + low * high + high**2)
        self.assertAlmostEqual(mesh["removed_volume_mm3"], expected, places=6)

    def test_positive_frustum_and_zero_radius_apex_are_supported(self):
        mesh = self.accepted(csg_native("CONE", {"DBOT": 100, "DTOP": 80, "HEIG": 40},
            cutters=[cut("BOX", {"XLEN": 200, "YLEN": 200, "ZLEN": 20}, position=(0, 0, 20))]))
        # Retained z range is -20..10, with radii 50 and 42.5.
        expected = 32 * math.sin(2 * math.pi / 32) / 2 * 30 / 3 * (50**2 + 50 * 42.5 + 42.5**2)
        self.assertAlmostEqual(mesh["net_volume_mm3"], expected, places=6)
        apex = self.accepted(csg_native(cutters=[cut("CONE", {"DBOT": 20, "DTOP": 0, "HEIG": 20})]))
        self.assertAlmostEqual(apex["removed_volume_mm3"], polygon_area(10, 32) * 20 / 3, places=6)

    def test_overlapping_cutters_remove_the_union_once(self):
        cutters = [cut("BOX", {"XLEN": 20, "YLEN": 20, "ZLEN": 60}, position=(x, 0, 0)) for x in (0, 10)]
        mesh = self.accepted(csg_native(cutters=cutters))
        self.assertAlmostEqual(mesh["removed_volume_mm3"], 30 * 20 * 40, places=6)
        coincident = self.accepted(csg_native(cutters=[cut(), cut()]))
        self.assertAlmostEqual(coincident["removed_volume_mm3"], polygon_area(10, 32) * 40, places=6)

    def test_nonintersecting_and_exactly_tangent_cutters_leave_material_unchanged(self):
        for x in (150, 60):
            with self.subTest(center_x=x):
                mesh = self.accepted(csg_native(cutters=[cut("BOX", {"XLEN": 20, "YLEN": 20, "ZLEN": 60}, position=(x, 0, 0))]))
                self.assertAlmostEqual(mesh["net_volume_mm3"], 320000, places=6)
                self.assertAlmostEqual(mesh["removed_volume_mm3"], 0, places=6)

    @requires_csg_engine
    def test_total_removal_returns_successful_empty_material_without_uncut_parent_fallback(self):
        mesh = derive_primitive_csg_mesh(csg_native(cutters=[cut("BOX", {"XLEN": 200, "YLEN": 200, "ZLEN": 100})]))
        for field in ("success", "available", "complete", "empty", "material_boundary_validated"):
            self.assertIs(mesh[field], True, field)
        self.assertEqual(mesh["vertices"], [])
        self.assertEqual(mesh["triangles"], [])
        self.assertEqual(mesh["triangle_normals"], [])
        self.assertEqual(mesh["net_volume_mm3"], 0)
        self.assertEqual(mesh["removed_volume_mm3"], 320000)
        self.assertEqual(mesh["geometry_sha256"], csg_geometry_sha256([], []))
        self.assertTrue(mesh["engine"]["empty"])
        self.assertTrue(mesh["validation"]["empty"])
        self.assertEqual(mesh["validation"]["findings"], [])
        self.assertFalse(mesh["native_cut_application_verified"])

    def test_enclosed_void_retains_inward_inner_shell_as_material_boundary(self):
        native = csg_native(dimensions={"XLEN": 20, "YLEN": 20, "ZLEN": 20},
            cutters=[cut("BOX", {"XLEN": 4, "YLEN": 4, "ZLEN": 4})])
        mesh = self.accepted(native)
        self.assertAlmostEqual(mesh["net_volume_mm3"], 8000 - 64, places=7)
        self.assertEqual(component_signed_volumes(mesh), [-64, 8000])
        self.assertFalse(material_contains(mesh, (0, 0, 0)))
        self.assertTrue(material_contains(mesh, (5, 0, 0)))
        validation = validate_triangle_mesh(mesh["vertices"], mesh["triangles"])
        self.assertFalse(validation["valid"], "The strict all-components-outward validator stays unchanged.")
        self.assertTrue(validation["checks"]["closed_manifold"])
        self.assertFalse(validation["checks"]["orientation_outward"])
        self.assertIn("component_winding_is_inward", [finding["code"] for finding in validation["findings"]])
        self.assertIn("component_winding_is_inward", [finding["code"] for finding in mesh["validation"]["findings"]])

    def test_large_world_translation_and_rotation_preserve_local_material(self):
        angle = 0.47
        axes = ((math.cos(angle), math.sin(angle), 0), (-math.sin(angle), math.cos(angle), 0), (0, 0, 1))
        position = (100000000.0, -200000000.0, 300000000.0)
        mesh = self.accepted(csg_native(position=position, axes=axes))
        self.assertAlmostEqual(mesh["removed_volume_mm3"], polygon_area(10, 32) * 40, delta=1e-3)
        self.assertFalse(material_contains(mesh, mapped((0, 0, 0), axes, position)))
        self.assertTrue(material_contains(mesh, mapped((30, 0, 0), axes, position)))
        self.assertGreater(min(p[0] for p in mesh["vertices"]), 99999900)
        for face, normal in zip(mesh["triangles"], mesh["triangle_normals"]):
            a, b, c = (mesh["vertices"][index] for index in face)
            geometric = cross([b[i] - a[i] for i in range(3)], [c[i] - a[i] for i in range(3)])
            length = math.hypot(*geometric)
            self.assertLess(math.dist(normal, [value / length for value in geometric]), 1e-12)

    def test_spherical_dish_parent_uses_existing_radi_zero_parameter_convention(self):
        mesh = self.accepted(csg_native("DISH", {"DIAM": 100, "HEIG": 20, "RADI": 0},
            cutters=[cut("BOX", {"XLEN": 2, "YLEN": 2, "ZLEN": 2}, position=(1000, 0, 0))]), segments=16)
        analytic = math.pi * 20 / 6 * (3 * 50**2 + 20**2)
        self.assertGreater(mesh["net_volume_mm3"], analytic * 0.96)
        self.assertLessEqual(mesh["net_volume_mm3"], analytic)
        self.assertAlmostEqual(mesh["removed_volume_mm3"], 0, places=6)
        self.assertAlmostEqual(min(p[2] for p in mesh["vertices"]), 0)
        self.assertAlmostEqual(max(p[2] for p in mesh["vertices"]), 20)

    def test_old_positive_only_mesh_entry_point_still_refuses_negative_children(self):
        result = derive_primitive_mesh(csg_native(), coordinate_space="WORLD")
        self.assertFalse(result["success"])
        self.assertIn("negative_children_present_or_unreported", result["errors"])

    def test_invalid_wrapper_membership_flags_frames_and_nested_cuts_stop_before_worker(self):
        faults = ("top_incomplete", "top_read_error", "top_negative", "root_flag", "child_flag", "missing_flag",
                  "member_missing", "member_extra", "duplicate_ref", "target_mismatch", "nested", "child_incomplete",
                  "child_unit", "child_type", "root_unit", "missing_world", "world_axis", "relative_disagreement",
                  "owner_missing", "owner_duplicate", "owner_self", "storage_self", "root_relative", "negative_owner",
                  "child_owner_member", "member_order", "top_member_mismatch", "top_children", "root_children",
                  "top_edit_reason", "root_edit_reason", "child_edit_reason", "revision_missing", "revision_invalid")
        for fault in faults:
            with self.subTest(fault=fault):
                native = csg_native(cutters=[cut(), cut(position=(30, 0, 0))])
                child = native["negative_children"][0]
                if fault == "top_incomplete": native["complete"] = False
                elif fault == "top_read_error": native["read_errors"] = {"DIAM": "unreadable"}
                elif fault == "top_negative": native["negative"] = True
                elif fault == "root_flag": native["is_negative"] = True
                elif fault == "child_flag": child["negative"] = False
                elif fault == "missing_flag": del child["is_negative"]
                elif fault == "member_missing": native["target_geometry"]["member_refnos"] = []
                elif fault == "member_extra": native["target_geometry"]["member_refnos"].append("=1/99")
                elif fault == "duplicate_ref": native["negative_children"][1]["element"]["refno"] = "=01/020"
                elif fault == "target_mismatch": native["target_geometry"]["dimensions"]["XLEN"] = 101
                elif fault == "nested": child["negative_children"] = [deepcopy(native["negative_children"][1])]
                elif fault == "child_incomplete": child["complete"] = False
                elif fault == "child_unit": child["unit"] = "m"
                elif fault == "child_type": child["element"]["type"] = "CYLI"
                elif fault == "root_unit": native["unit"] = "m"
                elif fault == "missing_world": del child["frame"]["world"]
                elif fault == "world_axis": child["frame"]["world"]["orientation"]["x_axis"] = [2, 0, 0]
                elif fault == "relative_disagreement": child["frame"]["relative_to_owner"]["position"] = [20, 0, 0]
                elif fault == "owner_missing": native["owner_member_refnos"] = []
                elif fault == "owner_duplicate": native["owner_member_refnos"] *= 2
                elif fault == "owner_self": native["owner"]["refno"] = "=1/10"
                elif fault == "storage_self": native["frame"]["storage_frame_refno"] = "=1/10"
                elif fault == "root_relative": native["frame"]["relative_to_owner"] = deepcopy(child["frame"]["native"])
                elif fault == "negative_owner": native["negative_owner"] = {"refno": "=1/1"}
                elif fault == "child_owner_member": native["owner_member_refnos"].append(child["element"]["refno"])
                elif fault == "member_order": native["target_geometry"]["member_refnos"].reverse()
                elif fault == "top_member_mismatch": native["member_refnos"] = []
                elif fault == "top_children": native["children"] = [{"refno": "=1/99"}]
                elif fault == "root_children": native["target_geometry"]["children"] = [{"refno": "=1/99"}]
                elif fault == "top_edit_reason": native["edit_reasons"] = ["unsupported primitive"]
                elif fault == "root_edit_reason": native["target_geometry"]["edit_reasons"] = ["unreadable parameter"]
                elif fault == "child_edit_reason": child["edit_reasons"] = ["unreadable parameter"]
                elif fault == "revision_missing": del native["revision"]
                else: native["revision"] = "not-a-native-revision"
                if fault not in {"target_mismatch", "member_missing", "member_extra"}:
                    sync_target(native)
                with patch(f"{MODULE}._run_csg_worker") as worker:
                    self.refused(native)
                worker.assert_not_called()

    def test_invalid_dimensions_radial_segments_and_cut_count_are_bounded(self):
        for dimensions in ({"DIAM": -1, "HEIG": 60}, {"DIAM": True, "HEIG": 60},
                           {"DIAM": float("nan"), "HEIG": 60}, {"DIAM": "20", "HEIG": 60}, {"DIAM": 20}):
            with self.subTest(dimensions=dimensions), patch(f"{MODULE}._run_csg_worker") as worker:
                self.refused(csg_native(cutters=[cut(dimensions=dimensions)]))
                worker.assert_not_called()
        for segments in (11, 100, 129, True, 12.5, "48", None):
            with self.subTest(segments=segments), patch(f"{MODULE}._run_csg_worker") as worker:
                self.refused(csg_native(), radial_segments=segments)
                worker.assert_not_called()
        for count in (0, 33):
            with self.subTest(cutters=count), patch(f"{MODULE}._run_csg_worker") as worker:
                self.refused(csg_native(cutters=[cut() for _ in range(count)]))
                worker.assert_not_called()

    def test_exactly_32_nonintersecting_cutters_are_admitted(self):
        native = csg_native(cutters=[cut("BOX", {"XLEN": 2, "YLEN": 2, "ZLEN": 2},
            position=(1000 + 10 * index, 0, 0)) for index in range(32)])
        mesh = self.accepted(native, segments=12)
        self.assertAlmostEqual(mesh["removed_volume_mm3"], 0)

    def test_unsupported_dish_radius_and_degenerate_cones_stop_before_worker(self):
        sources = [
            csg_native("DISH", {"DIAM": 100, "HEIG": 20, "RADI": 5}),
            csg_native("CONE", {"DBOT": 0, "DTOP": 0, "HEIG": 40}),
            csg_native(cutters=[cut("CONE", {"DBOT": 0, "DTOP": 0, "HEIG": 60})]),
            csg_native(cutters=[cut(dimensions={"DIAM": 20, "HEIG": 60, "RADI": 1})]),
        ]
        for native in sources:
            with self.subTest(shape=native["shape"], cutter=native["negative_children"][0]["dimensions"]):
                with patch(f"{MODULE}._run_csg_worker") as worker:
                    self.refused(native)
                worker.assert_not_called()

    def test_native_payload_byte_limit_stops_before_worker(self):
        native = csg_native()
        native["native_cut_scope"] = "x" * 4096
        with patch(f"{MODULE}.MAX_IPC_BYTES", 4096), patch(f"{MODULE}._run_csg_worker") as worker:
            mesh = self.refused(native)
        self.assertIn("csg_native_input_limit_exceeded", mesh["errors"])
        worker.assert_not_called()

    def test_cumulative_operand_vertex_and_triangle_limits_stop_before_worker(self):
        native = csg_native(cutters=[cut("BOX", {"XLEN": 2, "YLEN": 2, "ZLEN": 2})])
        for limit_name, limit in (("MAX_VERTICES", 15), ("MAX_TRIANGLES", 23)):
            with self.subTest(limit=limit_name), patch(f"{MODULE}.{limit_name}", limit):
                with patch(f"{MODULE}._run_csg_worker") as worker:
                    mesh = self.refused(native)
                self.assertIn("csg_input_mesh_limit_exceeded", mesh["errors"])
                self.assertEqual(mesh["diagnostics"], {"vertices": 16, "triangles": 24})
                worker.assert_not_called()

    def test_worker_timeout_is_reported_without_retry_or_partial_mesh(self):
        with patch(f"{MODULE}.subprocess.run", side_effect=subprocess.TimeoutExpired("csg worker", 30)) as worker:
            mesh = self.refused(csg_native())
        self.assertIn("csg_worker_timeout", mesh["errors"])
        worker.assert_called_once()
        self.assertEqual(worker.call_args.kwargs["timeout"], 30)
        self.assertIs(worker.call_args.kwargs["check"], False)
        for stream in ("stdin", "stdout", "stderr"):
            self.assertEqual(worker.call_args.kwargs[stream], subprocess.DEVNULL)
        if sys.platform == "win32":
            self.assertEqual(worker.call_args.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
        command = worker.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(Path(command[1]).name, "primitive_csg_worker.py")
        self.assertFalse(Path(command[2]).parent.exists(), "The timed-out request's temporary IPC files must be removed.")

    def test_worker_transport_rejects_process_failure_and_bad_responses(self):
        cases = [
            (1, b'{"success":true}', "csg_worker_failed"),
            (0, None, "csg_worker_failed"),
            (0, b"not json", "csg_worker_invalid_response"),
            (0, b"\xff", "csg_worker_invalid_response"),
            (0, b"[]", "csg_worker_invalid_response"),
            (0, b'{"success":1}', "csg_worker_invalid_response"),
            (0, b'{"success":false,"error_code":"csg_engine_unavailable"}', "csg_engine_unavailable"),
        ]
        for returncode, response, error in cases:
            def finish(command, **_kwargs):
                self.assertTrue(Path(command[2]).is_file())
                self.assertEqual(json.loads(Path(command[2]).read_bytes()), {"operands": []})
                if response is not None:
                    Path(command[3]).write_bytes(response)
                return subprocess.CompletedProcess(command, returncode)

            with self.subTest(returncode=returncode, response=response):
                with patch(f"{MODULE}.subprocess.run", side_effect=finish) as worker:
                    with self.assertRaises(CSGWorkerError) as caught:
                        csg_mesh._run_csg_worker([])
                self.assertEqual(caught.exception.code, error)
                worker.assert_called_once()
                self.assertFalse(Path(worker.call_args.args[0][2]).parent.exists())

    def test_worker_transport_has_independent_input_and_output_byte_limits(self):
        with patch(f"{MODULE}.MAX_IPC_BYTES", 1), patch(f"{MODULE}.subprocess.run") as process:
            with self.assertRaises(CSGWorkerError) as caught:
                csg_mesh._run_csg_worker([])
        self.assertEqual(caught.exception.code, "csg_worker_input_limit_exceeded")
        process.assert_not_called()

        def oversized_output(command, **_kwargs):
            Path(command[3]).write_bytes(b"x" * 129)
            return subprocess.CompletedProcess(command, 0)

        with patch(f"{MODULE}.MAX_IPC_BYTES", 128), patch(f"{MODULE}.subprocess.run", side_effect=oversized_output) as process:
            with self.assertRaises(CSGWorkerError) as caught:
                csg_mesh._run_csg_worker([])
        self.assertEqual(caught.exception.code, "csg_worker_output_limit_exceeded")
        process.assert_called_once()

    @requires_csg_engine
    def test_worker_itself_checks_operand_budgets(self):
        receipt = box_worker_receipt()
        operand = {"vertices": receipt["vertices"], "triangles": receipt["triangles"]}
        for limit_name in ("MAX_VERTICES", "MAX_TRIANGLES"):
            with self.subTest(limit=limit_name), patch.object(csg_worker, limit_name, 1):
                result = csg_worker._run({"operands": [operand, operand]})
            self.assertFalse(result["success"])
            self.assertEqual(result["error_code"], "csg_input_mesh_limit_exceeded")

    def test_missing_engine_is_reported_without_import_failure(self):
        with patch.dict(sys.modules, {"manifold3d": None}):
            result = csg_worker._run({"operands": []})
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "csg_engine_unavailable")

    def test_malformed_worker_geometry_receipts_and_mass_never_release_partial_mesh(self):
        receipt = box_worker_receipt()
        native = csg_native(cutters=[cut("BOX", {"XLEN": 2, "YLEN": 2, "ZLEN": 2}, position=(1000, 0, 0))])
        faults = ("engine_name", "engine_precision", "engine_api", "engine_operation", "engine_status", "engine_version",
                  "empty_disagrees", "empty_with_geometry", "nonempty_without_geometry", "vertices_type", "point_length",
                  "point_nan", "point_boolean", "vertex_limit", "triangle_limit", "open_boundary", "reversed_boundary",
                  "gross_mismatch", "net_mismatch", "net_negative", "net_over_gross", "net_nan")
        for fault in faults:
            result = deepcopy(receipt)
            if fault == "engine_name": result["engine"]["name"] = "not-manifold"
            elif fault == "engine_precision": result["engine"]["numeric_precision"] = "float32"
            elif fault == "engine_api": result["engine"]["mesh_api"] = "Mesh"
            elif fault == "engine_operation": result["engine"]["operation"] = "Add"
            elif fault == "engine_status": result["engine"]["status"] = "NotManifold"
            elif fault == "engine_version": result["engine"]["version"] = ""
            elif fault == "empty_disagrees": result["engine"]["empty"] = True
            elif fault == "empty_with_geometry": result["empty"] = result["engine"]["empty"] = True
            elif fault == "nonempty_without_geometry": result["vertices"] = result["triangles"] = []
            elif fault == "vertices_type": result["vertices"] = None
            elif fault == "point_length": result["vertices"][0] = [1, 2]
            elif fault == "point_nan": result["vertices"][0][0] = float("nan")
            elif fault == "point_boolean": result["vertices"][0][0] = True
            elif fault == "vertex_limit": result["vertices"] = [[0, 0, 0]] * 10001
            elif fault == "triangle_limit": result["triangles"] = [[0, 1, 2]] * 20001
            elif fault == "open_boundary": result["triangles"].pop()
            elif fault == "reversed_boundary": result["triangles"] = [list(reversed(face)) for face in result["triangles"]]
            elif fault == "gross_mismatch": result["gross_polyhedral_volume_mm3"] += 10
            elif fault == "net_mismatch": result["net_volume_mm3"] += 10
            elif fault == "net_negative": result["net_volume_mm3"] = -10
            elif fault == "net_over_gross": result["net_volume_mm3"] = result["gross_polyhedral_volume_mm3"] + 10
            else: result["net_volume_mm3"] = float("nan")
            with self.subTest(fault=fault), patch(f"{MODULE}._run_csg_worker", return_value=result) as worker:
                self.refused(native, radial_segments=32)
            worker.assert_called_once()

    def test_output_receipt_byte_budget_and_unexpected_worker_failure_are_structured(self):
        native = csg_native(cutters=[cut("BOX", {"XLEN": 2, "YLEN": 2, "ZLEN": 2}, position=(1000, 0, 0))])
        receipt = box_worker_receipt()
        input_size = len(json.dumps(native, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))
        with patch(f"{MODULE}.MAX_IPC_BYTES", input_size + 1), patch(f"{MODULE}._run_csg_worker", return_value=receipt):
            mesh = self.refused(native, radial_segments=32)
        self.assertIn("csg_result_output_limit_exceeded", mesh["errors"])
        with patch(f"{MODULE}._run_csg_worker", side_effect=RuntimeError("worker transport broke")) as worker:
            mesh = self.refused(native)
        self.assertIn("csg_derivation_failed", mesh["errors"])
        self.assertEqual(mesh["diagnostics"]["error_type"], "RuntimeError")
        worker.assert_called_once()

    def test_saved_real_native_wrappers_enter_same_parameter_csg_without_native_shape_claim(self):
        directory = ROOT / "artifacts" / "primitive-csg-native-readonly-20261120"
        if not directory.exists():
            self.skipTest("Saved native read-only receipts are optional.")
        for index in range(3):
            with self.subTest(source=index):
                payload = json.loads((directory / f"geometry-{index}.mcp.json").read_text(encoding="utf-8-sig"))
                native = payload["structuredContent"]["result"]
                mesh = self.accepted(native)
                self.assertEqual(mesh["refno"], native["element"]["refno"])
                self.assertEqual(mesh["revision"], native["revision"])
                self.assertGreater(mesh["removed_volume_mm3"], 0)
                self.assertFalse(mesh["native_cut_application_verified"])


if __name__ == "__main__":
    unittest.main()

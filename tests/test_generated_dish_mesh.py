from __future__ import annotations

import copy
import json
import math
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.generated_dish_mesh import (
    derive_generated_dish_mesh,
    plan_generated_dish_tessellation,
)
from aveva_e3d_mcp.primitive_mesh import derive_primitive_mesh


def _profile(a, h, k):
    return {"profile_kind": "spherical_cap" if k == 0 else "torispherical",
            "base_radius_mm": a, "height_mm": h, "effective_knuckle_radius_mm": k}


def _frame(origin=(0.0, 0.0, 0.0), axes=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))):
    return {"world": {"position": list(origin), "orientation": {
        key: list(axis) for key, axis in zip(("x_axis", "y_axis", "z_axis"), axes)
    }}}


def _dot(a, b):
    return math.fsum(x * y for x, y in zip(a, b))


def _sub(a, b):
    return [x - y for x, y in zip(a, b)]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _segment_distance(point, a, b):
    ab = _sub(b, a)
    t = max(0.0, min(1.0, _dot(_sub(point, a), ab) / _dot(ab, ab)))
    return math.dist(point, [a[i] + t * ab[i] for i in range(3)])


def _triangle_distance(point, a, b, c):
    # Independent plane / barycentric test, with closest edge fallback.
    ab, ac, ap = _sub(b, a), _sub(c, a), _sub(point, a)
    normal = _cross(ab, ac)
    nn = _dot(normal, normal)
    projection = [point[i] - normal[i] * _dot(ap, normal) / nn for i in range(3)]
    v = _sub(projection, a)
    ab2, ac2, abac = _dot(ab, ab), _dot(ac, ac), _dot(ab, ac)
    denominator = ab2 * ac2 - abac * abac
    beta = (_dot(v, ab) * ac2 - _dot(v, ac) * abac) / denominator
    gamma = (_dot(v, ac) * ab2 - _dot(v, ab) * abac) / denominator
    if min(beta, gamma, 1.0 - beta - gamma) >= -1e-12:
        return math.dist(point, projection)
    return min(_segment_distance(point, a, b), _segment_distance(point, b, c),
               _segment_distance(point, c, a))


def _known_profile_distance(point):
    # The independent a=100, h=40, k=10 construction has hub R=160,
    # centre (r,z)=(0,-120), joint (96,8), and knuckle centre (90,0).
    r, z = math.hypot(point[0], point[1]), point[2]
    joint = math.atan2(3.0, 4.0)
    distances = []
    for center_r, center_z, radius, lo, hi in ((0.0, -120.0, 160.0, 0.0, joint),
                                               (90.0, 0.0, 10.0, joint, math.pi / 2)):
        angle = max(lo, min(hi, math.atan2(r - center_r, z - center_z)))
        distances.append(math.hypot(r - center_r - radius * math.sin(angle),
                                    z - center_z - radius * math.cos(angle)))
    distances.append(abs(z) if r <= 100.0 else math.hypot(r - 100.0, z))
    return min(distances)


class GeneratedDishMeshTests(unittest.TestCase):
    def assert_closed_outward(self, mesh):
        self.assertTrue(mesh["success"], mesh["errors"])
        self.assertTrue(mesh["closed_manifold"])
        self.assertEqual(mesh["counts"], {"vertices": len(mesh["vertices"]), "triangles": len(mesh["triangles"])})
        edges, winding = Counter(), Counter()
        for triangle, normal in zip(mesh["triangles"], mesh["triangle_normals"]):
            self.assertEqual(len(set(triangle)), 3)
            a, b, c = (mesh["vertices"][index] for index in triangle)
            self.assertGreater(_dot(_cross(_sub(b, a), _sub(c, a)), normal), 0.0)
            self.assertAlmostEqual(math.hypot(*normal), 1.0, places=12)
            for left, right in zip(triangle, triangle[1:] + triangle[:1]):
                key = tuple(sorted((left, right)))
                edges[key] += 1
                winding[key] += 1 if left < right else -1
        self.assertEqual(set(edges.values()), {2})
        self.assertEqual(set(winding.values()), {0})
        self.assertEqual(len(mesh["vertices"]) - len(edges) + len(mesh["triangles"]), 2)
        self.assertGreater(mesh["parameter_mesh_volume_mm3"], 0.0)

    def assert_unavailable(self, mesh):
        self.assertFalse(mesh["success"])
        self.assertFalse(mesh["available"])
        self.assertFalse(mesh["complete"])
        self.assertTrue(mesh["errors"])
        self.assertEqual(mesh["vertices"], [])
        self.assertEqual(mesh["triangles"], [])
        self.assertEqual(mesh["triangle_normals"], [])
        self.assertEqual(mesh["counts"], {"vertices": 0, "triangles": 0})
        json.dumps(mesh, allow_nan=False)

    def test_explicit_target_profile_has_base_origin_and_closed_boundary(self):
        mesh = derive_generated_dish_mesh(_profile(705, 380, 270.3090126363434), 12, chord_tolerance_mm=0.5)
        self.assert_closed_outward(mesh)
        self.assertEqual(mesh["bounds"], {"min": [-705.0, -705.0, 0.0], "max": [705.0, 705.0, 380.0],
                                          "size": [1410.0, 1410.0, 380.0]})
        self.assertEqual(mesh["vertices"][0], [0.0, 0.0, 380.0])
        self.assertEqual(mesh["vertices"][-1], [0.0, 0.0, 0.0])
        self.assertAlmostEqual(mesh["parameters"]["hub_radius_mm"], 1186.4661739246785, places=9)
        self.assertLessEqual(mesh["tessellation"]["max_surface_deviation_mm"], 0.5)
        self.assertFalse(mesh["native_tessellation"])
        self.assertFalse(mesh["cuts_applied"])
        self.assertFalse(mesh["whole_equipment_geometry_verified"])

    def test_explicit_physical_knuckle_is_preserved(self):
        small = derive_generated_dish_mesh(_profile(705, 380, 70), 24)
        generated = derive_generated_dish_mesh(_profile(705, 380, 270.3090126363434), 24)
        self.assert_closed_outward(small)
        self.assert_closed_outward(generated)
        self.assertEqual(small["input_profile_parameters"], _profile(705.0, 380.0, 70.0))
        self.assertEqual(small["parameters"]["effective_knuckle_radius_mm"], 70.0)
        self.assertGreater(generated["parameter_mesh_volume_mm3"], small["parameter_mesh_volume_mm3"] * 1.1)

    def test_unresolved_knuckle_above_native_limit_is_rejected(self):
        self.assert_unavailable(derive_generated_dish_mesh(_profile(300, 150, 200), 12))
        mesh = derive_generated_dish_mesh(_profile(300, 150, 103.6474508437579), 12)
        self.assert_closed_outward(mesh)
        self.assertAlmostEqual(mesh["parameters"]["effective_knuckle_radius_mm"], 103.6474508437579, places=11)
        self.assertAlmostEqual(mesh["parameters"]["hub_radius_mm"], 542.7050983124842, places=10)

    def test_known_two_circle_joint_has_shared_position_and_tangent(self):
        mesh = derive_generated_dish_mesh(_profile(100, 40, 10), 24)
        p = mesh["parameters"]
        self.assertAlmostEqual(p["hub_radius_mm"], 160.0)
        self.assertAlmostEqual(p["joint_radius_mm"], 96.0)
        self.assertAlmostEqual(p["joint_z_mm"], 8.0)
        self.assertAlmostEqual(math.hypot(p["joint_radius_mm"], p["joint_z_mm"] + 120), 160.0)
        self.assertAlmostEqual(math.hypot(p["joint_radius_mm"] - 90, p["joint_z_mm"]), 10.0)
        self.assertAlmostEqual(p["joint_radius_mm"] / 160, (p["joint_radius_mm"] - 90) / 10)
        self.assertAlmostEqual((p["joint_z_mm"] + 120) / 160, p["joint_z_mm"] / 10)
        joint = [ring for ring in mesh["profile_rings"] if ring["arc"] == "hub"][-1]
        self.assertAlmostEqual(joint["radius_mm"], 96.0)
        self.assertAlmostEqual(joint["z_mm"], 8.0)

    def test_spherical_branch_converges_to_independent_analytic_cap_volume(self):
        coarse = derive_generated_dish_mesh(_profile(300, 150, 0), 12, chord_tolerance_mm=2.0)
        fine = derive_generated_dish_mesh(_profile(300, 150, 0), 12, chord_tolerance_mm=0.25)
        expected = math.pi * 150 ** 2 * (375 - 150 / 3)
        for mesh in (coarse, fine):
            self.assert_closed_outward(mesh)
            self.assertLess(mesh["parameter_mesh_volume_mm3"], expected)
        self.assertGreater(fine["parameter_mesh_volume_mm3"], coarse["parameter_mesh_volume_mm3"])
        self.assertLess((expected - fine["parameter_mesh_volume_mm3"]) / expected, 0.003)

    def test_deep_spherical_cap_preserves_equator_beyond_base_diameter(self):
        mesh = derive_generated_dish_mesh(_profile(100, 150, 0), 12, chord_tolerance_mm=0.5)
        self.assert_closed_outward(mesh)
        radius = 325 / 3
        self.assertAlmostEqual(mesh["bounds"]["max"][0], radius)
        self.assertGreater(mesh["bounds"]["size"][0], 200.0)
        self.assertTrue(any(abs(ring["radius_mm"] - radius) < 1e-12 for ring in mesh["profile_rings"]))
        self.assertEqual(mesh["profile_rings"][-1]["radius_mm"], 100.0)
        self.assertEqual(mesh["profile_rings"][-1]["z_mm"], 0.0)

    def test_exact_equal_dimensions_use_native_hemisphere_branch(self):
        mesh = derive_generated_dish_mesh(_profile(100, 100, 100), 24)
        self.assert_closed_outward(mesh)
        self.assertEqual(mesh["parameters"]["profile"], "hemisphere")
        self.assertEqual(mesh["parameters"]["effective_knuckle_radius_mm"], 100.0)
        self.assertLess(mesh["parameter_mesh_volume_mm3"], 2 * math.pi * 100 ** 3 / 3)

    def test_unresolved_positive_knuckle_branches_and_native_degeneracies_fail(self):
        for raw in ([100, 150, 10], [100, 100 + 5e-7, 10], [100, 100 - 5e-7, 10],
                    [0, 40, 10], [100, 0, 10], [1e-7, 40, 10], [100, 40, 1e-7],
                    [100, 40, -1], [1e8, 40, 0], [1e6, 1e-5, 0]):
            with self.subTest(raw=raw):
                self.assert_unavailable(derive_generated_dish_mesh(_profile(*raw)))

    def test_non_finite_boolean_or_incomplete_raw_data_never_produces_geometry(self):
        for raw in (None, {}, "100,40,10", [100, 40], [100, 40, 10, 0],
                    [True, 40, 10], [100, "40", 10], [100, 40, float("nan")],
                    [100, float("inf"), 10], [10 ** 500, 40, 10]):
            with self.subTest(raw=str(raw)[:80]):
                self.assert_unavailable(derive_generated_dish_mesh(raw))

    def test_plan_refines_both_azimuth_and_meridian_under_hard_budgets(self):
        plan = plan_generated_dish_tessellation(_profile(705, 380, 270.3090126363434), 12, 0.5)
        self.assertEqual(plan["radial_segments"] % 4, 0)
        self.assertGreater(plan["radial_segments"], 12)
        self.assertEqual(len(plan["arc_segments"]), 2)
        self.assertGreater(plan["latitude_segments"], 2)
        self.assertLessEqual(plan["latitude_segments"], 128)
        self.assertLessEqual(plan["counts"]["vertices"], 10_000)
        self.assertLessEqual(plan["counts"]["triangles"], 20_000)
        self.assertAlmostEqual(plan["max_surface_deviation_mm"], plan["azimuth_deviation_bound_mm"]
                               + plan["meridian_deviation_bound_mm"])
        self.assertLessEqual(plan["max_surface_deviation_mm"], 0.5)

    def test_triangle_interiors_and_diagonals_stay_within_analytic_surface_bound(self):
        mesh = derive_generated_dish_mesh(_profile(100, 40, 10), 12, chord_tolerance_mm=1.2)
        bound = mesh["tessellation"]["max_surface_deviation_mm"]
        largest = 0.0
        for triangle in mesh["triangles"]:
            a, b, c = (mesh["vertices"][index] for index in triangle)
            samples = [[(a[i] + b[i] + c[i]) / 3 for i in range(3)],
                       *[[(left[i] + right[i]) / 2 for i in range(3)]
                         for left, right in ((a, b), (b, c), (c, a))]]
            for point in samples:
                deviation = _known_profile_distance(point)
                largest = max(largest, deviation)
                self.assertLessEqual(deviation, bound + 1e-10)
        self.assertGreater(largest, 0.0)

    def test_analytic_surface_samples_stay_within_facet_bound(self):
        mesh = derive_generated_dish_mesh(_profile(100, 40, 10), 12, chord_tolerance_mm=1.2)
        faces = [[mesh["vertices"][index] for index in triangle] for triangle in mesh["triangles"]]
        bound = mesh["tessellation"]["max_surface_deviation_mm"]
        joint = math.atan2(3.0, 4.0)
        for center_r, center_z, radius, lo, hi in ((0.0, -120.0, 160.0, 0.0, joint),
                                                   (90.0, 0.0, 10.0, joint, math.pi / 2)):
            for i in range(1, 9):
                angle = lo + (hi - lo) * i / 9
                r, z = center_r + radius * math.sin(angle), center_z + radius * math.cos(angle)
                for phi in (0.13, 0.77, 1.93, 4.21):
                    point = [r * math.cos(phi), r * math.sin(phi), z]
                    distance = min(_triangle_distance(point, *face) for face in faces)
                    self.assertLessEqual(distance, bound + 1e-10)

    def test_unattainable_tolerance_or_invalid_segment_limits_return_no_partial_mesh(self):
        for kwargs in ({"chord_tolerance_mm": 1e-9}, {"chord_tolerance_mm": 0},
                       {"chord_tolerance_mm": -1}, {"chord_tolerance_mm": True},
                       {"chord_tolerance_mm": float("inf")}, {"radial_segments": 11},
                       {"radial_segments": True}, {"max_radial_segments": 129},
                       {"radial_segments": 99, "max_radial_segments": 99},
                       {"radial_segments": 12, "max_radial_segments": 12, "chord_tolerance_mm": 0.5}):
            with self.subTest(kwargs=kwargs):
                self.assert_unavailable(derive_generated_dish_mesh(_profile(705, 380, 270.3090126363434), **kwargs))

    def test_fixed_segment_mode_preserves_cardinal_extents_and_input(self):
        raw = [100, 40, 10]
        original = copy.deepcopy(raw)
        mesh = derive_generated_dish_mesh(_profile(*raw), 13)
        self.assert_closed_outward(mesh)
        self.assertEqual(raw, original)
        self.assertEqual(mesh["radial_segments"], 16)
        self.assertEqual(mesh["tessellation"]["latitude_segments"], 13)
        self.assertEqual(mesh["bounds"]["size"], [200.0, 200.0, 40.0])
        self.assertIsNone(mesh["tessellation"]["chord_tolerance_mm"])

    def test_world_transform_uses_generated_base_origin_and_rotates_entire_mesh(self):
        raw = [100, 40, 10]
        local = derive_generated_dish_mesh(_profile(*raw), 24)
        origin = [1000.0, 2900.0, 100.0]
        axes = [[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]]
        frame = _frame(origin, axes)
        original = copy.deepcopy(frame)
        world = derive_generated_dish_mesh(_profile(*raw), 24, coordinate_space="WORLD", frame=frame)
        self.assert_closed_outward(world)
        self.assertEqual(frame, original)
        self.assertEqual(world["triangles"], local["triangles"])
        self.assertEqual(world["vertices"][-1], origin)
        self.assertEqual(world["vertices"][0], [1000.0, 2940.0, 100.0])
        self.assertEqual(world["bounds"]["min"], [900.0, 2900.0, 0.0])
        self.assertEqual(world["bounds"]["max"], [1100.0, 2940.0, 200.0])
        self.assertAlmostEqual(world["parameter_mesh_volume_mm3"], local["parameter_mesh_volume_mm3"], places=5)
        self.assertEqual(world["profile_rings_coordinate_frame"], "generated_local")

    def test_arbitrary_world_rotation_preserves_volume_and_facet_tolerance(self):
        angle = 0.483
        axes = [[math.cos(angle), math.sin(angle), 0.0], [-math.sin(angle), math.cos(angle), 0.0], [0, 0, 1]]
        mesh = derive_generated_dish_mesh(_profile(100, 40, 10), 12, chord_tolerance_mm=0.5,
                                          coordinate_space="WORLD", frame=_frame([50, -70, 90], axes))
        self.assert_closed_outward(mesh)
        self.assertLessEqual(mesh["tessellation"]["max_surface_deviation_mm"], 0.5)
        self.assertEqual(mesh["vertices"][-1], [50.0, -70.0, 90.0])
        self.assertFalse(mesh["native_tessellation"])

    def test_missing_non_rigid_reversed_and_collapsed_world_frames_fail_closed(self):
        frames = [None, {}, {"world": {}}, _frame(axes=((2, 0, 0), (0, 1, 0), (0, 0, 1))),
                  _frame(axes=((1, 0, 0), (1, 1, 0), (0, 0, 1))),
                  _frame(axes=((1, 0, 0), (0, 1, 0), (0, 0, -1))),
                  _frame(origin=(float("nan"), 0, 0)), _frame(origin=(1e300, 0, 0))]
        for frame in frames:
            with self.subTest(frame=frame):
                self.assert_unavailable(derive_generated_dish_mesh(_profile(100, 40, 10), coordinate_space="WORLD", frame=frame))
        self.assert_unavailable(derive_generated_dish_mesh(_profile(100, 40, 10), coordinate_space="OWNER"))

    def test_json_output_is_finite_and_success_does_not_claim_native_certification(self):
        mesh = derive_generated_dish_mesh(_profile(100, 40, 10))
        self.assert_closed_outward(mesh)
        json.dumps(mesh, allow_nan=False)
        self.assertIn("analytic_dish_profile", mesh["mesh_source"])
        self.assertIn("triangle interiors", mesh["tessellation"]["error_bound_scope"])
        self.assertIn("native tessellation", mesh["tessellation"]["error_bound_scope"])

    def test_existing_primitive_mesh_still_rejects_nonzero_database_radi(self):
        geometry = {"success": True, "complete": True, "read_errors": [], "negative": False,
                    "negative_children": [], "children": [], "shape": "DISH",
                    "dimensions": {"DIAM": 1410, "HEIG": 380, "RADI": 70},
                    "element": {"refno": "=1/2", "type": "DISH"}, "revision": "old-reader",
                    "frame": {"requested": "OWNER", "refno": "=1/1"}}
        mesh = derive_primitive_mesh(geometry)
        self.assertFalse(mesh["success"])
        self.assertEqual(mesh["vertices"], [])


if __name__ == "__main__":
    unittest.main()

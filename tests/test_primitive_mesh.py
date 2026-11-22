from __future__ import annotations

import copy
import json
import math
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.primitive_mesh import derive_primitive_mesh, plan_primitive_tessellation


def _native(shape: str, dimensions: dict[str, float], **fields) -> dict:
    return {
        "success": True,
        "complete": True,
        "read_errors": [],
        "negative": False,
        "negative_children": [],
        "children": [],
        "shape": shape,
        "dimensions": dimensions,
        "element": {"refno": "=1/2", "type": shape, "name": "TEST"},
        "revision": "r-primitive-1",
        "frame": {"requested": "OWNER", "refno": "=1/1"},
        **fields,
    }


def _signed_volume(mesh: dict) -> float:
    total = 0.0
    vertices = mesh["vertices"]
    for i, j, k in mesh["triangles"]:
        a, b, c = vertices[i], vertices[j], vertices[k]
        cross = (b[1] * c[2] - b[2] * c[1],
                 b[2] * c[0] - b[0] * c[2],
                 b[0] * c[1] - b[1] * c[0])
        total += sum(a[axis] * cross[axis] for axis in range(3)) / 6.0
    return total


class PrimitiveMeshTests(unittest.TestCase):
    def assert_closed_and_outward(self, mesh: dict) -> None:
        vertices = mesh["vertices"]
        triangles = mesh["triangles"]
        normals = mesh["triangle_normals"]
        self.assertEqual(len(normals), len(triangles))
        edge_counts: Counter[tuple[int, int]] = Counter()
        edge_directions: Counter[tuple[int, int]] = Counter()
        for triangle, normal in zip(triangles, normals):
            self.assertEqual(len(set(triangle)), 3)
            self.assertAlmostEqual(math.sqrt(sum(value * value for value in normal)), 1.0, places=12)
            self.assertTrue(all(math.isfinite(value) for value in normal))
            a, b, c = (vertices[index] for index in triangle)
            cross = ((b[1] - a[1]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[1] - a[1]),
                     (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2]),
                     (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
            dot = sum(cross[axis] * normal[axis] for axis in range(3))
            self.assertGreater(dot, 0.0)
            for left, right in ((triangle[0], triangle[1]), (triangle[1], triangle[2]), (triangle[2], triangle[0])):
                edge = tuple(sorted((left, right)))
                edge_counts[edge] += 1
                edge_directions[edge] += 1 if left < right else -1
        self.assertTrue(edge_counts)
        self.assertEqual(set(edge_counts.values()), {2})
        self.assertEqual(set(edge_directions.values()), {0})
        self.assertGreater(_signed_volume(mesh), 0.0)

    def test_box_is_centered_closed_and_has_expected_volume(self) -> None:
        mesh = derive_primitive_mesh(_native("BOX", {"XLEN": 2.0, "YLEN": 4.0, "ZLEN": 6.0}))
        self.assertTrue(mesh["success"], mesh["errors"])
        self.assertTrue(mesh["available"])
        self.assertEqual(mesh["counts"], {"vertices": 8, "triangles": 12})
        self.assertTrue(mesh["closed_manifold"])
        self.assertEqual(mesh["vertices"], [
            [-1.0, -2.0, -3.0], [1.0, -2.0, -3.0], [1.0, 2.0, -3.0], [-1.0, 2.0, -3.0],
            [-1.0, -2.0, 3.0], [1.0, -2.0, 3.0], [1.0, 2.0, 3.0], [-1.0, 2.0, 3.0],
        ])
        self.assertAlmostEqual(_signed_volume(mesh), 48.0)
        self.assert_closed_and_outward(mesh)

    def test_cylinder_is_centered_closed_and_matches_polygonal_volume(self) -> None:
        segments = 24
        mesh = derive_primitive_mesh(_native("CYLI", {"DIAM": 10.0, "HEIG": 8.0}), segments)
        self.assertTrue(mesh["success"], mesh["errors"])
        self.assertEqual(mesh["counts"], {"vertices": 2 * segments + 2, "triangles": 4 * segments})
        self.assertTrue(mesh["closed_manifold"])
        self.assertAlmostEqual(min(vertex[2] for vertex in mesh["vertices"]), -4.0)
        self.assertAlmostEqual(max(vertex[2] for vertex in mesh["vertices"]), 4.0)
        expected = segments * 0.5 * 25.0 * math.sin(2.0 * math.pi / segments) * 8.0
        self.assertAlmostEqual(_signed_volume(mesh), expected, places=10)
        self.assert_closed_and_outward(mesh)

    def test_cone_frustum_and_both_apex_directions_are_closed_and_outward(self) -> None:
        segments = 20
        frustum = derive_primitive_mesh(_native("CONE", {"DTOP": 8.0, "DBOT": 4.0, "HEIG": 9.0}), segments)
        self.assertTrue(frustum["success"], frustum["errors"])
        self.assertTrue(frustum["closed_manifold"])
        area_factor = segments * 0.5 * math.sin(2.0 * math.pi / segments)
        expected_frustum = area_factor * 9.0 / 3.0 * (2.0**2 + 4.0**2 + 2.0 * 4.0)
        self.assertAlmostEqual(_signed_volume(frustum), expected_frustum, places=10)
        bottom_radius = max(math.hypot(x, y) for x, y, z in frustum["vertices"] if z == -4.5)
        top_radius = max(math.hypot(x, y) for x, y, z in frustum["vertices"] if z == 4.5)
        self.assertAlmostEqual(bottom_radius, 2.0)
        self.assertAlmostEqual(top_radius, 4.0)
        self.assert_closed_and_outward(frustum)

        for dimensions in ({"DTOP": 0.0, "DBOT": 4.0, "HEIG": 9.0},
                           {"DTOP": 4.0, "DBOT": 0.0, "HEIG": 9.0}):
            with self.subTest(dimensions=dimensions):
                cone = derive_primitive_mesh(_native("CONE", dimensions), segments)
                self.assertTrue(cone["success"], cone["errors"])
                self.assertTrue(cone["closed_manifold"])
                self.assertEqual(cone["counts"], {"vertices": segments + 2, "triangles": 2 * segments})
                self.assertAlmostEqual(_signed_volume(cone), area_factor * 9.0 / 3.0 * 2.0**2, places=10)
                self.assert_closed_and_outward(cone)
                apex_z = 4.5 if dimensions["DTOP"] == 0.0 else -4.5
                self.assertIn([0.0, 0.0, apex_z], cone["vertices"])

    def test_output_preserves_source_provenance_and_is_strict_json(self) -> None:
        native = _native("BOX", {"XLEN": 2, "YLEN": 3, "ZLEN": 4})
        mesh = derive_primitive_mesh(native)
        self.assertTrue(mesh["success"])
        self.assertEqual(mesh["shape"], "BOX")
        self.assertEqual(mesh["dimensions"], {"XLEN": 2.0, "YLEN": 3.0, "ZLEN": 4.0})
        self.assertEqual(mesh["refno"], "=1/2")
        self.assertEqual(mesh["revision"], "r-primitive-1")
        self.assertEqual(mesh["frame"], {"requested": "OWNER", "refno": "=1/1"})
        self.assertEqual(mesh["mesh_source"], "derived_from_e3d_primitive_parameters")
        self.assertFalse(mesh["native_tessellation"])
        self.assertFalse(mesh["cuts_applied"])
        self.assertIn("native POS", mesh["coordinate_frame"])
        json.dumps(mesh, allow_nan=False)

    def test_world_space_rotates_and_translates_vertices_and_normals(self) -> None:
        native = _native("BOX", {"XLEN": 2.0, "YLEN": 2.0, "ZLEN": 2.0}, frame={
            "world": {"position": [10.0, 20.0, 30.0], "orientation": {
                "x_axis": [0.0, 1.0, 0.0], "y_axis": [-1.0, 0.0, 0.0], "z_axis": [0.0, 0.0, 1.0],
            }},
        })
        local = derive_primitive_mesh(native)
        world = derive_primitive_mesh(native, coordinate_space="WORLD")
        self.assertTrue(world["success"], world["errors"])
        self.assertEqual(world["coordinate_frame"], "WORLD coordinates; millimetres")
        self.assertEqual(world["triangles"], local["triangles"])
        self.assertEqual(world["vertices"][0], [11.0, 19.0, 29.0])
        self.assertEqual(world["triangle_normals"][0], [0.0, -1.0, 0.0])
        self.assert_closed_and_outward(world)

    def test_world_space_rejects_invalid_frames_and_overflow(self) -> None:
        base = _native("BOX", {"XLEN": 2.0, "YLEN": 2.0, "ZLEN": 2.0})
        valid_frame = {"world": {"position": [0.0, 0.0, 0.0], "orientation": {
            "x_axis": [1.0, 0.0, 0.0], "y_axis": [0.0, 1.0, 0.0], "z_axis": [0.0, 0.0, 1.0],
        }}}
        invalid_frames = [
            {},
            {"world": {"position": [math.inf, 0.0, 0.0], "orientation": valid_frame["world"]["orientation"]}},
            {"world": {"position": [0.0, 0.0], "orientation": valid_frame["world"]["orientation"]}},
            {"world": {"position": [0.0, 0.0, 0.0], "orientation": {
                "x_axis": [2.0, 0.0, 0.0], "y_axis": [0.0, 1.0, 0.0], "z_axis": [0.0, 0.0, 1.0],
            }}},
            {"world": {"position": [0.0, 0.0, 0.0], "orientation": {
                "x_axis": [1.0, 0.0, 0.0], "y_axis": [1.0, 0.0, 0.0], "z_axis": [0.0, 0.0, 1.0],
            }}},
            {"world": {"position": [0.0, 0.0, 0.0], "orientation": {
                "x_axis": [1.0, 0.0, 0.0], "y_axis": [0.0, 0.0, 1.0], "z_axis": [0.0, 1.0, 0.0],
            }}},
        ]
        for frame in invalid_frames:
            with self.subTest(frame=frame):
                mesh = derive_primitive_mesh({**base, "frame": frame}, coordinate_space="WORLD")
                self.assertFalse(mesh["success"])
                self.assertFalse(mesh["available"])
                self.assertIn("invalid_or_unavailable_world_frame", mesh["errors"])
                self.assertEqual(mesh["vertices"], [])
                json.dumps(mesh, allow_nan=False)

        overflow_base = _native("BOX", {"XLEN": 1.7e308, "YLEN": 2.0, "ZLEN": 2.0})
        overflow_frame = {"world": {"position": [1e308, 0.0, 0.0],
            "orientation": valid_frame["world"]["orientation"]}}
        overflow = derive_primitive_mesh({**overflow_base, "frame": overflow_frame}, coordinate_space="WORLD")
        self.assertFalse(overflow["success"])
        self.assertIn("world_transform_non_finite", overflow["errors"])
        self.assertEqual(overflow["vertices"], [])
        json.dumps(overflow, allow_nan=False)

    def test_unknown_coordinate_space_fails_closed(self) -> None:
        mesh = derive_primitive_mesh(_native("BOX", {"XLEN": 2.0, "YLEN": 3.0, "ZLEN": 4.0}),
                                     coordinate_space="owner")
        self.assertFalse(mesh["success"])
        self.assertIn("unsupported_coordinate_space", mesh["errors"])
        self.assertEqual(mesh["vertices"], [])

    def test_invalid_native_reads_shapes_children_and_dimensions_fail_closed(self) -> None:
        valid = _native("BOX", {"XLEN": 2.0, "YLEN": 3.0, "ZLEN": 4.0})
        invalid_cases = []
        for field, value in (
            ("success", False), ("complete", False), ("read_errors", ["XLEN failed"]),
            ("negative", True), ("negative_children", ["=1/3"]), ("children", ["=1/3"]),
            ("shape", "NBOX"), ("shape", "box"),
        ):
            case = copy.deepcopy(valid)
            case[field] = value
            invalid_cases.append((field, value, case))

        for dimensions in (
            {"XLEN": 0.0, "YLEN": 3.0, "ZLEN": 4.0},
            {"XLEN": -1.0, "YLEN": 3.0, "ZLEN": 4.0},
            {"XLEN": math.nan, "YLEN": 3.0, "ZLEN": 4.0},
            {"XLEN": math.inf, "YLEN": 3.0, "ZLEN": 4.0},
            {"XLEN": True, "YLEN": 3.0, "ZLEN": 4.0},
            {"XLEN": "2", "YLEN": 3.0, "ZLEN": 4.0},
            {"XLEN": 2.0, "YLEN": 3.0},
        ):
            case = copy.deepcopy(valid)
            case["dimensions"] = dimensions
            invalid_cases.append(("dimensions", dimensions, case))

        no_read_errors = copy.deepcopy(valid)
        del no_read_errors["read_errors"]
        invalid_cases.append(("missing read_errors", None, no_read_errors))
        no_child_evidence = copy.deepcopy(valid)
        del no_child_evidence["negative_children"]
        invalid_cases.append(("missing negative_children", None, no_child_evidence))
        conflict = copy.deepcopy(valid)
        conflict["shape"] = "CYLI"
        conflict["type"] = "BOX"
        invalid_cases.append(("conflicting shape", None, conflict))
        type_mismatch = copy.deepcopy(valid)
        type_mismatch["element"]["type"] = "CYLI"
        invalid_cases.append(("element type shape mismatch", None, type_mismatch))

        for name, value, case in invalid_cases:
            with self.subTest(name=name, value=value):
                mesh = derive_primitive_mesh(case)
                self.assertFalse(mesh["success"])
                self.assertEqual(mesh["vertices"], [])
                self.assertEqual(mesh["triangles"], [])
                self.assertEqual(mesh["triangle_normals"], [])
                self.assertEqual(mesh["counts"], {"vertices": 0, "triangles": 0})
                self.assertEqual(mesh["frame"], valid["frame"])
                json.dumps(mesh, allow_nan=False)

    def test_shape_specific_constraints_and_non_mapping_fail_closed(self) -> None:
        unsupported = derive_primitive_mesh(_native("DISH", {"DIAM": 2.0}))
        self.assertFalse(unsupported["success"])
        self.assertEqual(unsupported["vertices"], [])
        cylinder = derive_primitive_mesh(_native("CYLI", {"DIAM": 0.0, "HEIG": 2.0}))
        self.assertFalse(cylinder["success"])
        cone = derive_primitive_mesh(_native("CONE", {"DTOP": 0.0, "DBOT": 0.0, "HEIG": 2.0}))
        self.assertFalse(cone["success"])
        cone_height = derive_primitive_mesh(_native("CONE", {"DTOP": 2.0, "DBOT": 4.0, "HEIG": 0.0}))
        self.assertFalse(cone_height["success"])
        non_mapping = derive_primitive_mesh(None)  # type: ignore[arg-type]
        self.assertFalse(non_mapping["success"])
        json.dumps(non_mapping, allow_nan=False)

    def test_zero_radius_dish_is_a_closed_spherical_cap_at_base_origin(self) -> None:
        diameter, height, segments = 600.0, 150.0, 32
        mesh = derive_primitive_mesh(_native("DISH", {
            "DIAM": diameter, "HEIG": height, "RADI": 0.0,
        }), segments)
        self.assertTrue(mesh["success"], mesh["errors"])
        self.assertTrue(mesh["available"])
        self.assertTrue(mesh["closed_manifold"])
        self.assertEqual(mesh["counts"], {
            "vertices": segments * segments + 2,
            "triangles": 2 * segments * segments,
        })
        self.assertAlmostEqual(min(vertex[2] for vertex in mesh["vertices"]), 0.0)
        self.assertAlmostEqual(max(vertex[2] for vertex in mesh["vertices"]), height)
        base_vertices = [vertex for vertex in mesh["vertices"] if vertex[2] == 0.0]
        self.assertTrue(base_vertices)
        self.assertAlmostEqual(max(math.hypot(vertex[0], vertex[1]) for vertex in base_vertices), diameter / 2.0,
                                 delta=diameter * 0.01)
        self.assertEqual(mesh["parameter_convention_evidence"].startswith("Installed CSG evidence"), True)
        self.assertEqual(mesh["surface_approximation"], "spherical cap with planar triangular facets")
        self.assert_closed_and_outward(mesh)
        # Polygonal rings inscribe the analytic cap, so the derived volume is a lower approximation.
        analytic = math.pi * height * (3.0 * (diameter / 2.0) ** 2 + height ** 2) / 6.0
        self.assertGreater(_signed_volume(mesh), analytic * 0.90)
        self.assertLess(_signed_volume(mesh), analytic)

    def test_dish_major_cap_and_world_pose_keep_base_origin(self) -> None:
        orientation = {"x_axis": [0.0, 1.0, 0.0], "y_axis": [-1.0, 0.0, 0.0],
                       "z_axis": [0.0, 0.0, 1.0]}
        native = _native("DISH", {"DIAM": 100.0, "HEIG": 120.0, "RADI": 0.0}, frame={
            "world": {"position": [10.0, 20.0, 30.0], "orientation": orientation},
        })
        local = derive_primitive_mesh(native, radial_segments=24)
        world = derive_primitive_mesh(native, radial_segments=24, coordinate_space="WORLD")
        self.assertTrue(local["success"], local["errors"])
        self.assertTrue(world["success"], world["errors"])
        self.assert_closed_and_outward(local)
        self.assert_closed_and_outward(world)
        self.assertAlmostEqual(min(vertex[2] for vertex in local["vertices"]), 0.0, places=10)
        self.assertAlmostEqual(max(vertex[2] for vertex in local["vertices"]), 120.0, places=10)
        self.assertEqual(world["coordinate_frame"], "WORLD coordinates; millimetres")
        expected = [10.0 - local["vertices"][0][1], 20.0 + local["vertices"][0][0],
                    30.0 + local["vertices"][0][2]]
        for actual, wanted in zip(world["vertices"][0], expected):
            self.assertAlmostEqual(actual, wanted)

    def test_dish_positive_radius_and_invalid_radius_fail_closed(self) -> None:
        base = _native("DISH", {"DIAM": 100.0, "HEIG": 25.0, "RADI": 0.0})
        for radius, expected in ((40.0, "nonzero_dish_radius_unsupported"),
                                 (-1.0, "negative_dish_radius"),
                                 (math.nan, "invalid_dimension_radi"),
                                 (math.inf, "invalid_dimension_radi")):
            with self.subTest(radius=radius):
                case = copy.deepcopy(base)
                case["dimensions"]["RADI"] = radius
                mesh = derive_primitive_mesh(case)
                self.assertFalse(mesh["success"])
                self.assertFalse(mesh["available"])
                self.assertIn(expected, mesh["errors"])
                self.assertEqual(mesh["vertices"], [])
                self.assertEqual(mesh["triangles"], [])
                json.dumps(mesh, allow_nan=False)

    def test_dish_missing_or_nonpositive_dimensions_fail_closed(self) -> None:
        base = _native("DISH", {"DIAM": 100.0, "HEIG": 25.0, "RADI": 0.0})
        for dimensions, expected in (
            ({"DIAM": 0.0, "HEIG": 25.0, "RADI": 0.0}, "nonpositive_dish_dimension"),
            ({"DIAM": 100.0, "HEIG": 0.0, "RADI": 0.0}, "nonpositive_dish_dimension"),
            ({"DIAM": 100.0, "HEIG": 25.0}, "invalid_dimension_radi"),
        ):
            with self.subTest(dimensions=dimensions):
                case = copy.deepcopy(base)
                case["dimensions"] = dimensions
                mesh = derive_primitive_mesh(case)
                self.assertFalse(mesh["success"])
                self.assertIn(expected, mesh["errors"])
                self.assertEqual(mesh["counts"], {"vertices": 0, "triangles": 0})

    def test_radial_segment_bounds_and_integer_contract(self) -> None:
        native = _native("CYLI", {"DIAM": 2.0, "HEIG": 3.0})
        for segments in (12, 128):
            with self.subTest(segments=segments):
                mesh = derive_primitive_mesh(native, segments)
                self.assertTrue(mesh["success"], mesh["errors"])
                self.assertLessEqual(mesh["counts"]["vertices"], 258)
                self.assertLessEqual(mesh["counts"]["triangles"], 512)
                self.assert_closed_and_outward(mesh)
        for segments in (11, 129, True, False, 12.0, "48", None):
            with self.subTest(segments=segments):
                mesh = derive_primitive_mesh(native, segments)  # type: ignore[arg-type]
                self.assertFalse(mesh["success"])
                self.assertEqual(mesh["vertices"], [])
                self.assertIn("radial_segments_out_of_range", mesh["errors"])

    def test_explicit_chord_tolerance_refines_actual_large_flange_circle(self) -> None:
        # Saved pump source =2000000001/2733 is DIAM=450, HEIG=60. At 12
        # facets its 7.67 mm radial error becomes 1.53 paper mm at 1:5.
        native = _native("CYLI", {"DIAM": 450.0, "HEIG": 60.0})
        coarse = derive_primitive_mesh(native, 12)
        medium = derive_primitive_mesh(native, 24)
        refined = derive_primitive_mesh(native, 12, chord_tolerance_mm=0.2)

        def measured_error(mesh):
            ring = sorted((v for v in mesh["vertices"] if v[2] == -30.0 and math.hypot(v[0], v[1]) > 0),
                          key=lambda v: math.atan2(v[1], v[0]))
            return max(225.0 - math.hypot((left[0] + right[0]) / 2,
                                         (left[1] + right[1]) / 2)
                       for left, right in zip(ring, ring[1:] + ring[:1]))

        self.assertAlmostEqual(measured_error(coarse), 7.666689084959633)
        self.assertAlmostEqual(measured_error(medium), 1.9249061908926575)
        self.assertTrue(refined["success"], refined["errors"])
        self.assertLessEqual(measured_error(refined), 0.2)
        self.assertAlmostEqual(refined["tessellation"]["max_surface_deviation_mm"], measured_error(refined))
        self.assertEqual(refined["radial_segments"] % 4, 0)
        self.assertGreater(225 * (1 - math.cos(math.pi / (refined["radial_segments"] - 4))), 0.2)
        for axis in (0, 1):
            self.assertEqual(min(v[axis] for v in refined['vertices']), -225)
            self.assertEqual(max(v[axis] for v in refined['vertices']), 225)
        self.assertLess(abs(_signed_volume(refined) - math.pi * 225**2 * 60),
                        abs(_signed_volume(medium) - math.pi * 225**2 * 60))
        self.assertEqual(refined["counts"], refined["tessellation"]["counts"])
        self.assert_closed_and_outward(refined)

    def test_cone_tolerance_controls_the_larger_profile_and_preserves_apices(self) -> None:
        for top, bottom in [(40.0, 450.0), (450.0, 40.0), (0.0, 450.0), (450.0, 0.0)]:
            with self.subTest(top=top, bottom=bottom):
                mesh = derive_primitive_mesh(_native("CONE", {
                    "DTOP": top, "DBOT": bottom, "HEIG": 100.0,
                }), 12, chord_tolerance_mm=0.25)
                self.assertTrue(mesh["success"], mesh["errors"])
                for z, radius in [(-50.0, bottom / 2), (50.0, top / 2)]:
                    ring = sorted((v for v in mesh["vertices"] if v[2] == z and math.hypot(v[0], v[1]) > 0),
                                  key=lambda v: math.atan2(v[1], v[0]))
                    if not radius:
                        self.assertIn([0.0, 0.0, z], mesh["vertices"])
                        continue
                    for left, right in zip(ring, ring[1:] + ring[:1]):
                        error = radius - math.hypot((left[0] + right[0]) / 2, (left[1] + right[1]) / 2)
                        self.assertLessEqual(error, 0.25)
                self.assertEqual(mesh["counts"], mesh["tessellation"]["counts"])
                self.assert_closed_and_outward(mesh)

    def test_dish_tolerance_bounds_facet_interiors_for_minor_and_major_caps(self) -> None:
        for diameter, height in [(600.0, 150.0), (100.0, 120.0)]:
            with self.subTest(diameter=diameter, height=height):
                mesh = derive_primitive_mesh(_native("DISH", {
                    "DIAM": diameter, "HEIG": height, "RADI": 0.0,
                }), 12, chord_tolerance_mm=0.25)
                self.assertTrue(mesh["success"], mesh["errors"])
                radius = ((diameter / 2) ** 2 + height**2) / (2 * height)
                center = [0.0, 0.0, height - radius]
                worst_plane_error = 0.0
                for triangle in mesh["triangles"]:
                    a, b, c = [mesh["vertices"][i] for i in triangle]
                    if all(abs(v[2]) < 1e-9 for v in (a, b, c)):
                        continue  # The planar closing disk is not the curved surface.
                    ab, ac = [b[i] - a[i] for i in range(3)], [c[i] - a[i] for i in range(3)]
                    normal = [ab[1]*ac[2] - ab[2]*ac[1], ab[2]*ac[0] - ab[0]*ac[2], ab[0]*ac[1] - ab[1]*ac[0]]
                    plane_distance = abs(sum(normal[i] * (a[i] - center[i]) for i in range(3))) / math.hypot(*normal)
                    worst_plane_error = max(worst_plane_error, radius - plane_distance)
                    # Include triangle interiors; an edge-only sagitta test is insufficient.
                    centroid = [sum(v[i] for v in (a, b, c)) / 3 for i in range(3)]
                    self.assertLessEqual(radius - math.dist(centroid, center), 0.25)
                self.assertLessEqual(worst_plane_error, 0.25)
                self.assertAlmostEqual(mesh["tessellation"]["max_surface_deviation_mm"], worst_plane_error, places=10)
                self.assertLess(mesh["tessellation"]["latitude_segments"], mesh["radial_segments"])
                self.assertLessEqual(mesh["counts"]["vertices"], 10_000)
                self.assertLessEqual(mesh["counts"]["triangles"], 20_000)
                self.assertEqual(mesh["counts"], mesh["tessellation"]["counts"])
                self.assert_closed_and_outward(mesh)

    def test_omitted_tolerance_preserves_fixed_mesh_fields_and_geometry(self) -> None:
        for shape, dimensions in [("BOX", {"XLEN": 2.0, "YLEN": 3.0, "ZLEN": 4.0}),
                                  ("CYLI", {"DIAM": 10.0, "HEIG": 8.0}),
                                  ("CONE", {"DBOT": 10.0, "DTOP": 2.0, "HEIG": 8.0}),
                                  ("DISH", {"DIAM": 100.0, "HEIG": 25.0, "RADI": 0.0})]:
            with self.subTest(shape=shape):
                native = _native(shape, dimensions)
                fixed = derive_primitive_mesh(native, 24)
                self.assertEqual(fixed, derive_primitive_mesh(native, 24, chord_tolerance_mm=None))
                self.assertNotIn("tessellation", fixed)
                self.assertEqual(fixed["radial_segments"], 24)
                plan = plan_primitive_tessellation(shape, dimensions, 24)
                self.assertEqual(fixed["counts"], plan["counts"])

    def test_adaptive_world_placement_preserves_surface_proof(self) -> None:
        native = _native("CYLI", {"DIAM": 450.0, "HEIG": 60.0}, frame={"world": {
            "position": [100.0, 200.0, 300.0], "orientation": {
                "x_axis": [0.0, 0.0, 1.0], "y_axis": [1.0, 0.0, 0.0], "z_axis": [0.0, 1.0, 0.0],
            }}})
        local = derive_primitive_mesh(native, 12, chord_tolerance_mm=0.2)
        world = derive_primitive_mesh(native, 12, coordinate_space="WORLD", chord_tolerance_mm=0.2)
        self.assertTrue(world["success"], world["errors"])
        self.assertEqual(world["triangles"], local["triangles"])
        self.assertEqual(world["tessellation"], local["tessellation"])
        for vertex, transformed in zip(local["vertices"], world["vertices"]):
            self.assertEqual(transformed, [100 + vertex[1], 200 + vertex[2], 300 + vertex[0]])
        self.assert_closed_and_outward(world)

    def test_tolerance_never_coarsens_requested_radial_sampling_or_refines_boxes(self) -> None:
        mesh = derive_primitive_mesh(_native("CYLI", {"DIAM": 2.0, "HEIG": 3.0}), 96, chord_tolerance_mm=1.0)
        self.assertTrue(mesh["success"], mesh["errors"])
        self.assertEqual(mesh["radial_segments"], 96)
        native = _native("BOX", {"XLEN": 2.0, "YLEN": 3.0, "ZLEN": 4.0})
        fixed = derive_primitive_mesh(native, 12)
        adaptive = derive_primitive_mesh(native, 12, chord_tolerance_mm=1e-9)
        self.assertEqual(adaptive["vertices"], fixed["vertices"])
        self.assertEqual(adaptive["triangles"], fixed["triangles"])
        self.assertEqual(adaptive["tessellation"]["max_surface_deviation_mm"], 0.0)

    def test_impossible_or_invalid_tolerance_fails_without_partial_geometry(self) -> None:
        for shape, dimensions in [("CYLI", {"DIAM": 450.0, "HEIG": 60.0}),
                                  ("DISH", {"DIAM": 600.0, "HEIG": 150.0, "RADI": 0.0})]:
            for tolerance, code in [(1e-9, "chord_tolerance_exceeds_tessellation_budget"),
                                    (0, "invalid_chord_tolerance_mm"), (-1, "invalid_chord_tolerance_mm"),
                                    (True, "invalid_chord_tolerance_mm"), ("0.2", "invalid_chord_tolerance_mm"),
                                    (math.nan, "invalid_chord_tolerance_mm"), (math.inf, "invalid_chord_tolerance_mm")]:
                with self.subTest(shape=shape, tolerance=tolerance):
                    mesh = derive_primitive_mesh(_native(shape, dimensions), 12, chord_tolerance_mm=tolerance)
                    self.assertFalse(mesh["success"])
                    self.assertIn(code, mesh["errors"])
                    self.assertEqual(mesh["vertices"], [])
                    self.assertEqual(mesh["triangles"], [])
                    json.dumps(mesh, allow_nan=False)
        mesh = derive_primitive_mesh(_native("DISH", {"DIAM": 600.0, "HEIG": 150.0, "RADI": 70.0}),
                                     12, chord_tolerance_mm=0.2)
        self.assertFalse(mesh["success"])
        self.assertIn("nonzero_dish_radius_unsupported", mesh["errors"])
        self.assertEqual(mesh["dimensions"]["RADI"], 70.0)

    def test_shared_planner_respects_callers_csg_segment_ceiling(self) -> None:
        dimensions = {"DIAM": 450.0, "HEIG": 60.0}
        plan = plan_primitive_tessellation("CYLI", dimensions, 12, 0.2, max_radial_segments=99)
        self.assertLessEqual(plan["radial_segments"], 99)
        with self.assertRaisesRegex(ValueError, "chord_tolerance_exceeds_tessellation_budget"):
            plan_primitive_tessellation("CYLI", dimensions, 12, 0.01, max_radial_segments=99)


if __name__ == "__main__":
    unittest.main()

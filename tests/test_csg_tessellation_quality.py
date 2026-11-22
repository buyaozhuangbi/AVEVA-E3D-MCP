"""Independent accuracy/topology checks for the retained nine-bore pump flange."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp import primitive_csg_mesh as csg_mesh
from test_primitive_csg_mesh import (
    CSG_ENGINE_AVAILABLE, csg_native, cut, material_contains, polygon_area,
    signed_volume,
)


MODULE = "aveva_e3d_mcp.primitive_csg_mesh"
TOLERANCE_MM = 0.2
FLANGE_AXES = ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
BOLT_CENTERS_LOCAL = (
    (0.0, 180.0, 0.0), (0.0, -180.0, 0.0),
    (-180.0, 0.0, 0.0), (180.0, 0.0, 0.0),
    (-127.279221, 127.279221, 0.0), (127.279221, 127.279221, 0.0),
    (-127.279221, -127.279221, 0.0), (127.279221, -127.279221, 0.0),
)


def flange_geometry():
    """Self-contained dimensions/poses from the retained 2026-11-20 receipt.

    The real source is CYLI =2000000001/2733 with the central NCYL /3755
    and eight NCYL /3773 through /3787. Synthetic refs keep this test offline.
    """
    cutters = [cut(dimensions={"DIAM": 220.0, "HEIG": 80.0})]
    cutters.extend(cut(dimensions={"DIAM": 32.0, "HEIG": 80.0}, position=center)
                   for center in BOLT_CENTERS_LOCAL)
    return csg_native("CYLI", {"DIAM": 450.0, "HEIG": 60.0}, cutters=cutters,
                      position=(0.0, 0.0, 720.0), axes=FLANGE_AXES)


def boundaries(native):
    return [(node["frame"]["world"]["position"], node["dimensions"]["DIAM"] / 2.0)
            for node in [native, *native["negative_children"]]]


def nearest_segment_radius(first, second, center):
    delta = [second[i] - first[i] for i in range(2)]
    relative = [first[i] - center[i] for i in range(2)]
    squared = math.fsum(value * value for value in delta)
    fraction = max(0.0, min(1.0, -math.fsum(a * b for a, b in zip(relative, delta)) / squared))
    return math.hypot(*(relative[i] + fraction * delta[i] for i in range(2)))


def horizontal_section(mesh, height):
    """Intersect the returned triangles, independently of the drawing code."""
    segments = []
    for face in mesh["triangles"]:
        points = [mesh["vertices"][index] for index in face]
        intersections = []
        for first, second in zip(points, points[1:] + points[:1]):
            if (first[2] < height < second[2]) or (second[2] < height < first[2]):
                fraction = (height - first[2]) / (second[2] - first[2])
                intersections.append(tuple(first[i] + fraction * (second[i] - first[i]) for i in range(2)))
        if intersections:
            if len(intersections) != 2:
                raise AssertionError("This fixture must have two intersections per crossing triangle.")
            segments.append(intersections)
    return segments


class CsgTessellationQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not CSG_ENGINE_AVAILABLE:
            raise unittest.SkipTest("Install the geometry extra for Mesh64 integration tests.")
        cls.native = flange_geometry()
        cls.original_native = deepcopy(cls.native)
        with patch(f"{MODULE}._run_csg_worker", wraps=csg_mesh._run_csg_worker) as worker:
            cls.adaptive = csg_mesh.derive_primitive_csg_mesh(
                cls.native, radial_segments=12, chord_tolerance_mm=TOLERANCE_MM)
        worker.assert_called_once()
        cls.adaptive_operands = deepcopy(worker.call_args.args[0])
        cls.coarse = csg_mesh.derive_primitive_csg_mesh(cls.native, radial_segments=12)
        with patch(f"{MODULE}.plan_primitive_tessellation") as planner:
            cls.default = csg_mesh.derive_primitive_csg_mesh(cls.native)
            cls.explicit_none = csg_mesh.derive_primitive_csg_mesh(cls.native, chord_tolerance_mm=None)
        planner.assert_not_called()
        for mesh in (cls.adaptive, cls.coarse, cls.default, cls.explicit_none):
            if mesh.get("success") is not True:
                raise AssertionError(mesh)

    def test_each_actual_worker_operand_has_minimal_quadrant_complete_chord_error(self):
        expected_segments = [76, 56, *([20] * 8)]
        self.assertEqual(len(self.adaptive_operands), 10)
        anchor = self.native["frame"]["world"]["position"]
        measured_errors = []
        for index, (operand, evidence, boundary, segments) in enumerate(zip(
                self.adaptive_operands, self.adaptive["source_operands"],
                boundaries(self.native), expected_segments)):
            center_world, radius = boundary
            center = [center_world[i] - anchor[i] for i in range(3)]
            top = max(point[2] for point in operand["vertices"])
            ring = {offset for offset, point in enumerate(operand["vertices"])
                    if abs(point[2] - top) < 1e-8 and math.hypot(
                        point[0] - center[0], point[1] - center[1]) > radius / 2}
            edges = {tuple(sorted((a, b))) for face in operand["triangles"]
                     for a, b in zip(face, face[1:] + face[:1]) if a in ring and b in ring}
            with self.subTest(operand=index):
                self.assertEqual(len(ring), segments)
                self.assertEqual(len(edges), segments)
                error = max(radius - nearest_segment_radius(
                    operand["vertices"][a], operand["vertices"][b], center) for a, b in edges)
                measured_errors.append(error)
                self.assertLessEqual(error, TOLERANCE_MM + 1e-10)
                self.assertEqual(segments % 4, 0, "Adaptive circles preserve all four local-axis extrema.")
                self.assertGreater(radius * (1.0 - math.cos(math.pi / (segments - 4))), TOLERANCE_MM)
                self.assertEqual(evidence["tessellation"]["radial_segments"], segments)
                self.assertAlmostEqual(evidence["tessellation"]["max_surface_deviation_mm"], error, places=10)
                self.assertEqual(evidence["tessellation"]["counts"],
                                 {"vertices": len(operand["vertices"]), "triangles": len(operand["triangles"])})
        self.assertAlmostEqual(self.adaptive["tessellation"]["max_operand_surface_deviation_mm"],
                               max(measured_errors), places=10)
        self.assertEqual(self.native, self.original_native)

    def test_final_boolean_section_preserves_ten_closed_contours_with_bounded_circle_error(self):
        self.assert_flange_section(self.adaptive, TOLERANCE_MM)

    def test_tighter_tolerance_refines_past_the_fixed_input_segment_limit(self):
        tolerance = 0.1
        mesh = csg_mesh.derive_primitive_csg_mesh(self.native, radial_segments=12, chord_tolerance_mm=tolerance)
        self.assertTrue(mesh["success"], mesh.get("diagnostics"))
        self.assertEqual(mesh["source_operands"][0]["tessellation"]["radial_segments"], 108)
        self.assertLessEqual(mesh["tessellation"]["max_operand_surface_deviation_mm"], tolerance)
        self.assertTrue(mesh["closed_manifold"])
        self.assert_flange_section(mesh, tolerance)

    def assert_flange_section(self, mesh, tolerance):
        for axis in (0, 1):
            self.assertAlmostEqual(min(point[axis] for point in mesh["vertices"]), -225.0)
            self.assertAlmostEqual(max(point[axis] for point in mesh["vertices"]), 225.0)
        segments = horizontal_section(mesh, 720.0)
        adjacency = defaultdict(set)
        matched_boundaries = Counter()
        for first, second in segments:
            # Quantization only connects independently computed triangle-plane
            # intersections; the accuracy oracle below uses the original floats.
            a, b = (tuple(round(value, 8) for value in point) for point in (first, second))
            adjacency[a].add(b)
            adjacency[b].add(a)
            candidates = [(index, center, radius) for index, (center, radius) in enumerate(boundaries(self.native))
                          if all(abs(math.hypot(point[0] - center[0], point[1] - center[1]) - radius)
                                 <= tolerance + 1e-8 for point in (first, second))]
            self.assertEqual(len(candidates), 1)
            index, center, radius = candidates[0]
            matched_boundaries[index] += 1
            self.assertLessEqual(radius - nearest_segment_radius(first, second, center), tolerance + 1e-8)
        self.assertEqual(set(matched_boundaries), set(range(10)))
        self.assertTrue(all(len(neighbors) == 2 for neighbors in adjacency.values()))
        remaining, contour_count = set(adjacency), 0
        while remaining:
            contour_count += 1
            pending = [next(iter(remaining))]
            while pending:
                current = pending.pop()
                if current in remaining:
                    remaining.remove(current)
                    pending.extend(adjacency[current])
        self.assertEqual(contour_count, 10, "One outer contour plus nine distinct bore contours are required.")

    def test_world_frame_expansion_refines_only_the_operand_that_would_exceed_tolerance(self):
        radius, scale = 30.0, 1.000008
        local_error_at_24 = radius * (1.0 - math.cos(math.pi / 24))
        tolerance = local_error_at_24 * (1.0 + (scale - 1.0) / 2.0)
        self.assertLess(local_error_at_24, tolerance)
        self.assertGreater(local_error_at_24 * scale, tolerance)
        native = csg_native("CYLI", {"DIAM": 200.0, "HEIG": 40.0}, cutters=[
            cut(dimensions={"DIAM": 2 * radius, "HEIG": 60.0}, position=(-40.0, 0.0, 0.0)),
            cut(dimensions={"DIAM": 2 * radius, "HEIG": 60.0}, position=(40.0, 0.0, 0.0),
                axes=((scale, 0.0, 0.0), (0.0, scale, 0.0), (0.0, 0.0, 1.0))),
        ])
        with patch(f"{MODULE}._run_csg_worker", wraps=csg_mesh._run_csg_worker) as worker:
            mesh = csg_mesh.derive_primitive_csg_mesh(native, radial_segments=12, chord_tolerance_mm=tolerance)
        self.assertTrue(mesh["success"], mesh.get("diagnostics"))
        operands = worker.call_args.args[0]
        for index, position, world_radius, segments in ((1, -40.0, radius, 24), (2, 40.0, radius * scale, 28)):
            operand = operands[index]
            plan = mesh["source_operands"][index]["tessellation"]
            center = (position, 0.0)
            with self.subTest(cutter=index):
                self.assertEqual(plan["radial_segments"], segments)
                ring = {i for i, point in enumerate(operand["vertices"])
                        if point[2] == 30.0 and math.dist(point[:2], center) > world_radius / 2}
                edges = {tuple(sorted((a, b))) for face in operand["triangles"]
                         for a, b in zip(face, face[1:] + face[:1]) if a in ring and b in ring}
                self.assertEqual(len(edges), segments)
                measured = max(world_radius - nearest_segment_radius(
                    operand["vertices"][a], operand["vertices"][b], center) for a, b in edges)
                self.assertLessEqual(measured, tolerance)
                self.assertAlmostEqual(plan["max_surface_deviation_mm"], measured, places=10)
                self.assertEqual(plan["chord_tolerance_mm"], tolerance)
        self.assertLessEqual(mesh["tessellation"]["max_operand_surface_deviation_mm"], tolerance)
        scaled_plan = mesh["source_operands"][2]["tessellation"]
        self.assertGreater(scaled_plan["max_surface_deviation_mm"], scaled_plan["local_surface_deviation_bound_mm"])
        self.assertGreaterEqual(scaled_plan["world_linear_scale_upper_bound"], scale)

    def test_all_nine_bores_remain_void_with_material_immediately_outside(self):
        for index, (center, radius) in enumerate(boundaries(self.native)[1:]):
            for dz in (-29.5, 0.0, 29.5):
                self.assertFalse(material_contains(self.adaptive, [center[0], center[1], center[2] + dz]))
                for angle in (0.137, 1.708, 3.279, 4.85):
                    for offset, expected_material in ((-0.5, False), (0.5, True)):
                        point = [center[0] + (radius + offset) * math.cos(angle),
                                 center[1] + (radius + offset) * math.sin(angle), center[2] + dz]
                        with self.subTest(bore=index, dz=dz, angle=angle, offset=offset):
                            self.assertEqual(material_contains(self.adaptive, point), expected_material)

    def test_material_boundary_is_one_closed_oriented_manifold_with_nine_handles(self):
        mesh = self.adaptive
        directed_edges, vertex_links = defaultdict(list), defaultdict(list)
        for face in mesh["triangles"]:
            for a, b in zip(face, face[1:] + face[:1]):
                directed_edges[tuple(sorted((a, b)))].append((a, b))
            for offset, vertex in enumerate(face):
                vertex_links[vertex].append((face[(offset + 1) % 3], face[(offset + 2) % 3]))
        for uses in directed_edges.values():
            self.assertEqual(len(uses), 2)
            self.assertEqual(uses[0], tuple(reversed(uses[1])))
        self.assertEqual(set(vertex_links), set(range(len(mesh["vertices"]))))
        for edges in vertex_links.values():
            neighbors = defaultdict(set)
            for a, b in edges:
                neighbors[a].add(b)
                neighbors[b].add(a)
            self.assertTrue(all(len(values) == 2 for values in neighbors.values()))
            visited, pending = set(), [next(iter(neighbors))]
            while pending:
                current = pending.pop()
                if current not in visited:
                    visited.add(current)
                    pending.extend(neighbors[current])
            self.assertEqual(visited, set(neighbors), "Every vertex link must be a single cycle.")
        neighbors = defaultdict(set)
        for a, b in directed_edges:
            neighbors[a].add(b)
            neighbors[b].add(a)
        visited, pending = set(), [0]
        while pending:
            current = pending.pop()
            if current not in visited:
                visited.add(current)
                pending.extend(neighbors[current])
        self.assertEqual(len(visited), len(mesh["vertices"]))
        self.assertEqual(len(mesh["vertices"]) - len(directed_edges) + len(mesh["triangles"]), 2 - 2 * 9)
        self.assertGreater(signed_volume(mesh["vertices"], mesh["triangles"]), 0.0)
        self.assertTrue(mesh["material_boundary_validated"])
        self.assertFalse(mesh["native_tessellation"])
        self.assertFalse(mesh["native_cut_application_verified"])

    def test_net_volume_matches_independent_polygon_oracle_and_converges_to_analytic(self):
        ideal = math.pi * (225.0 ** 2 - 110.0 ** 2 - 8 * 16.0 ** 2) * 60.0
        expected_polygon_net = 60.0 * (polygon_area(225.0, 76) - polygon_area(110.0, 56)
                                       - 8 * polygon_area(16.0, 20))
        actual = self.adaptive["net_volume_mm3"]
        self.assertAlmostEqual(actual, expected_polygon_net, delta=1e-5)
        self.assertAlmostEqual(signed_volume(self.adaptive["vertices"], self.adaptive["triangles"]),
                               expected_polygon_net, delta=1e-5)
        self.assertLess(abs(actual - ideal) / ideal, 0.001)
        self.assertLess(abs(actual - ideal), abs(self.coarse["net_volume_mm3"] - ideal) / 100)
        self.assertAlmostEqual(self.adaptive["gross_polyhedral_volume_mm3"],
                               polygon_area(225.0, 76) * 60.0, delta=1e-5)

    def test_omitted_tolerance_keeps_legacy_default_fields_and_indexed_geometry(self):
        self.assertEqual(self.default, self.explicit_none)
        self.assertEqual(self.default["radial_segments"], 48)
        self.assertNotIn("tessellation", self.default)
        for operand in self.default["source_operands"]:
            self.assertNotIn("tessellation", operand)
            self.assertEqual(operand["validation"]["counts"]["vertices"], 98)
            self.assertEqual(operand["validation"]["counts"]["triangles"], 192)
        legacy_polygon_net = 60.0 * (polygon_area(225.0, 48) - polygon_area(110.0, 48)
                                     - 8 * polygon_area(16.0, 48))
        self.assertAlmostEqual(self.default["net_volume_mm3"], legacy_polygon_net, delta=1e-5)
        self.assertNotEqual(self.default["geometry_sha256"], self.adaptive["geometry_sha256"])


class CsgTessellationAdmissionTests(unittest.TestCase):
    def assert_refused_before_worker(self, native, tolerance):
        with patch(f"{MODULE}._run_csg_worker") as worker:
            result = csg_mesh.derive_primitive_csg_mesh(native, radial_segments=12, chord_tolerance_mm=tolerance)
        worker.assert_not_called()
        self.assertFalse(result["success"])
        self.assertFalse(result["complete"])
        self.assertFalse(result["cuts_applied"])
        self.assertFalse(result["material_boundary_validated"])
        self.assertEqual(result["vertices"], [])
        self.assertEqual(result["triangles"], [])
        return result

    def test_strict_chord_and_cumulative_mesh_budgets_fail_before_the_worker(self):
        native = flange_geometry()
        # R225 needs more than 256 facets for 0.01 mm; never silently clamp.
        result = self.assert_refused_before_worker(native, 0.01)
        self.assertEqual(result["error_code"], "csg_chord_tolerance_exceeds_tessellation_budget")
        self.assertEqual(result["diagnostics"], {"source_refno": native["element"]["refno"]})
        # Every operand fits separately, but their combined input must also fit.
        for name, limit in (("MAX_VERTICES", 603), ("MAX_TRIANGLES", 1167)):
            with self.subTest(limit=name), patch(f"{MODULE}.{name}", limit):
                result = self.assert_refused_before_worker(native, TOLERANCE_MM)
            self.assertEqual(result["error_code"], "csg_input_mesh_limit_exceeded")
            self.assertEqual(result["diagnostics"], {"vertices": 604, "triangles": 1168})

    def test_large_round_operand_refines_above_128_and_still_rejects_unmet_tolerance(self):
        native = csg_native("CYLI", {"DIAM": 2000.0, "HEIG": 1000.0})
        root = csg_mesh._validate_node(native["target_geometry"], negative=False)

        plan = csg_mesh._plan_world_operand_tessellation(root, 48, 0.25)

        self.assertEqual(plan["radial_segments"], 144)
        self.assertLessEqual(plan["max_surface_deviation_mm"], 0.25)
        with self.assertRaisesRegex(ValueError, "chord_tolerance_exceeds_tessellation_budget"):
            csg_mesh._plan_world_operand_tessellation(root, 48, 0.01)

    def test_invalid_chord_tolerances_never_reach_the_worker(self):
        for tolerance in (0.0, -0.2, True, "0.2", math.nan, math.inf):
            with self.subTest(tolerance=tolerance):
                result = self.assert_refused_before_worker(flange_geometry(), tolerance)
                self.assertEqual(result["error_code"], "csg_invalid_chord_tolerance_mm")


if __name__ == "__main__":
    unittest.main()

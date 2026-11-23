"""Independent analytic oracles for the native PAVE definition-curve path.

The fixtures below contain explicit tangent/centre coordinates for rectangles,
an L corner and an equilateral triangle. They do not call a fillet solver or
derive expected answers from the production sampling algorithm.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aveva_e3d_mcp.panel_curve_boundary import derive_panel_curve_boundary


PANE = "=1/2"
WORLD = "=1/0"
WORLD_AXES = ((0.8, 0.6, 0.0), (-0.36, 0.48, 0.8), (0.48, -0.64, 0.6))
WORLD_TRANSLATION = (12000.0, -24000.0, 3500.0)


def rigid_point(point, axes=WORLD_AXES, translation=WORLD_TRANSLATION):
    return [math.fsum((translation[i], *(point[j] * axes[j][i] for j in range(3)))) for i in range(3)]


def rigid_direction(vector, axes=WORLD_AXES):
    return [math.fsum(vector[j] * axes[j][i] for j in range(3)) for i in range(3)]


def install_world(native, axes=WORLD_AXES, translation=WORLD_TRANSLATION):
    """Install an independently specified affine observation, rigid by default."""
    total = sum(len(loop["vertices"]) for loop in native["loops"])
    native.update(world_positions_requested=True, world_positions_complete=True,
                  world_position_count=total, world_position_vertex_count=total,
                  world_position_frame=WORLD, world_position_unit="mm", world_read_errors={})
    for loop in native["loops"]:
        for vertex in loop["vertices"]:
            vertex["position_world_mm"] = rigid_point(vertex["position"], axes, translation)
            vertex["world_position_complete"] = True
            if not vertex["fillet_radius_mm"]:
                continue
            frame = deepcopy(vertex["boundary_points"]["panel_local"])
            frame["frame_refno"] = WORLD
            for point in frame["points"]:
                point["position_mm"] = rigid_point(point["position_mm"], axes, translation)
                if "direction" in point:
                    point["direction"] = rigid_direction(point["direction"], axes)
            vertex["boundary_points"]["WORLD"] = frame
    return native


def native_from_corners(poles, radii, definitions, *, raw_z=-6.0):
    """Package explicit geometry into the actual panel-geometry field shape."""
    vertices = []
    for index, (pole, radius) in enumerate(zip(poles, radii)):
        position = [float(pole[0]), float(pole[1]), float(pole[2]) if len(pole) == 3 else 0.0]
        vertex = {"refno": f"=1/{100 + index}", "vertex_index": index, "position": position,
                  "fillet_radius_mm": radius, "CTYA": "unset", "GTYP": "unset", "BANG": "0degree",
                  "curve_import_descriptor": {"checked": True, "tagged": False,
                                               "attribute": "DESP", "raw": "unset"}}
        if radius:
            start, end, center = definitions[index]
            points = [{"number": 0, "exists": None, "exists_checked": False,
                       "position_mm": position[:], "position_complete": True,
                       "direction": [0.0, 0.0, 1.0], "direction_complete": True,
                       "complete": True, "errors": {}}]
            for number, xy in ((4, end), (6, start), (8, center)):
                points.append({"number": number, "exists": True, "exists_checked": True,
                               "position_mm": [float(xy[0]), float(xy[1]), raw_z],
                               "position_complete": True, "complete": True, "errors": {}})
            vertex["boundary_points"] = {"required": True, "complete": True,
                "panel_local": {"frame_refno": PANE, "unit": "mm", "points": points,
                                "complete": True, "errors": {}}}
        else:
            vertex["boundary_points"] = {"required": False, "reason": "straight_vertex"}
        vertices.append(vertex)
    loop = {"element": {"refno": "=1/3", "type": "PLOOP"}, "loop_index": 0,
            "role": "not inferred", "complete": True, "read_errors": {}, "blind": False,
            "thickness_mm": 12.0, "native_justification": "CENT", "vertex_count": len(vertices),
            "vertices": vertices, "polygon_valid": None if any(radii) else True,
            "unsupported_features": [{"code": "fillet_radius", "vertex_index": i, "radius_mm": r}
                                     for i, r in enumerate(radii) if r]}
    native = {"success": True, "complete": True, "partial": False, "unit": "mm",
              "element": {"refno": PANE, "type": "PANEL", "name": "/ORACLE-PANEL"},
              "coordinate_frame": PANE, "revision": "oracle-revision", "read_errors": {},
              "loop_count": 1, "vertex_count": len(vertices), "topology_valid": None if any(radii) else True,
              "boundary_points_requested": True, "boundary_points_complete": True,
              "boundary_points_read_complete": True, "boundary_points_source_geometry_complete": True,
              "boundary_points_vertex_count": len(vertices),
              "boundary_points_curved_vertex_count": sum(bool(r) for r in radii),
              "boundary_points_errors": {}, "loops": [loop]}
    return install_world(native)


def rectangle_native(width=100.0, height=60.0, radius=10.0, *, origin=(0.0, 0.0), radii=None):
    x, y = origin
    values = list(radii) if radii is not None else [radius] * 4
    poles = [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]
    a, b, c, d = values
    definitions = {
        0: ((x, y + a), (x + a, y), (x + a, y + a)),
        1: ((x + width - b, y), (x + width, y + b), (x + width - b, y + b)),
        2: ((x + width, y + height - c), (x + width - c, y + height), (x + width - c, y + height - c)),
        3: ((x + d, y + height), (x, y + height - d), (x + d, y + height - d)),
    }
    return native_from_corners(poles, values, definitions)


def l_corner_native(*, all_rounded=False):
    poles = [(0, 0), (100, 0), (100, 60), (60, 60), (60, 100), (0, 100)]
    r = 5.0 if all_rounded else 10.0
    radii = [r] * 6 if all_rounded else [0, 0, 0, r, 0, 0]
    definitions = {
        0: ((0, r), (r, 0), (r, r)),
        1: ((100-r, 0), (100, r), (100-r, r)),
        2: ((100, 60-r), (100-r, 60), (100-r, 60-r)),
        3: ((60+r, 60), (60, 60+r), (60+r, 60+r)),
        4: ((60, 100-r), (60-r, 100), (60-r, 100-r)),
        5: ((r, 100), (0, 100-r), (r, 100-r)),
    }
    return native_from_corners(poles, radii, definitions)


def triangle_native(radius=10.0):
    q = math.sqrt(3.0) * radius
    top = 50.0 * math.sqrt(3.0)
    definitions = {
        0: ((q/2, 1.5*radius), (q, 0), (q, radius)),
        1: ((100-q, 0), (100-q/2, 1.5*radius), (100-q, radius)),
        2: ((50+q/2, top-1.5*radius), (50-q/2, top-1.5*radius), (50, top-2*radius)),
    }
    return native_from_corners([(0, 0), (100, 0), (50, top)], [radius]*3, definitions)


def combine_native(*parts):
    native = deepcopy(parts[0])
    native["loops"] = []
    for index, part in enumerate(parts):
        loop = deepcopy(part["loops"][0])
        loop["loop_index"] = index
        loop["element"]["refno"] = f"=1/{300 + index}"
        for vi, vertex in enumerate(loop["vertices"]):
            vertex["refno"] = f"=1/{1000 + index * 100 + vi}"
        native["loops"].append(loop)
    total = sum(len(loop["vertices"]) for loop in native["loops"])
    native.update(loop_count=len(parts), vertex_count=total, boundary_points_vertex_count=total,
                  boundary_points_curved_vertex_count=sum(bool(v["fillet_radius_mm"])
                      for loop in native["loops"] for v in loop["vertices"]), topology_valid=None)
    return install_world(native)


def point_record(native, vertex=0, number=0, frame="panel_local"):
    return next(p for p in native["loops"][0]["vertices"][vertex]["boundary_points"][frame]["points"]
                if p["number"] == number)


def install_unset_existence(native):
    errors = {}
    for loop in native["loops"]:
        for vertex in loop["vertices"]:
            if not vertex["fillet_radius_mm"]:
                continue
            vertex["boundary_points"]["complete"] = False
            for name in ("panel_local", "WORLD"):
                frame = vertex["boundary_points"][name]
                frame["complete"] = False
                for point in frame["points"]:
                    if point["number"] == 0:
                        continue
                    message = "Attribute PPEXST is unset for element PAVERT 1 of PLOOP 1."
                    point.update(exists=None, exists_checked=True, complete=False, errors={"exists": message})
                    frame["errors"][f"P{point['number']}.exists"] = message
                    errors[f"{vertex['refno']}:{name}:P{point['number']}.exists"] = message
    native.update(boundary_points_complete=False, boundary_points_read_complete=False,
                  boundary_points_errors=errors)
    return native


def segment_measurements(loop):
    """Green's theorem and exact arc lengths, independently of sampling."""
    area_terms, length_terms = [], []
    for segment in loop["segments"]:
        a, b = segment["start"], segment["end"]
        if segment["kind"] == "line":
            area_terms.append((a[0]*b[1] - b[0]*a[1])/2)
            length_terms.append(math.dist(a, b))
        else:
            c, r, sweep = segment["center"], segment["radius_mm"], segment["sweep_radians"]
            area_terms.append((c[0]*(b[1]-a[1]) - c[1]*(b[0]-a[0]) + r*r*sweep)/2)
            length_terms.append(abs(sweep)*r)
    return math.fsum(area_terms), math.fsum(length_terms)


def polygon_area(points):
    origin = points[0]
    xy = [(p[0]-origin[0], p[1]-origin[1]) for p in points]
    return math.fsum(a[0]*b[1] - b[0]*a[1] for a, b in zip(xy, xy[1:]+xy[:1]))/2


class PanelCurveBoundaryTests(unittest.TestCase):
    def accepted(self, native, **options):
        result = derive_panel_curve_boundary(native, **options)
        self.assertTrue(result["success"], result)
        self.assertTrue(result["available"])
        self.assertTrue(result["complete"])
        for loop in result["loops"]:
            self.assertTrue(loop["closed"])
            points = loop["sampled_points"]
            self.assertGreaterEqual(len(points), 3)
            self.assertNotEqual(points[0], points[-1], "The closing edge is implicit, not a duplicate endpoint.")
            self.assertEqual(loop["sampled_edge_count"], len(points))
            segments = loop["segments"]
            for a, b in zip(segments, segments[1:]+segments[:1]):
                self.assertLessEqual(math.dist(a["end"], b["start"]), 1e-7)
        return result

    def rejected(self, native, **options):
        before = deepcopy(native)
        result = derive_panel_curve_boundary(native, **options)
        self.assertFalse(result["success"], result)
        self.assertFalse(result["available"])
        self.assertFalse(result["complete"])
        self.assertTrue(result["errors"])
        self.assertEqual(result["loops"], [], "Rejection must not fall back to the control polygon.")
        self.assertEqual(native, before)
        return result

    def assert_round_chord_error(self, result, centers, radius, tolerance):
        counts = [0] * len(centers)
        points = result["loops"][0]["sampled_points"]
        for a, b in zip(points, points[1:]+points[:1]):
            self.assertGreater(math.dist(a, b), 0)
            self.assertLess(abs(a[2]), 1e-8)
            for index, center in enumerate(centers):
                if all(abs(math.hypot(p[0]-center[0], p[1]-center[1])-radius) < 1e-6 for p in (a, b)):
                    midpoint = [(a[i]+b[i])/2 for i in range(2)]
                    sagitta = radius - math.hypot(midpoint[0]-center[0], midpoint[1]-center[1])
                    self.assertGreaterEqual(sagitta, -1e-7)
                    self.assertLessEqual(sagitta, tolerance + 1e-7)
                    counts[index] += 1
        self.assertTrue(all(n > 1 for n in counts), counts)

    def test_rounded_rectangle_matches_exact_area_perimeter_and_preserves_source(self):
        native = rectangle_native()
        before = deepcopy(native)
        result = self.accepted(native)
        loop = result["loops"][0]
        self.assertEqual((result["line_count"], result["arc_count"], result["segment_count"]), (4, 4, 8))
        area, perimeter = segment_measurements(loop)
        self.assertAlmostEqual(area, 6000 - (4-math.pi)*100, places=7)
        self.assertAlmostEqual(perimeter, 320 - 80 + 20*math.pi, places=7)
        self.assertAlmostEqual(loop["signed_area_mm2"], 6000 - (4-math.pi)*100, places=7)
        self.assertAlmostEqual(loop["perimeter_mm"], 320 - 80 + 20*math.pi, places=7)
        self.assertEqual(native, before)
        self.assertEqual(result["coordinate_frame"], PANE)
        self.assertEqual(result["coordinate_space"], "panel_local")
        self.assertEqual(result["coordinate_unit"], "mm")
        self.assertEqual(result["source"]["revision"], "oracle-revision")
        self.assertFalse(result["native_tessellation"])
        self.assertFalse(result["rendered_boundary"])
        self.assertFalse(result["cuts_applied"])
        self.assertFalse(result["hole_roles_inferred"])
        for arc in (s for s in loop["segments"] if s["kind"] == "arc"):
            self.assertAlmostEqual(arc["sweep_radians"], math.pi/2, places=10)
            self.assertAlmostEqual(arc["radius_mm"], 10)
        self.assertLess(polygon_area(loop["sampled_points"]), area)
        self.assertLess(area-polygon_area(loop["sampled_points"]), perimeter*0.1)

    def test_actual_chord_error_at_default_and_large_radius_small_tolerance(self):
        for width, height, radius, tolerance in ((100, 60, 10, 0.1), (10000, 6000, 1000, 0.003)):
            with self.subTest(radius=radius, tolerance=tolerance):
                result = self.accepted(rectangle_native(width, height, radius), chord_tolerance_mm=tolerance)
                centers = [(radius, radius), (width-radius, radius),
                           (width-radius, height-radius), (radius, height-radius)]
                self.assert_round_chord_error(result, centers, radius, tolerance)
                self.assertLessEqual(result["sampled_edge_count"], 12000)
                self.assertLessEqual(result["loops"][0]["diagnostics"]["chord_error_bound_mm"], tolerance)

    def test_smaller_tolerance_refines_geometry_without_changing_analytic_curve(self):
        native = rectangle_native()
        coarse = self.accepted(native, chord_tolerance_mm=0.5)
        fine = self.accepted(native, chord_tolerance_mm=0.01)
        self.assertGreater(fine["sampled_edge_count"], coarse["sampled_edge_count"])
        self.assertEqual(segment_measurements(coarse["loops"][0]), segment_measurements(fine["loops"][0]))

    def test_projection_removes_native_surface_offset_but_retains_raw_evidence(self):
        native = rectangle_native()
        result = self.accepted(native)
        arc = next(s for s in result["loops"][0]["segments"] if s["kind"] == "arc")
        source = arc["source"]
        self.assertEqual(source["native_boundary_points"], native["loops"][0]["vertices"][source["vertex_index"]]["boundary_points"]["panel_local"])
        self.assertEqual(arc["start"][2], 0)
        self.assertEqual(arc["end"][2], 0)
        self.assertEqual(arc["center"][2], 0)
        self.assertIn("projection", source)
        self.assertIn("signed_offsets_mm", source["projection"])
        self.assertEqual(point_record(native, number=6)["position_mm"][2], -6)

    def test_mixed_straight_and_rounded_corner_uses_trimmed_edges(self):
        result = self.accepted(rectangle_native(radii=[10, 0, 0, 0]))
        self.assertEqual((result["line_count"], result["arc_count"]), (4, 1))
        area, perimeter = segment_measurements(result["loops"][0])
        self.assertAlmostEqual(area, 6000-(1-math.pi/4)*100, places=7)
        self.assertAlmostEqual(perimeter, 320-20+5*math.pi, places=7)
        self.assertNotIn([0.0, 0.0, 0.0], result["loops"][0]["sampled_points"])
        self.assertIn([100.0, 0.0, 0.0], result["loops"][0]["sampled_points"])

    def test_concave_l_corner_uses_negative_sweep_and_adds_the_correct_area(self):
        result = self.accepted(l_corner_native())
        arc = next(s for s in result["loops"][0]["segments"] if s["kind"] == "arc")
        self.assertAlmostEqual(arc["sweep_radians"], -math.pi/2, places=10)
        area, perimeter = segment_measurements(result["loops"][0])
        self.assertAlmostEqual(area, 8400+(1-math.pi/4)*100, places=7)
        self.assertAlmostEqual(perimeter, 400-20+5*math.pi, places=7)

    def test_convex_and_concave_rounds_in_the_same_loop(self):
        result = self.accepted(l_corner_native(all_rounded=True))
        sweeps = [s["sweep_radians"] for s in result["loops"][0]["segments"] if s["kind"] == "arc"]
        self.assertEqual(sum(s > 0 for s in sweeps), 5)
        self.assertEqual(sum(s < 0 for s in sweeps), 1)
        area, perimeter = segment_measurements(result["loops"][0])
        self.assertAlmostEqual(area, 8400-4*(1-math.pi/4)*25, places=7)
        self.assertAlmostEqual(perimeter, 400-60+15*math.pi, places=7)

    def test_non_right_angle_arcs_use_120_degree_sweeps(self):
        result = self.accepted(triangle_native())
        arcs = [s for s in result["loops"][0]["segments"] if s["kind"] == "arc"]
        self.assertEqual(len(arcs), 3)
        for arc in arcs:
            self.assertAlmostEqual(arc["sweep_radians"], 2*math.pi/3, places=9)
        area, perimeter = segment_measurements(result["loops"][0])
        self.assertAlmostEqual(area, 2500*math.sqrt(3)-300*(math.sqrt(3)-math.pi/3), places=6)
        self.assertAlmostEqual(perimeter, 300-60*math.sqrt(3)+20*math.pi, places=7)

    def test_clockwise_input_reverses_sweeps_without_changing_shape(self):
        native = rectangle_native()
        vertices = native["loops"][0]["vertices"]
        vertices.reverse()
        for index, vertex in enumerate(vertices):
            vertex["vertex_index"] = index
            for name in ("panel_local", "WORLD"):
                points = {p["number"]: p for p in vertex["boundary_points"][name]["points"]}
                points[4]["position_mm"], points[6]["position_mm"] = points[6]["position_mm"], points[4]["position_mm"]
        result = self.accepted(native)
        area, perimeter = segment_measurements(result["loops"][0])
        self.assertAlmostEqual(area, -(6000-(4-math.pi)*100), places=7)
        self.assertAlmostEqual(perimeter, 320-80+20*math.pi, places=7)

    def test_nested_loops_remain_independent_and_are_not_labelled_holes(self):
        native = combine_native(rectangle_native(), rectangle_native(30, 20, 2, origin=(35, 20)))
        result = self.accepted(native)
        self.assertEqual(result["loop_count"], 2)
        self.assertEqual([loop["role"] for loop in result["loops"]], ["not inferred"]*2)
        self.assertFalse(result["hole_roles_inferred"])
        self.assertEqual(result["arc_count"], 8)
        self.assertAlmostEqual(segment_measurements(result["loops"][1])[0], 600-(4-math.pi)*4, places=7)

    def test_straight_only_loop_needs_no_native_derived_points(self):
        native = rectangle_native(radius=0)
        for vertex in native["loops"][0]["vertices"]:
            vertex.pop("boundary_points")
        result = self.accepted(native)
        self.assertEqual((result["line_count"], result["arc_count"]), (4, 0))
        self.assertEqual(result["sampled_edge_count"], 4)

    def test_world_is_the_same_curve_under_an_independent_rigid_transform(self):
        native = rectangle_native()
        local = self.accepted(native)
        world = self.accepted(native, coordinate_space="WORLD")
        self.assertEqual(world["coordinate_frame"], WORLD)
        self.assertEqual(world["sampled_edge_count"], local["sampled_edge_count"])
        for a, b in zip(local["loops"][0]["segments"], world["loops"][0]["segments"]):
            self.assertEqual(a["kind"], b["kind"])
            for field in (("start", "end", "center") if a["kind"] == "arc" else ("start", "end")):
                self.assertLess(math.dist(rigid_point(a[field]), b[field]), 1e-6)
            if b["kind"] == "arc":
                vertex = native["loops"][0]["vertices"][b["source"]["vertex_index"]]
                self.assertEqual(b["source"]["native_panel_local_boundary_points"], vertex["boundary_points"]["panel_local"])
        for a, b in zip(local["loops"][0]["sampled_points"], world["loops"][0]["sampled_points"]):
            self.assertLess(math.dist(rigid_point(a), b), 1e-6)

    def test_world_scale_shear_and_reflection_are_rejected(self):
        for axes in (((2, 0, 0), (0, 1, 0), (0, 0, 1)),
                     ((1, 0, 0), (0.2, 1, 0), (0, 0, 1)),
                     ((-1, 0, 0), (0, 1, 0), (0, 0, 1))):
            with self.subTest(axes=axes):
                self.rejected(install_world(rectangle_native(), axes), coordinate_space="WORLD")

    def test_world_point_and_direction_must_agree_with_pole_frame(self):
        for field in ("position", "direction"):
            with self.subTest(field=field):
                native = rectangle_native()
                if field == "position":
                    point_record(native, number=8, frame="WORLD")["position_mm"][0] += 2
                else:
                    point_record(native, number=0, frame="WORLD")["direction"] = [0, 0, 1]
                self.rejected(native, coordinate_space="WORLD")

    def test_local_mode_is_independent_of_failed_world_observations(self):
        native = rectangle_native()
        native.update(world_positions_complete=False, world_read_errors={"WORLD": "unavailable"}, world_position_frame=None)
        self.accepted(native, coordinate_space="panel_local")
        self.rejected(native, coordinate_space="WORLD")

    def test_wrong_wrt_or_units_never_substitute_another_frame(self):
        for space, frame in (("panel_local", "panel_local"), ("WORLD", "WORLD")):
            for field, value in (("frame_refno", "=9/999"), ("unit", "m")):
                with self.subTest(space=space, field=field):
                    native = rectangle_native()
                    native["loops"][0]["vertices"][0]["boundary_points"][frame][field] = value
                    self.rejected(native, coordinate_space=space)

    def test_poles_and_p0_planes_must_be_coplanar(self):
        for corruption in ("pole_z", "p0_z", "p0_normal"):
            with self.subTest(corruption=corruption):
                native = rectangle_native()
                if corruption == "pole_z":
                    native["loops"][0]["vertices"][0]["position"][2] = 1
                elif corruption == "p0_z":
                    point_record(native)["position_mm"][2] = 1
                else:
                    point_record(native)["direction"] = [math.sqrt(0.5), 0, math.sqrt(0.5)]
                self.rejected(native)

    def test_radius_tangent_and_point_order_must_match_adjacent_edges(self):
        for corruption in ("radius", "center", "tangent", "swap"):
            with self.subTest(corruption=corruption):
                native = rectangle_native()
                if corruption == "radius":
                    native["loops"][0]["vertices"][0]["fillet_radius_mm"] = 11
                elif corruption == "center":
                    point_record(native, number=8)["position_mm"][0] += 1
                elif corruption == "tangent":
                    point_record(native, number=6)["position_mm"][0] += 1
                else:
                    a, b = point_record(native, number=4), point_record(native, number=6)
                    a["position_mm"], b["position_mm"] = b["position_mm"], a["position_mm"]
                self.rejected(native)

    def test_adjacent_fillet_overshoot_and_self_crossing_are_rejected(self):
        self.rejected(rectangle_native(width=25, height=60, radius=15))
        self.rejected(native_from_corners([(0, 0), (100, 60), (0, 60), (100, 0)], [0]*4, {}))

    def test_analytic_line_arc_and_arc_arc_crossings_are_detected_between_coarse_samples(self):
        # This point is at 202.5 degrees on the lower-left R10 arc. With
        # tolerance 2, its containing 45-degree chord is about 0.76 mm away.
        # The 0.01/0.1 mm second loops therefore test curve intersections,
        # which cannot be established from those coarse sampled chords.
        angle = math.radians(202.5)
        x, y = 10+10*math.cos(angle), 10+10*math.sin(angle)
        first = rectangle_native()
        for second in (rectangle_native(0.01, 0.01, 0, origin=(x-0.005, y-0.005)),
                       rectangle_native(0.1, 0.1, 0.05, origin=(x-0.05, y-0.05))):
            with self.subTest(curved=bool(second["boundary_points_curved_vertex_count"])):
                self.accepted(second, chord_tolerance_mm=2)
                result = self.rejected(combine_native(first, second), chord_tolerance_mm=2)
                self.assertIn("loop_boundaries_intersect_or_touch", result["errors"])

    def test_negative_unknown_or_nonfinite_radius_is_rejected(self):
        for radius in (-1, None, True, "10", float("inf"), float("nan")):
            with self.subTest(radius=radius):
                native = rectangle_native()
                native["loops"][0]["vertices"][0]["fillet_radius_mm"] = radius
                if isinstance(radius, float) and math.isnan(radius):
                    result = derive_panel_curve_boundary(native)
                    self.assertFalse(result["success"], result)
                    self.assertEqual(result["loops"], [])
                else:
                    self.rejected(native)

    def test_import_descriptor_is_required_checked_and_untagged_for_every_vertex(self):
        for mode in ("missing", "unchecked", "tagged"):
            with self.subTest(mode=mode):
                native = rectangle_native(radii=[0, 10, 10, 10])
                vertex = native["loops"][0]["vertices"][0]
                if mode == "missing":
                    vertex.pop("curve_import_descriptor")
                else:
                    vertex["curve_import_descriptor"]["checked" if mode == "unchecked" else "tagged"] = mode != "unchecked"
                self.rejected(native)

    def test_unknown_curve_attributes_or_missing_evidence_are_rejected(self):
        for key, value in (("CTYA", "ARC"), ("GTYP", "CIRCLE"), ("BANG", "45degree"), ("CTYA", None)):
            with self.subTest(key=key, value=value):
                native = rectangle_native()
                if value is None:
                    native["loops"][0]["vertices"][0].pop(key)
                else:
                    native["loops"][0]["vertices"][0][key] = value
                self.rejected(native)

    def test_explicit_false_existence_is_rejected_without_rewriting_native(self):
        native = rectangle_native()
        point_record(native, number=4)["exists"] = False
        self.rejected(native)
        self.assertIs(point_record(native, number=4)["exists"], False)

    def test_ppexst_unset_can_resolve_geometry_while_raw_complete_stays_false(self):
        native = install_unset_existence(rectangle_native())
        before = deepcopy(native)
        for space in ("panel_local", "WORLD"):
            with self.subTest(space=space):
                result = self.accepted(native, coordinate_space=space)
                self.assertIs(result["native_boundary_points_complete"], False)
                self.assertIs(result["native_boundary_points_read_complete"], False)
                self.assertIs(result["native_existence_verified"], False)
                self.assertEqual(result["source_diagnostics"]["boundary_points_errors"], native["boundary_points_errors"])
        self.assertEqual(native, before)

    def test_unset_exception_does_not_admit_unrelated_errors_or_unchecked_existence(self):
        for corruption in ("unrelated", "unchecked", "missing_position", "extra_error"):
            with self.subTest(corruption=corruption):
                native = install_unset_existence(rectangle_native())
                point = point_record(native, number=4)
                if corruption == "unrelated":
                    point["errors"]["exists"] = "PPEXST could not be read because the database is unavailable."
                elif corruption == "unchecked":
                    point["exists_checked"] = False
                elif corruption == "missing_position":
                    point["position_complete"] = False
                else:
                    point["errors"]["position"] = "PPOS unavailable"
                self.rejected(native)

    def test_native_source_incomplete_blind_or_count_mismatch_is_rejected(self):
        for corruption in ("complete", "errors", "blind", "count", "source_complete", "element_type"):
            with self.subTest(corruption=corruption):
                native = rectangle_native()
                if corruption == "complete":
                    native["complete"] = False
                elif corruption == "errors":
                    native["read_errors"] = {"FRAD": "unavailable"}
                elif corruption == "blind":
                    native["loops"][0]["blind"] = True
                elif corruption == "count":
                    native["vertex_count"] += 1
                elif corruption == "source_complete":
                    native["boundary_points_source_geometry_complete"] = False
                else:
                    native["element"]["type"] = {"name": "PANEL"}
                self.rejected(native)

    def test_invalid_parameters_and_sampling_budget_are_rejected(self):
        for tolerance in (0, -0.1, True, "0.1", float("nan"), float("inf")):
            with self.subTest(tolerance=tolerance):
                result = derive_panel_curve_boundary(rectangle_native(), chord_tolerance_mm=tolerance)
                self.assertFalse(result["success"], result)
                self.assertEqual(result["loops"], [])
        for space in ("OWNER", [], {}, None, 7):
            with self.subTest(coordinate_space=space):
                self.rejected(rectangle_native(), coordinate_space=space)
        result = self.rejected(rectangle_native(10_000_000, 6_000_000, 1_000_000), chord_tolerance_mm=1e-5)
        self.assertTrue(any("limit" in str(error).lower() or "budget" in str(error).lower() for error in result["errors"]), result)

    def test_saved_r4_contract_preserves_unset_and_matches_rectangle_oracle(self):
        path = ROOT / "artifacts" / "pave-boundary-points-readback-20261120-r4.json"
        if not path.exists():
            self.skipTest("Saved native r4 receipt is optional; analytic fixtures remain independent.")
        receipt = json.loads(path.read_text(encoding="utf-8-sig"))
        native = deepcopy(receipt["geometry_observations"]["frad20.boundary_WORLD"])
        self.rejected(native)  # The historical receipt predates explicit DESP evidence.
        for loop in native["loops"]:
            for vertex in loop["vertices"]:
                vertex["curve_import_descriptor"] = {"checked": True, "tagged": False,
                                                       "source": "synthetic test qualification; not native DESP proof"}
        result = self.accepted(native)
        area, perimeter = segment_measurements(result["loops"][0])
        self.assertAlmostEqual(area, 20000-(4-math.pi)*400, places=5)
        self.assertAlmostEqual(perimeter, 600-160+40*math.pi, places=6)
        self.assertIs(result["native_boundary_points_complete"], False)
        self.assertIs(result["native_existence_verified"], False)
        self.accepted(native, coordinate_space="WORLD")


if __name__ == "__main__":
    unittest.main()

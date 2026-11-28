from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import random
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.piping_centerline import (
    _segment_distance_mm, centerline_options, centerline_schema, plan_centerline,
)
from aveva_e3d_mcp.protocol import _schema_matches


OPTIONS = {
    "start_endpoint_index": 0, "end_endpoint_index": 1, "waypoints_world_mm": [[1000, 0, 0]],
    "bend_radius_mm": 100, "minimum_straight_mm": 50, "position_tolerance_mm": 0.01,
    "direction_tolerance_degrees": 0.1, "start_direction_sense": 1, "end_direction_sense": -1,
}


def endpoint(position, direction, number=1):
    return {"point_number": number, "binding": {"matches_enclosing_context": True}, "response": {
        "ok": True, "version": "E3D21", "result": {
            "success": True, "element": {"refno": "=1/2", "type": "NOZZ"},
            "frame": {"requested": "WORLD", "type": "WORL"}, "errors": {},
            "points": [{"number": number, "errors": {},
                "exists": {"attribute": "PPEXST", "available": True, "value": True, "error": None},
                "position": {"attribute": "PPOS", "available": True, "unit": "mm", "error": None,
                             **dict(zip(("x", "y", "z"), position))},
                "direction": {"attribute": "PDIR", "available": True, "error": None,
                              **dict(zip(("x", "y", "z"), direction))},
            }],
        }}}


def endpoints():
    return [endpoint([0, 0, 0], [1, 0, 0]), endpoint([1000, 1000, 0], [0, -1, 0])]


def plan(options=None, groups=None):
    return plan_centerline(centerline_options(OPTIONS if options is None else options, 2),
                           endpoints() if groups is None else groups)


def assert_analytic_continuity(result):
    elements = result["elements"]
    assert elements
    for first, second in zip(elements, elements[1:]):
        assert first["end_world_mm"] == pytest.approx(second["start_world_mm"])
    for item in elements:
        if item["kind"] == "straight":
            assert math.dist(item["start_world_mm"], item["end_world_mm"]) == pytest.approx(item["length_mm"])
        else:
            center, radius = item["center_world_mm"], item["radius_mm"]
            assert math.dist(center, item["start_world_mm"]) == pytest.approx(radius, rel=1e-7)
            assert math.dist(center, item["end_world_mm"]) == pytest.approx(radius, rel=1e-7)
            x, y, angle = item["radial_x_world"], item["radial_y_world"], item["sweep_radians"]
            assert [center[i] + radius * (x[i] * math.cos(angle) + y[i] * math.sin(angle))
                    for i in range(3)] == pytest.approx(item["end_world_mm"], abs=1e-7)
            assert sum(x[i] * item["incoming_direction"][i] for i in range(3)) == pytest.approx(0, abs=1e-7)
            assert y == pytest.approx(item["incoming_direction"], abs=1e-7)


def test_right_angle_run_lengths_and_analytic_bend():
    result = plan()
    assert result["complete"] is result["geometry_fits"] is result["geometric_fit"] is True
    assert result["status"] == "computed"
    assert [item["kind"] for item in result["elements"]] == ["straight", "circular_bend", "straight"]
    bend = result["bend_candidates"][0]
    assert bend["start_world_mm"] == pytest.approx([900, 0, 0])
    assert bend["end_world_mm"] == pytest.approx([1000, 100, 0])
    assert bend["center_world_mm"] == pytest.approx([900, 100, 0])
    assert bend["normal_world"] == pytest.approx([0, 0, 1])
    assert bend["turn_degrees"] == pytest.approx(90)
    assert bend["takeout_mm"] == pytest.approx(100)
    assert result["polyline_length_mm"] == 2000
    assert result["centerline_length_mm"] == pytest.approx(1800 + 50 * math.pi)
    assert result["execution_prepared"] is result["catalogue_verified"] is result["planned_member_geometry_verified"] is False
    assert_analytic_continuity(result)


@pytest.mark.parametrize("degrees", [1, 15, 45, 90, 120, 170])
@pytest.mark.parametrize("plane", ["XY", "XZ", "oblique"])
def test_arbitrary_angle_and_spatial_bends(degrees, plane):
    theta = math.radians(degrees)
    u = [1, 0, 0] if plane != "oblique" else [1 / math.sqrt(2), 1 / math.sqrt(2), 0]
    v = [0, 1, 0] if plane == "XY" else [0, 0, 1]
    outgoing = [math.cos(theta) * u[i] + math.sin(theta) * v[i] for i in range(3)]
    vertex = [5000 * x for x in u]
    end = [vertex[i] + 5000 * outgoing[i] for i in range(3)]
    result = plan({**OPTIONS, "waypoints_world_mm": [vertex]},
                  [endpoint([0, 0, 0], u), endpoint(end, [-value for value in outgoing])])
    assert result["geometric_fit"] is True
    assert result["bend_candidates"][0]["takeout_mm"] == pytest.approx(100 * math.tan(theta / 2))
    assert_analytic_continuity(result)


def test_contiguous_spatial_bends_reserve_both_adjacent_takeouts():
    opts = {**OPTIONS, "waypoints_world_mm": [[1000, 0, 0], [1000, 1000, 0]]}
    result = plan(opts, [endpoint([0, 0, 0], [1, 0, 0]), endpoint([1000, 1000, 1000], [0, 0, -1])])
    assert result["geometric_fit"] is True
    assert result["legs"][1]["required_takeout_mm"] == pytest.approx(200)
    assert result["legs"][1]["remaining_straight_mm"] == pytest.approx(800)
    assert result["centerline_length_mm"] == pytest.approx(2600 + 100 * math.pi)
    assert_analytic_continuity(result)


@pytest.mark.parametrize("radius,minimum,code", [(600, 0, "bend_takeouts_overlap"), (100, 850, "minimum_straight_not_met")])
def test_insufficient_space_never_returns_an_assembled_route(radius, minimum, code):
    opts = {**OPTIONS, "waypoints_world_mm": [[1000, 0, 0], [1000, 1000, 0]],
            "bend_radius_mm": radius, "minimum_straight_mm": minimum}
    result = plan(opts, [endpoint([0, 0, 0], [1, 0, 0]), endpoint([2000, 1000, 0], [-1, 0, 0])])
    assert result["complete"] is True
    assert result["geometry_fits"] is result["geometric_fit"] is False
    assert result["elements"] == []
    assert result["centerline_length_mm"] is None
    assert code in {row["code"] for row in result["diagnostics"]}


def test_large_caller_tolerance_does_not_hide_negative_straight():
    result = plan({**OPTIONS, "bend_radius_mm": 1000.1, "minimum_straight_mm": 0, "position_tolerance_mm": 100})
    assert result["geometric_fit"] is False
    assert {row["code"] for row in result["diagnostics"]} == {"bend_takeouts_overlap"}


def test_touching_bends_with_explicit_zero_minimum_omit_the_zero_length_spool():
    result = plan({**OPTIONS, "waypoints_world_mm": [[1000, 0, 0], [1000, 200, 0]], "minimum_straight_mm": 0},
                  [endpoint([0, 0, 0], [1, 0, 0]), endpoint([2000, 200, 0], [-1, 0, 0])])
    assert result["geometric_fit"] is True
    assert [row["kind"] for row in result["elements"]] == ["straight", "circular_bend", "circular_bend", "straight"]
    assert_analytic_continuity(result)


def test_collinear_waypoints_do_not_create_artificial_short_spools():
    result = plan({**OPTIONS, "waypoints_world_mm": [[100, 0, 0], [900, 0, 0]], "minimum_straight_mm": 500},
                  [endpoint([0, 0, 0], [1, 0, 0]), endpoint([1000, 0, 0], [-1, 0, 0])])
    assert result["geometric_fit"] is True
    assert result["omitted_collinear_vertex_indices"] == [1, 2]
    assert len(result["elements"]) == 1
    assert result["centerline_length_mm"] == 1000


@pytest.mark.parametrize("waypoints,code", [([[0, 0, 0]], "coincident_or_short_leg"),
    ([[0.005, 0, 0]], "coincident_or_short_leg"), ([[1000, 0, 0], [500, 0, 0]], "route_reversal")])
def test_degenerate_routes_are_explicitly_blocked(waypoints, code):
    result = plan({**OPTIONS, "waypoints_world_mm": waypoints})
    assert result["geometric_fit"] is False
    assert result["elements"] == []
    assert code in {row["code"] for row in result["diagnostics"]}
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("first,second,expected", [
    (([0, 0, 0], [100, 100, 0]), ([0, 100, 0], [100, 0, 0]), 0),
    (([0, 0, 0], [100, 0, 0]), ([50, -10, 1], [50, 10, 1]), 1),
    (([0, 0, 0], [100, 0, 0]), ([0, 0, 0.0001], [100, 0, 0.0001]), 0.0001),
    (([0, 0, 0], [100, 0, 0]), ([75, 0, 0], [25, 0, 0]), 0),
    (([0, 0, 0], [100, 0, 0]), ([100, 0, 0], [100, 0, 100]), 0),
])
def test_segment_distance_is_finite_three_dimensional_and_handles_overlap(first, second, expected):
    assert _segment_distance_mm(*first, *second) == pytest.approx(expected, abs=1e-9)


def test_segment_distance_is_stable_at_large_world_offsets():
    shift = [999999000.0, -999999000.0, 999999000.0]
    translate = lambda point: [shift[index] + point[index] for index in range(3)]
    assert _segment_distance_mm(translate([0, 0, 0]), translate([100, 100, 0]),
                                translate([0, 100, 0]), translate([100, 0, 0])) <= 1e-5


def test_segment_distance_detects_shallow_crossing_of_long_segments():
    # A dot-product Gram determinant loses the small angular component here.
    # The segments cross at x=300,000,000 mm despite their 1e-7 rad angle.
    separation = _segment_distance_mm([0, 0, 0], [1_000_000_000, 0, 0],
                                      [100_000_000, -20, 0], [900_000_000, 60, 0])
    assert separation <= 1e-5


def test_planner_blocks_a_shallow_crossing_in_long_world_segments():
    diagonal = 1 / math.sqrt(2)
    options = {**OPTIONS,
        "waypoints_world_mm": [[1_000_000_000, 0, 0], [100_000_000, -20, 0], [900_000_000, 60, 0]],
        "bend_radius_mm": 0.000001, "minimum_straight_mm": 0}
    groups = [endpoint([0, 0, 0], [1, 0, 0]),
              endpoint([1_000_000_000, 100_000_000, 0], [-diagonal, -diagonal, 0], number=2)]

    result = plan(options, groups)

    assert result["geometric_fit"] is result["geometry_fits"] is False
    assert result["elements"] == []
    assert any(row["code"] == "waypoint_polyline_self_intersection" and row["segment_indices"] == [0, 2]
               for row in result["diagnostics"])


def test_input_waypoint_polyline_crossing_blocks_route_geometry():
    diagonal = 1 / math.sqrt(2)
    options = {**OPTIONS, "waypoints_world_mm": [[100, 100, 0], [0, 100, 0]],
               "bend_radius_mm": 1, "minimum_straight_mm": 0}
    groups = [endpoint([0, 0, 0], [diagonal, diagonal, 0]),
              endpoint([100, 0, 0], [-diagonal, diagonal, 0], number=2)]

    result = plan(options, groups)

    assert result["geometric_fit"] is result["geometry_fits"] is False
    assert result["elements"] == []
    assert result["centerline_length_mm"] is None
    intersection = next(row for row in result["diagnostics"]
                        if row["code"] == "waypoint_polyline_self_intersection")
    assert intersection["segment_indices"] == [0, 2]
    assert intersection["separation_mm"] <= intersection["rounding_tolerance_mm"]


def test_projected_crossing_with_world_separation_is_not_a_polyline_intersection():
    diagonal = 1 / math.sqrt(2)
    options = {**OPTIONS, "waypoints_world_mm": [[100, 100, 0], [0, 100, 10]],
               "bend_radius_mm": 1, "minimum_straight_mm": 0}
    groups = [endpoint([0, 0, 0], [diagonal, diagonal, 0]),
              endpoint([100, 0, 10], [-diagonal, diagonal, 0], number=2)]

    result = plan(options, groups)

    assert "waypoint_polyline_self_intersection" not in {row["code"] for row in result["diagnostics"]}


def test_direction_sense_is_explicit_and_mismatch_is_separate_from_space():
    result = plan({**OPTIONS, "start_direction_sense": -1})
    assert result["geometry_fits"] is True
    assert result["geometric_fit"] is False
    assert result["endpoint_alignment"]["start"]["angle_degrees"] == 180
    assert result["endpoint_alignment"]["end"]["within_tolerance"] is True


def test_missing_direction_retains_geometry_with_unknown_endpoint_fit():
    groups = endpoints()
    groups[0]["response"]["result"]["points"][0]["direction"]["available"] = False
    result = plan(groups=groups)
    assert result["available"] is result["geometry_fits"] is True
    assert result["complete"] is False
    assert result["geometric_fit"] is None
    assert result["status"] == "endpoint_direction_unavailable"
    assert result["elements"]


def test_other_native_field_failures_do_not_erase_independent_position_and_direction():
    groups = endpoints()
    native = groups[0]["response"]["result"]
    native.update(complete=False, errors={"P1.bore": "unavailable"})
    native["points"][0].update(complete=False, errors={"bore": "unavailable"})
    assert plan(groups=groups)["geometric_fit"] is True


def test_straight_route_needs_no_fabricated_bend_or_takeout():
    result = plan({**OPTIONS, "waypoints_world_mm": [], "direction_tolerance_degrees": 0},
                  [endpoint([0, 0, 0], [1, 0, 0]), endpoint([1000, 0, 0], [-1, 0, 0])])
    assert result["geometric_fit"] is True
    assert result["bend_candidates"] == []
    assert result["centerline_length_mm"] == 1000
    assert result["legs"][0]["required_takeout_mm"] == 0


def test_large_world_offset_preserves_small_local_lengths_and_bend_orientation():
    shift = [999999000, -999999000, 999999000]
    translate = lambda point: [shift[i] + point[i] for i in range(3)]
    options = {**OPTIONS, "waypoints_world_mm": [translate([1000, 0, 0])]}
    result = plan(options, [endpoint(translate([0, 0, 0]), [1, 0, 0]),
                            endpoint(translate([1000, 1000, 0]), [0, -1, 0])])
    assert result["geometric_fit"] is True
    assert result["centerline_length_mm"] == pytest.approx(plan()["centerline_length_mm"])
    assert_analytic_continuity(result)


def test_near_reversal_is_bounded_and_never_overflows_json_numbers():
    result = plan({**OPTIONS, "waypoints_world_mm": [[1000, 0, 0]]},
                  [endpoint([0, 0, 0], [1, 0, 0]), endpoint([0, 0.00001, 0], [1, 0, 0])])
    assert result["geometric_fit"] is False
    assert result["elements"] == []
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("mutate", [
    lambda g: g["binding"].update(matches_enclosing_context=False),
    lambda g: g["response"].update(ok=False),
    lambda g: g["response"].update(version="E3D31"),
    lambda g: g["response"]["result"].update(success=False),
    lambda g: g["response"]["result"]["frame"].update(requested="OWNER"),
    lambda g: g["response"]["result"]["frame"].update(type="EQUI"),
    lambda g: g["response"]["result"]["points"].append(deepcopy(g["response"]["result"]["points"][0])),
    lambda g: g["response"]["result"]["points"][0].update(number=True),
    lambda g: g["response"]["result"]["points"][0]["exists"].update(value=False),
    lambda g: g["response"]["result"]["points"][0]["position"].update(unit="in"),
    lambda g: g["response"]["result"]["points"][0]["position"].update(x=float("nan")),
    lambda g: g["response"]["result"]["points"][0]["position"].update(x=True),
    lambda g: g["response"]["result"]["points"][0]["position"].update(x=10**400),
    lambda g: g["response"]["result"]["points"][0]["position"].update(error="failed"),
    lambda g: g["response"]["result"]["points"][0]["errors"].update(position="failed"),
    lambda g: g["response"]["result"]["errors"].update({"P1.position": "failed"}),
])
def test_unverified_native_position_or_context_produces_no_geometry(mutate):
    groups = endpoints()
    mutate(groups[0])
    result = plan(groups=groups)
    assert result["available"] is result["complete"] is False
    assert result["geometric_fit"] is None
    assert result["elements"] == []


@pytest.mark.parametrize("direction", [[0, 0, 0], [2, 0, 0], [float("inf"), 0, 0]])
def test_invalid_native_direction_remains_unknown(direction):
    groups = [endpoint([0, 0, 0], direction), endpoints()[1]]
    assert plan(groups=groups)["geometric_fit"] is None


@pytest.mark.parametrize("key,value", [
    ("bend_radius_mm", 0), ("bend_radius_mm", float("nan")), ("minimum_straight_mm", -1),
    ("bend_radius_mm", 10**400), ("bend_radius_mm", True), ("start_endpoint_index", 2),
    ("end_endpoint_index", 0), ("start_endpoint_index", True), ("start_direction_sense", True),
    ("end_direction_sense", 0), ("position_tolerance_mm", 0), ("direction_tolerance_degrees", 91),
    ("waypoints_world_mm", [[0, 1]]), ("waypoints_world_mm", [[10**400, 0, 0]]),
    ("waypoints_world_mm", [[0, 0, 0]] * 33), ("waypoints_world_mm", [[True, 0, 0]]),
])
def test_strict_options_reject_invalid_choices(key, value):
    raw = {**OPTIONS, key: value}
    with pytest.raises(ValueError):
        centerline_options(raw, 2)
    assert _schema_matches(raw, centerline_schema()) is False or key in {"start_endpoint_index", "end_endpoint_index"}


@pytest.mark.parametrize("raw", [None, {}, {**OPTIONS, "create": True}])
def test_no_defaults_are_invented_for_required_geometric_choices(raw):
    with pytest.raises(ValueError):
        centerline_options(raw, 2)


def test_sources_and_caller_options_are_not_mutated():
    raw, groups = deepcopy(OPTIONS), endpoints()
    original = deepcopy([raw, groups])
    plan(raw, groups)
    assert [raw, groups] == original


def test_random_spatial_bends_preserve_circle_radius_and_join_continuity():
    rng = random.Random(101)
    for _ in range(120):
        u = [rng.uniform(-1, 1) for _ in range(3)]
        magnitude = math.hypot(*u)
        u = [value / magnitude for value in u]
        v = [-u[1], u[0], 0]
        magnitude = math.hypot(*v)
        v = [value / magnitude for value in v]
        theta = rng.uniform(0.02, 2.8)
        outgoing = [u[i] * math.cos(theta) + v[i] * math.sin(theta) for i in range(3)]
        origin = [rng.uniform(-1e6, 1e6) for _ in range(3)]
        vertex = [origin[i] + 10000 * u[i] for i in range(3)]
        end = [vertex[i] + 10000 * outgoing[i] for i in range(3)]
        result = plan({**OPTIONS, "waypoints_world_mm": [vertex]},
                      [endpoint(origin, u), endpoint(end, [-value for value in outgoing])])
        assert result["geometric_fit"] is True
        assert_analytic_continuity(result)
        json.dumps(result, allow_nan=False)

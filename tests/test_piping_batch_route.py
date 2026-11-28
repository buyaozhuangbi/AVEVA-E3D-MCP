from copy import deepcopy
import json

import pytest

from aveva_e3d_mcp.piping_batch_route import (
    batch_route_schema,
    optimize_batch_routes,
    validate_batch_routes,
    _batch_polyline_alerts,
    _choose_global_candidates,
)


BASE = {
    "bend_radius_mm": 100,
    "minimum_straight_mm": 0,
    "position_tolerance_mm": 0.01,
    "direction_tolerance_degrees": 0.1,
    "routes": [{"route_id": "R-101", "head_endpoint_index": 0, "tail_endpoint_index": 1}],
}


def endpoint(position, direction, refno, number=1):
    return {
        "point_number": number,
        "binding": {"matches_enclosing_context": True},
        "response": {
            "ok": True,
            "version": "E3D21",
            "result": {
                "success": True,
                "element": {"refno": refno, "type": "NOZZ"},
                "frame": {"requested": "WORLD", "type": "WORL"},
                "errors": {},
                "points": [{
                    "number": number,
                    "errors": {},
                    "exists": {"attribute": "PPEXST", "available": True, "value": True, "error": None},
                    "position": {"attribute": "PPOS", "available": True, "unit": "mm", "error": None,
                                 **dict(zip(("x", "y", "z"), position))},
                    "direction": {"attribute": "PDIR", "available": True, "error": None,
                                  **dict(zip(("x", "y", "z"), direction))},
                }],
            },
        },
    }


def good_endpoints():
    return [endpoint([0, 0, 0], [1, 0, 0], "=1/10"),
            endpoint([1000, 1000, 0], [0, -1, 0], "=1/11")]


def test_batch_validation_derives_lead_length_and_accepts_route_overrides():
    raw = deepcopy(BASE)
    raw["minimum_straight_mm"] = 40
    raw["routes"][0]["bend_radius_mm"] = 150
    raw["routes"][0]["direction_tolerance_degrees"] = 1

    result = validate_batch_routes(raw, 2)

    assert result["routes"][0]["lead_length_mm"] == 190
    assert result["routes"][0]["lead_length_source"] == "bend_radius_plus_minimum_straight_90_degree_rule"
    assert result["routes"][0]["bend_radius_mm"] == 150
    assert result["routes"][0]["minimum_straight_mm"] == 40
    assert result["routes"][0]["direction_tolerance_degrees"] == 1


def test_explicit_lead_length_is_echoed_and_source_is_identified():
    raw = deepcopy(BASE)
    raw["routes"][0]["lead_length_mm"] = 225
    result = validate_batch_routes(raw, 2)["routes"][0]
    assert result["lead_length_mm"] == 225
    assert result["lead_length_source"] == "caller"


@pytest.mark.parametrize("mutate", [
    lambda raw: raw.update(extra=True),
    lambda raw: raw["routes"][0].update(extra=True),
    lambda raw: raw["routes"][0].update(head_endpoint_index=True),
    lambda raw: raw["routes"][0].update(tail_endpoint_index=0),
    lambda raw: raw["routes"][0].update(route_id="bad id"),
    lambda raw: raw["routes"].append(dict(raw["routes"][0])),
    lambda raw: raw["routes"][0].update(bend_radius_mm=float("nan")),
    lambda raw: raw["routes"][0].update(lead_length_mm=10**400),
    lambda raw: raw["routes"][0].update(minimum_straight_mm=True),
])
def test_batch_validation_rejects_ambiguous_or_invalid_inputs(mutate):
    raw = deepcopy(BASE)
    mutate(raw)
    with pytest.raises(ValueError):
        validate_batch_routes(raw, 4)


def test_batch_optimizer_ranks_candidates_and_returns_reproducible_selected_path():
    before = deepcopy(BASE)
    result = optimize_batch_routes(BASE, good_endpoints())
    repeated = optimize_batch_routes(BASE, good_endpoints())

    route = result["routes"][0]
    assert result["status"] == "computed"
    assert route["status"] == "computed"
    assert route["candidate_count"] > 0
    assert route["selected"] is not None
    assert route["candidate_previews"]
    scores = [candidate["score"]["centerline_length_mm"] for candidate in route["candidate_previews"]]
    assert scores == sorted(scores)
    assert route["selected"]["candidate_id"] == route["candidate_previews"][0]["candidate_id"]
    assert route["selected"]["centerline"]["geometric_fit"] is True
    assert result["optimization"]["execution_prepared"] is False
    assert result["optimization"]["obstacle_clearance_checked"] is False
    assert result == repeated
    assert BASE == before
    json.dumps(result, allow_nan=False)


def test_route_specific_radius_and_straight_are_used_for_lead_and_candidate_geometry():
    raw = deepcopy(BASE)
    raw["routes"][0].update(bend_radius_mm=120, minimum_straight_mm=35)
    result = optimize_batch_routes(raw, good_endpoints())["routes"][0]

    assert result["geometry_constraints"]["bend_radius_mm"] == 120
    assert result["geometry_constraints"]["minimum_straight_mm"] == 35
    assert result["lead_length_mm"] == 155
    assert result["selected"] is not None


def test_missing_native_direction_fails_closed_for_that_route():
    groups = good_endpoints()
    groups[0]["response"]["result"]["points"][0]["direction"]["available"] = False
    result = optimize_batch_routes(BASE, groups)
    assert result["status"] == "no_route_computed"
    assert result["routes"][0]["status"] == "endpoint_direction_unavailable"
    assert result["routes"][0]["selected"] is None


def test_candidates_with_non_right_angle_bends_are_rejected_and_counted():
    raw = deepcopy(BASE)
    raw["direction_tolerance_degrees"] = 90
    groups = [endpoint([0, 0, 0], [2 ** -0.5, 2 ** -0.5, 0], "=1/10"),
              endpoint([1000, 500, 0], [2 ** -0.5, 2 ** -0.5, 0], "=1/11")]

    route = optimize_batch_routes(raw, groups)["routes"][0]

    assert route["rejected_diagnostics"]["non_90_degree_bend"] > 0
    for candidate in route.get("candidate_previews", []):
        assert all(abs(bend["turn_degrees"] - 90) <= 1e-6
                   for bend in candidate["centerline"]["bend_candidates"])


def test_global_beam_prefers_fewer_polyline_contacts_before_route_length():
    def candidate(candidate_id, points, length):
        return {
            "candidate_id": candidate_id,
            "score": {"centerline_length_mm": length, "bend_count": 1,
                      "polyline_length_mm": length + 10},
            "centerline": {"vertices_world_mm": points},
        }

    routes = [
        {"route_id": "R1", "candidate_previews": [
            candidate("short-crossing", [[0, 0, 0], [100, 100, 0]], 140),
            candidate("long-clear", [[0, 0, 0], [0, -100, 0], [200, -100, 0],
                                      [200, 100, 0], [100, 100, 0]], 500),
        ]},
        {"route_id": "R2", "candidate_previews": [
            candidate("fixed-crossing", [[0, 100, 0], [100, 0, 0]], 140),
        ]},
    ]

    selected, score, expanded = _choose_global_candidates(routes, 64)

    assert selected["R1"]["candidate_id"] == "long-clear"
    assert selected["R2"]["candidate_id"] == "fixed-crossing"
    assert score["selected_polyline_contact_count"] == 0
    assert score["total_centerline_length_mm"] == 640
    assert expanded == 4


@pytest.mark.parametrize("beam_width", [0, 65, True, 1.5])
def test_beam_width_must_be_a_bounded_integer(beam_width):
    with pytest.raises(ValueError):
        optimize_batch_routes(BASE, good_endpoints(), beam_width=beam_width)


def test_same_native_p_point_cannot_be_reused_for_a_different_route():
    raw = deepcopy(BASE)
    raw["routes"] = [
        {"route_id": "R1", "head_endpoint_index": 0, "tail_endpoint_index": 1},
        {"route_id": "R2", "head_endpoint_index": 2, "tail_endpoint_index": 3},
    ]
    groups = good_endpoints() + [deepcopy(good_endpoints()[0]), deepcopy(good_endpoints()[1])]
    result = optimize_batch_routes(raw, groups)
    assert result["routes"][0]["status"] == "computed"
    assert result["routes"][1]["status"] == "endpoint_reused_across_batch"
    assert result["status"] == "partially_computed"


def test_batch_polyline_crossing_is_reported_as_an_alert_not_an_engineering_verdict():
    def selected(route_id, points):
        return {"route_id": route_id, "selected": {"centerline": {"vertices_world_mm": points}}}

    result = _batch_polyline_alerts([
        selected("R1", [[0, 0, 0], [100, 100, 0]]),
        selected("R2", [[0, 100, 0], [100, 0, 0]]),
    ])
    assert result["available"] is True
    assert result["alerts"][0]["code"] == "selected_route_polyline_proximity"
    assert result["alerts"][0]["route_ids"] == ["R1", "R2"]
    assert "not an engineering clash verdict" in result["limitations"]


def test_schema_keeps_batch_constraints_explicit_and_lead_optional():
    schema = batch_route_schema()
    assert set(schema["required"]) == {"routes", "bend_radius_mm", "minimum_straight_mm",
                                      "position_tolerance_mm", "direction_tolerance_degrees"}
    assert "lead_length_mm" not in schema["properties"]["routes"]["items"]["required"]
    assert schema["properties"]["routes"]["maxItems"] == 8

"""Bounded, read-only batch route optimisation over bound P-point evidence.

This module proposes analytic paths only.  It does not inspect obstacles,
catalogue geometry or connectivity, and it never prepares or submits a model
write.  Native observations and every engineering choice must be supplied by
the caller.
"""
from __future__ import annotations

import itertools
import math
import re
from typing import Any

from .piping_centerline import MAX_COORDINATE_MM, _segment_distance_mm, centerline_options, plan_centerline


MAX_BATCH_ROUTES = 8
MAX_COMBINATION_BEAM_WIDTH = 64
MAX_CANDIDATE_PREVIEWS = 5
RIGHT_ANGLE_TOLERANCE_DEGREES = 1e-6
MAX_ROUTE_ID_LENGTH = 64
_BATCH_FIELDS = {
    "routes", "bend_radius_mm", "minimum_straight_mm", "position_tolerance_mm",
    "direction_tolerance_degrees",
}
_ROUTE_REQUIRED = {"route_id", "head_endpoint_index", "tail_endpoint_index"}
_ROUTE_OVERRIDES = {"bend_radius_mm", "minimum_straight_mm", "position_tolerance_mm", "direction_tolerance_degrees"}
_ROUTE_OPTIONAL = _ROUTE_OVERRIDES | {"lead_length_mm"}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")


def _finite(value: Any, field: str, minimum: float, maximum: float, *, open_minimum: bool = False) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{field} must be a finite number")
    if not minimum <= value <= maximum or open_minimum and value == minimum:
        qualifier = "greater than" if open_minimum else "at least"
        raise ValueError(f"{field} must be {qualifier} {minimum} and at most {maximum}")
    return float(value)


def validate_batch_routes(raw: Any, endpoint_count: int) -> dict[str, Any]:
    """Validate the complete batch request before any native observation call."""
    if not isinstance(raw, dict) or set(raw) != _BATCH_FIELDS:
        raise ValueError("batch route request requires exactly " + ", ".join(sorted(_BATCH_FIELDS)))
    routes = raw["routes"]
    if not isinstance(routes, list) or not 1 <= len(routes) <= MAX_BATCH_ROUTES:
        raise ValueError(f"routes must contain 1 to {MAX_BATCH_ROUTES} head/tail pairs")
    result: dict[str, Any] = {
        "bend_radius_mm": _finite(raw["bend_radius_mm"], "bend_radius_mm", 0.000001, 1_000_000, open_minimum=True),
        "minimum_straight_mm": _finite(raw["minimum_straight_mm"], "minimum_straight_mm", 0, 1_000_000),
        "position_tolerance_mm": _finite(raw["position_tolerance_mm"], "position_tolerance_mm", 0.000001, 1000, open_minimum=True),
        "direction_tolerance_degrees": _finite(raw["direction_tolerance_degrees"], "direction_tolerance_degrees", 0, 90),
        "routes": [],
    }
    seen_ids: set[str] = set()
    seen_endpoints: set[int] = set()
    for index, route in enumerate(routes):
        if not isinstance(route, dict) or not _ROUTE_REQUIRED <= set(route) or set(route) - _ROUTE_REQUIRED - _ROUTE_OPTIONAL:
            raise ValueError(f"routes[{index}] requires route_id, head_endpoint_index and tail_endpoint_index, with optional geometric overrides")
        route_id = route["route_id"]
        if not isinstance(route_id, str) or not _ID.fullmatch(route_id) or len(route_id) > MAX_ROUTE_ID_LENGTH:
            raise ValueError(f"routes[{index}].route_id must be a short printable identifier")
        if route_id.casefold() in seen_ids:
            raise ValueError("route_id values must be unique")
        seen_ids.add(route_id.casefold())
        head, tail = route["head_endpoint_index"], route["tail_endpoint_index"]
        for endpoint_index, label in ((head, "head"), (tail, "tail")):
            if type(endpoint_index) is not int or not 0 <= endpoint_index < endpoint_count:
                raise ValueError(f"routes[{index}].{label}_endpoint_index must select a supplied endpoint")
            if endpoint_index in seen_endpoints:
                raise ValueError("each batch P-point endpoint can be assigned to only one route")
            seen_endpoints.add(endpoint_index)
        if head == tail:
            raise ValueError(f"routes[{index}] head and tail must be distinct endpoints")
        normalized = {
            "route_id": route_id,
            "head_endpoint_index": head,
            "tail_endpoint_index": tail,
        }
        for key, minimum, maximum, open_minimum in (
            ("bend_radius_mm", 0.000001, 1_000_000, True),
            ("minimum_straight_mm", 0, 1_000_000, False),
            ("position_tolerance_mm", 0.000001, 1000, True),
            ("direction_tolerance_degrees", 0, 90, False),
        ):
            normalized[key] = _finite(route.get(key, result[key]), f"routes[{index}].{key}", minimum, maximum, open_minimum=open_minimum)
        # A 90-degree bend consumes one bend radius of tangent length. Adding
        # the requested minimum straight therefore gives a deterministic,
        # explicit lead-in rule without asking the caller for another value.
        # The centreline checker still rejects larger local takeouts when a
        # candidate turns by more than 90 degrees.
        normalized["lead_length_mm"] = _finite(
            route.get("lead_length_mm", normalized["bend_radius_mm"] + normalized["minimum_straight_mm"]),
            f"routes[{index}].lead_length_mm", 0.000001, 1_000_000, open_minimum=True,
        )
        normalized["lead_length_source"] = "caller" if "lead_length_mm" in route else "bend_radius_plus_minimum_straight_90_degree_rule"
        result["routes"].append(normalized)
    return result


def _unit(value: list[float]) -> list[float]:
    length = math.hypot(*value)
    return [axis / length for axis in value]


def _lead(position: list[float], direction: list[float], distance: float) -> list[float]:
    return [position[axis] + direction[axis] * distance for axis in range(3)]


def _candidate_waypoints(start: list[float], end: list[float]) -> list[tuple[str, list[list[float]]]]:
    """Direct and six axis-order paths between the selected tangent lead points."""
    candidates: list[tuple[str, list[list[float]]]] = [("direct", [start, end])]
    for order in itertools.permutations(range(3)):
        point = list(start)
        path = [list(start)]
        for axis in order:
            point = list(point)
            point[axis] = end[axis]
            if point != path[-1]:
                path.append(point)
        if path[-1] != end:
            path.append(list(end))
        candidates.append(("axis_" + "".join("xyz"[axis] for axis in order), path))
    # Different axis orders collapse to the same geometry when coordinates
    # coincide. Evaluate each route only once, retaining deterministic names.
    unique: dict[tuple[tuple[float, float, float], ...], tuple[str, list[list[float]]]] = {}
    for name, points in candidates:
        key = tuple(tuple(point) for point in points)
        unique.setdefault(key, (name, points))
    return list(unique.values())


def _native_identity(endpoint: dict[str, Any]) -> tuple[str, str, int] | None:
    element = endpoint.get("element")
    refno = element.get("refno") if isinstance(element, dict) else None
    kind = element.get("type") if isinstance(element, dict) else None
    point = endpoint.get("point_number")
    if isinstance(refno, str) and isinstance(kind, str) and type(point) is int:
        return refno.casefold(), kind.upper(), point
    return None


def optimize_batch_routes(raw: Any, endpoint_groups: list[dict[str, Any]], *,
                          beam_width: int = MAX_COMBINATION_BEAM_WIDTH) -> dict[str, Any]:
    """Optimise a bounded batch of explicit head/tail pairs from native evidence.

    The global objective is minimum pairwise input-polyline contacts, then
    total analytic centreline length, fewest circular bends, and shortest
    total polyline. Each route searches direct plus six axis orders between
    explicit straight lead-ins; candidates with bends other than 90 degrees
    are rejected. No obstacle clearance or engineering suitability is inferred.
    """
    if type(beam_width) is not int or not 1 <= beam_width <= MAX_COMBINATION_BEAM_WIDTH:
        raise ValueError(f"beam_width must be an integer from 1 through {MAX_COMBINATION_BEAM_WIDTH}")
    options = validate_batch_routes(raw, len(endpoint_groups) if isinstance(endpoint_groups, list) else 0)
    output: dict[str, Any] = {
        "requested": True,
        "available": True,
        "status": "computed",
        "routes": [],
        "optimization": {
            "objective": ["fewest_selected_input_polyline_contacts", "minimum_total_centerline_length_mm",
                          "fewest_total_bends", "minimum_total_polyline_length_mm"],
            "search": "direct plus unique axis-order paths between explicit PDIR lead-ins; both endpoint direction senses; only 90-degree bends; per-route top-five candidates combined with bounded beam search",
            "obstacle_clearance_checked": False,
            "catalogue_and_specification_checked": False,
            "execution_prepared": False,
            "combination_search": {"kind": "beam", "beam_width": beam_width,
                                   "candidate_pool_per_route": MAX_CANDIDATE_PREVIEWS},
        },
        "scope": "Read-only analytic route ranking only. The planner does not inspect obstacles, pipe clashes, clearance, "
                 "catalogue or specification suitability, or native connection state, and cannot certify a safe route. "
                 "Review the selected route and independently validate it before model execution.",
    }
    identities: dict[tuple[str, str, int], str] = {}
    for route in options["routes"]:
        head_index, tail_index = route["head_endpoint_index"], route["tail_endpoint_index"]
        base = {
            "route_id": route["route_id"],
            "head_endpoint_index": head_index,
            "tail_endpoint_index": tail_index,
            "lead_length_mm": route["lead_length_mm"],
            "lead_length_source": route["lead_length_source"],
            "geometry_constraints": {key: route[key] for key in _ROUTE_OVERRIDES},
            "status": "no_geometrically_fitting_candidate",
            "candidate_count": 0,
            "selected": None,
            "rejected_diagnostics": {},
        }
        # Reuse the exact native evidence gate used by the single-route
        # centreline planner, while keeping its options isolated per candidate.
        probe_options = centerline_options({
            "start_endpoint_index": head_index,
            "end_endpoint_index": tail_index,
            "waypoints_world_mm": [],
            "bend_radius_mm": route["bend_radius_mm"],
            "minimum_straight_mm": route["minimum_straight_mm"],
            "position_tolerance_mm": route["position_tolerance_mm"],
            "direction_tolerance_degrees": route["direction_tolerance_degrees"],
            "start_direction_sense": 1,
            "end_direction_sense": 1,
        }, len(endpoint_groups))
        probe = plan_centerline(probe_options, endpoint_groups)
        start_endpoint = probe["endpoints"]["start"]
        end_endpoint = probe["endpoints"]["end"]
        base["endpoint_evidence"] = {"head": start_endpoint, "tail": end_endpoint}
        if start_endpoint.get("position_world_mm") is None or end_endpoint.get("position_world_mm") is None:
            base["status"] = "endpoint_position_unavailable"
            output["routes"].append(base)
            continue
        for endpoint, role in ((start_endpoint, "head"), (end_endpoint, "tail")):
            identity = _native_identity(endpoint)
            if identity is None:
                continue
            prior = identities.get(identity)
            if prior is not None and prior != route["route_id"]:
                base["status"] = "endpoint_reused_across_batch"
                base["endpoint_reuse"] = {"role": role, "native_identity": list(identity), "other_route_id": prior}
                output["routes"].append(base)
                break
            identities[identity] = route["route_id"]
        if base.get("status") == "endpoint_reused_across_batch":
            continue
        if start_endpoint.get("pdir_world") is None or end_endpoint.get("pdir_world") is None:
            base["status"] = "endpoint_direction_unavailable"
            output["routes"].append(base)
            continue
        start_position = start_endpoint["position_world_mm"]
        end_position = end_endpoint["position_world_mm"]
        lead_length = route["lead_length_mm"]
        if _native_identity(start_endpoint) == _native_identity(end_endpoint):
            base["status"] = "head_and_tail_resolve_to_same_p_point"
            output["routes"].append(base)
            continue
        candidates: list[dict[str, Any]] = []
        for start_sense in (1, -1):
            start_direction = [start_sense * value for value in _unit(start_endpoint["pdir_world"])]
            start_lead = _lead(start_position, start_direction, lead_length)
            for end_sense in (1, -1):
                # The final route tangent points from the tail lead into the
                # endpoint and therefore follows the same sense tested by
                # plan_centerline at the end.
                end_direction = [end_sense * value for value in _unit(end_endpoint["pdir_world"])]
                end_lead = _lead(end_position, end_direction, -lead_length)
                candidate_paths = _candidate_waypoints(start_lead, end_lead)
                if any(abs(value) > MAX_COORDINATE_MM for point in (start_lead, end_lead) for value in point):
                    base["candidate_count"] += len(candidate_paths)
                    base["rejected_diagnostics"]["candidate_coordinate_out_of_range"] = base["rejected_diagnostics"].get("candidate_coordinate_out_of_range", 0) + len(candidate_paths)
                    continue
                for candidate_name, tangent_path in candidate_paths:
                    waypoints = [list(start_lead), *[list(point) for point in tangent_path[1:-1]], list(end_lead)]
                    candidate_options = centerline_options({
                        "start_endpoint_index": head_index,
                        "end_endpoint_index": tail_index,
                        "waypoints_world_mm": waypoints,
                        "bend_radius_mm": route["bend_radius_mm"],
                        "minimum_straight_mm": route["minimum_straight_mm"],
                        "position_tolerance_mm": route["position_tolerance_mm"],
                        "direction_tolerance_degrees": route["direction_tolerance_degrees"],
                        "start_direction_sense": start_sense,
                        "end_direction_sense": end_sense,
                    }, len(endpoint_groups))
                    planned = plan_centerline(candidate_options, endpoint_groups)
                    base["candidate_count"] += 1
                    bend_angles = [item.get("turn_degrees") for item in planned.get("bend_candidates", [])]
                    non_right_angles = [angle for angle in bend_angles
                                        if type(angle) not in (int, float) or
                                        abs(angle - 90.0) > RIGHT_ANGLE_TOLERANCE_DEGREES]
                    if non_right_angles:
                        base["rejected_diagnostics"]["non_90_degree_bend"] = (
                            base["rejected_diagnostics"].get("non_90_degree_bend", 0) + 1)
                        details = base.setdefault("rejected_candidate_details", [])
                        if len(details) < MAX_CANDIDATE_PREVIEWS:
                            details.append({"candidate_id": candidate_name + f"_s{start_sense}_e{end_sense}",
                                            "code": "non_90_degree_bend",
                                            "turn_degrees": bend_angles})
                        continue
                    if planned.get("geometric_fit") is True and planned.get("centerline_length_mm") is not None:
                        bends = sum(item.get("kind") == "circular_bend" for item in planned.get("elements", []))
                        score = (planned["centerline_length_mm"], bends, planned["polyline_length_mm"])
                        candidates.append({
                            "candidate_id": candidate_name + f"_s{start_sense}_e{end_sense}",
                            "score": {"centerline_length_mm": score[0], "bend_count": bends,
                                      "polyline_length_mm": score[2]},
                            "_score": score,
                            "centerline": planned,
                        })
                    else:
                        for diagnostic in planned.get("diagnostics", []):
                            code = diagnostic.get("code", "unspecified")
                            base["rejected_diagnostics"][code] = base["rejected_diagnostics"].get(code, 0) + 1
                        if planned.get("status") not in {"geometrically_blocked", "endpoint_direction_unavailable"}:
                            code = planned.get("status", "candidate_not_fit")
                            base["rejected_diagnostics"][code] = base["rejected_diagnostics"].get(code, 0) + 1
        if candidates:
            candidates.sort(key=lambda row: (row["_score"], row["candidate_id"]))
            base["candidate_previews"] = [{
                key: value for key, value in candidate.items() if key != "_score"
            } for candidate in candidates[:MAX_CANDIDATE_PREVIEWS]]
            base.update(status="computed")
        output["routes"].append(base)
    selected, global_score, explored = _choose_global_candidates(output["routes"], beam_width)
    for route in output["routes"]:
        chosen = selected.get(route["route_id"])
        if chosen is not None:
            route["selected"] = chosen
            route["globally_selected"] = True
    output["optimization"]["combination_search"].update(
        routes_combined=len(selected), expanded_states=explored,
        selected_score=global_score,
    )
    output["batch_interactions"] = _batch_polyline_alerts(output["routes"])
    if any(route["status"] != "computed" for route in output["routes"]):
        output["status"] = "partially_computed" if any(route["status"] == "computed" for route in output["routes"]) else "no_route_computed"
    return output


def _polyline_pair_alerts(first_id: str, first: list[list[float]],
                          second_id: str, second: list[list[float]]) -> list[dict[str, Any]]:
    alerts = []
    for i, (a, b) in enumerate(zip(first, first[1:])):
        for j, (c, d) in enumerate(zip(second, second[1:])):
            scale = max(abs(value) for point in (a, b, c, d) for value in point)
            tolerance = max(1e-9, 32 * math.ulp(scale))
            separation = _segment_distance_mm(a, b, c, d)
            if separation <= tolerance:
                alerts.append({
                    "code": "selected_route_polyline_proximity",
                    "route_ids": [first_id, second_id],
                    "segment_indices": [i, j],
                    "separation_mm": separation,
                    "rounding_tolerance_mm": tolerance,
                })
    return alerts


def _candidate_vertices(candidate: dict[str, Any]) -> list[list[float]]:
    return candidate["centerline"]["vertices_world_mm"]


def _candidate_score(candidate: dict[str, Any]) -> tuple[float, int, float]:
    score = candidate["score"]
    return (score["centerline_length_mm"], score["bend_count"], score["polyline_length_mm"])


def _choose_global_candidates(routes: list[dict[str, Any]], beam_width: int) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None, int]:
    """Choose among each route's bounded top candidates with a deterministic beam."""
    candidate_routes = [(row["route_id"], row["candidate_previews"])
                        for row in routes if row.get("candidate_previews")]
    if not candidate_routes:
        return {}, None, 0
    states: list[dict[str, Any]] = [{
        "selected": [], "alerts": [], "centerline_length_mm": 0.0,
        "bend_count": 0, "polyline_length_mm": 0.0,
    }]
    expanded = 0
    for route_id, candidates in candidate_routes:
        next_states = []
        for state in states:
            for candidate in candidates:
                expanded += 1
                vertices = _candidate_vertices(candidate)
                added_alerts = []
                for prior_id, prior_candidate in state["selected"]:
                    added_alerts.extend(_polyline_pair_alerts(
                        prior_id, _candidate_vertices(prior_candidate), route_id, vertices))
                score = _candidate_score(candidate)
                next_states.append({
                    "selected": [*state["selected"], (route_id, candidate)],
                    "alerts": [*state["alerts"], *added_alerts],
                    "centerline_length_mm": state["centerline_length_mm"] + score[0],
                    "bend_count": state["bend_count"] + score[1],
                    "polyline_length_mm": state["polyline_length_mm"] + score[2],
                })
        next_states.sort(key=lambda state: (
            len(state["alerts"]), state["centerline_length_mm"], state["bend_count"],
            state["polyline_length_mm"], tuple(item[1]["candidate_id"] for item in state["selected"]),
        ))
        states = next_states[:beam_width]
    best = states[0]
    selected = {route_id: candidate for route_id, candidate in best["selected"]}
    score = {
        "selected_polyline_contact_count": len(best["alerts"]),
        "total_centerline_length_mm": best["centerline_length_mm"],
        "total_bend_count": best["bend_count"],
        "total_polyline_length_mm": best["polyline_length_mm"],
        "candidate_ids": [candidate["candidate_id"] for _, candidate in best["selected"]],
    }
    return selected, score, expanded


def _batch_polyline_alerts(routes: list[dict[str, Any]]) -> dict[str, Any]:
    """Report contacts between selected input polylines, not filleted arc clashes."""
    alerts = []
    selected = [(row["route_id"], row["selected"]["centerline"]["vertices_world_mm"])
                for row in routes if row.get("selected")]
    for (first_id, first), (second_id, second) in itertools.combinations(selected, 2):
        alerts.extend(_polyline_pair_alerts(first_id, first, second_id, second))
    return {
        "available": True,
        "checked": "selected WORLD input polyline segments only",
        "alerts": alerts,
        "contact_count": len(alerts),
        "limitations": "Does not evaluate filleted circular arcs, pipe outside diameter, clearance, supports, or model obstacles; an alert is a review prompt, not an engineering clash verdict.",
    }


def batch_route_schema() -> dict[str, Any]:
    """MCP-friendly schema fragment for a caller-provided batch request."""
    return {
        "type": "object",
        "description": "Read-only batch analytic pipe-route optimisation. Supply explicit endpoint indices and shared geometric constraints; optional per-route constraints override those values. Lead length defaults to bend radius plus minimum straight under a 90-degree tangent rule. Only candidates with 90-degree bends are retained; each route's top five candidates are combined using a beam of at most 64 states, first minimizing input-polyline contacts. This is not clash, net-clearance, or engineering validation.",
        "properties": {
            "routes": {"type": "array", "minItems": 1, "maxItems": MAX_BATCH_ROUTES, "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "route_id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$"},
                    "head_endpoint_index": {"type": "integer", "minimum": 0, "maximum": 15},
                    "tail_endpoint_index": {"type": "integer", "minimum": 0, "maximum": 15},
                    "lead_length_mm": {"type": "number", "exclusiveMinimum": 0, "maximum": 1_000_000},
                    "bend_radius_mm": {"type": "number", "exclusiveMinimum": 0, "maximum": 1_000_000},
                    "minimum_straight_mm": {"type": "number", "minimum": 0, "maximum": 1_000_000},
                    "position_tolerance_mm": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000},
                    "direction_tolerance_degrees": {"type": "number", "minimum": 0, "maximum": 90},
                },
                "required": ["route_id", "head_endpoint_index", "tail_endpoint_index"],
            }},
            "bend_radius_mm": {"type": "number", "exclusiveMinimum": 0, "maximum": 1_000_000},
            "minimum_straight_mm": {"type": "number", "minimum": 0, "maximum": 1_000_000},
            "position_tolerance_mm": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000},
            "direction_tolerance_degrees": {"type": "number", "minimum": 0, "maximum": 90},
        },
        "required": sorted(_BATCH_FIELDS),
        "additionalProperties": False,
    }


# Keep the initial names as stable aliases while callers migrate to the names
# that make the validation/planning split clearer.
batch_route_options = validate_batch_routes
plan_batch_routes = optimize_batch_routes

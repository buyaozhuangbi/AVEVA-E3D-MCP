"""Read-only, native-evidence-backed batch pipe-route optimisation."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .bridge import BridgeClient, BridgeError
from .context_snapshot import context_snapshot
from .piping_batch_route import optimize_batch_routes, batch_route_schema, validate_batch_routes
from .piping_route_plan import _context_agreement, _endpoints, _identity, _response_ok, _source_binding
from .protocol import McpServer


TOOL = "e3d_piping_batch_route_plan"
MAX_ENDPOINTS = 16


def _create_geometry_draft(route: dict[str, Any], endpoints: list[dict[str, Any]]) -> dict[str, Any] | None:
    selected = route.get("selected")
    line = selected.get("centerline") if isinstance(selected, dict) else None
    options = line.get("options") if isinstance(line, dict) else None
    vertices = line.get("vertices_world_mm") if isinstance(line, dict) else None
    head, tail = route.get("head_endpoint_index"), route.get("tail_endpoint_index")
    if (not isinstance(options, dict) or not isinstance(vertices, list) or len(vertices) < 2 or
            type(head) is not int or type(tail) is not int or head >= len(endpoints) or tail >= len(endpoints)):
        return None
    create_options = {key: options[key] for key in (
        "bend_radius_mm", "minimum_straight_mm", "position_tolerance_mm", "direction_tolerance_degrees",
        "start_direction_sense", "end_direction_sense")}
    create_options.update(start_endpoint_index=0, end_endpoint_index=1,
                          waypoints_world_mm=deepcopy(vertices[1:-1]))
    bend_angles = [bend.get("turn_degrees") for bend in line.get("bend_candidates", [])]
    native_tolerance = 0.01 <= create_options["position_tolerance_mm"] <= 1
    native_angles = all(type(angle) in (int, float) and abs(angle - 90) <= 1e-6 for angle in bend_angles)
    return {
        "status": "geometry_only_requires_fresh_native_preflight" if native_tolerance and native_angles else "not_native_create_compatible",
        "model_write_authorized": False,
        "endpoints": [deepcopy(endpoints[head]), deepcopy(endpoints[tail])],
        "centerline": create_options,
        "native_route_create_compatible_geometry": native_tolerance and native_angles,
        "requires": ["parent_refno", "pipe_name", "template_branch_refno", "template_elbow_refno",
                     "fresh native route preview and revision"],
        "limitations": "Geometry draft only. Native create must re-read endpoints and verify source specification, bore, template radius, catalogue and full route readback.",
    }


def _error(errors: list[dict[str, Any]], stage: str, exc: Any) -> None:
    row = {"stage": stage, "code": "read_error", "type": type(exc).__name__, "message": str(exc)[:1024]}
    if isinstance(exc, BridgeError):
        row.update({key: getattr(exc, key) for key in ("http_status", "transport_error", "response_invalid")
                    if hasattr(exc, key)})
    errors.append(row)


def piping_batch_route_plan(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    allowed = {"endpoints", "routes", "bend_radius_mm", "minimum_straight_mm",
               "position_tolerance_mm", "direction_tolerance_degrees"}
    if not isinstance(args, dict) or set(args) != allowed:
        raise ValueError("Batch route planning requires endpoints, routes and explicit geometric constraints")
    endpoints = _endpoints(args.get("endpoints"))
    if not 2 <= len(endpoints) <= MAX_ENDPOINTS:
        raise ValueError(f"endpoints must contain 2 to {MAX_ENDPOINTS} unique typed P-points")
    batch_request = {key: deepcopy(args[key]) for key in allowed - {"endpoints"}}
    validate_batch_routes(batch_request, len(endpoints))

    version = bridge._selected_version(None)
    result: dict[str, Any] = {
        "operation": "piping_batch_route_plan", "success": False, "available": False,
        "complete": False, "read_only": True, "atomic_snapshot": False,
        "consistency": "grouped_sequential_native_reads", "status": "unavailable",
        "native_observations_complete": False, "optimization_complete": False,
        "context": {"before": None, "after": None, "boundary_agreement": None, "status": "unknown"},
        "endpoint_reads": [], "endpoints": [], "optimization": None, "read_errors": [],
        "scope": "Read-only route candidate ranking from explicit native WORLD P-point observations. success/complete "
                 "describe the bound native reads; optimization_complete separately says whether every requested pair has a ranked route. "
                 "No model write, endpoint connection, obstacle, clearance or engineering suitability verdict is performed.",
    }
    envelope = {"ok": True, "version": version, "result": result}
    if version != "E3D21" or bridge.settings.mode != "bridge":
        result["read_errors"].append({"stage": "availability", "code": "native_unavailable",
                                     "message": "Batch route planning requires E3D21 bridge mode"})
        return envelope

    try:
        before = _identity(context_snapshot(bridge, bridge.settings))
    except Exception as exc:
        _error(result["read_errors"], "context_before", exc)
        return envelope
    result["context"]["before"] = before
    if before.get("complete") is not True:
        result["read_errors"].append({"stage": "context_before", "code": "context_unknown",
                                      "message": "A fresh ready session/project/MDB/current-element boundary is unavailable"})
        return envelope

    # Batch points on each native component into one WORLD read. Every
    # requested typed point still gets its own context/identity binding.
    groups: dict[tuple[str, str], list[int]] = {}
    for index, endpoint in enumerate(endpoints):
        groups.setdefault((endpoint["refno"].casefold(), endpoint["expected_type"]), []).append(index)
    replies: dict[tuple[str, str], Any] = {}
    group_requests: dict[tuple[str, str], dict[str, Any]] = {}
    for key, indices in groups.items():
        endpoint = endpoints[indices[0]]
        group_requests[key] = {"refno": endpoint["refno"], "expected_type": endpoint["expected_type"],
                               "point_numbers": [endpoints[index]["point_number"] for index in indices],
                               "endpoint_indexes": indices}
        try:
            replies[key] = bridge.connection_points(endpoint["refno"], "WORLD",
                group_requests[key]["point_numbers"], expected_type=endpoint["expected_type"])
        except Exception as exc:
            replies[key] = None
            _error(result["read_errors"], "connection_points", exc)

    try:
        after = _identity(context_snapshot(bridge, bridge.settings))
    except Exception as exc:
        after = {"complete": False, "identity": {}}
        _error(result["read_errors"], "context_after", exc)
    result["context"]["after"] = after
    agreement = _context_agreement(before, after)
    result["context"]["boundary_agreement"] = agreement
    result["context"]["status"] = "consistent" if agreement is True else "changed" if agreement is False else "unknown"

    endpoint_groups: list[dict[str, Any]] = []
    for key, request in group_requests.items():
        reply = replies.get(key)
        result["endpoint_reads"].append({**request, "response": reply})
    for index, endpoint in enumerate(endpoints):
        key = (endpoint["refno"].casefold(), endpoint["expected_type"])
        reply = replies.get(key)
        binding = _source_binding(reply, before, after, source_kind="connection_points",
            requested_refno=endpoint["refno"], requested_type=endpoint["expected_type"],
            requested_point=endpoint["point_number"])
        result["endpoints"].append({**endpoint, "response": reply, "binding": binding})
        endpoint_groups.append({**endpoint, "response": reply, "binding": binding})

    result["optimization"] = optimize_batch_routes(batch_request, endpoint_groups)
    for route in result["optimization"].get("routes", []):
        route["native_route_create_geometry"] = _create_geometry_draft(route, endpoints)
    result["optimization_complete"] = result["optimization"].get("status") == "computed"
    native_reads_complete = agreement is True
    for key, request in group_requests.items():
        reply = replies.get(key)
        if not (_response_ok(reply) and reply["result"].get("complete") is True):
            native_reads_complete = False
        if any(result["endpoints"][index]["binding"].get("matches_enclosing_context") is not True
               for index in request["endpoint_indexes"]):
            native_reads_complete = False
    result["available"] = True
    result["complete"] = native_reads_complete
    result["native_observations_complete"] = native_reads_complete
    result["success"] = native_reads_complete
    result["status"] = ("route_candidates_ready" if result["optimization_complete"] else
                         result["optimization"].get("status", "optimization_unavailable")) if native_reads_complete else "partial_or_unknown"
    if agreement is not True:
        result["read_errors"].append({"stage": "context_after", "code": "context_unknown" if agreement is None else "context_changed",
                                      "message": "Session, build, project, MDB or current element changed or could not be verified across endpoint reads"})
    return envelope


def register_piping_batch_route_plan(server: McpServer, bridge: BridgeClient) -> None:
    option_schema = batch_route_schema()
    properties = {
        "endpoints": {"type": "array", "minItems": 2, "maxItems": MAX_ENDPOINTS,
            "description": "Shared native head/tail endpoint pool. Routes refer to these rows by zero-based index.",
            "items": {"type": "object", "properties": {
                "refno": {"type": "string", "minLength": 2, "maxLength": 256},
                "expected_type": {"type": "string", "minLength": 1, "maxLength": 64},
                "point_number": {"type": "integer", "minimum": 1, "maximum": 10000},
            }, "required": ["refno", "expected_type", "point_number"], "additionalProperties": False}},
        **option_schema["properties"],
    }
    schema = {"type": "object", "properties": properties,
              "required": ["endpoints", *option_schema["required"]], "additionalProperties": False}
    server.add_tool(TOOL,
        "Plan multiple pipe head/tail links from one shared endpoint list. Reads each selected native element's "
        "WORLD P-points in grouped E3D21 observations, then computes and ranks direct/axis-order centreline candidates "
        "for every explicitly paired head and tail. The objective and selected geometry are returned per route for "
        "immediate review. It combines up to five candidates per route with a bounded 64-state search that first minimizes "
        "input-polyline contacts across the batch. A computed candidate uses only 90-degree turns supported by the current fixed ELBO creator. "
        "No PIPE/BRAN is created and no endpoint is connected. The calculation does not inspect obstacles, pipe diameter "
        "clearance, catalogue suitability, or engineering rules; ranked geometry is not a safe-route certification. "
        "Sequential native reads are not an atomic database snapshot.",
        schema, lambda args: piping_batch_route_plan(bridge, args), read_only=True)

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.piping_batch_route_plan import TOOL, piping_batch_route_plan, register_piping_batch_route_plan
from aveva_e3d_mcp.protocol import McpServer, _schema_matches


SETTINGS = Settings(mode="bridge", version="E3D21", allow_commands=False)
ENDPOINTS = [
    {"refno": "=1/10", "expected_type": "NOZZ", "point_number": 1},
    {"refno": "=1/20", "expected_type": "NOZZ", "point_number": 2},
]
ARGS = {
    "endpoints": ENDPOINTS,
    "routes": [{"route_id": "line-A", "head_endpoint_index": 0, "tail_endpoint_index": 1}],
    "bend_radius_mm": 100,
    "minimum_straight_mm": 50,
    "position_tolerance_mm": 0.01,
    "direction_tolerance_degrees": 1,
}


@pytest.fixture(autouse=True)
def forbid_live_bridge_requests():
    with patch.object(BridgeClient, "_request", side_effect=AssertionError("Native reads must be mocked")) as request:
        yield
    request.assert_not_called()


def context(session="session"):
    identity = {"version": "E3D21", "session_id": session, "bridge_build": "build"}
    return {"ok": True, "version": "E3D21", "result": {
        "context_complete": True, "read_only": True,
        "readiness": {"before": "ready", "after": "ready"},
        "session_coherence": {"status": "consistent", "after": identity},
        "context": {"project": "APS", "mdb": "ALL", "current_element": "=1/44"},
    }}


def point_reply(refno, kind, numbers):
    points = []
    for number in numbers:
        if refno == "=1/10" and number == 1:
            position, direction = (0, 0, 0), (1, 0, 0)
        elif refno == "=1/10" and number == 2:
            position, direction = (1000, 0, 0), (0, 1, 0)
        else:
            position, direction = (1000, 1000, 0), (0, -1, 0)
        points.append({
            "number": number,
            "exists": {"attribute": "PPEXST", "available": True, "value": True},
            "position": {"attribute": "PPOS", "available": True, "unit": "mm",
                          "x": position[0], "y": position[1], "z": position[2]},
            "direction": {"attribute": "PDIR", "available": True,
                          "x": direction[0], "y": direction[1], "z": direction[2]},
            "bore": {"attribute": "PPBO", "available": True, "unit": "mm", "value_mm": 100},
        })
    sample = {"project": "APS", "mdb": "ALL", "current_element": "=1/44",
              "current_element_type": "EQUI"}
    return {"ok": True, "version": "E3D21", "session_id": "session", "bridge_build": "build",
        "result": {"success": True, "complete": True, "frame": {"requested": "WORLD", "type": "WORL"},
            "element": {"refno": refno, "type": kind}, "points": points,
            "context": {"before": sample, "after": dict(sample), "same_project_instance": True,
                        "same_mdb_instance": True, "identity_unchanged": True,
                        "ce_unchanged": True, "boundary_agreement": True},
            "project": "APS", "project_after": "APS", "mdb": "ALL", "mdb_after": "ALL",
            "ce_before": "=1/44", "ce_after": "=1/44", "identity_unchanged": True, "ce_unchanged": True}}


def run(args=ARGS, *, contexts=None, bad_ref=False):
    bridge = BridgeClient(SETTINGS)
    snapshots = list(contexts or [context(), context()])
    calls = []

    def read_points(refno, wrt, numbers, *, expected_type):
        calls.append((refno, wrt, list(numbers), expected_type))
        reply = point_reply(refno, expected_type, numbers)
        if bad_ref and refno == "=1/20":
            reply["result"]["element"]["refno"] = "=1/999"
        return reply

    with patch("aveva_e3d_mcp.piping_batch_route_plan.context_snapshot", side_effect=snapshots), \
         patch.object(BridgeClient, "connection_points", side_effect=read_points):
        result = piping_batch_route_plan(bridge, args)
    return result["result"], calls


def test_batches_endpoint_reads_and_computes_a_ranked_route_without_model_writes():
    result, calls = run()
    assert result["success"] is True and result["complete"] is True
    assert result["status"] == "route_candidates_ready"
    assert result["optimization_complete"] is True
    assert len(calls) == 2
    assert result["optimization"]["routes"][0]["status"] == "computed"
    selected = result["optimization"]["routes"][0]["selected"]
    assert selected["score"]["bend_count"] >= 0
    draft = result["optimization"]["routes"][0]["native_route_create_geometry"]
    assert draft["native_route_create_compatible_geometry"] is True
    assert [row["point_number"] for row in draft["endpoints"]] == [1, 2]
    assert draft["centerline"]["start_endpoint_index"] == 0
    assert draft["centerline"]["end_endpoint_index"] == 1
    assert result["optimization"]["optimization"]["execution_prepared"] is False
    assert result["optimization"]["batch_interactions"]["alerts"] == []


def test_multiple_points_on_one_native_element_are_read_in_one_group():
    args = {**ARGS,
        "endpoints": [
            {"refno": "=1/10", "expected_type": "NOZZ", "point_number": 1},
            {"refno": "=1/10", "expected_type": "NOZZ", "point_number": 2},
        ],
        "routes": [{"route_id": "same-equipment", "head_endpoint_index": 0, "tail_endpoint_index": 1}],
    }
    result, calls = run(args)
    assert len(calls) == 1
    assert calls[0][2] == [1, 2]
    assert result["endpoints"][0]["binding"]["checks"]["requested_point"] is True
    assert result["endpoints"][1]["binding"]["checks"]["requested_point"] is True
    assert result["optimization"]["routes"][0]["endpoint_evidence"]["tail"]["position_world_mm"] == [1000.0, 0.0, 0.0]
    assert result["optimization"]["routes"][0]["status"] in {
        "computed", "no_geometrically_fitting_candidate", "endpoint_position_unavailable",
        "endpoint_direction_unavailable", "head_and_tail_resolve_to_same_p_point",
    }


def test_context_drift_keeps_optimization_unbound_and_incomplete():
    result, _ = run(contexts=[context(), context("changed")])
    assert result["complete"] is False
    assert result["context"]["boundary_agreement"] is False
    assert all(endpoint["binding"]["matches_enclosing_context"] is False for endpoint in result["endpoints"])
    assert result["optimization"]["routes"][0]["status"] == "endpoint_position_unavailable"


def test_wrong_native_reference_blocks_endpoint_evidence():
    result, _ = run(bad_ref=True)
    endpoint = result["endpoints"][1]
    assert endpoint["binding"]["matches_enclosing_context"] is False
    assert result["complete"] is False


@pytest.mark.parametrize("change", [
    {"routes": [{"route_id": "bad", "head_endpoint_index": 0, "tail_endpoint_index": 2}]},
    {"routes": [{"route_id": "dup", "head_endpoint_index": 0, "tail_endpoint_index": 1},
                {"route_id": "dup2", "head_endpoint_index": 0, "tail_endpoint_index": 1}]},
    {"bend_radius_mm": True},
])
def test_invalid_batch_fails_before_native_reads(change):
    args = {**ARGS, **change}
    with patch("aveva_e3d_mcp.piping_batch_route_plan.context_snapshot") as read:
        with pytest.raises(ValueError):
            piping_batch_route_plan(BridgeClient(SETTINGS), args)
    read.assert_not_called()


def test_registered_schema_accepts_batch_route_input():
    server = McpServer("batch-route-test", "0")
    register_piping_batch_route_plan(server, BridgeClient(SETTINGS))
    tool = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})["result"]["tools"][0]
    assert tool["name"] == TOOL
    assert tool["annotations"]["readOnlyHint"] is True
    assert _schema_matches(ARGS, tool["inputSchema"]) is True
    assert tool["inputSchema"]["additionalProperties"] is False

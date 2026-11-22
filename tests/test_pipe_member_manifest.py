from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.pipe_member_manifest import DEFAULT_ATTRIBUTES, PATH, TOOL, register_pipe_member_manifest
from aveva_e3d_mcp.protocol import McpServer


BRIDGE = Settings(mode="bridge", version="E3D21", allow_commands=False)


def call(args, settings=BRIDGE):
    server = McpServer("pipe-manifest-test", "0")
    register_pipe_member_manifest(server, BridgeClient(settings))
    return server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": TOOL, "arguments": args}})["result"]


def result_of(response):
    return response["structuredContent"]["result"]


def sample(*, fields=("SPREF",), limit=64, depth=8):
    def row(id_, refno, kind, name, parent, layer, ordinal, spec):
        values = {"SPREF": spec} if "SPREF" in fields and spec is not None else {}
        return {"id": id_, "refno": refno, "type": kind, "name": name, "identity_key": f"{refno}|{kind}",
                "depth": layer, "parent_index": parent, "sibling_index": ordinal, "name_error": None,
                "values": values, "typed_values": {"ABOR": 80.0} if "ABOR" in fields and spec else {},
                "errors": {}, "typed_read_errors": {}, "unavailable_attributes": [f for f in fields if f not in values],
                "complete": True}

    rows = [
        row(1, "=1/2", "BRAN", "/P/B1", 0, 1, 1, None),
        row(2, "=1/3", "FLAN", "=1/3", 1, 2, 1, "/A3B/FWD80"),
        row(3, "=1/3", "TUBI", "ileave tube of =1/3", 1, 2, 2, "/A3B/PA80"),
    ]
    for member in rows[1:]:
        for field in fields:
            if field == "ABOR":
                member["values"][field] = "80mm"
                member["unavailable_attributes"].remove(field)
    before = {"project": "APS", "mdb": "ALL", "current_element": "=1/9", "current_element_type": "EQUI"}
    return {"ok": True, "version": "E3D21", "session_id": "instance", "bridge_build": "build", "result": {
        "operation": "pipe_member_manifest", "available": True, "read_only": True, "atomic_snapshot": False,
        "single_dispatch": True, "requested_refno": "=1/1", "root": {
            "id": 0, "refno": "=1/1", "type": "PIPE", "name": "/P", "identity_key": "=1/1|PIPE", "depth": 0},
        "rows": rows, "returned_count": len(rows), "order": "database_preorder", "max_depth": depth, "limit": limit,
        "attributes": list(fields), "traversal_complete": True, "all_returned_values_available": not fields,
        "complete": True, "success": True, "truncated": False, "truncation_reasons": [], "read_errors": [],
        "context": {"before": before, "after": dict(before), "same_project_instance": True,
                    "same_mdb_instance": True, "boundary_agreement": True},
    }}


def test_read_only_registration_and_shared_dbref_with_distinct_attributes():
    server = McpServer("test", "0")
    register_pipe_member_manifest(server, BridgeClient(BRIDGE))
    tool, = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})["result"]["tools"]
    assert tool["annotations"]["readOnlyHint"] is True
    assert tool["inputSchema"]["properties"]["attributes"]["default"] == list(DEFAULT_ATTRIBUTES)
    native = sample(fields=("SPREF", "ABOR"))
    with patch.object(BridgeClient, "_request", return_value=native) as request:
        response = call({"refno": "=01/001", "attributes": [" spref ", "abor"]})
    request.assert_called_once_with("POST", PATH, {"refno": "=1/1", "max_depth": 8, "limit": 64,
                                                 "attributes": ["SPREF", "ABOR"]}, version="E3D21")
    assert not response.get("isError")
    assert json.loads(response["content"][0]["text"]) == native
    result = result_of(response)
    assert result["complete"] and not result["atomic_snapshot"]
    assert not result["all_returned_values_available"]
    assert [row["values"]["SPREF"] for row in result["rows"][1:]] == ["/A3B/FWD80", "/A3B/PA80"]
    assert [row["identity_key"] for row in result["rows"][1:]] == ["=1/3|FLAN", "=1/3|TUBI"]


def test_defaults_empty_branch_and_literal_name():
    native = sample(fields=DEFAULT_ATTRIBUTES)
    result = native["result"]
    result.update(requested_refno="/branch", rows=[], returned_count=0, all_returned_values_available=True)
    result["root"].update(name="/BRANCH", type="BRAN", identity_key="=1/1|BRAN")
    with patch.object(BridgeClient, "_request", return_value=native) as request:
        response = call({"refno": " /branch "})
    request.assert_called_once_with("POST", PATH, {"refno": "/branch", "max_depth": 8, "limit": 64,
                                                 "attributes": list(DEFAULT_ATTRIBUTES)}, version="E3D21")
    assert not response.get("isError"), response
    assert result_of(response)["complete"] is True


@pytest.mark.parametrize("args", [
    {}, {"refno": "CE"}, {"refno": "/P/*"}, {"refno": "/P?"}, {"refno": "=0/1"}, {"refno": "=2147483648/1"},
    {"refno": "/P", "limit": True}, {"refno": "/P", "limit": 101}, {"refno": "/P", "max_depth": 17},
    {"refno": "/P", "attributes": ["POS", " pos "]}, {"refno": "/P", "attributes": ["POS WRT"]},
    {"refno": "/P", "limit": 100, "attributes": ["A", "B", "C", "D", "E", "F"]},
    {"refno": "/P", "cursor": "unused"},
])
def test_invalid_arguments_never_contact_bridge(args):
    with patch.object(BridgeClient, "_request") as request:
        assert call(args)["isError"]
    request.assert_not_called()


@pytest.mark.parametrize("kind", ["row_limit", "depth_limit", "member", "name", "typed_attribute", "context", "unknown_context"])
def test_partial_response_retains_verified_rows(kind):
    args = {"refno": "=1/1", "attributes": ["SPREF"]}
    native = sample()
    result = native["result"]
    result.update(success=False, complete=False)
    context = result["context"]
    if kind in ("row_limit", "depth_limit"):
        if kind == "row_limit":
            result["limit"] = args["limit"] = 1
        else:
            result["max_depth"] = args["max_depth"] = 1
        result.update(rows=result["rows"][:1], returned_count=1, truncated=True, traversal_complete=False,
                      truncation_reasons=["limit" if kind == "row_limit" else "depth"])
    elif kind == "member":
        result.update(rows=result["rows"][:2], returned_count=2, traversal_complete=False,
                      read_errors=[{"stage": "member", "parent_index": 1, "sibling_index": 2,
                                    "message": "Could not verify owner"}])
    elif kind == "name":
        result["rows"][1].update(name=None, name_error="Read failed", complete=False)
    elif kind == "typed_attribute":
        result["rows"][1].update(typed_read_errors={"SPREF": "Typed read failed"}, complete=False)
    else:
        if kind == "unknown_context":
            context.update(after=None, same_project_instance=None, same_mdb_instance=None, boundary_agreement=None)
        else:
            context.update(same_project_instance=False, boundary_agreement=False)
        result["read_errors"].append({"stage": "context_after", "message": "Could not confirm the same context"})
    with patch.object(BridgeClient, "_request", return_value=native):
        response = call(args)
    assert response["isError"]
    assert result_of(response) == result


@pytest.mark.parametrize("kind", ["root", "parent", "duplicate", "overlap", "typed_scalar", "context", "coverage", "truncation",
                                  "identity", "completeness"])
def test_malformed_native_evidence_is_not_exposed_as_a_manifest(kind):
    native = sample()
    result = native["result"]
    last = result["rows"][-1]
    if kind == "root":
        result["root"].update(type="BRAN", refno="=1/2", identity_key="=1/2|BRAN")
    elif kind == "parent":
        last["parent_index"] = 0
    elif kind == "duplicate":
        last.update(type="FLAN", identity_key="=1/3|FLAN")
    elif kind == "overlap":
        last["unavailable_attributes"] = ["SPREF"]
    elif kind == "typed_scalar":
        last["typed_values"] = {"SPREF": float("inf")}
    elif kind == "context":
        result["context"]["after"]["current_element_type"] = "TUBI"
    elif kind == "coverage":
        last["values"].clear()
    elif kind == "truncation":
        result.update(truncated=True, truncation_reasons=["limit"])
    elif kind == "identity":
        del native["bridge_build"]
    else:
        result["traversal_complete"] = False
    with patch.object(BridgeClient, "_request", return_value=native):
        response = call({"refno": "=1/1", "attributes": ["SPREF"]})
    assert response["isError"]
    rejected = result_of(response)
    assert not rejected["available"] and not rejected["complete"] and rejected["rows"] == []
    assert rejected["read_errors"][0]["code"] == "invalid_native_response"


def test_mock_wrong_version_and_running_bridge_without_feature():
    with patch.object(BridgeClient, "_request") as request:
        for settings in (Settings(mode="mock", version="E3D21"), Settings(mode="bridge", version="E3D31")):
            assert result_of(call({"refno": "/P"}, settings))["read_errors"][0]["code"] == "native_unavailable"
    request.assert_not_called()
    with patch.object(BridgeClient, "_request", side_effect=BridgeError("missing", http_status=404)) as request:
        response = call({"refno": "/P"})
    request.assert_called_once()
    assert response["isError"]
    assert result_of(response)["read_errors"][0]["code"] == "native_feature_unavailable"

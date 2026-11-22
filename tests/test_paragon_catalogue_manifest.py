from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.paragon_catalogue import (
    PATH, TOOL, _parameters, _validate, paragon_catalogue_manifest, register_paragon_catalogue_manifest,
)
from aveva_e3d_mcp.protocol import McpServer


SETTINGS = Settings(mode="bridge", version="E3D21", allow_commands=False)


def identity(number, kind, *, catalogue=True):
    return {"refno": f"=1/{number}", "type": kind, "identity_key": f"=1/{number}|{kind}",
            "db_type": "Catalogue" if catalogue else "Design", "db_type_code": 2 if catalogue else 1}


def sample(*, attributes=("DESC",)):
    identities = [identity(1, "CATA"), identity(2, "SECT"), identity(3, "CATE")]
    rows = []
    for index, native in enumerate(identities):
        rows.append({**native, "id": index, "depth": index, "name": ["/CAT", "/CAT/S", "/CAT/S/C"][index],
                     "name_error": None, "parent_index": index - 1 if index else None,
                     "sibling_index": 1 if index else None,
                     "owner": identities[index - 1] if index else identity(99, "WORL"), "owner_error": None,
                     "values": {name: "native display" for name in attributes},
                     "typed_values": {name: "native scalar" for name in attributes},
                     "errors": {}, "typed_read_errors": {}, "unavailable_attributes": [], "complete": True})
    before = {"session_id": "session", "bridge_build": "build", "project": "PRJ", "mdb": "CAT-MDB",
              "current_element": identity(90, "EQUI", catalogue=False), "errors": {}}
    return {"ok": True, "version": "E3D21", "session_id": "session", "bridge_build": "build", "result": {
        "operation": "paragon_catalogue_manifest", "available": True, "success": True, "complete": True,
        "read_only": True, "single_dispatch": True, "atomic_snapshot": False, "requested_refno": "=1/1", "root_id": 0,
        "rows": rows, "returned_count": 3, "order": "database_preorder", "max_depth": 8, "limit": 64,
        "attributes": list(attributes), "traversal_complete": True, "all_returned_values_available": True,
        "truncated": False, "truncation_reasons": [], "read_errors": [],
        "context": {"before": before, "after": deepcopy(before), "same_project_instance": True,
                    "same_mdb_instance": True, "boundary_agreement": True},
        "scope": {"kind": "explicit_catalogue_subtree", "database_type": "Catalogue", "database_type_code": 2,
                  "whole_project_coverage": False, "current_module": None,
                  "database_creation_verified": None, "engineering_validated": None},
    }}


def call(args, settings=SETTINGS):
    server = McpServer("paragon-test", "0")
    register_paragon_catalogue_manifest(server, BridgeClient(settings))
    return server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": TOOL, "arguments": args}})["result"]


def result_of(response):
    return response["structuredContent"]["result"]


def test_read_only_registration_single_request_and_native_catalogue_evidence():
    server = McpServer("paragon-test", "0")
    register_paragon_catalogue_manifest(server, BridgeClient(SETTINGS))
    tool, = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})["result"]["tools"]
    assert tool["name"] == TOOL and tool["annotations"]["readOnlyHint"] is True
    assert tool["inputSchema"]["properties"]["attributes"]["default"] == []
    native = sample()
    with patch.object(BridgeClient, "_request", return_value=native) as request:
        response = call({"refno": "=01/001", "attributes": [" desc "]})
    request.assert_called_once_with("POST", PATH, {"refno": "=1/1", "max_depth": 8, "limit": 64,
                                                 "attributes": ["DESC"]}, version="E3D21")
    assert not response.get("isError"), response
    assert json.loads(response["content"][0]["text"]) == native
    assert result_of(response) == native["result"]
    assert result_of(response)["context"]["before"]["current_element"]["db_type"] == "Design"
    assert result_of(response)["scope"]["current_module"] is None


@pytest.mark.parametrize("args", [
    {}, {"refno": "CE"}, {"refno": "/"}, {"refno": "/C?"}, {"refno": "/C/*"},
    {"refno": "/C A"}, {"refno": "/C\x00"}, {"refno": "=0/1"}, {"refno": "=1/-1"},
    {"refno": "=2147483648/1"}, {"refno": "=1/2147483648"}, {"refno": "/" + "a" * 256},
    {"refno": "/C", "max_depth": True}, {"refno": "/C", "max_depth": 0}, {"refno": "/C", "max_depth": 17},
    {"refno": "/C", "limit": True}, {"refno": "/C", "limit": 0}, {"refno": "/C", "limit": 101},
    {"refno": "/C", "attributes": None}, {"refno": "/C", "attributes": ["DESC", " desc "]},
    {"refno": "/C", "attributes": ["DESC WRT"]}, {"refno": "/C", "attributes": ["D\u00c9SC"]},
    {"refno": "/C", "attributes": ["A"] * 11},
    {"refno": "/C", "limit": 100, "attributes": ["A", "B", "C", "D", "E", "F"]},
    {"refno": "/C", "command": "ignored"}, {"refno": "/C", "version": "E3D31"},
])
def test_invalid_input_never_contacts_bridge(args):
    with patch.object(BridgeClient, "_request") as request:
        assert call(args)["isError"]
    request.assert_not_called()


def test_explicit_name_default_attributes_and_exact_root_count():
    native = sample(attributes=())
    result = native["result"]
    result.update(requested_refno="/cat", rows=result["rows"][:1], returned_count=1, limit=1)
    with patch.object(BridgeClient, "_request", return_value=native) as request:
        response = call({"refno": " /cat ", "limit": 1})
    request.assert_called_once_with("POST", PATH, {"refno": "/cat", "max_depth": 8, "limit": 1,
                                                 "attributes": []}, version="E3D21")
    assert not response.get("isError"), response
    assert result_of(response)["returned_count"] == 1 and result_of(response)["complete"]
    assert _parameters({"refno": "=1/0", "limit": 100, "attributes": ["A", "B", "C", "D", "E"]})["limit"] == 100


@pytest.mark.parametrize("kind", ["depth", "limit", "member", "name", "owner", "attribute", "typed_attribute",
                                  "unavailable_attribute", "unknown_before_ce", "unknown_after_mdb", "same_name_mdb",
                                  "typed_ce", "changed_project"])
def test_partial_and_unknown_evidence_preserves_verified_rows(kind):
    native = sample()
    result = native["result"]
    args = {"refno": "=1/1", "attributes": ["DESC"]}
    result.update(success=False, complete=False)
    context = result["context"]
    if kind in {"depth", "limit"}:
        result.update(rows=result["rows"][:2], returned_count=2, truncated=True,
                      truncation_reasons=[kind], traversal_complete=False)
        name, value = ("max_depth", 1) if kind == "depth" else ("limit", 2)
        args[name] = result[name] = value
    elif kind == "member":
        result.update(traversal_complete=False, read_errors=[{
            "stage": "member", "parent_identity_key": "=1/2|SECT", "sibling_index": 2, "message": "SDK member failed"}])
    elif kind in {"name", "owner", "attribute", "typed_attribute", "unavailable_attribute"}:
        row = result["rows"][0]
        row["complete"] = False
        if kind in {"name", "owner"}:
            row.update({kind: None, kind + "_error": "SDK read failed"})
        else:
            result["all_returned_values_available"] = False
            if kind == "typed_attribute":
                row["typed_values"].clear()
                row["typed_read_errors"]["DESC"] = "SDK scalar read failed"
            else:
                row["values"].clear()
                row["typed_values"].clear()
                if kind == "attribute":
                    row["errors"]["DESC"] = "SDK text read failed"
                else:
                    row["unavailable_attributes"] = ["DESC"]
    elif kind.startswith("unknown"):
        boundary, field = ("before", "current_element") if kind == "unknown_before_ce" else ("after", "mdb")
        context[boundary][field] = None
        context[boundary]["errors"][field] = "SDK context read failed"
        context["boundary_agreement"] = None
        if field == "mdb":
            context["same_mdb_instance"] = None
    else:
        context["boundary_agreement"] = False
        if kind == "same_name_mdb":
            context["same_mdb_instance"] = False
        elif kind == "typed_ce":
            ce = context["after"]["current_element"]
            ce.update(type="BOX", identity_key="=1/90|BOX")
        else:
            context["after"]["project"] = "OTHER"
    with patch.object(BridgeClient, "_request", return_value=native):
        response = call(args)
    assert response["isError"]
    assert result_of(response) == result, response


@pytest.mark.parametrize("kind", ["design_cata", "draft_liby", "owner", "sibling", "parent", "duplicate", "depth", "count",
                                  "budget", "name", "scope", "session", "build", "context", "missing_unknown_error",
                                  "attribute_coverage", "attribute_overlap", "scalar_nan", "complete", "truncation"])
def test_malformed_or_misbound_native_evidence_is_rejected(kind):
    native = sample()
    result = native["result"]
    first, last = result["rows"][0], result["rows"][-1]
    if kind == "design_cata":
        first.update(db_type="Design", db_type_code=1)
    elif kind == "draft_liby":
        first.update(type="LIBY", identity_key="=1/1|LIBY", db_type="Draft", db_type_code=7)
    elif kind == "owner":
        last["owner"] = identity(1, "CATA")
    elif kind in {"sibling", "parent", "depth"}:
        last[{"sibling": "sibling_index", "parent": "parent_index", "depth": "depth"}[kind]] = 9
    elif kind == "duplicate":
        last.update(identity(2, "SECT"))
    elif kind == "count":
        result["returned_count"] = 4
    elif kind == "budget":
        result["limit"] = 100
    elif kind == "name":
        result["requested_refno"] = "/CAT"
    elif kind == "scope":
        result["scope"]["whole_project_coverage"] = True
    elif kind in {"session", "build"}:
        result["context"]["after"]["session_id" if kind == "session" else "bridge_build"] = "changed"
    elif kind == "context":
        result["context"]["after"]["current_element"].update(type="BOX", identity_key="=1/90|BOX")
    elif kind == "missing_unknown_error":
        result["context"]["after"]["current_element"] = None
    elif kind == "attribute_coverage":
        last["values"].clear()
    elif kind == "attribute_overlap":
        last["unavailable_attributes"] = ["DESC"]
    elif kind == "scalar_nan":
        last["typed_values"]["DESC"] = float("nan")
    elif kind == "complete":
        result["complete"] = False
    else:
        result.update(truncated=True, truncation_reasons=["limit"])
    with patch.object(BridgeClient, "_request", return_value=native):
        response = call({"refno": "=1/1", "attributes": ["DESC"]})
    rejected = result_of(response)
    assert response["isError"] and not rejected["available"] and not rejected["complete"]
    assert rejected["rows"] == [] and rejected["read_errors"][0]["code"] == "invalid_native_response"


def test_unavailable_modes_and_old_bridge_do_not_invent_catalogue_evidence():
    with patch.object(BridgeClient, "_request") as request:
        for settings in (Settings(mode="mock", version="E3D21"), Settings(mode="bridge", version="E3D31")):
            result = result_of(call({"refno": "/CAT"}, settings))
            assert not result["available"] and result["rows"] == [] and result["context"]["before"] is None
    request.assert_not_called()
    with patch.object(BridgeClient, "_request", side_effect=BridgeError("missing", http_status=404)) as request:
        assert result_of(call({"refno": "/CAT"}))["read_errors"][0]["code"] == "native_feature_unavailable"
    request.assert_called_once()


@pytest.mark.parametrize("transport_error", [False, True])
@pytest.mark.parametrize("marker", [
    {"error_code": "native_database_fault", "native_fault_latched": True, "retry_safe": False},
    {"error_code": "native_database_fault"}, {"native_fault_latched": True},
    {"error_code": "native_fault"}, {"native_fault": True}, {"native_fault": {"has_fault": True, "code": 538}},
])
def test_native_fault_stops_without_retries_or_diagnostic_followups(transport_error, marker):
    diagnostics = {"ok": False, **marker}
    error = BridgeError("native stopped", http_status=404, diagnostics=diagnostics)
    kwargs = {"side_effect": error} if transport_error else {"return_value": diagnostics}
    with patch.object(BridgeClient, "_request", **kwargs) as request:
        with pytest.raises(BridgeError) as caught:
            paragon_catalogue_manifest(BridgeClient(SETTINGS), {"refno": "/CAT"})
    request.assert_called_once()
    assert caught.value.diagnostics == diagnostics


def test_validator_accepts_identity_only_and_preserves_unknown_module():
    native = sample(attributes=())
    _validate(native, _parameters({"refno": "=1/1"}))
    assert native["result"]["scope"]["database_creation_verified"] is None

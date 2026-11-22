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
from aveva_e3d_mcp.paragon_schema import PATH, TOOL, _parameters, _validate, paragon_catalogue_schema, register_paragon_catalogue_schema
from aveva_e3d_mcp.protocol import McpServer


SETTINGS = Settings(mode="bridge", version="E3D21", allow_commands=False)


def identity(number, kind, *, catalogue=True):
    return {"refno": f"=1/{number}", "type": kind, "identity_key": f"=1/{number}|{kind}",
            "db_type": "Catalogue" if catalogue else "Design", "db_type_code": 2 if catalogue else 1}


def constraint():
    return {"status": "complete", "allowed_values": {
        "status": "defined", "defined": True,
        "values": [{"value": "A", "description": "native description", "value_truncated": False, "description_truncated": False}],
        "total_count": 1, "returned_count": 1, "truncated": False, "error": None,
    }, "allowed_ranges": {"status": "not_defined", "defined": False, "raw_boundaries": [], "total_count": 0,
        "returned_count": 0, "truncated": False, "error": None, "non_finite_indices": [], "unit_basis": "unknown", "boundary_encoding": "unknown"},
        "values_or_ranges_optional": {"status": "complete", "value": True, "error": None}}


def sample(*, attributes=("DESC",), include=True):
    boundary = {"session_id": "session", "bridge_build": "build", "project": "PRJ", "mdb": "CAT-MDB",
                "current_element": identity(90, "EQUI", catalogue=False), "errors": {}}
    children = [{"sdk_index": index, "valid": True, "name": name, "short_name": name, "database_type_codes": [code],
                 "database_types_total_count": 1, "database_types_truncated": False, "catalogue_supported": code == 2,
                 "sdk_creatable_now": code == 2, "errors": {}, "complete": True}
                for index, (name, code) in enumerate((("SECT", 2), ("LIBY", 7)))]
    fields = [{"requested_name": name, "known": True, "supported": True, "name": name, "short_name": name,
               "data_type": "STRING", "is_array": False, "is_pseudo": False, "is_uda": name.startswith(":"),
               "unit_kind": "NONE", "maximum_size": 80, "errors": {}, "metadata_complete": True, "complete": True,
               "constraints": constraint() if include else None} for name in attributes]
    return {"ok": True, "version": "E3D21", "session_id": "session", "bridge_build": "build", "result": {
        "operation": "paragon_catalogue_schema", "available": True, "success": True, "complete": True,
        "read_only": True, "single_dispatch": True, "atomic_snapshot": False,
        "requested_refno": "=1/1", "requested_attributes": list(attributes), "include_constraints": include,
        "target": {**identity(1, "CATA"), "name": "/CAT", "name_error": None}, "child_types": children,
        "child_types_observation": {"limit": 200, "returned_count": 2, "total_count": 2, "truncated": False,
                                    "complete": True, "order": "sdk_member_types", "native_arrays_materialized_before_output_limits": True},
        "attributes": fields, "attributes_complete": True, "constraints_complete": True if include else None, "read_errors": {},
        "context": {"before": boundary, "after": deepcopy(boundary), "same_project_instance": True,
                    "same_mdb_instance": True, "boundary_agreement": True},
        "output_limits": {"child_types": 200, "database_types_per_child": 32, "attributes": 10,
                          "values_or_boundaries_per_attribute": 100, "constraint_text_utf16_characters": 1024,
                          "native_arrays_materialized_before_output_limits": True},
        "scope": {"kind": "catalogue_parent_schema", "attribute_target": "current_target", "member_type_basis": "native_base_type",
                  "future_child_attributes_observed": False, "whole_project_coverage": False,
                  "current_module": None, "permissions_verified": None, "proposed_values_validated": False,
                  "engineering_validated": None, "range_unit_basis": "unknown", "range_boundary_encoding": "unknown",
                  "optionality_is_requiredness": False},
    }}


def call(args, settings=SETTINGS):
    server = McpServer("paragon-schema-test", "0")
    register_paragon_catalogue_schema(server, BridgeClient(settings))
    return server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": TOOL, "arguments": args}})["result"]


def result_of(response):
    return response["structuredContent"]["result"]


def test_discovery_readonly_single_request_parent_attribute_semantics():
    server = McpServer("paragon-schema-test", "0")
    register_paragon_catalogue_schema(server, BridgeClient(SETTINGS))
    tool, = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})["result"]["tools"]
    assert tool["name"] == TOOL and tool["annotations"]["readOnlyHint"] is True
    assert tool["inputSchema"]["properties"]["attributes"]["default"] == []
    assert tool["inputSchema"]["properties"]["include_constraints"]["default"] is False
    native = sample()
    with patch.object(BridgeClient, "_request", return_value=native) as request:
        response = call({"refno": "=01/001", "attributes": [" desc "], "include_constraints": True})
    request.assert_called_once_with("POST", PATH, {"refno": "=1/1", "attributes": ["DESC"], "include_constraints": True}, version="E3D21")
    assert not response.get("isError"), response
    assert json.loads(response["content"][0]["text"]) == native
    result = result_of(response)
    assert result["child_types"][1]["database_type_codes"] == [7] and result["child_types"][1]["catalogue_supported"] is False
    assert result["scope"]["attribute_target"] == "current_target" and not result["scope"]["future_child_attributes_observed"]
    assert "direct_edit_supported" not in result["attributes"][0]


def test_default_identity_only_name_reference_and_empty_native_members():
    native = sample(attributes=(), include=False)
    result = native["result"]
    result["requested_refno"] = "/cat"
    result["child_types"] = []
    result["child_types_observation"].update(returned_count=0, total_count=0)
    with patch.object(BridgeClient, "_request", return_value=native) as request:
        response = call({"refno": " /cat "})
    request.assert_called_once_with("POST", PATH, {"refno": "/cat", "attributes": [], "include_constraints": False}, version="E3D21")
    assert not response.get("isError") and result_of(response)["constraints_complete"] is None
    assert _parameters({"refno": "=1/0", "attributes": ["A" + str(i) for i in range(10)], "include_constraints": True})["refno"] == "=1/0"


@pytest.mark.parametrize("args", [
    {}, {"refno": "CE"}, {"refno": "/"}, {"refno": "/CAT/*"}, {"refno": "/CAT?"}, {"refno": "/C A"},
    {"refno": "/CAT\x01"}, {"refno": "=0/1"}, {"refno": "=1/-1"}, {"refno": "=2147483648/1"},
    {"refno": "=1/2147483648"}, {"refno": "/" + "C" * 256}, {"refno": "/C", "attributes": None},
    {"refno": "/C", "attributes": "DESC"}, {"refno": "/C", "attributes": ["DESC", " desc "]},
    {"refno": "/C", "attributes": ["A" + str(i) for i in range(11)]}, {"refno": "/C", "attributes": [1]},
    {"refno": "/C", "attributes": ["DÉSC"]}, {"refno": "/C", "attributes": ["DESC WRT"]},
    {"refno": "/C", "include_constraints": 1}, {"refno": "/C", "include_constraints": "false"},
    {"refno": "/C", "include_constraints": True}, {"refno": "/C", "attributes": [], "include_constraints": True},
    {"refno": "/C", "proposed_values": {}}, {"refno": "/C", "child_type": "SECT"},
    {"refno": "/C", "limit": 200}, {"refno": "/C", "version": "E3D31"},
])
def test_invalid_arguments_do_not_dispatch(args):
    with patch.object(BridgeClient, "_request") as request:
        assert call(args)["isError"]
    request.assert_not_called()


@pytest.mark.parametrize("kind", ["member_types", "child_name", "database_types", "creatable", "invalid_child", "type_budget",
                                  "unknown_attribute", "metadata_error", "unsupported", "allowed_values", "allowed_ranges", "optionality",
                                  "constraint_budget", "name", "unknown_context", "same_name_mdb", "typed_ce"])
def test_partial_and_unknown_observations_preserve_independent_evidence(kind):
    native = sample()
    result = native["result"]
    result.update(success=False, complete=False)
    child, field = result["child_types"][0], result["attributes"][0]
    if kind == "member_types":
        result["child_types"] = []
        result["child_types_observation"].update(returned_count=0, total_count=None, truncated=None, complete=False)
        result["read_errors"] = {"member_types": "SDK member types failed"}
    elif kind in {"child_name", "database_types", "creatable", "invalid_child", "type_budget"}:
        result["child_types_observation"]["complete"] = False
        child["complete"] = False
        if kind == "child_name":
            child["name"] = None
            child["errors"] = {"name": "SDK name failed"}
        elif kind == "creatable":
            child["sdk_creatable_now"] = None
            child["errors"] = {"sdk_creatable_now": "SDK current create query failed"}
        elif kind == "database_types":
            child.update(database_type_codes=None, database_types_total_count=None, database_types_truncated=None, catalogue_supported=None)
            child["errors"] = {"database_type_codes": "SDK database type list failed"}
        elif kind == "invalid_child":
            for name in ("name", "short_name", "database_type_codes", "database_types_total_count", "database_types_truncated", "catalogue_supported", "sdk_creatable_now"):
                child[name] = None
            child.update(valid=False, errors={"type": "SDK returned no member type"})
        else:
            child.update(database_type_codes=[1] * 32, database_types_total_count=33, database_types_truncated=True, catalogue_supported=None)
    elif kind in {"unknown_attribute", "metadata_error", "unsupported", "allowed_values", "allowed_ranges", "optionality", "constraint_budget"}:
        field["complete"] = False
        if kind == "unknown_attribute":
            for name in ("supported", "name", "short_name", "data_type", "is_array", "is_pseudo", "is_uda", "unit_kind", "maximum_size"):
                field[name] = None
            field.update(known=False, metadata_complete=False, constraints={"status": "not_attempted", "reason": "unknown_attribute"})
            result.update(attributes_complete=False, constraints_complete=False)
        elif kind == "metadata_error":
            field.update(unit_kind=None, errors={"unit_kind": "SDK unit category failed"}, metadata_complete=False)
            result["attributes_complete"] = False
        elif kind == "unsupported":
            field.update(supported=False, constraints={"status": "not_attempted", "reason": "attribute_not_supported"})
            result["constraints_complete"] = False
        elif kind == "constraint_budget":
            values = field["constraints"]["allowed_values"]
            values.update(values=values["values"] * 100, total_count=101, returned_count=100, truncated=True)
            field["constraints"]["status"] = "truncated"
            result["constraints_complete"] = False
        else:
            key = "values_or_ranges_optional" if kind == "optionality" else kind
            row = field["constraints"][key]
            if key == "values_or_ranges_optional":
                row.update(status="read_error", value=None, error="SDK optionality failed")
            else:
                row.update(status="read_error", defined=None, total_count=None, returned_count=0, truncated=None, error="SDK constraint failed")
                row["values" if key == "allowed_values" else "raw_boundaries"] = []
            field["constraints"]["status"] = "partial"
            result["constraints_complete"] = False
    elif kind == "name":
        result["target"].update(name=None, name_error="SDK name read failed")
    else:
        context = result["context"]
        if kind == "unknown_context":
            context["before"]["current_element"] = None
            context["before"]["errors"] = {"current_element": "SDK CE unknown"}
            context["boundary_agreement"] = None
        elif kind == "same_name_mdb":
            context.update(same_mdb_instance=False, boundary_agreement=False)
        else:
            context["after"]["current_element"].update(type="BOX", identity_key="=1/90|BOX")
            context["boundary_agreement"] = False
    with patch.object(BridgeClient, "_request", return_value=native):
        response = call({"refno": "=1/1", "attributes": ["DESC"], "include_constraints": True})
    assert response["isError"] and result_of(response) == result, response


@pytest.mark.parametrize("kind", ["target_db", "target_ref", "envelope", "session", "build", "typed_ce", "unknown_without_error",
                                  "request", "child_count", "child_order", "child_budget", "catalogue_compatibility", "creatable",
                                  "unknown_db", "metadata", "attribute_target", "direct_edit_policy", "coverage", "permissions",
                                  "units", "optional_required", "values_count", "text_budget", "raw_range", "huge_range", "constraint_status", "invalid_status",
                                  "complete", "truncation"])
def test_invalid_or_misbound_native_evidence_fails_closed(kind):
    native = sample()
    result = native["result"]
    child, field = result["child_types"][0], result["attributes"][0]
    if kind == "target_db":
        result["target"].update(db_type="Design", db_type_code=1)
    elif kind == "target_ref":
        result["target"].update(refno="=1/2", identity_key="=1/2|CATA")
    elif kind == "envelope":
        del native["bridge_build"]
    elif kind in {"session", "build"}:
        result["context"]["after"]["session_id" if kind == "session" else "bridge_build"] = "changed"
    elif kind == "typed_ce":
        result["context"]["after"]["current_element"].update(type="BOX", identity_key="=1/90|BOX")
    elif kind == "unknown_without_error":
        result["context"]["before"]["mdb"] = None
    elif kind == "request":
        result["requested_attributes"] = ["HEIGHT"]
    elif kind == "child_count":
        result["child_types_observation"]["returned_count"] = 3
    elif kind == "child_order":
        child["sdk_index"] = 2
    elif kind == "child_budget":
        child["database_type_codes"] *= 33
    elif kind == "catalogue_compatibility":
        child["catalogue_supported"] = False
    elif kind == "creatable":
        child["sdk_creatable_now"] = None
    elif kind == "unknown_db":
        child["database_type_codes"] = None
    elif kind == "metadata":
        field["unit_kind"] = None
    elif kind in {"attribute_target", "coverage", "permissions", "optional_required"}:
        name, value = {"attribute_target": ("attribute_target", "future_child"), "coverage": ("whole_project_coverage", True),
                       "permissions": ("permissions_verified", True), "optional_required": ("optionality_is_requiredness", True)}[kind]
        result["scope"][name] = value
    elif kind == "direct_edit_policy":
        field["direct_edit_supported"] = True
    elif kind == "units":
        field["constraints"]["allowed_ranges"]["unit_basis"] = "millimetres"
    elif kind == "values_count":
        field["constraints"]["allowed_values"]["returned_count"] = 5
    elif kind == "text_budget":
        field["constraints"]["allowed_values"]["values"][0]["value"] = "😀" * 513
    elif kind in {"raw_range", "huge_range"}:
        field["constraints"]["allowed_ranges"].update(status="defined", defined=True, total_count=1, returned_count=1,
                                                      raw_boundaries=[float("nan") if kind == "raw_range" else 10**400])
    elif kind == "constraint_status":
        field["constraints"]["status"] = "partial"
    elif kind == "invalid_status":
        field["constraints"]["allowed_values"]["status"] = []
    elif kind == "complete":
        result["success"] = False
    else:
        result["child_types_observation"]["truncated"] = True
    with patch.object(BridgeClient, "_request", return_value=native):
        response = call({"refno": "=1/1", "attributes": ["DESC"], "include_constraints": True})
    rejected = result_of(response)
    assert response["isError"] and not rejected["available"] and rejected["target"] is None
    assert rejected["read_errors"][0]["code"] == "invalid_native_response", response


def test_sdk_false_not_defined_and_null_text_do_not_invent_validation_rules():
    native = sample()
    values = native["result"]["attributes"][0]["constraints"]["allowed_values"]
    values.update(status="not_defined", defined=False, values=[], total_count=0, returned_count=0)
    _validate(native, _parameters({"refno": "=1/1", "attributes": ["DESC"], "include_constraints": True}))
    native = sample()
    values = native["result"]["attributes"][0]["constraints"]["allowed_values"]
    values["values"][0].update(value=None, description=None)
    _validate(native, _parameters({"refno": "=1/1", "attributes": ["DESC"], "include_constraints": True}))


def test_mock_other_version_and_absent_feature_return_unknown():
    with patch.object(BridgeClient, "_request") as request:
        for settings in (Settings(mode="mock", version="E3D21"), Settings(mode="bridge", version="E3D31")):
            result = result_of(call({"refno": "/CAT"}, settings))
            assert result["available"] is False and result["target"] is None and result["context"]["before"] is None
    request.assert_not_called()
    with patch.object(BridgeClient, "_request", side_effect=BridgeError("missing", http_status=404)) as request:
        response = call({"refno": "/CAT"})
    request.assert_called_once()
    assert result_of(response)["read_errors"][0]["code"] == "native_feature_unavailable"


@pytest.mark.parametrize("bridge_error", [False, True])
@pytest.mark.parametrize("marker", [{"error_code": "native_database_fault"}, {"native_fault_latched": True},
                                  {"error_code": "native_database_fault", "native_fault_latched": True, "retry_safe": False},
                                  {"native_fault": {"has_fault": True, "code": 538}}, {"native_fault": True}])
def test_fault_reply_or_http_404_diagnostics_propagates_without_followups(bridge_error, marker):
    reply = {"ok": False, **marker}
    kwargs = {"side_effect": BridgeError("native fault", http_status=404, diagnostics=reply)} if bridge_error else {"return_value": reply}
    with patch.object(BridgeClient, "_request", **kwargs) as request:
        with pytest.raises(BridgeError) as caught:
            paragon_catalogue_schema(BridgeClient(SETTINGS), {"refno": "/CAT"})
    request.assert_called_once()
    assert caught.value.diagnostics == reply

"""Offline consumer contract tests; every bridge request is mocked.

Fixtures follow E3d21PipingSpecificationRead.cs and its managed SDK harness.
The test wiring uses mocked transport; no deployed endpoint is accessed.
"""
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
from aveva_e3d_mcp.piping_specification_read import (
    PATH_CANDIDATE, TOOL, PipingSpecificationReadRoute, _parameters,
    piping_specification_read, register_piping_specification_read,
)
from aveva_e3d_mcp.protocol import McpServer


SETTINGS = Settings(mode="bridge", version="E3D21", allow_commands=False)
ROUTE = PipingSpecificationReadRoute(PATH_CANDIDATE, True, True)
ARGS = {"refno": "=1/10", "expected_type": "BRAN", "database_type": "Design"}
DB_TYPES = {"Design": 1, "Catalogue": 2, "Property": 4, "Dictionary": 8}
SESSION = "11111111-1111-4111-8111-111111111111"
BUILD = "22222222-2222-4222-8222-222222222222"


def identity(refno="=1/10", kind="BRAN", database="Design", code=None):
    return {"refno": refno, "type": kind, "identity_key": f"{refno}|{kind}",
            "db_type": database, "db_type_code": DB_TYPES[database] if code is None else code}


def observation(native=None):
    native = identity() if native is None else native
    return {**deepcopy(native), "name": "/NATIVE", "name_error": None, "database": {
        "status": "observed", "name": "NATIVE-DB", "number": 777,
        "type": native["db_type"], "type_code": native["db_type_code"], "errors": {},
    }}


def attribute(name, kind="ELEMENT", value=None, *, unit_kind="NONE"):
    if kind == "ELEMENT" and value is None:
        value = observation(identity("=2/20", "SPEC", "Catalogue"))
    return {"requested_name": name, "status": "observed", "supported": True, "sdk_type": kind,
            "is_array": False, "unit_kind": unit_kind, "unit": None, "unit_status": "unknown",
            "unit_reason": "no_explicit_unit_conversion", "formatted_value": "native display",
            "formatted_status": "observed", "typed_value": value, "typed_status": "observed",
            "reference_null": False if kind == "ELEMENT" else None, "errors": {}}


def sample(attributes=None):
    attributes = {} if attributes is None else attributes
    natives = [identity(), identity(kind="TUBI"), identity("=1/11", "ELBO")]
    rows = []
    for index, native in enumerate(natives):
        rows.append({**observation(native), "id": index, "depth": 0 if index == 0 else 1,
                     "parent_index": None if index == 0 else 0, "sibling_index": None if index == 0 else index,
                     "owner": identity("=1/90", "PIPE") if index == 0 else identity(), "owner_error": None,
                     "attributes": deepcopy(attributes), "complete": True})
    before = {"session_id": SESSION, "bridge_build": BUILD, "project": "PRJ", "mdb": "MDB",
              "current_element": identity(), "errors": {}}
    return {"ok": True, "version": "E3D21", "session_id": SESSION, "bridge_build": BUILD, "result": {
        "operation": "piping_specification_read", "available": True, "success": True, "complete": True,
        "read_only": True, "single_dispatch": True, "atomic_snapshot": False, "requested_refno": "=1/10",
        "expected_type": "BRAN", "database_type": "Design", "database_type_code": 1,
        "attributes": list(attributes), "max_depth": 8, "limit": 64, "rows": rows, "returned_count": 3,
        "order": "database_preorder", "traversal_complete": True, "truncated": False,
        "truncation_reasons": [], "read_errors": [],
        "context": {"before": before, "after": deepcopy(before), "same_project_instance": True,
                    "same_mdb_instance": True, "boundary_agreement": True},
        "scope": {"kind": "explicit_typed_subtree_and_reference_identities", "reference_hops": 1,
                  "reference_targets_expanded": False, "whole_project_coverage": False, "units_verified": False,
                  "engineering_compatible": None, "material_selected": None, "database_access_authorized": None},
        "completeness_scope": "Requested raw/typed observations and bounded ownership traversal only; units and engineering suitability remain unverified.",
    }}


def read(native, args=None, *, route=ROUTE, settings=SETTINGS):
    with patch.object(BridgeClient, "_request", return_value=native) as request:
        reply = piping_specification_read(BridgeClient(settings), ARGS if args is None else args, route=route)
    return reply, request


def incomplete(native, *, row=0):
    native["result"].update(success=False, complete=False)
    native["result"]["rows"][row]["complete"] = False


def field(native, name="PSPEC", row=0):
    return native["result"]["rows"][row]["attributes"][name]


def test_single_explicit_post_preserves_shared_ref_typed_members_and_native_evidence():
    attrs = {name: attribute(name) for name in ("PSPEC", "ISPEC", "TSPEC", "SPREF")}
    native = sample(attrs)
    before = deepcopy(native)
    args = {"refno": " =01/0010 ", "expected_type": " bran ", "database_type": " design ",
            "attributes": [" pspec ", "ispec", "tspec", "spref"]}
    reply, request = read(native, args)
    assert reply is native and native == before
    request.assert_called_once_with("POST", PATH_CANDIDATE, {
        **ARGS, "max_depth": 8, "limit": 64, "attributes": list(attrs),
    }, version="E3D21")
    root, tube, elbow = reply["result"]["rows"]
    assert root["refno"] == tube["refno"] and root["identity_key"] != tube["identity_key"]
    assert elbow["owner"]["identity_key"] == root["identity_key"]
    assert root["database"]["number"] == 777  # Independent of numeric REF's database part.
    assert field(reply)["typed_value"]["db_type"] == "Catalogue"
    assert set(field(reply)["typed_value"]) == {
        "refno", "type", "identity_key", "db_type", "db_type_code", "name", "name_error", "database"}
    assert reply["result"]["complete"] is True
    assert reply["result"]["scope"]["units_verified"] is False
    assert reply["result"]["scope"]["engineering_compatible"] is None


@pytest.mark.parametrize("database,kind", [("Design", "BRAN"), ("Catalogue", "SPCO"),
                                           ("Property", "CMPD"), ("Dictionary", "UDET")])
def test_explicit_support_database_and_reference_targets_have_independent_types(database, kind):
    native = sample({"CATR": attribute("CATR", value=observation(identity("=9/200", "SCOM", "Catalogue")))})
    result = native["result"]
    result.update(expected_type=kind, database_type=database, database_type_code=DB_TYPES[database],
                  rows=result["rows"][:1], returned_count=1)
    result["rows"][0].update(identity(kind=kind, database=database))
    result["rows"][0]["database"].update(type=database, type_code=DB_TYPES[database])
    reply, request = read(native, {**ARGS, "expected_type": kind, "database_type": database, "attributes": ["CATR"]})
    assert reply is native and request.call_count == 1
    assert field(reply, "CATR")["typed_value"]["type"] == "SCOM"


@pytest.mark.parametrize("change", [
    {"refno": None}, {"refno": "/NAME"}, {"refno": "CE"}, {"refno": "=0/1"}, {"refno": "=1/-1"},
    {"refno": "=1/1*"}, {"refno": "=1/1;DELETE"}, {"refno": "=１/1"}, {"refno": "=1/1\x00"},
    {"refno": "=2147483648/1"}, {"refno": "=1/2147483648"}, {"refno": "=" + "0" * 256 + "1/1"},
    {"expected_type": None}, {"expected_type": ""}, {"expected_type": "BRAN;DELETE"},
    {"expected_type": ":UDA"}, {"expected_type": "1BRAN"}, {"expected_type": "A" * 65},
    {"database_type": None}, {"database_type": ""}, {"database_type": "All"}, {"database_type": "Catalog"},
    {"database_type": 1}, {"max_depth": True}, {"max_depth": 0}, {"max_depth": 17},
    {"limit": False}, {"limit": 0}, {"limit": 101}, {"limit": 1.0}, {"attributes": None},
    {"attributes": ["DESC", " desc "]}, {"attributes": ["A"] * 11}, {"attributes": ["DÉSC"]},
    {"attributes": ["DESC WRT"]}, {"attributes": ["A" * 65]}, {"attributes": [1]},
    {"limit": 51, "attributes": [f"A{i}" for i in range(10)]}, {"session_id": SESSION},
    {"version": "E3D21"}, {"db_type": "Design"}, {"command": "Q CE"},
])
def test_invalid_input_never_contacts_bridge(change):
    with patch.object(BridgeClient, "_request") as request, pytest.raises(ValueError):
        piping_specification_read(BridgeClient(SETTINGS), {**ARGS, **change}, route=ROUTE)
    request.assert_not_called()


@pytest.mark.parametrize("args", [None, [], {}, {"refno": "=1/10", "expected_type": "BRAN"},
                                  {"refno": "=1/10", "database_type": "Design"}])
def test_explicit_type_and_database_are_required(args):
    with patch.object(BridgeClient, "_request") as request, pytest.raises(ValueError):
        piping_specification_read(BridgeClient(SETTINGS), args, route=ROUTE)
    request.assert_not_called()


def test_native_int32_pair_and_row_attribute_budget_boundaries():
    payload = _parameters({**ARGS, "refno": "=2147483647/0", "max_depth": 16, "limit": 50,
                           "attributes": [f"A{i}" for i in range(10)]})
    assert payload["refno"] == "=2147483647/0" and len(payload["attributes"]) * payload["limit"] == 500
    assert _parameters({**ARGS, "limit": 100, "attributes": [":UDA", "BORE", "DESC", "PSPEC", "ISPEC"]})["limit"] == 100


@pytest.mark.parametrize("route", [
    None, PipingSpecificationReadRoute(PATH_CANDIDATE), PipingSpecificationReadRoute(PATH_CANDIDATE, True),
    PipingSpecificationReadRoute(PATH_CANDIDATE, False, True), PipingSpecificationReadRoute(PATH_CANDIDATE, 1, True),
    PipingSpecificationReadRoute("http://host/api/v1/piping/specification-read", True, True),
    PipingSpecificationReadRoute("/api/v1/piping/specification-read?fallback=1", True, True),
    PipingSpecificationReadRoute("/api/v1/piping/member-manifest", True, True),
    PipingSpecificationReadRoute("/api/v1/paragon/catalogue-manifest", True, True),
])
def test_unreviewed_route_or_incomplete_transport_stays_unavailable_without_probing(route):
    reply, request = read(sample(), route=route)
    request.assert_not_called()
    result = reply["result"]
    assert result["available"] is False and result["complete"] is False
    assert result["rows"] == [] and result["context"]["before"] is None
    assert result["read_errors"][0]["code"] == "native_route_unverified"
    assert "session_id" not in reply and "bridge_build" not in reply


@pytest.mark.parametrize("settings", [Settings(mode="mock", version="E3D21"), Settings(mode="bridge", version="E3D31")])
def test_wrong_mode_or_version_never_contacts_bridge(settings):
    reply, request = read(sample(), settings=settings)
    request.assert_not_called()
    assert reply["result"]["read_errors"][0]["code"] == "native_unavailable"


def test_integration_uses_only_explicitly_reviewed_path():
    route = PipingSpecificationReadRoute("/api/v1/piping/specification-observations", True, True)
    reply, request = read(sample(), route=route)
    assert reply["result"]["available"] is True
    assert request.call_args.args[:2] == ("POST", route.path) and request.call_count == 1


def test_registration_is_read_only_has_required_numeric_type_db_inputs_and_defaults_unavailable():
    server = McpServer("piping-spec-test", "0")
    register_piping_specification_read(server, BridgeClient(SETTINGS))
    tool, = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})["result"]["tools"]
    assert tool["name"] == TOOL and tool["annotations"]["readOnlyHint"] is True
    schema = tool["inputSchema"]
    assert set(schema["required"]) == {"refno", "expected_type", "database_type"}
    assert schema["properties"]["attributes"]["default"] == [] and schema["additionalProperties"] is False
    with patch.object(BridgeClient, "_request") as request:
        response = server._dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                     "params": {"name": TOOL, "arguments": ARGS}})["result"]
    request.assert_not_called()
    assert response.get("isError") is True  # The shared MCP protocol marks success:false as a tool error.
    assert json.loads(response["content"][0]["text"])["result"]["available"] is False


@pytest.mark.parametrize("kind,value", [("DOUBLE", 0.0), ("DOUBLE", 123.5), ("INTEGER", 0),
                                        ("INTEGER", -2147483648), ("BOOL", False), ("STRING", "")])
def test_raw_scalars_keep_zero_false_and_empty_with_unknown_units(kind, value):
    native = sample({"BORE": attribute("BORE", kind, value, unit_kind="BORE")})
    reply, request = read(native, {**ARGS, "attributes": ["BORE"]})
    assert reply is native and request.call_count == 1
    observed = field(reply, "BORE")
    assert observed["typed_value"] == value and type(observed["typed_value"]) is type(value)
    assert observed["unit_kind"] == "BORE" and observed["unit"] is None and observed["unit_status"] == "unknown"


@pytest.mark.parametrize("failure", ["formatted", "typed", "sdk_type", "is_array", "unit_kind",
                                     "null_reference", "unsupported", "undefined", "array", "complex"])
def test_independent_attribute_failures_and_unknowns_preserve_partial_evidence(failure):
    native = sample({"PSPEC": attribute("PSPEC")})
    value = field(native)
    if failure in {"unsupported", "undefined"}:
        for key in ("sdk_type", "is_array", "unit_kind", "formatted_value", "typed_value", "reference_null"):
            value[key] = None
        value.update(supported=False if failure == "unsupported" else None,
                     formatted_status="unknown", typed_status="unknown")
        value["errors"] = {"value" if failure == "unsupported" else "attribute": "native attribute unavailable"}
    elif failure == "formatted":
        value.update(formatted_status="unknown", formatted_value=None)
        value["errors"]["formatted"] = "formatter failed"
    elif failure == "unit_kind":
        value["unit_kind"] = None
        value["errors"]["unit_kind"] = "unit category getter failed"
    else:
        value.update(typed_status="unknown", typed_value=None, reference_null=None)
        value["errors"]["typed"] = "native typed value unknown"
        if failure in {"sdk_type", "is_array"}:
            value[failure] = None
            value["errors"][failure] = "metadata getter failed"
        elif failure == "null_reference":
            value["reference_null"] = True
            value["formatted_value"] = "Unset"
        elif failure == "array":
            value.update(sdk_type="DOUBLE", is_array=True, formatted_value="1 2 3")
        elif failure == "complex":
            value.update(sdk_type="POSITION", formatted_value="native position")
    value["status"] = "unknown"
    incomplete(native)
    reply, _ = read(native, {**ARGS, "attributes": ["PSPEC"]})
    assert reply is native and field(reply) is value
    assert reply["result"]["available"] is True and reply["result"]["complete"] is False
    if failure == "formatted":
        assert value["typed_status"] == "observed" and value["typed_value"]["refno"] == "=2/20"
    if failure == "unit_kind":
        assert value["typed_status"] == value["formatted_status"] == "observed"


@pytest.mark.parametrize("failure", ["name", "database", "database_name", "database_number", "database_type",
                                     "database_type_mismatch", "database_type_unknown"])
@pytest.mark.parametrize("target", ["row", "reference"])
def test_name_and_database_field_errors_retain_independent_identity_and_metadata(failure, target):
    native = sample({"PSPEC": attribute("PSPEC")})
    value = field(native)
    observation_value = native["result"]["rows"][0] if target == "row" else value["typed_value"]
    if failure == "name":
        observation_value.update(name=None, name_error="native name getter failed")
    else:
        database = observation_value["database"]
        database["status"] = "unknown"
        if failure == "database":
            database.update(name=None, number=None, type=None, type_code=None, errors={"database": "database getter failed"})
        elif failure in {"database_type_mismatch", "database_type_unknown"}:
            database.update(type="Dictionary" if failure.endswith("mismatch") else "Unknown",
                            type_code=8 if failure.endswith("mismatch") else 0, errors={"type": "native type readings disagree or unknown"})
            if failure.endswith("unknown") and target == "reference":
                observation_value.update(db_type="Unknown", db_type_code=0)
        elif failure == "database_type":
            database.update(type=None, type_code=None, errors={"type": "type getter failed"})
        else:
            key = failure.removeprefix("database_")
            database[key] = None
            database["errors"][key] = "native metadata getter failed"
    if target == "reference":
        value["status"] = "unknown"
        value["errors"]["reference_metadata"] = "reference identity retained; metadata unknown"
    incomplete(native)
    reply, _ = read(native, {**ARGS, "attributes": ["PSPEC"]})
    assert reply is native
    assert observation_value["identity_key"] == ("=1/10|BRAN" if target == "row" else "=2/20|SPEC")
    if target == "reference":
        assert value["typed_status"] == "observed" and value["formatted_status"] == "observed"


def test_external_root_owner_error_is_retained():
    native = sample()
    native["result"]["rows"][0].update(owner=None, owner_error="native_null_owner")
    incomplete(native)
    reply, _ = read(native)
    assert reply is native and reply["result"]["rows"][0]["owner_error"] == "native_null_owner"


@pytest.mark.parametrize("field_name", ["project", "mdb", "current_element"])
@pytest.mark.parametrize("boundary", ["before", "after"])
def test_unknown_context_stays_unknown_without_synthesized_identity(field_name, boundary):
    native = sample()
    result = native["result"]
    sample_value = result["context"][boundary]
    sample_value[field_name] = None
    sample_value["errors"][field_name] = "native getter failed"
    result["context"]["boundary_agreement"] = None
    result.update(success=False, complete=False)
    reply, _ = read(native)
    assert reply is native and reply["result"]["context"]["boundary_agreement"] is None


@pytest.mark.parametrize("drift", ["project", "mdb", "ce_type", "ce_database", "project_instance", "mdb_instance", "unknown_instance"])
def test_known_context_drift_is_retained_as_incomplete_observation(drift):
    native = sample()
    result = native["result"]
    context = result["context"]
    if drift in {"project", "mdb"}:
        context["after"][drift] = "CHANGED"
    elif drift == "ce_type":
        context["after"]["current_element"] = identity(kind="TUBI")  # Same REF, different native type.
    elif drift == "ce_database":
        context["after"]["current_element"] = identity(database="Property")
    elif drift == "unknown_instance":
        context["same_mdb_instance"] = None
    else:
        context["same_" + drift] = False
    context["boundary_agreement"] = None if drift == "unknown_instance" else False
    result.update(success=False, complete=False)
    reply, _ = read(native)
    assert reply is native and reply["result"]["complete"] is False


@pytest.mark.parametrize("reasons", [["limit"], ["depth"], ["depth", "limit"]])
def test_truncation_is_a_preorder_prefix_and_never_complete(reasons):
    native = sample()
    result = native["result"]
    args = dict(ARGS)
    if "limit" in reasons:
        args["limit"] = result["limit"] = 3
    if "depth" in reasons:
        args["max_depth"] = result["max_depth"] = 1
    result.update(success=False, complete=False, traversal_complete=False, truncated=True, truncation_reasons=reasons)
    reply, _ = read(native, args)
    assert reply is native and len(reply["result"]["rows"]) == 3


def test_member_error_preserves_prefix_and_failed_next_ordinal():
    native = sample()
    result = native["result"]
    result.update(success=False, complete=False, traversal_complete=False, read_errors=[{
        "stage": "member", "parent_identity_key": "=1/10|BRAN", "sibling_index": 3, "message": "native Member(3) failed",
    }])
    reply, _ = read(native)
    assert reply is native and reply["result"]["read_errors"] == result["read_errors"]


@pytest.mark.parametrize("missing", ["session_id", "bridge_build", "version", "result"])
def test_top_envelope_is_required_and_never_reconstructed_from_native_boundaries(missing):
    native = sample()
    del native[missing]
    reply, request = read(native)
    assert request.call_count == 1 and reply["result"]["available"] is False
    assert reply["result"]["read_errors"][0]["code"] == "invalid_native_response"
    assert "session_id" not in reply and "bridge_build" not in reply


def test_bare_native_result_is_unavailable():
    reply, request = read(sample()["result"])
    assert request.call_count == 1 and reply["result"]["available"] is False


@pytest.mark.parametrize("field_name", ["session_id", "bridge_build"])
@pytest.mark.parametrize("boundary", ["before", "after"])
def test_every_boundary_must_bind_to_top_session_and_build(field_name, boundary):
    native = sample()
    native["result"]["context"][boundary][field_name] = "33333333-3333-4333-8333-333333333333"
    native["result"].update(success=False, complete=False)
    native["result"]["context"]["boundary_agreement"] = False
    reply, request = read(native)
    assert request.call_count == 1 and reply["result"]["available"] is False


@pytest.mark.parametrize("kind,item", [("DOUBLE", float("nan")), ("DOUBLE", float("inf")),
                                      ("DOUBLE", True), ("DOUBLE", 10**400), ("INTEGER", True),
                                      ("INTEGER", 2147483648), ("BOOL", 0), ("STRING", None),
                                      ("STRING", "x" * 4097), ("POSITION", [0, 0, 0])])
def test_invalid_or_unexpanded_typed_scalar_claim_is_unavailable(kind, item):
    native = sample({"VALUE": attribute("VALUE", kind, item)})
    reply, _ = read(native, {**ARGS, "attributes": ["VALUE"]})
    assert reply["result"]["available"] is False


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(expected_type="TUBI"),
    lambda r: r.update(database_type_code=True),
    lambda r: r["rows"][0].update(type="TUBI", identity_key="=1/10|TUBI"),
    lambda r: r["rows"][0].update(refno="=01/10", identity_key="=01/10|BRAN"),
    lambda r: r["rows"][1].update(id=True),
    lambda r: r["rows"][1].update(depth=3),
    lambda r: r["rows"][1].update(parent_index=1),
    lambda r: r["rows"][1].update(sibling_index=2),
    lambda r: r["rows"][1].update(owner=identity(kind="TUBI")),
    lambda r: r["rows"][1].update(owner_error="owner failed"),
    lambda r: r["rows"][1].update(type="BRAN", identity_key="=1/10|BRAN"),
    lambda r: r.update(returned_count=True),
    lambda r: r.update(returned_count=2),
    lambda r: r.update(truncated=True),
    lambda r: r.update(complete=False, success=False),
    lambda r: r["context"].update(boundary_agreement=False),
    lambda r: r["context"].update(same_project_instance=1),
    lambda r: r["context"]["after"].update(current_element=None),
    lambda r: r["context"]["after"]["current_element"].pop("type"),
    lambda r: r["context"]["after"].update(project=None),
    lambda r: r["context"]["after"]["errors"].update(mdb="unbound error"),
    lambda r: r["scope"].update(units_verified=True),
    lambda r: r["scope"].update(reference_hops=True),
    lambda r: r["scope"].update(reference_targets_expanded=True),
    lambda r: r["scope"].update(engineering_compatible=False),
    lambda r: r["scope"].update(material_selected=True),
    lambda r: r["scope"].update(database_access_authorized=True),
    lambda r: r.update(read_only=False),
    lambda r: r.update(single_dispatch=False),
    lambda r: r.update(atomic_snapshot=True),
    lambda r: r.update(operation="paragon_catalogue_manifest"),
    lambda r: r.update(read_errors=[{"stage": "member", "parent_identity_key": "=1/90|PIPE", "sibling_index": 1, "message": "failed"}]),
])
def test_forged_identity_hierarchy_context_scope_or_completeness_is_unavailable(mutation):
    native = sample()
    mutation(native["result"])
    reply, request = read(native)
    assert request.call_count == 1 and reply["result"]["available"] is False
    assert reply["result"]["read_errors"][0]["code"] == "invalid_native_response"


@pytest.mark.parametrize("mutation", [
    lambda v: v.update(unit="mm"),
    lambda v: v.update(unit_status="observed"),
    lambda v: v.update(sdk_type="BOOL"),
    lambda v: v.update(is_array=True),
    lambda v: v.update(reference_null=True),
    lambda v: v.update(status="unknown"),
    lambda v: v.update(formatted_status="unknown"),
    lambda v: v.update(typed_status="unknown"),
    lambda v: v.update(requested_name="ISPEC"),
    lambda v: v["typed_value"].update(attributes={"CATR": "expanded"}),
    lambda v: v["typed_value"].update(refno="/SPEC"),
    lambda v: v["typed_value"]["database"].update(number=True),
    lambda v: v["typed_value"]["database"].update(type_code=8),
    lambda v: v["typed_value"]["database"].update(status="unknown"),
    lambda v: v["errors"].update(reference_metadata="unbound metadata failure"),
    lambda v: v["errors"].update(sdk_type="unbound type failure"),
])
def test_contradictory_attribute_and_reference_claims_are_unavailable(mutation):
    native = sample({"PSPEC": attribute("PSPEC")})
    mutation(field(native))
    reply, _ = read(native, {**ARGS, "attributes": ["PSPEC"]})
    assert reply["result"]["available"] is False


FAULT_MARKERS = [
    {"has_fault": True}, {"native_fault": True}, {"native_database_fault": True},
    {"native_fault_latched": True}, {"error_code": "native_fault"}, {"error_code": "native_database_fault"},
    {"native_database_fault": {"has_fault": True, "code": 538}, "retry_safe": False},
]


@pytest.mark.parametrize("marker", FAULT_MARKERS)
def test_recursive_native_fault_in_reply_takes_precedence_over_envelope_validation_and_is_preserved(marker):
    native = {"result": {"arbitrary": [{"nested": deepcopy(marker)}]}}
    before = deepcopy(native)
    with patch.object(BridgeClient, "_request", return_value=native) as request, pytest.raises(BridgeError) as caught:
        piping_specification_read(BridgeClient(SETTINGS), ARGS, route=ROUTE)
    assert caught.value.diagnostics is native and native == before
    assert request.call_count == 1


@pytest.mark.parametrize("marker", FAULT_MARKERS)
@pytest.mark.parametrize("http_status", [404, 500, None])
def test_recursive_transport_native_fault_is_reraised_without_retry_or_followups(marker, http_status):
    diagnostics = {"state": {"nested": [{"receipt": deepcopy(marker)}]}}
    failure = BridgeError("native read stopped", http_status=http_status, diagnostics=diagnostics)
    with patch.object(BridgeClient, "_request", side_effect=failure) as request, pytest.raises(BridgeError) as caught:
        piping_specification_read(BridgeClient(SETTINGS), ARGS, route=ROUTE)
    assert caught.value is failure and caught.value.diagnostics is diagnostics
    assert request.call_count == 1


def test_explicit_false_fault_markers_and_unrelated_error_code_are_not_faults():
    native = sample()
    native["diagnostics"] = {"native_fault": False, "native_database_fault": False,
                             "has_fault": False, "native_fault_latched": False,
                             "error_code": ["not a code"], "retry_safe": False}
    reply, request = read(native)
    assert reply is native and request.call_count == 1


def test_reviewed_but_missing_native_feature_is_unavailable_with_one_request():
    with patch.object(BridgeClient, "_request", side_effect=BridgeError("not found", http_status=404)) as request:
        reply = piping_specification_read(BridgeClient(SETTINGS), ARGS, route=ROUTE)
    assert request.call_count == 1 and reply["result"]["available"] is False
    assert reply["result"]["read_errors"][0]["code"] == "native_feature_unavailable"


@pytest.mark.parametrize("failure", [BridgeError("busy", http_status=409), BridgeError("failed", http_status=500),
                                     BridgeError("timeout", transport_error=True), BridgeError("invalid JSON", response_invalid=True)])
def test_other_bridge_errors_propagate_unchanged_without_retry(failure):
    with patch.object(BridgeClient, "_request", side_effect=failure) as request, pytest.raises(BridgeError) as caught:
        piping_specification_read(BridgeClient(SETTINGS), ARGS, route=ROUTE)
    assert caught.value is failure and request.call_count == 1

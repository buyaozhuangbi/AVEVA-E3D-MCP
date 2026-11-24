"""Literal creation requests and native evidence, with no E3D process."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import math
import struct
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.paragon_component_create import (
    OPERATION, PATH, STATUS_PATH, STATUS_TOOL, TOOL, _parameters, _status_parameters,
    _validate, _validate_status, _SCOPE, _PROGRESS_FIELDS, _SUMMARY_FIELDS, _ATTEMPT_FIELDS, _FACT_FIELDS,
    _after,
    paragon_component_create, paragon_component_create_status, register_paragon_component_create,
)
from aveva_e3d_mcp.protocol import McpServer
from test_paragon_component_copy import snapshot as copy_snapshot
from test_paragon_generation_plan_evidence import template_attribute, template_identity, template_observation


SESSION = "11111111-1111-4111-8111-111111111111"
BUILD = "a2222222-2222-4222-8222-222222222222"
REQUEST_ID = "a3333333-3333-4333-8333-333333333333"
REVISION = "a" * 64
PARENT, POINT, GEOMETRY, NEW = "=7/100", "=7/200", "=7/300", "=7/400"
ARGS = {"parent_refno": PARENT, "point_refno": POINT, "geometry_refno": GEOMETRY,
        "name": "/NEW_SCOM", "parameters": [1.0, -0.0, 3.5]}
EXECUTE = {**ARGS, "dry_run": False, "request_id": REQUEST_ID, "expected_session_id": SESSION,
           "expected_bridge_build": BUILD, "expected_revision": REVISION}
STATUS_ARGS = {"request_id": REQUEST_ID, "expected_session_id": SESSION}


def wire(args=ARGS):
    return {**deepcopy(args), "parameters": [float(number) for number in args["parameters"]],
            "parameter_bits": [struct.pack(">d", float(number)).hex() for number in args["parameters"]],
            "dry_run": args.get("dry_run", True)}


def client(*, commands=True, mode="bridge", version="E3D21"):
    return BridgeClient(Settings(mode=mode, version=version, allow_commands=commands))


def registered(bridge=None):
    server = McpServer("offline-paragon-component-create", "0")
    register_paragon_component_create(server, client() if bridge is None else bridge)
    return server


def call(server, arguments, tool=TOOL):
    return server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": tool, "arguments": arguments}})["result"]


def core(reference=PARENT, kind="CATE"):
    return {"refno": reference, "type": kind, "db_type": "Catalogue", "db_type_code": 2,
            "identity_key": f"{reference}|{kind}"}


def envelope(result):
    return {"ok": True, "version": "E3D21", "session_id": SESSION, "bridge_build": BUILD, "result": result}


def context():
    return {"session_id": SESSION, "bridge_build": BUILD, "project": "PLANT", "mdb": "CATALOGUE-MDB",
            "current_element": core(), "instance_binding": "b" * 64}


def set_observation(reference=POINT, kind="PTSE", count=1):
    return {"target": template_identity(reference, kind, "/" + kind), "members": [
        {"identity": core(f"=7/{500 + index + (100 if kind == 'GMSE' else 0)}", "SCYL" if kind == "GMSE" else "PTAX"),
         "owner": core(reference, kind)} for index in range(count)], "members_complete": True}


def leaf():
    return {"source": "DbElement.Member(1)", "status": "observed", "is_leaf": True,
            "first_member": None, "complete": True, "errors": {}}


def new_template():
    result = template_observation()["result"]
    result.update(session_id=SESSION, bridge_build=BUILD, requested_refno=NEW,
                  target=template_identity(NEW, "SCOM", ARGS["name"], requested=NEW), leaf=leaf(),
                  parameters=template_attribute(values=ARGS["parameters"]))
    for side in ("before", "after"):
        result["context"][side] = {key: deepcopy(value) for key, value in context().items() if key != "instance_binding"}
        result["context"][side]["errors"] = {}
    for name, reference, kind in (("PTREF", POINT, "PTSE"), ("GMREF", GEOMETRY, "GMSE")):
        attribute = template_attribute(name, [{"index": 0, "status": "observed", "reference_null": False,
            "error": None, "identity": template_identity(reference, kind, "/" + kind)}])
        attribute.update(is_array=False, shape="element", reference_null=False, native_array_materialized=False)
        result["references"][name] = attribute
    return result


def before():
    return {"module": {"name": "PARAGON", "number": 81}, "context": context(),
            "parent": template_identity(PARENT, "CATE", "/CATEGORY"), "point_set": set_observation(),
            "geometry_set": set_observation(GEOMETRY, "GMSE"), "members": [], "members_complete": True,
            "name_lookup": {"status": "absent", "target": None}, "creatable": True}


def prepared():
    progress = {key: None for key in _PROGRESS_FIELDS}
    progress.update({key: False for key in set(_ATTEMPT_FIELDS) | {
        "readback_complete", "outcome_uncertain", "native_fault_latched", "retry_safe"}})
    return envelope({"operation": OPERATION, "dry_run": True,
        "request": {key: value for key, value in wire().items() if key != "dry_run"},
        "session_id": SESSION, "bridge_build": BUILD, "request_id": None, "status": "prepared", "success": True,
        "complete": True, "scoped_revision": REVISION, "before": before(), "after": None, "progress": progress,
        "errors": [], "journal": None, "scope": deepcopy(_SCOPE), "this_submission_executed": False, "request_replayed": False})


def created(*, journal=False):
    reply = prepared()
    result = reply["result"]
    result.update(dry_run=False, request_id=REQUEST_ID, status="created", this_submission_executed=True)
    result["progress"].update({key: True for key in set(_ATTEMPT_FIELDS) | _FACT_FIELDS | {"readback_complete"}})
    result["progress"]["known_new_refno"] = NEW
    result["after"] = {"module": {"name": "PARAGON", "number": 81}, "context": context(),
        "parent": template_identity(PARENT, "CATE", "/CATEGORY"), "point_set": set_observation(),
        "geometry_set": set_observation(GEOMETRY, "GMSE"), "members": [
            {**core(NEW, "SCOM"), "name": ARGS["name"], "name_error": None}], "members_complete": True,
        "new_identity": template_identity(NEW, "SCOM", ARGS["name"]),
        "new_owner": template_identity(PARENT, "CATE", "/CATEGORY"), "new_leaf": leaf(),
        "new_template": new_template(), "parameter_bits": wire()["parameter_bits"]}
    if journal:
        result["journal"] = snapshot("completed", receipt=deepcopy(reply))
    return reply


def snapshot(status="unknown_or_expired", *, receipt=None):
    row = copy_snapshot(status)
    row["operation"] = OPERATION
    row["critical_summary"] = {key: None for key in _SUMMARY_FIELDS}
    row["critical_summary"]["retry_safe"] = False
    if receipt is not None:
        row.update(receipt_available=True, receipt_omitted_reason=None, receipt=receipt)
        row["critical_summary"].update({key: receipt["result"]["progress"][key] for key in _SUMMARY_FIELDS - {"error_code"}})
    return row


def blocked():
    reply = prepared()
    reply["result"].update(status="blocked", success=False, complete=False, scoped_revision=None,
                           errors=[{"stage": "preflight", "code": "observation_unknown", "message": "Scoped observation is incomplete."}])
    return reply


def at(value, path):
    for key in path:
        value = value[key]
    return value


@pytest.mark.parametrize("args", [ARGS, {**ARGS, "dry_run": True}, EXECUTE])
def test_defaults_and_python_derived_binary64_bits(args):
    payload = _parameters(args)
    assert payload == wire(args)
    assert payload["parameter_bits"] == ["3ff0000000000000", "8000000000000000", "400c000000000000"]
    assert all(type(number) is float for number in payload["parameters"])
    assert math.copysign(1, payload["parameters"][1]) == -1
    assert payload["parameters"] is not args["parameters"]


@pytest.mark.parametrize("field", ["parent_refno", "point_refno", "geometry_refno"])
@pytest.mark.parametrize("bad", [None, True, 1, "CE", "/NAME", "=0/1", "=01/1", "=1/01", "=1/-1", "=1/+1",
                                 " =1/1", "=1/1 ", "=1/1\n", "=1/１", "=2147483648/0", "=1/2147483648"])
def test_explicit_reference_contract(field, bad):
    with pytest.raises(ValueError):
        _parameters({**ARGS, field: bad})


@pytest.mark.parametrize("first,last", [("parent_refno", "point_refno"), ("parent_refno", "geometry_refno"),
                                      ("point_refno", "geometry_refno")])
def test_three_distinct_native_identities_are_required(first, last):
    with pytest.raises(ValueError, match="distinct"):
        _parameters({**ARGS, first: ARGS[last]})


@pytest.mark.parametrize("name", ["/A", "/Name_9-X", "/A/B2/C_D-E", "/" + "A" * 49])
def test_literal_names(name):
    assert _parameters({**ARGS, "name": name})["name"] == name


@pytest.mark.parametrize("name", [None, True, "A", "/", "/A/", "/A//B", "/1A", "/A/1B", "/A/_B", "/_A",
                                  "/A B", "/A;NEW", "/A\n", " /A", "/A ", "/Ａ", "/A'", "/A|", "/" + "A" * 50])
def test_name_cannot_inject_or_exceed_native_budget(name):
    with pytest.raises(ValueError):
        _parameters({**ARGS, "name": name})


@pytest.mark.parametrize("value", [None, True, "1", {}, [], float("nan"), float("inf"), -float("inf"), 10**1000])
def test_param_values_are_finite_double_numbers(value):
    with pytest.raises(ValueError):
        _parameters({**ARGS, "parameters": [value]})


@pytest.mark.parametrize("values", [None, (), "1", [], [0] * 129])
def test_nonempty_param_array_bounds(values):
    with pytest.raises(ValueError):
        _parameters({**ARGS, "parameters": values})


def test_param_array_boundary_is_preserved_in_order():
    values = [float(index) for index in range(128)]
    assert _parameters({**ARGS, "parameters": values})["parameters"] == values


@pytest.mark.parametrize("field", ["parent_refno", "point_refno", "geometry_refno", "name", "parameters"])
def test_every_creation_intent_field_is_required(field):
    args = deepcopy(ARGS)
    del args[field]
    with pytest.raises(ValueError):
        _parameters(args)


@pytest.mark.parametrize("field,value", [("parameter_bits", ["0" * 16] * 3), ("source_refno", "=7/101"),
    ("template_refno", "=7/101"), ("ptref", POINT), ("gmref", GEOMETRY), ("pml", "NEW SCOM"),
    ("recipe", "copy"), ("geometry", {}), ("points", []), ("version", "E3D21"), ("capture_after", True)])
def test_no_pml_template_or_caller_bit_override(field, value):
    with pytest.raises(ValueError):
        _parameters({**ARGS, field: value})


@pytest.mark.parametrize("field", ["request_id", "expected_session_id", "expected_bridge_build", "expected_revision"])
@pytest.mark.parametrize("value", [None, "present"])
def test_preview_forbids_execution_tokens_even_null(field, value):
    with pytest.raises(ValueError):
        _parameters({**ARGS, field: value})


@pytest.mark.parametrize("field", ["request_id", "expected_session_id", "expected_bridge_build", "expected_revision"])
def test_execution_requires_all_tokens(field):
    args = deepcopy(EXECUTE)
    del args[field]
    with pytest.raises(ValueError):
        _parameters(args)


@pytest.mark.parametrize("field,bad", [("request_id", REQUEST_ID.upper()), ("request_id", None),
    ("expected_bridge_build", BUILD.upper()), ("expected_bridge_build", "{" + BUILD + "}"),
    ("expected_session_id", ""), ("expected_session_id", "x\n"), ("expected_session_id", "x" * 257),
    ("expected_session_id", "😀" * 129), ("expected_session_id", "\ud800"),
    ("expected_revision", "A" * 64), ("expected_revision", "a" * 63), ("expected_revision", "a" * 64 + "\n"),
    ("dry_run", None), ("dry_run", 0), ("dry_run", "false")])
def test_execution_token_shape_and_exact_identity(field, bad):
    with pytest.raises(ValueError):
        _parameters({**EXECUTE, field: bad})


def test_utf16_session_boundary_preserves_exact_string():
    session = "😀" * 128
    assert _parameters({**EXECUTE, "expected_session_id": session})["expected_session_id"] == session


@pytest.mark.parametrize("args", [{}, {"request_id": REQUEST_ID}, {"expected_session_id": SESSION},
    {**STATUS_ARGS, "dry_run": True}, {**STATUS_ARGS, "parent_refno": PARENT}, {**STATUS_ARGS, "version": "E3D21"}])
def test_managed_status_is_closed(args):
    with pytest.raises(ValueError):
        _status_parameters(args)


def test_permission_checked_before_any_io():
    bridge = client(commands=False)
    with patch.object(bridge, "_request") as request, pytest.raises(ValueError, match="ALLOW_COMMANDS"):
        paragon_component_create(bridge, EXECUTE)
    request.assert_not_called()


@pytest.mark.parametrize("mode,version", [("mock", "E3D21"), ("bridge", "E3D31")])
def test_unsupported_runtime_has_no_mock_creation(mode, version):
    bridge = client(mode=mode, version=version)
    with patch.object(bridge, "_request") as request:
        with pytest.raises(BridgeError, match="E3D21 bridge"):
            paragon_component_create(bridge, ARGS)
        with pytest.raises(BridgeError, match="E3D21 bridge"):
            paragon_component_create_status(bridge, STATUS_ARGS)
    request.assert_not_called()


def test_registration_does_not_probe_or_execute():
    with patch("urllib.request.urlopen", side_effect=AssertionError("No catalog I/O")) as transport:
        server = registered()
    transport.assert_not_called()
    assert set(server._tools) == {TOOL, STATUS_TOOL}
    assert server._tools[TOOL][0]["annotations"]["readOnlyHint"] is False
    assert server._tools[TOOL][0]["annotations"]["idempotentHint"] is False
    assert server._tools[STATUS_TOOL][0]["annotations"]["readOnlyHint"] is True
    for definition, _ in server._tools.values():
        assert definition["inputSchema"]["additionalProperties"] is False


@pytest.mark.parametrize("field,value", [("parameter_bits", []), ("parameters", [True]), ("parameters", []),
    ("point_refno", "=01/1"), ("geometry_refno", "=1/1\n"), ("source_refno", NEW),
    ("name", "/A;NEW"), ("dry_run", 0), ("request_id", None)])
def test_public_schema_and_handler_reject_before_transport(field, value):
    server = registered()
    with patch("urllib.request.urlopen", side_effect=AssertionError("No invalid input I/O")) as transport:
        result = call(server, {**ARGS, field: value})
    assert result["isError"] is True
    transport.assert_not_called()


@pytest.mark.parametrize("factory,args", [(prepared, ARGS), (created, EXECUTE), (blocked, ARGS)])
def test_independent_native_fixtures_pass_without_being_modified(factory, args):
    reply = factory()
    original = deepcopy(reply)
    _validate(reply, wire(args))
    assert reply == original
    bridge = client()
    with patch.object(bridge, "_request", return_value=reply) as request:
        assert paragon_component_create(bridge, args) is reply
    request.assert_called_once_with("POST", PATH, wire(args), version="E3D21")


@pytest.mark.parametrize("factory,args", [(prepared, ARGS), (created, EXECUTE)])
@pytest.mark.parametrize("path,field,value", [
    ((), "version", "E3D31"), ((), "ok", 1), ((), "session_id", "other"), ((), "bridge_build", BUILD.upper()),
    (("result",), "operation", "paragon_component_copy"), (("result",), "dry_run", None),
    (("result",), "session_id", "other"), (("result",), "bridge_build", "b" * 36),
    (("result",), "success", 1), (("result",), "complete", 1), (("result",), "request_replayed", True),
    (("result",), "scoped_revision", "A" * 64),
    (("result", "request"), "point_refno", GEOMETRY), (("result", "request"), "geometry_refno", POINT),
    (("result", "request"), "parent_refno", NEW), (("result", "request"), "name", "/OTHER"),
    (("result", "request"), "parameters", [True, 0.0, 3.5]),
    (("result", "request"), "parameter_bits", ["3ff0000000000000", "0000000000000000", "400c000000000000"]),
    (("result", "before", "module"), "number", 80),
    (("result", "before", "context"), "project", None),
    (("result", "before", "context"), "mdb", None),
    (("result", "before", "context"), "session_id", "other"),
    (("result", "before", "context"), "bridge_build", "other"),
    (("result", "before", "context"), "instance_binding", None),
    (("result", "before"), "members_complete", False), (("result", "before"), "creatable", False),
    (("result", "before", "name_lookup"), "status", "unknown"),
    (("result", "scope"), "geometry_evaluated", True), (("result", "scope"), "engineering_validated", True),
    (("result", "scope"), "set_membership_only", False),
])
def test_positive_receipts_require_bound_input_context_and_native_preconditions(factory, args, path, field, value):
    reply = factory()
    at(reply, path)[field] = value
    with pytest.raises(ValueError):
        _validate(reply, wire(args))


@pytest.mark.parametrize("set_name,kind", [("point_set", "PTSE"), ("geometry_set", "GMSE")])
@pytest.mark.parametrize("path,field,value", [
    ((), "members", []), ((), "members_complete", False), ((), "target", None),
    (("target",), "type", "SCOM"), (("target",), "db_type_code", 1),
    (("target", "actual_type"), "short_name", "UDET:SET"),
    (("target", "base_type"), "short_name", "UDET:SET"),
    (("members", 0), "owner", None),
    (("members", 0, "owner"), "refno", PARENT),
    (("members", 0, "identity"), "db_type_code", 1),
])
def test_prepared_requires_exact_actual_sets_and_fully_bound_direct_members(set_name, kind, path, field, value):
    reply = prepared()
    at(reply["result"]["before"][set_name], path)[field] = value
    with pytest.raises(ValueError):
        _validate(reply, wire())


@pytest.mark.parametrize("set_name,reference,kind", [("point_set", POINT, "PTSE"), ("geometry_set", GEOMETRY, "GMSE")])
def test_direct_membership_boundary_and_unique_ref_proof(set_name, reference, kind):
    reply = prepared()
    reply["result"]["before"][set_name] = set_observation(reference, kind, 64)
    _validate(reply, wire())
    reply["result"]["before"][set_name]["members"].append(deepcopy(reply["result"]["before"][set_name]["members"][0]))
    with pytest.raises(ValueError):
        _validate(reply, wire())
    reply["result"]["before"][set_name] = set_observation(reference, kind, 2)
    members = reply["result"]["before"][set_name]["members"]
    members[1] = deepcopy(members[0])
    with pytest.raises(ValueError):
        _validate(reply, wire())


@pytest.mark.parametrize("set_name", ["point_set", "geometry_set"])
def test_ordinary_owner_read_failure_is_preserved_as_blocked_not_as_empty_members(set_name):
    reply = blocked()
    reply["result"]["before"][set_name]["members"][0]["owner"] = None
    reply["result"]["before"][set_name]["members_complete"] = False
    original = deepcopy(reply)
    _validate(reply, wire())
    assert reply == original


@pytest.mark.parametrize("set_name", ["point_set", "geometry_set"])
def test_set_name_read_failure_does_not_erase_observed_type_and_membership(set_name):
    reply = prepared()
    target = reply["result"]["before"][set_name]["target"]
    target.update(name=None, name_error="NAME getter failed", errors={"name": "NAME getter failed"}, complete=False)
    _validate(reply, wire())


@pytest.mark.parametrize("path,field,value", [
    (("result",), "status", "executed"), (("result",), "request_id", None),
    (("result",), "this_submission_executed", False),
    (("result", "after"), "new_identity", None), (("result", "after"), "new_owner", None),
    (("result", "after"), "new_leaf", None), (("result", "after"), "new_template", None),
    (("result", "after"), "members_complete", False),
    (("result", "after", "new_identity"), "name", "/OTHER"),
    (("result", "after", "new_identity", "actual_type"), "short_name", "UDET:SCOM"),
    (("result", "after", "new_leaf"), "is_leaf", False),
    (("result", "after", "context"), "project", "OTHER"),
    (("result", "after", "context"), "mdb", "OTHER"),
    (("result", "after", "context"), "instance_binding", "c" * 64),
    (("result", "after"), "parameter_bits", ["0" * 16] * 3),
    (("result", "after", "point_set", "members", 0, "owner"), "refno", GEOMETRY),
    (("result", "after", "geometry_set"), "members", []),
    (("result", "progress"), "known_new_refno", POINT),
    *[(("result", "progress"), field, False) for field in set(_ATTEMPT_FIELDS) | _FACT_FIELDS | {"readback_complete"}],
    (("result", "progress"), "outcome_uncertain", True),
    (("result", "progress"), "native_fault_latched", True),
    (("result", "progress"), "retry_safe", True),
])
def test_created_requires_raw_creation_and_readback_proof(path, field, value):
    reply = created()
    at(reply, path)[field] = value
    with pytest.raises(ValueError):
        _validate(reply, wire(EXECUTE))


@pytest.mark.parametrize("name,reference", [("PTREF", POINT), ("GMREF", GEOMETRY)])
@pytest.mark.parametrize("mutation", ["different_ref", "array", "actual_type", "native_null", "unknown_value"])
def test_created_requires_scalar_typed_requested_ptref_and_gmref(name, reference, mutation):
    reply = created()
    raw = reply["result"]["after"]["new_template"]
    row = raw["references"][name]
    if mutation == "different_ref":
        target = row["values"][0]["identity"]
        target.update(refno="=7/999", identity_key="=7/999|" + target["type"])
    elif mutation == "array":
        row.update(is_array=True, shape="element_array", reference_null=None, native_array_materialized=True)
    elif mutation == "actual_type":
        row["values"][0]["identity"]["actual_type"]["short_name"] = "UDET:SET"
    elif mutation == "native_null":
        row.update(reference_null=True)
        row["values"] = [{"index": 0, "identity": None, "status": "unset", "reference_null": True, "error": None}]
    else:
        row.update(total_count=None, returned_count=0, values=[], reference_null=None, native_array_materialized=False,
                   truncated=False, inspection_limited=None, status="unknown", errors={"value": "Getter failed"}, complete=False)
        raw.update(success=False, complete=False)
    with pytest.raises(ValueError):
        _validate(reply, wire(EXECUTE))


def test_optional_reference_read_failure_does_not_gate_complete_required_creation_slice():
    reply = created()
    raw = reply["result"]["after"]["new_template"]
    row = raw["references"]["DTREF"]
    row.update(total_count=None, returned_count=0, values=[], reference_null=None, native_array_materialized=False,
               truncated=None, inspection_limited=None, status="unknown", errors={"value": "Getter failed"}, complete=False)
    raw.update(success=False, complete=False)
    from aveva_e3d_mcp.paragon_component_template import _validate as validate_template
    validate_template(envelope(raw), {"refno": NEW})
    _validate(reply, wire(EXECUTE))
    assert raw["references"]["DTREF"]["status"] == "unknown" and raw["complete"] is False


@pytest.mark.parametrize("mismatch", [False, True])
def test_reference_disagreement_dominates_an_independent_unknown_but_unknown_never_becomes_equal(mismatch):
    reply = created()
    result = reply["result"]
    raw = result["after"]["new_template"]
    if mismatch:
        identity = raw["references"]["PTREF"]["values"][0]["identity"]
        identity.update(refno="=7/999", identity_key="=7/999|PTSE")
    row = raw["references"]["GMREF"]
    row.update(total_count=None, returned_count=0, values=[], reference_null=None, native_array_materialized=False,
               truncated=None, inspection_limited=None, status="unknown", errors={"value": "Getter failed"}, complete=False)
    raw.update(success=False, complete=False)
    result["progress"]["references_equal_request"] = False if mismatch else None
    assert _after(result["after"], result["before"], wire(EXECUTE), SESSION, BUILD, result["progress"]) is False
    result["progress"]["references_equal_request"] = True
    with pytest.raises(ValueError, match="independent references_equal_request"):
        _after(result["after"], result["before"], wire(EXECUTE), SESSION, BUILD, result["progress"])


@pytest.mark.parametrize("status", ["unknown_or_expired", "accepted", "running", "completed"])
def test_managed_journal_states_and_native_unknowns(status):
    receipt = created() if status == "completed" else None
    reply = envelope(snapshot(status, receipt=receipt))
    _validate_status(reply, STATUS_ARGS)
    original = deepcopy(reply)
    bridge = client()
    with patch.object(bridge, "_request", return_value=reply) as request:
        assert paragon_component_create_status(bridge, STATUS_ARGS) is reply
    request.assert_called_once_with("GET", STATUS_PATH, query=STATUS_ARGS, version="E3D21")
    assert reply == original


@pytest.mark.parametrize("field,value", [("operation", "paragon_component_copy"), ("request_id", BUILD),
    ("retry_safe", True), ("status", "created"), ("receipt_available", 1), ("completion_pending", 1)])
def test_managed_journal_binding_and_state_types(field, value):
    reply = envelope(snapshot("completed", receipt=created()))
    reply["result"][field] = value
    with pytest.raises(ValueError):
        _validate_status(reply, STATUS_ARGS)


@pytest.mark.parametrize("field", sorted(_SUMMARY_FIELDS - {"error_code", "retry_safe"}))
def test_available_history_critical_summary_must_agree_with_stored_native_receipt(field):
    reply = envelope(snapshot("completed", receipt=created()))
    summary = reply["result"]["critical_summary"]
    summary[field] = POINT if field == "known_new_refno" else not summary[field]
    with pytest.raises(ValueError):
        _validate_status(reply, STATUS_ARGS)


@pytest.mark.parametrize("field", sorted(_SUMMARY_FIELDS - {"error_code", "retry_safe"}))
def test_available_history_cannot_drop_known_native_facts_from_its_summary(field):
    reply = envelope(snapshot("completed", receipt=created()))
    reply["result"]["critical_summary"][field] = None
    with pytest.raises(ValueError):
        _validate_status(reply, STATUS_ARGS)


@pytest.mark.parametrize("field", sorted(_SUMMARY_FIELDS - {"retry_safe"}))
def test_unstarted_history_cannot_invent_native_facts(field):
    reply = envelope(snapshot())
    reply["result"]["critical_summary"][field] = NEW if field == "known_new_refno" else "error" if field == "error_code" else False
    with pytest.raises(ValueError):
        _validate_status(reply, STATUS_ARGS)

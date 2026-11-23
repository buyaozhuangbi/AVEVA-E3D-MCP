"""Offline input, observation and scoped postcondition checks for native copy."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.paragon_component_copy import (
    PATH, RECIPE, STATUS_PATH, STATUS_TOOL, TOOL, _parameters, _status_parameters, _validate, _validate_status,
    paragon_component_copy, paragon_component_copy_status, register_paragon_component_copy,
)
from aveva_e3d_mcp.protocol import McpServer
from aveva_e3d_mcp.paragon_component_template import _validate as validate_template
from test_paragon_generation_plan_evidence import template_identity, template_observation


SESSION = "11111111-1111-4111-8111-111111111111"
BUILD = "a2222222-2222-4222-8222-222222222222"
REQUEST_ID = "a3333333-3333-4333-8333-333333333333"
REVISION = "a" * 64
SOURCE, PARENT, NEW = "=7/101", "=7/100", "=7/103"
ARGS = {"source_refno": SOURCE, "parent_refno": PARENT, "name": "/NEW_SCOM"}
PREPARE = {**ARGS, "recipe": RECIPE, "dry_run": True}
EXECUTE = {**ARGS, "recipe": RECIPE, "dry_run": False, "request_id": REQUEST_ID,
           "expected_session_id": SESSION, "expected_bridge_build": BUILD, "expected_revision": REVISION}
STATUS_ARGS = {"request_id": REQUEST_ID, "expected_session_id": SESSION}
REFERENCES = ("PTREF", "GMREF", "DTREF", "BLRFA", "NGMREF", "PSTREF", "GSTREF")
SCOPE = {
    "kind": "same_category_scom_template_copy", "backend": "server_generated_fixed_pml",
    "parameter_comparison": "complete_finite_numeric_PARAM_only", "reference_comparison": "observed_supported_fixed_seven_only",
    "all_references_covered": False, "whole_clone_fingerprint": False, "geometry_observed": False,
    "geometry_equivalence_verified": False, "word_parameters_observed": False, "hidden_attributes_observed": False,
    "engineering_validated": None, "write_permission_verified": None, "savework_performed": False,
    "automatic_ce_restore": False, "atomic_snapshot": False, "scoped_revision_is_lock": False,
    "whole_project_coverage": False,
}


def envelope(result):
    return {"ok": True, "version": "E3D21", "session_id": SESSION, "bridge_build": BUILD, "result": result}


def core(reference=PARENT, kind="CATE"):
    return {"refno": reference, "type": kind, "db_type": "Catalogue", "db_type_code": 2,
            "identity_key": f"{reference}|{kind}"}


def member(reference=SOURCE, kind="SCOM", name="/TEMPLATE"):
    return {**core(reference, kind), "name": name, "name_error": None}


def context():
    return {"session_id": SESSION, "bridge_build": BUILD, "project": "PLANT", "mdb": "CATALOGUE-MDB",
            "current_element": core(), "instance_binding": "b" * 64}


def template(reference=SOURCE, name="/TEMPLATE"):
    result = template_observation()["result"]
    result["session_id"], result["bridge_build"] = SESSION, BUILD
    result["requested_refno"] = reference
    result["target"] = template_identity(reference, "SCOM", name, requested=reference)
    for side in ("before", "after"):
        result["context"][side] = {key: deepcopy(value) for key, value in context().items() if key != "instance_binding"}
        result["context"][side]["errors"] = {}
    return result


def before():
    return {"module": {"name": "PARAGON", "number": 81}, "context": context(), "source_template": template(),
            "parent": template_identity(PARENT, "CATE", "/CATEGORY"),
            "source_owner": template_identity(PARENT, "CATE", "/CATEGORY"),
            "members": [member()], "members_complete": True,
            "name_lookup": {"status": "absent", "target": None}, "creatable": True}


def not_attempted():
    return {"creation_attempted": False, "native_run_attempted": False, "native_run_returned": False,
            "native_run_success": None, "known_new_refno": None, "known_new_refno_verified": None,
            "readback_complete": False, "source_unchanged_scoped": None, "copied_parameters_equal": None,
            "copied_references_equal": None, "outcome_uncertain": False, "native_fault_latched": False, "retry_safe": False}


def prepared():
    return envelope({"operation": "paragon_component_copy", "recipe": RECIPE, "dry_run": True,
        "request": {**ARGS, "recipe": RECIPE}, "session_id": SESSION, "bridge_build": BUILD,
        "request_id": None, "status": "prepared", "success": True, "complete": True,
        "scoped_revision": REVISION, "before": before(), "after": None, "progress": not_attempted(),
        "errors": [], "journal": None, "scope": deepcopy(SCOPE), "this_submission_executed": False, "request_replayed": False})


def executed(*, journal=True):
    reply = prepared()
    result = reply["result"]
    result.update(dry_run=False, request_id=REQUEST_ID, status="executed", this_submission_executed=True)
    result["progress"].update(creation_attempted=True, native_run_attempted=True, native_run_returned=True,
        native_run_success=True, known_new_refno=NEW, known_new_refno_verified=True, readback_complete=True,
        source_unchanged_scoped=True, copied_parameters_equal=True, copied_references_equal=True)
    result["after"] = {"module": {"name": "PARAGON", "number": 81}, "context": context(),
        "source_template": template(), "new_template": template(NEW, ARGS["name"]),
        "parent": template_identity(PARENT, "CATE", "/CATEGORY"),
        "new_owner": template_identity(PARENT, "CATE", "/CATEGORY"),
        "members": [member(), member(NEW, name=ARGS["name"])], "members_complete": True,
        "verification": {"new_identity": True, "parent_append": True, "source_unchanged_scoped": True,
                         "copied_parameters_equal": True, "copied_references_equal": True}}
    if journal:
        result["journal"] = snapshot("completed", reply=deepcopy(reply))
    return reply


def snapshot(status="unknown_or_expired", *, reply=None):
    summary = {key: None for key in ("creation_attempted", "native_run_attempted", "native_run_returned",
        "native_run_success", "known_new_refno", "known_new_refno_verified", "readback_complete",
        "source_unchanged_scoped", "outcome_uncertain", "native_fault_latched", "error_code")}
    summary["retry_safe"] = False
    if reply:
        summary.update({key: reply["result"]["progress"][key] for key in summary if key != "error_code"})
    return {"operation": "paragon_component_copy", "available": status != "unknown_or_expired", "status": status,
        "request_id": REQUEST_ID, "request_fingerprint": "f" * 64 if status != "unknown_or_expired" else None,
        "request_id_reserved": status != "unknown_or_expired", "accepted_utc": "2026-11-24T01:00:00.0000000+00:00" if status != "unknown_or_expired" else None,
        "started_utc": "2026-11-24T01:00:01.0000000+00:00" if status in {"running", "completed"} else None,
        "completed_utc": "2026-11-24T01:00:02.0000000+00:00" if status == "completed" else None,
        "completion_pending": False, "http_status": 200 if status == "completed" else None,
        "receipt_available": reply is not None, "receipt_omitted_reason": None if reply else
            "receipt_missing" if status == "completed" else "unknown_or_expired" if status == "unknown_or_expired" else "not_completed",
        "receipt": reply, "critical_summary": summary, "retry_safe": False,
        "store_policy": {"receipt_record_limit": 64, "max_receipt_utf8_bytes": 262144, "session_request_id_limit": 65536,
            "completed_retention_seconds": 1800.0, "maximum_json_depth": 64, "receipt_budget_kind": "serialized_utf8_logical_bytes",
            "accepted_or_running_evicted": False, "request_ids_released_before_session_end": False,
            "durable": False, "cross_session_exactly_once": False},
        "scope": "Session-local managed callback ownership only; accepted is not running, running does not prove native execution, and completed records a final receipt rather than native success. Unknown/expired does not prove no execution. No replay, durability, cross-session exactly-once, or SDK/UI ownership guarantee."}


def bridge(*, commands=True, mode="bridge", version="E3D21"):
    return BridgeClient(Settings(mode=mode, version=version, allow_commands=commands))


def call(server, args, tool=TOOL):
    return server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": tool, "arguments": args}})["result"]


@pytest.mark.parametrize("args,payload", [(ARGS, PREPARE), (PREPARE, PREPARE), (EXECUTE, EXECUTE)])
def test_input_defaults_and_execution_tokens_are_explicit(args, payload):
    assert _parameters(args) == payload
    assert _parameters(args) is not args


@pytest.mark.parametrize("field", ["source_refno", "parent_refno"])
@pytest.mark.parametrize("bad", [None, True, 1, "CE", "/NAME", "=0/1", "=01/1", "=1/01", "=1/-1", "=1/+1",
                                 " =1/1", "=1/1 ", "=1/1\n", "=1/１", "=2147483648/0", "=1/2147483648"])
def test_numeric_reference_contract_is_closed(field, bad):
    with pytest.raises(ValueError):
        _parameters({**ARGS, field: bad})


@pytest.mark.parametrize("name", ["/A", "/Name_9-X", "/A/B2/C_D-E", "/" + "A" * 49])
def test_name_segments_allow_only_letter_led_ascii(name):
    assert _parameters({**ARGS, "name": name})["name"] == name


@pytest.mark.parametrize("name", [None, True, "A", "/", "/A/", "/A//B", "/1A", "/A/1B", "/A/_B", "/_A",
                                  "/A B", "/A;NEW", "/A\n", " /A", "/A ", "/Ａ", "/A'", "/A|", "/" + "A" * 50])
def test_name_cannot_inject_fixed_pml_or_exceed_budget(name):
    with pytest.raises(ValueError):
        _parameters({**ARGS, "name": name})


@pytest.mark.parametrize("key", ["request_id", "expected_session_id", "expected_bridge_build", "expected_revision"])
@pytest.mark.parametrize("value", [None, "present"])
def test_prepare_cannot_carry_any_execution_token_even_null(key, value):
    with pytest.raises(ValueError):
        _parameters({**ARGS, key: value})


@pytest.mark.parametrize("key", ["request_id", "expected_session_id", "expected_bridge_build", "expected_revision"])
def test_execute_requires_every_execution_token(key):
    args = dict(EXECUTE)
    del args[key]
    with pytest.raises(ValueError):
        _parameters(args)


@pytest.mark.parametrize("key,bad", [
    ("request_id", REQUEST_ID.upper()), ("request_id", REQUEST_ID + "\n"), ("request_id", None),
    ("expected_bridge_build", BUILD.upper()), ("expected_bridge_build", "{" + BUILD + "}"),
    ("expected_revision", "A" * 64), ("expected_revision", "a" * 63), ("expected_revision", "a" * 64 + "\n"),
    ("expected_session_id", ""), ("expected_session_id", "x\n"), ("expected_session_id", "x" * 257),
    ("expected_session_id", "😀" * 129), ("expected_session_id", "\ud800"),
    ("dry_run", 0), ("dry_run", None), ("dry_run", "false"), ("recipe", "another_recipe"),
])
def test_execution_token_type_and_identity_bounds(key, bad):
    with pytest.raises(ValueError):
        _parameters({**EXECUTE, key: bad})


def test_source_and_parent_must_differ():
    with pytest.raises(ValueError, match="different"):
        _parameters({**ARGS, "source_refno": PARENT})


def test_exact_utf16_session_boundary_preserves_original_identity():
    session = "😀" * 128
    assert _parameters({**EXECUTE, "expected_session_id": session})["expected_session_id"] == session


@pytest.mark.parametrize("tokens", [{}, {"recipe": None}, {"dry_run": True}, {"capture_after": True}, {"version": "E3D21"}])
def test_closed_status_arguments_and_required_session_pin(tokens):
    args = tokens if not tokens else {**STATUS_ARGS, **tokens}
    with pytest.raises(ValueError):
        _status_parameters(args)


def test_execute_permission_is_checked_before_any_io():
    client = bridge(commands=False)
    with patch.object(client, "_request") as request, pytest.raises(ValueError, match="ALLOW_COMMANDS"):
        paragon_component_copy(client, EXECUTE)
    request.assert_not_called()


@pytest.mark.parametrize("mode,version", [("mock", "E3D21"), ("bridge", "E3D31")])
def test_copy_and_status_unsupported_runtime_have_no_mock_execution(mode, version):
    client = bridge(mode=mode, version=version)
    with patch.object(client, "_request") as request:
        with pytest.raises(BridgeError, match="E3D21 bridge"):
            paragon_component_copy(client, ARGS)
        with pytest.raises(BridgeError, match="E3D21 bridge"):
            paragon_component_copy_status(client, STATUS_ARGS)
    request.assert_not_called()


def test_closed_public_schema_enforces_prepare_and_execute_before_handler():
    server = McpServer("copy-offline", "0")
    client = bridge()
    register_paragon_component_copy(server, client)
    copy_tool = server._tools[TOOL][0]
    status_tool = server._tools[STATUS_TOOL][0]
    assert copy_tool["annotations"] == {"readOnlyHint": False, "destructiveHint": True,
                                         "idempotentHint": False, "openWorldHint": False}
    assert status_tool["annotations"]["readOnlyHint"] is True
    assert copy_tool["inputSchema"]["additionalProperties"] is False
    assert status_tool["inputSchema"]["additionalProperties"] is False
    with patch.object(client, "_request") as request:
        for args in ({**ARGS, "request_id": None}, {**ARGS, "dry_run": False},
                     {**ARGS, "source_refno": "=2147483648/1"}, {**ARGS, "name": "/A/1"}):
            assert call(server, args)["isError"] is True
    request.assert_not_called()


@pytest.mark.parametrize("factory,payload", [(prepared, PREPARE), (executed, EXECUTE)])
def test_native_scoped_success_is_validated_and_returned_intact(factory, payload):
    reply = factory()
    original = deepcopy(reply)
    _validate(reply, payload)
    client = bridge()
    with patch.object(client, "_request", return_value=reply) as request:
        assert paragon_component_copy(client, payload) is reply
    request.assert_called_once_with("POST", PATH, payload, version="E3D21")
    assert reply == original


@pytest.mark.parametrize("status", ["accepted", "running", "completed", "unknown_or_expired"])
def test_managed_status_never_upgrades_journal_state_to_native_success(status):
    reply = envelope(snapshot(status))
    _validate_status(reply, STATUS_ARGS)
    client = bridge(commands=False)
    with patch.object(client, "_request", return_value=reply) as request:
        assert paragon_component_copy_status(client, STATUS_ARGS) is reply
    request.assert_called_once_with("GET", STATUS_PATH, query=STATUS_ARGS, version="E3D21")
    assert "success" not in reply["result"]


@pytest.mark.parametrize("part,key,value", [
    ("progress", "native_run_success", False), ("progress", "readback_complete", False),
    ("progress", "known_new_refno", SOURCE), ("progress", "known_new_refno_verified", False),
    ("progress", "creation_attempted", 1), ("result", "this_submission_executed", False),
    ("result", "request_replayed", True), ("result", "scoped_revision", "b" * 64),
    ("scope", "whole_clone_fingerprint", True), ("scope", "engineering_validated", True),
    ("result", "success", 1), ("result", "complete", False),
])
def test_summary_booleans_do_not_replace_independent_copy_evidence(part, key, value):
    reply = executed(journal=False)
    result = reply["result"]
    {"result": result, "progress": result["progress"], "scope": result["scope"]}[part][key] = value
    with pytest.raises(ValueError):
        _validate(reply, EXECUTE)


@pytest.mark.parametrize("side,field,value", [
    ("before", "instance_binding", "c" * 64), ("after", "instance_binding", "c" * 64),
    ("before", "current_element", None), ("after", "current_element", None),
    ("after", "project", "ANOTHER"), ("after", "mdb", "ANOTHER"),
    ("before", "session_id", "another-session"), ("after", "bridge_build", "b" * 36),
])
def test_context_identity_changes_cannot_pass_the_execution_gate(side, field, value):
    reply = executed(journal=False)
    reply["result"][side]["context"][field] = value
    with pytest.raises(ValueError):
        _validate(reply, EXECUTE)


@pytest.mark.parametrize("field,value", [("refno", "=7/999"), ("type", "SCOM"), ("db_type_code", 1)])
def test_new_owner_raw_identity_must_match_explicit_catalogue_cate(field, value):
    reply = executed(journal=False)
    reply["result"]["after"]["new_owner"][field] = value
    with pytest.raises(ValueError):
        _validate(reply, EXECUTE)


@pytest.mark.parametrize("which", ["PARAM", "PTREF", "parent_order", "new_name", "source_name", "actual_type"])
def test_copy_values_and_parent_append_are_recomputed_from_raw_receipts(which):
    reply = executed(journal=False)
    after = reply["result"]["after"]
    if which == "PARAM":
        after["new_template"]["parameters"]["values"][1]["value"] = 81.0
    elif which == "PTREF":
        target = after["new_template"]["references"]["PTREF"]["values"][0]["identity"]
        target["refno"], target["identity_key"] = "=7/999", "=7/999|PTSE"
    elif which == "parent_order":
        after["members"].reverse()
    elif which == "new_name":
        after["new_template"]["target"]["name"] = "/DIFFERENT"
    elif which == "source_name":
        after["source_template"]["target"]["name"] = "/CHANGED"
    else:
        after["new_template"]["target"]["actual_type"]["short_name"] = "U_SCOM"
    with pytest.raises(ValueError):
        _validate(reply, EXECUTE)


def template_partial(raw):
    raw.update(success=False, complete=False)


def unsupported(raw, name):
    template_partial(raw)
    attribute = raw["references"][name]
    attribute.update(status="unknown", supported=False, sdk_type=None, is_array=None, unit_kind=None,
        shape=None, reference_null=None, total_count=None, returned_count=0, truncated=None,
        inspection_limited=None, native_array_materialized=False, values=[],
        errors={"support": "unsupported_attribute_value_unknown"}, complete=False)


def reference_name_error(raw, *, type_name=False, short_name=False):
    template_partial(raw)
    attribute = raw["references"]["PTREF"]
    attribute.update(status="partial", complete=False, errors={"items": "Partial native reference identity"})
    item = attribute["values"][0]
    item.update(status="partial", error="Native identity metadata unavailable")
    identity = item["identity"]
    identity["complete"] = False
    if type_name or short_name:
        observation = identity["actual_type"]
        field = "short_name" if short_name else "name"
        observation[field] = None
        observation.update(status="unknown", errors={field: "Native type getter unavailable"})
    else:
        identity.update(name=None, name_error="Native NAME getter unavailable", errors={"name": "Native NAME getter unavailable"})


def assert_template_valid(raw):
    validate_template(envelope(raw), {"refno": raw["requested_refno"]})


@pytest.mark.parametrize("type_name", [False, True])
def test_reference_name_partial_does_not_erase_independent_typed_comparison(type_name):
    reply = executed(journal=False)
    result = reply["result"]
    for raw in (result["before"]["source_template"], result["after"]["source_template"], result["after"]["new_template"]):
        reference_name_error(raw, type_name=type_name)
        assert_template_valid(raw)
    _validate(reply, EXECUTE)
    assert result["success"] is True
    assert result["before"]["source_template"]["complete"] is False


def test_unknown_reference_actual_short_name_cannot_be_compared_to_another_unknown():
    reply = executed(journal=False)
    result = reply["result"]
    for raw in (result["before"]["source_template"], result["after"]["source_template"], result["after"]["new_template"]):
        reference_name_error(raw, short_name=True)
        assert_template_valid(raw)
    with pytest.raises(ValueError):
        _validate(reply, EXECUTE)


def test_known_unsupported_references_are_excluded_when_other_slices_are_observed():
    reply = executed(journal=False)
    result = reply["result"]
    for raw in (result["before"]["source_template"], result["after"]["source_template"], result["after"]["new_template"]):
        unsupported(raw, "BLRFA")
        assert_template_valid(raw)
    _validate(reply, EXECUTE)


def test_all_unsupported_reference_attributes_cannot_establish_copy_coverage():
    reply = prepared()
    raw = reply["result"]["before"]["source_template"]
    for name in REFERENCES:
        unsupported(raw, name)
    assert_template_valid(raw)
    with pytest.raises(ValueError):
        _validate(reply, PREPARE)
    reply["result"].update(status="blocked", success=False, complete=False, scoped_revision=None)
    reply["result"]["errors"] = [{"stage": "preflight", "code": "no_supported_reference_coverage", "message": "No supported reference slice"}]
    _validate(reply, PREPARE)


@pytest.mark.parametrize("which", ["source", "new"])
def test_valid_param_output_prefix_is_not_complete_numeric_copy_evidence(which):
    reply = executed(journal=False)
    raw = reply["result"]["before"]["source_template"] if which == "source" else reply["result"]["after"]["new_template"]
    template_partial(raw)
    value = raw["parameters"]
    value.update(status="partial", complete=False, total_count=129, returned_count=128, truncated=True,
                 errors={"output_limit": "Numeric prefix truncated"}, values=[
                    {"index": index, "value": float(index), "status": "observed", "error": None} for index in range(128)])
    assert_template_valid(raw)
    with pytest.raises(ValueError):
        _validate(reply, EXECUTE)


def test_numeric_negative_zero_is_compared_as_native_double_bits():
    reply = executed(journal=False)
    reply["result"]["after"]["new_template"]["parameters"]["values"][0]["value"] = -0.0
    assert_template_valid(reply["result"]["after"]["new_template"])
    with pytest.raises(ValueError):
        _validate(reply, EXECUTE)


@pytest.mark.parametrize("which", ["before", "after"])
def test_bounded_parent_limit_is_enforced_without_changing_whole_source_coverage(which):
    reply = executed(journal=False)
    members = reply["result"][which]["members"]
    maximum = 100 if which == "before" else 101
    members[:] = [member(f"=7/{1000 + index}", name=f"/SIBLING{index}") for index in range(maximum + 1)]
    with pytest.raises(ValueError, match="member observation bounds"):
        _validate(reply, EXECUTE)


def test_one_hundred_observed_members_can_have_one_verified_append():
    reply = executed(journal=False)
    siblings = [member(f"=7/{1000 + index}", name=f"/SIBLING{index}") for index in range(99)]
    reply["result"]["before"]["members"] = [member(), *deepcopy(siblings)]
    reply["result"]["after"]["members"] = [member(), *deepcopy(siblings), member(NEW, name=ARGS["name"])]
    _validate(reply, EXECUTE)


@pytest.mark.parametrize("which", ["before", "after"])
def test_duplicate_member_ref_cannot_make_a_unique_append(which):
    reply = executed(journal=False)
    reply["result"][which]["members"].append(member())
    with pytest.raises(ValueError):
        _validate(reply, EXECUTE)


@pytest.mark.parametrize("field", ["native_run_attempted", "known_new_refno_verified", "readback_complete", "outcome_uncertain"])
@pytest.mark.parametrize("fake", [0, 1, "true"])
def test_progress_never_coerces_python_bool_equal_numeric_values(field, fake):
    reply = executed(journal=False)
    reply["result"]["progress"][field] = fake
    with pytest.raises(ValueError):
        _validate(reply, EXECUTE)


@pytest.mark.parametrize("field,bad", [("version", None), ("session_id", "another-session"), ("bridge_build", BUILD.upper())])
def test_status_rejects_missing_or_conflicting_independent_envelope_identity(field, bad):
    reply = envelope(snapshot())
    reply[field] = bad
    with pytest.raises(ValueError):
        _validate_status(reply, STATUS_ARGS)


@pytest.mark.parametrize("reason", ["receipt_too_large", "receipt_not_pure_json", "serialization_failed", "receipt_missing"])
def test_omitted_history_retains_nullable_critical_facts_without_success_projection(reason):
    row = snapshot("completed")
    row["receipt_omitted_reason"] = reason
    row["critical_summary"].update(creation_attempted=True, native_run_attempted=True, known_new_refno=NEW,
                                    outcome_uncertain=True, native_fault_latched=True)
    reply = envelope(row)
    _validate_status(reply, STATUS_ARGS)
    assert "success" not in row and row["receipt"] is None


@pytest.mark.parametrize("field,value", [("available", True), ("receipt_available", True), ("request_fingerprint", "f" * 64),
                                       ("http_status", 200), ("critical_summary", {"success": True})])
def test_unknown_or_expired_history_cannot_claim_observed_execution(field, value):
    row = snapshot()
    row[field] = value
    with pytest.raises(ValueError):
        _validate_status(envelope(row), STATUS_ARGS)


def native_null_reference(raw, *, array=False, mixed=False):
    value = raw["references"]["PTREF"]
    null = {"index": 0, "status": "unset", "identity": None, "reference_null": True, "error": None}
    rows = [null]
    if mixed:
        observed = deepcopy(value["values"][0])
        observed["index"] = 1
        rows.append(observed)
    value.update(is_array=array, shape="element_array" if array else "element", reference_null=None if array else True,
                 native_array_materialized=array, total_count=len(rows), returned_count=len(rows), values=rows)


@pytest.mark.parametrize("array,mixed", [(False, False), (True, False), (True, True)])
def test_verified_native_null_reference_values_are_comparable_in_scalar_or_array_order(array, mixed):
    reply = executed(journal=False)
    result = reply["result"]
    for raw in (result["before"]["source_template"], result["after"]["source_template"], result["after"]["new_template"]):
        native_null_reference(raw, array=array, mixed=mixed)
        assert_template_valid(raw)
    _validate(reply, EXECUTE)


def test_only_supported_native_null_attribute_can_establish_fixed_reference_coverage():
    reply = prepared()
    raw = reply["result"]["before"]["source_template"]
    for name in REFERENCES:
        if name != "PTREF":
            unsupported(raw, name)
    native_null_reference(raw)
    assert_template_valid(raw)
    _validate(reply, PREPARE)


def test_native_null_array_order_is_preserved_in_reference_comparison():
    reply = executed(journal=False)
    result = reply["result"]
    for raw in (result["before"]["source_template"], result["after"]["source_template"], result["after"]["new_template"]):
        native_null_reference(raw, array=True, mixed=True)
    values = result["after"]["new_template"]["references"]["PTREF"]["values"]
    values.reverse()
    for index, row in enumerate(values):
        row["index"] = index
    assert_template_valid(result["after"]["new_template"])
    with pytest.raises(ValueError):
        _validate(reply, EXECUTE)


def test_unknown_null_proxy_is_not_equal_to_verified_native_null():
    reply = executed(journal=False)
    result = reply["result"]
    for raw in (result["before"]["source_template"], result["after"]["source_template"]):
        native_null_reference(raw)
    raw = result["after"]["new_template"]
    template_partial(raw)
    attribute = raw["references"]["PTREF"]
    attribute.update(status="partial", complete=False, errors={"items": "Unknown native reference"})
    attribute["values"] = [{"index": 0, "status": "unknown", "identity": None, "reference_null": None,
                             "error": "Native CLR proxy unread"}]
    assert_template_valid(raw)
    with pytest.raises(ValueError):
        _validate(reply, EXECUTE)


def test_appended_member_and_new_template_must_share_full_typed_identity():
    reply = executed(journal=False)
    row = reply["result"]["after"]["members"][-1]
    row.update(type="CYLI", identity_key=f"{NEW}|CYLI")
    with pytest.raises(ValueError, match="identity"):
        _validate(reply, EXECUTE)


def test_unicode_casefold_expansion_cannot_substitute_for_requested_ascii_name():
    args = {**EXECUTE, "name": "/SS"}
    reply = executed(journal=False)
    result = reply["result"]
    result["request"]["name"] = "/SS"
    result["after"]["members"][-1]["name"] = "/ß"
    result["after"]["new_template"]["target"]["name"] = "/ß"
    with pytest.raises(ValueError, match="identity"):
        _validate(reply, args)


def test_reference_base_identity_drift_cannot_be_compared_as_positive_coverage():
    reply = executed(journal=False)
    result = reply["result"]
    for raw in (result["before"]["source_template"], result["after"]["source_template"], result["after"]["new_template"]):
        template_partial(raw)
        value = raw["references"]["PTREF"]
        value.update(status="partial", complete=False, errors={"items": "Native base identity drift"})
        item = value["values"][0]
        item.update(status="partial", error="Native base identity drift")
        identity = item["identity"]
        identity["base_type"]["short_name"] = "GMSE"
        identity.update(complete=False, errors={"base_type": "Native base type changed"})
        assert_template_valid(raw)
    with pytest.raises(ValueError):
        _validate(reply, EXECUTE)

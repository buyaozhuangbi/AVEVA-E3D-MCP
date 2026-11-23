"""Public input, binary64 and native PARAM evidence validation without E3D."""
from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import struct
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.paragon_component_parameters import (
    OPERATION, PATH, STATUS_PATH, STATUS_TOOL, TOOL, _parameters, _validate, _validate_status,
    paragon_component_parameters_set, paragon_component_parameters_status, register_paragon_component_parameters,
)
from aveva_e3d_mcp.protocol import McpServer
from test_paragon_component_copy import (
    BUILD, NEW, PARENT, REQUEST_ID, REVISION, SESSION, STATUS_ARGS, context, core, envelope,
    reference_name_error, snapshot as copy_snapshot, template, template_identity, template_partial,
)


VALUES = [1.0, -0.0, 3.5]
ARGS = {"refno": NEW, "parameters": VALUES}
PREPARE = {**ARGS, "dry_run": True}
EXECUTE = {**ARGS, "dry_run": False, "request_id": REQUEST_ID, "expected_session_id": SESSION,
           "expected_bridge_build": BUILD, "expected_revision": REVISION}
SCOPE = {
    "kind": "fixed_catalogue_scom_numeric_parameters_update", "backend": "native_sdk_double_array_setter",
    "parameter_comparison": "complete_finite_binary64_bits", "whole_array_replacement": True,
    "array_length_change_allowed": False, "units_converted": False, "units_verified": False,
    "geometry_observed": False, "geometry_equivalence_verified": False, "word_parameters_observed": False,
    "hidden_attributes_observed": False, "all_references_covered": False, "whole_clone_fingerprint": False,
    "target_creation_verified": False, "engineering_validated": None, "write_permission_verified": None,
    "savework_performed": False, "automatic_ce_restore": False, "atomic_snapshot": False,
    "scoped_revision_is_lock": False, "whole_project_coverage": False,
}
PROGRESS_FIELDS = {"known_target_refno", "known_target_refno_verified", "parameter_write_attempted", "parameter_write_returned",
    "parameters_equal_request", "readback_complete", "context_unchanged", "no_change", "outcome_uncertain", "native_fault_latched", "retry_safe"}


def bits(values):
    return [struct.pack(">d", float(value)).hex() for value in values]


def wire(args=PREPARE):
    return {**deepcopy(args), "parameters": [float(value) for value in args["parameters"]], "parameter_bits": bits(args["parameters"]),
            "dry_run": args.get("dry_run", True)}


def evidence(values=None, *, after=False):
    raw = template(NEW, "/NEW_SCOM")
    if values is not None:
        raw["parameters"].update(total_count=len(values), returned_count=len(values), values=[
            {"index": index, "value": float(value), "status": "observed", "error": None} for index, value in enumerate(values)])
    observed = [row["value"] for row in raw["parameters"]["values"]]
    return {"module": {"name": "PARAGON", "number": 81}, "context": context(), "template": raw,
            "target_binding": core(NEW, "SCOM"),
            "owner": template_identity(PARENT, "CATE", "/CATEGORY"), "setable": None if after else True,
            "parameter_bits": bits(observed)}


def prepared(values=VALUES):
    request = {"refno": NEW, "parameters": list(values), "parameter_bits": bits(values)}
    before = evidence()
    return envelope({"operation": OPERATION, "dry_run": True, "request": request, "session_id": SESSION, "bridge_build": BUILD,
        "request_id": None, "status": "prepared", "success": True, "complete": True, "scoped_revision": REVISION,
        "before": before, "after": None, "progress": {
            "known_target_refno": NEW, "known_target_refno_verified": True, "parameter_write_attempted": False,
            "parameter_write_returned": False, "parameters_equal_request": None, "readback_complete": False,
            "context_unchanged": None, "no_change": before["parameter_bits"] == bits(values),
            "outcome_uncertain": False, "native_fault_latched": False, "retry_safe": False},
        "errors": [], "journal": None, "scope": deepcopy(SCOPE), "this_submission_executed": False, "request_replayed": False})


def updated(values=VALUES, *, journal=True, unchanged=False):
    reply = prepared(values)
    result = reply["result"]
    result.update(dry_run=False, request_id=REQUEST_ID, status="unchanged" if unchanged else "updated",
                  after=evidence(values, after=True), this_submission_executed=not unchanged)
    if unchanged:
        result["before"] = evidence(values)
    result["progress"].update(parameter_write_attempted=not unchanged, parameter_write_returned=not unchanged,
                             parameters_equal_request=True, readback_complete=True, context_unchanged=True, no_change=unchanged)
    if journal:
        result["journal"] = snapshot("completed", receipt=deepcopy(reply))
    return reply


def snapshot(status="unknown_or_expired", *, receipt=None):
    row = copy_snapshot(status)
    row["operation"] = OPERATION
    row["critical_summary"] = {key: None for key in PROGRESS_FIELDS | {"error_code"}}
    row["critical_summary"]["retry_safe"] = False
    if receipt is not None:
        row.update(receipt_available=True, receipt_omitted_reason=None, receipt=receipt)
        row["critical_summary"].update(receipt["result"]["progress"])
    return row


def client(*, commands=True, mode="bridge", version="E3D21"):
    return BridgeClient(Settings(mode=mode, version=version, allow_commands=commands))


def call(server, arguments, tool=TOOL):
    return server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": tool, "arguments": arguments}})["result"]


@pytest.mark.parametrize("args", [ARGS, PREPARE, EXECUTE])
def test_only_normalized_double_values_and_python_derived_bits_reach_wire(args):
    payload = _parameters(args)
    assert payload == wire(args)
    assert payload["parameter_bits"] == ["3ff0000000000000", "8000000000000000", "400c000000000000"]
    assert all(type(value) is float for value in payload["parameters"])
    assert math.copysign(1, payload["parameters"][1]) == -1
    assert payload["parameters"] is not args["parameters"]


@pytest.mark.parametrize("value", [None, True, "1", {}, [], float("nan"), float("inf"), -float("inf"), 10**1000])
def test_non_double_items_stop_before_bridge_access(value):
    with pytest.raises(ValueError):
        _parameters({"refno": NEW, "parameters": [value]})


@pytest.mark.parametrize("values", [None, "1,2", (), [], list(range(129))])
def test_only_complete_bounded_parameter_list_is_accepted(values):
    with pytest.raises(ValueError):
        _parameters({"refno": NEW, "parameters": values})


@pytest.mark.parametrize("count", [1, 128])
def test_numeric_count_boundaries_and_large_integer_normalization(count):
    values = [2**60 + 1] * count
    payload = _parameters({"refno": NEW, "parameters": values})
    assert payload["parameters"] == [float(value) for value in values]
    assert len(payload["parameter_bits"]) == count


@pytest.mark.parametrize("refno", [None, True, "CE", "/SCOM", "=0/1", "=01/1", "=1/01", "=1/-1", "=1/+1",
                                  " =1/1", "=1/1\n", "=1/１", "=2147483648/0", "=1/2147483648"])
def test_canonical_numeric_ref_is_required(refno):
    with pytest.raises(ValueError):
        _parameters({**ARGS, "refno": refno})


@pytest.mark.parametrize("field", ["parameter_bits", "attribute", "units", "recipe", "source_refno", "parent_refno", "version", "capture_after"])
def test_public_input_does_not_accept_wire_bits_or_other_attribute_controls(field):
    with pytest.raises(ValueError):
        _parameters({**ARGS, field: None})


@pytest.mark.parametrize("field", ["request_id", "expected_session_id", "expected_bridge_build", "expected_revision"])
@pytest.mark.parametrize("value", [None, "present"])
def test_preview_execution_tokens_are_forbidden_even_null(field, value):
    with pytest.raises(ValueError):
        _parameters({**ARGS, field: value})


@pytest.mark.parametrize("field", ["request_id", "expected_session_id", "expected_bridge_build", "expected_revision"])
def test_execute_requires_all_explicit_tokens(field):
    args = dict(EXECUTE)
    del args[field]
    with pytest.raises(ValueError):
        _parameters(args)


@pytest.mark.parametrize("field,value", [("request_id", REQUEST_ID.upper()), ("expected_bridge_build", BUILD.upper()),
    ("expected_session_id", "x\n"), ("expected_session_id", "😀" * 129), ("expected_session_id", "\ud800"),
    ("expected_revision", "A" * 64), ("dry_run", 0), ("dry_run", None)])
def test_execution_identity_and_boolean_types(field, value):
    with pytest.raises(ValueError):
        _parameters({**EXECUTE, field: value})


def test_permission_check_precedes_any_io_and_status_needs_no_command_permission():
    bridge = client(commands=False)
    with patch.object(bridge, "_request") as request, pytest.raises(ValueError, match="ALLOW_COMMANDS"):
        paragon_component_parameters_set(bridge, EXECUTE)
    request.assert_not_called()
    receipt = envelope(snapshot())
    with patch.object(bridge, "_request", return_value=receipt) as request:
        assert paragon_component_parameters_status(bridge, STATUS_ARGS) is receipt
    request.assert_called_once_with("GET", STATUS_PATH, query=STATUS_ARGS, version="E3D21")


@pytest.mark.parametrize("mode,version", [("mock", "E3D21"), ("bridge", "E3D31")])
def test_unsupported_runtime_never_fabricates_a_setter_or_mock_success(mode, version):
    bridge = client(mode=mode, version=version)
    with patch.object(bridge, "_request") as request:
        with pytest.raises(BridgeError):
            paragon_component_parameters_set(bridge, ARGS)
        with pytest.raises(BridgeError):
            paragon_component_parameters_status(bridge, STATUS_ARGS)
    request.assert_not_called()


def test_registration_is_closed_and_marks_only_status_read_only():
    server = McpServer("PARAM offline", "0")
    register_paragon_component_parameters(server, client())
    definition = server._tools[TOOL][0]
    assert definition["annotations"] == {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False}
    assert server._tools[STATUS_TOOL][0]["annotations"]["readOnlyHint"] is True
    assert definition["inputSchema"]["additionalProperties"] is False
    assert "parameter_bits" not in definition["inputSchema"]["properties"]
    assert definition["inputSchema"]["properties"]["parameters"] == {"type": "array", "minItems": 1, "maxItems": 128, "items": {"type": "number"}}


@pytest.mark.parametrize("factory,args", [(prepared, PREPARE), (updated, EXECUTE), (lambda: updated(unchanged=True), EXECUTE)])
def test_normal_native_receipt_is_validated_and_preserved(factory, args):
    reply = factory()
    original = deepcopy(reply)
    _validate(reply, wire(args))
    bridge = client()
    with patch.object(bridge, "_request", return_value=reply) as request:
        assert paragon_component_parameters_set(bridge, args) is reply
    request.assert_called_once_with("POST", PATH, wire(args), version="E3D21")
    assert reply == original


@pytest.mark.parametrize("where", ["request", "before", "after"])
def test_wire_zero_sign_loss_is_permitted_only_when_explicit_native_bits_are_preserved(where):
    reply = updated(journal=False)
    if where == "request":
        reply["result"]["request"]["parameters"][1] = 0.0
    else:
        if where == "before":
            reply["result"]["before"] = evidence([-0.0, 80.0, 125.5])
        index = 0 if where == "before" else 1
        reply["result"][where]["template"]["parameters"]["values"][index]["value"] = 0.0
    _validate(reply, wire(EXECUTE))


@pytest.mark.parametrize("where", ["request", "before", "after"])
@pytest.mark.parametrize("bad", ["7ff0000000000000", "fff0000000000000", "7ff8000000000000", "3FF0000000000000", "0" * 15, 1, None])
def test_nonfinite_or_noncanonical_bits_never_support_scoped_success(where, bad):
    reply = updated(journal=False)
    reply["result"][where]["parameter_bits"][0] = bad
    with pytest.raises(ValueError):
        _validate(reply, wire(EXECUTE))


@pytest.mark.parametrize("where", ["request", "before", "after"])
def test_nonzero_value_and_bits_must_match_exactly(where):
    reply = updated(journal=False)
    if where == "request":
        reply["result"][where]["parameters"][0] = 2.0
    else:
        reply["result"][where]["template"]["parameters"]["values"][0]["value"] = 2.0
    with pytest.raises(ValueError):
        _validate(reply, wire(EXECUTE))


def test_negative_zero_intent_cannot_be_replaced_by_positive_zero_bits():
    reply = updated(journal=False)
    reply["result"]["after"]["parameter_bits"][1] = "0000000000000000"
    with pytest.raises(ValueError):
        _validate(reply, wire(EXECUTE))


@pytest.mark.parametrize("field,value", [("parameters_equal_request", False), ("context_unchanged", False),
    ("parameter_write_returned", False), ("parameter_write_attempted", False), ("known_target_refno", "=7/999"),
    ("known_target_refno_verified", False), ("readback_complete", False), ("no_change", True)])
def test_progress_cannot_override_independent_numeric_context_and_identity_evidence(field, value):
    reply = updated(journal=False)
    reply["result"]["progress"][field] = value
    with pytest.raises(ValueError):
        _validate(reply, wire(EXECUTE))


@pytest.mark.parametrize("field", ["known_target_refno_verified", "parameter_write_attempted", "parameter_write_returned", "parameters_equal_request",
                                    "readback_complete", "context_unchanged", "no_change", "outcome_uncertain", "native_fault_latched"])
@pytest.mark.parametrize("fake", [0, 1, "true"])
def test_progress_uses_native_boolean_types(field, fake):
    reply = updated(journal=False)
    reply["result"]["progress"][field] = fake
    with pytest.raises(ValueError):
        _validate(reply, wire(EXECUTE))


@pytest.mark.parametrize("which", ["name", "unit_kind", "reference_name"])
def test_optional_partial_metadata_preserves_required_numeric_slice(which):
    reply = updated(journal=False)
    for side in ("before", "after"):
        raw = reply["result"][side]["template"]
        template_partial(raw)
        if which == "name":
            raw["target"].update(name=None, name_error="Native NAME unavailable", errors={"name": "Native NAME unavailable"}, complete=False)
        elif which == "unit_kind":
            raw["parameters"].update(unit_kind=None, errors={"unit_kind": "Native unit unavailable"}, status="partial", complete=False)
        else:
            reference_name_error(raw)
    _validate(reply, wire(EXECUTE))


@pytest.mark.parametrize("field,value", [("setable", False), ("module", {"name": "DESIGN", "number": 1}), ("context", None)])
def test_unobserved_or_wrong_required_precondition_blocks_preparation(field, value):
    reply = prepared()
    result = reply["result"]
    result.update(status="blocked", success=False, complete=False, scoped_revision=None,
                  errors=[{"stage": "preflight", "code": "required_slice_unknown", "message": "Native prerequisite failed"}])
    result["before"][field] = value
    result["progress"]["no_change"] = None
    _validate(reply, wire(PREPARE))


@pytest.mark.parametrize("side", ["before", "after"])
def test_array_length_change_cannot_pass_as_full_replacement(side):
    reply = updated(journal=False)
    reply["result"][side] = evidence([1.0, 2.0], after=side == "after")
    with pytest.raises(ValueError):
        _validate(reply, wire(EXECUTE))


@pytest.mark.parametrize("scope_key", ["units_verified", "geometry_observed", "target_creation_verified", "whole_clone_fingerprint", "array_length_change_allowed"])
def test_numeric_update_never_claims_creation_units_geometry_or_length_changes(scope_key):
    reply = updated(journal=False)
    reply["result"]["scope"][scope_key] = True
    with pytest.raises(ValueError):
        _validate(reply, wire(EXECUTE))


@pytest.mark.parametrize("status", ["accepted", "running", "completed", "unknown_or_expired"])
def test_new_operation_managed_status_does_not_claim_native_success(status):
    reply = envelope(snapshot(status))
    _validate_status(reply, STATUS_ARGS)
    assert "success" not in reply["result"]


def test_status_keeps_a_completed_historical_fault_without_native_fault_probe():
    previous = updated(journal=False)
    previous.update(ok=False, error="Original setter fault", error_code="native_database_fault", native_fault_latched=True, retry_safe=False)
    previous["result"].update(status="failed", success=False, complete=False)
    previous["result"]["progress"].update(native_fault_latched=True, outcome_uncertain=True, readback_complete=False)
    record = snapshot("completed", receipt=previous)
    record["http_status"] = 500
    _validate_status(envelope(record), STATUS_ARGS)


def test_old_copy_operation_journal_cannot_be_used_as_a_param_status_receipt():
    with pytest.raises(ValueError):
        _validate_status(envelope(copy_snapshot()), STATUS_ARGS)


@pytest.mark.parametrize("facts", [
    {"known_target_refno_verified": True, "known_target_refno": None},
    {"parameter_write_returned": True, "parameter_write_attempted": False},
    {"parameter_write_returned": True, "parameter_write_attempted": None},
])
def test_omitted_receipt_cannot_hide_contradictory_critical_facts(facts):
    row = snapshot("completed")
    row["critical_summary"].update(facts)
    with pytest.raises(ValueError):
        _validate_status(envelope(row), STATUS_ARGS)


def test_omitted_receipt_keeps_unknown_native_facts_nullable():
    row = snapshot("completed")
    row["critical_summary"].update(known_target_refno=NEW, known_target_refno_verified=True,
                                    parameter_write_attempted=None, parameter_write_returned=None)
    _validate_status(envelope(row), STATUS_ARGS)


def blocked_after_first_binding():
    reply = prepared()
    result = reply["result"]
    result.update(status="blocked", success=False, complete=False, scoped_revision=None,
        errors=[{"stage": "preflight", "code": "template_read_failed", "message": "Second native resolution failed"}])
    result["before"].update(template=None, owner=None, setable=None, parameter_bits=None)
    result["progress"]["no_change"] = None
    return reply


def test_first_typed_numeric_binding_survives_a_later_template_read_failure():
    reply = blocked_after_first_binding()
    _validate(reply, wire(PREPARE))
    assert reply["result"]["progress"]["known_target_refno"] == NEW
    assert reply["result"]["progress"]["known_target_refno_verified"] is True
    assert reply["result"]["before"]["template"] is None


@pytest.mark.parametrize("replacement", [None, core(NEW, "CATE"), core("=7/999", "SCOM")])
def test_positive_target_flag_requires_the_independent_first_binding(replacement):
    reply = blocked_after_first_binding()
    reply["result"]["before"]["target_binding"] = replacement
    with pytest.raises(ValueError):
        _validate(reply, wire(PREPARE))


def test_observed_wrong_base_type_is_legal_blocked_evidence_without_positive_binding():
    reply = blocked_after_first_binding()
    reply["result"]["before"]["target_binding"] = core(NEW, "CATE")
    reply["result"]["progress"].update(known_target_refno=None, known_target_refno_verified=None)
    _validate(reply, wire(PREPARE))


@pytest.mark.parametrize("side", ["before", "after"])
def test_ready_state_requires_early_and_later_full_core_identity_to_agree(side):
    reply = updated(journal=False)
    reply["result"][side]["target_binding"] = core("=7/999", "SCOM")
    with pytest.raises(ValueError):
        _validate(reply, wire(EXECUTE))


def test_legitimate_truncated_numeric_prefix_does_not_establish_full_array_coverage():
    reply = prepared()
    result = reply["result"]
    raw = result["before"]["template"]
    template_partial(raw)
    raw["parameters"].update(total_count=129, returned_count=128, truncated=True, complete=False,
        status="partial", errors={"output_limit": "Only PARAM prefix returned"}, values=[
            {"index": index, "value": float(index), "status": "observed", "error": None} for index in range(128)])
    result.update(status="blocked", success=False, complete=False, scoped_revision=None,
        errors=[{"stage": "preflight", "code": "numeric_slice_unknown", "message": "Numeric array exceeds output budget"}])
    result["before"]["parameter_bits"] = None
    result["progress"]["no_change"] = None
    _validate(reply, wire(PREPARE))
    result.update(status="prepared", success=True, complete=True, scoped_revision=REVISION, errors=[])
    with pytest.raises(ValueError):
        _validate(reply, wire(PREPARE))


def test_nonfinite_native_item_remains_unknown_and_cannot_be_replaced_by_known_bits():
    reply = prepared()
    result = reply["result"]
    raw = result["before"]["template"]
    template_partial(raw)
    value = raw["parameters"]
    value.update(status="partial", complete=False, errors={"non_finite": "Native numeric item is nonfinite"})
    value["values"][0].update(status="unknown", value=None, error="non_finite_native_value")
    result.update(status="blocked", success=False, complete=False, scoped_revision=None,
        errors=[{"stage": "preflight", "code": "numeric_slice_unknown", "message": "Native nonfinite value"}])
    result["before"]["parameter_bits"] = None
    result["progress"]["no_change"] = None
    _validate(reply, wire(PREPARE))
    result["before"]["parameter_bits"] = bits([0.0, 80.0, 125.5])
    with pytest.raises(ValueError):
        _validate(reply, wire(PREPARE))


def test_owner_name_partial_is_independent_of_required_typed_cate_identity():
    reply = updated(journal=False)
    for side in ("before", "after"):
        owner = reply["result"][side]["owner"]
        owner.update(name=None, name_error="Owner NAME getter unavailable", errors={"name": "Owner NAME getter unavailable"}, complete=False)
    _validate(reply, wire(EXECUTE))


@pytest.mark.parametrize("count", [1, 128])
def test_full_numeric_count_boundaries_support_successful_requested_replacement(count):
    values = [float(index + 1) for index in range(count)]
    args = {**EXECUTE, "parameters": values}
    reply = updated(values, journal=False)
    reply["result"]["before"] = evidence([0.0] * count)
    _validate(reply, wire(args))


@pytest.mark.parametrize("subnormal", [5e-324, -5e-324, 1.7976931348623157e308, -1.7976931348623157e308])
def test_finite_binary64_extremes_round_trip_as_explicit_bit_intent(subnormal):
    args = {**EXECUTE, "parameters": [subnormal, -0.0, 3.5]}
    reply = updated(args["parameters"], journal=False)
    _validate(reply, wire(args))

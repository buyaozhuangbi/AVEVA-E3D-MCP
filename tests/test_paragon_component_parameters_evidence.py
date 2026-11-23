"""Public PARAM and explicit copy-to-REF workflow using only mocked urlopen."""
from __future__ import annotations

from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import urllib.error
import urllib.parse
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.paragon_component_parameters import PATH, STATUS_PATH, STATUS_TOOL, TOOL, _validate
from aveva_e3d_mcp.tools import create_server
from test_paragon_component_copy import EXECUTE as COPY_ARGS, PATH as COPY_PATH, TOOL as COPY_TOOL, executed as copied
from test_paragon_component_parameters import (
    ARGS, BUILD, EXECUTE, NEW, PREPARE, REQUEST_ID, REVISION, SESSION, STATUS_ARGS, VALUES, bits, call, envelope,
    blocked_after_first_binding, evidence, prepared, reference_name_error, snapshot, template_partial, updated, wire,
)


SETTINGS = Settings(mode="bridge", version="E3D21", allow_commands=True)


class Wire:
    def __init__(self, reply, payload=None, *, status=None, status_tool=False, raw=None):
        self.reply, self.payload, self.status, self.status_tool, self.raw = reply, payload, status, status_tool, raw
        self.calls, self.original = [], deepcopy(reply)

    def __call__(self, request, **kwargs):
        assert not self.calls, "No automatic retry, diagnostics, feature or status read is permitted"
        url = urllib.parse.urlsplit(request.full_url)
        assert request.full_url.startswith(SETTINGS.bridge_url)
        assert url.path == (STATUS_PATH if self.status_tool else PATH)
        if self.status_tool:
            assert request.method == "GET" and request.data is None
            assert urllib.parse.parse_qs(url.query, strict_parsing=True) == {
                "request_id": [REQUEST_ID], "expected_session_id": [SESSION]}
        else:
            assert request.method == "POST" and not url.query
            assert request.get_header("Content-type") == "application/json"
            assert json.loads(request.data) == self.payload
        self.calls.append((request.method, url.path, request.data))
        body = io.BytesIO(self.raw if self.raw is not None else json.dumps(self.reply, allow_nan=False).encode("utf-8"))
        if self.status is not None:
            raise urllib.error.HTTPError(request.full_url, self.status, "Original PARAM failure", {}, body)
        return body


def run(reply, args=ARGS, *, status_tool=False, settings=SETTINGS, **options):
    source = Wire(reply, wire(args) if not status_tool else None, status_tool=status_tool, **options)
    server = create_server(settings)
    with patch("urllib.request.urlopen", side_effect=source) as transport:
        result = call(server, args, STATUS_TOOL if status_tool else TOOL)
    assert transport.call_count == len(source.calls) == 1
    assert reply == source.original
    return result


def test_public_catalog_freezes_closed_schema_without_caller_bits_and_read_only_status():
    with patch("urllib.request.urlopen", side_effect=AssertionError("No catalog I/O")) as transport:
        server = create_server(SETTINGS)
    transport.assert_not_called()
    writer = server._tools[TOOL][0]
    assert writer["annotations"] == {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False}
    assert writer["inputSchema"]["additionalProperties"] is False
    assert "parameter_bits" not in writer["inputSchema"]["properties"]
    assert server._tools[STATUS_TOOL][0]["annotations"]["readOnlyHint"] is True


@pytest.mark.parametrize("factory,args", [(prepared, ARGS), (prepared, PREPARE), (updated, EXECUTE),
                                         (lambda: updated(unchanged=True), EXECUTE)])
def test_real_public_consumer_keeps_native_receipt_and_exact_bit_intent(factory, args):
    reply = factory()
    _validate(reply, wire(args))
    result = run(reply, args)
    assert result.get("isError") is not True and result["structuredContent"] == reply
    assert reply["result"]["request"]["parameter_bits"][1] == "8000000000000000"
    assert json.loads(result["content"][0]["text"]) == reply


def test_public_net48_zero_serialization_loss_does_not_change_negative_zero_write_intent():
    reply = updated(journal=False)
    reply["result"]["request"]["parameters"][1] = 0.0
    reply["result"]["after"]["template"]["parameters"]["values"][1]["value"] = 0.0
    result = run(reply, EXECUTE)
    assert result.get("isError") is not True and result["structuredContent"] == reply
    assert reply["result"]["after"]["parameter_bits"][1] == "8000000000000000"


@pytest.mark.parametrize("bad", [
    {}, {**ARGS, "parameter_bits": bits(VALUES)}, {**ARGS, "parameters": []}, {**ARGS, "parameters": list(range(129))},
    {**ARGS, "parameters": [True]}, {**ARGS, "parameters": ["1.0"]}, {**ARGS, "parameters": [float("inf")]},
    {**ARGS, "refno": "CE"}, {**ARGS, "refno": "=01/1"}, {**ARGS, "refno": "=1/2147483648"},
    {**ARGS, "dry_run": False}, {**ARGS, "dry_run": 0},
    *[{**ARGS, key: None} for key in ("request_id", "expected_session_id", "expected_bridge_build", "expected_revision")],
    *[{**ARGS, key: "caller value"} for key in ("attribute", "units", "source", "recipe", "capture_after", "version")],
    {**EXECUTE, "request_id": REQUEST_ID.upper()}, {**EXECUTE, "expected_revision": "A" * 64},
])
def test_public_invalid_arguments_stop_before_any_native_request_or_token_generation(bad):
    server = create_server(SETTINGS)
    with patch("urllib.request.urlopen", side_effect=AssertionError("No I/O permitted")) as transport:
        result = call(server, bad)
    transport.assert_not_called()
    assert result.get("isError") is True


def test_public_execute_disabled_commands_prevents_even_preview_or_health_io():
    server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))
    with patch("urllib.request.urlopen", side_effect=AssertionError("No I/O permitted")) as transport:
        result = call(server, EXECUTE)
    transport.assert_not_called()
    assert result.get("isError") is True and "ALLOW_COMMANDS" in result["content"][0]["text"]


@pytest.mark.parametrize("mode,version", [("mock", "E3D21"), ("bridge", "E3D31")])
def test_public_unsupported_runtime_has_no_simulated_native_update(mode, version):
    server = create_server(Settings(mode=mode, version=version, allow_commands=True))
    with patch("urllib.request.urlopen", side_effect=AssertionError("No I/O permitted")) as transport:
        result = call(server, ARGS)
    transport.assert_not_called()
    assert result.get("isError") is True and "structuredContent" not in result


@pytest.mark.parametrize("kind", ["name", "reference", "unit_kind"])
def test_public_optional_metadata_partial_preserves_numeric_update_without_whole_template_gate(kind):
    reply = updated(journal=False)
    for side in ("before", "after"):
        raw = reply["result"][side]["template"]
        template_partial(raw)
        if kind == "name":
            raw["target"].update(name=None, name_error="NAME getter failed", errors={"name": "NAME getter failed"}, complete=False)
        elif kind == "reference":
            reference_name_error(raw)
        else:
            raw["parameters"].update(unit_kind=None, errors={"unit_kind": "Unit getter failed"}, complete=False, status="partial")
    _validate(reply, wire(EXECUTE))
    result = run(reply, EXECUTE)
    assert result.get("isError") is not True and result["structuredContent"] == reply
    assert reply["result"]["before"]["template"]["complete"] is False
    assert reply["result"]["scope"]["units_verified"] is False


@pytest.mark.parametrize("status", ["accepted", "running", "completed", "unknown_or_expired"])
def test_public_managed_status_does_not_upgrade_journal_states_to_setter_success(status):
    reply = envelope(snapshot(status))
    result = run(reply, STATUS_ARGS, status_tool=True,
                 settings=Settings(mode="bridge", version="E3D21", allow_commands=False))
    assert result.get("isError") is not True and result["structuredContent"] == reply
    assert "success" not in reply["result"]


@pytest.mark.parametrize("facts", [
    {"known_target_refno_verified": True, "known_target_refno": None},
    {"parameter_write_returned": True, "parameter_write_attempted": False},
    {"parameter_write_returned": True, "parameter_write_attempted": None},
])
def test_public_omitted_receipt_summary_cannot_claim_logically_contradictory_facts(facts):
    row = snapshot("completed")
    row["critical_summary"].update(facts)
    result = run(envelope(row), STATUS_ARGS, status_tool=True)
    assert result.get("isError") is True and "structuredContent" not in result


def test_public_status_historical_fault_is_managed_data_with_target_ref_preserved():
    previous = updated(journal=False)
    previous.update(ok=False, error="Original setter fault", error_code="native_database_fault", native_fault_latched=True, retry_safe=False)
    previous["result"].update(status="failed", success=False, complete=False)
    previous["result"]["progress"].update(outcome_uncertain=True, native_fault_latched=True, readback_complete=False)
    row = snapshot("completed", receipt=previous)
    row["http_status"] = 500
    raw = envelope(row)
    result = run(raw, STATUS_ARGS, status_tool=True)
    assert result.get("isError") is not True and result["structuredContent"] == raw
    assert row["critical_summary"]["known_target_refno"] == NEW


@pytest.mark.parametrize("http_status", [200, 404, 500])
@pytest.mark.parametrize("container", ["details", "diagnostics", "result"])
def test_public_fault_stop_keeps_original_nested_container_and_never_auto_queries_status(http_status, container):
    raw = {"ok": False, "version": "E3D21", "session_id": SESSION, "bridge_build": BUILD,
           "error": "Original native PARAM fault", container: [{"nested": {"native_fault_latched": True}, "sdk_code": 538}],
           "private": "not forwarded"}
    result = run(raw, EXECUTE, status=None if http_status == 200 else http_status)
    assert result.get("isError") is True
    evidence = result["structuredContent"]
    assert evidence[container] == raw[container] and "Original native PARAM fault" in evidence["error"]
    assert "private" not in evidence and "native_feature_unavailable" not in json.dumps(evidence)


@pytest.mark.parametrize("outcome,http_status", [("failed", 500), ("rejected", 409), ("duplicate", 409)])
def test_public_failure_preserves_complete_original_progress_and_target_binding(outcome, http_status):
    result = updated(journal=False)["result"] if outcome == "failed" else prepared()["result"]
    result.update(dry_run=False, request_id=REQUEST_ID, status=outcome, success=False, complete=False)
    if outcome == "failed":
        result["progress"].update(outcome_uncertain=True, readback_complete=False)
    raw = {"ok": False, "version": "E3D21", "session_id": SESSION, "bridge_build": BUILD, "error": "Original native result", "result": result}
    response = run(raw, EXECUTE, status=http_status)
    assert response.get("isError") is True and response["structuredContent"]["result"] == result
    assert result["progress"]["known_target_refno"] == NEW


def mutate(reply, path, value):
    target = reply
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = deepcopy(value)


@pytest.mark.parametrize("path,value", [
    (("version",), "E3D31"), (("session_id",), "another-session"), (("bridge_build",), "another-build"),
    (("result", "request", "parameter_bits", 1), "0000000000000000"),
    (("result", "after", "parameter_bits", 1), "0000000000000000"),
    (("result", "after", "template", "parameters", "values", 0, "value"), 999.0),
    (("result", "after", "owner", "refno"), "=7/999"),
    (("result", "after", "context", "instance_binding"), "c" * 64),
    (("result", "after", "context", "current_element"), None),
    (("result", "progress", "parameter_write_returned"), False),
    (("result", "progress", "parameters_equal_request"), False),
    (("result", "progress", "no_change"), True),
    (("result", "scope", "target_creation_verified"), True),
    (("result", "scope", "units_verified"), True),
    (("result", "scope", "geometry_equivalence_verified"), True),
])
def test_public_forged_summary_does_not_replace_raw_numeric_identity_or_scope_checks(path, value):
    reply = updated(journal=False)
    mutate(reply, path, value)
    result = run(reply, EXECUTE)
    assert result.get("isError") is True
    assert result["structuredContent"]["outcome_uncertain"] is True and result["structuredContent"]["retry_safe"] is False
    assert "result" not in result["structuredContent"]


@pytest.mark.parametrize("final", ["success", "setter_fault", "timeout"])
def test_explicit_copy_then_parameter_update_binds_only_verified_new_ref_and_keeps_copy_on_failure(final):
    copy_receipt = copied(journal=False)
    original_copy = deepcopy(copy_receipt)
    param_preview = prepared()
    param_revision = "b" * 64
    param_preview["result"]["scoped_revision"] = param_revision
    param_execute_args = {**EXECUTE, "expected_revision": param_revision}
    param_response = updated(journal=False)
    param_response["result"]["scoped_revision"] = param_revision
    if final == "setter_fault":
        param_response.update(ok=False, error="Original setter fault", error_code="native_database_fault", native_fault_latched=True, retry_safe=False)
        param_response["result"].update(status="failed", success=False, complete=False)
        param_response["result"]["progress"].update(native_fault_latched=True, outcome_uncertain=True, readback_complete=False)
    expected = [(COPY_PATH, COPY_ARGS), (PATH, wire(PREPARE)), (PATH, wire(param_execute_args))]
    calls = []

    def urlopen(request, **kwargs):
        index = len(calls)
        assert index < len(expected), "No failure recovery read or replay"
        url = urllib.parse.urlsplit(request.full_url)
        assert request.method == "POST" and not url.query
        payload = json.loads(request.data)
        assert (url.path, payload) == expected[index]
        calls.append((url.path, payload))
        if index == 2 and final == "timeout":
            raise TimeoutError("PARAM timeout after copy")
        raw = [copy_receipt, param_preview, param_response][index]
        body = io.BytesIO(json.dumps(raw, allow_nan=False).encode("utf-8"))
        if index == 2 and final == "setter_fault":
            raise urllib.error.HTTPError(request.full_url, 500, "Native setter fault", {}, body)
        return body

    server = create_server(SETTINGS)
    with patch("urllib.request.urlopen", side_effect=urlopen) as transport:
        copied_result = call(server, COPY_ARGS, COPY_TOOL)
        native = copied_result["structuredContent"]["result"]
        assert native["success"] is True and native["progress"]["known_new_refno_verified"] is True
        verified_ref = native["progress"]["known_new_refno"]
        assert verified_ref == NEW
        preview_result = call(server, {"refno": verified_ref, "parameters": VALUES})
        assert preview_result["structuredContent"]["result"]["scoped_revision"] == param_revision
        update_result = call(server, {**param_execute_args, "refno": verified_ref})
    assert transport.call_count == len(calls) == 3
    assert copied_result["structuredContent"] == original_copy and copy_receipt == original_copy
    assert param_revision != native["scoped_revision"]
    if final == "success":
        assert update_result.get("isError") is not True
        assert update_result["structuredContent"]["result"]["scope"]["target_creation_verified"] is False
    else:
        assert update_result.get("isError") is True
        assert update_result["structuredContent"]["outcome_uncertain"] is True
        assert native["progress"]["known_new_refno"] == NEW


def test_unverified_copy_receipt_never_supplies_a_target_for_the_following_writer():
    raw = copied(journal=False)
    raw["result"]["progress"]["known_new_refno_verified"] = False
    server = create_server(SETTINGS)
    with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(raw).encode("utf-8"))) as transport:
        result = call(server, COPY_ARGS, COPY_TOOL)
    transport.assert_called_once()
    assert result.get("isError") is True
    assert "known_new_refno" not in result["structuredContent"]


def test_public_first_binding_is_retained_when_later_template_resolution_fails():
    reply = blocked_after_first_binding()
    result = run(reply)
    assert result.get("isError") is True and result["structuredContent"] == reply
    assert reply["result"]["before"]["target_binding"]["refno"] == NEW
    assert reply["result"]["progress"]["known_target_refno_verified"] is True
    assert reply["result"]["before"]["template"] is None


@pytest.mark.parametrize("which", ["before", "after"])
def test_public_forged_first_proof_cannot_be_replaced_by_summary_flags(which):
    reply = updated(journal=False)
    reply["result"][which]["target_binding"] = None
    result = run(reply, EXECUTE)
    assert result.get("isError") is True
    assert result["structuredContent"]["outcome_uncertain"] is True


@pytest.mark.parametrize("omission", ["receipt_too_large", "receipt_not_pure_json", "serialization_failed", "receipt_missing"])
def test_public_valid_omitted_history_keeps_critical_target_and_attempt_facts(omission):
    row = snapshot("completed")
    row["receipt_omitted_reason"] = omission
    row["critical_summary"].update(known_target_refno=NEW, known_target_refno_verified=True,
        parameter_write_attempted=True, parameter_write_returned=True, outcome_uncertain=True, native_fault_latched=True)
    raw = envelope(row)
    result = run(raw, STATUS_ARGS, status_tool=True)
    assert result.get("isError") is not True and result["structuredContent"] == raw
    assert row["receipt"] is None and "success" not in row


@pytest.mark.parametrize("late_check,code,message", [
    ("context", "parameter_context_changed", "The native project/MDB/typed CE context changed during the parameter observation."),
    ("module", "not_paragon_81", "The native module must be exactly PARAGON/81."),
])
@pytest.mark.parametrize("parameters,no_change", [(VALUES, False), ([0.0, 80.0, 125.5], True)])
def test_public_late_preflight_conflict_keeps_complete_before_and_observed_no_change(late_check, code, message, parameters, no_change):
    reply = prepared(parameters)
    result = reply["result"]
    captured_before = deepcopy(result["before"])
    terminal_error = {"stage": "preflight", "code": code, "message": message}
    result.update(status="blocked", success=False, complete=False, scoped_revision=None, errors=[terminal_error])
    args = {"refno": NEW, "parameters": parameters}
    _validate(reply, wire(args))
    observed = run(reply, args)
    assert observed.get("isError") is True and observed["structuredContent"] == reply
    assert result["before"] == captured_before and result["errors"] == [terminal_error]
    assert result["progress"]["no_change"] is no_change
    assert result["progress"]["known_target_refno_verified"] is True
    assert result["progress"]["parameter_write_attempted"] is False
    assert result["progress"]["parameter_write_returned"] is False
    assert result["progress"]["readback_complete"] is False
    assert result["after"] is None and result["scoped_revision"] is None
    forged = deepcopy(reply)
    forged["result"].update(status="prepared", success=True, complete=True, scoped_revision=REVISION)
    with pytest.raises(ValueError):
        _validate(forged, wire(args))

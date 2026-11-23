"""Public create_server copy/status tests replacing only real urlopen I/O."""
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
from aveva_e3d_mcp.paragon_component_copy import PATH, STATUS_PATH, STATUS_TOOL, TOOL, _validate
from aveva_e3d_mcp.tools import create_server
from test_paragon_component_copy import (
    ARGS, BUILD, EXECUTE, NEW, PARENT, PREPARE, REQUEST_ID, SESSION, SOURCE, STATUS_ARGS,
    assert_template_valid, call, envelope, executed, native_null_reference, prepared, reference_name_error, snapshot,
    template_partial, unsupported,
)


SETTINGS = Settings(mode="bridge", version="E3D21", allow_commands=True)


class Wire:
    def __init__(self, reply, payload=PREPARE, *, status=None, status_tool=False, raw=None):
        self.reply, self.payload, self.status, self.status_tool, self.raw = reply, payload, status, status_tool, raw
        self.calls = []
        self.original = deepcopy(reply)

    def __call__(self, request, **kwargs):
        assert not self.calls, "Copy/status permits exactly one explicit wire request"
        url = urllib.parse.urlsplit(request.full_url)
        assert request.full_url.startswith(SETTINGS.bridge_url)
        assert url.path == (STATUS_PATH if self.status_tool else PATH)
        if self.status_tool:
            assert request.method == "GET" and request.data is None
            assert urllib.parse.parse_qs(url.query, strict_parsing=True) == {
                "request_id": [REQUEST_ID], "expected_session_id": [SESSION]}
        else:
            assert request.method == "POST" and not url.query
            assert json.loads(request.data) == self.payload
            assert request.get_header("Content-type") == "application/json"
        self.calls.append((request.method, url.path, request.data))
        raw = self.raw if self.raw is not None else json.dumps(self.reply, allow_nan=False).encode("utf-8")
        body = io.BytesIO(raw)
        if self.status is not None:
            raise urllib.error.HTTPError(request.full_url, self.status, "Original HTTP failure", {}, body)
        return body


def run(reply, arguments=ARGS, *, settings=SETTINGS, status_tool=False, **options):
    payload = EXECUTE if arguments.get("dry_run") is False else PREPARE
    wire = Wire(reply, payload, status_tool=status_tool, **options)
    server = create_server(settings)
    with patch("urllib.request.urlopen", side_effect=wire) as transport:
        result = call(server, arguments, STATUS_TOOL if status_tool else TOOL)
    assert transport.call_count == len(wire.calls) == 1
    assert reply == wire.original
    return result


def test_public_catalog_adds_exact_copy_and_read_only_status_contracts():
    with patch("urllib.request.urlopen", side_effect=AssertionError("Catalog is local")) as transport:
        server = create_server(SETTINGS)
    transport.assert_not_called()
    copy_definition = server._tools[TOOL][0]
    status_definition = server._tools[STATUS_TOOL][0]
    assert copy_definition["annotations"] == {"readOnlyHint": False, "destructiveHint": True,
                                               "idempotentHint": False, "openWorldHint": False}
    assert status_definition["annotations"] == {"readOnlyHint": True, "destructiveHint": False,
                                                 "idempotentHint": True, "openWorldHint": False}
    assert copy_definition["inputSchema"]["additionalProperties"] is False
    assert status_definition["inputSchema"]["additionalProperties"] is False


@pytest.mark.parametrize("factory,args", [(prepared, ARGS), (prepared, PREPARE), (executed, EXECUTE)])
def test_public_prepare_and_execute_keep_exact_native_receipt_with_no_extra_reads(factory, args):
    reply = factory()
    _validate(reply, PREPARE if args.get("dry_run", True) else EXECUTE)
    result = run(reply, args)
    assert result.get("isError") is not True and result["structuredContent"] == reply
    assert json.loads(result["content"][0]["text"]) == reply
    assert "source" not in reply["result"] and "preview_request" not in reply["result"]
    if not args.get("dry_run", True):
        progress = result["structuredContent"]["result"]["progress"]
        assert progress["known_new_refno"] == NEW and progress["known_new_refno_verified"] is True
        assert progress["readback_complete"] is True and progress["retry_safe"] is False


def test_public_execute_disabled_commands_prevents_all_native_io():
    server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))
    with patch("urllib.request.urlopen", side_effect=AssertionError("No native I/O")) as transport:
        result = call(server, EXECUTE)
    transport.assert_not_called()
    assert result["isError"] is True
    assert "ALLOW_COMMANDS" in result["content"][0]["text"]


@pytest.mark.parametrize("bad", [
    {}, {**ARGS, "source_refno": "=2147483648/0"}, {**ARGS, "parent_refno": "=01/1"},
    {**ARGS, "name": "/A/1"}, {**ARGS, "name": "/A;NEW"}, {**ARGS, "name": "/A\n"},
    {**ARGS, "name": "/" + "A" * 50}, {**ARGS, "dry_run": "false"}, {**ARGS, "dry_run": False},
    *[{**ARGS, key: None} for key in ("request_id", "expected_session_id", "expected_bridge_build", "expected_revision")],
    *[{**ARGS, key: "caller supplied"} for key in ("source", "command", "capture_after", "version", "timeout")],
    {**EXECUTE, "request_id": REQUEST_ID.upper()}, {**EXECUTE, "expected_revision": "A" * 64},
    {**EXECUTE, "expected_session_id": "x\n"}, {**EXECUTE, "source_refno": PARENT},
])
def test_public_closed_arguments_fail_before_any_wire_or_generated_token(bad):
    server = create_server(SETTINGS)
    with patch("urllib.request.urlopen", side_effect=AssertionError("No native I/O")) as transport:
        result = call(server, bad)
    transport.assert_not_called()
    assert result.get("isError") is True


@pytest.mark.parametrize("status", ["accepted", "running", "completed", "unknown_or_expired"])
def test_public_status_is_read_only_and_journal_completion_is_not_native_success(status):
    raw = envelope(snapshot(status))
    observed = run(raw, STATUS_ARGS, status_tool=True,
                   settings=Settings(mode="bridge", version="E3D21", allow_commands=False))
    assert observed.get("isError") is not True and observed["structuredContent"] == raw
    assert "success" not in raw["result"]


def test_public_status_keeps_completed_fault_and_all_original_native_progress():
    previous = executed(journal=False)
    previous.update(ok=False, error="Original native copy fault", native_fault_latched=True,
                    error_code="native_database_fault", retry_safe=False)
    previous["result"].update(status="failed", success=False, complete=False)
    previous["result"]["progress"].update(native_fault_latched=True, readback_complete=False, outcome_uncertain=True)
    raw = envelope(snapshot("completed", reply=previous))
    raw["result"]["http_status"] = 500
    observed = run(raw, STATUS_ARGS, status_tool=True)
    assert observed.get("isError") is not True and observed["structuredContent"] == raw
    assert raw["result"]["receipt"]["result"]["progress"]["known_new_refno"] == NEW
    assert raw["result"]["receipt"]["result"]["progress"]["outcome_uncertain"] is True


@pytest.mark.parametrize("failure_status,http_status", [("failed", 500), ("rejected", 409), ("duplicate", 409)])
def test_public_native_failures_keep_error_and_complete_original_result(failure_status, http_status):
    result = executed(journal=False)["result"] if failure_status == "failed" else prepared()["result"]
    result.update(dry_run=False, request_id=REQUEST_ID, status=failure_status, success=False, complete=False)
    if failure_status == "failed":
        result["progress"].update(readback_complete=False, outcome_uncertain=True)
    else:
        result.update(before=None, scoped_revision=None, journal=snapshot("completed", reply=executed(journal=False)))
    raw = {"ok": False, "version": "E3D21", "session_id": SESSION, "bridge_build": BUILD,
           "error": "Original native outcome", "result": result, "private": "do not expose"}
    response = run(raw, EXECUTE, status=http_status)
    assert response.get("isError") is True
    structured = response["structuredContent"]
    assert structured["ok"] is False and "Original native outcome" in structured["error"]
    assert structured["result"] == result and "private" not in structured
    assert "status_tool" not in structured and "native_feature_unavailable" not in json.dumps(structured)


@pytest.mark.parametrize("status", [200, 404, 500])
@pytest.mark.parametrize("container", ["details", "diagnostics", "result"])
def test_public_fault_stop_preserves_original_containers_without_health_or_journal_retry(status, container):
    raw = {"ok": False, "version": "E3D21", "session_id": SESSION, "bridge_build": BUILD,
           "error": "Original fault", container: [{"nested": {"native_fault_latched": True}, "sdk_code": 538}],
           "private": "do not expose"}
    observed = run(raw, EXECUTE, status=None if status == 200 else status)
    assert observed.get("isError") is True
    evidence = observed["structuredContent"]
    assert evidence[container] == raw[container] and "Original fault" in evidence["error"]
    assert "private" not in evidence and "native_feature_unavailable" not in json.dumps(evidence)


def mutation(reply, path, value):
    target = reply
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = deepcopy(value)


@pytest.mark.parametrize("path,value", [
    (("version",), "E3D31"), (("bridge_build",), "old-build"), (("session_id",), "another-session"),
    (("result", "progress", "known_new_refno"), SOURCE),
    (("result", "progress", "native_run_success"), False),
    (("result", "progress", "known_new_refno_verified"), False),
    (("result", "progress", "readback_complete"), False),
    (("result", "after", "new_owner", "refno"), "=7/999"),
    (("result", "after", "members_complete"), False),
    (("result", "before", "members_complete"), False),
    (("result", "after", "context", "instance_binding"), "c" * 64),
    (("result", "after", "context", "current_element"), None),
    (("result", "after", "new_template", "parameters", "values", 1, "value"), 82.0),
    (("result", "after", "new_template", "target", "name"), "/DIFFERENT"),
    (("result", "after", "source_template", "target", "name"), "/CHANGED"),
    (("result", "after", "new_template", "target", "actual_type", "short_name"), "U_SCOM"),
    (("result", "scope", "whole_clone_fingerprint"), True),
    (("result", "scope", "engineering_validated"), True),
    (("result", "scope", "savework_performed"), True),
])
def test_public_cannot_upgrade_forged_execution_success_from_raw_evidence(path, value):
    reply = executed(journal=False)
    mutation(reply, path, value)
    observed = run(reply, EXECUTE)
    assert observed.get("isError") is True
    evidence = observed["structuredContent"]
    assert evidence["outcome_uncertain"] is True and evidence["retry_safe"] is False
    assert "result" not in evidence


@pytest.mark.parametrize("which", ["prepare", "execute"])
def test_public_unit_kind_only_partial_keeps_complete_numeric_value_slice(which):
    reply = prepared() if which == "prepare" else executed(journal=False)
    result = reply["result"]
    templates = [result["before"]["source_template"]]
    if result["after"] is not None:
        templates += [result["after"]["source_template"], result["after"]["new_template"]]
    for raw in templates:
        raw.update(success=False, complete=False)
        raw["parameters"].update(unit_kind=None, errors={"unit_kind": "Native metadata unavailable"},
                                 status="partial", complete=False)
    payload = PREPARE if which == "prepare" else EXECUTE
    _validate(reply, payload)
    observed = run(reply, payload)
    assert observed.get("isError") is not True and observed["structuredContent"] == reply
    assert result["success"] is True and result["complete"] is True
    assert result["before"]["source_template"]["complete"] is False


def test_public_blocked_preparation_keeps_legitimate_partial_observations_without_execution():
    reply = prepared()
    result = reply["result"]
    result.update(status="blocked", success=False, complete=False, scoped_revision=None)
    result["before"]["creatable"] = False
    result["errors"] = [{"stage": "preflight", "code": "not_creatable", "message": "SDK creatability is false"}]
    observed = run(reply)
    assert observed.get("isError") is True and observed["structuredContent"] == reply
    assert result["progress"]["creation_attempted"] is False


@pytest.mark.parametrize("field", ["version", "session_id", "bridge_build"])
def test_public_missing_native_identity_never_gets_a_transport_fallback(field):
    reply = executed(journal=False)
    del reply[field]
    observed = run(reply, EXECUTE)
    assert observed.get("isError") is True
    assert observed["structuredContent"]["outcome_uncertain"] is True


@pytest.mark.parametrize("raw", [b"offline", b"[]", b'{"ok":true}', b'{"ok":false,"error":null}',
                                 b'{"ok":true,"result":NaN}'])
def test_public_invalid_response_requires_manual_status_inspection_and_never_auto_reads_it(raw):
    observed = run({}, EXECUTE, raw=raw)
    assert observed.get("isError") is True
    assert observed["structuredContent"]["outcome_uncertain"] is True
    assert observed["structuredContent"]["retry_safe"] is False


def test_public_status_session_pin_is_independent_of_saved_receipt_identity():
    reply = envelope(snapshot("completed", reply=executed(journal=False)))
    reply["session_id"] = "another-session"
    observed = run(reply, STATUS_ARGS, status_tool=True)
    assert observed.get("isError") is True
    assert "structuredContent" not in observed


@pytest.mark.parametrize("array,mixed", [(False, False), (True, False), (True, True)])
def test_public_copy_preserves_observed_native_null_values_without_inventing_unknowns(array, mixed):
    reply = executed(journal=False)
    result = reply["result"]
    for raw in (result["before"]["source_template"], result["after"]["source_template"], result["after"]["new_template"]):
        native_null_reference(raw, array=array, mixed=mixed)
        assert_template_valid(raw)
    observed = run(reply, EXECUTE)
    assert observed.get("isError") is not True and observed["structuredContent"] == reply
    assert result["progress"]["copied_references_equal"] is True


def test_public_copy_requires_observed_values_when_native_reference_getter_failed():
    reply = executed(journal=False)
    result = reply["result"]
    for raw in (result["before"]["source_template"], result["after"]["source_template"]):
        native_null_reference(raw)
    raw = result["after"]["new_template"]
    template_partial(raw)
    raw["references"]["PTREF"].update(status="unknown", complete=False, total_count=None, returned_count=0,
        truncated=None, inspection_limited=None, native_array_materialized=False, values=[],
        errors={"value": "Native reference getter failed"})
    assert_template_valid(raw)
    observed = run(reply, EXECUTE)
    assert observed.get("isError") is True
    assert observed["structuredContent"]["outcome_uncertain"] is True


@pytest.mark.parametrize("type_name", [False, True])
def test_public_copy_reference_name_partial_keeps_independent_numeric_and_type_slice(type_name):
    reply = executed(journal=False)
    result = reply["result"]
    for raw in (result["before"]["source_template"], result["after"]["source_template"], result["after"]["new_template"]):
        reference_name_error(raw, type_name=type_name)
        assert_template_valid(raw)
    observed = run(reply, EXECUTE)
    assert observed.get("isError") is not True and observed["structuredContent"] == reply


def test_public_blocked_copy_preserves_all_unsupported_fixed_references_without_false_coverage():
    reply = prepared()
    result = reply["result"]
    raw = result["before"]["source_template"]
    for name in raw["references"]:
        unsupported(raw, name)
    assert_template_valid(raw)
    result.update(status="blocked", success=False, complete=False, scoped_revision=None)
    result["errors"] = [{"stage": "preflight", "code": "no_supported_reference_coverage", "message": "No observed supported reference slice"}]
    observed = run(reply)
    assert observed.get("isError") is True and observed["structuredContent"] == reply
    assert result["progress"]["creation_attempted"] is False

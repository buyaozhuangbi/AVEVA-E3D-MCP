"""Preserve PARAM write errors, native facts and manual status ownership."""
from __future__ import annotations

from copy import deepcopy
import http.client
import io
import json
from pathlib import Path
import sys
import urllib.error
import urllib.parse
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError, _response_failure_diagnostics
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.paragon_component_parameters import PATH, STATUS_PATH, paragon_component_parameters_set, paragon_component_parameters_status
from test_paragon_component_parameters import (
    BUILD, EXECUTE, PREPARE, REQUEST_ID, SESSION, STATUS_ARGS, envelope, prepared, snapshot, updated, wire,
)


SETTINGS = Settings(mode="bridge", version="E3D21", allow_commands=True)
PROVENANCE = {"version": "E3D21", "session_id": SESSION, "bridge_build": BUILD}
MARKERS = [
    {"native_fault": True}, {"native_database_fault": True}, {"has_fault": True}, {"native_fault_latched": True},
    {"error_code": "native_fault"}, {"error_code": "native_database_fault"}, {"code": "native_database_fault"},
]


def response(value):
    return io.BytesIO(json.dumps(value, allow_nan=False).encode("utf-8"))


def http_error(status, body, path=PATH):
    return urllib.error.HTTPError(SETTINGS.bridge_url + path, status, "Original failure", {}, body)


def one_post(transport, args):
    transport.assert_called_once()
    request = transport.call_args.args[0]
    assert request.method == "POST" and request.full_url == SETTINGS.bridge_url + PATH
    assert json.loads(request.data) == wire(args)


class RecordingBridge:
    settings = SETTINGS

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def _selected_version(self, version):
        assert version is None
        return "E3D21"

    def _request(self, method, path, payload, *, version):
        self.calls.append((method, path, payload, version))
        if isinstance(self.reply, BridgeError):
            raise self.reply
        return self.reply

    def __getattr__(self, name):
        raise AssertionError(f"No recovery I/O is allowed: {name}")


@pytest.mark.parametrize("status", [200, 404, 409, 500])
@pytest.mark.parametrize("container", [None, "details", "diagnostics", "result"])
@pytest.mark.parametrize("marker", MARKERS)
@pytest.mark.parametrize("args", [PREPARE, EXECUTE])
def test_nested_faults_keep_original_container_and_http_cause_before_any_fallback(status, container, marker, args):
    fault = deepcopy(marker) if container is None else {container: [{"sdk_code": 538, "fault": deepcopy(marker)}]}
    raw = {"ok": False, **PROVENANCE, "error": "Original PARAM fault", **fault, "retry_safe": False, "private": "not forwarded"}
    body = response(raw)
    cause = http_error(status, body)
    send = {"return_value": body} if status == 200 else {"side_effect": cause}
    with patch("urllib.request.urlopen", **send) as transport, pytest.raises(BridgeError) as caught:
        paragon_component_parameters_set(BridgeClient(SETTINGS), args)
    one_post(transport, args)
    assert caught.value.__cause__ is (None if status == 200 else cause)
    assert caught.value.http_status == (None if status == 200 else status)
    assert "Original PARAM fault" in str(caught.value)
    assert all(caught.value.diagnostics[key] == value for key, value in {**PROVENANCE, **fault, "retry_safe": False}.items())
    assert "private" not in caught.value.diagnostics


@pytest.mark.parametrize("flags", [{}, {"transport_error": True}, {"response_invalid": True}, {"transport_error": True, "response_invalid": True}])
def test_consumer_preserves_bridge_error_object_message_cause_and_existing_native_flags(flags):
    raw = {"result": {"progress": {"known_target_refno": "=7/103", "parameter_write_returned": True, "native_fault_latched": True}},
           "details": [{"native_database_fault": True}], "retry_safe": False}
    original = BridgeError("Original exact error", http_status=404, diagnostics=raw, **flags)
    cause = http_error(404, response({"ok": False, "error": "original"}))
    original.__cause__ = cause
    bridge = RecordingBridge(original)
    with pytest.raises(BridgeError) as caught:
        paragon_component_parameters_set(bridge, EXECUTE)
    assert caught.value is original and caught.value.diagnostics is raw and caught.value.__cause__ is cause
    assert str(caught.value) == "Original exact error" and caught.value.http_status == 404
    assert raw["result"]["progress"]["parameter_write_returned"] is True
    assert bridge.calls == [("POST", PATH, wire(EXECUTE), "E3D21")]
    if flags:
        assert caught.value.outcome_uncertain is True


@pytest.mark.parametrize("status", [400, 404, 405, 409, 423, 500])
def test_ordinary_native_failure_keeps_full_progress_and_target_ref(status):
    result = updated(journal=False)["result"]
    result.update(status="failed", success=False, complete=False)
    result["progress"].update(outcome_uncertain=True, readback_complete=False)
    raw = {"ok": False, **PROVENANCE, "error": "Original readback failure", "result": result}
    cause = http_error(status, response(raw))
    with patch("urllib.request.urlopen", side_effect=cause) as transport, pytest.raises(BridgeError) as caught:
        paragon_component_parameters_set(BridgeClient(SETTINGS), EXECUTE)
    one_post(transport, EXECUTE)
    assert caught.value.__cause__ is cause and caught.value.http_status == status
    assert caught.value.diagnostics["result"] == result and caught.value.outcome_uncertain is True
    assert caught.value.diagnostics["result"]["progress"]["known_target_refno"] == "=7/103"
    assert caught.value.diagnostics["retry_safe"] is False


@pytest.mark.parametrize("status", ["rejected", "duplicate"])
def test_rejected_submission_does_not_inherit_old_journal_write_attempt(status):
    result = prepared()["result"]
    result.update(dry_run=False, request_id=REQUEST_ID, status=status, success=False, complete=False,
                  journal=snapshot("completed", receipt=updated(journal=False)))
    raw = {"ok": False, **PROVENANCE, "error": "Original admission refusal", "result": result}
    cause = http_error(409, response(raw))
    with patch("urllib.request.urlopen", side_effect=cause) as transport, pytest.raises(BridgeError) as caught:
        paragon_component_parameters_set(BridgeClient(SETTINGS), EXECUTE)
    one_post(transport, EXECUTE)
    assert caught.value.__cause__ is cause and caught.value.outcome_uncertain is False
    assert caught.value.diagnostics["result"] == result
    assert result["journal"]["critical_summary"]["parameter_write_attempted"] is True
    assert result["this_submission_executed"] is False


@pytest.mark.parametrize("args", [PREPARE, EXECUTE])
@pytest.mark.parametrize("failure", [TimeoutError("offline timeout"), urllib.error.URLError("offline"), OSError("offline"),
                                   http.client.IncompleteRead(b'{"ok":', 12)])
def test_transport_failure_never_retries_reads_health_or_automatically_queries_status(args, failure):
    with patch("urllib.request.urlopen", side_effect=failure) as transport, pytest.raises(BridgeError) as caught:
        paragon_component_parameters_set(BridgeClient(SETTINGS), args)
    one_post(transport, args)
    assert caught.value.transport_error and caught.value.__cause__ is failure
    assert caught.value.outcome_uncertain is (not args["dry_run"])
    if not args["dry_run"]:
        assert caught.value.diagnostics["outcome_uncertain"] is True and caught.value.diagnostics["retry_safe"] is False


@pytest.mark.parametrize("args", [PREPARE, EXECUTE])
@pytest.mark.parametrize("raw", [b"missing", b"null", b"[]", b'{"ok":true}', b'{"ok":false,"error":null}', b'{"ok":true,"result":NaN}'])
def test_invalid_response_preserves_uncertainty_instead_of_optional_feature_absence(args, raw):
    with patch("urllib.request.urlopen", return_value=io.BytesIO(raw)) as transport, pytest.raises(BridgeError) as caught:
        paragon_component_parameters_set(BridgeClient(SETTINGS), args)
    one_post(transport, args)
    assert caught.value.response_invalid is True and caught.value.outcome_uncertain is (not args["dry_run"])


@pytest.mark.parametrize("field", ["version", "session_id", "bridge_build"])
def test_missing_native_identity_is_not_supplied_from_configuration(field):
    raw = updated(journal=False)
    del raw[field]
    with patch("urllib.request.urlopen", return_value=response(raw)) as transport, pytest.raises(BridgeError) as caught:
        paragon_component_parameters_set(BridgeClient(SETTINGS), EXECUTE)
    one_post(transport, EXECUTE)
    assert caught.value.response_invalid is True and caught.value.outcome_uncertain is True


@pytest.mark.parametrize("path", [("status",), ("before", "template", "parameters", "status"), ("journal", "status")])
@pytest.mark.parametrize("bad", [None, True, 1, [], {}, ["updated"]])
def test_malformed_nested_json_types_cannot_escape_execution_uncertainty(path, bad):
    raw = updated()
    target = raw["result"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = bad
    with patch("urllib.request.urlopen", return_value=response(raw)) as transport, pytest.raises(BridgeError) as caught:
        paragon_component_parameters_set(BridgeClient(SETTINGS), EXECUTE)
    one_post(transport, EXECUTE)
    assert caught.value.response_invalid and caught.value.outcome_uncertain


def test_exact_route_diagnostics_keep_all_parameter_progress_without_expanding_other_routes():
    raw = {**PROVENANCE, "result": {"progress": {"known_target_refno": "=7/103", "parameter_write_returned": True}},
           "details": {"native_fault_latched": True}, "retry_safe": False, "private": "not permitted"}
    for path in (PATH, STATUS_PATH):
        assert _response_failure_diagnostics(path, raw) == {key: value for key, value in raw.items() if key != "private"}
    for path in (PATH + "/", PATH + "-other", STATUS_PATH + "/", STATUS_PATH + "/other"):
        assert _response_failure_diagnostics(path, raw) == {}


def test_raw_embedded_fault_is_retained_before_any_normal_shape_projection():
    raw = {"ok": True, "version": "not-E3D21", "result": [{"fault": {"native_fault_latched": True}}]}
    bridge = RecordingBridge(raw)
    with pytest.raises(BridgeError) as caught:
        paragon_component_parameters_set(bridge, EXECUTE)
    assert caught.value.diagnostics is raw
    assert bridge.calls == [("POST", PATH, wire(EXECUTE), "E3D21")]


def test_managed_status_keeps_historical_faults_and_never_probes_native_state():
    previous = updated(journal=False)
    previous.update(ok=False, error="Original native setter fault", error_code="native_database_fault", native_fault_latched=True, retry_safe=False)
    previous["result"].update(status="failed", success=False, complete=False)
    previous["result"]["progress"].update(native_fault_latched=True, outcome_uncertain=True, readback_complete=False)
    record = snapshot("completed", receipt=previous)
    record["http_status"] = 500
    raw = envelope(record)
    with patch("urllib.request.urlopen", return_value=response(raw)) as transport:
        assert paragon_component_parameters_status(BridgeClient(SETTINGS), STATUS_ARGS) == raw
    transport.assert_called_once()
    request = transport.call_args.args[0]
    url = urllib.parse.urlsplit(request.full_url)
    assert request.method == "GET" and request.data is None and url.path == STATUS_PATH
    assert urllib.parse.parse_qs(url.query) == {"request_id": [REQUEST_ID], "expected_session_id": [SESSION]}

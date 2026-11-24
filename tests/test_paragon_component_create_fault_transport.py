"""Creation failures keep native ownership facts and never trigger recovery I/O."""
from __future__ import annotations

from copy import deepcopy
import http.client
import io
import json
from pathlib import Path
import sys
import urllib.error
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError, _response_failure_diagnostics
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.paragon_component_create import PATH, STATUS_PATH, paragon_component_create, paragon_component_create_status
from test_paragon_component_create import (
    ARGS, BUILD, EXECUTE, NEW, REQUEST_ID, SESSION, STATUS_ARGS, created, envelope, prepared, snapshot, wire,
)


SETTINGS = Settings(mode="bridge", version="E3D21", allow_commands=True)
PROVENANCE = {"version": "E3D21", "session_id": SESSION, "bridge_build": BUILD}
MARKERS = [
    {"native_fault": True}, {"has_fault": True}, {"native_database_fault": True}, {"native_fault_latched": True},
    {"error_code": "native_fault"}, {"error_code": "native_database_fault"},
    {"code": "native_fault"}, {"code": "native_database_fault"},
]


def response(value):
    return io.BytesIO(json.dumps(value, allow_nan=False).encode("utf-8"))


def http_error(status, value, path=PATH):
    return urllib.error.HTTPError(SETTINGS.bridge_url + path, status, "original native failure", {}, response(value))


def assert_one_post(urlopen, args):
    urlopen.assert_called_once()
    request = urlopen.call_args.args[0]
    assert request.method == "POST" and request.full_url == SETTINGS.bridge_url + PATH
    assert json.loads(request.data) == wire(args)


class RecordingBridge:
    settings = SETTINGS

    def __init__(self, value):
        self.value, self.calls = value, []

    def _selected_version(self, version):
        assert version is None
        return "E3D21"

    def _request(self, method, path, payload, *, version):
        self.calls.append((method, path, deepcopy(payload), version))
        if isinstance(self.value, BridgeError):
            raise self.value
        return self.value

    def __getattr__(self, name):
        raise AssertionError("Forbidden recovery read or script forwarding: " + name)


@pytest.mark.parametrize("args", [ARGS, EXECUTE])
@pytest.mark.parametrize("status", [200, 404, 409, 500])
@pytest.mark.parametrize("container", [None, "details", "diagnostics", "result"])
@pytest.mark.parametrize("marker", MARKERS)
def test_nested_native_faults_keep_original_transport_cause_and_never_fallback(args, status, container, marker):
    nested = deepcopy(marker) if container is None else {container: [{"raw": deepcopy(marker), "sdk_code": 538}]}
    raw = {"ok": False, "error": "Original native create fault", **PROVENANCE, **nested,
           "retry_safe": False, "private": "must not escape"}
    original = deepcopy(raw)
    cause = http_error(status, raw)
    route = {"return_value": response(raw)} if status == 200 else {"side_effect": cause}
    with patch("urllib.request.urlopen", **route) as urlopen, pytest.raises(BridgeError) as caught:
        paragon_component_create(BridgeClient(SETTINGS), args)
    assert_one_post(urlopen, args)
    error = caught.value
    assert error.http_status == (None if status == 200 else status)
    assert error.__cause__ is (None if status == 200 else cause)
    assert "Original native create fault" in str(error)
    assert all(error.diagnostics[key] == value for key, value in {**PROVENANCE, **nested, "retry_safe": False}.items())
    assert "private" not in error.diagnostics and raw == original


@pytest.mark.parametrize("status", [400, 404, 405, 409, 423, 500])
def test_http_failed_creation_keeps_captured_new_ref_and_all_native_stage_evidence(status):
    native = created()["result"]
    native.update(status="failed", success=False, complete=False)
    native["progress"].update(readback_complete=False, outcome_uncertain=True)
    raw = {"ok": False, **PROVENANCE, "error": "Original setter or readback failed", "result": native}
    original = deepcopy(raw)
    cause = http_error(status, raw)
    with patch("urllib.request.urlopen", side_effect=cause) as urlopen, pytest.raises(BridgeError) as caught:
        paragon_component_create(BridgeClient(SETTINGS), EXECUTE)
    assert_one_post(urlopen, EXECUTE)
    error = caught.value
    assert error.__cause__ is cause and error.http_status == status
    assert error.diagnostics["result"] == original["result"]
    assert error.diagnostics["result"]["progress"]["known_new_refno"] == NEW
    assert error.diagnostics["result"]["after"]["new_template"] == native["after"]["new_template"]
    assert error.outcome_uncertain is True and error.diagnostics["retry_safe"] is False


@pytest.mark.parametrize("status", ["rejected", "duplicate"])
def test_admission_failure_owns_no_write_even_when_prior_journal_has_creation(status):
    native = prepared()["result"]
    native.update(dry_run=False, request_id=REQUEST_ID, status=status, success=False, complete=False,
                  before=None, scoped_revision=None, journal=snapshot("completed", receipt=created()))
    raw = {"ok": False, **PROVENANCE, "error": "Original admission refusal", "result": native}
    cause = http_error(409, raw)
    with patch("urllib.request.urlopen", side_effect=cause) as urlopen, pytest.raises(BridgeError) as caught:
        paragon_component_create(BridgeClient(SETTINGS), EXECUTE)
    assert_one_post(urlopen, EXECUTE)
    assert caught.value.__cause__ is cause
    assert caught.value.diagnostics["result"] == native
    assert caught.value.diagnostics["result"]["journal"]["critical_summary"]["creation_attempted"] is True
    assert caught.value.diagnostics["result"]["this_submission_executed"] is False
    assert caught.value.outcome_uncertain is False


@pytest.mark.parametrize("args", [ARGS, EXECUTE])
@pytest.mark.parametrize("status", [404, 405, 500])
def test_ordinary_http_error_has_no_health_probe_fallback_status_or_retry(args, status):
    cause = http_error(status, {"ok": False, "error": "Ordinary unavailable creator", **PROVENANCE})
    with patch("urllib.request.urlopen", side_effect=cause) as urlopen, pytest.raises(BridgeError) as caught:
        paragon_component_create(BridgeClient(SETTINGS), args)
    assert_one_post(urlopen, args)
    assert caught.value.__cause__ is cause and caught.value.http_status == status
    assert "Ordinary unavailable creator" in str(caught.value)
    assert "available" not in caught.value.diagnostics and "status" not in caught.value.diagnostics


@pytest.mark.parametrize("args", [ARGS, EXECUTE])
@pytest.mark.parametrize("cause", [TimeoutError("timed out"), urllib.error.URLError("connection lost"),
                                  OSError("socket failed"), http.client.IncompleteRead(b"prefix", 100)])
def test_transport_failure_is_uncertain_only_for_execute_and_is_never_replayed(args, cause):
    with patch("urllib.request.urlopen", side_effect=cause) as urlopen, pytest.raises(BridgeError) as caught:
        paragon_component_create(BridgeClient(SETTINGS), args)
    assert_one_post(urlopen, args)
    assert caught.value.__cause__ is cause and caught.value.transport_error is True
    assert caught.value.outcome_uncertain is (args is EXECUTE)
    if args is EXECUTE:
        assert caught.value.diagnostics["outcome_uncertain"] is True and caught.value.diagnostics["retry_safe"] is False


@pytest.mark.parametrize("args", [ARGS, EXECUTE])
@pytest.mark.parametrize("raw", [b"not JSON", b"[]", b"{\"ok\":true,\"ok\":true}", b"\xff", b"{\"ok\":true,\"result\":NaN}"])
def test_invalid_transport_json_never_invents_preparation_or_retries(args, raw):
    with patch("urllib.request.urlopen", return_value=io.BytesIO(raw)) as urlopen, pytest.raises(BridgeError) as caught:
        paragon_component_create(BridgeClient(SETTINGS), args)
    assert_one_post(urlopen, args)
    assert caught.value.response_invalid is True
    assert caught.value.outcome_uncertain is (args is EXECUTE)
    assert "result" not in caught.value.diagnostics


@pytest.mark.parametrize("flags", [{}, {"transport_error": True}, {"response_invalid": True},
                                  {"transport_error": True, "response_invalid": True}])
def test_original_bridge_error_object_and_nested_fault_diagnostics_are_not_replaced(flags):
    diagnostics = {"result": {"progress": {"creation_attempted": True, "known_new_refno": NEW}},
                   "details": [{"native_database_fault": True}], "retry_safe": False}
    original = BridgeError("Original BridgeError", http_status=404, diagnostics=diagnostics, **flags)
    cause = http_error(404, {"ok": False, "error": "original"})
    original.__cause__ = cause
    bridge = RecordingBridge(original)
    with pytest.raises(BridgeError) as caught:
        paragon_component_create(bridge, EXECUTE)
    assert caught.value is original and caught.value.__cause__ is cause
    assert caught.value.diagnostics is diagnostics and caught.value.http_status == 404
    assert bridge.calls == [("POST", PATH, wire(EXECUTE), "E3D21")]


@pytest.mark.parametrize("marker", MARKERS)
def test_success_envelope_embedded_fault_is_checked_before_evidence_validation(marker):
    raw = {"ok": True, "result": {"unvalidated": [{"raw": deepcopy(marker)}]}}
    bridge = RecordingBridge(raw)
    with pytest.raises(BridgeError) as caught:
        paragon_component_create(bridge, EXECUTE)
    assert caught.value.response_invalid is False and caught.value.diagnostics is raw
    assert caught.value.outcome_uncertain is True and len(bridge.calls) == 1


def test_status_remains_managed_with_historical_native_fault_and_known_new_identity():
    stored = created()
    stored.update(ok=False, error="Historical native fault", error_code="native_database_fault", native_fault_latched=True, retry_safe=False)
    stored["result"].update(status="failed", success=False, complete=False)
    stored["result"]["progress"].update(native_fault_latched=True, outcome_uncertain=True, readback_complete=False)
    raw = envelope(snapshot("completed", receipt=stored))
    original = deepcopy(raw)
    with patch("urllib.request.urlopen", return_value=response(raw)) as urlopen:
        observed = paragon_component_create_status(BridgeClient(SETTINGS), STATUS_ARGS)
    urlopen.assert_called_once()
    request = urlopen.call_args.args[0]
    assert request.method == "GET" and request.full_url.startswith(SETTINGS.bridge_url + STATUS_PATH + "?")
    assert request.data is None and observed == original and raw == original
    assert observed["result"]["critical_summary"]["known_new_refno"] == NEW
    assert observed["result"]["receipt"]["native_fault_latched"] is True


@pytest.mark.parametrize("path", [PATH + "/other", PATH + "-other", "/api/v1/command", "/api/v1/pml-script"])
def test_diagnostic_scope_never_expands_by_path_prefix(path):
    raw = {"ok": False, **PROVENANCE, "error": "error", "result": {"private": "not a creation receipt"}}
    assert "result" not in _response_failure_diagnostics(path, raw)
    assert _response_failure_diagnostics(PATH, raw)["result"] == raw["result"]

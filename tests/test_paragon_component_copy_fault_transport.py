"""One-request native copy transport preserves failure ownership and progress."""
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
from aveva_e3d_mcp.paragon_component_copy import (
    PATH, STATUS_PATH, paragon_component_copy, paragon_component_copy_status,
)
from test_paragon_component_copy import (
    ARGS, BUILD, EXECUTE, PREPARE, REQUEST_ID, SESSION, STATUS_ARGS, envelope, executed, not_attempted,
    prepared, snapshot,
)


SETTINGS = Settings(mode="bridge", version="E3D21", allow_commands=True)
PROVENANCE = {"version": "E3D21", "session_id": SESSION, "bridge_build": BUILD}
MARKERS = [
    {"native_fault": True}, {"has_fault": True}, {"native_database_fault": True},
    {"native_fault_latched": True}, {"error_code": "native_fault"},
    {"error_code": "native_database_fault"}, {"code": "native_fault"}, {"code": "native_database_fault"},
]


def response(value):
    return io.BytesIO(json.dumps(value, allow_nan=False).encode("utf-8"))


def http_error(status, body, path=PATH):
    return urllib.error.HTTPError(SETTINGS.bridge_url + path, status, "native failure", {}, body)


def assert_one_post(urlopen, payload):
    urlopen.assert_called_once()
    request = urlopen.call_args.args[0]
    assert request.method == "POST" and request.full_url == SETTINGS.bridge_url + PATH
    assert json.loads(request.data) == payload


class RecordingBridge:
    settings = SETTINGS

    def __init__(self, value):
        self.value, self.calls = value, []

    def _selected_version(self, version):
        assert version is None
        return "E3D21"

    def _request(self, method, path, payload, *, version):
        self.calls.append((method, path, payload, version))
        if isinstance(self.value, BridgeError):
            raise self.value
        return self.value

    def __getattr__(self, name):
        raise AssertionError(f"Forbidden recovery read or script forwarding: {name}")


@pytest.mark.parametrize("payload", [PREPARE, EXECUTE])
@pytest.mark.parametrize("status", [200, 404, 409, 500])
@pytest.mark.parametrize("container", [None, "details", "diagnostics", "result"])
@pytest.mark.parametrize("marker", MARKERS)
def test_faults_preserve_original_nested_diagnostics_without_feature_fallback(payload, status, container, marker):
    nested = deepcopy(marker) if container is None else {container: [{"raw": deepcopy(marker), "sdk_code": 538}]}
    reply = {"ok": False, "error": "Original native copy fault", **PROVENANCE, **nested,
             "retry_safe": False, "private": "not permitted"}
    original = deepcopy(reply)
    body = response(reply)
    cause = http_error(status, body)
    transport = {"return_value": body} if status == 200 else {"side_effect": cause}
    with patch("urllib.request.urlopen", **transport) as urlopen, pytest.raises(BridgeError) as caught:
        paragon_component_copy(BridgeClient(SETTINGS), payload)
    assert_one_post(urlopen, payload)
    diagnostics = caught.value.diagnostics
    assert all(diagnostics[key] == value for key, value in {**PROVENANCE, **nested, "retry_safe": False}.items())
    assert "private" not in diagnostics
    assert reply == original
    assert "Original native copy fault" in str(caught.value)
    assert caught.value.http_status == (None if status == 200 else status)
    assert caught.value.__cause__ is (None if status == 200 else cause)


@pytest.mark.parametrize("status", [400, 404, 405, 409, 423, 500])
def test_native_failed_result_keeps_all_progress_and_original_http_cause(status):
    result = executed(journal=False)["result"]
    result.update(status="failed", success=False, complete=False)
    result["progress"].update(readback_complete=False, outcome_uncertain=True)
    reply = {"ok": False, **PROVENANCE, "error": "Original readback failed", "result": result}
    original = deepcopy(reply)
    cause = http_error(status, response(reply))
    with patch("urllib.request.urlopen", side_effect=cause) as urlopen, pytest.raises(BridgeError) as caught:
        paragon_component_copy(BridgeClient(SETTINGS), EXECUTE)
    assert_one_post(urlopen, EXECUTE)
    assert caught.value.__cause__ is cause and caught.value.http_status == status
    assert caught.value.diagnostics["result"] == original["result"]
    assert caught.value.diagnostics["result"]["progress"]["known_new_refno"] == "=7/103"
    assert caught.value.outcome_uncertain is True
    assert caught.value.diagnostics["retry_safe"] is False
    assert "Original readback failed" in str(caught.value)


@pytest.mark.parametrize("status", ["rejected", "duplicate"])
def test_admission_failure_can_retain_original_journal_without_claiming_this_submission_executed(status):
    result = prepared()["result"]
    result.update(dry_run=False, request_id=REQUEST_ID, status=status, success=False, complete=False,
                  before=None, scoped_revision=None, journal=snapshot("completed", reply=executed(journal=False)))
    reply = {"ok": False, **PROVENANCE, "error": "Original admission refusal", "result": result}
    cause = http_error(409, response(reply))
    with patch("urllib.request.urlopen", side_effect=cause) as urlopen, pytest.raises(BridgeError) as caught:
        paragon_component_copy(BridgeClient(SETTINGS), EXECUTE)
    assert_one_post(urlopen, EXECUTE)
    assert caught.value.__cause__ is cause
    assert caught.value.diagnostics["result"] == result
    assert caught.value.diagnostics["result"]["journal"]["critical_summary"]["native_run_attempted"] is True
    assert caught.value.diagnostics["result"]["this_submission_executed"] is False
    assert caught.value.outcome_uncertain is False


@pytest.mark.parametrize("flags", [{}, {"transport_error": True}, {"response_invalid": True},
                                  {"transport_error": True, "response_invalid": True}])
def test_bridge_error_object_message_flags_and_cause_are_not_replaced(flags):
    native = {"result": {"progress": {**not_attempted(), "native_fault_latched": True}},
              "details": [{"native_database_fault": True}], "retry_safe": False}
    original = BridgeError("Original BridgeError", http_status=404, diagnostics=native, **flags)
    cause = http_error(404, response({"ok": False, "error": "original"}))
    original.__cause__ = cause
    client = RecordingBridge(original)
    with pytest.raises(BridgeError) as caught:
        paragon_component_copy(client, EXECUTE)
    assert caught.value is original and caught.value.__cause__ is cause
    assert caught.value.diagnostics is native
    assert str(caught.value) == "Original BridgeError" and caught.value.http_status == 404
    assert native["result"]["progress"]["native_fault_latched"] is True
    assert client.calls == [("POST", PATH, EXECUTE, "E3D21")]
    if flags:
        assert caught.value.outcome_uncertain is True


@pytest.mark.parametrize("payload", [PREPARE, EXECUTE])
@pytest.mark.parametrize("failure", [TimeoutError("offline timeout"), urllib.error.URLError("offline"),
                                   http.client.IncompleteRead(b"{", 5), OSError("offline")])
def test_transport_failure_has_no_retry_status_lookup_health_or_native_diagnostics(payload, failure):
    with patch("urllib.request.urlopen", side_effect=failure) as urlopen, pytest.raises(BridgeError) as caught:
        paragon_component_copy(BridgeClient(SETTINGS), payload)
    assert_one_post(urlopen, payload)
    assert caught.value.transport_error is True and caught.value.__cause__ is failure
    assert caught.value.outcome_uncertain is (not payload["dry_run"])
    if not payload["dry_run"]:
        assert caught.value.diagnostics["outcome_uncertain"] is True
        assert caught.value.diagnostics["retry_safe"] is False


@pytest.mark.parametrize("raw", [b"missing", b"null", b"[]", b'{"ok":true}', b'{"ok":false,"error":null}',
                                 b'{"ok":true,"result":NaN}'])
@pytest.mark.parametrize("payload", [PREPARE, EXECUTE])
def test_invalid_response_is_not_recast_as_success_or_feature_absence(raw, payload):
    with patch("urllib.request.urlopen", return_value=io.BytesIO(raw)) as urlopen, pytest.raises(BridgeError) as caught:
        paragon_component_copy(BridgeClient(SETTINGS), payload)
    assert_one_post(urlopen, payload)
    assert caught.value.response_invalid is True
    assert caught.value.outcome_uncertain is (not payload["dry_run"])
    if not payload["dry_run"]:
        assert caught.value.diagnostics["outcome_uncertain"] is True and caught.value.diagnostics["retry_safe"] is False


@pytest.mark.parametrize("field", ["version", "session_id", "bridge_build"])
def test_native_success_must_provide_its_own_version_and_session_build(field):
    reply = executed()
    del reply[field]
    with patch("urllib.request.urlopen", return_value=response(reply)) as urlopen, pytest.raises(BridgeError) as caught:
        paragon_component_copy(BridgeClient(SETTINGS), EXECUTE)
    assert_one_post(urlopen, EXECUTE)
    assert caught.value.response_invalid and caught.value.outcome_uncertain


def test_fault_bearing_raw_success_retains_the_original_before_shape_validation():
    reply = {"ok": True, "version": "wrong", "result": [{"raw": {"native_fault_latched": True}}]}
    client = RecordingBridge(reply)
    with pytest.raises(BridgeError) as caught:
        paragon_component_copy(client, EXECUTE)
    assert caught.value.diagnostics is reply
    assert client.calls == [("POST", PATH, EXECUTE, "E3D21")]


def test_exact_route_diagnostics_keep_copy_progress_but_do_not_expand_prefix_access():
    result = {"progress": {"known_new_refno": "=7/103"}, "journal": {"retained": "original"}}
    raw = {**PROVENANCE, "result": result, "details": {"native_fault_latched": True},
           "retry_safe": False, "private": "not permitted"}
    for path in (PATH, STATUS_PATH):
        expected = {key: value for key, value in raw.items() if key != "private"}
        assert _response_failure_diagnostics(path, raw) == expected
    for path in (PATH + "/", PATH + "-other", STATUS_PATH + "/", STATUS_PATH + "/other"):
        assert _response_failure_diagnostics(path, raw) == {}


def test_status_with_historical_fault_is_available_without_a_native_probe():
    previous = executed(journal=False)
    previous.update(ok=False, error="Original native fault", error_code="native_database_fault",
                    native_fault_latched=True, retry_safe=False)
    previous["result"].update(status="failed", success=False, complete=False)
    previous["result"]["progress"].update(native_fault_latched=True, readback_complete=False, outcome_uncertain=True)
    raw = envelope(snapshot("completed", reply=previous))
    raw["result"]["http_status"] = 500
    with patch("urllib.request.urlopen", return_value=response(raw)) as urlopen:
        observed = paragon_component_copy_status(BridgeClient(SETTINGS), STATUS_ARGS)
    urlopen.assert_called_once()
    request = urlopen.call_args.args[0]
    assert request.method == "GET" and request.data is None
    assert request.full_url.startswith(SETTINGS.bridge_url + STATUS_PATH + "?")
    assert observed == raw


def test_status_session_conflict_preserves_original_failure_without_lookup_retry():
    reply = {"ok": False, **PROVENANCE, "error": "Session pin mismatch", "write_attempted": False,
             "outcome_uncertain": False, "retry_safe": False}
    cause = http_error(409, response(reply), STATUS_PATH)
    with patch("urllib.request.urlopen", side_effect=cause) as urlopen, pytest.raises(BridgeError) as caught:
        paragon_component_copy_status(BridgeClient(SETTINGS), STATUS_ARGS)
    urlopen.assert_called_once()
    assert caught.value.__cause__ is cause and caught.value.http_status == 409
    assert caught.value.outcome_uncertain is False
    assert caught.value.diagnostics["write_attempted"] is False


@pytest.mark.parametrize("path", [("status",), ("before", "name_lookup", "status"),
                                  ("before", "source_template", "parameters", "status"),
                                  ("journal", "status")])
@pytest.mark.parametrize("bad", [None, 1, True, [], {}, ["executed"]])
def test_malformed_nested_json_types_cannot_escape_execution_uncertainty(path, bad):
    reply = executed()
    target = reply["result"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = bad
    with patch("urllib.request.urlopen", return_value=response(reply)) as urlopen, pytest.raises(BridgeError) as caught:
        paragon_component_copy(BridgeClient(SETTINGS), EXECUTE)
    assert_one_post(urlopen, EXECUTE)
    assert caught.value.response_invalid is True and caught.value.outcome_uncertain is True
    assert caught.value.diagnostics["outcome_uncertain"] is True and caught.value.diagnostics["retry_safe"] is False


def test_partial_appended_member_name_cannot_bypass_uncertain_failure_receipt():
    reply = executed(journal=False)
    reply["result"]["after"]["members"][-1].update(name=None, name_error="Native NAME failed")
    with patch("urllib.request.urlopen", return_value=response(reply)) as urlopen, pytest.raises(BridgeError) as caught:
        paragon_component_copy(BridgeClient(SETTINGS), EXECUTE)
    assert_one_post(urlopen, EXECUTE)
    assert caught.value.response_invalid is True and caught.value.outcome_uncertain is True

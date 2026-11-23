"""Exercise reference-trace fault priority through the real HTTP decoder."""
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
from aveva_e3d_mcp.paragon_reference_trace import PATH, VERSION, paragon_reference_trace
from test_paragon_reference_trace import BUILD, SESSION


ARGS = {"refno": "=1/21", "trace_limit": 24}
SETTINGS = Settings(mode="bridge", version=VERSION, allow_commands=False)
PROVENANCE = {"version": VERSION, "session_id": SESSION, "bridge_build": BUILD}
FAULTS = [
    {"native_fault": True},
    {"has_fault": True},
    {"native_database_fault": True},
    {"native_fault_latched": True},
    {"error_code": "native_fault"},
    {"error_code": "native_database_fault"},
    {"code": "native_fault"},
    {"code": "native_database_fault"},
]


def response(value):
    return io.BytesIO(json.dumps(value, allow_nan=False).encode("utf-8"))


def http_failure(status, body):
    return urllib.error.HTTPError("http://offline.invalid" + PATH, status, "failure", {}, body)


def assert_one_post(urlopen):
    urlopen.assert_called_once()
    request = urlopen.call_args.args[0]
    assert request.method == "POST"
    assert request.full_url == SETTINGS.bridge_url + PATH
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data) == ARGS


class RecordingBridge:
    settings = SETTINGS

    def __init__(self, receipt):
        self.receipt, self.calls = receipt, []

    def _selected_version(self, version):
        assert version is None
        return VERSION

    def _request(self, method, path, payload, *, version):
        self.calls.append((method, path, payload, version))
        if isinstance(self.receipt, BridgeError):
            raise self.receipt
        return self.receipt

    def __getattr__(self, name):
        raise AssertionError(f"Unexpected bridge access: {name}")


@pytest.mark.parametrize("flags", [{}, {"transport_error": True}, {"response_invalid": True},
                                  {"transport_error": True, "response_invalid": True}])
@pytest.mark.parametrize("marker", FAULTS)
def test_fault_bearing_bridge_error_is_re_raised_before_any_404_classification(flags, marker):
    diagnostics = {"details": [{"diagnostics": deepcopy(marker), "native_code": 538}]}
    original = BridgeError("Original native failure", http_status=404, diagnostics=diagnostics, **flags)
    cause = http_failure(404, response({"ok": False, "error": "original"}))
    original.__cause__ = cause
    bridge = RecordingBridge(original)
    with pytest.raises(BridgeError) as caught:
        paragon_reference_trace(bridge, ARGS)
    assert caught.value is original and caught.value.__cause__ is cause
    assert caught.value.diagnostics is diagnostics
    assert str(caught.value) == "Original native failure"
    assert bridge.calls == [("POST", PATH, ARGS, VERSION)]


@pytest.mark.parametrize("marker", FAULTS + [{"NativeFault": {"HasFault": True}},
                                           {"status": "NativeDatabaseFaultException"}])
def test_raw_embedded_fault_preserves_original_receipt_before_shape_validation(marker):
    reply = {"ok": True, "version": "not-E3D21", "result": {"details": [deepcopy(marker)]}}
    original = deepcopy(reply)
    bridge = RecordingBridge(reply)
    with pytest.raises(BridgeError, match="native database fault") as caught:
        paragon_reference_trace(bridge, ARGS)
    assert caught.value.diagnostics is reply
    assert reply == original and caught.value.outcome_uncertain is False
    assert bridge.calls == [("POST", PATH, ARGS, VERSION)]


@pytest.mark.parametrize("status", [200, 404, 500])
@pytest.mark.parametrize("container", [None, "details", "diagnostics", "result", "native_fault", "native_database_fault"])
@pytest.mark.parametrize("marker", FAULTS)
def test_wire_fault_preserves_nested_container_and_http_cause(status, container, marker):
    content = deepcopy(marker) if container is None else {container: [{"detail": deepcopy(marker), "sdk_code": 538}]}
    details = {**PROVENANCE, **content, "retry_safe": False}
    reply = {"ok": False, "error": "Original reference trace fault", "private": "do not disclose", **details}
    body = response(reply)
    original_http = http_failure(status, body)
    transport = {"return_value": body} if status == 200 else {"side_effect": original_http}
    with patch("urllib.request.urlopen", **transport) as urlopen, pytest.raises(BridgeError) as caught:
        paragon_reference_trace(BridgeClient(SETTINGS), ARGS)
    assert_one_post(urlopen)
    assert caught.value.diagnostics == details
    assert "private" not in caught.value.diagnostics
    assert caught.value.http_status == (None if status == 200 else status)
    assert caught.value.__cause__ is (None if status == 200 else original_http)
    assert "Original reference trace fault" in str(caught.value)
    assert caught.value.outcome_uncertain is False


@pytest.mark.parametrize("container", ["details", "diagnostics", "result"])
def test_fault_in_success_reply_reaches_consumer_without_losing_raw_receipt(container):
    reply = {"ok": True, **PROVENANCE, "result": {}, container: {"nested": [{"native_fault_latched": True}]}}
    with patch("urllib.request.urlopen", return_value=response(reply)) as urlopen, pytest.raises(BridgeError) as caught:
        paragon_reference_trace(BridgeClient(SETTINGS), ARGS)
    assert_one_post(urlopen)
    assert caught.value.diagnostics == reply


def test_transport_diagnostics_preserve_only_exact_route_and_fault_bearing_containers():
    receipt = {
        **PROVENANCE, "native_fault_latched": True, "retry_safe": False,
        "details": {"code": "native_database_fault", "raw": [538]},
        "diagnostics": {"ordinary": "private"}, "result": {"ordinary": "private"},
        "extra": {"native_fault_latched": True}, "state": {"busy": True},
    }
    expected = {key: value for key, value in receipt.items() if key not in {"diagnostics", "result", "extra"}}
    assert _response_failure_diagnostics(PATH, receipt) == expected
    for path in (PATH + "/", PATH + "/extra", PATH + "-other"):
        assert _response_failure_diagnostics(path, receipt) == {"state": receipt["state"]}


def test_valid_route_404_reports_unknown_without_any_other_request():
    body = response({"ok": False, "error": "Reader unavailable", **PROVENANCE})
    with patch("urllib.request.urlopen", side_effect=http_failure(404, body)) as urlopen:
        reply = paragon_reference_trace(BridgeClient(SETTINGS), ARGS)
    assert_one_post(urlopen)
    result = reply["result"]
    assert result["status"] == "unknown" and result["available"] is False
    assert result["success"] is False and result["complete"] is False
    assert result["read_errors"][0]["code"] == "native_feature_unavailable"
    assert not {"trace", "context", "db_type_codes"} & result.keys()


@pytest.mark.parametrize("raw", [
    b"missing", b'{"ok":false,"ok":true,"error":"missing"}',
    b'{"ok":false,"error":null}', b'{"ok":true}', b"[]", b'null', b'"missing"',
])
def test_malformed_404_is_invalid_instead_of_optional_feature_absence(raw):
    with patch("urllib.request.urlopen", side_effect=http_failure(404, io.BytesIO(raw))) as urlopen:
        reply = paragon_reference_trace(BridgeClient(SETTINGS), ARGS)
    assert_one_post(urlopen)
    assert reply["result"]["read_errors"][0]["code"] == "invalid_native_response"


@pytest.mark.parametrize("status", [404, 500])
def test_truncated_error_body_remains_transport_error(status):
    class IncompleteResponse(io.BytesIO):
        def read(self, *args):
            raise http.client.IncompleteRead(b'{"ok":', 100)

    with patch("urllib.request.urlopen", side_effect=http_failure(status, IncompleteResponse())) as urlopen, \
         pytest.raises(BridgeError) as caught:
        paragon_reference_trace(BridgeClient(SETTINGS), ARGS)
    assert_one_post(urlopen)
    assert caught.value.http_status == status and caught.value.transport_error is True


@pytest.mark.parametrize("status", [400, 405, 409, 423, 500])
def test_ordinary_bridge_error_object_is_raised_intact(status):
    original = BridgeError("Bridge read failed", http_status=status, diagnostics=deepcopy(PROVENANCE))
    bridge = RecordingBridge(original)
    with pytest.raises(BridgeError) as caught:
        paragon_reference_trace(bridge, ARGS)
    assert caught.value is original
    assert bridge.calls == [("POST", PATH, ARGS, VERSION)]


@pytest.mark.parametrize("failure", [TimeoutError("offline timeout"), urllib.error.URLError("offline"),
                                   OSError("offline"), http.client.IncompleteRead(b"{", 5)])
def test_transport_failure_never_becomes_optional_feature_absence(failure):
    with patch("urllib.request.urlopen", side_effect=failure) as urlopen, pytest.raises(BridgeError) as caught:
        paragon_reference_trace(BridgeClient(SETTINGS), ARGS)
    assert_one_post(urlopen)
    assert caught.value.transport_error is True and caught.value.__cause__ is failure


def test_missing_native_version_is_never_backfilled_by_transport():
    reply = {"ok": True, **{key: PROVENANCE[key] for key in ("session_id", "bridge_build")}, "result": {}}
    with patch("urllib.request.urlopen", return_value=response(reply)) as urlopen:
        raw = BridgeClient(SETTINGS)._request("POST", PATH, ARGS, version=VERSION)
    assert_one_post(urlopen)
    assert raw == reply and "version" not in raw
    with patch("urllib.request.urlopen", return_value=response(reply)) as urlopen:
        invalid = paragon_reference_trace(BridgeClient(SETTINGS), ARGS)
    assert_one_post(urlopen)
    assert invalid["result"]["read_errors"][0]["code"] == "invalid_native_response"

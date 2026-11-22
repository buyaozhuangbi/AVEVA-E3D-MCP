"""Test exact route transport with intercepted HTTP and no AVEVA process."""
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
from aveva_e3d_mcp.paragon_component_template import PATH, VERSION, paragon_component_template
from test_paragon_component_template import ARGS


SETTINGS = Settings(mode="bridge", version=VERSION, allow_commands=False)
PROVENANCE = {
    "version": VERSION,
    "session_id": "11111111-1111-4111-8111-111111111111",
    "bridge_build": "22222222-2222-4222-8222-222222222222",
}
FAULT_MARKERS = [
    {"native_database_fault": True},
    {"native_fault_latched": True},
    {"native_fault": True},
    {"has_fault": True},
    {"error_code": "native_fault"},
    {"error_code": "native_database_fault"},
    {"code": "native_database_fault"},
    {"state": {"detail": [{"native_fault": {"has_fault": True, "code": 538}}]}},
    {"diagnostics": {"details": [{"native_fault_latched": True}]}},
    {"result": {"details": {"native_database_fault": True}}},
]


class RecordingBridge:
    settings = SETTINGS

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def _selected_version(self, version):
        assert version is None
        return VERSION

    def _request(self, method, path, payload, *, version):
        self.calls.append((method, path, payload, version))
        if isinstance(self.reply, BridgeError):
            raise self.reply
        return self.reply

    def __getattr__(self, name):
        raise AssertionError(f"Unexpected bridge access: {name}")


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


def test_native_fault_diagnostics_take_precedence_over_bridge_error_status_flags():
    diagnostics = {"result": [{"code": "native_fault", "detail": {"native_code": 538}}]}
    original = BridgeError("Native error", http_status=404, response_invalid=True,
                           transport_error=True, diagnostics=diagnostics)
    bridge = RecordingBridge(original)
    with pytest.raises(BridgeError) as caught:
        paragon_component_template(bridge, ARGS)
    assert caught.value is original
    assert caught.value.diagnostics is diagnostics
    assert bridge.calls == [("POST", PATH, ARGS, VERSION)]


@pytest.mark.parametrize("marker", FAULT_MARKERS)
def test_raw_native_faults_preserve_the_original_even_if_success_or_shape_is_invalid(marker):
    reply = {"ok": True, "version": "unexpected", **deepcopy(marker)}
    original = deepcopy(reply)
    bridge = RecordingBridge(reply)
    with pytest.raises(BridgeError) as caught:
        paragon_component_template(bridge, ARGS)
    assert caught.value.diagnostics is reply and reply == original
    assert caught.value.outcome_uncertain is False
    assert bridge.calls == [("POST", PATH, ARGS, VERSION)]


@pytest.mark.parametrize("status", [200, 404, 500])
@pytest.mark.parametrize("marker", FAULT_MARKERS)
def test_wire_faults_keep_exact_diagnostics_without_retry_or_alternate_route(status, marker):
    details = {**PROVENANCE, **deepcopy(marker), "retry_safe": False}
    receipt = {"ok": False, "error": "Native component template read fault.", "extra": "private", **details}
    body = response(receipt)
    transport = {"return_value": body} if status == 200 else {"side_effect": http_failure(status, body)}
    bridge = BridgeClient(SETTINGS)
    with patch("urllib.request.urlopen", **transport) as urlopen, pytest.raises(BridgeError) as caught:
        paragon_component_template(bridge, ARGS)
    assert_one_post(urlopen)
    assert caught.value.diagnostics == details
    assert caught.value.http_status == (None if status == 200 else status)
    assert caught.value.outcome_uncertain is False


def test_missing_native_version_is_not_backfilled_from_configuration():
    receipt = {"ok": True, "result": {}, **{key: PROVENANCE[key] for key in ("session_id", "bridge_build")}}
    with patch("urllib.request.urlopen", return_value=response(receipt)) as urlopen:
        raw = BridgeClient(SETTINGS)._request("POST", PATH, ARGS, version=VERSION)
    assert_one_post(urlopen)
    assert raw == receipt and "version" not in raw
    with patch("urllib.request.urlopen", return_value=response(receipt)) as urlopen:
        missing = paragon_component_template(BridgeClient(SETTINGS), ARGS)
    assert_one_post(urlopen)
    assert missing["result"]["read_errors"][0]["code"] == "invalid_native_response"


def test_exact_path_allowlists_only_native_diagnostics():
    receipt = {
        **PROVENANCE,
        "error_code": "native_database_fault",
        "native_fault_latched": True,
        "retry_safe": False,
        "result": {"private": "ordinary result"},
        "state": {"detail": "ordinary state remains transport diagnostics"},
        "extra": {"native_fault_latched": True},
    }
    expected = {key: value for key, value in receipt.items() if key not in {"extra", "result"}}
    assert _response_failure_diagnostics(PATH, receipt) == expected
    for path in (PATH + "/", PATH + "/extra", PATH + "-other"):
        assert _response_failure_diagnostics(path, receipt) == {"state": receipt["state"]}


def test_valid_missing_route_is_unknown_without_another_request():
    body = response({"ok": False, "error": "Feature unavailable", **PROVENANCE})
    with patch("urllib.request.urlopen", side_effect=http_failure(404, body)) as urlopen:
        unavailable = paragon_component_template(BridgeClient(SETTINGS), ARGS)
    assert_one_post(urlopen)
    result = unavailable["result"]
    assert result["available"] is False and result["complete"] is False and result["success"] is False
    assert result["read_errors"][0]["code"] == "native_feature_unavailable"


@pytest.mark.parametrize("raw", [
    b"missing", b'{"ok":false,"ok":true,"error":"missing"}',
    b'{"ok":false,"error":false}', b'{"ok":true}',
])
def test_malformed_404_is_invalid_evidence_and_does_not_report_feature_absence(raw):
    with patch("urllib.request.urlopen", side_effect=http_failure(404, io.BytesIO(raw))) as urlopen:
        invalid = paragon_component_template(BridgeClient(SETTINGS), ARGS)
    assert_one_post(urlopen)
    assert invalid["result"]["read_errors"][0]["code"] == "invalid_native_response"


def test_truncated_404_survives_as_transport_failure_without_feature_absence():
    class IncompleteResponse(io.BytesIO):
        def read(self, *args):
            raise http.client.IncompleteRead(b'{"ok":', 100)

    with patch("urllib.request.urlopen", side_effect=http_failure(404, IncompleteResponse())) as urlopen, \
         pytest.raises(BridgeError) as caught:
        paragon_component_template(BridgeClient(SETTINGS), ARGS)
    assert_one_post(urlopen)
    assert caught.value.http_status == 404 and caught.value.transport_error is True


def test_ordinary_http_error_is_raised_unchanged_by_the_consumer():
    original = BridgeError("Bridge is busy.", http_status=409, diagnostics=PROVENANCE)
    bridge = RecordingBridge(original)
    with pytest.raises(BridgeError) as caught:
        paragon_component_template(bridge, ARGS)
    assert caught.value is original
    assert bridge.calls == [("POST", PATH, ARGS, VERSION)]

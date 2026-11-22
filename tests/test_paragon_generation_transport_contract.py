"""Keep PARAGON plan evidence native across the actual HTTP decoder."""
import io
import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError, _response_failure_diagnostics
from aveva_e3d_mcp.config import Settings


READS = [("GET", "/api/v1/module/context"),
         ("POST", "/api/v1/piping/specification-read"),
         ("POST", "/api/v1/paragon/catalogue-manifest"),
         ("POST", "/api/v1/paragon/catalogue-schema")]


class JsonResponse(io.BytesIO):
    def __init__(self, value):
        super().__init__(json.dumps(value).encode("utf-8"))


def client():
    return BridgeClient(Settings(mode="bridge", version="E3D21", allow_commands=False))


@pytest.mark.parametrize("method,path", READS)
def test_missing_native_version_is_not_filled_from_plan_routing_configuration(method, path):
    value = {"ok": True, "session_id": "native-session", "bridge_build": "native-build", "result": {}}
    with patch("urllib.request.urlopen", return_value=JsonResponse(value)) as urlopen:
        result = client()._request(method, path, None if method == "GET" else {}, version="E3D21")
    urlopen.assert_called_once()
    assert result == value and "version" not in result


@pytest.mark.parametrize("method,path", READS)
@pytest.mark.parametrize("status", [200, 404, 500])
@pytest.mark.parametrize("marker", [
    {"error_code": "native_database_fault", "native_fault_latched": True, "retry_safe": False},
    {"details": {"nested": [{"native_database_fault": True, "code": 538}]}},
    {"diagnostics": [{"nested": {"error_code": "native_fault", "message": "native diagnosis"}}]},
    {"result": {"status": "failed", "native_fault": {"has_fault": True, "code": 538}}},
])
def test_native_fault_metadata_survives_success_error_and_404_envelopes_once(method, path, status, marker):
    value = {"ok": False, "version": "E3D21", "session_id": "native-session",
             "bridge_build": "native-build", "error": "Original native fault", **marker}
    transport = {"return_value": JsonResponse(value)} if status == 200 else {
        "side_effect": urllib.error.HTTPError("http://offline.invalid" + path, status, "fault", {}, JsonResponse(value))}
    with patch("urllib.request.urlopen", **transport) as urlopen, pytest.raises(BridgeError) as caught:
        client()._request(method, path, None if method == "GET" else {}, version="E3D21")
    urlopen.assert_called_once()
    assert caught.value.http_status == (None if status == 200 else status)
    assert caught.value.diagnostics == {
        "version": "E3D21", "session_id": "native-session", "bridge_build": "native-build", **marker}
    assert caught.value.outcome_uncertain is False


@pytest.mark.parametrize("_,path", READS)
def test_only_exact_read_routes_keep_fault_bearing_diagnostics_and_ordinary_results_stay_private(_, path):
    value = {"version": "E3D21", "session_id": "session", "bridge_build": "build",
             "result": {"ordinary": "not an error contract"}, "extra": "not forwarded"}
    assert _response_failure_diagnostics(path, value) == {
        "version": "E3D21", "session_id": "session", "bridge_build": "build"}
    assert _response_failure_diagnostics(path + "/other", value) == {}
    assert _response_failure_diagnostics(path + "-other", value) == {}

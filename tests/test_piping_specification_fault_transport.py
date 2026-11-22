"""Exercise public specification wiring and real HTTP decoding offline."""
from __future__ import annotations

from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import urllib.error
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError, _response_failure_diagnostics
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.piping_specification_read import (
    PATH_CANDIDATE as PATH, TOOL, PipingSpecificationReadRoute, piping_specification_read,
)
from aveva_e3d_mcp.tools import create_server
from test_piping_specification_read import ARGS, BUILD, SESSION, sample


ROUTE = PipingSpecificationReadRoute(PATH, True, True)


class JsonResponse(io.BytesIO):
    def __init__(self, value):
        super().__init__(json.dumps(value, allow_nan=False).encode("utf-8"))


def client():
    return BridgeClient(Settings(mode="bridge", version="E3D21", allow_commands=False))


def public_call(server, arguments=ARGS):
    return server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": TOOL, "arguments": arguments}})["result"]


def test_public_registration_uses_reviewed_exact_route_once_with_commands_disabled():
    receipt = sample()
    server = create_server(client().settings)
    with patch("urllib.request.urlopen", return_value=JsonResponse(receipt)) as urlopen:
        reply = public_call(server)
    assert reply.get("isError") is not True
    assert reply["structuredContent"] == receipt
    request = urlopen.call_args.args[0]
    assert request.method == "POST" and request.full_url.endswith(PATH)
    assert json.loads(request.data) == {**ARGS, "max_depth": 8, "limit": 64, "attributes": []}
    urlopen.assert_called_once()


@pytest.mark.parametrize("status", [200, 404, 500])
@pytest.mark.parametrize("marker", [
    {"error_code": "native_database_fault", "native_fault_latched": True, "retry_safe": False},
    {"error_code": "native_fault"}, {"native_fault_latched": True},
    {"state": {"nested": [{"native_database_fault": True}]}},
    {"state": {"nested": [{"native_fault": {"has_fault": True, "code": 538}}]}},
])
def test_wire_native_faults_survive_404_and_never_fallback_or_issue_diagnostics(status, marker):
    receipt = {"ok": False, "version": "E3D21", "session_id": SESSION, "bridge_build": BUILD,
               "error": "Native fault evidence", **deepcopy(marker)}
    transport = {"return_value": JsonResponse(receipt)} if status == 200 else {
        "side_effect": urllib.error.HTTPError("http://offline.invalid" + PATH, status, "fault", {}, JsonResponse(receipt))}
    bridge = client()
    with patch("urllib.request.urlopen", **transport) as urlopen, \
         patch.object(bridge, "health", side_effect=AssertionError("No fault diagnostics")), \
         patch.object(bridge, "project", side_effect=AssertionError("No context fallback")), \
         pytest.raises(BridgeError) as caught:
        piping_specification_read(bridge, ARGS, route=ROUTE)
    urlopen.assert_called_once()
    assert caught.value.diagnostics == {
        "version": "E3D21", "session_id": SESSION, "bridge_build": BUILD, **marker}
    assert caught.value.http_status == (None if status == 200 else status)
    assert caught.value.outcome_uncertain is False


def test_missing_native_version_is_preserved_and_public_validator_rejects_it():
    receipt = sample()
    del receipt["version"]
    with patch("urllib.request.urlopen", return_value=JsonResponse(receipt)) as urlopen:
        raw = client()._request("POST", PATH, ARGS, version="E3D21")
    urlopen.assert_called_once()
    assert "version" not in raw
    with patch("urllib.request.urlopen", return_value=JsonResponse(receipt)) as urlopen:
        reply = public_call(create_server(client().settings))
    urlopen.assert_called_once()
    assert reply["isError"] is True
    assert reply["structuredContent"]["result"]["read_errors"][0]["code"] == "invalid_native_response"


@pytest.mark.parametrize("layer", ["envelope", "before", "after"])
@pytest.mark.parametrize("field", ["session_id", "bridge_build"])
def test_public_wire_keeps_conflicting_or_missing_native_bindings_for_validation(layer, field):
    receipt = sample()
    target = receipt if layer == "envelope" else receipt["result"]["context"][layer]
    target[field] = "different-native-identity"
    with patch("urllib.request.urlopen", return_value=JsonResponse(receipt)) as urlopen:
        reply = public_call(create_server(client().settings))
    urlopen.assert_called_once()
    assert reply["isError"] is True
    assert reply["structuredContent"]["result"]["available"] is False
    assert target[field] == "different-native-identity"


def test_unavailable_bridge_returns_exact_feature_unavailable_without_alternate_path():
    receipt = {"ok": False, "version": "E3D21", "session_id": SESSION, "bridge_build": BUILD,
               "error": "Feature unavailable"}
    transport = urllib.error.HTTPError("http://offline.invalid" + PATH, 404, "missing", {}, JsonResponse(receipt))
    with patch("urllib.request.urlopen", side_effect=transport) as urlopen:
        reply = public_call(create_server(client().settings))
    urlopen.assert_called_once()
    assert reply["isError"] is True
    assert reply["structuredContent"]["result"]["read_errors"][0]["code"] == "native_feature_unavailable"


@pytest.mark.parametrize("settings", [Settings(mode="mock", version="E3D21"), Settings(mode="bridge", version="E3D31")])
def test_public_mock_or_other_version_does_not_contact_e3d(settings):
    with patch("urllib.request.urlopen", side_effect=AssertionError("No request in unavailable mode")) as urlopen:
        reply = public_call(create_server(settings))
    urlopen.assert_not_called()
    assert reply["structuredContent"]["result"]["available"] is False


def test_exact_typed_fault_allowlist_keeps_other_response_data_private():
    receipt = {"version": "E3D21", "session_id": SESSION, "bridge_build": BUILD,
               "error_code": "native_database_fault", "native_fault_latched": True, "retry_safe": False,
               "result": {"arbitrary": "not part of error contract"}, "extra": "private"}
    expected = {k: v for k, v in receipt.items() if k not in {"result", "extra"}}
    assert _response_failure_diagnostics(PATH, receipt) == expected
    assert _response_failure_diagnostics(PATH + "/extra", receipt) == {}
    assert _response_failure_diagnostics(PATH + "-other", receipt) == {}
    invalid = {key: 7 for key in expected}
    invalid.update(native_fault_latched=1, retry_safe=0)
    assert _response_failure_diagnostics(PATH, invalid) == {}

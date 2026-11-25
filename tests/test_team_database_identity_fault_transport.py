"""Public identity transport/provenance tests; urlopen is always mocked."""
from __future__ import annotations

from copy import deepcopy
import hashlib
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
from aveva_e3d_mcp.protocol import McpServer
from aveva_e3d_mcp.team_database_identity import (
    FEATURE, PATH, TOOL, _native_fault, register_team_database_identity_manifest,
    team_database_identity_manifest,
)
from aveva_e3d_mcp.tools import create_server
from test_team_database_identity import ARGS, BUILD, CONTEXT, SESSION, error, partial, reply


SETTINGS = Settings(mode="bridge", version="E3D21", allow_commands=False)


def healthy():
    return {"ok": True, "version": "E3D21", "session_id": SESSION, "bridge_build": BUILD,
            "connected": True, "diagnostic_error": None, "api_features": [FEATURE], "state": {
                "database_ready": True, "busy": False, "recovery_required": False,
                "native_database_fault": {"has_fault": False},
                "generated_csg_quarantine": {"active": False}}}


class JsonResponse(io.BytesIO):
    def __init__(self, value):
        super().__init__(json.dumps(value, allow_nan=False).encode("utf-8"))


class RecordingBridge:
    settings = SETTINGS

    def __init__(self, manifest, health=None):
        self.events = []
        self.manifest = manifest
        self.health_value = healthy() if health is None else health

    def _selected_version(self, version):
        assert version is None
        return "E3D21"

    def health(self, *, version):
        self.events.append(("health", version))
        if isinstance(self.health_value, BaseException):
            raise self.health_value
        return self.health_value

    def _request(self, method, path, payload, *, version):
        self.events.append((method, path, deepcopy(payload), version))
        if isinstance(self.manifest, BaseException):
            raise self.manifest
        return self.manifest

    def __getattr__(self, name):
        raise AssertionError(f"Unexpected identity fallback or diagnostics: {name}")


def expected_events(limit=64):
    return [("health", "E3D21"), ("POST", PATH, {**ARGS, "limit": limit}, "E3D21")]


def public_call(server, arguments=None):
    return server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": TOOL, "arguments": ARGS if arguments is None else arguments}})["result"]


def test_registration_is_read_only_and_has_only_the_frozen_caller_arguments(reply):
    bridge = RecordingBridge(reply)
    server = McpServer("offline-team-identity", "0")
    register_team_database_identity_manifest(server, bridge)
    definition = server._tools[TOOL][0]
    schema = definition["inputSchema"]
    assert set(schema["properties"]) == {"expected_context", "limit"}
    assert schema["required"] == ["expected_context"] and schema["additionalProperties"] is False
    assert schema["properties"]["limit"] == {
        "type": "integer", "minimum": 1, "maximum": 250, "default": 64,
        "description": "Independent maximum for each of TEAM, system DB records and current MDB arrays."}
    assert definition["annotations"] == {"readOnlyHint": True, "destructiveHint": False,
                                          "idempotentHint": True, "openWorldHint": False}
    result = public_call(server)
    assert result.get("isError") is not True
    assert result["structuredContent"]["native_observation"] is reply["result"]
    assert bridge.events == expected_events()


@pytest.mark.parametrize("arguments", [
    {}, {"expected_context": CONTEXT.upper()}, {"expected_context": CONTEXT + "\n"},
    {**ARGS, "limit": True}, {**ARGS, "limit": None}, {**ARGS, "limit": 1.0},
    {**ARGS, "limit": 0}, {**ARGS, "limit": 251},
    *[{**ARGS, name: "untrusted"} for name in ("session_id", "bridge_build", "team", "offset", "execute")],
])
def test_registered_invalid_arguments_never_read_health_or_manifest(reply, arguments):
    bridge = RecordingBridge(reply)
    server = McpServer("offline-team-identity", "0")
    register_team_database_identity_manifest(server, bridge)
    assert public_call(server, arguments)["isError"] is True
    assert bridge.events == []


def test_public_create_server_wiring_uses_health_then_exact_manifest_wire_with_commands_disabled(reply):
    server = create_server(SETTINGS)
    with patch("urllib.request.urlopen", side_effect=[JsonResponse(healthy()), JsonResponse(reply)]) as urlopen:
        result = public_call(server)
    assert result.get("isError") is not True
    assert result["structuredContent"]["native_observation"] == reply["result"]
    assert urlopen.call_count == 2
    health_request, manifest_request = [call.args[0] for call in urlopen.call_args_list]
    assert health_request.method == "GET" and health_request.full_url == SETTINGS.bridge_url + "/health"
    assert health_request.data is None
    assert manifest_request.method == "POST" and manifest_request.full_url == SETTINGS.bridge_url + PATH
    assert json.loads(manifest_request.data) == {**ARGS, "limit": 64}
    assert "?" not in manifest_request.full_url


@pytest.mark.parametrize("settings", [Settings(mode="mock", version="E3D21"),
                                      Settings(mode="bridge", version="E3D31"),
                                      Settings(mode="bridge", default_version="default")])
def test_mock_and_other_versions_are_unknown_without_any_transport(settings):
    bridge = BridgeClient(settings)
    with patch("urllib.request.urlopen", side_effect=AssertionError("No native IO")) as urlopen, \
         patch.object(bridge, "health", side_effect=AssertionError("No health in unavailable mode")):
        observed = team_database_identity_manifest(bridge, ARGS)
    urlopen.assert_not_called()
    assert observed["available"] is False and observed["native_observation"] is None
    assert observed["positive_matches"] == [] and observed["single_dispatch"] is None


def test_health_feature_absence_is_unavailable_after_one_independent_read(reply):
    health = healthy()
    health["api_features"] = ["database_state"]
    bridge = RecordingBridge(reply, health)
    assert team_database_identity_manifest(bridge, ARGS)["reason"] == "native_feature_unavailable"
    assert bridge.events == [("health", "E3D21")]


@pytest.mark.parametrize("path,value", [
    (("ok",), False), (("version",), "E3D31"), (("version",), None),
    (("session_id",), ""), (("session_id",), None),
    (("bridge_build",), BUILD.upper()), (("bridge_build",), "{" + BUILD + "}"),
    (("bridge_build",), "build-from-config"), (("bridge_build",), None),
    (("connected",), None), (("connected",), False), (("connected",), 1),
    (("state",), None), (("state", "database_ready"), None), (("state", "database_ready"), False),
    (("state", "busy"), True), (("state", "busy"), None),
    (("state", "recovery_required"), True), (("state", "recovery_required"), None),
    (("state", "native_database_fault"), None), (("state", "native_database_fault", "has_fault"), None),
    (("state", "generated_csg_quarantine"), None), (("state", "generated_csg_quarantine", "active"), True),
    (("state", "generated_csg_quarantine", "active"), None),
    (("api_features",), None), (("api_features",), [FEATURE, 1]),
    (("diagnostic_error",), "Command-state observation failed."),
])
def test_unready_unknown_or_malformed_health_never_dispatches_manifest(reply, path, value):
    health = healthy()
    target = health
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    bridge = RecordingBridge(reply, health)
    with pytest.raises(ValueError):
        team_database_identity_manifest(bridge, ARGS)
    assert bridge.events == [("health", "E3D21")]


@pytest.mark.parametrize("field", ["version", "session_id", "bridge_build"])
def test_health_missing_native_provenance_is_never_filled_from_config_or_manifest(reply, field):
    health = healthy()
    del health[field]
    bridge = BridgeClient(SETTINGS)
    with patch("urllib.request.urlopen", return_value=JsonResponse(health)) as urlopen, pytest.raises(ValueError):
        team_database_identity_manifest(bridge, ARGS)
    urlopen.assert_called_once()


@pytest.mark.parametrize("field", ["session_id", "bridge_build"])
def test_health_provenance_is_independent_even_if_manifest_layers_agree_on_forgery(reply, field):
    forged = "different-server-session" if field == "session_id" else "22222222-2222-4222-8222-222222222222"
    reply[field] = reply["result"]["context"][field] = forged
    bridge = RecordingBridge(reply)
    with pytest.raises(ValueError, match="envelope session/build"):
        team_database_identity_manifest(bridge, ARGS)
    assert bridge.events == expected_events()


def test_prior_context_is_never_refreshed_from_new_native_context(reply):
    new_context = hashlib.sha256(f"{SESSION}\nOTHER\nALL".encode()).hexdigest()
    for boundary in ("before", "after"):
        reply["result"]["context"][boundary].update(project="OTHER", context_id=new_context)
    bridge = RecordingBridge(reply)
    with pytest.raises(ValueError, match="expected_context binding"):
        team_database_identity_manifest(bridge, ARGS)
    assert bridge.events == expected_events()
    assert bridge.events[-1][2]["expected_context"] == CONTEXT


@pytest.mark.parametrize("field", ["version", "session_id", "bridge_build"])
def test_manifest_missing_native_provenance_is_preserved_for_strict_validation(reply, field):
    del reply[field]
    with patch("urllib.request.urlopen", side_effect=[JsonResponse(healthy()), JsonResponse(reply)]) as urlopen, \
         pytest.raises(ValueError):
        team_database_identity_manifest(BridgeClient(SETTINGS), ARGS)
    assert urlopen.call_count == 2
    assert field not in reply


def test_ordinary_partial_keeps_original_observations_without_inventing_native_available(reply):
    native = reply["result"]
    native["databases"][0]["fino"] = None
    native["databases"][0]["read_errors"] = [error("databases[0].FINO")]
    native["read_errors"] = deepcopy(native["databases"][0]["read_errors"])
    partial(native)
    original = deepcopy(reply)
    bridge = RecordingBridge(reply)
    observed = team_database_identity_manifest(bridge, ARGS)
    assert observed["available"] is True and observed["status"] == "partial"
    assert observed["native_observation"] is native and reply == original
    assert "available" not in native and "session_id" not in native and "bridge_build" not in native
    assert all(row["team"] is None for row in observed["positive_matches"])
    assert bridge.events == expected_events()


FAULT_MARKERS = [
    {"has_fault": True}, {"native_fault": True}, {"native_database_fault": True},
    {"native_fault_latched": True}, {"error_code": "native_database_fault"},
    {"error_code": "native_fault"}, {"code": "native_fault"},
    {"state": {"native_database_fault": {"has_fault": True, "code": 538}}},
    {"diagnostics": {"details": [{"native_fault_latched": True}]}},
    {"result": {"details": [{"native_database_fault": True}]}},
]


@pytest.mark.parametrize("stage", ["health", "manifest"])
@pytest.mark.parametrize("marker", FAULT_MARKERS)
@pytest.mark.parametrize("http_status", [None, 404, 500])
def test_original_fault_bridge_error_is_rethrown_unchanged_before_availability(reply, stage, marker, http_status):
    diagnostics = {"state": {"nested": [deepcopy(marker)]}, "retry_safe": False}
    original = BridgeError("Native fault; stop.", http_status=http_status, diagnostics=diagnostics)
    bridge = RecordingBridge(original if stage == "manifest" else reply,
                             original if stage == "health" else None)
    with pytest.raises(BridgeError) as caught:
        team_database_identity_manifest(bridge, ARGS)
    assert caught.value is original and caught.value.diagnostics is diagnostics
    assert bridge.events == ([("health", "E3D21")] if stage == "health" else expected_events())


@pytest.mark.parametrize("stage", ["health", "manifest"])
@pytest.mark.parametrize("marker", FAULT_MARKERS)
def test_raw_fault_reply_wins_before_schema_or_availability_and_remains_original(reply, stage, marker):
    raw = {"ok": False, "oversized_errors": ["ordinary"] * 5000, "detail": deepcopy(marker)}
    bridge = RecordingBridge(raw if stage == "manifest" else reply, raw if stage == "health" else None)
    with pytest.raises(BridgeError) as caught:
        team_database_identity_manifest(bridge, ARGS)
    assert caught.value.diagnostics is raw and "do not retry" in str(caught.value)
    assert bridge.events == ([("health", "E3D21")] if stage == "health" else expected_events())


@pytest.mark.parametrize("stage", ["health", "manifest"])
@pytest.mark.parametrize("http_status", [404, 500])
@pytest.mark.parametrize("marker", FAULT_MARKERS)
def test_real_urlopen_error_transport_preserves_native_faults_and_stops(stage, http_status, marker):
    envelope = {"ok": False, "version": "E3D21", "session_id": SESSION, "bridge_build": BUILD,
                "error": "Native fault stop", **deepcopy(marker)}
    path = "/health" if stage == "health" else PATH
    failure = urllib.error.HTTPError(SETTINGS.bridge_url + path, http_status, "native fault", {}, JsonResponse(envelope))
    responses = [failure] if stage == "health" else [JsonResponse(healthy()), failure]
    with patch("urllib.request.urlopen", side_effect=responses) as urlopen, pytest.raises(BridgeError) as caught:
        team_database_identity_manifest(BridgeClient(SETTINGS), ARGS)
    assert urlopen.call_count == (1 if stage == "health" else 2)
    assert caught.value.http_status == http_status and _native_fault(caught.value.diagnostics)
    assert caught.value.diagnostics == _response_failure_diagnostics(path, envelope)
    assert caught.value.outcome_uncertain is False


@pytest.mark.parametrize("stage", ["health", "manifest"])
@pytest.mark.parametrize("marker", FAULT_MARKERS)
def test_real_urlopen_success_body_raw_fault_is_checked_before_validation(stage, marker):
    envelope = {"ok": True, "result": {"unvalidated": [deepcopy(marker)]}}
    responses = [JsonResponse(envelope)] if stage == "health" else [JsonResponse(healthy()), JsonResponse(envelope)]
    with patch("urllib.request.urlopen", side_effect=responses) as urlopen, pytest.raises(BridgeError) as caught:
        team_database_identity_manifest(BridgeClient(SETTINGS), ARGS)
    assert urlopen.call_count == (1 if stage == "health" else 2)
    assert caught.value.diagnostics == envelope


def test_only_plain_manifest_404_is_feature_unavailable_with_no_retry_or_alternate_read(reply):
    original = BridgeError("Optional reader missing", http_status=404)
    bridge = RecordingBridge(original)
    result = team_database_identity_manifest(bridge, ARGS)
    assert result["reason"] == "native_feature_unavailable" and result["native_observation"] is None
    assert bridge.events == expected_events()
    health_bridge = RecordingBridge(reply, original)
    with pytest.raises(BridgeError) as caught:
        team_database_identity_manifest(health_bridge, ARGS)
    assert caught.value is original and health_bridge.events == [("health", "E3D21")]


@pytest.mark.parametrize("failure", [BridgeError("Offline", transport_error=True),
                                     BridgeError("Ordinary failure", http_status=500),
                                     BridgeError("Unready", http_status=409),
                                     BridgeError("Invalid response", response_invalid=True)])
def test_ordinary_manifest_errors_are_unchanged_and_never_retry(reply, failure):
    bridge = RecordingBridge(failure)
    with pytest.raises(BridgeError) as caught:
        team_database_identity_manifest(bridge, ARGS)
    assert caught.value is failure and bridge.events == expected_events()

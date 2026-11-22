"""Exercise the actual child-schema HTTP client without an E3D connection."""
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
from aveva_e3d_mcp.equipment_child_schema import PATH, equipment_child_schema
from test_equipment_child_schema import arguments, response


class JsonResponse(io.BytesIO):
    def __init__(self, value):
        super().__init__(json.dumps(value, allow_nan=False).encode("utf-8"))


def client():
    return BridgeClient(Settings(mode="bridge", version="E3D21", allow_commands=False))


@pytest.mark.parametrize("status", [200, 404, 500])
def test_wire_fault_metadata_survives_once_without_feature_fallback(status):
    receipt = {"ok": False, "version": "E3D21", "session_id": "native-session",
               "bridge_build": "native-build", "error": "A native database fault stopped this read.",
               "error_code": "native_database_fault", "native_fault_latched": True, "retry_safe": False}
    if status == 200:
        transport = dict(return_value=JsonResponse(receipt))
    else:
        transport = dict(side_effect=urllib.error.HTTPError(
            "http://offline.invalid" + PATH, status, "fault", {}, JsonResponse(receipt)))
    bridge = client()
    with patch("urllib.request.urlopen", **transport) as urlopen, \
         patch.object(bridge, "health", side_effect=AssertionError("No diagnostic follow-up")), \
         patch.object(bridge, "project", side_effect=AssertionError("No context fallback")):
        with pytest.raises(BridgeError) as raised:
            equipment_child_schema(bridge, arguments())
    urlopen.assert_called_once()
    error = raised.value
    assert error.diagnostics == {key: receipt[key] for key in (
        "version", "session_id", "bridge_build", "error_code", "native_fault_latched", "retry_safe")}
    assert error.http_status == (None if status == 200 else status)
    assert error.response_invalid is False
    assert error.outcome_uncertain is False  # This request contains no write path.


def test_missing_native_version_is_not_filled_from_configuration():
    receipt = response()
    del receipt["version"]
    with patch("urllib.request.urlopen", return_value=JsonResponse(receipt)) as urlopen:
        raw = client()._request("POST", PATH, arguments(), version="E3D21")
    urlopen.assert_called_once()
    assert "version" not in raw
    with patch("urllib.request.urlopen", return_value=JsonResponse(receipt)) as urlopen:
        result = equipment_child_schema(client(), arguments())["result"]
    urlopen.assert_called_once()
    assert result["available"] is False
    assert result["read_errors"][0]["code"] == "invalid_native_response"


@pytest.mark.parametrize("field,layer", [
    ("session_id", "envelope"), ("bridge_build", "envelope"),
    ("session_id", "result"), ("bridge_build", "result"),
    ("session_id", "before"), ("bridge_build", "before"),
    ("session_id", "after"), ("bridge_build", "after"),
])
def test_real_wire_preserves_identity_conflicts_for_validator(field, layer):
    receipt = response()
    original = deepcopy(receipt)
    target = receipt if layer == "envelope" else receipt["result"] if layer == "result" else \
        receipt["result"]["context"][layer]
    target[field] = "conflicting-observation"
    with patch("urllib.request.urlopen", return_value=JsonResponse(receipt)) as urlopen:
        result = equipment_child_schema(client(), arguments())["result"]
    urlopen.assert_called_once()
    assert result["available"] is False
    assert result["read_errors"][0]["code"] == "invalid_native_response"
    assert original != receipt


def test_diagnostic_allowlist_is_exact_typed_and_does_not_forward_arbitrary_data():
    receipt = {"version": "E3D21", "session_id": "session", "bridge_build": "build",
               "error_code": "native_database_fault", "native_fault_latched": True, "retry_safe": False,
               "result": {"arbitrary": "not part of the error contract"}, "extra": "not forwarded"}
    expected = {key: receipt[key] for key in (
        "version", "session_id", "bridge_build", "error_code", "native_fault_latched", "retry_safe")}
    assert _response_failure_diagnostics(PATH, receipt) == expected
    assert _response_failure_diagnostics(PATH + "-other", receipt) == {}
    assert _response_failure_diagnostics(PATH + "/extra", receipt) == {}
    malformed = {key: 7 for key in expected}
    malformed.update(native_fault_latched=1, retry_safe=0)
    assert _response_failure_diagnostics(PATH, malformed) == {}

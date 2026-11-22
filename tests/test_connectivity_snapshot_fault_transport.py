"""Native-fault evidence survives the real HTTP client without replay."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch
import urllib.error
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.piping_connectivity_snapshot import PATH, piping_connectivity_snapshot


class HttpError(urllib.error.HTTPError):
    def __init__(self, body):
        super().__init__("http://127.0.0.1/api/v1/piping/connectivity-snapshot", 500, "fault", {}, io.BytesIO(body))


class ConnectivitySnapshotFaultTransportTests(unittest.TestCase):
    def test_real_request_preserves_native_fault_marker_and_never_retries(self):
        body = json.dumps({"ok": False, "version": "E3D21", "session_id": "session",
                           "bridge_build": "build", "error_code": "native_database_fault",
                           "native_fault_latched": True, "retry_safe": False,
                           "error": "Do not continue"}).encode("utf-8")
        settings = Settings(mode="bridge", version="E3D21", bridge_url="http://127.0.0.1", allow_commands=False)
        bridge = BridgeClient(settings)
        with patch("urllib.request.urlopen", side_effect=HttpError(body)) as urlopen:
            with self.assertRaises(BridgeError) as raised:
                piping_connectivity_snapshot(bridge, {
                    "branches": [{"refno": "=1/1", "expected_type": "BRAN"}]
                })
        urlopen.assert_called_once()
        error = raised.exception
        self.assertEqual(error.http_status, 500)
        self.assertEqual(error.diagnostics.get("error_code"), "native_database_fault")
        self.assertIs(error.diagnostics.get("native_fault_latched"), True)
        self.assertIs(error.diagnostics.get("retry_safe"), False)
        self.assertEqual(error.diagnostics.get("bridge_build"), "build")

    def test_fault_marker_filter_is_typed_and_exact_route_only(self):
        from aveva_e3d_mcp.bridge import _response_failure_diagnostics
        payload = {"version": "E3D21", "session_id": "s", "bridge_build": "b",
                   "error_code": "native_database_fault", "native_fault_latched": True,
                   "retry_safe": False, "result": {"secret": "not forwarded"}}
        allowed = _response_failure_diagnostics(PATH, payload)
        self.assertEqual(allowed, {key: payload[key] for key in (
            "version", "session_id", "bridge_build", "error_code", "native_fault_latched", "retry_safe")})
        self.assertEqual(_response_failure_diagnostics("/api/v1/piping/connectivity-snapshot-evil", payload), {})
        wrong_types = {**payload, "native_fault_latched": 1, "retry_safe": 0, "error_code": 7}
        self.assertEqual(_response_failure_diagnostics(PATH, wrong_types), {
            "version": "E3D21", "session_id": "s", "bridge_build": "b"})


if __name__ == "__main__":
    unittest.main()

"""Preserve bounded PARAGON fault metadata through the real HTTP transport."""
from __future__ import annotations

from contextlib import ExitStack
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.paragon_catalogue import PATH, paragon_catalogue_manifest


def native_fault() -> dict:
    return {
        "ok": False, "version": "E3D21", "session_id": "native-session",
        "bridge_build": "native-build", "error_code": "native_database_fault",
        "native_fault_latched": True, "retry_safe": False,
        "error": "A native database fault stopped catalogue inspection.",
    }


def fault_metadata() -> dict:
    return {key: value for key, value in native_fault().items() if key not in {"ok", "error"}}


class ParagonFaultTransportTests(unittest.TestCase):
    def setUp(self):
        self.client = BridgeClient(Settings(mode="bridge", version="E3D21", allow_commands=False))

    def request_failure(self, path: str, envelope: dict, *, wrapper: bool = False,
                        http_error: bool = True) -> BridgeError:
        encoded = json.dumps(envelope, allow_nan=False).encode("utf-8")
        response = (patch("urllib.request.urlopen", side_effect=HTTPError(
            "http://bridge" + path, 500, "native failure", {}, io.BytesIO(encoded))) if http_error else
            patch("urllib.request.urlopen", return_value=io.BytesIO(encoded)))
        with ExitStack() as stack:
            request = stack.enter_context(response)
            followups = [stack.enter_context(patch.object(self.client, method)) for method in (
                "_recover_command_timeout", "cancel_command", "command_state", "health", "project", "capture_view")]
            with self.assertRaises(BridgeError) as caught:
                if wrapper:
                    paragon_catalogue_manifest(self.client, {"refno": "/CAT"})
                else:
                    self.client._request("POST", path, {"refno": "/CAT"}, version="E3D21")
        request.assert_called_once()
        for followup in followups:
            followup.assert_not_called()
        self.assertEqual(caught.exception.http_status, 500 if http_error else None)
        self.assertFalse(caught.exception.transport_error)
        self.assertFalse(caught.exception.response_invalid)
        return caught.exception

    def test_native_http_500_flags_survive_real_request_and_wrapper_without_followups(self):
        envelope = native_fault()
        envelope.update(result={"arbitrary": "native result must stay private"},
                        unrelated={"arbitrary": "envelope must stay private"})
        error = self.request_failure(PATH, envelope, wrapper=True)
        self.assertEqual(error.diagnostics, fault_metadata())
        self.assertIs(error.diagnostics["native_fault_latched"], True)
        self.assertIs(error.diagnostics["retry_safe"], False)
        self.assertIn("native database fault", str(error))

    def test_failure_envelope_on_http_200_keeps_the_same_fault_metadata(self):
        error = self.request_failure(PATH, native_fault(), wrapper=True, http_error=False)
        self.assertEqual(error.diagnostics, fault_metadata())

    def test_identity_and_error_code_fields_require_strings(self):
        for field in ("version", "session_id", "bridge_build", "error_code"):
            for value in (None, False, 17, [], {}):
                with self.subTest(field=field, value=value):
                    envelope = native_fault()
                    envelope[field] = value
                    expected = fault_metadata()
                    expected.pop(field)
                    self.assertEqual(self.request_failure(PATH, envelope, wrapper=True).diagnostics, expected)

    def test_native_flags_require_booleans_and_keep_both_boolean_values(self):
        for field in ("native_fault_latched", "retry_safe"):
            for value in (None, 0, 1, "false", [], {}):
                with self.subTest(field=field, value=value):
                    envelope = native_fault()
                    envelope[field] = value
                    expected = fault_metadata()
                    expected.pop(field)
                    self.assertEqual(self.request_failure(PATH, envelope, wrapper=True).diagnostics, expected)
            for value in (False, True):
                with self.subTest(field=field, value=value):
                    envelope = native_fault()
                    envelope[field] = value
                    expected = fault_metadata()
                    expected[field] = value
                    self.assertEqual(self.request_failure(PATH, envelope, wrapper=True).diagnostics, expected)

    def test_whitelist_matches_only_the_exact_catalogue_route(self):
        for path in (PATH + "/extra", PATH + "-extra", PATH + "/",
                     "/api/v1/paragon/other", "/api/v1/project", "/api/v1/command", "/unknown"):
            with self.subTest(path=path):
                self.assertEqual(self.request_failure(path, native_fault()).diagnostics, {})


if __name__ == "__main__":
    unittest.main()

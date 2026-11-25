from __future__ import annotations

import copy
import json
import sys
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class SelectionEditToolTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(mode="bridge", version="E3D21", allow_commands=False)
        self.server = create_server(self.settings)

    def call(self, args, *, allow=False, server=None):
        target = server or (create_server(Settings(mode="bridge", version="E3D21", allow_commands=True)) if allow else self.server)
        return target._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "e3d_set_graphical_selection", "arguments": args}})["result"]

    def test_discovery_declares_write_with_explicit_empty_clear_and_revision_guard(self):
        listed = self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        self.assertEqual(len(listed), 126)
        tool = next(t for t in listed if t["name"] == "e3d_set_graphical_selection")
        self.assertEqual(tool["annotations"], {
            "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False})
        self.assertEqual(set(tool["inputSchema"]["properties"]), {"refnos", "dry_run", "expected_revision"})
        self.assertEqual(tool["inputSchema"]["required"], ["refnos"])
        self.assertEqual(tool["inputSchema"]["properties"]["refnos"]["minItems"], 0)
        for text in ("separate UI callback", "addGraphics=false", "readback_verified", "no automatic rollback", "same-set"):
            self.assertIn(text, tool["description"])

    def test_invalid_inputs_never_reach_bridge(self):
        invalid = [{}, {"refnos": None}, {"refnos": "=1/2"}, {"refnos": ["=1/2"] * 33},
                   {"refnos": ["=1/2", " =001/02 "]}]
        invalid.extend({"refnos": [value]} for value in (
            None, True, 1, {}, "", "CE", "WORLD", "/PUMP", "=0/1", "=1/-1", "=+1/2", "=1/2/3",
            "=2147483648/1", "=1/2147483648", "=\u0661/2", "=1/2\nDELETE CE", "=1/" + "9" * 5000))
        invalid.extend({"refnos": [], "dry_run": value} for value in (None, 1, "false"))
        invalid.extend({"refnos": [], "expected_revision": value} for value in (None, "", "a" * 63, "g" * 64, True))
        invalid.extend({"refnos": [], key: value} for key, value in (
            ("capture_after", False), ("capture_width", 800), ("addGraphics", False), ("add_graphics", False),
            ("mode", "add"), ("version", "E3D21"), ("limit", 50)))
        with patch.object(BridgeClient, "set_graphical_selection") as write:
            for args in invalid:
                with self.subTest(args=str(args)[:100]):
                    self.assertTrue(self.call(args)["isError"])
        write.assert_not_called()

    def test_preview_needs_no_permission_and_clear_is_explicit(self):
        native = {"ok": True, "result": {"success": True, "dry_run": True, "revision": "a" * 64}}
        with patch.object(BridgeClient, "_request", return_value=native) as request:
            for refs, expected in (([], []), ([" =0001/02 ", "=2147483647/0"], ["=1/2", "=2147483647/0"])):
                result = self.call({"refnos": refs})
                self.assertEqual(result["structuredContent"], native)
                self.assertFalse(result.get("isError", False))
                request.assert_called_with("POST", "/api/v1/view/graphical-selection", {"refnos": expected, "dry_run": True}, version=None)
            self.assertEqual(request.call_count, 2)

    def test_actual_requires_permission_and_preview_revision_before_dispatch(self):
        with patch.object(BridgeClient, "_request", return_value={"ok": True}) as request:
            self.assertTrue(self.call({"refnos": [], "dry_run": False, "expected_revision": "a" * 64})["isError"])
            self.assertTrue(self.call({"refnos": [], "dry_run": False}, allow=True)["isError"])
            request.assert_not_called()
            self.assertFalse(self.call({"refnos": [], "dry_run": False, "expected_revision": "A" * 64}, allow=True).get("isError", False))
        request.assert_called_once_with("POST", "/api/v1/view/graphical-selection", {
            "refnos": [], "dry_run": False, "expected_revision": "a" * 64}, version=None)

    def test_native_failure_partial_unknown_and_noop_receipts_are_preserved(self):
        for state in ("verified", "not_confirmed", "unknown", "rejected"):
            data = {"success": state == "verified", "dry_run": False, "verification_state": state,
                    "write_attempted": state != "rejected", "readback_verified": state == "verified",
                    "no_change": state == "verified", "retry_safe": False,
                    "outcome_uncertain": state == "unknown", "display_unchanged": None,
                    "readback": {"complete": False, "refnos": ["=1/2"]},
                    "errors": [] if state == "verified" else [{"code": state}]}
            native = {"ok": True, "result": data}
            original = copy.deepcopy(native)
            with self.subTest(state=state), patch.object(BridgeClient, "_request", return_value=native) as request, \
                    patch.object(BridgeClient, "command") as pml, patch.object(BridgeClient, "cancel_command") as cancel, \
                    patch.object(BridgeClient, "capture_view") as capture:
                result = self.call({"refnos": ["=1/2"], "dry_run": False, "expected_revision": "a" * 64}, allow=True)
            self.assertEqual(result.get("isError", False), state != "verified")
            self.assertEqual(result["structuredContent"], original)
            self.assertEqual(native, original)
            request.assert_called_once()
            for followup in (pml, cancel, capture):
                followup.assert_not_called()

    def test_transport_invalid_response_and_unstructured_http_actual_are_unknown_without_retry(self):
        for failure in (BridgeError("closed", transport_error=True), BridgeError("bad JSON", response_invalid=True),
                        BridgeError("HTTP 500", http_status=500, diagnostics={"state": {"database_ready": False}})):
            with self.subTest(failure=str(failure)), patch.object(BridgeClient, "_request", side_effect=failure) as request, \
                    patch.object(BridgeClient, "command_state") as state, patch.object(BridgeClient, "cancel_command") as cancel, \
                    patch.object(BridgeClient, "graphical_selection") as read, patch.object(BridgeClient, "capture_view") as capture:
                result = self.call({"refnos": [], "dry_run": False, "expected_revision": "a" * 64}, allow=True)
            self.assertTrue(result["isError"])
            data = result["structuredContent"]
            self.assertTrue(data["outcome_uncertain"])
            self.assertFalse(data["retry_safe"])
            self.assertIsNone(data["write_attempted"])
            self.assertFalse(data["readback_verified"])
            if failure.diagnostics:
                self.assertEqual(data["state"], failure.diagnostics["state"])
            request.assert_called_once()
            for followup in (state, cancel, read, capture):
                followup.assert_not_called()

    def test_preview_transport_failure_does_not_invent_write_uncertainty(self):
        with patch.object(BridgeClient, "_request", side_effect=BridgeError("closed", transport_error=True)) as request:
            result = self.call({"refnos": []})
        self.assertTrue(result["isError"])
        self.assertNotIn("outcome_uncertain", json.dumps(result))
        request.assert_called_once()

    def test_http_error_preserves_original_backend_write_receipt(self):
        original = {"ok": False, "error": "native fault", "state": {"database_ready": False}, "result": {
            "success": False, "write_attempted": True, "clear_completed": True, "add_completed": False,
            "outcome_uncertain": True, "retry_safe": False, "verification_state": "unknown"}}
        failure = urllib.error.HTTPError("http://127.0.0.1", 500, "failure", {}, BytesIO(json.dumps(original).encode()))
        with patch("urllib.request.urlopen", side_effect=failure) as request:
            result = self.call({"refnos": ["=1/2"], "dry_run": False, "expected_revision": "a" * 64}, allow=True)
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["result"], original["result"])
        self.assertEqual(result["structuredContent"]["state"], original["state"])
        request.assert_called_once()

    def test_direct_bridge_validates_numeric_identities_and_version(self):
        client = BridgeClient(self.settings)
        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.set_graphical_selection(["=0001/0"], expected_revision="B" * 64, version="E3D21")
            request.assert_called_once_with("POST", "/api/v1/view/graphical-selection", {
                "refnos": ["=1/0"], "dry_run": True, "expected_revision": "b" * 64}, version="E3D21")
            request.reset_mock()
            for refs in (None, (), ["/PUMP"], ["=1/2", "=01/002"]):
                with self.assertRaises(ValueError):
                    client.set_graphical_selection(refs)
            with self.assertRaises(ValueError):
                client.set_graphical_selection([], dry_run=False)
            request.assert_not_called()

    def test_mock_preview_and_actual_never_fabricate_selection_or_revision(self):
        server = create_server(Settings(mode="mock", version="E3D21", allow_commands=True))
        with patch.object(BridgeClient, "_request") as request:
            for dry_run in (True, False):
                result = self.call({"refnos": [], "dry_run": dry_run, "expected_revision": "a" * 64}, server=server)
                self.assertTrue(result["isError"])
                data = result["structuredContent"]["result"]
                for flag in ("success", "available", "write_attempted", "readback_verified", "outcome_uncertain", "retry_safe"):
                    self.assertFalse(data[flag])
                for key in ("revision", "readback", "ce_unchanged", "context_unchanged", "display_unchanged"):
                    self.assertNotIn(key, data)
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()

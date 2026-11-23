"""Offline contracts for session-guarded script submission and receipt lookup."""
from __future__ import annotations

import io
import http.client
import json
from pathlib import Path
import sys
import unittest
import urllib.error
import urllib.parse
import uuid
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError, pml_script_request_id
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class PmlScriptStatusToolsTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(mode="bridge", version="E3D21", allow_commands=True)
        self.client = BridgeClient(self.settings)
        self.server = create_server(self.settings)
        # A version 1 UUID deliberately proves the contract is not UUID4-only.
        self.request_id = "12345678-1234-1234-abcd-1234567890ab"
        self.session = "session-one"
        self.source = "$P PRIVATE-SCRIPT-SOURCE\n"

    def tool(self, name, arguments, server=None):
        return (server or self.server)._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}})["result"]

    def lookup(self):
        return {"request_id": self.request_id, "expected_session_id": self.session,
                "status_tool": "e3d_pml_script_status"}

    def test_execution_generates_uuid_and_gets_session_before_single_post(self):
        operation = {"ok": True, "request_id": self.request_id, "session_id": self.session,
                     "result": {"success": True}}
        with patch.object(self.client, "_request", side_effect=[{"session_id": self.session}, operation]) as request, \
                patch("aveva_e3d_mcp.bridge.uuid.uuid4", return_value=uuid.UUID(self.request_id)) as generate:
            result = self.client.execute_pml_script(self.source, False, version="E3D21")
        generate.assert_called_once_with()
        self.assertEqual(request.call_args_list, [call("GET", "/health", version="E3D21"),
            call("POST", "/api/v1/pml-script", {"source": self.source, "dry_run": False,
                "request_id": self.request_id, "expected_session_id": self.session}, version="E3D21")])
        self.assertIs(result, operation)

    def test_explicit_session_and_normalized_id_skip_health(self):
        with patch.object(self.client, "_request", return_value={"ok": True}) as request, \
                patch.object(self.client, "health") as health, patch("aveva_e3d_mcp.bridge.uuid.uuid4") as generate:
            self.client.execute_pml_script(self.source, False, request_id=self.request_id.upper(),
                                           expected_session_id=self.session)
        health.assert_not_called()
        generate.assert_not_called()
        self.assertEqual(request.call_args.args[2]["request_id"], self.request_id)
        self.assertEqual(request.call_args.args[2]["expected_session_id"], self.session)

    def test_preview_validates_and_forwards_id_without_health_or_generating_id(self):
        with patch.object(self.client, "_request", return_value={"ok": True}) as request, \
                patch.object(self.client, "health") as health, patch("aveva_e3d_mcp.bridge.uuid.uuid4") as generate:
            self.client.execute_pml_script(self.source, request_id=self.request_id.upper())
        request.assert_called_once_with("POST", "/api/v1/pml-script", {
            "source": self.source, "dry_run": True, "request_id": self.request_id}, version=None)
        health.assert_not_called()
        generate.assert_not_called()

    def test_missing_or_invalid_health_session_never_submits(self):
        for health in ({}, {"session_id": None}, {"session_id": False}, {"session_id": ""},
                       {"session_id": " \t"}, {"session_id": []}, None):
            with self.subTest(health=health), patch.object(self.client, "_request", return_value=health) as request, \
                    patch.object(self.client, "_recover_command_timeout") as recover:
                with self.assertRaises(BridgeError) as caught:
                    self.client.execute_pml_script(self.source, False)
            request.assert_called_once_with("GET", "/health", version=None)
            recover.assert_not_called()
            self.assertIs(caught.exception.diagnostics["execution_attempted"], False)
            self.assertIs(caught.exception.diagnostics["outcome_uncertain"], False)
            self.assertNotIn("pml_script_request", caught.exception.diagnostics)

    def test_preflight_failure_is_known_non_execution_at_mcp_boundary(self):
        for capture_after in (False, True):
            for failure in (BridgeError("health timed out", transport_error=True),
                            BridgeError("invalid health JSON", response_invalid=True)):
                with self.subTest(capture=capture_after, error=str(failure)), \
                        patch.object(BridgeClient, "_request", side_effect=failure) as request, \
                        patch.object(BridgeClient, "_recover_command_timeout") as recover, \
                        patch.object(BridgeClient, "capture_view") as capture:
                    result = self.tool("e3d_execute_pml_script", {"source": self.source,
                        "dry_run": False, "capture_after": capture_after})
                request.assert_called_once_with("GET", "/health", version=None)
                recover.assert_not_called()
                capture.assert_not_called()
                self.assertTrue(result["isError"])
                metadata = result["structuredContent"]
                self.assertIs(metadata["execution_attempted"], False)
                self.assertIs(metadata["outcome_uncertain"], False)
                self.assertIs(metadata["retry_safe"], False)
                self.assertNotIn("pml_script_request", metadata)
                self.assertNotIn(self.source, json.dumps(result))

    def test_post_transport_failure_keeps_lookup_ids_without_replay_or_capture(self):
        for capture_after in (False, True):
            for failure in (BridgeError("request timed out", transport_error=True),
                            BridgeError("connection closed", transport_error=True),
                            BridgeError("invalid JSON", response_invalid=True)):
                with self.subTest(capture=capture_after, error=str(failure)), \
                        patch.object(BridgeClient, "_request", side_effect=failure) as request, \
                        patch.object(BridgeClient, "_recover_command_timeout") as recover, \
                        patch.object(BridgeClient, "command_state") as command_state, \
                        patch.object(BridgeClient, "cancel_command") as cancel, \
                        patch.object(BridgeClient, "capture_view") as capture:
                    response = self.tool("e3d_execute_pml_script", {"source": self.source, "dry_run": False,
                        "request_id": self.request_id.upper(), "expected_session_id": self.session,
                        "capture_after": capture_after})
                request.assert_called_once()
                recover.assert_not_called()
                command_state.assert_not_called()
                cancel.assert_not_called()
                capture.assert_not_called()
                metadata = response["structuredContent"]
                self.assertEqual(metadata["pml_script_request"], self.lookup())
                self.assertTrue(metadata["outcome_uncertain"])
                self.assertFalse(metadata["retry_safe"])
                self.assertNotIn(self.source, json.dumps(response))

    def test_timeout_and_disconnect_allow_explicit_read_only_receipt_lookup(self):
        for error_type in (TimeoutError, http.client.RemoteDisconnected):
            for status in ("pending", "completed", "unknown_or_expired"):
                stored = {"ok": True, "version": "E3D21", "session_id": self.session, "result": {
                    "available": True, "status": status, "request_id": self.request_id,
                    "receipt_available": status == "completed", "retry_safe": False,
                    "receipt": {"result": {"success": True}} if status == "completed" else None,
                }}
                with self.subTest(error=error_type.__name__, status=status), \
                        patch("urllib.request.urlopen", side_effect=[error_type(
                            "timed out" if error_type is TimeoutError else "connection closed"),
                            io.BytesIO(json.dumps(stored).encode("utf-8"))]) as request, \
                        patch.object(BridgeClient, "command_state") as command_state, \
                        patch.object(BridgeClient, "cancel_command") as cancel, \
                        patch.object(BridgeClient, "capture_view") as capture:
                    failed = self.tool("e3d_execute_pml_script", {"source": self.source, "dry_run": False,
                        "request_id": self.request_id, "expected_session_id": self.session, "capture_after": True})
                    self.assertTrue(failed["isError"])
                    metadata = failed["structuredContent"]
                    self.assertTrue(metadata["outcome_uncertain"])
                    self.assertFalse(metadata["retry_safe"])
                    self.assertNotIn("execution_completed", metadata)
                    self.assertEqual(metadata["receipt"]["capture"]["reason"], "operation_outcome_unknown")
                    lookup = metadata["pml_script_request"]
                    observed = self.tool(lookup["status_tool"], {"request_id": lookup["request_id"],
                        "expected_session_id": lookup["expected_session_id"]})
                command_state.assert_not_called()
                cancel.assert_not_called()
                capture.assert_not_called()
                self.assertEqual(request.call_count, 2)
                sent = [entry.args[0] for entry in request.call_args_list]
                self.assertEqual([entry.method for entry in sent], ["POST", "GET"])
                self.assertEqual(urllib.parse.urlparse(sent[0].full_url).path, "/api/v1/pml-script")
                status_url = urllib.parse.urlparse(sent[1].full_url)
                self.assertEqual(status_url.path, "/api/v1/pml-script/status")
                self.assertEqual(urllib.parse.parse_qs(status_url.query), {
                    "request_id": [self.request_id], "expected_session_id": [self.session]})
                self.assertEqual(observed["structuredContent"], stored)
                self.assertNotIn(self.source, json.dumps(failed))

    def test_uuid_validation_rejects_noncanonical_values_before_transport(self):
        invalid = [None, True, 1, [], {}, "", " " + self.request_id, self.request_id + "\n",
                   self.request_id.replace("-", ""), "{" + self.request_id + "}",
                   "urn:uuid:" + self.request_id, self.request_id[:-1] + "z"]
        with patch.object(self.client, "_request") as request:
            for value in invalid:
                with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                    pml_script_request_id(value)
                with self.assertRaises(ValueError):
                    self.client.pml_script_status(value, self.session)
            request.assert_not_called()
        self.assertEqual(pml_script_request_id(self.request_id.upper()), self.request_id)
        self.assertEqual(pml_script_request_id("00000000-0000-0000-0000-000000000000"),
                         "00000000-0000-0000-0000-000000000000")

    def test_status_discovery_is_read_only_and_has_only_two_required_arguments(self):
        definitions = self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        self.assertEqual(len(definitions), 115)
        tool = next(tool for tool in definitions if tool["name"] == "e3d_pml_script_status")
        self.assertEqual(set(tool["inputSchema"]["properties"]), {"request_id", "expected_session_id"})
        self.assertEqual(set(tool["inputSchema"]["required"]), {"request_id", "expected_session_id"})
        self.assertEqual(tool["inputSchema"]["properties"]["expected_session_id"]["pattern"], r"\S")
        self.assertFalse(tool["inputSchema"]["additionalProperties"])
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["destructiveHint"])
        self.assertTrue(tool["annotations"]["idempotentHint"])
        for text in ("pending", "unknown_or_expired", "64", "30 minutes", "65536", "request_id_reserved"):
            self.assertIn(text, tool["description"])

    def test_status_rejects_missing_invalid_or_extra_arguments_before_dispatch(self):
        valid = {"request_id": self.request_id, "expected_session_id": self.session}
        invalid = [{}, {"request_id": self.request_id}, {"expected_session_id": self.session},
                   *[{**valid, "request_id": value} for value in (None, False, "", "not-a-uuid")],
                   *[{**valid, "expected_session_id": value} for value in (None, False, "", " \n")],
                   *[{**valid, key: True} for key in ("version", "capture_after", "dry_run", "private-secret-key")]]
        with patch.object(BridgeClient, "pml_script_status") as lookup, patch.object(BridgeClient, "capture_view") as capture:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    response = self.tool("e3d_pml_script_status", arguments)
                    self.assertTrue(response["isError"])
                    self.assertNotIn("private-secret-key", json.dumps(response))
        lookup.assert_not_called()
        capture.assert_not_called()

    def test_status_preserves_pending_unknown_failed_completed_and_size_limited_receipts(self):
        receipts = [
            {"status": "pending", "receipt_available": False, "receipt": None},
            {"status": "unknown_or_expired", "request_id_reserved": True, "receipt_available": False, "receipt": None},
            {"status": "completed", "receipt_available": True,
             "receipt": {"http_status": 500, "envelope": {"ok": False, "error": "native failure"}}},
            {"status": "completed", "receipt_available": False,
             "receipt": {"http_status": 200, "omitted": True, "reason": "size_limit"}},
        ]
        server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))
        for result in receipts:
            envelope = {"ok": True, "session_id": self.session, "result": {"available": True, **result}}
            with self.subTest(status=result["status"]), \
                    patch.object(BridgeClient, "pml_script_status", return_value=envelope) as lookup, \
                    patch.object(BridgeClient, "execute_pml_script") as execute, \
                    patch.object(BridgeClient, "capture_view") as capture:
                response = self.tool("e3d_pml_script_status", {"request_id": self.request_id.upper(),
                    "expected_session_id": self.session}, server=server)
            lookup.assert_called_once_with(self.request_id, self.session)
            execute.assert_not_called()
            capture.assert_not_called()
            self.assertEqual(response["structuredContent"], envelope)
            self.assertFalse(response.get("isError", False))

    def test_status_http_get_encodes_original_session_and_never_posts(self):
        session = "original/session ?=&中文"
        envelope = {"ok": True, "version": "E3D21", "result": {"available": True, "status": "pending"}}
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(envelope).encode("utf-8"))) as request:
            result = self.client.pml_script_status(self.request_id.upper(), session, version="E3D21")
        request.assert_called_once()
        actual = request.call_args.args[0]
        self.assertEqual(actual.method, "GET")
        self.assertIsNone(actual.data)
        url = urllib.parse.urlparse(actual.full_url)
        self.assertEqual(url.path, "/api/v1/pml-script/status")
        self.assertEqual(urllib.parse.parse_qs(url.query), {"request_id": [self.request_id], "expected_session_id": [session]})
        self.assertEqual(result, envelope)

    def test_http_rejection_preserves_native_session_and_ids(self):
        native = {"ok": False, "error": "session mismatch", "request_id": self.request_id,
                  "session_id": "new-session", "execution_attempted": False, "outcome_uncertain": False}
        for status_only in (False, True):
            error = urllib.error.HTTPError("http://test/", 409, "Conflict", {}, io.BytesIO(json.dumps(native).encode()))
            with self.subTest(status=status_only), patch("urllib.request.urlopen", side_effect=error) as request, \
                    patch.object(self.client, "_recover_command_timeout") as recover:
                with self.assertRaises(BridgeError) as caught:
                    if status_only:
                        self.client.pml_script_status(self.request_id, self.session)
                    else:
                        self.client.execute_pml_script(self.source, False, request_id=self.request_id,
                                                       expected_session_id=self.session)
            request.assert_called_once()
            recover.assert_not_called()
            self.assertEqual(caught.exception.http_status, 409)
            self.assertEqual(caught.exception.diagnostics["session_id"], "new-session")
            self.assertEqual(caught.exception.diagnostics["request_id"], self.request_id)
            self.assertFalse(caught.exception.diagnostics["execution_attempted"])
            if not status_only:
                self.assertEqual(caught.exception.diagnostics["pml_script_request"], self.lookup())

    def test_mock_status_does_not_invent_a_native_receipt(self):
        server = create_server(Settings(mode="mock", version="E3D21", allow_commands=False))
        with patch.object(BridgeClient, "_request") as request:
            response = self.tool("e3d_pml_script_status", {"request_id": self.request_id,
                "expected_session_id": self.session}, server=server)
        request.assert_not_called()
        self.assertTrue(response["isError"])
        self.assertFalse(response["structuredContent"]["result"]["available"])
        self.assertFalse(response["structuredContent"]["result"]["receipt_available"])
        self.assertIsNone(response["structuredContent"]["result"]["receipt"])


if __name__ == "__main__":
    unittest.main()

"""Keep native failure receipts intact without widening PML diagnostic output."""
from __future__ import annotations

import copy
import http.client
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.protocol import McpServer
from aveva_e3d_mcp.tools import create_server


class BridgeFailureDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.client = BridgeClient(Settings(mode="bridge", version="E3D21"))

    def request_failure(self, path, envelope, *, http_error):
        encoded = json.dumps(envelope).encode("utf-8")
        if http_error:
            failure = HTTPError("http://bridge" + path, 500, "failure", {}, io.BytesIO(encoded))
            response = patch("urllib.request.urlopen", side_effect=failure)
        else:
            response = patch("urllib.request.urlopen", return_value=io.BytesIO(encoded))
        with response as request:
            with self.assertRaises(BridgeError) as caught:
                self.client._request("POST", path, {"dry_run": False})
        request.assert_called_once()
        self.assertEqual(caught.exception.http_status, 500 if http_error else None)
        self.assertFalse(caught.exception.transport_error)
        self.assertFalse(caught.exception.response_invalid)
        return caught.exception

    def test_native_mutation_error_routes_keep_complete_structured_results(self):
        # Exact production routes, rather than a prefix that could admit PML.
        paths = (
            "/api/v1/element/create", "/api/v1/element/copy", "/api/v1/element/primitive",
            "/api/v1/panel/create", "/api/v1/panel/update", "/api/v1/element/transform",
            "/api/v1/element/delete", "/api/v1/element/attribute", "/api/v1/element/attributes/set",
            "/api/v1/view/set", "/api/v1/view/camera/set", "/api/v1/view/fit", "/api/v1/view/restore",
            "/api/v1/drawlist/visual", "/api/v1/drawlist/remove", "/api/v1/view/primitive-visual",
            "/api/v1/view/graphical-selection", "/api/v1/clash/check",
        )
        native = {
            "success": False, "creation_attempted": True, "creation_stage": "PAVE 96",
            "creation_result_unknown": True, "outcome_uncertain": True, "retry_safe": False,
            "refno": "=1/101", "loop_refno": "=1/102", "residual_refno": "=1/101",
            "creation_journal": [{"refno": "=1/101", "type": "PANE", "children": ["=1/102"]}],
            "creation_journal_complete": False, "cleanup_attempted": True, "cleanup_succeeded": False,
            "cleanup": {"eligible": False, "reason": "unverified child remains"},
            "partial": True, "rollback_attempted": False, "rollback_succeeded": None,
            "error": "Native error 538 after a partial append", "ce_unchanged": None,
        }
        state = {"database_ready": False, "recovery_required": True}
        envelope = {"ok": False, "error": "Native operation failed", "result": native, "state": state,
                    "source": "private PML", "password": "private credential", "unrelated": {"x": 1}}
        before = copy.deepcopy(envelope)
        for path in paths:
            for http_error in (False, True):
                with self.subTest(path=path, http_error=http_error):
                    error = self.request_failure(path, envelope, http_error=http_error)
                    self.assertEqual(error.diagnostics, {"state": state, "result": native})
        self.assertEqual(envelope, before)

    def test_success_response_requires_boolean_ok_envelope(self):
        for ok in ("missing", None, 0, "true", []):
            with self.subTest(ok=ok):
                envelope = {"result": {"success": True, "refno": "=1/2"}}
                if ok != "missing":
                    envelope["ok"] = ok
                with patch("urllib.request.urlopen", return_value=io.BytesIO(
                        json.dumps(envelope, allow_nan=False).encode("utf-8"))):
                    with self.assertRaises(BridgeError) as caught:
                        self.client._request("GET", "/api/v1/element/primitive")
                self.assertTrue(caught.exception.response_invalid)
        with patch("urllib.request.urlopen", return_value=io.BytesIO(
                b'{"ok":true,"result":{"success":true}}')):
            self.assertEqual(self.client._request("GET", "/api/v1/element/primitive")["ok"], True)

    def test_normal_failure_response_requires_string_error(self):
        """The 200 response path must enforce the same failure envelope as HTTP errors."""
        for error_value in (None, 17, {"message": "not a string"}, ["bad"]):
            with self.subTest(error_value=error_value):
                body = json.dumps({"ok": False, "error": error_value,
                                   "result": {"success": False, "write_attempted": True,
                                               "retry_safe": False}}).encode("utf-8")
                with patch("urllib.request.urlopen", return_value=io.BytesIO(body)):
                    with self.assertRaises(BridgeError) as caught:
                        self.client._request("POST", "/api/v1/element/attribute",
                                             {"dry_run": False})
                self.assertTrue(caught.exception.response_invalid)
                self.assertFalse(caught.exception.transport_error)
                self.assertFalse(caught.exception.outcome_uncertain)
                self.assertEqual(caught.exception.diagnostics, {})

        valid = {"ok": False, "error": "native failure", "result": {
            "success": False, "write_attempted": True, "retry_safe": False}}
        with patch("urllib.request.urlopen", return_value=io.BytesIO(
                json.dumps(valid).encode("utf-8"))):
            with self.assertRaises(BridgeError) as caught:
                self.client._request("POST", "/api/v1/element/attribute",
                                     {"dry_run": False})
        self.assertFalse(caught.exception.response_invalid)
        self.assertEqual(str(caught.exception), "native failure")

    def test_http_error_response_requires_failure_ok_contract(self):
        valid = {"ok": False, "error": "native failure", "state": {"busy": False}}
        error = HTTPError("http://bridge/api/v1/element/primitive", 500, "failure", {},
                          io.BytesIO(json.dumps(valid).encode("utf-8")))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(BridgeError) as caught:
                self.client._request("GET", "/api/v1/element/primitive")
        self.assertFalse(caught.exception.response_invalid)
        self.assertEqual(caught.exception.diagnostics, {"state": valid["state"]})

        invalid = (
            {"error": "missing ok", "state": {"busy": False}},
            {"ok": True, "error": "success on HTTP error"},
            {"ok": "false", "error": "non-boolean ok"},
            {"ok": False},
            {"ok": False, "error": {"message": "not a string"},
             "result": {"success": False, "write_attempted": True, "retry_safe": False}},
        )
        for envelope in invalid:
            with self.subTest(envelope=envelope):
                error = HTTPError("http://bridge/api/v1/element/primitive", 500, "failure", {},
                                  io.BytesIO(json.dumps(envelope).encode("utf-8")))
                with patch("urllib.request.urlopen", side_effect=error):
                    with self.assertRaises(BridgeError) as caught:
                        self.client._request("GET", "/api/v1/element/primitive")
                self.assertTrue(caught.exception.response_invalid)

        # A few gateways historically emit a plain proxy error body without
        # the bridge envelope. Keep that narrow compatibility form working.
        proxy = HTTPError("http://bridge/api/v1/element/primitive", 502, "proxy failure", {},
                          io.BytesIO(b'{"error":"upstream unavailable"}'))
        with patch("urllib.request.urlopen", side_effect=proxy):
            with self.assertRaises(BridgeError) as caught:
                self.client._request("GET", "/api/v1/element/primitive")
        self.assertFalse(caught.exception.response_invalid)
        self.assertIn("upstream unavailable", str(caught.exception))


    def test_long_creation_journal_and_trailing_native_error_survive_real_tool_boundary(self):
        journal = [{"refno": f"=1/{1000 + i}", "type": "PAVE", "owner_refno": "=1/102",
                    "children": [], "creation_ordinal": i} for i in range(258)]
        native = {"success": False, "creation_journal": journal, "creation_journal_complete": False,
                  "cleanup_attempted": False, "cleanup_succeeded": None, "residual_refno": "=1/101",
                  "creation_result_unknown": True, "outcome_uncertain": True, "retry_safe": False,
                  "error": "Native 538: final appended vertex could not be verified"}
        envelope = {"ok": False, "error": "Panel creation failed", "result": native}
        encoded = json.dumps(envelope).encode("utf-8")
        self.assertGreater(len(encoded), 20000)
        for http_error in (False, True):
            for capture_after in (False, True):
                with self.subTest(http_error=http_error, capture_after=capture_after):
                    server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=True))
                    failure = HTTPError("http://bridge/api/v1/panel/create", 500, "failure", {}, io.BytesIO(encoded))
                    response = (patch("urllib.request.urlopen", side_effect=failure) if http_error else
                                patch("urllib.request.urlopen", return_value=io.BytesIO(encoded)))
                    with response as request, patch.object(BridgeClient, "capture_view") as capture:
                        result = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                            "name": "e3d_create_panel", "arguments": {
                                "parent_refno": "=1/2", "name": "/NEW", "vertices": [[0, 0], [10, 0], [0, 10]],
                                "thickness": 2, "dry_run": False, "capture_after": capture_after}}})["result"]
                    request.assert_called_once()
                    capture.assert_not_called()
                    self.assertTrue(result["isError"])
                    self.assertFalse(result["structuredContent"]["ok"])
                    self.assertEqual(result["structuredContent"]["result"], native)
                    self.assertEqual(result["structuredContent"]["result"]["creation_journal"][-1], journal[-1])
                    if capture_after:
                        self.assertEqual(result["structuredContent"]["receipt"]["capture"]["status"], "skipped")

    def test_native_success_envelope_with_failed_result_remains_intact(self):
        native = {"success": False, "cleanup_attempted": True, "residual_refno": "=1/101",
                  "creation_journal": [{"refno": "=1/101"}], "error": "Native verification failed"}
        envelope = {"ok": True, "version": "E3D21", "result": native}
        server = McpServer("test", "1")
        server.add_tool("create", "test", {"type": "object"},
                        lambda _: self.client._request("POST", "/api/v1/panel/create", {}))
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(envelope).encode())) as request:
            result = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                       "params": {"name": "create"}})["result"]
        request.assert_called_once()
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"], envelope)
        self.assertEqual(json.loads(result["content"][0]["text"]), envelope)

    def test_result_forwarding_does_not_expand_to_commands_scripts_or_unknown_routes(self):
        sensitive = {"source": "private script", "command": "private login command", "password": "private credential"}
        state = {"database_ready": False}
        script_metadata = {"request_id": "request", "session_id": "session", "execution_attempted": False,
                           "outcome_uncertain": False, "retry_safe": False}
        for path in ("/api/v1/command", "/api/v1/command-batch", "/api/v1/pml-script",
                     "/api/v1/pml-script/status", "/api/v1/project", "/api/v1/element/create/extra",
                     "/api/v1/panel/create-extra", "/unknown"):
            for http_error in (False, True):
                with self.subTest(path=path, http_error=http_error):
                    envelope = {"ok": False, "error": "Request rejected", "state": state,
                                "result": sensitive, **script_metadata, **sensitive}
                    error = self.request_failure(path, envelope, http_error=http_error)
                    expected = {"state": state}
                    if path in ("/api/v1/pml-script", "/api/v1/pml-script/status"):
                        expected.update(script_metadata)
                    self.assertEqual(error.diagnostics, expected)
                    self.assertNotIn("private", json.dumps(error.diagnostics))

    def test_nonobject_native_results_are_not_promoted_into_diagnostics(self):
        for value in (None, [], "untrusted text", False, 5):
            for http_error in (False, True):
                with self.subTest(value=value, http_error=http_error):
                    error = self.request_failure("/api/v1/panel/create", {
                        "ok": False, "error": "Request rejected", "result": value}, http_error=http_error)
                    self.assertEqual(error.diagnostics, {})

    def test_truncated_http_error_body_is_transport_failure_and_unknown_delete(self):
        class TruncatedBody:
            def read(self, *_args, **_kwargs):
                raise http.client.IncompleteRead(b'{"ok":false', 24)

            def close(self):
                pass

        def failure():
            return HTTPError("http://bridge/api/v1/element/delete", 500, "failure", {}, TruncatedBody())

        with patch("urllib.request.urlopen", side_effect=failure()):
            with self.assertRaises(BridgeError) as caught:
                self.client._request("POST", "/api/v1/element/delete", {"refno": "/PUMP", "dry_run": False})
        self.assertEqual(caught.exception.http_status, 500)
        self.assertTrue(caught.exception.transport_error)
        self.assertFalse(caught.exception.response_invalid)
        self.assertEqual(caught.exception.diagnostics, {})

        server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=True))
        with patch("urllib.request.urlopen", side_effect=failure()) as request, \
                patch.object(BridgeClient, "capture_view") as capture:
            response = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "e3d_delete_element", "arguments": {
                    "refno": "/PUMP", "dry_run": False, "capture_after": True}}})["result"]
        request.assert_called_once()
        capture.assert_not_called()
        self.assertTrue(response["isError"])
        operation = response["structuredContent"]
        self.assertTrue(operation["outcome_uncertain"])
        self.assertFalse(operation["retry_safe"])
        self.assertEqual(operation["receipt"]["capture"]["reason"], "operation_outcome_unknown")


class ProtocolFailureDiagnosticsTests(unittest.TestCase):
    def test_diagnostics_cannot_override_the_authoritative_failure_envelope(self):
        diagnostics = {"ok": True, "error": "forged success", "result": {"success": True},
                       "state": {"database_ready": False}}
        original = copy.deepcopy(diagnostics)
        server = McpServer("test", "1")
        def fail(_):
            raise BridgeError("Actual bridge failure", diagnostics=diagnostics)
        server.add_tool("fail", "test", {"type": "object"}, fail)
        result = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                   "params": {"name": "fail"}})["result"]
        self.assertTrue(result["isError"])
        self.assertFalse(result["structuredContent"]["ok"])
        self.assertEqual(result["structuredContent"]["error"], "Actual bridge failure")
        self.assertEqual(result["structuredContent"]["result"], diagnostics["result"])
        self.assertEqual(result["structuredContent"]["state"], diagnostics["state"])
        self.assertEqual(result["content"], [{"type": "text", "text": "Actual bridge failure"}])
        self.assertEqual(diagnostics, original)

    def test_empty_or_nonobject_diagnostics_keep_the_legacy_failure_shape(self):
        for diagnostics in ({}, None, [], "untrusted", False):
            with self.subTest(diagnostics=diagnostics):
                server = McpServer("test", "1")
                def fail(_):
                    error = RuntimeError("Actual failure")
                    error.diagnostics = diagnostics
                    raise error
                server.add_tool("fail", "test", {"type": "object"}, fail)
                result = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                           "params": {"name": "fail"}})["result"]
                self.assertEqual(result, {"isError": True, "content": [{"type": "text", "text": "Actual failure"}]})


if __name__ == "__main__":
    unittest.main()

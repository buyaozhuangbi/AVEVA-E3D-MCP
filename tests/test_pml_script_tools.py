"""Offline MCP/HTTP contract for complete PML scripts; never requests E3D."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError, PML_SCRIPT_MAX_BYTES, pml_script_utf8
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server
from test_capture_receipts import _capture_payload


class PmlScriptToolsTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(mode="bridge", version="E3D21", allow_commands=True, bridge_timeout=0.2)
        self.server = create_server(self.settings)
        self.source = "  -- source formatting must survive\r\n!n = 1\r\nif (!n eq 1) then\r\n  $P READY\r\nendif\r\n"
        self.request_id = "12345678-1234-1234-abcd-1234567890ab"

    def call(self, arguments, server=None):
        return (server or self.server)._dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "e3d_execute_pml_script", "arguments": arguments},
        })["result"]

    def operation(self, *, success=True, dry_run=False):
        return {"ok": True, "version": "E3D21", "session_id": "session-one", "result": {
            "success": success, "dry_run": dry_run, "script_id": "script-one",
            "source_sha256": hashlib.sha256(self.source.encode("utf-8")).hexdigest(),
            "byte_count": len(self.source.encode("utf-8")), "line_count": 6,
            "source_echoed": False, "execution_attempted": not dry_run,
            "execution_completed": not dry_run, "execution_scope": "nested", "syntax_checked": False,
            "native_run_returned": success if not dry_run else None,
            "result": "", "error": "" if success else "Syntax error",
            "messages": [] if success else [{"type": "ERROR", "code": [47, 15]}],
            "outcome_uncertain": not success, "retry_safe": False,
            "encoding": {"code_page": 936, "name": "gb2312", "strict": True,
                         "byte_count": len(self.source.encode("utf-8")), "terminal_newline_added": False},
            "cleanup": {"attempted": not dry_run, "status": "deleted" if not dry_run else "not_needed",
                        "retained": False, "path": None, "error": None},
            "error_metadata": None,
        }}

    def test_discovery_source_contract_preview_default_and_mutation_annotations(self):
        discovery = self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})["result"]
        tools = {item["name"]: item for item in discovery["tools"]}
        self.assertEqual(len(tools), 126)
        tool = tools["e3d_execute_pml_script"]
        schema = tool["inputSchema"]
        self.assertEqual(schema["required"], ["source"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["properties"]), {"source", "dry_run", "expected_session_id", "request_id",
                                                    "capture_after", "capture_width", "capture_height"})
        self.assertEqual(schema["properties"]["source"]["minLength"], 1)
        self.assertEqual(schema["properties"]["source"]["maxLength"], 262144)
        self.assertEqual(schema["properties"]["source"]["pattern"], r"\S")
        self.assertEqual(schema["properties"]["source"]["not"], {"pattern": r"\x00"})
        self.assertEqual(schema["properties"]["source"]["x-maxUtf8Bytes"], PML_SCRIPT_MAX_BYTES)
        self.assertEqual(schema["properties"]["expected_session_id"]["pattern"], r"\S")
        self.assertIs(schema["properties"]["dry_run"]["default"], True)
        self.assertFalse(tool["annotations"]["readOnlyHint"])
        self.assertTrue(tool["annotations"]["destructiveHint"])
        self.assertFalse(tool["annotations"]["idempotentHint"])
        self.assertTrue(tool["annotations"]["openWorldHint"])
        for text in ("UTF-8 bytes", "code-page", "does not check syntax", "partially executed", "cleanup", "source_sha256"):
            self.assertIn(text, tool["description"])
        instructions = self.server._dispatch({"jsonrpc": "2.0", "id": 2, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "pml-script-test", "version": "1.0"}}})["result"]["instructions"]
        self.assertIn("e3d_execute_pml_script", instructions)
        self.assertIn("IF/DO", instructions)

    def test_schema_rejects_utf8_overflow_nul_and_whitespace_before_handler(self):
        with patch("aveva_e3d_mcp.tools._execute_pml_script") as handler:
            server = create_server(self.settings)
            invalid = [
                {"source": "汉" * 87382},
                {"source": "valid\x00source"},
                {"source": " \t\r\n"},
                {"source": "\ud800"},
                {"source": self.source, "expected_session_id": " \t"},
            ]
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    response = self.call(arguments, server=server)
                    self.assertTrue(response["isError"])
        handler.assert_not_called()

    def test_preview_forwards_exact_source_and_never_captures(self):
        server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))
        for with_capture in (False, True):
            with self.subTest(capture=with_capture), patch.object(BridgeClient, "execute_pml_script", return_value=self.operation(dry_run=True)) as execute, \
                    patch.object(BridgeClient, "capture_view") as capture:
                response = self.call({"source": self.source, "capture_after": with_capture}, server=server)
            execute.assert_called_once_with(self.source, True)
            capture.assert_not_called()
            self.assertFalse(response.get("isError", False), response)
            self.assertNotIn(self.source, json.dumps(response))
            if with_capture:
                self.assertEqual(response["structuredContent"]["receipt"]["capture"]["reason"], "dry_run")

    def test_explicit_session_guard_and_source_are_forwarded_verbatim(self):
        operation = self.operation()
        with patch.object(BridgeClient, "execute_pml_script", return_value=operation) as execute:
            response = self.call({"source": self.source, "dry_run": False, "expected_session_id": "session-one"})
        execute.assert_called_once_with(self.source, False, expected_session_id="session-one")
        self.assertEqual(response["structuredContent"], operation)
        self.assertFalse(response.get("isError", False))

    def test_invalid_input_never_dispatches_and_does_not_echo_source(self):
        invalid = [
            {}, {"source": None}, {"source": False}, {"source": 123}, {"source": []}, {"source": {}},
            {"source": ""}, {"source": " \t\r\n"}, {"source": "private-source\x00suffix"},
            {"source": "private-source\ud800suffix"}, {"source": "private-source\udfff"},
            {"source": "x" * (PML_SCRIPT_MAX_BYTES + 1)}, {"source": "汉" * 87382},
            {"source": self.source, "dry_run": None}, {"source": self.source, "dry_run": 0},
            {"source": self.source, "dry_run": "false"}, {"source": self.source, "version": "E3D31"},
            {"source": self.source, "unknown-secret-key": True},
            *[{"source": self.source, "expected_session_id": value} for value in (None, False, 1, "", " ")],
            *[{"source": self.source, "request_id": value} for value in (None, False, 1, "", " ", "not-a-uuid",
                "1234567812341234abcd1234567890ab", "{12345678-1234-1234-abcd-1234567890ab}")],
            {"source": self.source, "capture_after": "true"}, {"source": self.source, "capture_width": True},
            {"source": self.source, "capture_height": 2049},
        ]
        with patch.object(BridgeClient, "execute_pml_script") as execute, patch.object(BridgeClient, "capture_view") as capture:
            for index, arguments in enumerate(invalid):
                with self.subTest(case=index):
                    response = self.call(arguments)
                    self.assertTrue(response["isError"])
                    self.assertNotIn("private-source", json.dumps(response))
                    self.assertNotIn("unknown-secret-key", json.dumps(response))
        execute.assert_not_called()
        capture.assert_not_called()

    def test_utf8_byte_cap_accepts_boundary_without_normalizing_source(self):
        for source in ("x" * PML_SCRIPT_MAX_BYTES, "汉" * 87381 + "a"):
            with self.subTest(character_count=len(source)), patch.object(BridgeClient, "execute_pml_script", return_value=self.operation(dry_run=True)) as execute:
                self.assertEqual(len(pml_script_utf8(source)), PML_SCRIPT_MAX_BYTES)
                result = self.call({"source": source})
                self.assertFalse(result.get("isError", False))
                execute.assert_called_once_with(source, True)
        self.assertEqual(pml_script_utf8("$P 😀\n"), "$P 😀\n".encode("utf-8"))

    def test_write_permission_blocks_execution_before_bridge_or_capture(self):
        server = create_server(Settings(mode="bridge", allow_commands=False))
        with patch.object(BridgeClient, "execute_pml_script") as execute, patch.object(BridgeClient, "capture_view") as capture:
            response = self.call({"source": self.source, "dry_run": False, "capture_after": True}, server=server)
        self.assertTrue(response["isError"])
        self.assertIn("E3D_MCP_ALLOW_COMMANDS", response["structuredContent"]["error"])
        execute.assert_not_called()
        capture.assert_not_called()

    def test_native_partial_failure_preserves_cleanup_encoding_and_error_without_capture(self):
        operation = self.operation(success=False)
        operation["result"]["cleanup"].update(status="retained", retained=True, path=r"C:\scripts\one.pmlmac", error="locked")
        for requested in (False, True):
            with self.subTest(capture=requested), patch.object(BridgeClient, "execute_pml_script", return_value=operation) as execute, \
                    patch.object(BridgeClient, "capture_view") as capture:
                response = self.call({"source": self.source, "dry_run": False, "capture_after": requested})
            execute.assert_called_once()
            capture.assert_not_called()
            self.assertTrue(response["isError"])
            self.assertEqual(response["structuredContent"]["result"], operation["result"])
            self.assertIsNone(response["structuredContent"]["result"]["error_metadata"])
            if requested:
                self.assertEqual(response["structuredContent"]["receipt"]["capture"]["status"], "skipped")
                self.assertFalse(response["structuredContent"]["receipt"]["operation_replayed"])

    def test_success_captures_once_and_retains_native_result(self):
        operation = self.operation()
        with patch.object(BridgeClient, "execute_pml_script", return_value=operation) as execute, \
                patch.object(BridgeClient, "capture_view", return_value=_capture_payload()) as capture, \
                patch.object(BridgeClient, "restore_view") as restore, patch.object(BridgeClient, "fit_view") as fit:
            response = self.call({"source": self.source, "dry_run": False, "capture_after": True,
                                  "capture_width": 64, "capture_height": 64})
        execute.assert_called_once_with(self.source, False)
        capture.assert_called_once_with(64, 64)
        restore.assert_not_called()
        fit.assert_not_called()
        self.assertFalse(response.get("isError", False))
        self.assertEqual(response["structuredContent"]["result"], operation["result"])
        self.assertEqual(response["structuredContent"]["receipt"]["capture"]["status"], "captured")
        self.assertEqual(len([entry for entry in response["content"] if entry["type"] == "image"]), 1)
        self.assertFalse(response["structuredContent"]["receipt"]["operation_replayed"])

    def test_capture_failure_does_not_replay_or_turn_confirmed_script_into_failure(self):
        operation = self.operation()
        with patch.object(BridgeClient, "execute_pml_script", return_value=operation) as execute, \
                patch.object(BridgeClient, "capture_view", side_effect=BridgeError("capture failed")) as capture:
            response = self.call({"source": self.source, "dry_run": False, "capture_after": True})
        execute.assert_called_once()
        capture.assert_called_once()
        self.assertFalse(response.get("isError", False))
        self.assertEqual(response["structuredContent"]["result"], operation["result"])
        self.assertEqual(response["structuredContent"]["receipt"]["capture"]["status"], "failed")

    def test_http_forwarding_preserves_source_and_native_metadata(self):
        client = BridgeClient(self.settings)
        operation = self.operation()
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(operation).encode("utf-8"))) as request:
            result = client.execute_pml_script(self.source, False, version="E3D21", expected_session_id="session-one",
                                               request_id=self.request_id)
        request.assert_called_once()
        actual = request.call_args.args[0]
        self.assertEqual(actual.method, "POST")
        self.assertTrue(actual.full_url.endswith("/api/v1/pml-script"))
        self.assertEqual(json.loads(actual.data), {"source": self.source, "dry_run": False,
                                                "expected_session_id": "session-one", "request_id": self.request_id})
        self.assertEqual(result, operation)
        self.assertEqual(request.call_args.kwargs["timeout"], self.settings.bridge_timeout)

    def test_bridge_default_preview_and_invalid_input_do_not_mutate_or_send(self):
        client = BridgeClient(self.settings)
        with patch.object(client, "_request", return_value=self.operation(dry_run=True)) as request:
            client.execute_pml_script(self.source)
            request.assert_called_once_with("POST", "/api/v1/pml-script", {"source": self.source, "dry_run": True}, version=None)
            request.reset_mock()
            for source in (None, "", " \n", "secret\x00", "secret\ud800", "x" * (PML_SCRIPT_MAX_BYTES + 1)):
                with self.subTest(source_type=type(source).__name__), self.assertRaises(ValueError):
                    client.execute_pml_script(source)
            for kwargs in ({"dry_run": 1}, {"dry_run": None}, {"expected_session_id": ""}, {"expected_session_id": True}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    client.execute_pml_script(self.source, **kwargs)
            request.assert_not_called()

    def test_timeout_preserves_unknown_outcome_without_recovery_or_replay(self):
        client = BridgeClient(self.settings)
        timeout = BridgeError("request timed out", transport_error=True,
                              diagnostics={"transport_phase": "awaiting_response"})
        with patch.object(client, "_request", side_effect=[{"session_id": "session-one"}, timeout]) as request, \
                patch("aveva_e3d_mcp.bridge.uuid.uuid4", return_value=uuid.UUID(self.request_id)), \
                patch.object(client, "_recover_command_timeout") as recover, \
                patch.object(client, "command_state") as command_state, \
                patch.object(client, "cancel_command") as cancel:
            with self.assertRaises(BridgeError) as caught:
                client.execute_pml_script(self.source, False, version="E3D21")
        self.assertEqual(request.call_count, 2)
        self.assertEqual([call.args[0] for call in request.call_args_list], ["GET", "POST"])
        recover.assert_not_called()
        command_state.assert_not_called()
        cancel.assert_not_called()
        self.assertIs(caught.exception, timeout)
        diagnostics = caught.exception.diagnostics
        self.assertEqual(diagnostics["transport_phase"], "awaiting_response")
        self.assertTrue(diagnostics["outcome_uncertain"])
        self.assertFalse(diagnostics["retry_safe"])
        self.assertNotIn("execution_completed", diagnostics)
        self.assertNotIn("native_run_returned", diagnostics)
        self.assertEqual(diagnostics["pml_script_request"], {"request_id": self.request_id,
            "expected_session_id": "session-one", "status_tool": "e3d_pml_script_status"})
        self.assertNotIn(self.source, json.dumps(diagnostics))

    def test_preview_and_http_rejection_do_not_cancel_or_replay(self):
        client = BridgeClient(self.settings)
        for dry_run, error in ((True, BridgeError("timed out", transport_error=True)),
                               (False, BridgeError("session changed", http_status=409)),
                               (False, BridgeError("native timed out", http_status=500)),
                               (False, BridgeError("invalid JSON", response_invalid=True))):
            with self.subTest(preview=dry_run, error=str(error)), patch.object(client, "_request", side_effect=error) as request, \
                    patch.object(client, "_recover_command_timeout") as recover:
                with self.assertRaises(BridgeError):
                    client.execute_pml_script(self.source, dry_run, expected_session_id="session-one")
            request.assert_called_once()
            recover.assert_not_called()

    def test_transport_or_invalid_response_failure_keeps_uncertainty_and_skips_image(self):
        for failure in (BridgeError("connection closed", transport_error=True), BridgeError("invalid JSON", response_invalid=True)):
            with self.subTest(error=str(failure)), patch.object(BridgeClient, "execute_pml_script", side_effect=failure) as execute, \
                    patch.object(BridgeClient, "capture_view") as capture:
                response = self.call({"source": self.source, "dry_run": False, "capture_after": True})
            execute.assert_called_once()
            capture.assert_not_called()
            self.assertTrue(response["isError"])
            operation = response["structuredContent"]
            self.assertTrue(operation["outcome_uncertain"])
            self.assertFalse(operation["retry_safe"])
            self.assertEqual(operation["receipt"]["capture"]["reason"], "operation_outcome_unknown")

    def test_mock_never_fabricates_native_encoding_execution_file_or_image(self):
        server = create_server(Settings(mode="mock", version="E3D21", allow_commands=True))
        for dry_run in (True, False):
            with self.subTest(preview=dry_run), patch.object(BridgeClient, "capture_view") as capture:
                response = self.call({"source": self.source, "dry_run": dry_run, "capture_after": True}, server=server)
            capture.assert_not_called()
            self.assertTrue(response["isError"])
            operation = response["structuredContent"]["result"]
            self.assertFalse(operation["available"])
            self.assertFalse(operation["execution_attempted"])
            self.assertFalse(operation["execution_completed"])
            self.assertFalse(operation["syntax_checked"])
            self.assertFalse(operation["source_echoed"])
            self.assertIsNone(operation["encoding"])
            self.assertFalse(operation["cleanup"]["attempted"])
            self.assertIsNone(operation["cleanup"]["path"])
            self.assertEqual(operation["source_sha256"], hashlib.sha256(self.source.encode("utf-8")).hexdigest())
            self.assertNotIn(self.source, json.dumps(response))


if __name__ == "__main__":
    unittest.main()

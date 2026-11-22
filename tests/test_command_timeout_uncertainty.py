import sys
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class CommandTimeoutUncertaintyTests(unittest.TestCase):
    """Offline contract for command timeout recovery.

    A timeout only proves that the HTTP response was lost.  Even when a
    follow-up state poll says the command is idle, the original mutation may
    already have been applied, so callers must receive an explicit uncertain
    outcome and must never infer success or replay the command.
    """

    def setUp(self):
        self.client = BridgeClient(Settings(mode="bridge", bridge_timeout=0.2))
        # These tests must never contact a running E3D while inspecting a
        # recovery receipt. Individual cases replace this local read as needed.
        message_patch = patch.object(self.client, "_request", return_value={"ok": True, "messages": []})
        self.message_request = message_patch.start()
        self.addCleanup(message_patch.stop)

    def test_timeout_with_idle_state_is_still_uncertain(self):
        timeout = BridgeError("HTTP request timed out", transport_error=True)
        with patch.object(self.client, "command_state", return_value={
                "ok": True, "version": "E3D21",
                "state": {"busy": False, "database_ready": True}}) as state:
            result = self.client._recover_command_timeout(timeout, "E3D21")

        state.assert_called_once_with(version="E3D21")
        self.assertFalse(result["ok"])
        self.assertTrue(result["timeout"])
        self.assertTrue(result["outcome_uncertain"])
        self.assertFalse(result["retry_safe"])
        self.assertNotIn("success", result)
        self.assertNotIn("replayed", result)
        self.assertIsNone(result["pml_input_cleared"])
        self.assertTrue(result["message_diagnostics"]["available"])
        self.assertFalse(result["message_diagnostics"]["complete"])
        self.assertFalse(result["message_diagnostics"]["operation_correlated"])

    def test_timeout_after_cancel_and_idle_poll_remains_uncertain(self):
        timeout = BridgeError("HTTP request timed out", transport_error=True)
        states = iter([
            {"ok": True, "version": "E3D21",
             "state": {"busy": True, "database_ready": True}},
        ])
        with patch.object(self.client, "command_state", side_effect=lambda version=None: next(states)) as state, \
                patch.object(self.client, "cancel_command", return_value={
                    "ok": True,
                    "final_state": {"busy": False, "database_ready": True},
                    "settled": True,
                }) as cancel:
            result = self.client._recover_command_timeout(timeout, "E3D21")

        state.assert_called_once_with(version="E3D21")
        cancel.assert_called_once_with(version="E3D21")
        self.assertTrue(result["cancel_attempted"])
        self.assertFalse(result["ok"])
        self.assertTrue(result["outcome_uncertain"])
        self.assertFalse(result["retry_safe"])
        self.assertEqual(result["final_state"]["busy"], False)

    def test_execution_receipts_preserve_timeout_uncertainty_without_replaying(self):
        server = create_server(Settings(mode="bridge", allow_commands=True, bridge_timeout=0.2))
        for name, arguments in (
            ("e3d_execute_pml", {"command": "NEW BOX"}),
            ("e3d_execute_pml_batch", {"commands": ["NEW BOX", "XLEN 100"]}),
            ("e3d_execute_pml_script", {"source": "NEW BOX\nXLEN 100\n", "expected_session_id": "session-one",
                                        "request_id": "12345678-1234-1234-abcd-1234567890ab"}),
        ):
            for capture_after in (False, True):
                with self.subTest(name=name, capture_after=capture_after), \
                        patch.object(BridgeClient, "_request", side_effect=[
                            BridgeError("HTTP request timed out", transport_error=True),
                            {"ok": True, "state": {"busy": False, "database_ready": True}},
                            {"ok": True, "messages": [{"type": "ERROR", "description": "(47,15) CP: Syntax error",
                                                       "details": " In line 4 of Command/Form Input\nSYSTEM secret-password\n"}]},
                        ]) as requests, patch.object(BridgeClient, "capture_view") as capture, \
                        patch.object(BridgeClient, "cancel_command") as cancel:
                    response = server._dispatch({
                        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": name, "arguments": {
                            **arguments, "dry_run": False, "capture_after": capture_after}},
                    })["result"]
                self.assertTrue(response["isError"])
                operation = response["structuredContent"]
                self.assertFalse(operation["ok"])
                self.assertTrue(operation["outcome_uncertain"])
                self.assertFalse(operation["retry_safe"])
                if name == "e3d_execute_pml_script":
                    requests.assert_called_once()
                    self.assertEqual(requests.call_args.args[:2], ("POST", "/api/v1/pml-script"))
                    self.assertEqual(operation["pml_script_request"], {
                        "request_id": arguments["request_id"], "expected_session_id": "session-one",
                        "status_tool": "e3d_pml_script_status"})
                    for field in ("message_diagnostics", "initial_state", "final_state", "execution_completed"):
                        self.assertNotIn(field, operation)
                else:
                    self.assertEqual(requests.call_count, 3)
                    self.assertEqual([call.args[0] for call in requests.call_args_list], ["POST", "GET", "GET"])
                    self.assertEqual(requests.call_args_list[-1].args[1], "/api/v1/messages")
                    self.assertEqual(requests.call_args_list[-1].kwargs["query"], {"tail": "20"})
                    summary = operation["message_diagnostics"]["messages"][0]
                    self.assertEqual((summary["code"], summary["category"], summary["line_number"]), ([47, 15], "syntax_error", 4))
                self.assertNotIn("secret-password", json.dumps(response))
                capture.assert_not_called()
                cancel.assert_not_called()

    def test_recent_message_summary_omits_arbitrary_text_and_credentials(self):
        secret = "do-not-echo-this-password"
        observed = {"ok": True, "messages": [
            {"type": "ERROR", "description": "(47,15) CP: Syntax error",
             "details": " In line 4 of Command/Form Input\nSYSTEM " + secret,
             "date": secret, "password": secret, "authorization": secret, "command": secret,
             "nested": {"credentials": secret}},
            {"type": secret, "description": "PASSWORD=" + secret, "details": secret, "category": secret},
            {"type": "ERROR", "description": "(2,109) Unsupported value " + secret,
             "details": " In line 18 of " + secret},
        ]}
        self.message_request.return_value = observed
        result = self.client._timeout_message_diagnostics("E3D21")
        self.assertTrue(result["available"])
        self.assertFalse(result["complete"])
        self.assertFalse(result["operation_correlated"])
        self.assertIsNone(result["has_more"])
        self.assertFalse(result["raw_text_included"])
        self.assertNotIn(secret, json.dumps(result))
        self.assertEqual(result["messages"][0], {"tail_index": 1, "type": "ERROR", "code": [47, 15],
                                                "category": "syntax_error", "line_number": 4})
        self.assertEqual(result["messages"][1]["type"], "UNKNOWN")
        self.assertEqual(result["messages"][2]["code"], [2, 109])
        self.assertIsNone(result["messages"][2]["line_number"])
        self.message_request.assert_called_once_with("GET", "/api/v1/messages", query={"tail": "20"},
                                                    version="E3D21", timeout_seconds=2.0)

    def test_message_failure_does_not_replace_original_timeout_or_echo_error_body(self):
        for failure in (BridgeError("PASSWORD=sensitive", transport_error=True),
                        BridgeError("SYSTEM sensitive", http_status=500),
                        BridgeError("sensitive", response_invalid=True)):
            with self.subTest(kind=vars(failure)), patch.object(self.client, "_request", side_effect=failure) as request, \
                    patch.object(self.client, "command_state", return_value={"ok": True, "state": {"busy": False}}):
                result = self.client._recover_command_timeout(BridgeError("original timed out", transport_error=True), None)
            request.assert_called_once()
            self.assertIn("original timed out", result["error"])
            self.assertTrue(result["outcome_uncertain"])
            self.assertFalse(result["retry_safe"])
            self.assertFalse(result["message_diagnostics"]["available"])
            self.assertEqual(result["message_diagnostics"]["read_errors"][0]["code"], "message_read_failed")
            self.assertNotIn("sensitive", json.dumps(result))

    def test_message_array_failure_is_not_an_empty_success(self):
        for observed in ({"ok": True}, {"ok": True, "messages": None}, {"ok": True, "messages": "sensitive"}):
            with self.subTest(observed=observed):
                self.message_request.return_value = observed
                result = self.client._timeout_message_diagnostics(None)
                self.assertFalse(result["available"])
                self.assertFalse(result["complete"])
                self.assertEqual(result["read_errors"][0]["code"], "invalid_messages")
                self.assertNotIn("sensitive", json.dumps(result))

    def test_message_tail_is_bounded_and_preserves_malformed_entry_diagnostics(self):
        self.message_request.return_value = {"ok": True, "messages": [
            {"type": "ERROR", "description": "(1,1) CP: Syntax error"},
            *[{"type": "WARNING", "description": "sensitive"} for _ in range(19)],
            "sensitive", {"type": "ERROR", "description": "Unhandled Database error 538"},
        ]}
        result = self.client._timeout_message_diagnostics(None)
        self.assertEqual(result["returned_count"], 19)
        self.assertEqual(result["messages"][-1]["tail_index"], 20)
        self.assertEqual(result["messages"][-1]["category"], "database_error")
        self.assertEqual([item["code"] for item in result["read_errors"]], ["message_limit_exceeded", "invalid_message"])
        self.assertNotIn("sensitive", json.dumps(result))
        self.assertFalse(result["complete"])

    def test_message_observation_uses_short_http_timeout_without_mutating_normal_timeout(self):
        for configured, expected in ((10.0, 2.0), (0.2, 0.2)):
            client = BridgeClient(Settings(mode="bridge", bridge_url="http://127.0.0.1:8766", bridge_timeout=configured))
            with self.subTest(configured=configured), patch("urllib.request.urlopen", return_value=io.BytesIO(
                    b'{"ok":true,"messages":[]}')) as request:
                result = client._timeout_message_diagnostics(None)
            request.assert_called_once()
            self.assertEqual(request.call_args.args[0].method, "GET")
            self.assertEqual(request.call_args.args[0].full_url, "http://127.0.0.1:8766/api/v1/messages?tail=20")
            self.assertEqual(request.call_args.kwargs["timeout"], expected)
            self.assertEqual(client.settings.bridge_timeout, configured)
            self.assertTrue(result["available"])
            self.assertEqual(result["returned_count"], 0)
            self.assertFalse(result["complete"])

    def test_http_500_with_timeout_text_does_not_start_timeout_recovery(self):
        client = BridgeClient(Settings(mode="bridge", bridge_timeout=0.2))
        state = {"busy": True, "database_ready": False, "recovery_required": True,
                 "native_database_fault": {"has_fault": True, "code": 538}}
        for batch in (False, True):
            failure = HTTPError("http://127.0.0.1:8766", 500, "failed", {}, io.BytesIO(json.dumps({
                "ok": False, "error": "Native command timed out", "state": state,
            }).encode()))
            with self.subTest(batch=batch), patch("urllib.request.urlopen", side_effect=failure) as request, \
                    patch.object(client, "_recover_command_timeout") as recover:
                with self.assertRaises(BridgeError) as caught:
                    if batch:
                        client.command_batch(["NEW BOX"], False)
                    else:
                        client.command("NEW BOX", False)
            request.assert_called_once()
            recover.assert_not_called()
            self.assertFalse(caught.exception.transport_error)
            self.assertEqual(caught.exception.http_status, 500)
            self.assertEqual(caught.exception.diagnostics["state"], state)

    def test_preview_timeout_does_not_cancel_an_unrelated_active_command(self):
        for batch in (False, True):
            with self.subTest(batch=batch), patch.object(self.client, "_request", side_effect=BridgeError(
                    "HTTP request timed out", transport_error=True)) as request, \
                    patch.object(self.client, "_recover_command_timeout") as recover:
                with self.assertRaises(BridgeError):
                    if batch:
                        self.client.command_batch(["NEW BOX"], True)
                    else:
                        self.client.command("NEW BOX", True)
            request.assert_called_once()
            recover.assert_not_called()

    def test_capture_fault_diagnostics_are_kept_with_confirmed_write(self):
        server = create_server(Settings(mode="bridge", allow_commands=True))
        state = {"busy": False, "database_ready": False, "recovery_required": True,
                 "native_database_fault": {"has_fault": True, "code": 538}}
        operation = {"ok": True, "result": {"success": True, "changed": True}}
        with patch.object(BridgeClient, "set_attribute", return_value=operation), \
                patch.object(BridgeClient, "capture_view", side_effect=BridgeError(
                    "Native database fault is latched", diagnostics={"state": state})):
            response = server._dispatch({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "e3d_set_attribute", "arguments": {
                    "refno": "=1/2", "attribute": "DESC", "value": "x",
                    "dry_run": False, "capture_after": True}},
            })["result"]
        self.assertFalse(response.get("isError", False))
        receipt = response["structuredContent"]["receipt"]
        self.assertTrue(response["structuredContent"]["result"]["success"])
        self.assertEqual(receipt["capture"]["state"], state)
        self.assertFalse(receipt["operation_replayed"])

    def test_returned_capture_failure_keeps_fault_diagnostics_and_confirmed_write(self):
        server = create_server(Settings(mode="bridge", allow_commands=True))
        state = {"busy": False, "database_ready": False, "recovery_required": True,
                 "native_database_fault": {"has_fault": True, "code": 538}}
        operation = {"ok": True, "result": {"success": True, "changed": True}}
        with patch.object(BridgeClient, "set_attribute", return_value=operation) as write, \
                patch.object(BridgeClient, "capture_view", return_value={
                    "ok": True, "state": state,
                    "result": {"success": False, "error": "Native database fault is latched"},
                }) as capture:
            response = server._dispatch({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "e3d_set_attribute", "arguments": {
                    "refno": "=1/2", "attribute": "DESC", "value": "x",
                    "dry_run": False, "capture_after": True}},
            })["result"]
        write.assert_called_once()
        capture.assert_called_once()
        self.assertFalse(response.get("isError", False))
        self.assertEqual(response["structuredContent"]["result"], operation["result"])
        receipt = response["structuredContent"]["receipt"]
        self.assertEqual(receipt["capture"]["state"], state)
        self.assertEqual(receipt["capture"]["error"], "Native database fault is latched")
        self.assertFalse(receipt["operation_replayed"])

    def test_recovery_errors_preserve_new_fault_diagnostics(self):
        busy = {"busy": True, "database_ready": True}
        fault = {"busy": True, "database_ready": False, "recovery_required": True,
                 "native_database_fault": {"has_fault": True, "code": 538}}
        for stage in ("initial_state", "cancel", "final_state"):
            with self.subTest(stage=stage):
                diagnostic = BridgeError("fault", http_status=500, diagnostics={"state": fault})
                observations = ([diagnostic] if stage == "initial_state" else
                                [{"ok": True, "state": busy},
                                 diagnostic if stage == "final_state" else BridgeError("connection closed", transport_error=True)])
                cancel_failure = diagnostic if stage == "cancel" else BridgeError("cancel failed")
                with patch.object(self.client, "command_state", side_effect=observations), \
                        patch.object(self.client, "cancel_command", side_effect=cancel_failure) as cancel:
                    response = self.client._recover_command_timeout(
                        BridgeError("request timed out", transport_error=True), None)
                self.assertEqual(response["final_state"], fault)
                self.assertEqual(response["state"], fault)
                self.assertTrue(response["outcome_uncertain"])
                self.assertFalse(response["retry_safe"])
                self.assertFalse(response["ok"])
                if stage == "initial_state":
                    cancel.assert_not_called()
                else:
                    cancel.assert_called_once()


if __name__ == "__main__":
    unittest.main()

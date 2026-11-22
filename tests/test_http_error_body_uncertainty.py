"""Classify incomplete HTTP error receipts without replay or command recovery."""
from __future__ import annotations

import http.client
import io
import json
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class _BrokenBody:
    def __init__(self, error):
        self.error = error

    def read(self, *args):
        raise self.error

    def close(self):
        pass


def failure(body):
    stream = _BrokenBody(body) if isinstance(body, BaseException) else io.BytesIO(body)
    return HTTPError("http://bridge/api/v1/test", 500, "native failure", {}, stream)


def broken_bodies():
    bodies = [
        OSError("socket read failed"), TimeoutError("timed out"),
        http.client.IncompleteRead(b'{"ok":false', 20),
        b'{"ok":false,"error":"\xff"}', b'{"ok":', b"", b"null", b"[]",
        b'"not an envelope"', b"false", b"12",
        b'{"ok":false,"unused":NaN}', b'{"ok":false,"unused":Infinity}',
        b'{"ok":false,"unused":-Infinity}', b'{"ok":false,"unused":1e999}',
    ]
    # The C scanner's nesting threshold differs from Python's frame limit
    # across releases. Only include a real parser failure, not valid deep JSON.
    deep = b'{"ok":false,"unused":' + b'[' * 10000 + b'0' + b']' * 10000 + b'}'
    try:
        json.loads(deep)
    except RecursionError:
        bodies.append(deep)
    integer_limit = getattr(sys, "get_int_max_str_digits", lambda: 0)()
    if integer_limit:
        bodies.append(b'{"ok":false,"unused":' + b'9' * (integer_limit + 1) + b'}')
    return bodies


class HttpErrorBodyUncertaintyTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(mode="bridge", version="E3D21", allow_commands=True)
        self.client = BridgeClient(self.settings)

    def test_body_io_failures_keep_status_and_transport_classification(self):
        for body in broken_bodies()[:3]:
            with self.subTest(body=type(body).__name__):
                with patch("urllib.request.urlopen", side_effect=failure(body)) as http:
                    with self.assertRaises(BridgeError) as caught:
                        self.client._request("POST", "/api/v1/element/attribute", {"dry_run": False})
                http.assert_called_once()
                error = caught.exception
                self.assertEqual(error.http_status, 500)
                self.assertTrue(error.transport_error)
                self.assertFalse(error.response_invalid)
                # Message-based command timeout recovery must not be activated
                # merely because the HTTP error body's socket timed out.
                self.assertNotIn("timed out", str(error))

    def test_invalid_body_requires_strict_utf8_json_object(self):
        for body in broken_bodies()[3:]:
            with self.subTest(body=body):
                with patch("urllib.request.urlopen", side_effect=failure(body)) as http:
                    with self.assertRaises(BridgeError) as caught:
                        self.client._request("POST", "/api/v1/element/attribute", {"dry_run": False})
                http.assert_called_once()
                self.assertEqual(caught.exception.http_status, 500)
                self.assertTrue(caught.exception.response_invalid)
                self.assertFalse(caught.exception.transport_error)

    def call_failure(self, tool, arguments, body):
        server = create_server(self.settings)
        with ExitStack() as stack:
            http = stack.enter_context(patch("urllib.request.urlopen", side_effect=failure(body)))
            quiet = [stack.enter_context(patch.object(BridgeClient, method)) for method in
                     ("_recover_command_timeout", "cancel_command", "capture_view", "command_state", "health")]
            response = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": tool, "arguments": arguments}})["result"]
        http.assert_called_once()
        for method in quiet:
            method.assert_not_called()
        self.assertTrue(response["isError"])
        return response

    def test_actual_attribute_write_stays_uncertain_with_or_without_capture(self):
        for body in broken_bodies():
            for capture in (False, True):
                with self.subTest(body=type(body).__name__, capture=capture):
                    response = self.call_failure("e3d_set_attribute", {
                        "refno": "/PUMP", "attribute": "DESC", "value": "modified",
                        "dry_run": False, "capture_after": capture}, body)
                    operation = response["structuredContent"]
                    self.assertTrue(operation["outcome_uncertain"])
                    self.assertFalse(operation["retry_safe"])
                    if capture:
                        self.assertEqual(operation["receipt"]["capture"]["reason"], "operation_outcome_unknown")

    def test_preview_failure_does_not_invent_mutation_uncertainty(self):
        for body in broken_bodies():
            for capture in (False, True):
                with self.subTest(body=type(body).__name__, capture=capture):
                    response = self.call_failure("e3d_set_attribute", {
                        "refno": "/PUMP", "attribute": "DESC", "value": "preview",
                        "dry_run": True, "capture_after": capture}, body)
                    operation = response.get("structuredContent", {})
                    self.assertIsNot(operation.get("outcome_uncertain"), True)
                    if capture:
                        self.assertEqual(operation["receipt"]["capture"]["reason"], "operation_failed")

    def test_script_error_keeps_original_lookup_without_recovery_or_replay(self):
        for body in broken_bodies():
            for capture in (False, True):
                with self.subTest(body=type(body).__name__, capture=capture):
                    response = self.call_failure("e3d_execute_pml_script", {
                        "source": "Q NAME", "dry_run": False,
                        "expected_session_id": "session-one", "request_id": "12345678-1234-1234-abcd-1234567890ab",
                        "capture_after": capture}, body)
                    operation = response["structuredContent"]
                    self.assertTrue(operation["outcome_uncertain"])
                    self.assertFalse(operation["retry_safe"])
                    self.assertEqual(operation["pml_script_request"], {
                        "request_id": "12345678-1234-1234-abcd-1234567890ab", "expected_session_id": "session-one",
                        "status_tool": "e3d_pml_script_status"})
                    if capture:
                        self.assertEqual(operation["receipt"]["capture"]["reason"], "operation_outcome_unknown")

    def test_command_and_batch_error_bodies_do_not_trigger_timeout_cancellation(self):
        for tool, arguments in (
            ("e3d_execute_pml", {"command": "Q NAME", "expected_session_id": "session-one"}),
            ("e3d_execute_pml_batch", {"commands": ["Q NAME"]}),
        ):
            for body in (TimeoutError("timed out"), b'{"ok":'):
                with self.subTest(tool=tool, body=type(body).__name__):
                    response = self.call_failure(tool, {**arguments, "dry_run": False, "capture_after": True}, body)
                    operation = response["structuredContent"]
                    self.assertTrue(operation["outcome_uncertain"])
                    self.assertFalse(operation["retry_safe"])
                    self.assertEqual(operation["receipt"]["capture"]["reason"], "operation_outcome_unknown")

    def test_valid_native_rejection_receipt_keeps_its_existing_classification(self):
        native = {"success": False, "write_attempted": False, "outcome_uncertain": False,
                  "retry_safe": True, "error": "Native preflight rejected the attribute"}
        body = json.dumps({"ok": False, "error": "Rejected", "result": native}).encode()
        with patch("urllib.request.urlopen", side_effect=failure(body)):
            with self.assertRaises(BridgeError) as caught:
                self.client._request("POST", "/api/v1/element/attribute", {"dry_run": False})
        self.assertFalse(caught.exception.transport_error)
        self.assertFalse(caught.exception.response_invalid)
        self.assertEqual(caught.exception.diagnostics["result"], native)

    def test_invalid_normal_success_body_also_retains_write_uncertainty(self):
        for body in broken_bodies()[3:]:
            with self.subTest(body=body[:60]):
                server = create_server(self.settings)
                with patch("urllib.request.urlopen", return_value=io.BytesIO(body)) as http, \
                        patch.object(BridgeClient, "capture_view") as capture:
                    response = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "e3d_set_attribute", "arguments": {
                            "refno": "/PUMP", "attribute": "DESC", "value": "modified",
                            "dry_run": False, "capture_after": True}}})["result"]
                http.assert_called_once()
                capture.assert_not_called()
                self.assertTrue(response["isError"])
                self.assertTrue(response["structuredContent"]["outcome_uncertain"])
                self.assertFalse(response["structuredContent"]["retry_safe"])
                self.assertEqual(response["structuredContent"]["receipt"]["capture"]["reason"],
                                 "operation_outcome_unknown")


if __name__ == "__main__":
    unittest.main()

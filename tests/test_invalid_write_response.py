import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class InvalidWriteResponseTests(unittest.TestCase):
    def test_http_error_with_uncertain_native_write_receipt_keeps_retry_and_capture_guards(self):
        body = {
            "ok": False,
            "error": "Create failed during post-create verification",
            "result": {
                "success": False,
                "creation_attempted": True,
                "creation_result_unknown": True,
                "outcome_uncertain": True,
                "retry_safe": False,
                "residual_refno": "=1/900",
                "error": "The created element could not be safely identified.",
            },
        }
        for capture_after in (False, True):
            with self.subTest(capture_after=capture_after):
                server = create_server(Settings(mode="bridge", allow_commands=True))
                error = urllib.error.HTTPError("http://127.0.0.1/api/v1/element/create", 500,
                    "Internal Server Error", {}, io.BytesIO(json.dumps(body).encode("utf-8")))
                with patch("urllib.request.urlopen", side_effect=error) as requests, \
                        patch.object(BridgeClient, "capture_view") as capture:
                    response = server._dispatch({
                        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "e3d_create_element", "arguments": {
                            "parent_refno": "/PARENT", "element_type": "BOX", "name": "/PARTIAL",
                            "dry_run": False, "expected_revision": "a" * 64,
                            "capture_after": capture_after}},
                    })["result"]
                requests.assert_called_once()
                capture.assert_not_called()
                self.assertTrue(response["isError"])
                operation = response["structuredContent"]
                self.assertFalse(operation["ok"])
                self.assertTrue(operation["outcome_uncertain"])
                self.assertFalse(operation["retry_safe"])
                self.assertEqual(operation["result"]["residual_refno"], "=1/900")
                self.assertIn("Do not automatically resend", operation["error"])
                if capture_after:
                    self.assertEqual(operation["receipt"]["capture"]["reason"], "operation_outcome_unknown")

    def test_http_error_with_uncertain_pml_script_receipt_preserves_lookup_path(self):
        request_id = "9f5c1d20-441d-4e12-9ad2-3a6160f7e271"
        body = {
            "ok": False,
            "error": "PML execution ended without a complete receipt",
            "request_id": request_id,
            "session_id": "session-1",
            "execution_attempted": True,
            "outcome_uncertain": True,
            "retry_safe": False,
        }
        for capture_after in (False, True):
            with self.subTest(capture_after=capture_after):
                server = create_server(Settings(mode="bridge", allow_commands=True))
                error = urllib.error.HTTPError("http://127.0.0.1/api/v1/pml-script", 500,
                    "Internal Server Error", {}, io.BytesIO(json.dumps(body).encode("utf-8")))
                with patch("urllib.request.urlopen", side_effect=error) as requests, \
                        patch.object(BridgeClient, "capture_view") as capture:
                    response = server._dispatch({
                        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "e3d_execute_pml_script", "arguments": {
                            "source": "!!CODEX_TEST = 1", "dry_run": False,
                            "request_id": request_id, "expected_session_id": "session-1",
                            "capture_after": capture_after}},
                    })["result"]
                requests.assert_called_once()
                capture.assert_not_called()
                self.assertTrue(response["isError"])
                operation = response["structuredContent"]
                self.assertTrue(operation["outcome_uncertain"])
                self.assertFalse(operation["retry_safe"])
                self.assertTrue(operation["execution_attempted"])
                self.assertEqual(operation["session_id"], "session-1")
                self.assertEqual(operation["pml_script_request"], {
                    "request_id": request_id,
                    "expected_session_id": "session-1",
                    "status_tool": "e3d_pml_script_status",
                })
                self.assertIn("e3d_pml_script_status", operation["error"])
                self.assertNotIn("Read back the target state", operation["error"])
                if capture_after:
                    self.assertEqual(operation["receipt"]["capture"]["reason"], "operation_outcome_unknown")

    def test_http_native_no_retry_marker_is_preserved_without_inventing_uncertainty(self):
        body = {"ok": False, "error": "Setter response unavailable", "result": {
            "success": False, "write_attempted": True, "write_applied": None,
            "retry_safe": False,
        }}
        server = create_server(Settings(mode="bridge", allow_commands=True))
        error = urllib.error.HTTPError("http://127.0.0.1/api/v1/element/attribute", 500,
            "Internal Server Error", {}, io.BytesIO(json.dumps(body).encode("utf-8")))
        with patch("urllib.request.urlopen", side_effect=error), \
                patch.object(BridgeClient, "capture_view") as capture:
            response = server._dispatch({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "e3d_set_attribute", "arguments": {
                    "refno": "=1/2", "attribute": "DESC", "value": "x",
                    "dry_run": False, "capture_after": True}},
            })["result"]
        capture.assert_not_called()
        self.assertTrue(response["isError"])
        operation = response["structuredContent"]
        self.assertFalse(operation["retry_safe"])
        self.assertNotIn("outcome_uncertain", operation)
        self.assertEqual(operation["receipt"]["capture"]["reason"], "operation_failed")

    def test_http_delete_preflight_rejection_does_not_become_uncertain(self):
        body = {"ok": False, "error": "Element was not found", "result": {
            "success": False, "delete_attempted": False,
            "verification_status": "not_attempted", "outcome_uncertain": False,
            "retry_safe": True,
        }}
        server = create_server(Settings(mode="bridge", allow_commands=True))
        error = urllib.error.HTTPError("http://127.0.0.1/api/v1/element/delete", 404,
            "Not Found", {}, io.BytesIO(json.dumps(body).encode("utf-8")))
        with patch("urllib.request.urlopen", side_effect=error), \
                patch.object(BridgeClient, "capture_view") as capture:
            response = server._dispatch({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "e3d_delete_element", "arguments": {
                    "refno": "=1/999", "dry_run": False, "capture_after": True}},
            })["result"]
        capture.assert_not_called()
        self.assertTrue(response["isError"])
        operation = response["structuredContent"]
        self.assertFalse(operation.get("outcome_uncertain", False))
        self.assertTrue(operation["retry_safe"])
        self.assertEqual(operation["result"]["verification_status"], "not_attempted")
        self.assertEqual(operation["receipt"]["capture"]["reason"], "operation_failed")

    def test_unreadable_success_response_leaves_mutation_outcome_unknown(self):
        for response_bytes in (b"not json", b"\xff", b"[]", b"null"):
            for capture_after in (False, True):
                with self.subTest(response=response_bytes, capture_after=capture_after):
                    server = create_server(Settings(mode="bridge", allow_commands=True))
                    with patch("urllib.request.urlopen", return_value=io.BytesIO(response_bytes)) as requests, \
                            patch.object(BridgeClient, "capture_view") as capture:
                        response = server._dispatch({
                            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "e3d_set_attribute", "arguments": {
                                "refno": "=1/2", "attribute": "DESC", "value": "x",
                                "dry_run": False, "capture_after": capture_after}},
                        })["result"]
                    requests.assert_called_once()
                    capture.assert_not_called()
                    self.assertTrue(response["isError"])
                    operation = response["structuredContent"]
                    self.assertFalse(operation["ok"])
                    self.assertTrue(operation["outcome_uncertain"])
                    self.assertFalse(operation["retry_safe"])
                    if capture_after:
                        self.assertEqual(operation["receipt"]["capture"]["reason"], "operation_outcome_unknown")

    def test_invalid_response_is_distinct_from_transport_and_http_failure(self):
        client = BridgeClient(Settings(mode="bridge"))
        with patch("urllib.request.urlopen", return_value=io.BytesIO(b"bad json")):
            with self.assertRaises(BridgeError) as caught:
                client.set_attribute("=1/2", "DESC", "x", False)
        self.assertTrue(caught.exception.response_invalid)
        self.assertFalse(caught.exception.transport_error)
        self.assertIsNone(caught.exception.http_status)

    def test_invalid_preview_response_does_not_claim_possible_mutation(self):
        server = create_server(Settings(mode="bridge", allow_commands=True))
        with patch("urllib.request.urlopen", return_value=io.BytesIO(b"bad json")) as requests:
            response = server._dispatch({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "e3d_set_attribute", "arguments": {
                    "refno": "=1/2", "attribute": "DESC", "value": "x", "dry_run": True}},
            })["result"]
        requests.assert_called_once()
        self.assertTrue(response["isError"])
        self.assertNotIn("outcome_uncertain", response.get("structuredContent", {}))


if __name__ == "__main__":
    unittest.main()

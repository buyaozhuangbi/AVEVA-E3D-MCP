"""Offline CLI receipts for typed MCP failures; no subprocess or native calls."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import live_mcp


class LiveMcpCliTests(unittest.TestCase):
    def run_cli(self, *, result=None, failure=None, output=True):
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        if failure is None:
            client.call.return_value = result
        else:
            client.call.side_effect = failure
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "receipt.json"
            path.write_text('{"prior_operation":"success"}', encoding="utf-8")
            argv = ["e3d_project_info"] + (["--output", str(path)] if output else [])
            with patch.object(live_mcp, "LiveMcp", return_value=client), \
                    patch("sys.stderr", new_callable=io.StringIO) as stderr, \
                    patch("sys.stdout", new_callable=io.StringIO) as stdout:
                code = live_mcp.main(argv)
            saved = json.loads(path.read_text(encoding="utf-8")) if output else json.loads(stdout.getvalue())
        client.call.assert_called_once_with("e3d_project_info", {}, image_path=None)
        client.rpc.assert_not_called()
        client.wait_response.assert_not_called()
        self.assertEqual(client.method_calls, [unittest.mock.call.call("e3d_project_info", {}, image_path=None)])
        return code, saved, stderr.getvalue()

    def test_json_rpc_error_replaces_stale_success_with_exact_full_envelope(self):
        response = {"jsonrpc": "2.0", "id": 2, "error": {"code": -32602,
            "message": "Unknown tool: e3d_project_info_typo",
            "data": {"large_receipt": "private-server-data" * 4000}}}
        failure = live_mcp.LiveMcpProtocolError("MCP JSON-RPC error for request 2 (tools/call)",
            request_id=2, method="tools/call", response=response, awaiting_request_id=2)
        code, saved, stderr = self.run_cli(failure=failure)
        self.assertEqual(code, 1)
        self.assertEqual(saved, response)
        self.assertIn("Unknown tool: e3d_project_info_typo", stderr)
        self.assertNotIn("private-server-data", stderr)
        self.assertIs(failure.response, response)
        self.assertEqual(failure.request_id, 2)

    def test_malformed_frame_preserves_exact_raw_evidence_and_original_request_ids(self):
        raw = '{"jsonrpc":"2.0", malformed frame}\n'
        failure = live_mcp.LiveMcpProtocolError("MCP response is not valid JSON",
            request_id=7, method="tools/call", raw_line=raw, awaiting_request_id=7)
        code, saved, stderr = self.run_cli(failure=failure)
        self.assertEqual(code, 1)
        self.assertEqual(saved["raw_line"], raw)
        self.assertIsNone(saved["response"])
        self.assertEqual(saved["request_id"], 7)
        self.assertEqual(saved["awaiting_request_id"], 7)
        self.assertTrue(saved["outcome_uncertain"])
        self.assertFalse(saved["retry_safe"])
        self.assertIn("not valid JSON", stderr)

    def test_timeout_persists_original_identity_and_unknown_outcome_without_waiting(self):
        failure = live_mcp.LiveMcpTimeoutError(11, "tools/call")
        code, saved, stderr = self.run_cli(failure=failure)
        self.assertEqual(code, 1)
        self.assertEqual(saved["error_type"], "LiveMcpTimeoutError")
        self.assertEqual(saved["request_id"], 11)
        self.assertEqual(saved["method"], "tools/call")
        self.assertTrue(saved["outcome_uncertain"])
        self.assertFalse(saved["retry_safe"])
        self.assertIn("request 11", stderr)
        self.assertIn("Do not replay writes", stderr)

    def test_transport_failure_saves_reader_evidence_and_original_identity(self):
        failure = live_mcp.LiveMcpTransportError("MCP transport failed: invalid UTF-8",
            request_id=19, method="tools/call", reader_error=UnicodeError("invalid UTF-8"))
        code, saved, stderr = self.run_cli(failure=failure)
        self.assertEqual(code, 1)
        self.assertEqual(saved["request_id"], 19)
        self.assertEqual(saved["reader_error"], {"type": "UnicodeError", "message": "invalid UTF-8"})
        self.assertTrue(saved["outcome_uncertain"])
        self.assertFalse(saved["retry_safe"])
        self.assertIn("invalid UTF-8", stderr)

    def test_transport_failure_saves_bounded_stderr_and_read_error(self):
        tail = "earlier diagnostic\n" + "x" * 65510 + "\nlatest diagnostic\n"
        failure = live_mcp.LiveMcpTransportError("MCP process closed without a response",
            request_id=20, method="tools/call", stderr_tail=tail,
            stderr_error=UnicodeError("stderr decoder failed"), stderr_complete=True,
            stderr_truncated=True, stderr_dropped_characters=3)
        code, saved, stderr = self.run_cli(failure=failure)
        self.assertEqual(code, 1)
        self.assertEqual(saved["stderr_tail"], tail)
        self.assertEqual(saved["stderr_error"], {"type": "UnicodeError", "message": "stderr decoder failed"})
        self.assertTrue(saved["stderr_complete"])
        self.assertTrue(saved["stderr_truncated"])
        self.assertEqual(saved["stderr_dropped_characters"], 3)
        self.assertIn(": stderr: latest diagnostic", stderr)
        self.assertLess(len(stderr), 600)

    def test_stderr_terminal_summary_strips_control_characters_but_receipt_stays_exact(self):
        tail = "\x1b[31msecret-like diagnostic\x1b[0m\nlatest\x1b[2J diagnostic\n"
        failure = live_mcp.LiveMcpTransportError("MCP process closed without a response",
            request_id=21, method="tools/call", stderr_tail=tail, stderr_complete=True)
        code, saved, stderr = self.run_cli(failure=failure)
        self.assertEqual(code, 1)
        self.assertEqual(saved["stderr_tail"], tail)
        self.assertNotIn("\x1b", stderr)
        self.assertIn("latest[2J diagnostic", stderr)

    def test_protocol_and_timeout_receipts_do_not_gain_stderr_fields(self):
        protocol = live_mcp.LiveMcpProtocolError("MCP response is not valid JSON", request_id=3,
            method="tools/call", raw_line="not-json", awaiting_request_id=3)
        _, saved_protocol, _ = self.run_cli(failure=protocol)
        self.assertNotIn("stderr_tail", saved_protocol)
        self.assertNotIn("stderr_error", saved_protocol)
        _, saved_timeout, _ = self.run_cli(failure=live_mcp.LiveMcpTimeoutError(4, "tools/call"))
        self.assertNotIn("stderr_tail", saved_timeout)
        self.assertNotIn("stderr_error", saved_timeout)

    def test_error_stdout_and_invalid_unicode_are_lossless(self):
        response = {"jsonrpc": "2.0", "id": 2,
                    "error": {"code": -32602, "message": "Unpaired Unicode: \ud800"}}
        failure = live_mcp.LiveMcpProtocolError("MCP JSON-RPC error", request_id=2,
            method="tools/call", response=response, awaiting_request_id=2)
        code, saved, stderr = self.run_cli(failure=failure, output=False)
        self.assertEqual(code, 1)
        self.assertEqual(saved, response)
        self.assertIn("\\ud800", stderr)

    def test_successful_json_shape_is_unchanged(self):
        result = {"ok": True, "project": {"name": "APS", "mdb": "ALL", "user": "用户"}}
        code, saved, stderr = self.run_cli(result=result)
        self.assertEqual(code, 0)
        self.assertEqual(saved, result)
        self.assertEqual(stderr, "")


if __name__ == "__main__":
    unittest.main()

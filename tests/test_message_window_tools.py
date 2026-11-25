"""Production HTTP/tool contracts for native managed message cursors."""
from __future__ import annotations

import copy
import io
import json
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.message_window import message_window_parameters, validate_message_window
from aveva_e3d_mcp.tools import create_server


def window(*, after=0, latest=3, first=1, limit=100, checkpoint=False):
    if checkpoint:
        after = latest
    start = max(after, first - 1) if first is not None else after
    available = 0 if checkpoint or first is None else latest - start
    returned = min(limit, available)
    remaining = available - returned
    overwritten = 0 if checkpoint or first is None else max(0, first - after - 1)
    rows = [{"sequence": str(n), "type": "Error", "description": "captured error " + str(n),
             "details": "native output", "category": "test", "date": "2026-11-19"}
            for n in range(start + 1, start + returned + 1)]
    return {"ok": True, "version": "E3D21", "session_id": "session-one", "bridge_build": "build-one", "result": {
        "schema_version": 1, "success": True, "available": True,
        "status": "checkpoint" if checkpoint else "overwritten" if overwritten else "more" if remaining else "complete",
        "requested_limit": limit, "checkpoint": checkpoint, "messages": rows,
        "first_retained_sequence": str(first) if first is not None else None,
        "after_sequence": str(after), "latest_sequence": str(latest),
        "next_sequence": str(start + returned if returned else after), "returned_count": returned,
        "overwritten_count": str(overwritten), "remaining_count": remaining, "has_more": remaining > 0,
        "complete_since_after_sequence": overwritten == 0 and remaining == 0,
        "next_cursor": "opaque-native-cursor", "operation_correlated": False, "managed_only": True}}


class MessageWindowToolTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(mode="bridge", version="E3D21", allow_commands=False)
        self.server = create_server(self.settings)

    def call(self, arguments=None, *, response=None, error=None, server=None):
        with ExitStack() as stack:
            quiet = [stack.enter_context(patch.object(BridgeClient, name)) for name in
                     ("health", "project", "command_state", "command", "cancel_command", "capture_view")]
            native = stack.enter_context(patch("urllib.request.urlopen",
                side_effect=error, return_value=io.BytesIO(json.dumps(response or window()).encode("utf-8"))))
            result = (server or self.server)._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "e3d_read_message_window", "arguments": arguments or {}}})["result"]
        for quiet_call in quiet:
            quiet_call.assert_not_called()
        return result, native

    def test_discovery_adds_one_read_only_tool_and_preserves_legacy_tail_schema(self):
        catalog = self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        self.assertEqual(len(catalog), 126)
        self.assertEqual(len({tool["name"] for tool in catalog}), 126)
        tool = next(item for item in catalog if item["name"] == "e3d_read_message_window")
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["destructiveHint"])
        self.assertFalse(tool["inputSchema"]["additionalProperties"])
        self.assertEqual(set(tool["inputSchema"]["properties"]), {"cursor", "checkpoint", "limit"})
        self.assertNotIn("capture_after", tool["inputSchema"]["properties"])
        old = next(item for item in catalog if item["name"] == "e3d_read_messages")
        self.assertEqual(set(old["inputSchema"]["properties"]), {"tail"})
        for phrase in ("message_cursor_v1", "causation", "overwritten_count", "unchanged"):
            self.assertIn(phrase, tool["description"])

    def test_one_get_to_new_route_without_health_or_any_recovery_and_preserve_output(self):
        payload = window()
        original = copy.deepcopy(payload)
        result, native = self.call(response=payload)
        self.assertFalse(result.get("isError", False), result)
        self.assertEqual(result["structuredContent"], original)
        self.assertEqual(json.loads(result["content"][0]["text"]), original)
        native.assert_called_once()
        request = native.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(urlsplit(request.full_url).path, "/api/v1/messages/window")
        self.assertEqual(parse_qs(urlsplit(request.full_url).query), {"limit": ["100"], "checkpoint": ["false"]})

    def test_checkpoint_and_empty_delta_are_not_invented_no_error_verdicts(self):
        for latest, first in ((0, None), (300, 1), (900, 401)):
            with self.subTest(latest=latest):
                payload = window(latest=latest, first=first, checkpoint=True)
                result, native = self.call({"checkpoint": True}, response=payload)
                self.assertFalse(result.get("isError", False), result)
                self.assertEqual(result["structuredContent"]["result"]["messages"], [])
                self.assertFalse(result["structuredContent"]["result"]["operation_correlated"])
                native.assert_called_once()
        payload = window(after=3)
        result, _ = self.call({"cursor": "pass-unchanged"}, response=payload)
        self.assertFalse(result.get("isError", False))
        self.assertEqual(result["structuredContent"]["result"]["after_sequence"], "3")

    def test_pagination_and_overwrites_stay_distinct_in_full_native_receipt(self):
        for after, first, latest, limit in ((0, 1, 5, 2), (0, 101, 600, 2), (105, 101, 600, 500)):
            with self.subTest(after=after, first=first):
                arguments = {"limit": limit, **({"cursor": "signed-cursor"} if after else {})}
                payload = window(after=after, first=first, latest=latest, limit=limit)
                result, native = self.call(arguments, response=payload)
                self.assertFalse(result.get("isError", False), result)
                self.assertEqual(result["structuredContent"], payload)
                native.assert_called_once()

    def test_cursor_is_not_parsed_or_normalized_and_large_positions_remain_strings(self):
        cursor = "opaque+/= token"
        after = 9007199254741007
        payload = window(after=after, first=after - 2, latest=after + 3, limit=2)
        result, native = self.call({"cursor": cursor, "limit": 2}, response=payload)
        self.assertFalse(result.get("isError", False), result)
        self.assertEqual(parse_qs(urlsplit(native.call_args.args[0].full_url).query)["cursor"], [cursor])
        self.assertEqual(result["structuredContent"]["result"]["messages"][0]["sequence"], str(after + 1))
        self.assertIsInstance(result["structuredContent"]["result"]["next_sequence"], str)

    def test_invalid_arguments_rejected_before_io_and_unknown_fields_not_echoed(self):
        invalid = [{"cursor": None}, {"cursor": 1}, {"cursor": "x" * 1025},
                   {"checkpoint": None}, {"checkpoint": 1}, {"checkpoint": "true"},
                   {"checkpoint": True, "cursor": "existing"},
                   {"limit": True}, {"limit": 0}, {"limit": 501}, {"limit": 1.0},
                   {"tail": 1}, {"secret-field-value": "not echoed"}, {"capture_after": True}, {"version": "E3D31"}]
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                result, native = self.call(arguments)
                self.assertTrue(result["isError"])
                native.assert_not_called()
                self.assertNotIn("secret-field-value", str(result))

    def test_legacy_tail_missing_native_identity_and_invalid_sequences_fail_closed(self):
        invalid = [{"ok": True, "version": "E3D21", "messages": []}]
        for field in ("version", "session_id", "bridge_build", "result"):
            payload = window()
            del payload[field]
            invalid.append(payload)
        for value in (1, True, "01", "-1", "1.0", "9223372036854775808"):
            payload = window()
            payload["result"]["latest_sequence"] = value
            invalid.append(payload)
        for payload in invalid:
            with self.subTest(payload=payload):
                result, native = self.call(response=payload)
                self.assertTrue(result["isError"])
                self.assertEqual(result["structuredContent"]["error_code"], "message_window_response_invalid")
                self.assertEqual(result["structuredContent"]["evidence"], payload)
                native.assert_called_once()

    def test_inconsistent_window_claims_cannot_become_valid_checkpoints_or_cursors(self):
        mutations = [
            ("schema_version", True), ("requested_limit", 99), ("managed_only", False),
            ("operation_correlated", True), ("checkpoint", True), ("returned_count", 2),
            ("overwritten_count", "1"), ("remaining_count", 1), ("has_more", True),
            ("complete_since_after_sequence", False), ("next_sequence", "2"),
            ("first_retained_sequence", None), ("status", "more"), ("next_cursor", ""),
        ]
        for field, value in mutations:
            with self.subTest(field=field):
                payload = window()
                payload["result"][field] = value
                result, _ = self.call(response=payload)
                self.assertTrue(result["isError"], result)
        for index in (0, 2):
            payload = window()
            payload["result"]["messages"][index]["sequence"] = "2"
            result, _ = self.call(response=payload)
            self.assertTrue(result["isError"])

    def test_native_managed_errors_keep_codes_and_session_without_fallback(self):
        for status, code in ((400, "message_cursor_invalid"), (409, "message_cursor_session_changed"),
                             (409, "message_cursor_ahead"), (500, "message_cursor_read_failed"),
                             (501, "message_cursor_unavailable"), (404, None)):
            with self.subTest(status=status, code=code):
                body = {"ok": False, "version": "E3D21", "session_id": "current-session", "bridge_build": "build",
                        "error": "native cursor rejected", "managed_only": True, "operation_correlated": False}
                if code is not None:
                    body["error_code"] = code
                error = HTTPError("http://bridge/api/v1/messages/window", status, "failed", {},
                                  io.BytesIO(json.dumps(body).encode()))
                result, native = self.call({"cursor": "original"}, error=error)
                self.assertTrue(result["isError"])
                evidence = result["structuredContent"]
                self.assertEqual(evidence["error_code"], code or "message_cursor_unavailable")
                self.assertEqual(evidence["http_status"], status)
                self.assertEqual(evidence["session_id"], "current-session")
                self.assertNotIn("result", evidence)
                self.assertNotIn("next_cursor", evidence)
                self.assertIsNot(evidence.get("outcome_uncertain"), True)
                native.assert_called_once()

    def test_mock_or_other_version_never_invents_native_cursor_or_contacts_e3d(self):
        for settings in (Settings(mode="mock", version="E3D21"), Settings(mode="bridge", version="E3D31")):
            with self.subTest(settings=settings):
                result, native = self.call(server=create_server(settings))
                self.assertTrue(result["isError"])
                self.assertFalse(result["structuredContent"]["result"]["available"])
                self.assertNotIn("next_cursor", result["structuredContent"]["result"])
                native.assert_not_called()

    def test_legacy_tail_route_and_request_are_unchanged(self):
        client = BridgeClient(self.settings)
        with patch.object(client, "_request", return_value={"ok": True, "messages": []}) as native:
            self.assertEqual(client.messages(20), {"ok": True, "messages": []})
        native.assert_called_once_with("GET", "/api/v1/messages", query={"tail": "20"}, version=None)

    def test_int64_upper_boundary_and_empty_history_validate_without_float_conversion(self):
        for payload, cursor in ((window(latest=0, first=None), ""),
                                (window(after=9223372036854775806, first=9223372036854775807,
                                        latest=9223372036854775807), "cursor")):
            validate_message_window(payload, version="E3D21", cursor=cursor, limit=100, checkpoint=False)
        self.assertEqual(message_window_parameters("", 100, True), {"limit": "100", "checkpoint": "true"})


if __name__ == "__main__":
    unittest.main()

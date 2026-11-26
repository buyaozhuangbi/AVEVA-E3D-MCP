"""Exercise the dedicated display-removal MCP boundary without loading E3D."""
from catalog_compatibility import CURRENT_MOCK_TOOL_COUNT
from contextlib import ExitStack
from io import BytesIO, StringIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server
from test_capture_receipts import _capture_payload


class DrawlistRemovalToolTests(unittest.TestCase):
    def setUp(self):
        self.server = create_server(Settings(mode="bridge", allow_commands=True))
        self.revision = "ab" * 32
        self.base = {"refno": "/PUMP"}
        self.execute = {**self.base, "expected_revision": self.revision, "dry_run": False}

    def rpc(self, method, params, server=None):
        output = StringIO()
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        with patch("sys.stdout", output):
            (server or self.server).serve([request + "\n"])
        result = json.loads(output.getvalue())
        self.assertNotIn("error", result, result)
        return result["result"]

    def call(self, arguments, server=None):
        return self.rpc("tools/call", {"name": "e3d_remove_drawlist_member", "arguments": arguments}, server)

    @staticmethod
    def native(**fields):
        return {"ok": True, "version": "E3D21", "result": {"success": True, **fields}}

    def test_discovery_schema_permission_annotations_and_capture(self):
        tools = {tool["name"]: tool for tool in self.rpc("tools/list", {})["tools"]}
        self.assertEqual(len(tools), CURRENT_MOCK_TOOL_COUNT)
        self.assertEqual(sum("capture_after" in tool["inputSchema"]["properties"] for tool in tools.values()), 19)
        tool = tools["e3d_remove_drawlist_member"]
        self.assertEqual(tool["annotations"], {"readOnlyHint": False, "destructiveHint": False,
                                               "idempotentHint": False, "openWorldHint": False})
        schema = tool["inputSchema"]
        self.assertEqual(schema["required"], ["refno"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["properties"]), {"refno", "expected_revision", "dry_run", "capture_after", "capture_width", "capture_height"})
        self.assertTrue(schema["properties"]["dry_run"]["default"])
        self.assertEqual(schema["properties"]["expected_revision"]["pattern"], "^[0-9a-fA-F]{64}$")
        self.assertEqual(schema["allOf"][0]["then"], {"required": ["expected_revision"]})
        for phrase in ("revision", "preview", "database", "member"):
            self.assertIn(phrase, tool["description"].lower())

    def test_preview_default_is_allowed_without_commands_and_keeps_exact_payload(self):
        server = create_server(Settings(mode="bridge", allow_commands=False))
        payload = self.native(dry_run=True, revision=self.revision, remove_attempted=False,
                              exact_member=True, expected_removed_primitive_entries=[{"owner_refno": "=1/1", "refno": "=1/2"}])
        with patch.object(BridgeClient, "remove_drawlist_member", return_value=payload) as remove:
            response = self.call(self.base, server)
        remove.assert_called_once_with("/PUMP", dry_run=True)
        self.assertEqual(response["structuredContent"], payload)
        self.assertEqual(json.loads(response["content"][0]["text"]), payload)

    def test_explicit_ref_and_uppercase_revision_normalize_and_execute_once(self):
        payload = self.native(dry_run=False, removed=True, membership_verified=True, database_delete_attempted=False)
        with ExitStack() as stack:
            remove = stack.enter_context(patch.object(BridgeClient, "remove_drawlist_member", return_value=payload))
            fallback = [stack.enter_context(patch.object(BridgeClient, name)) for name in
                        ("command", "delete_element", "set_drawlist_visual", "restore_view", "capture_view")]
            response = self.call({"refno": " =1/2 ", "expected_revision": self.revision.upper(), "dry_run": False})
        remove.assert_called_once_with("=1/2", dry_run=False, expected_revision=self.revision)
        for method in fallback:
            method.assert_not_called()
        self.assertEqual(response["structuredContent"], payload)

    def test_invalid_or_missing_explicit_refs_do_not_reach_native(self):
        cases = [None, True, False, 1, 1.2, [], {}, "", " ", "CE", "OWNER", "WORLD", "/", "=x/2", "=1/2/3",
                 "/P\nDELETE CE", "/P\rREM ALL", "/P\tX", "/P X", "/P\x00", "/P\x7f", "/P\u0085X"]
        with patch.object(BridgeClient, "remove_drawlist_member") as remove, patch.object(BridgeClient, "capture_view") as capture:
            self.assertTrue(self.call({})["isError"])
            for refno in cases:
                with self.subTest(refno=repr(refno)):
                    self.assertTrue(self.call({"refno": refno, "capture_after": True})["isError"])
        remove.assert_not_called()
        capture.assert_not_called()

    def test_unknown_controls_never_select_an_ancestor_or_expand_removal(self):
        with patch.object(BridgeClient, "remove_drawlist_member") as remove:
            for field, value in (("all", True), ("owner_refno", "/SITE"), ("refnos", ["/A", "/B"]),
                                 ("remove_only", True), ("delete", True), ("version", "E3D31"),
                                 ("add", True), ("colour", "RED"), ("visibility", False), ("revision", self.revision)):
                with self.subTest(field=field):
                    self.assertTrue(self.call({**self.base, field: value})["isError"])
        remove.assert_not_called()

    def test_boolean_and_revision_types_are_strict_before_any_dispatch(self):
        invalid_revision = (None, True, False, 1, [], {}, "", "a" * 63, "a" * 65, "z" * 64,
                            " " + self.revision, self.revision + "\n")
        with patch.object(BridgeClient, "remove_drawlist_member") as remove:
            for dry in (True, False):
                for revision in invalid_revision:
                    with self.subTest(dry=dry, revision=revision):
                        self.assertTrue(self.call({**self.base, "dry_run": dry, "expected_revision": revision})["isError"])
            for value in (None, 0, 1, "false", "true", [], {}):
                with self.subTest(dry_run=value):
                    self.assertTrue(self.call({**self.execute, "dry_run": value})["isError"])
            self.assertTrue(self.call({**self.base, "dry_run": False})["isError"])
        remove.assert_not_called()

    def test_optional_valid_revision_is_forwarded_for_preview(self):
        with patch.object(BridgeClient, "remove_drawlist_member", return_value=self.native(dry_run=True)) as remove:
            self.call({**self.base, "expected_revision": self.revision.upper()})
        remove.assert_called_once_with("/PUMP", dry_run=True, expected_revision=self.revision)

    def test_execute_requires_command_permission(self):
        server = create_server(Settings(mode="bridge", allow_commands=False))
        with patch.object(BridgeClient, "remove_drawlist_member") as remove, patch.object(BridgeClient, "capture_view") as capture:
            response = self.call({**self.execute, "capture_after": True}, server)
        self.assertTrue(response["isError"])
        self.assertIn("E3D_MCP_ALLOW_COMMANDS", response["structuredContent"]["error"])
        remove.assert_not_called()
        capture.assert_not_called()

    def test_invalid_receipt_controls_block_even_disabled_capture(self):
        cases = ({"capture_after": None}, {"capture_after": 1}, {"capture_width": True}, {"capture_width": 63},
                 {"capture_height": 2049}, {"capture_height": 64.0}, {"capture_height": "64"})
        with patch.object(BridgeClient, "remove_drawlist_member") as remove, patch.object(BridgeClient, "capture_view") as capture:
            for options in cases:
                with self.subTest(options=options):
                    self.assertTrue(self.call({**self.execute, "capture_after": False, **options})["isError"])
        remove.assert_not_called()
        capture.assert_not_called()

    def test_preview_never_captures_and_noop_native_fields_are_not_invented(self):
        payload = self.native(dry_run=True, revision=self.revision, exact_member=False, no_change=True,
                              removed=False, remove_attempted=False, pixel_visibility_verified=False)
        with patch.object(BridgeClient, "remove_drawlist_member", return_value=payload) as remove, patch.object(BridgeClient, "capture_view") as capture:
            response = self.call({**self.base, "capture_after": True})
        remove.assert_called_once()
        capture.assert_not_called()
        self.assertEqual(response["structuredContent"]["result"], payload["result"])
        self.assertEqual(response["structuredContent"]["receipt"]["capture"]["reason"], "dry_run")

    def test_native_scope_revision_partial_and_uncertain_rejections_remain_failures(self):
        cases = (dict(error="not exact TOPF", remove_attempted=False, scope_mismatch=True),
                 dict(error="overlapping membership", remove_attempted=False, overlap_refs=["=1/4"]),
                 dict(error="revision changed", remove_attempted=False, revision_conflict=True),
                 dict(error="remove mutated then threw", remove_attempted=True, removed=True, outcome_uncertain=False,
                      residual_complete=True, actual_members=[]),
                 dict(error="readback unavailable", remove_attempted=True, removed=None, outcome_uncertain=True,
                      residual_complete=False, actual_members=None))
        for fields in cases:
            payload = self.native(success=False, dry_run=False, **fields)
            with self.subTest(fields=fields), ExitStack() as stack:
                remove = stack.enter_context(patch.object(BridgeClient, "remove_drawlist_member", return_value=payload))
                others = [stack.enter_context(patch.object(BridgeClient, name)) for name in ("capture_view", "command", "restore_view", "delete_element")]
                response = self.call({**self.execute, "capture_after": True})
            self.assertTrue(response["isError"])
            self.assertEqual(response["structuredContent"]["result"], payload["result"])
            remove.assert_called_once()
            for method in others:
                method.assert_not_called()

    def test_success_and_persistence_failure_capture_after_removal_once(self):
        for persistence_ok in (True, False):
            events = []
            payload = self.native(dry_run=False, removed=True, remaining_members_verified=True,
                                  persistence={"succeeded": persistence_ok, "error": None if persistence_ok else "disk full"})
            def remove(*args, **kwargs):
                events.append("remove")
                return payload
            def capture(width, height):
                events.append("capture")
                return _capture_payload(width, height)
            with self.subTest(persistence_ok=persistence_ok), patch.object(BridgeClient, "remove_drawlist_member", side_effect=remove) as write, \
                    patch.object(BridgeClient, "capture_view", side_effect=capture) as image:
                response = self.call({**self.execute, "capture_after": True, "capture_width": 64, "capture_height": 96})
            self.assertEqual(events, ["remove", "capture"])
            write.assert_called_once()
            image.assert_called_once_with(64, 96)
            self.assertFalse(response.get("isError", False))
            self.assertEqual(response["structuredContent"]["result"], payload["result"])
            self.assertEqual([row["type"] for row in response["content"]], ["text", "image"])
            self.assertFalse(response["structuredContent"]["receipt"]["operation_replayed"])

    def test_capture_failure_does_not_repeat_or_recast_successful_removal(self):
        payload = self.native(dry_run=False, removed=True, remaining_members_verified=True)
        with patch.object(BridgeClient, "remove_drawlist_member", return_value=payload) as remove, \
                patch.object(BridgeClient, "capture_view", side_effect=BridgeError("GetBitmap failed")) as capture:
            response = self.call({**self.execute, "capture_after": True})
        remove.assert_called_once()
        capture.assert_called_once()
        self.assertFalse(response.get("isError", False))
        self.assertEqual(response["structuredContent"]["result"], payload["result"])
        self.assertEqual(response["structuredContent"]["receipt"]["capture"]["status"], "failed")

    def test_unknown_execution_outcome_is_never_replayed_or_captured(self):
        for error in (BridgeError("timeout", transport_error=True), BridgeError("invalid body", response_invalid=True)):
            for receipt in (False, True):
                with self.subTest(error=error, receipt=receipt), patch.object(BridgeClient, "remove_drawlist_member", side_effect=error) as remove, \
                        patch.object(BridgeClient, "capture_view") as capture:
                    response = self.call({**self.execute, "capture_after": receipt})
                self.assertTrue(response["isError"])
                self.assertTrue(response["structuredContent"]["outcome_uncertain"])
                self.assertFalse(response["structuredContent"]["retry_safe"])
                self.assertEqual("receipt" in response["structuredContent"], receipt)
                remove.assert_called_once()
                capture.assert_not_called()

    def test_preview_transport_error_does_not_claim_write_uncertainty(self):
        with patch.object(BridgeClient, "remove_drawlist_member", side_effect=BridgeError("timeout", transport_error=True)) as remove:
            response = self.call({**self.base, "capture_after": True})
        self.assertTrue(response["isError"])
        self.assertNotIn("outcome_uncertain", response["structuredContent"])
        self.assertNotIn("retry_safe", response["structuredContent"])
        remove.assert_called_once()

    def test_mock_cannot_fabricate_removal_readiness_revision_or_picture(self):
        server = create_server(Settings(mode="mock", allow_commands=True))
        with patch.object(BridgeClient, "_request") as request, patch.object(BridgeClient, "capture_view") as capture:
            results = [self.call(self.base, server), self.call({**self.execute, "capture_after": True}, server)]
        request.assert_not_called()
        capture.assert_not_called()
        for response in results:
            self.assertTrue(response["isError"])
            result = response["structuredContent"]["result"]
            for field in ("success", "available", "complete", "native_validated", "write_attempted", "write_applied", "pixel_visibility_verified"):
                self.assertFalse(result[field], field)
            for field in ("revision", "ce_unchanged", "membership_verified", "removed_members"):
                self.assertNotIn(field, result)

    def test_bridge_exact_post_route_selected_version_and_omitted_revision(self):
        client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        payload = self.native(success=False, complete=None, revision=None, residual_members=None)
        with patch.object(client, "_request", return_value=payload) as request:
            self.assertEqual(client.remove_drawlist_member("/PUMP+01", version="E3D21"), payload)
            request.assert_called_once_with("POST", "/api/v1/drawlist/remove", {"refno": "/PUMP+01", "dry_run": True}, version="E3D21")
            request.reset_mock()
            self.assertEqual(client.remove_drawlist_member("=1/2", expected_revision=self.revision, dry_run=False, version="E3D21"), payload)
            request.assert_called_once_with("POST", "/api/v1/drawlist/remove",
                {"refno": "=1/2", "dry_run": False, "expected_revision": self.revision}, version="E3D21")

    def test_http_payload_is_json_and_response_is_preserved_without_extra_calls(self):
        client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        payload = self.native(success=False, residual_complete=False, errors=["partial"], actual_members=None)
        with patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
            self.assertEqual(client.remove_drawlist_member("/PUMP+01", expected_revision=self.revision, dry_run=False), payload)
        urlopen.assert_called_once()
        request = urlopen.call_args.args[0]
        parsed = urllib.parse.urlsplit(request.full_url)
        self.assertEqual((request.get_method(), parsed.path, parsed.query), ("POST", "/api/v1/drawlist/remove", ""))
        self.assertEqual(json.loads(request.data), {"refno": "/PUMP+01", "dry_run": False, "expected_revision": self.revision})

    def test_http_error_timeout_and_invalid_json_never_recover_or_repeat(self):
        client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        failures = (TimeoutError("timeout"), urllib.error.HTTPError("http://bridge/api/v1/drawlist/remove", 409, "Conflict", {}, BytesIO(b'{"error":"revision conflict"}')))
        for failure in failures:
            with self.subTest(failure=failure), patch("urllib.request.urlopen", side_effect=failure) as request, \
                    patch.object(client, "_recover_command_timeout") as recover, patch.object(client, "cancel_command") as cancel:
                with self.assertRaises(BridgeError):
                    client.remove_drawlist_member("/PUMP", expected_revision=self.revision, dry_run=False)
            request.assert_called_once()
            recover.assert_not_called()
            cancel.assert_not_called()
        with patch("urllib.request.urlopen") as request, patch.object(client, "_recover_command_timeout") as recover:
            request.return_value.__enter__.return_value.read.return_value = b'not json'
            with self.assertRaises(BridgeError) as caught:
                client.remove_drawlist_member("/PUMP", expected_revision=self.revision, dry_run=False)
        self.assertTrue(caught.exception.response_invalid)
        request.assert_called_once()
        recover.assert_not_called()


if __name__ == "__main__":
    unittest.main()

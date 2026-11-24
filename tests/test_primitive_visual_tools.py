from __future__ import annotations

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


class PrimitiveVisualToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(Settings(mode="bridge", allow_commands=True))
        self.revision = "ab" * 32
        self.base = {"refno": "/PART", "colour": "RED"}
        self.execute = {**self.base, "dry_run": False, "expected_revision": self.revision}

    def _rpc(self, method: str, params: dict, *, server=None) -> dict:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        output = StringIO()
        with patch("sys.stdout", output):
            (server or self.server).serve([request + "\n"])
        response = json.loads(output.getvalue())
        self.assertNotIn("error", response, response)
        return response["result"]

    def _call(self, arguments: dict, *, server=None) -> dict:
        return self._rpc("tools/call", {"name": "e3d_set_primitive_visual", "arguments": arguments}, server=server)

    def _snapshot(self, arguments: dict | None = None, *, server=None) -> dict:
        return self._rpc("tools/call", {"name": "e3d_display_snapshot", "arguments": arguments or {}}, server=server)

    @staticmethod
    def _native(**fields) -> dict:
        return {"ok": True, "version": "E3D21", "result": {"success": True, **fields}}

    def test_catalog_has_80_tools_19_receipts_and_strict_schemas(self) -> None:
        tools = {tool["name"]: tool for tool in self._rpc("tools/list", {})["tools"]}
        self.assertEqual(len(tools), 120)
        self.assertEqual(sum("capture_after" in tool["inputSchema"]["properties"] for tool in tools.values()), 19)
        snapshot = tools["e3d_display_snapshot"]
        self.assertEqual(snapshot["inputSchema"], {"type": "object", "properties": {}, "additionalProperties": False})
        self.assertEqual(snapshot["annotations"], {
            "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False,
        })
        write = tools["e3d_set_primitive_visual"]
        self.assertFalse(write["annotations"]["readOnlyHint"])
        self.assertFalse(write["annotations"]["idempotentHint"])
        schema = write["inputSchema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["required"], ["refno"])
        self.assertEqual(set(schema["properties"]), {
            "refno", "expected_revision", "colour", "transparency", "edges", "visibility", "shallow",
            "dry_run", "capture_after", "capture_width", "capture_height",
        })
        self.assertTrue(schema["properties"]["dry_run"]["default"])
        self.assertEqual(schema["properties"]["expected_revision"]["pattern"], "^[0-9a-fA-F]{64}$")
        self.assertEqual(schema["properties"]["transparency"], {"type": "integer", "minimum": 0, "maximum": 100})
        self.assertEqual(len(schema["anyOf"]), 5)
        for field in ("edges", "visibility", "shallow"):
            self.assertEqual(schema["properties"][field]["type"], ["boolean", "null"])
        self.assertEqual(schema["allOf"][0]["then"], {"required": ["expected_revision"]})
        for contract in ("non-TOPF", "PRMF", "all owner aliases", "Never Add", "override masks", "pixel visibility"):
            self.assertIn(contract, write["description"])

    def test_snapshot_preserves_complete_partial_nullable_and_multiple_owner_maps(self) -> None:
        read_only = create_server(Settings(mode="bridge", allow_commands=False))
        for complete in (True, False, None):
            payload = self._native(success=complete, available=True, complete=complete,
                members=[{"refno": "=1/1", "colour": None}, {"refno": "=1/8", "colour": 7}],
                primitives=[{"refno": "=1/2", "owner_refno": "=1/1", "visibility": False},
                            {"refno": "=1/2", "owner_refno": "=1/8", "visibility": None}],
                errors=[] if complete else ["native property unavailable"], observation_hash=None,
                ce_unchanged=None, pixel_visibility_verified=False)
            with self.subTest(complete=complete), patch.object(BridgeClient, "display_snapshot", return_value=payload) as read, \
                    patch.object(BridgeClient, "set_primitive_visual") as write, patch.object(BridgeClient, "capture_view") as capture:
                response = self._snapshot(server=read_only)
            read.assert_called_once_with()
            write.assert_not_called()
            capture.assert_not_called()
            self.assertEqual(response["structuredContent"], payload)
            self.assertEqual(json.loads(response["content"][0]["text"]), payload)
            self.assertEqual(response.get("isError", False), complete is False)

    def test_snapshot_rejects_every_argument_and_never_writes(self) -> None:
        with patch.object(BridgeClient, "display_snapshot") as read:
            for key, value in (("refno", "/PART"), ("dry_run", True), ("capture_after", False),
                               ("capture_width", 64), ("capture_height", 64), ("scope", "all"),
                               ("version", "E3D21"), ("expected_revision", self.revision)):
                with self.subTest(key=key):
                    self.assertTrue(self._snapshot({key: value})["isError"])
        read.assert_not_called()

    def test_snapshot_transport_error_has_no_write_uncertainty_or_receipt(self) -> None:
        with patch.object(BridgeClient, "display_snapshot", side_effect=BridgeError("closed", transport_error=True)) as read:
            response = self._snapshot()
        self.assertTrue(response["isError"])
        read.assert_called_once()
        for field in ("outcome_uncertain", "retry_safe", "receipt"):
            self.assertNotIn(field, json.dumps(response))

    def test_mock_snapshot_and_writes_are_unavailable_without_fabricated_native_state(self) -> None:
        mock = create_server(Settings(mode="mock", version="E3D21", allow_commands=True))
        with patch.object(BridgeClient, "_request") as request, patch.object(BridgeClient, "capture_view") as capture:
            responses = [self._snapshot(server=mock), self._call(self.base, server=mock),
                         self._call({**self.execute, "capture_after": True}, server=mock)]
        request.assert_not_called()
        capture.assert_not_called()
        for response in responses:
            self.assertTrue(response["isError"])
            result = response["structuredContent"]["result"]
            for field in ("success", "available", "complete", "pixel_visibility_verified"):
                self.assertFalse(result[field])
            self.assertEqual(result["source"], "mock")
            for field in ("revision", "original_revision", "members", "primitives", "rollback_verified", "ce_unchanged"):
                self.assertNotIn(field, result)
        for response in responses[1:]:
            self.assertFalse(response["structuredContent"]["result"]["write_attempted"])

    def test_preview_defaults_true_needs_no_permission_and_preserves_native_revision(self) -> None:
        read_only = create_server(Settings(mode="bridge", allow_commands=False))
        payload = self._native(dry_run=True, revision=self.revision, write_attempted=False,
                               aliases=[{"owner_refno": "=1/1"}, {"owner_refno": "=1/8"}])
        with patch.object(BridgeClient, "set_primitive_visual", return_value=payload) as write:
            response = self._call(self.base, server=read_only)
        self.assertEqual(response["structuredContent"], payload)
        write.assert_called_once_with("/PART", dry_run=True, colour="RED")

    def test_exact_explicit_reference_and_same_changes_revision_are_sent_once(self) -> None:
        payload = self._native(dry_run=False, all_maps_verified=True, observed_properties_verified=True,
                               override_mask_verified=False, pixel_visibility_verified=False)
        with ExitStack() as stack:
            write = stack.enter_context(patch.object(BridgeClient, "set_primitive_visual", return_value=payload))
            others = [stack.enter_context(patch.object(BridgeClient, name)) for name in
                      ("set_drawlist_visual", "display_snapshot", "display_state", "fit_view", "command", "capture_view")]
            response = self._call({"refno": " =1/2 ", "colour": "colour_7", "transparency": 0,
                                  "edges": False, "visibility": False, "shallow": True,
                                  "expected_revision": self.revision.upper(), "dry_run": False})
        write.assert_called_once_with("=1/2", dry_run=False, colour="colour_7", transparency=0,
                                      edges=False, visibility=False, shallow=True, expected_revision=self.revision)
        for other in others:
            other.assert_not_called()
        self.assertEqual(response["structuredContent"], payload)

    def test_omitted_empty_colour_and_null_booleans_preserve_values_with_actual_change(self) -> None:
        cases = [({"edges": False}, {"edges": False}),
                 ({"colour": "", "transparency": 100}, {"colour": "", "transparency": 100}),
                 ({"colour": " \t", "edges": None, "visibility": None, "shallow": True},
                  {"colour": " \t", "shallow": True})]
        with patch.object(BridgeClient, "set_primitive_visual", return_value=self._native(dry_run=True)) as write:
            for changes, expected in cases:
                with self.subTest(changes=changes):
                    self.assertFalse(self._call({"refno": "/PART", **changes}).get("isError", False))
                    write.assert_called_with("/PART", dry_run=True, **expected)

    def test_missing_changes_and_preserve_only_inputs_do_not_call_native(self) -> None:
        cases = [{}, {"colour": ""}, {"colour": " \t\n"}, {"edges": None},
                 {"edges": None, "visibility": None, "shallow": None, "colour": " "},
                 {"expected_revision": self.revision}, {"dry_run": False, "expected_revision": self.revision}]
        with patch.object(BridgeClient, "set_primitive_visual") as write, patch.object(BridgeClient, "capture_view") as capture:
            for options in cases:
                with self.subTest(options=options):
                    self.assertTrue(self._call({"refno": "/PART", "capture_after": True, **options})["isError"])
        write.assert_not_called()
        capture.assert_not_called()

    def test_implicit_malformed_or_missing_refs_are_rejected(self) -> None:
        with patch.object(BridgeClient, "set_primitive_visual") as write:
            self.assertTrue(self._call({"colour": "RED"})["isError"])
            for refno in (None, True, 2, [], "", " ", "CE", "OWNER", "WORLD", "/", "=x/2", "=1/2/3", "/P\nDELETE CE"):
                with self.subTest(refno=refno):
                    self.assertTrue(self._call({**self.base, "refno": refno})["isError"])
        write.assert_not_called()

    def test_unknown_add_owner_version_and_other_controls_are_rejected(self) -> None:
        with patch.object(BridgeClient, "set_primitive_visual") as write:
            for field, value in (("add", True), ("create_entry", True), ("owner_refno", "/PUMP"),
                                 ("version", "E3D21"), ("scope", "all"), ("revision", self.revision),
                                 ("x_axis", [1, 0, 0]), ("ref", "/PART")):
                with self.subTest(field=field):
                    self.assertTrue(self._call({**self.base, field: value})["isError"])
        write.assert_not_called()

    def test_visual_value_types_are_strict_before_native_and_capture(self) -> None:
        invalid = [{"colour": value} for value in (None, True, 1, [], {})]
        invalid.extend({"transparency": value} for value in (None, True, False, -1, 101, 0.0, "0", [], {}, float("nan")))
        invalid.extend({field: value} for field in ("edges", "visibility", "shallow")
                       for value in (0, 1, "true", "false", [], {}))
        invalid.extend({"dry_run": value} for value in (None, 0, 1, "true", [], {}))
        with patch.object(BridgeClient, "set_primitive_visual") as write, patch.object(BridgeClient, "capture_view") as capture:
            for options in invalid:
                with self.subTest(options=options):
                    self.assertTrue(self._call({**self.base, **options, "capture_after": True})["isError"])
        write.assert_not_called()
        capture.assert_not_called()

    def test_revision_is_optional_for_preview_but_strict_when_supplied_and_required_to_execute(self) -> None:
        invalid = (None, True, 1, [], "", "a" * 63, "a" * 65, "z" * 64, " " + self.revision, self.revision + "\n")
        with patch.object(BridgeClient, "set_primitive_visual") as write:
            for dry_run in (True, False):
                for revision in invalid:
                    with self.subTest(dry_run=dry_run, revision=revision):
                        self.assertTrue(self._call({**self.base, "dry_run": dry_run, "expected_revision": revision})["isError"])
            self.assertTrue(self._call({**self.base, "dry_run": False})["isError"])
        write.assert_not_called()

    def test_execution_requires_write_permission_and_skips_capture(self) -> None:
        read_only = create_server(Settings(mode="bridge", allow_commands=False))
        with patch.object(BridgeClient, "set_primitive_visual") as write, patch.object(BridgeClient, "capture_view") as capture:
            response = self._call({**self.execute, "capture_after": True}, server=read_only)
        self.assertTrue(response["isError"])
        self.assertIn("E3D_MCP_ALLOW_COMMANDS", response["structuredContent"]["error"])
        write.assert_not_called()
        capture.assert_not_called()

    def test_capture_controls_are_validated_before_native_even_if_disabled(self) -> None:
        invalid = ({"capture_after": None}, {"capture_after": 1}, {"capture_width": True},
                   {"capture_width": 63}, {"capture_height": 2049}, {"capture_height": 64.0})
        with patch.object(BridgeClient, "set_primitive_visual") as write, patch.object(BridgeClient, "capture_view") as capture:
            for options in invalid:
                with self.subTest(options=options):
                    self.assertTrue(self._call({**self.execute, "capture_after": False, **options})["isError"])
        write.assert_not_called()
        capture.assert_not_called()

    def test_preview_with_requested_receipt_skips_capture_and_preserves_revision(self) -> None:
        with patch.object(BridgeClient, "set_primitive_visual", return_value=self._native(dry_run=True, revision=self.revision)), \
                patch.object(BridgeClient, "capture_view") as capture:
            response = self._call({**self.base, "capture_after": True})
        capture.assert_not_called()
        self.assertEqual(response["structuredContent"]["result"]["revision"], self.revision)
        self.assertEqual(response["structuredContent"]["receipt"]["capture"]["reason"], "dry_run")

    def test_native_rejection_rollback_and_uncertain_readback_preserved_without_capture_or_fallback(self) -> None:
        cases = [dict(write_attempted=False, error="No existing entry", existing_entry=False),
                 dict(write_attempted=False, error="Stale revision", revision_conflict=True),
                 dict(write_attempted=True, rollback_attempted=True, rollback_verified=True, residual=None),
                 dict(write_attempted=True, rollback_attempted=True, rollback_verified=None,
                      residual={"members": None, "errors": ["readback failed"]})]
        for fields in cases:
            payload = self._native(success=False, dry_run=False, **fields)
            with self.subTest(fields=fields), ExitStack() as stack:
                write = stack.enter_context(patch.object(BridgeClient, "set_primitive_visual", return_value=payload))
                others = [stack.enter_context(patch.object(BridgeClient, name)) for name in
                          ("set_drawlist_visual", "fit_view", "command", "capture_view")]
                response = self._call({**self.execute, "capture_after": True})
            self.assertTrue(response["isError"])
            self.assertEqual(response["structuredContent"]["result"], payload["result"])
            self.assertEqual(response["structuredContent"]["receipt"]["capture"]["reason"], "operation_failed")
            write.assert_called_once()
            for other in others:
                other.assert_not_called()

    def test_success_captures_once_after_write_and_preserves_alias_diagnostics(self) -> None:
        events = []
        payload = self._native(dry_run=False, aliases=[{"owner_refno": "=1/1"}, {"owner_refno": "=1/8"}],
                               all_maps_verified=True, pixel_visibility_verified=False)

        def write(*args, **kwargs):
            events.append("write")
            return payload

        def capture(width, height):
            events.append("capture")
            return _capture_payload(width, height)

        with patch.object(BridgeClient, "set_primitive_visual", side_effect=write) as update, \
                patch.object(BridgeClient, "capture_view", side_effect=capture) as screenshot:
            response = self._call({**self.execute, "capture_after": True, "capture_width": 64, "capture_height": 96})
        self.assertEqual(events, ["write", "capture"])
        update.assert_called_once_with("/PART", dry_run=False, colour="RED", expected_revision=self.revision)
        screenshot.assert_called_once_with(64, 96)
        self.assertFalse(response.get("isError", False))
        self.assertEqual(response["structuredContent"]["result"], payload["result"])
        self.assertEqual([item["type"] for item in response["content"]], ["text", "image"])
        self.assertFalse(response["structuredContent"]["receipt"]["operation_replayed"])

    def test_capture_failure_preserves_success_without_replaying_write(self) -> None:
        payload = self._native(dry_run=False, all_maps_verified=True)
        with patch.object(BridgeClient, "set_primitive_visual", return_value=payload) as write, \
                patch.object(BridgeClient, "capture_view", side_effect=BridgeError("capture unavailable")) as capture:
            response = self._call({**self.execute, "capture_after": True})
        write.assert_called_once()
        capture.assert_called_once()
        self.assertFalse(response.get("isError", False))
        self.assertEqual(response["structuredContent"]["result"], payload["result"])
        self.assertEqual(response["structuredContent"]["receipt"]["capture"]["status"], "failed")

    def test_execution_transport_failure_is_uncertain_with_or_without_receipt_and_never_replayed(self) -> None:
        for capture_after in (False, True):
            with self.subTest(capture_after=capture_after), \
                    patch.object(BridgeClient, "set_primitive_visual", side_effect=BridgeError("closed", transport_error=True)) as write, \
                    patch.object(BridgeClient, "capture_view") as capture:
                response = self._call({**self.execute, "capture_after": capture_after})
            self.assertTrue(response["isError"])
            result = response["structuredContent"]
            self.assertTrue(result["outcome_uncertain"])
            self.assertFalse(result["retry_safe"])
            self.assertEqual("receipt" in result, capture_after)
            if capture_after:
                self.assertEqual(result["receipt"]["capture"]["reason"], "operation_outcome_unknown")
            write.assert_called_once()
            capture.assert_not_called()

    def test_preview_transport_failure_does_not_claim_an_uncertain_write(self) -> None:
        with patch.object(BridgeClient, "set_primitive_visual", side_effect=BridgeError("closed", transport_error=True)):
            response = self._call({**self.base, "capture_after": True})
        self.assertTrue(response["isError"])
        self.assertNotIn("outcome_uncertain", response["structuredContent"])
        self.assertNotIn("retry_safe", response["structuredContent"])

    def test_bridge_routes_omit_unprovided_values_and_preserve_selected_instance(self) -> None:
        client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        payload = self._native(complete=None, errors=["partial"], members=None)
        with patch.object(client, "_request", return_value=payload) as request:
            self.assertEqual(client.display_snapshot(version="E3D21"), payload)
            request.assert_called_once_with("GET", "/api/v1/view/display-snapshot", version="E3D21")
            request.reset_mock()
            self.assertEqual(client.set_primitive_visual("=1/2", colour="", visibility=False, edges=None,
                                                        expected_revision=self.revision, dry_run=False, version="E3D21"), payload)
            request.assert_called_once_with("POST", "/api/v1/view/primitive-visual",
                {"refno": "=1/2", "dry_run": False, "colour": "", "visibility": False, "expected_revision": self.revision},
                version="E3D21")

    def test_http_get_and_post_use_exact_routes_and_preserve_native_payloads(self) -> None:
        client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        payload = self._native(success=False, complete=False, errors=["partial"], members=None, revision=None)
        for operation in ("snapshot", "write"):
            with self.subTest(operation=operation), patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
                actual = client.display_snapshot() if operation == "snapshot" else client.set_primitive_visual(
                    "/PART+01", transparency=0, shallow=False, dry_run=True)
            self.assertEqual(actual, payload)
            urlopen.assert_called_once()
            request = urlopen.call_args.args[0]
            parsed = urllib.parse.urlsplit(request.full_url)
            self.assertEqual(parsed.query, "")
            if operation == "snapshot":
                self.assertEqual(request.get_method(), "GET")
                self.assertEqual(parsed.path, "/api/v1/view/display-snapshot")
                self.assertIsNone(request.data)
            else:
                self.assertEqual(request.get_method(), "POST")
                self.assertEqual(parsed.path, "/api/v1/view/primitive-visual")
                self.assertEqual(json.loads(request.data), {"refno": "/PART+01", "dry_run": True, "transparency": 0, "shallow": False})

    def test_http_failures_never_retry_cancel_or_recover(self) -> None:
        for operation in ("snapshot", "write"):
            for kind in ("timeout", "http"):
                failure = TimeoutError("timed out") if kind == "timeout" else urllib.error.HTTPError(
                    "http://bridge/api/v1/view/primitive-visual", 409, "Conflict", {}, BytesIO(b'{"error":"revision conflict"}'))
                client = BridgeClient(Settings(mode="bridge", version="E3D21"))
                with self.subTest(operation=operation, kind=kind), \
                        patch("urllib.request.urlopen", side_effect=failure) as request, \
                        patch.object(client, "_recover_command_timeout") as recover, patch.object(client, "cancel_command") as cancel:
                    with self.assertRaises(BridgeError) as caught:
                        if operation == "snapshot":
                            client.display_snapshot()
                        else:
                            client.set_primitive_visual("/PART", colour="RED", expected_revision=self.revision, dry_run=False)
                request.assert_called_once()
                recover.assert_not_called()
                cancel.assert_not_called()
                self.assertEqual(caught.exception.transport_error, kind == "timeout")
                if kind == "http":
                    self.assertEqual(caught.exception.http_status, 409)
                    self.assertIn("revision conflict", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import base64
import json
import struct
import sys
import unittest
import zlib
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


def _capture_payload(width: int, height: int) -> dict:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = (b"\x00" + bytes(width * 3)) * height
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
           + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    return {"ok": True, "result": {
        "success": True, "mime_type": "image/png", "image_base64": base64.b64encode(png).decode("ascii"),
        "width": width, "height": height, "source": "E3D View3D.GetBitmap", "drawlist_id": 17,
    }}


class PanelUpdateToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(Settings(mode="bridge", allow_commands=True))
        self.base = {"refno": "/PANEL", "vertices": [[0, 0], [120, 0], [0, 80]]}
        self.revision = "a1" * 32

    def _rpc(self, method: str, params: dict, *, server=None) -> dict:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        output = StringIO()
        with patch("sys.stdout", output):
            (server or self.server).serve([request + "\n"])
        response = json.loads(output.getvalue())
        self.assertNotIn("error", response, response)
        return response["result"]

    def _call(self, arguments: dict, *, server=None) -> dict:
        return self._rpc("tools/call", {"name": "e3d_update_panel", "arguments": arguments}, server=server)

    @staticmethod
    def _success(**fields) -> dict:
        return {"ok": True, "version": "E3D21", "result": {"success": True, "dry_run": False, **fields}}

    def test_update_schema_exposes_guarded_preview_omission_and_receipt_contracts(self) -> None:
        tools = {tool["name"]: tool for tool in self._rpc("tools/list", {})["tools"]}
        self.assertEqual(len(tools), 117)
        tool = tools["e3d_update_panel"]
        self.assertFalse(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["idempotentHint"])
        self.assertTrue(tool["annotations"]["destructiveHint"])
        schema = tool["inputSchema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), {"refno", "vertices"})
        self.assertEqual(schema["properties"]["vertices"]["minItems"], 3)
        self.assertEqual(schema["properties"]["vertices"]["maxItems"], 256)
        self.assertEqual(schema["properties"]["vertices"]["items"]["maxItems"], 2)
        self.assertTrue(schema["properties"]["dry_run"]["default"])
        self.assertFalse(schema["properties"]["allow_vertex_deletion"]["default"])
        self.assertFalse(schema["properties"]["capture_after"]["default"])
        for field in ("thickness", "justification", "expected_revision"):
            self.assertNotIn("default", schema["properties"][field])
        self.assertEqual(schema["properties"]["expected_revision"]["pattern"], "^[0-9a-fA-F]{64}$")
        self.assertNotIn("position", schema["properties"])
        self.assertNotIn("name", schema["properties"])

    def test_preview_can_omit_revision_and_keeps_optional_fields_absent(self) -> None:
        read_only = create_server(Settings(mode="bridge", allow_commands=False))
        preview = self._success(dry_run=True, expected_revision=self.revision, removed_vertex_refs=[])
        with patch.object(BridgeClient, "update_panel", return_value=preview) as update:
            response = self._call(self.base, server=read_only)
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"], preview)
        update.assert_called_once_with("/PANEL", [[0.0, 0.0], [120.0, 0.0], [0.0, 80.0]],
                                       allow_vertex_deletion=False, dry_run=True)

    def test_execution_normalizes_explicit_options_in_one_native_call(self) -> None:
        args = {**self.base, "refno": " =123/456 ", "thickness": 5, "justification": " CENTRE ",
                "expected_revision": self.revision.upper(), "allow_vertex_deletion": True, "dry_run": False}
        with patch.object(BridgeClient, "update_panel", return_value=self._success()) as update, \
                patch.object(BridgeClient, "create_panel") as create, patch.object(BridgeClient, "command") as command:
            response = self._call(args)
        self.assertFalse(response.get("isError", False), response)
        update.assert_called_once_with("=123/456", self.base["vertices"], thickness=5.0, justification="centre",
                                       expected_revision=self.revision, allow_vertex_deletion=True, dry_run=False)
        create.assert_not_called()
        command.assert_not_called()

    def test_every_option_combination_preserves_omitted_fields(self) -> None:
        options = [{}, {"thickness": 7}, {"justification": "negative"},
                   {"thickness": 7, "justification": "positive"}, {"expected_revision": self.revision}]
        with patch.object(BridgeClient, "update_panel", return_value=self._success(dry_run=True)) as update:
            for provided in options:
                with self.subTest(provided=provided):
                    response = self._call({**self.base, **provided})
                    self.assertFalse(response.get("isError", False), response)
                    self.assertEqual(update.call_args.kwargs, {"allow_vertex_deletion": False, "dry_run": True, **provided})

    def test_execution_requires_revision_and_command_permission(self) -> None:
        read_only = create_server(Settings(mode="bridge", allow_commands=False))
        with patch.object(BridgeClient, "update_panel") as update:
            missing_revision = self._call({**self.base, "dry_run": False})
            denied = self._call({**self.base, "expected_revision": self.revision, "dry_run": False}, server=read_only)
        self.assertTrue(missing_revision["isError"])
        self.assertIn("expected_revision", missing_revision["content"][0]["text"])
        self.assertTrue(denied["isError"])
        update.assert_not_called()

    def test_malformed_revision_is_rejected_for_both_preview_and_execution(self) -> None:
        invalid = (None, True, 1, [], "", "a" * 63, "a" * 65, "g" * 64, " " + self.revision,
                   self.revision + "\n", "=1/2")
        with patch.object(BridgeClient, "update_panel") as update:
            for dry_run in (True, False):
                for revision in invalid:
                    with self.subTest(dry_run=dry_run, revision=revision):
                        self.assertTrue(self._call({**self.base, "dry_run": dry_run, "expected_revision": revision})["isError"])
        update.assert_not_called()

    def test_required_inputs_and_nonfinite_or_wrong_shape_vertices_do_not_reach_bridge(self) -> None:
        invalid = [{}, {"refno": "/PANEL"}, {"vertices": self.base["vertices"]}]
        invalid.extend({**self.base, "refno": value} for value in (None, True, 1, "", "CE", "OWNER", "/", "=x/2", "/PANEL\nDELETE CE"))
        vertex_sets = [None, "triangle", [], [[0, 0], [1, 1]], [[0, 0]] * 257,
                       [[0, 0, 0], [1, 0], [0, 1]], [[0], [1, 0], [0, 1]],
                       [[0, 0], None, [0, 1]], [[0, 0], ["1", 0], [0, 1]],
                       [[0, 0], [True, 0], [0, 1]], [[0, 0], [None, 0], [0, 1]]]
        vertex_sets.extend([[0, 0], [value, 0], [0, 1]] for value in (
            float("nan"), float("inf"), float("-inf"), 10 ** 1000,
        ))
        invalid.extend({**self.base, "vertices": vertices} for vertices in vertex_sets)
        with patch.object(BridgeClient, "update_panel") as update:
            for args in invalid:
                with self.subTest(args=args):
                    self.assertTrue(self._call(args)["isError"])
        update.assert_not_called()

    def test_null_and_invalid_optional_values_are_rejected_rather_than_preserved(self) -> None:
        invalid = [{**self.base, "thickness": value} for value in (
            None, "4", True, False, [], 0, -1, float("nan"), float("inf"), float("-inf"), 10 ** 1000,
        )]
        invalid.extend({**self.base, "justification": value} for value in (None, True, 1, "", "BOTT", "center"))
        for field in ("allow_vertex_deletion", "dry_run"):
            invalid.extend({**self.base, field: value} for value in (None, "false", 0, 1))
        with patch.object(BridgeClient, "update_panel") as update:
            for args in invalid:
                with self.subTest(args=args):
                    self.assertTrue(self._call(args)["isError"])
        update.assert_not_called()

    def test_transform_name_and_unknown_controls_are_rejected_before_update_or_capture(self) -> None:
        with patch.object(BridgeClient, "update_panel") as update, patch.object(BridgeClient, "capture_view") as capture:
            for field in ("position", "orientation", "wrt", "name", "parent_refno", "attributes", "holes", "command", "fit", "add"):
                response = self._call({**self.base, field: True, "expected_revision": self.revision,
                                       "dry_run": False, "capture_after": True})
                self.assertTrue(response["isError"])
                self.assertEqual(response["structuredContent"]["receipt"]["capture"]["reason"], "operation_failed")
        update.assert_not_called()
        capture.assert_not_called()

    def test_shrink_preview_preserves_exact_removed_references_without_capture(self) -> None:
        preview = self._success(dry_run=True, expected_revision=self.revision,
            retained_vertex_refs=["=1/4", "=1/5", "=1/6"], removed_vertex_refs=["=1/7", "=1/8"],
            reversible=False, requires_vertex_deletion=True)
        with patch.object(BridgeClient, "update_panel", return_value=preview) as update, \
                patch.object(BridgeClient, "capture_view") as capture:
            response = self._call({**self.base, "allow_vertex_deletion": True, "capture_after": True})
        update.assert_called_once()
        capture.assert_not_called()
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"]["result"], preview["result"])
        self.assertEqual(response["structuredContent"]["receipt"]["capture"]["reason"], "dry_run")

    def test_stale_revision_and_partial_deletion_failures_keep_diagnostics_without_replay_or_image(self) -> None:
        cases = [
            {"success": False, "revision_match": False, "write_attempted": False, "actual_revision": "b" * 64},
            {"success": False, "partial": True, "write_attempted": True, "write_applied": True,
             "rollback": {"success": False, "unrestored_deleted_refs": ["=1/8"]},
             "actual_residual": {"vertex_refs": ["=1/4", "=1/5", "=1/6", "=1/7"]}, "retry_safe": False},
        ]
        for fields in cases:
            with self.subTest(fields=fields), patch.object(BridgeClient, "update_panel", return_value=self._success(**fields)) as update, \
                    patch.object(BridgeClient, "capture_view") as capture:
                response = self._call({**self.base, "expected_revision": self.revision,
                                       "allow_vertex_deletion": True, "dry_run": False, "capture_after": True})
            update.assert_called_once()
            capture.assert_not_called()
            self.assertTrue(response["isError"])
            self.assertEqual(response["structuredContent"]["result"], {"dry_run": False, **fields})
            self.assertEqual(response["structuredContent"]["receipt"]["capture"]["reason"], "operation_failed")
            self.assertFalse(any(part["type"] == "image" for part in response["content"]))

    def test_success_keeps_verified_panel_and_vertex_references_then_captures_once(self) -> None:
        events = []
        operation = self._success(refno="=1/2", loop_refno="=1/3", vertex_refs=["=1/4", "=1/5", "=1/6"],
                                  readback={"complete": True, "topology_valid": True}, revision="b" * 64)

        def update(*_, **__):
            events.append("update")
            return operation

        def capture(width, height):
            events.append("capture")
            return _capture_payload(width, height)

        with patch.object(BridgeClient, "update_panel", side_effect=update) as write, \
                patch.object(BridgeClient, "capture_view", side_effect=capture) as image, \
                patch.object(BridgeClient, "set_drawlist_visual") as membership, \
                patch.object(BridgeClient, "fit_view") as fit, patch.object(BridgeClient, "restore_view") as restore:
            response = self._call({**self.base, "expected_revision": self.revision, "dry_run": False,
                                  "capture_after": True, "capture_width": 64, "capture_height": 64})
        self.assertEqual(events, ["update", "capture"])
        write.assert_called_once()
        image.assert_called_once_with(64, 64)
        membership.assert_not_called()
        fit.assert_not_called()
        restore.assert_not_called()
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"]["result"], operation["result"])
        self.assertEqual(response["structuredContent"]["receipt"]["capture"]["status"], "captured")
        self.assertFalse(response["structuredContent"]["receipt"]["operation_replayed"])

    def test_transport_uncertainty_is_explicit_with_and_without_capture(self) -> None:
        for capture_after in (False, True):
            with self.subTest(capture_after=capture_after), \
                    patch.object(BridgeClient, "update_panel", side_effect=BridgeError("connection closed", transport_error=True)) as update, \
                    patch.object(BridgeClient, "capture_view") as capture:
                response = self._call({**self.base, "expected_revision": self.revision, "dry_run": False,
                                       "capture_after": capture_after})
            update.assert_called_once()
            capture.assert_not_called()
            self.assertTrue(response["isError"])
            self.assertTrue(response["structuredContent"]["outcome_uncertain"])
            self.assertFalse(response["structuredContent"]["retry_safe"])
            if capture_after:
                self.assertEqual(response["structuredContent"]["receipt"]["capture"]["reason"], "operation_outcome_unknown")
            else:
                self.assertNotIn("receipt", response["structuredContent"])

    def test_capture_failure_preserves_confirmed_update_and_never_retries(self) -> None:
        with patch.object(BridgeClient, "update_panel", return_value=self._success(refno="=1/2")) as update, \
                patch.object(BridgeClient, "capture_view", side_effect=BridgeError("capture unavailable")) as capture:
            response = self._call({**self.base, "expected_revision": self.revision, "dry_run": False, "capture_after": True})
        update.assert_called_once()
        capture.assert_called_once()
        self.assertFalse(response.get("isError", False), response)
        self.assertTrue(response["structuredContent"]["result"]["success"])
        self.assertEqual(response["structuredContent"]["receipt"]["capture"]["status"], "failed")

    def test_preview_transport_failure_never_claims_an_uncertain_write(self) -> None:
        for capture_after in (False, True):
            with self.subTest(capture_after=capture_after), \
                    patch.object(BridgeClient, "update_panel", side_effect=BridgeError("connection closed", transport_error=True)) as update, \
                    patch.object(BridgeClient, "capture_view") as capture:
                response = self._call({**self.base, "capture_after": capture_after})
            update.assert_called_once()
            capture.assert_not_called()
            self.assertTrue(response["isError"])
            self.assertNotIn("outcome_uncertain", json.dumps(response))
            self.assertNotIn("retry_safe", json.dumps(response))

    def test_native_inspection_revision_is_preserved_as_data(self) -> None:
        payload = {"ok": True, "result": {"success": True, "complete": True, "revision": self.revision}}
        with patch.object(BridgeClient, "panel_geometry", return_value=payload) as read:
            response = self._rpc("tools/call", {"name": "e3d_panel_geometry", "arguments": {"refno": "/PANEL"}})
        read.assert_called_once_with("/PANEL")
        self.assertEqual(response["structuredContent"], payload)

    def test_mock_preview_and_execution_never_fabricate_native_revision_geometry_or_image(self) -> None:
        mock = create_server(Settings(mode="mock", allow_commands=True, version="E3D21"))
        preview = self._call({**self.base, "capture_after": True}, server=mock)
        self.assertFalse(preview.get("isError", False), preview)
        self.assertFalse(preview["structuredContent"]["result"]["native_validated"])
        self.assertEqual(preview["structuredContent"]["receipt"]["capture"]["reason"], "dry_run")
        executed = self._call({**self.base, "expected_revision": self.revision, "dry_run": False, "capture_after": True}, server=mock)
        self.assertTrue(executed["isError"])
        for response in (preview, executed):
            result = response["structuredContent"]["result"]
            self.assertTrue(result["mock"])
            self.assertFalse(result["write_attempted"])
            self.assertFalse(result["write_applied"])
            for field in ("revision", "readback", "loops", "vertex_refs", "actual_residual"):
                self.assertNotIn(field, result)
            self.assertFalse(any(part["type"] == "image" for part in response["content"]))

    def test_bridge_omits_unprovided_fields_and_preserves_instance_and_explicit_payload(self) -> None:
        client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.update_panel("=1/2", self.base["vertices"], version="E3D21")
            request.assert_called_once_with("POST", "/api/v1/panel/update", {
                "refno": "=1/2", "vertices": self.base["vertices"], "allow_vertex_deletion": False, "dry_run": True,
            }, version="E3D21")
            request.reset_mock()
            client.update_panel("=1/2", self.base["vertices"], thickness=6, justification="negative",
                                expected_revision=self.revision, allow_vertex_deletion=True, dry_run=False, version="E3D21")
            request.assert_called_once_with("POST", "/api/v1/panel/update", {
                "refno": "=1/2", "vertices": self.base["vertices"], "thickness": 6, "justification": "negative",
                "expected_revision": self.revision, "allow_vertex_deletion": True, "dry_run": False,
            }, version="E3D21")

    def test_bridge_update_timeout_never_uses_command_cancellation_or_recovery(self) -> None:
        client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        with patch.object(client, "_request", side_effect=BridgeError("request timed out", transport_error=True)) as request, \
                patch.object(client, "_recover_command_timeout") as recover, patch.object(client, "cancel_command") as cancel:
            with self.assertRaises(BridgeError):
                client.update_panel("/PANEL", self.base["vertices"], expected_revision=self.revision, dry_run=False)
        request.assert_called_once()
        recover.assert_not_called()
        cancel.assert_not_called()


if __name__ == "__main__":
    unittest.main()

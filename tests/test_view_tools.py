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


def _capture_payload(width: int = 64, height: int = 64) -> dict:
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


class ViewToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(Settings(mode="bridge", allow_commands=True))

    def _rpc(self, method: str, params: dict, *, server=None) -> dict:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        output = StringIO()
        with patch("sys.stdout", output):
            (server or self.server).serve([request + "\n"])
        response = json.loads(output.getvalue())
        self.assertNotIn("error", response, response)
        return response["result"]

    def _call(self, name: str, arguments: dict, *, server=None) -> dict:
        return self._rpc("tools/call", {"name": name, "arguments": arguments}, server=server)

    @staticmethod
    def _success(**fields) -> dict:
        return {"ok": True, "result": {"success": True, "dry_run": False, **fields}}

    def test_camera_tools_expose_read_write_and_preview_contracts(self) -> None:
        tools = {tool["name"]: tool for tool in self._rpc("tools/list", {})["tools"]}
        state = tools["e3d_view_state"]
        self.assertTrue(state["annotations"]["readOnlyHint"])
        self.assertEqual(state["inputSchema"]["properties"], {})
        clipping = tools["e3d_view_clipping_state"]
        self.assertTrue(clipping["annotations"]["readOnlyHint"])
        self.assertTrue(clipping["annotations"]["idempotentHint"])
        self.assertFalse(clipping["inputSchema"]["additionalProperties"])
        self.assertEqual(clipping["inputSchema"]["properties"], {})
        write = tools["e3d_set_view"]
        self.assertFalse(write["annotations"]["readOnlyHint"])
        self.assertFalse(write["annotations"]["destructiveHint"])
        schema = write["inputSchema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["preset"]["default"], "isometric")
        self.assertEqual(schema["properties"]["fit"]["default"], "none")
        self.assertEqual(schema["properties"]["padding"]["default"], 1.15)
        self.assertTrue(schema["properties"]["dry_run"]["default"])
        self.assertFalse(schema["properties"]["capture_after"]["default"])
        self.assertEqual(len(schema["allOf"]), 3)

    def test_empty_set_view_call_uses_the_published_isometric_default(self) -> None:
        with patch.object(BridgeClient, "set_view", return_value=self._success(dry_run=True)) as write:
            response = self._call("e3d_set_view", {})
        self.assertFalse(response.get("isError", False), response)
        write.assert_called_once_with("isometric", "none", "", 1.15, True)

    def test_state_read_requires_no_write_permission_and_preserves_partial_fields(self) -> None:
        read_only = create_server(Settings(mode="bridge", allow_commands=False))
        state = {"ok": True, "result": {"success": False, "view_id": 8,
                 "centre": {"available": True, "x": 1, "y": 2, "z": 3},
                 "direction": {"available": False}, "read_errors": {"direction": "SDK read failed"}}}
        with patch.object(BridgeClient, "view_state", return_value=state) as read:
            response = self._call("e3d_view_state", {}, server=read_only)
        read.assert_called_once_with()
        self.assertTrue(response["isError"])
        self.assertEqual(response["structuredContent"], state)

    def test_state_rejects_any_arguments_without_reading_or_writing(self) -> None:
        with patch.object(BridgeClient, "view_state") as read, patch.object(BridgeClient, "set_view") as write:
            for args in ({"capture_after": True}, {"dry_run": True}, {"refno": "/PUMP"}):
                self.assertTrue(self._call("e3d_view_state", args)["isError"])
        read.assert_not_called()
        write.assert_not_called()

    def test_clipping_state_reads_without_arguments_and_preserves_partial_native_fields(self) -> None:
        state = {"ok": True, "result": {
            "success": False,
            "available": True,
            "complete": False,
            "status": "partial",
            "clipping_enabled": True,
            "capping": True,
            "cap_colour": 326,
            "box_data": {"available": True, "lengths": None},
            "box_faces": [{"face": "W", "defined": None, "active": None}],
            "read_errors": {"box_data.lengths": "SDK returned no vector"},
            "view_unchanged": True,
        }}
        with patch.object(BridgeClient, "view_clipping_state", return_value=state) as read:
            response = self._call("e3d_view_clipping_state", {})
        read.assert_called_once_with()
        self.assertTrue(response["isError"])
        self.assertEqual(response["structuredContent"], state)

        with patch.object(BridgeClient, "view_clipping_state") as read:
            for args in ({"capture_after": True}, {"dry_run": True}, {"refno": "/PUMP"}):
                with self.subTest(args=args):
                    self.assertTrue(self._call("e3d_view_clipping_state", args)["isError"])
        read.assert_not_called()

    def test_presets_and_fitting_forward_only_explicit_normalized_options(self) -> None:
        presets = ("isometric", "x_positive", "x_negative", "y_positive", "y_negative", "z_positive", "z_negative")
        with patch.object(BridgeClient, "set_view", return_value=self._success(dry_run=True)) as write:
            for preset in presets:
                response = self._call("e3d_set_view", {"preset": preset})
                self.assertFalse(response.get("isError", False), response)
                write.assert_called_with(preset, "none", "", 1.15, True)
            self._call("e3d_set_view", {"fit": " Drawlist ", "padding": 4})
            write.assert_called_with("isometric", "drawlist", "", 4.0, True)
            self._call("e3d_set_view", {"preset": " Z_NEGATIVE ", "fit": " TARGET ",
                                      "target_refno": " =123/456 ", "padding": 1})
            write.assert_called_with("z_negative", "target", "=123/456", 1.0, True)

    def test_whitespace_target_refno_is_treated_as_omitted_for_non_target_fit(self) -> None:
        with patch.object(BridgeClient, "set_view", return_value=self._success(dry_run=True)) as write:
            response = self._call("e3d_set_view", {"fit": "drawlist", "target_refno": " \t"})
        self.assertFalse(response.get("isError", False), response)
        write.assert_called_once_with("isometric", "drawlist", "", 1.15, True)

    def test_whitespace_target_refno_for_target_fit_is_rejected_by_schema(self) -> None:
        with patch("aveva_e3d_mcp.tools._set_view") as handler:
            server = create_server(Settings(mode="bridge", allow_commands=True))
            response = self._call("e3d_set_view", {"fit": "target", "target_refno": " \t"}, server=server)
        self.assertTrue(response["isError"])
        handler.assert_not_called()

    def test_empty_operation_invalid_enums_targets_padding_and_flags_do_not_reach_bridge(self) -> None:
        invalid = [{"preset": "keep"}, {"preset": "keep", "fit": "none"}]
        for field in ("preset", "fit"):
            invalid.extend({"preset": "isometric", field: value} for value in ("", "front", None, True, 1, []))
        invalid.extend({"preset": "isometric", "padding": value} for value in (
            True, False, None, "1.15", [], 0.99, 4.01, float("nan"), float("inf"), float("-inf"),
        ))
        invalid.extend({"preset": "isometric", "dry_run": value} for value in (None, "false", 0, 1))
        invalid.extend({"fit": "target", "target_refno": value} for value in ("", "  ", None, 1, True))
        invalid.extend((
            {"fit": "target"}, {"preset": "isometric", "target_refno": "/PUMP"},
            {"fit": "drawlist", "target_refno": "/PUMP"},
        ))
        with patch.object(BridgeClient, "set_view") as write, patch.object(BridgeClient, "capture_view") as capture:
            for args in invalid:
                with self.subTest(args=args):
                    self.assertTrue(self._call("e3d_set_view", args)["isError"])
        write.assert_not_called()
        capture.assert_not_called()

    def test_noop_set_view_is_rejected_by_schema_before_handler(self) -> None:
        with patch("aveva_e3d_mcp.tools._set_view") as handler:
            server = create_server(Settings(mode="bridge", allow_commands=True))
            response = self._call("e3d_set_view", {"preset": "keep", "fit": "none"}, server=server)
        self.assertTrue(response["isError"])
        self.assertEqual(response["content"], [{"type": "text", "text": "Invalid e3d_set_view arguments"}])
        handler.assert_not_called()

    def test_unknown_controls_are_rejected_before_any_view_change(self) -> None:
        with patch.object(BridgeClient, "set_view") as write, patch.object(BridgeClient, "capture_view") as capture:
            for field in ("auto_add_current", "restore", "shaded", "command", "refno"):
                result = self._call("e3d_set_view", {"preset": "isometric", field: True,
                                                    "dry_run": False, "capture_after": True})
                self.assertTrue(result["isError"])
                self.assertEqual(result["structuredContent"]["receipt"]["capture"]["reason"], "operation_failed")
        write.assert_not_called()
        capture.assert_not_called()

    def test_closed_view_tool_schemas_block_unknown_arguments_before_native_writes(self) -> None:
        with patch.object(BridgeClient, "fit_view") as fit, patch.object(BridgeClient, "restore_view") as restore:
            for name, arguments in (("e3d_fit_view", {"dry_run": True}),
                                    ("e3d_restore_view", {"fit": False, "dry_run": True})):
                with self.subTest(name=name):
                    result = self._call(name, arguments)
                    self.assertTrue(result["isError"])
                    self.assertIn(f"Unknown {name} argument", result["content"][0]["text"])
            fit.assert_not_called()
            restore.assert_not_called()

    def test_execution_requires_permission_and_an_explicit_false_preview_flag(self) -> None:
        read_only = create_server(Settings(mode="bridge", allow_commands=False))
        with patch.object(BridgeClient, "set_view", return_value=self._success()) as write:
            preview = self._call("e3d_set_view", {"preset": "isometric"}, server=read_only)
            self.assertFalse(preview.get("isError", False), preview)
            write.assert_called_once_with("isometric", "none", "", 1.15, True)
            denied = self._call("e3d_set_view", {"preset": "isometric", "dry_run": False}, server=read_only)
            self.assertTrue(denied["isError"])
            self.assertEqual(write.call_count, 1)
            written = self._call("e3d_set_view", {"preset": "isometric", "dry_run": False})
            self.assertFalse(written.get("isError", False), written)
            write.assert_called_with("isometric", "none", "", 1.15, False)

    def test_explicit_target_view_captures_once_after_success_without_implicit_membership_changes(self) -> None:
        events = []

        def set_view(*args):
            events.append("set_view")
            return self._success(target_in_drawlist=True, after={"view_id": 9})

        def capture(width, height):
            events.append("capture")
            return _capture_payload(width, height)

        with patch.object(BridgeClient, "set_view", side_effect=set_view) as write, \
                patch.object(BridgeClient, "capture_view", side_effect=capture) as image, \
                patch.object(BridgeClient, "fit_view") as fit, \
                patch.object(BridgeClient, "restore_view") as restore, \
                patch.object(BridgeClient, "set_drawlist_visual") as membership:
            result = self._call("e3d_set_view", {"preset": "x_positive", "fit": "target", "target_refno": "/PUMP",
                "dry_run": False, "capture_after": True, "capture_width": 64, "capture_height": 64})
        self.assertEqual(events, ["set_view", "capture"])
        write.assert_called_once_with("x_positive", "target", "/PUMP", 1.15, False)
        image.assert_called_once_with(64, 64)
        fit.assert_not_called()
        restore.assert_not_called()
        membership.assert_not_called()
        self.assertFalse(result.get("isError", False), result)
        self.assertEqual(result["structuredContent"]["result"]["after"], {"view_id": 9})
        self.assertEqual(result["structuredContent"]["receipt"]["capture"]["status"], "captured")
        self.assertEqual(len([part for part in result["content"] if part["type"] == "image"]), 1)

    def test_preview_and_failed_or_uncertain_set_view_never_capture_or_replay(self) -> None:
        cases = [
            ({}, self._success(dry_run=True), "dry_run", False),
            ({"dry_run": False}, {"ok": True, "result": {"success": False, "write_applied": True}}, "operation_failed", True),
            ({"dry_run": False}, BridgeError("connection closed", transport_error=True), "operation_outcome_unknown", True),
        ]
        for options, outcome, reason, failed in cases:
            with self.subTest(reason=reason), patch.object(BridgeClient, "set_view") as write, \
                    patch.object(BridgeClient, "capture_view") as capture:
                if isinstance(outcome, Exception):
                    write.side_effect = outcome
                else:
                    write.return_value = outcome
                result = self._call("e3d_set_view", {"preset": "isometric", "capture_after": True, **options})
            write.assert_called_once()
            capture.assert_not_called()
            self.assertEqual(bool(result.get("isError")), failed)
            self.assertEqual(result["structuredContent"]["receipt"]["capture"]["reason"], reason)
            self.assertFalse(result["structuredContent"]["receipt"]["operation_replayed"])
            if reason == "operation_outcome_unknown":
                self.assertTrue(result["structuredContent"]["outcome_uncertain"])
                self.assertFalse(result["structuredContent"]["retry_safe"])

    def test_failed_capture_preserves_successful_camera_change_without_replay(self) -> None:
        with patch.object(BridgeClient, "set_view", return_value=self._success(write_applied=True)) as write, \
                patch.object(BridgeClient, "capture_view", side_effect=BridgeError("capture unavailable")) as capture:
            result = self._call("e3d_set_view", {"preset": "isometric", "dry_run": False, "capture_after": True})
        write.assert_called_once()
        capture.assert_called_once()
        self.assertFalse(result.get("isError", False), result)
        self.assertTrue(result["structuredContent"]["result"]["write_applied"])
        self.assertEqual(result["structuredContent"]["receipt"]["capture"]["status"], "failed")
        self.assertFalse(any(part["type"] == "image" for part in result["content"]))

    def test_mock_never_claims_native_state_or_changes_or_returns_an_image(self) -> None:
        mock = create_server(Settings(mode="mock", allow_commands=True, version="E3D21"))
        state = self._call("e3d_view_state", {}, server=mock)
        self.assertTrue(state["isError"])
        self.assertFalse(state["structuredContent"]["result"]["available"])
        self.assertEqual(state["structuredContent"]["version"], "E3D21")
        preview = self._call("e3d_set_view", {"preset": "isometric", "capture_after": True}, server=mock)
        self.assertFalse(preview.get("isError", False), preview)
        self.assertTrue(preview["structuredContent"]["result"]["mock"])
        self.assertEqual(preview["structuredContent"]["receipt"]["capture"]["reason"], "dry_run")
        executed = self._call("e3d_set_view", {"preset": "isometric", "dry_run": False, "capture_after": True}, server=mock)
        self.assertTrue(executed["isError"])
        self.assertFalse(executed["structuredContent"]["result"]["write_attempted"])
        for response in (state, preview, executed):
            self.assertFalse(any(part["type"] == "image" for part in response["content"]))

    def test_mock_clipping_state_is_explicitly_unavailable(self) -> None:
        mock = create_server(Settings(mode="mock", allow_commands=True, version="E3D21"))
        response = self._call("e3d_view_clipping_state", {}, server=mock)
        self.assertTrue(response["isError"])
        result = response["structuredContent"]["result"]
        self.assertFalse(result["available"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["box_data"])
        self.assertEqual(result["box_faces"], [])

    def test_bridge_routes_preserve_the_selected_instance_and_payload(self) -> None:
        client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.view_state(version="E3D21")
            request.assert_called_once_with("GET", "/api/v1/view/state", version="E3D21")
            request.reset_mock()
            client.view_clipping_state(version="E3D21")
            request.assert_called_once_with("GET", "/api/v1/view/clipping-state", version="E3D21")
            request.reset_mock()
            client.set_view("keep", "target", "=123/456", 1.5, True, version="E3D21")
            request.assert_called_once_with("POST", "/api/v1/view/set", {
                "preset": "keep", "fit": "target", "target_refno": "=123/456", "padding": 1.5, "dry_run": True,
            }, version="E3D21")


if __name__ == "__main__":
    unittest.main()

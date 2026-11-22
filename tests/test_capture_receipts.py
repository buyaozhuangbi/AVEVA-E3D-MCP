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

from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.protocol import ImageToolResult, McpServer
from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.tools import create_server


def _png_base64(width: int = 64, height: int = 64) -> str:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = (b"\x00" + bytes(width * 3)) * height
    image = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
             + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    return base64.b64encode(image).decode("ascii")


def _capture_payload(width: int = 64, height: int = 64) -> dict:
    return {"ok": True, "version": "E3D21", "result": {
        "success": True, "mime_type": "image/png", "image_base64": _png_base64(width, height),
        "width": width, "height": height, "source": "E3D View3D.GetBitmap", "drawlist_id": 7,
    }}


def _verified_delete_payload(**changes) -> dict:
    result = {
        "success": True, "refno": "=1/2", "delete_attempted": True,
        "native_delete_returned": True, "verification_status": "confirmed_deleted",
        "deletion_verified": True, "readback_refno": "=1/2",
        "readback_reference_is_valid": False, "readback_deleted_state": True,
        "readback_absent_from_owner": True,
        "readback_owner_membership_matches_expected": True, "owner_refno": "=1/1",
    }
    result.update(changes)
    return {"ok": True, "version": "E3D21", "result": result}


class CaptureReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(Settings(mode="bridge", allow_commands=True))

    def _rpc(self, method: str, params: dict) -> dict:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        output = StringIO()
        with patch("sys.stdout", output):
            self.server.serve([request + "\n"])
        response = json.loads(output.getvalue())
        self.assertNotIn("error", response, response)
        return response["result"]

    def _call(self, name: str, arguments: dict) -> dict:
        return self._rpc("tools/call", {"name": name, "arguments": arguments})

    @staticmethod
    def _success(**result) -> dict:
        return {"ok": True, "version": "E3D21", "result": {"success": True, **result}}

    def test_receipt_controls_are_discoverable_on_all_edit_and_view_operations(self) -> None:
        definitions = {tool["name"]: tool for tool in self._rpc("tools/list", {})["tools"]}
        names = {
            "e3d_create_element", "e3d_copy_element", "e3d_set_transform", "e3d_set_attribute", "e3d_set_attributes", "e3d_delete_element",
            "e3d_set_drawlist_visual", "e3d_set_primitive_visual", "e3d_remove_drawlist_member", "e3d_execute_pml", "e3d_execute_pml_batch", "e3d_execute_pml_script",
            "e3d_fit_view", "e3d_restore_view",
            "e3d_set_view", "e3d_set_camera", "e3d_create_panel", "e3d_update_panel", "e3d_update_primitive",
        }
        self.assertEqual(len(names), 19)
        self.assertEqual(names, {name for name, definition in definitions.items()
                                 if "capture_after" in definition["inputSchema"].get("properties", {})})
        for name in names:
            schema = definitions[name]["inputSchema"]
            self.assertEqual(schema["properties"]["capture_after"], {
                "type": "boolean", "default": False,
                "description": "After a confirmed successful operation, return a native E3D image receipt.",
            })
            self.assertEqual(schema["properties"]["capture_width"]["default"], 1280)
            self.assertEqual(schema["properties"]["capture_height"]["default"], 960)
            for dimension in ("capture_width", "capture_height"):
                self.assertEqual(schema["properties"][dimension]["minimum"], 64)
                self.assertEqual(schema["properties"][dimension]["maximum"], 2048)

    def test_capture_parameters_are_validated_before_any_write(self) -> None:
        with patch.object(BridgeClient, "create_element", return_value=self._success()) as create, \
                patch.object(BridgeClient, "capture_view", return_value=_capture_payload()) as capture:
            invalid = [
                {"capture_after": "yes"}, {"capture_width": True}, {"capture_width": 63},
                {"capture_height": 2049}, {"capture_height": 960.0},
            ]
            for options in invalid:
                with self.subTest(options=options):
                    result = self._call("e3d_create_element", {
                        "parent_refno": "/PUMP", "element_type": "BOX", "dry_run": False,
                        "expected_revision": "a" * 64,
                        "capture_after": True, **options,
                    })
                    self.assertTrue(result["isError"])
            create.assert_not_called()
            capture.assert_not_called()

    def test_invalid_receipt_options_block_every_target_operation(self) -> None:
        operations = [
            ("e3d_create_element", "create_element", {"parent_refno": "/PUMP", "element_type": "BOX", "dry_run": False,
                                                      "expected_revision": "a" * 64}),
            ("e3d_copy_element", "copy_element", {"source_refno": "/PUMP", "parent_refno": "/DESTINATION", "name": "/COPY",
                                                 "expected_revision": "a" * 64, "dry_run": False}),
            ("e3d_create_panel", "create_panel", {"parent_refno": "/PUMP", "vertices": [[0, 0], [10, 0], [0, 10]], "thickness": 1, "dry_run": False}),
            ("e3d_update_panel", "update_panel", {"refno": "/PANEL", "vertices": [[0, 0], [10, 0], [0, 10]],
                                                 "expected_revision": "a" * 64, "dry_run": False}),
            ("e3d_update_primitive", "update_primitive", {"refno": "/PRIMITIVE", "dimensions": {"DIAM": 10},
                                                         "expected_revision": "a" * 64, "dry_run": False}),
            ("e3d_set_transform", "set_transform", {"refno": "/PUMP", "position": [1, 2, 3], "dry_run": False}),
            ("e3d_set_attribute", "set_attribute", {"refno": "/PUMP", "attribute": "DESC", "value": "x", "dry_run": False}),
            ("e3d_set_attributes", "set_attributes", {"refno": "/PUMP", "values": {"DESC": "x"}, "expected_revision": "a" * 64, "dry_run": False}),
            ("e3d_delete_element", "delete_element", {"refno": "/PUMP", "dry_run": False}),
            ("e3d_set_drawlist_visual", "set_drawlist_visual", {"refno": "/PUMP", "dry_run": False}),
            ("e3d_set_primitive_visual", "set_primitive_visual", {"refno": "/PART", "colour": "RED",
                                                                 "expected_revision": "a" * 64, "dry_run": False}),
            ("e3d_remove_drawlist_member", "remove_drawlist_member", {"refno": "/PUMP",
                                                                    "expected_revision": "a" * 64, "dry_run": False}),
            ("e3d_execute_pml", "command", {"command": "$P DONE", "dry_run": False}),
            ("e3d_execute_pml_batch", "command_batch", {"commands": ["$P DONE"], "dry_run": False}),
            ("e3d_fit_view", "fit_view", {}), ("e3d_restore_view", "restore_view", {}),
            ("e3d_set_view", "set_view", {"preset": "isometric", "dry_run": False}),
            ("e3d_set_camera", "set_camera", {"centre": [1, 2, 3], "direction": [1, 1, -1], "radius": 100,
                "perspective": False, "extra_twist_degrees": 10, "expected_revision": "a" * 64, "dry_run": False}),
        ]
        for name, method, args in operations:
            with self.subTest(tool=name), patch.object(BridgeClient, method) as operation, \
                    patch.object(BridgeClient, "capture_view") as screenshot:
                response = self._call(name, {**args, "capture_after": True, "capture_width": 63})
                self.assertTrue(response["isError"])
                operation.assert_not_called()
                screenshot.assert_not_called()

    def test_capture_disabled_keeps_existing_response_and_still_validates_options(self) -> None:
        operation = self._success(refno="=1/2")
        with patch.object(BridgeClient, "create_element", return_value=operation) as write, \
                patch.object(BridgeClient, "capture_view") as capture:
            response = self._call("e3d_create_element", {
                "parent_refno": "/PUMP", "element_type": "BOX", "dry_run": False,
                "expected_revision": "a" * 64,
            })
            self.assertEqual(response["structuredContent"], operation)
            invalid = self._call("e3d_create_element", {
                "parent_refno": "/PUMP", "element_type": "BOX", "dry_run": False,
                "expected_revision": "a" * 64,
                "capture_after": False, "capture_height": True,
            })
        self.assertTrue(invalid["isError"])
        write.assert_called_once_with("/PUMP", "BOX", "", {}, None, None, None, "OWNER", False,
                                      expected_revision="a" * 64)
        capture.assert_not_called()

    def test_confirmed_write_runs_once_before_native_image_and_returns_receipt(self) -> None:
        events: list[str] = []

        def write(*args, **kwargs):
            self.assertIs(args[-1], False)
            self.assertEqual(kwargs, {"expected_revision": "a" * 64})
            events.append("write")
            return self._success(refno="=1/2")

        def capture(width: int, height: int):
            events.append("capture")
            self.assertEqual((width, height), (640, 480))
            return _capture_payload(width, height)

        with patch.object(BridgeClient, "create_element", side_effect=write) as create, \
                patch.object(BridgeClient, "capture_view", side_effect=capture) as screenshot:
            response = self._call("e3d_create_element", {
                "parent_refno": "/PUMP", "element_type": "BOX", "dry_run": False,
                "expected_revision": "a" * 64,
                "capture_after": True, "capture_width": 640, "capture_height": 480,
            })

        self.assertEqual(events, ["write", "capture"])
        create.assert_called_once()
        screenshot.assert_called_once_with(640, 480)
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"]["result"]["refno"], "=1/2")
        self.assertEqual(response["structuredContent"]["receipt"], {
            "operation_replayed": False,
            "capture": {"success": True, "status": "captured", "width": 640, "height": 480,
                        "source": "E3D View3D.GetBitmap", "drawlist_id": 7, "mime_type": "image/png"},
        })
        images = [part for part in response["content"] if part["type"] == "image"]
        self.assertEqual(images, [{"type": "image", "mimeType": "image/png", "data": _png_base64(640, 480)}])

    def test_capture_failure_keeps_confirmed_write_and_never_marks_it_failed(self) -> None:
        operation = self._success(refno="=1/2")
        with patch.object(BridgeClient, "set_transform", return_value=operation) as write, \
                patch.object(BridgeClient, "capture_view", return_value={
                    "ok": True, "result": {"success": False, "error": "graphics not ready"},
                }) as screenshot:
            response = self._call("e3d_set_transform", {
                "refno": "=1/2", "position": [10, 20, 30], "dry_run": False, "capture_after": True,
            })

        write.assert_called_once()
        screenshot.assert_called_once_with(1280, 960)
        self.assertFalse(response.get("isError", False), response)
        metadata = response["structuredContent"]
        self.assertEqual(metadata["result"]["refno"], "=1/2")
        self.assertEqual(metadata["result"]["success"], True)
        self.assertEqual(metadata["receipt"]["operation_replayed"], False)
        self.assertEqual(metadata["receipt"]["capture"]["status"], "failed")
        self.assertIn("graphics not ready", metadata["receipt"]["capture"]["error"])
        self.assertFalse(any(part["type"] == "image" for part in response["content"]))

    @staticmethod
    def _visual_operations(persistence: dict) -> list[tuple[str, str, dict, dict]]:
        return [
            ("e3d_set_drawlist_visual", "set_drawlist_visual",
             {"refno": "=1/2", "colour": "RED", "add": False, "dry_run": False},
             {"ok": True, "version": "E3D21", "result": {
                 "success": True, "dry_run": False, "readback_verified": True,
                 "all_members_verified": True, "membership_verified": True,
                 "rollback_attempted": False, "persistence": persistence,
             }}),
            ("e3d_restore_view", "restore_view", {"fit": False},
             {"ok": True, "version": "E3D21", "result": {
                 "ready": True, "restore_succeeded": True, "visual_restore_succeeded": True,
                 "pending_count": 0, "add_errors": {}, "visual_errors": {}, "persistence": persistence,
             }}),
            ("e3d_fit_view", "fit_view", {"auto_add_current": True},
             {"ok": True, "version": "E3D21", "result": {
                 "success": True, "auto_added_current": True, "persistence": persistence,
             }}),
        ]

    @staticmethod
    def _failed_visual_persistence() -> dict:
        return {
            "attempted": True, "succeeded": False, "status": "write_failed",
            "path": r"C:\visual-test\E3D21.drawlist.json", "error": "Access to the snapshot was denied.",
            "legacy_export": {
                "attempted": False, "succeeded": None, "status": "not_attempted",
                "path": r"C:\visual-test\E3D21.drawlist", "error": None,
            },
        }

    def test_visual_save_failure_preserves_display_success_and_optional_receipt(self) -> None:
        for name, method, arguments, operation in self._visual_operations(self._failed_visual_persistence()):
            for capture_after in (False, True):
                with self.subTest(tool=name, capture_after=capture_after), \
                        patch.object(BridgeClient, method, return_value=operation) as display, \
                        patch.object(BridgeClient, "capture_view", return_value=_capture_payload()) as capture:
                    response = self._call(name, {
                        **arguments, "capture_after": capture_after, "capture_width": 64, "capture_height": 64,
                    })
                display.assert_called_once()
                self.assertFalse(response.get("isError", False), response)
                metadata = response["structuredContent"]
                self.assertEqual(metadata["result"], operation["result"])
                self.assertFalse(metadata["result"]["persistence"]["succeeded"])
                text_result = json.loads(next(part["text"] for part in response["content"] if part["type"] == "text"))
                self.assertEqual(text_result["result"], operation["result"])
                self.assertNotIn("outcome_uncertain", metadata)
                images = [part for part in response["content"] if part["type"] == "image"]
                if capture_after:
                    capture.assert_called_once_with(64, 64)
                    self.assertEqual(len(images), 1)
                    self.assertEqual(metadata["receipt"]["capture"]["status"], "captured")
                    self.assertFalse(metadata["receipt"]["operation_replayed"])
                else:
                    capture.assert_not_called()
                    self.assertEqual(images, [])
                    self.assertNotIn("receipt", metadata)

    def test_visual_save_and_capture_failures_preserve_both_diagnostics(self) -> None:
        for name, method, arguments, operation in self._visual_operations(self._failed_visual_persistence()):
            with self.subTest(tool=name), patch.object(BridgeClient, method, return_value=operation) as display, \
                    patch.object(BridgeClient, "capture_view", return_value={
                        "ok": True, "result": {"success": False, "error": "Graphics capture unavailable."},
                    }) as capture:
                response = self._call(name, {**arguments, "capture_after": True})
            display.assert_called_once()
            capture.assert_called_once_with(1280, 960)
            self.assertFalse(response.get("isError", False), response)
            metadata = response["structuredContent"]
            self.assertEqual(metadata["result"], operation["result"])
            self.assertEqual(metadata["receipt"]["capture"]["status"], "failed")
            self.assertEqual(metadata["receipt"]["capture"]["error"], "Graphics capture unavailable.")
            self.assertFalse(metadata["receipt"]["operation_replayed"])
            self.assertFalse(any(part["type"] == "image" for part in response["content"]))

    def test_visual_legacy_export_failure_keeps_authoritative_save_and_receipt_success(self) -> None:
        persistence = {
            "attempted": True, "succeeded": True, "status": "saved",
            "path": r"C:\visual-test\E3D21.drawlist.json", "error": None,
            "legacy_export": {
                "attempted": True, "succeeded": False, "status": "write_failed",
                "path": r"C:\visual-test\E3D21.drawlist", "error": "Legacy export is locked.",
            },
        }
        for name, method, arguments, operation in self._visual_operations(persistence):
            with self.subTest(tool=name), patch.object(BridgeClient, method, return_value=operation) as display, \
                    patch.object(BridgeClient, "capture_view", return_value=_capture_payload()) as capture:
                response = self._call(name, {**arguments, "capture_after": True})
            display.assert_called_once()
            capture.assert_called_once_with(1280, 960)
            self.assertFalse(response.get("isError", False), response)
            metadata = response["structuredContent"]
            self.assertEqual(metadata["result"], operation["result"])
            self.assertEqual(metadata["receipt"]["capture"]["status"], "captured")
            self.assertFalse(metadata["receipt"]["operation_replayed"])

    def test_visual_preview_and_display_failure_keep_persistence_diagnostics_and_skip_capture(self) -> None:
        cases = [
            ("e3d_set_drawlist_visual", "set_drawlist_visual", {"refno": "=1/2"},
             {"dry_run": True, "colour": "RED"}, "preview", "dry_run", False),
            ("e3d_set_drawlist_visual", "set_drawlist_visual", {"refno": "=1/2", "dry_run": False},
             {"success": False, "error": "Visual readback failed.", "rollback_verified_original": True},
             "display_failed", "operation_failed", True),
            ("e3d_restore_view", "restore_view", {}, {"ready": False, "restore_succeeded": False},
             "restore_incomplete", "operation_not_ready", False),
        ]
        for name, method, arguments, result, status, reason, is_error in cases:
            persistence = self._failed_visual_persistence()
            persistence.update(attempted=False, succeeded=None, status=status, error=None)
            operation = {"ok": True, "version": "E3D21", "result": {**result, "persistence": persistence}}
            with self.subTest(tool=name, status=status), \
                    patch.object(BridgeClient, method, return_value=operation) as display, \
                    patch.object(BridgeClient, "capture_view") as capture:
                response = self._call(name, {**arguments, "capture_after": True})
            display.assert_called_once()
            capture.assert_not_called()
            self.assertEqual(response.get("isError", False), is_error, response)
            metadata = response["structuredContent"]
            self.assertEqual(metadata["result"], operation["result"])
            self.assertEqual(metadata["receipt"]["capture"]["status"], "skipped")
            self.assertEqual(metadata["receipt"]["capture"]["reason"], reason)
            self.assertFalse(metadata["receipt"]["operation_replayed"])

    def test_visual_native_rejection_skips_capture_without_claiming_persistence(self) -> None:
        cases = [
            ("e3d_set_drawlist_visual", "set_drawlist_visual",
             {"refno": "=1/2", "colour": "UNKNOWN-COLOUR", "dry_run": dry_run},
             "Unknown native E3D colour name or index: UNKNOWN-COLOUR")
            for dry_run in (True, False)
        ] + [
            ("e3d_fit_view", "fit_view", {}, "No current E3D 3D view is available."),
        ]
        for name, method, arguments, error in cases:
            with self.subTest(tool=name, arguments=arguments), \
                    patch.object(BridgeClient, method, side_effect=BridgeError(error)) as display, \
                    patch.object(BridgeClient, "capture_view") as capture:
                response = self._call(name, {**arguments, "capture_after": True})
            display.assert_called_once()
            capture.assert_not_called()
            self.assertTrue(response["isError"])
            metadata = response["structuredContent"]
            self.assertEqual(metadata["error"], error)
            self.assertNotIn("result", metadata)
            self.assertNotIn("persistence", metadata)
            self.assertNotIn("outcome_uncertain", metadata)
            self.assertEqual(metadata["receipt"]["capture"]["reason"], "operation_failed")
            self.assertFalse(metadata["receipt"]["operation_replayed"])

    def test_capture_exception_or_bad_png_does_not_replay_or_lose_operation(self) -> None:
        operation = self._success(attribute="DESC", value="updated")
        for outcome in (BridgeError("HTTP capture timeout", transport_error=True), {"ok": True, "result": {
            "success": True, "mime_type": "image/png", "image_base64": "invalid PNG",
        }}):
            with self.subTest(outcome=outcome), patch.object(BridgeClient, "set_attribute", return_value=operation) as write, \
                    patch.object(BridgeClient, "capture_view", side_effect=outcome if isinstance(outcome, Exception) else None,
                                 return_value=outcome) as capture:
                response = self._call("e3d_set_attribute", {
                    "refno": "/PUMP", "attribute": "DESC", "value": "updated", "dry_run": False,
                    "capture_after": True,
                })
                write.assert_called_once()
                capture.assert_called_once_with(1280, 960)
                metadata = response["structuredContent"]
                self.assertTrue(metadata["ok"])
                self.assertEqual(metadata["result"], operation["result"])
                self.assertEqual(metadata["receipt"]["capture"]["status"], "failed")
                self.assertFalse(metadata["receipt"]["operation_replayed"])
                self.assertNotIn("outcome_uncertain", metadata)
                self.assertNotIn("retry_safe", metadata)
                self.assertFalse(response.get("isError", False), response)

    def test_write_transport_error_reports_unknown_outcome_without_replay_or_capture(self) -> None:
        with patch.object(BridgeClient, "set_attribute", side_effect=BridgeError(
                "HTTP write response timed out", transport_error=True)) as write, \
                patch.object(BridgeClient, "capture_view") as capture:
            response = self._call("e3d_set_attribute", {
                "refno": "/PUMP", "attribute": "DESC", "value": "updated", "dry_run": False,
                "capture_after": True,
            })

        write.assert_called_once_with("/PUMP", "DESC", "updated", False)
        capture.assert_not_called()
        self.assertTrue(response["isError"])
        metadata = response["structuredContent"]
        self.assertFalse(metadata["ok"])
        self.assertTrue(metadata["outcome_uncertain"])
        self.assertFalse(metadata["retry_safe"])
        self.assertNotIn("success", metadata)
        self.assertNotIn("result", metadata)
        self.assertIn("HTTP write response timed out", metadata["error"])
        self.assertIn("may already have taken effect", metadata["error"])
        self.assertIn("Do not automatically resend", metadata["error"])
        self.assertIn("Read back the target state", metadata["error"])
        self.assertFalse(metadata["receipt"]["operation_replayed"])
        self.assertEqual(metadata["receipt"]["capture"]["status"], "skipped")
        self.assertEqual(metadata["receipt"]["capture"]["reason"], "operation_outcome_unknown")
        self.assertFalse(any(part["type"] == "image" for part in response["content"]))

    def test_preview_and_failed_operation_skip_capture_with_reasons(self) -> None:
        cases = [
            ("e3d_set_attribute", {"refno": "/PUMP", "attribute": "DESC", "value": "x"},
             self._success(dry_run=True), "dry_run"),
            ("e3d_set_attributes", {"refno": "/PUMP", "values": {"DESC": "x"}},
             self._success(dry_run=True), "dry_run"),
            ("e3d_set_attribute", {"refno": "/PUMP", "attribute": "DESC", "value": "x", "dry_run": True},
             self._success(dry_run=True), "dry_run"),
            ("e3d_delete_element", {"refno": "/PUMP", "dry_run": False},
             {"ok": True, "result": {"success": False, "delete_attempted": False,
                                       "error": "not deletable"}}, "operation_failed"),
            ("e3d_set_drawlist_visual", {"refno": "/PUMP", "dry_run": False},
             {"ok": False, "error": "busy"}, "operation_failed"),
            ("e3d_restore_view", {"fit": False},
             {"ok": True, "result": {"ready": False, "retry": True}}, "operation_not_ready"),
        ]
        methods = {
            "e3d_set_attribute": "set_attribute", "e3d_delete_element": "delete_element",
            "e3d_set_attributes": "set_attributes",
            "e3d_set_drawlist_visual": "set_drawlist_visual", "e3d_restore_view": "restore_view",
        }
        for tool_name, args, operation, reason in cases:
            with self.subTest(tool=tool_name), patch.object(BridgeClient, methods[tool_name], return_value=operation), \
                    patch.object(BridgeClient, "capture_view", return_value=_capture_payload()) as screenshot:
                response = self._call(tool_name, {**args, "capture_after": True})
                self.assertEqual(response["structuredContent"]["receipt"]["capture"]["reason"], reason)
                screenshot.assert_not_called()
                if McpServer._operation_failed(operation):
                    self.assertTrue(response["isError"])
                else:
                    self.assertFalse(response.get("isError", False))

    def test_delete_requires_native_exact_reference_and_owner_verification_before_capture(self) -> None:
        incomplete = self._success(delete_attempted=True, native_delete_returned=True)
        for changes in (
            {"verification_status": "still_present", "readback_reference_is_valid": True,
             "readback_deleted_state": False, "readback_absent_from_owner": False},
            {"readback_owner_membership_matches_expected": False},
            {"readback_refno": "=1/999"},
            {"readback_deleted_state": False},
        ):
            payload = _verified_delete_payload(**changes)
            with self.subTest(changes=changes), patch.object(BridgeClient, "delete_element", return_value=payload) as delete, \
                    patch.object(BridgeClient, "capture_view", return_value=_capture_payload()) as capture:
                result = self._call("e3d_delete_element", {"refno": "=1/2", "dry_run": False, "capture_after": True})
            delete.assert_called_once_with("=1/2", False)
            capture.assert_not_called()
            self.assertTrue(result["isError"])
            receipt = result["structuredContent"]["result"]
            self.assertFalse(receipt["success"])
            self.assertEqual(receipt["verification_status"], "unknown")
            self.assertTrue(receipt["outcome_uncertain"])
            self.assertFalse(receipt["retry_safe"])
            self.assertEqual(result["structuredContent"]["receipt"]["capture"]["reason"], "operation_outcome_unknown")

        with patch.object(BridgeClient, "delete_element", return_value=incomplete), \
                patch.object(BridgeClient, "capture_view") as capture:
            result = self._call("e3d_delete_element", {"refno": "=1/2", "dry_run": False, "capture_after": True})
        capture.assert_not_called()
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["result"]["verification_status"], "unknown")

        with patch.object(BridgeClient, "delete_element", return_value=_verified_delete_payload()), \
                patch.object(BridgeClient, "capture_view", return_value=_capture_payload()) as capture:
            result = self._call("e3d_delete_element", {"refno": "=1/2", "dry_run": False, "capture_after": True})
        capture.assert_called_once()
        self.assertFalse(result.get("isError", False), result)
        self.assertEqual(result["structuredContent"]["result"]["verification_status"], "confirmed_deleted")

    def test_attempted_delete_with_incomplete_or_failed_readback_is_never_retryable(self) -> None:
        attempted_without_readback = {"ok": True, "result": {
            "success": False, "delete_attempted": True, "native_delete_returned": False,
            "error": "Delete returned false",
        }}
        for payload in (
            attempted_without_readback,
            {"ok": True, "result": {"success": False, "error": "Legacy add-in response"}},
            _verified_delete_payload(success=False, delete_attempted=True,
                verification_status="unknown", deletion_verified=False,
                readback_refno="=1/2", readback_reference_is_valid=None,
                readback_deleted_state=None, readback_absent_from_owner=None,
                readback_owner_membership_matches_expected=None),
        ):
            with self.subTest(payload=payload), \
                    patch.object(BridgeClient, "delete_element", return_value=payload), \
                    patch.object(BridgeClient, "capture_view") as capture:
                response = self._call("e3d_delete_element", {
                    "refno": "=1/2", "dry_run": False, "capture_after": True})
            capture.assert_not_called()
            self.assertTrue(response["isError"])
            result = response["structuredContent"]["result"]
            self.assertEqual(result["verification_status"], "unknown")
            self.assertTrue(result["outcome_uncertain"])
            self.assertFalse(result["retry_safe"])
            self.assertEqual(response["structuredContent"]["receipt"]["capture"]["reason"],
                             "operation_outcome_unknown")

    def test_attempted_delete_with_complete_still_present_readback_is_not_uncertain_or_retryable(self) -> None:
        payload = _verified_delete_payload(success=False, delete_attempted=True,
            verification_status="still_present", deletion_verified=False,
            readback_refno="=1/2", readback_reference_is_valid=True,
            readback_deleted_state=False, readback_absent_from_owner=False,
            readback_owner_membership_matches_expected=False)
        with patch.object(BridgeClient, "delete_element", return_value=payload), \
                patch.object(BridgeClient, "capture_view") as capture:
            response = self._call("e3d_delete_element", {
                "refno": "=1/2", "dry_run": False, "capture_after": True})
        capture.assert_not_called()
        self.assertTrue(response["isError"])
        result = response["structuredContent"]["result"]
        self.assertEqual(result["verification_status"], "still_present")
        self.assertTrue(result["outcome_uncertain"])
        self.assertFalse(result["retry_safe"])
        self.assertEqual(response["structuredContent"]["receipt"]["capture"]["reason"],
                         "operation_outcome_unknown")

    def test_native_preflight_rejection_explicitly_preserves_safe_retry_state(self) -> None:
        response = {"ok": True, "result": {
            "success": False, "delete_attempted": False,
            "verification_status": "not_attempted", "outcome_uncertain": False,
            "retry_safe": True, "error": "Element was not found"}}
        with patch.object(BridgeClient, "delete_element", return_value=response), \
                patch.object(BridgeClient, "capture_view") as capture:
            result = self._call("e3d_delete_element", {"refno": "=1/2", "dry_run": False,
                                                        "capture_after": True})
        capture.assert_not_called()
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["result"]["verification_status"], "not_attempted")
        self.assertFalse(result["structuredContent"]["result"]["outcome_uncertain"])
        self.assertTrue(result["structuredContent"]["result"]["retry_safe"])
        self.assertEqual(result["structuredContent"]["receipt"]["capture"]["reason"], "operation_failed")

    def test_delete_transport_uncertainty_is_never_replayed_or_captured(self) -> None:
        for capture_after in (False, True):
            with self.subTest(capture_after=capture_after), \
                    patch.object(BridgeClient, "delete_element", side_effect=BridgeError("connection closed", transport_error=True)) as delete, \
                    patch.object(BridgeClient, "capture_view") as capture:
                result = self._call("e3d_delete_element", {"refno": "=1/2", "dry_run": False, "capture_after": capture_after})
            delete.assert_called_once_with("=1/2", False)
            capture.assert_not_called()
            self.assertTrue(result["isError"])
            self.assertTrue(result["structuredContent"]["outcome_uncertain"])
            self.assertFalse(result["structuredContent"]["retry_safe"])

    def test_mutation_transport_uncertainty_is_explicit_with_or_without_a_receipt(self) -> None:
        operations = [
            ("e3d_delete_element", "delete_element", {"refno": "=1/2", "dry_run": False}),
            ("e3d_set_attribute", "set_attribute", {"refno": "/PUMP", "attribute": "DESC", "value": "x", "dry_run": False}),
            ("e3d_set_attributes", "set_attributes", {"refno": "/PUMP", "values": {"DESC": "x"}, "expected_revision": "a" * 64, "dry_run": False}),
            ("e3d_create_panel", "create_panel", {"parent_refno": "/PUMP", "vertices": [[0, 0], [10, 0], [0, 10]],
                                                 "thickness": 1, "dry_run": False}),
            ("e3d_fit_view", "fit_view", {}),
            ("e3d_restore_view", "restore_view", {}),
        ]
        for name, method, args in operations:
            for capture_after in (False, True):
                with self.subTest(name=name, capture_after=capture_after), \
                        patch.object(BridgeClient, method, side_effect=BridgeError("connection closed", transport_error=True)) as write, \
                        patch.object(BridgeClient, "capture_view") as capture:
                    result = self._call(name, {**args, "capture_after": capture_after})
                write.assert_called_once()
                capture.assert_not_called()
                self.assertTrue(result["isError"])
                metadata = result["structuredContent"]
                self.assertTrue(metadata["outcome_uncertain"])
                self.assertFalse(metadata["retry_safe"])
                self.assertIn("Do not automatically resend", metadata["error"])
                if capture_after:
                    self.assertEqual(metadata["receipt"]["capture"]["reason"], "operation_outcome_unknown")
                else:
                    self.assertNotIn("receipt", metadata)

    def test_preview_transport_failures_do_not_claim_a_possible_write(self) -> None:
        for preview_options in ({}, {"dry_run": True}):
            for capture_after in (False, True):
                with self.subTest(preview_options=preview_options, capture_after=capture_after), \
                        patch.object(BridgeClient, "create_panel", side_effect=BridgeError("connection closed", transport_error=True)) as preview, \
                        patch.object(BridgeClient, "capture_view") as capture:
                    result = self._call("e3d_create_panel", {
                        "parent_refno": "/PUMP", "vertices": [[0, 0], [10, 0], [0, 10]], "thickness": 1,
                        "capture_after": capture_after, **preview_options,
                    })
                preview.assert_called_once()
                capture.assert_not_called()
                self.assertTrue(result["isError"])
                metadata = result.get("structuredContent", {})
                self.assertNotIn("outcome_uncertain", metadata)
                self.assertNotIn("retry_safe", metadata)
                self.assertNotIn("may already have taken effect", json.dumps(result))
                if capture_after:
                    self.assertEqual(metadata["receipt"]["capture"]["reason"], "operation_failed")

    def test_native_read_transport_error_does_not_claim_an_uncertain_write(self) -> None:
        with patch.object(BridgeClient, "panel_geometry", side_effect=BridgeError("connection closed", transport_error=True)) as read:
            result = self._call("e3d_panel_geometry", {"refno": "/PANEL"})
        read.assert_called_once()
        self.assertTrue(result["isError"])
        self.assertNotIn("outcome_uncertain", json.dumps(result))
        self.assertNotIn("retry_safe", json.dumps(result))

    def test_failed_batch_retains_partial_results_and_skips_image(self) -> None:
        operation = {"ok": True, "results": [
            {"command": "NEW BOX", "success": True},
            {"command": "BOGUS", "success": False, "error": "bad command"},
        ]}
        with patch.object(BridgeClient, "command_batch", return_value=operation) as execute, \
                patch.object(BridgeClient, "capture_view") as screenshot:
            response = self._call("e3d_execute_pml_batch", {
                "commands": ["NEW BOX", "BOGUS", "END"], "dry_run": False, "capture_after": True,
            })
        execute.assert_called_once()
        screenshot.assert_not_called()
        self.assertTrue(response["isError"])
        self.assertEqual(response["structuredContent"]["results"], operation["results"])
        self.assertEqual(response["structuredContent"]["receipt"]["capture"]["reason"], "operation_failed")

    def test_receipt_never_implicitly_changes_drawlist_or_camera(self) -> None:
        with patch.object(BridgeClient, "delete_element", return_value=_verified_delete_payload()), \
                patch.object(BridgeClient, "capture_view", return_value=_capture_payload()), \
                patch.object(BridgeClient, "fit_view") as fit, patch.object(BridgeClient, "restore_view") as restore, \
                patch.object(BridgeClient, "set_drawlist_visual") as drawlist:
            response = self._call("e3d_delete_element", {"refno": "/PUMP", "dry_run": False, "capture_after": True})
        self.assertFalse(response.get("isError", False))
        fit.assert_not_called()
        restore.assert_not_called()
        drawlist.assert_not_called()

    def test_batch_captures_once_after_batch_and_preserves_results(self) -> None:
        events: list[str] = []

        def batch(commands: list[str], dry_run: bool, stop_on_error: bool):
            events.append("batch")
            return {"ok": True, "results": [
                {"command": item, "success": True} for item in commands
            ]}

        def capture(width: int, height: int):
            events.append("capture")
            return _capture_payload(width, height)

        with patch.object(BridgeClient, "command_batch", side_effect=batch) as execute, \
                patch.object(BridgeClient, "capture_view", side_effect=capture) as screenshot:
            response = self._call("e3d_execute_pml_batch", {
                "commands": ["NEW BOX", "END"], "dry_run": False, "capture_after": True,
            })
        self.assertEqual(events, ["batch", "capture"])
        execute.assert_called_once()
        screenshot.assert_called_once_with(1280, 960)
        self.assertEqual(len(response["structuredContent"]["results"]), 2)
        self.assertEqual(len([part for part in response["content"] if part["type"] == "image"]), 1)

    def test_fit_and_restore_capture_only_after_confirmed_state(self) -> None:
        with patch.object(BridgeClient, "fit_view", return_value=self._success()) as fit, \
                patch.object(BridgeClient, "capture_view", return_value=_capture_payload()) as screenshot:
            fitted = self._call("e3d_fit_view", {"capture_after": True})
        fit.assert_called_once()
        screenshot.assert_called_once_with(1280, 960)
        self.assertEqual(fitted["structuredContent"]["receipt"]["capture"]["success"], True)

        with patch.object(BridgeClient, "restore_view", return_value={"ok": True, "result": {"ready": True}}), \
                patch.object(BridgeClient, "capture_view", return_value=_capture_payload()) as screenshot:
            restored = self._call("e3d_restore_view", {"capture_after": True})
        screenshot.assert_called_once_with(1280, 960)
        self.assertTrue(restored["structuredContent"]["receipt"]["capture"]["success"])

    def test_mock_mode_never_fabricates_receipt_image(self) -> None:
        mock_server = create_server(Settings(mode="mock", allow_commands=True))
        arguments = {"name": "e3d_create_element", "arguments": {
            "parent_refno": "/PUMP", "element_type": "BOX", "dry_run": False,
            "expected_revision": "a" * 64, "capture_after": True,
        }}
        response = self._call_with_server(mock_server, arguments)
        self.assertFalse(response.get("isError", False), response)
        self.assertFalse(any(part["type"] == "image" for part in response["content"]))
        self.assertEqual(response["structuredContent"]["receipt"]["capture"]["status"], "failed")
        self.assertIn("running E3D bridge", response["structuredContent"]["receipt"]["capture"]["error"])

    def _call_with_server(self, server: McpServer, arguments: dict) -> dict:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": arguments})
        output = StringIO()
        with patch("sys.stdout", output):
            server.serve([request + "\n"])
        return json.loads(output.getvalue())["result"]

    def test_image_result_propagates_failed_operation_metadata(self) -> None:
        server = McpServer("test", "1")
        metadata = {"ok": True, "result": {"success": False}}
        server.add_tool("image", "Image", {"type": "object"},
                        lambda _: ImageToolResult(_png_base64(), "image/png", metadata))
        response = self._call_with_server(server, {"name": "image", "arguments": {}})
        self.assertTrue(response["isError"])
        self.assertEqual(response["structuredContent"], metadata)


if __name__ == "__main__":
    unittest.main()

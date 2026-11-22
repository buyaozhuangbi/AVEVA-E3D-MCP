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


class PanelToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(Settings(mode="bridge", allow_commands=True))
        self.base = {"parent_refno": "/EQUIPMENT", "vertices": [[0, 0], [100, 0], [0, 80]], "thickness": 4}

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
        return {"ok": True, "version": "E3D21", "result": {"success": True, "dry_run": False, **fields}}

    def test_tools_publish_explicit_geometry_preview_and_receipt_contracts(self) -> None:
        tools = {tool["name"]: tool for tool in self._rpc("tools/list", {})["tools"]}
        create = tools["e3d_create_panel"]
        self.assertFalse(create["annotations"]["readOnlyHint"])
        self.assertFalse(create["annotations"]["idempotentHint"])
        schema = create["inputSchema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), {"parent_refno", "vertices", "thickness"})
        self.assertEqual(schema["properties"]["vertices"]["minItems"], 3)
        self.assertEqual(schema["properties"]["vertices"]["maxItems"], 256)
        self.assertEqual(schema["properties"]["vertices"]["items"]["maxItems"], 2)
        self.assertTrue(schema["properties"]["dry_run"]["default"])
        self.assertEqual(schema["properties"]["wrt"]["default"], "OWNER")
        self.assertEqual(schema["properties"]["position"]["default"], [0, 0, 0])
        self.assertEqual(schema["properties"]["justification"]["enum"], ["positive", "centre", "negative"])
        self.assertEqual(schema["properties"]["justification"]["default"], "positive")
        self.assertEqual(schema["properties"]["orientation"]["default"], {
            "x_axis": [1, 0, 0], "y_axis": [0, 1, 0],
        })
        self.assertFalse(schema["properties"]["capture_after"]["default"])
        read = tools["e3d_panel_geometry"]
        self.assertTrue(read["annotations"]["readOnlyHint"])
        self.assertTrue(read["annotations"]["idempotentHint"])
        self.assertEqual(read["inputSchema"]["required"], ["refno"])
        self.assertEqual(set(read["inputSchema"]["properties"]), {"refno", "include_world", "include_boundary"})
        self.assertFalse(read["inputSchema"]["properties"]["include_world"]["default"])
        self.assertEqual(read["inputSchema"]["properties"]["include_boundary"]["type"], "boolean")
        self.assertFalse(read["inputSchema"]["properties"]["include_boundary"]["default"])

    def test_default_preview_sends_a_fully_defined_local_frame_without_write_permission(self) -> None:
        read_only = create_server(Settings(mode="bridge", allow_commands=False))
        with patch.object(BridgeClient, "create_panel", return_value=self._success(dry_run=True)) as create:
            result = self._call("e3d_create_panel", self.base, server=read_only)
        self.assertFalse(result.get("isError", False), result)
        create.assert_called_once_with("/EQUIPMENT", [[0.0, 0.0], [100.0, 0.0], [0.0, 80.0]], 4.0, "",
                                       [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], "OWNER", "positive", True)

    def test_explicit_transform_and_name_are_forwarded_in_one_native_call(self) -> None:
        args = {**self.base, "parent_refno": " =123/456 ", "name": "/PANEL-ONE", "position": [10, -20, 30],
                "orientation": {"x_axis": [0, 1, 0], "y_axis": [0, 0, 1]}, "wrt": " world ",
                "justification": " CENTRE ", "dry_run": False}
        with patch.object(BridgeClient, "create_panel", return_value=self._success()) as create, \
                patch.object(BridgeClient, "create_element") as primitive, patch.object(BridgeClient, "command") as command:
            response = self._call("e3d_create_panel", args)
        self.assertFalse(response.get("isError", False), response)
        create.assert_called_once_with("=123/456", self.base["vertices"], 4.0, "/PANEL-ONE",
                                       [10.0, -20.0, 30.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], "WORLD", "centre", False)
        primitive.assert_not_called()
        command.assert_not_called()

    def test_required_inputs_and_bad_references_are_rejected_before_bridge(self) -> None:
        invalid = [{}, {key: value for key, value in self.base.items() if key != "vertices"},
                   {key: value for key, value in self.base.items() if key != "thickness"}]
        invalid.extend({**self.base, "parent_refno": value} for value in (
            "", "  ", "CE", "OWNER", "EQUI", "/", "=1/2/3", "=x/2", "/PUMP\nDELETE CE", None, 1, True,
        ))
        invalid.extend({**self.base, "wrt": value} for value in ("", "CE", "=x/2", None, True))
        with patch.object(BridgeClient, "create_panel") as create:
            for args in invalid:
                with self.subTest(args=args):
                    self.assertTrue(self._call("e3d_create_panel", args)["isError"])
        create.assert_not_called()

    def test_nonfinite_wrong_shape_and_oversized_vertices_never_reach_native(self) -> None:
        invalid = [None, "triangle", [], [[0, 0], [1, 1]], [[0, 0]] * 257,
                   [[0, 0, 0], [1, 0], [0, 1]], [[0], [1, 0], [0, 1]],
                   [[0, 0], None, [0, 1]], [[0, 0], ["1", 0], [0, 1]],
                   [[0, 0], [True, 0], [0, 1]], [[0, 0], [None, 0], [0, 1]]]
        invalid.extend([[0, 0], [value, 0], [0, 1]] for value in (
            float("nan"), float("inf"), float("-inf"), 10 ** 1000,
        ))
        with patch.object(BridgeClient, "create_panel") as create:
            for vertices in invalid:
                with self.subTest(vertices=vertices):
                    self.assertTrue(self._call("e3d_create_panel", {**self.base, "vertices": vertices})["isError"])
        create.assert_not_called()

    def test_invalid_thickness_flags_names_and_transforms_never_reach_native(self) -> None:
        invalid = [{**self.base, "thickness": value} for value in (
            None, "4", True, False, [], 0, -1, float("nan"), float("inf"), float("-inf"), 10 ** 1000,
        )]
        invalid.extend({**self.base, "dry_run": value} for value in (None, "false", 0, 1))
        invalid.extend({**self.base, "justification": value} for value in (None, True, 1, "", "BOTT", "both", "center"))
        invalid.extend({**self.base, "name": value} for value in (None, True, 1, []))
        invalid.extend({**self.base, "position": value} for value in (None, [0, 0], [0, 0, True], [0, float("nan"), 0]))
        invalid.extend({**self.base, "orientation": value} for value in (
            None, {}, {"x_axis": [1, 0, 0]}, {"x_axis": [1, 0, 0], "y_axis": [1, 0, 0]},
            {"x_axis": [2, 0, 0], "y_axis": [0, 1, 0]},
            {"x_axis": [1, 0, 0], "y_axis": [0, 1, 0], "z_axis": [0, 0, 1]},
        ))
        with patch.object(BridgeClient, "create_panel") as create:
            for args in invalid:
                with self.subTest(args=args):
                    self.assertTrue(self._call("e3d_create_panel", args)["isError"])
        create.assert_not_called()

    def test_unknown_controls_are_rejected_before_creation_or_capture(self) -> None:
        with patch.object(BridgeClient, "create_panel") as create, patch.object(BridgeClient, "capture_view") as capture:
            for field in ("holes", "attributes", "command", "fit", "add", "x_axis", "vertex_limit"):
                response = self._call("e3d_create_panel", {**self.base, field: True, "dry_run": False, "capture_after": True})
                self.assertTrue(response["isError"])
                self.assertEqual(response["structuredContent"]["receipt"]["capture"]["reason"], "operation_failed")
        create.assert_not_called()
        capture.assert_not_called()

    def test_native_topology_validation_is_authoritative_and_diagnostics_are_preserved(self) -> None:
        invalid = {"ok": True, "result": {"success": False, "dry_run": True, "write_attempted": False,
                   "validation": {"valid": False, "issues": ["self_intersection"]}}}
        # Python validates transport shape; native code is responsible for the polygon proof.
        args = {**self.base, "vertices": [[0, 0], [10, 10], [0, 10], [10, 0]]}
        with patch.object(BridgeClient, "create_panel", return_value=invalid) as create:
            result = self._call("e3d_create_panel", args)
        create.assert_called_once()
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"], invalid)

    def test_execution_requires_permission_without_changing_preview_behavior(self) -> None:
        read_only = create_server(Settings(mode="bridge", allow_commands=False))
        with patch.object(BridgeClient, "create_panel", return_value=self._success()) as create:
            denied = self._call("e3d_create_panel", {**self.base, "dry_run": False}, server=read_only)
            self.assertTrue(denied["isError"])
            create.assert_not_called()
            executed = self._call("e3d_create_panel", {**self.base, "dry_run": False})
        self.assertFalse(executed.get("isError", False), executed)
        create.assert_called_once()
        self.assertFalse(create.call_args.args[-1])

    def test_explicit_inspection_works_without_commands_and_preserves_partial_and_findings(self) -> None:
        read_only = create_server(Settings(mode="bridge", allow_commands=False))
        cases = [
            {"ok": True, "result": {"success": True, "complete": True, "validation": {"valid": False, "issues": ["clockwise"]}}},
            {"ok": True, "result": {"success": False, "complete": False, "loops": [{"refno": "=1/3", "vertices": [[1, 2, 0]]}],
             "validation": {"valid": None}, "read_errors": {"vertex:2": "SDK failure"}}},
        ]
        for payload in cases:
            with self.subTest(payload=payload), patch.object(BridgeClient, "panel_geometry", return_value=payload) as read:
                result = self._call("e3d_panel_geometry", {"refno": " =1/2 "}, server=read_only)
            read.assert_called_once_with("=1/2")
            self.assertEqual(result["structuredContent"], payload)
            self.assertEqual(bool(result.get("isError")), not payload["result"]["success"])

    def test_inspection_never_uses_implicit_current_element_or_unknown_controls(self) -> None:
        invalid = [{}, {"refno": ""}, {"refno": "CE"}, {"refno": "/"}, {"refno": None}]
        invalid.extend({"refno": "/PANEL", key: value} for key, value in (
            ("capture_after", True), ("dry_run", True), ("wrt", "WORLD"), ("limit", 10),
        ))
        with patch.object(BridgeClient, "panel_geometry") as read, patch.object(BridgeClient, "command") as command:
            for args in invalid:
                with self.subTest(args=args):
                    self.assertTrue(self._call("e3d_panel_geometry", args)["isError"])
        read.assert_not_called()
        command.assert_not_called()

    def test_successful_create_is_captured_once_after_verified_readback(self) -> None:
        events = []
        operation = self._success(refno="=1/2", readback={"success": True, "loops": [{"vertices": [[0, 0, 0]]}]})

        def create(*_):
            events.append("create")
            return operation

        def capture(width, height):
            events.append("capture")
            return _capture_payload(width, height)

        with patch.object(BridgeClient, "create_panel", side_effect=create) as write, \
                patch.object(BridgeClient, "capture_view", side_effect=capture) as image, \
                patch.object(BridgeClient, "set_drawlist_visual") as membership, \
                patch.object(BridgeClient, "fit_view") as fit, patch.object(BridgeClient, "restore_view") as restore:
            result = self._call("e3d_create_panel", {**self.base, "dry_run": False,
                "capture_after": True, "capture_width": 64, "capture_height": 64})
        self.assertEqual(events, ["create", "capture"])
        write.assert_called_once()
        image.assert_called_once_with(64, 64)
        membership.assert_not_called()
        fit.assert_not_called()
        restore.assert_not_called()
        self.assertFalse(result.get("isError", False), result)
        self.assertEqual(result["structuredContent"]["result"], operation["result"])
        self.assertEqual(result["structuredContent"]["receipt"]["capture"]["status"], "captured")
        self.assertFalse(result["structuredContent"]["receipt"]["operation_replayed"])
        self.assertEqual(len([part for part in result["content"] if part["type"] == "image"]), 1)

    def test_preview_failure_and_transport_uncertainty_never_capture_or_retry_creation(self) -> None:
        cases = [
            ({}, self._success(dry_run=True), "dry_run", False),
            ({"dry_run": False}, {"ok": True, "result": {"success": False, "cleanup": {"success": False}, "residual_refno": "=1/2"}}, "operation_failed", True),
            ({"dry_run": False}, BridgeError("connection closed", transport_error=True), "operation_outcome_unknown", True),
        ]
        for options, outcome, reason, failed in cases:
            with self.subTest(reason=reason), patch.object(BridgeClient, "create_panel") as create, \
                    patch.object(BridgeClient, "capture_view") as capture:
                if isinstance(outcome, Exception):
                    create.side_effect = outcome
                else:
                    create.return_value = outcome
                result = self._call("e3d_create_panel", {**self.base, "capture_after": True, **options})
            create.assert_called_once()
            capture.assert_not_called()
            self.assertEqual(bool(result.get("isError")), failed)
            self.assertEqual(result["structuredContent"]["receipt"]["capture"]["reason"], reason)
            self.assertFalse(result["structuredContent"]["receipt"]["operation_replayed"])
            if reason == "operation_outcome_unknown":
                self.assertTrue(result["structuredContent"]["outcome_uncertain"])
                self.assertFalse(result["structuredContent"]["retry_safe"])
            if reason == "operation_failed":
                self.assertEqual(result["structuredContent"]["result"]["residual_refno"], "=1/2")

    def test_failed_capture_keeps_the_created_panel_success_and_does_not_repeat_creation(self) -> None:
        with patch.object(BridgeClient, "create_panel", return_value=self._success(refno="=1/2")) as create, \
                patch.object(BridgeClient, "capture_view", side_effect=BridgeError("graphics unavailable")) as capture:
            result = self._call("e3d_create_panel", {**self.base, "dry_run": False, "capture_after": True})
        create.assert_called_once()
        capture.assert_called_once()
        self.assertFalse(result.get("isError", False), result)
        self.assertEqual(result["structuredContent"]["result"]["refno"], "=1/2")
        self.assertEqual(result["structuredContent"]["receipt"]["capture"]["status"], "failed")

    def test_mock_requires_native_geometry_and_never_fabricates_a_panel_or_image(self) -> None:
        mock = create_server(Settings(mode="mock", allow_commands=True, version="E3D21"))
        read = self._call("e3d_panel_geometry", {"refno": "/PANEL"}, server=mock)
        self.assertTrue(read["isError"])
        self.assertFalse(read["structuredContent"]["result"]["available"])
        self.assertEqual(read["structuredContent"]["version"], "E3D21")
        preview = self._call("e3d_create_panel", {**self.base, "capture_after": True}, server=mock)
        self.assertFalse(preview.get("isError", False), preview)
        self.assertTrue(preview["structuredContent"]["result"]["mock"])
        self.assertFalse(preview["structuredContent"]["result"]["native_validated"])
        self.assertEqual(preview["structuredContent"]["receipt"]["capture"]["reason"], "dry_run")
        executed = self._call("e3d_create_panel", {**self.base, "dry_run": False, "capture_after": True}, server=mock)
        self.assertTrue(executed["isError"])
        self.assertFalse(executed["structuredContent"]["result"]["write_attempted"])
        self.assertFalse(executed["structuredContent"]["result"]["write_applied"])
        for result in (preview, executed):
            for native_field in ("refno", "loops", "readback"):
                self.assertNotIn(native_field, result["structuredContent"]["result"])
        for result in (read, preview, executed):
            self.assertFalse(any(part["type"] == "image" for part in result["content"]))

    def test_bridge_routes_preserve_selected_instance_and_exact_native_payload(self) -> None:
        client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.panel_geometry("=123/456", version="E3D21")
            request.assert_called_once_with("GET", "/api/v1/panel/geometry", query={"refno": "=123/456"}, version="E3D21")
            request.reset_mock()
            request.return_value = {"ok": True, "result": {"world_positions_requested": True}}
            client.panel_geometry("=123/456", version="E3D21", include_world=True)
            request.assert_called_once_with("GET", "/api/v1/panel/geometry",
                                            query={"refno": "=123/456", "include_world": "true"}, version="E3D21")
            request.reset_mock()
            request.return_value = {"ok": True, "result": {"complete": True}}
            with self.assertRaisesRegex(BridgeError, "does not support native WORLD"):
                client.panel_geometry("=123/456", version="E3D21", include_world=True)
            request.reset_mock()
            client.create_panel("=123/456", self.base["vertices"], 4.0, "/PANEL", [10, 20, 30],
                                [0, 1, 0], [0, 0, 1], "WORLD", "negative", False, version="E3D21")
            request.assert_called_once_with("POST", "/api/v1/panel/create", {
                "parent_refno": "=123/456", "vertices": self.base["vertices"], "thickness": 4.0, "name": "/PANEL",
                "position": [10, 20, 30], "x_axis": [0, 1, 0], "y_axis": [0, 0, 1], "wrt": "WORLD",
                "justification": "negative", "dry_run": False,
            }, version="E3D21")

    def test_include_world_requires_a_strict_boolean_and_is_forwarded(self) -> None:
        for invalid in (None, 0, 1, "true", [], {}):
            with self.subTest(invalid=invalid), patch.object(BridgeClient, "panel_geometry") as geometry:
                result = self._call("e3d_panel_geometry", {"refno": "/PANEL", "include_world": invalid})
                self.assertTrue(result["isError"])
                geometry.assert_not_called()
        with patch.object(BridgeClient, "panel_geometry", return_value={"ok": True}) as geometry:
            result = self._call("e3d_panel_geometry", {"refno": "/PANEL", "include_world": True})
        self.assertFalse(result.get("isError", False), result)
        geometry.assert_called_once_with("/PANEL", include_world=True)

    def test_create_transport_failure_is_one_request_without_command_recovery(self) -> None:
        client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        with patch.object(client, "_request", side_effect=BridgeError("request timed out", transport_error=True)) as request, \
                patch.object(client, "_recover_command_timeout") as recover, patch.object(client, "cancel_command") as cancel:
            with self.assertRaises(BridgeError):
                client.create_panel("/EQUIPMENT", self.base["vertices"], 4.0, "", [0, 0, 0],
                                    [1, 0, 0], [0, 1, 0], "OWNER", "positive", False)
        request.assert_called_once()
        recover.assert_not_called()
        cancel.assert_not_called()


if __name__ == "__main__":
    unittest.main()

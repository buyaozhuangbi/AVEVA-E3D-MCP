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


class PrimitiveUpdateToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(Settings(mode="bridge", allow_commands=True))
        self.base = {"refno": "/PRIMITIVE", "dimensions": {"DIAM": 120, "HEIG": 40}}
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
        return self._rpc("tools/call", {"name": "e3d_update_primitive", "arguments": arguments}, server=server)

    def _read(self, arguments: dict, *, server=None) -> dict:
        return self._rpc("tools/call", {"name": "e3d_primitive_geometry", "arguments": arguments}, server=server)

    @staticmethod
    def _success(**fields) -> dict:
        return {"ok": True, "version": "E3D21", "result": {"success": True, "dry_run": False, **fields}}

    def test_catalog_exposes_explicit_read_and_guarded_combined_update(self) -> None:
        tools = {tool["name"]: tool for tool in self._rpc("tools/list", {})["tools"]}
        self.assertEqual(len(tools), 119)
        read = tools["e3d_primitive_geometry"]
        self.assertEqual(read["annotations"], {
            "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False,
        })
        self.assertEqual(set(read["inputSchema"]["properties"]), {"refno"})
        self.assertEqual(read["inputSchema"]["required"], ["refno"])
        self.assertFalse(read["inputSchema"]["additionalProperties"])
        update = tools["e3d_update_primitive"]
        self.assertFalse(update["annotations"]["readOnlyHint"])
        self.assertFalse(update["annotations"]["idempotentHint"])
        self.assertTrue(update["annotations"]["destructiveHint"])
        schema = update["inputSchema"]
        self.assertEqual(schema["required"], ["refno"])
        self.assertEqual(schema["anyOf"], [{"required": ["dimensions"]}, {"required": ["position"]}, {"required": ["orientation"]}])
        self.assertFalse(schema["additionalProperties"])
        self.assertTrue(schema["properties"]["dry_run"]["default"])
        self.assertEqual(schema["properties"]["wrt"]["default"], "OWNER")
        self.assertFalse(schema["properties"]["capture_after"]["default"])
        dimensions = schema["properties"]["dimensions"]
        self.assertEqual((dimensions["minProperties"], dimensions["maxProperties"]), (1, 3))
        self.assertEqual(dimensions["additionalProperties"], {"type": "number", "minimum": 0})
        self.assertNotIn("default", dimensions)
        self.assertNotIn("default", schema["properties"]["expected_revision"])
        self.assertEqual(schema["properties"]["expected_revision"]["pattern"], "^[0-9a-fA-F]{64}$")
        for kind in ("CYLI", "NCYL", "BOX", "NBOX", "CONE", "NCON", "DISH"):
            self.assertIn(kind, update["description"])

    def test_read_without_permission_preserves_native_geometry_revision_and_children(self) -> None:
        read_only = create_server(Settings(mode="bridge", allow_commands=False))
        payload = {"ok": True, "result": {
            "success": True, "type": "CYLI", "revision": self.revision,
            "dimensions": {"DIAM": {"value": 120, "unit": "mm"}},
            "children": [{"refno": "=1/3", "type": "NCYL", "position": [1, 2, 3]}],
            "eligibility": {"supported": True}, "complete": True,
        }}
        with patch.object(BridgeClient, "primitive_geometry", return_value=payload) as read, \
                patch.object(BridgeClient, "update_primitive") as update, patch.object(BridgeClient, "capture_view") as capture:
            response = self._read({"refno": " =1/2 "}, server=read_only)
        read.assert_called_once_with("=1/2")
        update.assert_not_called()
        capture.assert_not_called()
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"], payload)

    def test_read_and_update_reject_implicit_or_malformed_target_references(self) -> None:
        invalid = [None, True, 1, [], "", " ", "CE", "OWNER", "WORLD", "/", "=x/2", "=1/2/3", "/PUMP\nDELETE CE"]
        with patch.object(BridgeClient, "primitive_geometry") as read, patch.object(BridgeClient, "update_primitive") as update:
            self.assertTrue(self._read({})["isError"])
            self.assertTrue(self._call({"dimensions": {"DIAM": 1}})["isError"])
            for refno in invalid:
                with self.subTest(refno=refno):
                    self.assertTrue(self._read({"refno": refno})["isError"])
                    self.assertTrue(self._call({**self.base, "refno": refno})["isError"])
        read.assert_not_called()
        update.assert_not_called()

    def test_read_strictly_rejects_write_receipt_and_other_unknown_controls(self) -> None:
        with patch.object(BridgeClient, "primitive_geometry") as read:
            for name, value in (("dry_run", True), ("capture_after", False), ("capture_width", 64),
                                ("capture_height", 64), ("dimensions", {"DIAM": 1}), ("wrt", "OWNER"),
                                ("expected_revision", self.revision), ("scope", "element")):
                with self.subTest(name=name):
                    self.assertTrue(self._read({"refno": "/PUMP", name: value})["isError"])
        read.assert_not_called()

    def test_read_partial_failures_preserve_actual_data_and_errors(self) -> None:
        payload = {"ok": True, "result": {
            "success": False, "complete": False, "dimensions": {"HEIG": 4}, "revision": None,
            "read_errors": {"DIAM": "native dimension unavailable"}, "children": [{"refno": "=1/3"}],
        }}
        with patch.object(BridgeClient, "primitive_geometry", return_value=payload):
            response = self._read({"refno": "/PUMP"})
        self.assertTrue(response["isError"])
        self.assertEqual(response["structuredContent"], payload)

    def test_read_transport_failure_does_not_claim_an_uncertain_write(self) -> None:
        with patch.object(BridgeClient, "primitive_geometry", side_effect=BridgeError("closed", transport_error=True)) as read:
            response = self._read({"refno": "/PUMP"})
        read.assert_called_once()
        self.assertTrue(response["isError"])
        for field in ("outcome_uncertain", "retry_safe", "receipt"):
            self.assertNotIn(field, json.dumps(response))

    def test_preview_omits_revision_and_preserves_unprovided_transform_options(self) -> None:
        read_only = create_server(Settings(mode="bridge", allow_commands=False))
        payload = self._success(dry_run=True, expected_revision=self.revision)
        with patch.object(BridgeClient, "update_primitive", return_value=payload) as update:
            response = self._call(self.base, server=read_only)
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"], payload)
        update.assert_called_once_with("/PRIMITIVE", dimensions={"DIAM": 120.0, "HEIG": 40.0}, wrt="OWNER", dry_run=True)

    def test_combined_update_normalizes_dimensions_frame_and_revision_in_one_write(self) -> None:
        orientation = {"x_axis": [0, 1, 0], "y_axis": [-1, 0, 0]}
        with patch.object(BridgeClient, "update_primitive", return_value=self._success()) as update, \
                patch.object(BridgeClient, "set_transform") as transform, patch.object(BridgeClient, "set_attribute") as attribute, \
                patch.object(BridgeClient, "command") as command, patch.object(BridgeClient, "primitive_geometry") as read:
            response = self._call({"refno": " =1/2 ", "dimensions": {" diam ": 80, "heig": 60},
                "position": [10, -20, 30.5], "orientation": orientation, "wrt": " world ",
                "expected_revision": self.revision.upper(), "dry_run": False})
        self.assertFalse(response.get("isError", False), response)
        update.assert_called_once_with("=1/2", dimensions={"DIAM": 80.0, "HEIG": 60.0}, position=[10.0, -20.0, 30.5],
            x_axis=[0.0, 1.0, 0.0], y_axis=[-1.0, 0.0, 0.0], wrt="WORLD", dry_run=False, expected_revision=self.revision)
        transform.assert_not_called()
        attribute.assert_not_called()
        command.assert_not_called()
        read.assert_not_called()

    def test_position_and_orientation_only_preserve_dimensions_with_explicit_frames(self) -> None:
        cases = [
            ({"position": [1, 2, 3]}, {"position": [1.0, 2.0, 3.0], "wrt": "OWNER"}),
            ({"orientation": {"x_axis": [1, 0, 0], "y_axis": [0, 1, 0]}, "wrt": " /FRAME "},
             {"x_axis": [1.0, 0.0, 0.0], "y_axis": [0.0, 1.0, 0.0], "wrt": "/FRAME"}),
            ({"position": [0, 0, 0], "wrt": "=2/3"}, {"position": [0.0, 0.0, 0.0], "wrt": "=2/3"}),
        ]
        with patch.object(BridgeClient, "update_primitive", return_value=self._success(dry_run=True)) as update:
            for arguments, expected in cases:
                with self.subTest(arguments=arguments):
                    response = self._call({"refno": "/PUMP", **arguments})
                    self.assertFalse(response.get("isError", False), response)
                    update.assert_called_with("/PUMP", dry_run=True, **expected)
        self.assertEqual(update.call_count, len(cases))

    def test_shape_specific_dimensions_and_zero_values_are_delegated_to_native(self) -> None:
        # Python validates the transport contract without inferring the referenced
        # shape. Native eligibility decides applicable fields and valid zero tips.
        cases = [{"DIAM": 0, "HEIG": 4}, {"XLEN": 1, "YLEN": 2, "ZLEN": 3},
                 {"DTOP": 0, "DBOT": 20, "HEIG": 10}, {"DIAM": 600, "HEIG": 150, "RADI": 0}]
        with patch.object(BridgeClient, "update_primitive", return_value=self._success(dry_run=True)) as update:
            for dimensions in cases:
                with self.subTest(dimensions=dimensions):
                    response = self._call({"refno": "/PUMP", "dimensions": dimensions})
                    self.assertFalse(response.get("isError", False), response)
                    self.assertEqual(update.call_args.kwargs["dimensions"], dimensions)
        self.assertEqual(update.call_count, len(cases))

    def test_invalid_dimensions_rejected_before_native_or_capture(self) -> None:
        invalid = [None, [], "DIAM 10", {}, {"DIAM": 1, "HEIG": 2, "DTOP": 3, "DBOT": 4},
                   {"BORE": 1}, {"": 1}, {"diam": 1, "DIAM": 2}, {"DIAM ": 1, " diam": 2},
                   {" radi ": 1, "RADI": 2}]
        invalid.extend({"DIAM": value} for value in (None, True, False, "10", [], {}, -1, float("nan"),
                                                      float("inf"), float("-inf"), 10 ** 400))
        with patch.object(BridgeClient, "update_primitive") as update, patch.object(BridgeClient, "capture_view") as capture:
            for dimensions in invalid:
                with self.subTest(dimensions=dimensions):
                    response = self._call({"refno": "/PUMP", "dimensions": dimensions, "capture_after": True})
                    self.assertTrue(response["isError"])
        update.assert_not_called()
        capture.assert_not_called()

    def test_missing_changes_and_unknown_update_fields_rejected_before_native(self) -> None:
        invalid = [{"refno": "/PUMP"}, {"refno": "/PUMP", "wrt": "WORLD"},
                   {"refno": "/PUMP", "expected_revision": self.revision}]
        invalid.extend({**self.base, name: value} for name, value in (
            ("name", "/NEW"), ("attributes", {"DIAM": "10"}), ("x_axis", [1, 0, 0]),
            ("y_axis", [0, 1, 0]), ("children", []), ("element_type", "CYLI"), ("allow_child_deletion", True)))
        with patch.object(BridgeClient, "update_primitive") as update:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self._call(arguments)["isError"])
        update.assert_not_called()

    def test_invalid_transform_vectors_and_frames_never_reach_native(self) -> None:
        invalid = [{"position": value} for value in (None, [], [1, 2], [True, 0, 0], [float("inf"), 0, 0], [0, "1", 2])]
        invalid.extend({"orientation": value} for value in (
            None, {}, {"x_axis": [1, 0, 0]}, {"x_axis": [1, 0, 0], "y_axis": [1, 0, 0]},
            {"x_axis": [2, 0, 0], "y_axis": [0, 1, 0]}, {"x_axis": [1, 0, 0], "y_axis": [0, float("nan"), 0]},
            {"x_axis": [1, 0, 0], "y_axis": [0, 1, 0], "z_axis": [0, 0, 1]}))
        invalid.extend({"wrt": value} for value in (None, True, 1, "", "CE", "=x/2", "/FRAME\nDELETE CE"))
        with patch.object(BridgeClient, "update_primitive") as update:
            for values in invalid:
                with self.subTest(values=values):
                    self.assertTrue(self._call({**self.base, **values})["isError"])
        update.assert_not_called()

    def test_execution_requires_explicit_revision_and_command_permission(self) -> None:
        read_only = create_server(Settings(mode="bridge", allow_commands=False))
        with patch.object(BridgeClient, "update_primitive") as update:
            missing = self._call({**self.base, "dry_run": False})
            denied = self._call({**self.base, "dry_run": False, "expected_revision": self.revision}, server=read_only)
        self.assertTrue(missing["isError"])
        self.assertIn("expected_revision", missing["content"][0]["text"])
        self.assertTrue(denied["isError"])
        update.assert_not_called()

    def test_invalid_revision_and_preview_flags_rejected_before_native(self) -> None:
        with patch.object(BridgeClient, "update_primitive") as update:
            for dry_run in (True, False):
                for revision in (None, True, 1, "", "a" * 63, "a" * 65, "g" * 64, " " + self.revision):
                    with self.subTest(dry_run=dry_run, revision=revision):
                        self.assertTrue(self._call({**self.base, "dry_run": dry_run, "expected_revision": revision})["isError"])
            for dry_run in (None, 0, 1, "false"):
                with self.subTest(dry_run=dry_run):
                    self.assertTrue(self._call({**self.base, "dry_run": dry_run})["isError"])
        update.assert_not_called()

    def test_preview_keeps_native_token_and_negative_child_findings_without_image(self) -> None:
        payload = self._success(dry_run=True, expected_revision=self.revision,
            retained_child_refs=["=1/3"], local_cuts_preserved=True, eligibility={"supported": True})
        with patch.object(BridgeClient, "update_primitive", return_value=payload) as update, \
                patch.object(BridgeClient, "capture_view") as capture:
            response = self._call({**self.base, "capture_after": True})
        update.assert_called_once()
        capture.assert_not_called()
        self.assertEqual(response["structuredContent"]["result"], payload["result"])
        self.assertEqual(response["structuredContent"]["receipt"]["capture"]["reason"], "dry_run")

    def test_stale_revision_and_partial_rollback_preserve_real_residual_without_replay(self) -> None:
        failures = [
            {"success": False, "revision_match": False, "write_attempted": False, "actual_revision": "b" * 64},
            {"success": False, "partial": True, "write_attempted": True, "write_applied": True,
             "rollback": {"attempted": True, "success": False, "errors": ["HEIG restore failed"]},
             "actual_residual": {"refno": "=1/2", "dimensions": {"DIAM": 120, "HEIG": 60}, "children": ["=1/3"]},
             "retry_safe": False},
        ]
        for fields in failures:
            with self.subTest(fields=fields), patch.object(BridgeClient, "update_primitive", return_value=self._success(**fields)) as update, \
                    patch.object(BridgeClient, "capture_view") as capture:
                response = self._call({**self.base, "expected_revision": self.revision, "dry_run": False, "capture_after": True})
            update.assert_called_once()
            capture.assert_not_called()
            self.assertTrue(response["isError"])
            self.assertEqual(response["structuredContent"]["result"], {"dry_run": False, **fields})
            self.assertEqual(response["structuredContent"]["receipt"]["capture"]["reason"], "operation_failed")

    def test_success_preserves_refs_and_local_cut_readback_then_captures_once(self) -> None:
        events = []
        payload = self._success(refno="=1/2", children=[{"refno": "=1/3", "type": "NCYL", "owner_position": [0, 0, 0]}],
            readback={"complete": True, "local_cuts_preserved": True}, revision="b" * 64)

        def update(*_, **__):
            events.append("update")
            return payload

        def capture(width, height):
            events.append("capture")
            return _capture_payload(width, height)

        with patch.object(BridgeClient, "update_primitive", side_effect=update) as write, \
                patch.object(BridgeClient, "capture_view", side_effect=capture) as image, \
                patch.object(BridgeClient, "set_drawlist_visual") as membership, patch.object(BridgeClient, "fit_view") as fit, \
                patch.object(BridgeClient, "restore_view") as restore:
            response = self._call({**self.base, "expected_revision": self.revision, "dry_run": False,
                                  "capture_after": True, "capture_width": 64, "capture_height": 64})
        self.assertEqual(events, ["update", "capture"])
        write.assert_called_once()
        image.assert_called_once_with(64, 64)
        membership.assert_not_called()
        fit.assert_not_called()
        restore.assert_not_called()
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"]["result"], payload["result"])
        self.assertEqual(response["structuredContent"]["receipt"]["capture"]["status"], "captured")
        self.assertFalse(response["structuredContent"]["receipt"]["operation_replayed"])
        self.assertEqual(sum(part["type"] == "image" for part in response["content"]), 1)

    def test_write_transport_uncertainty_is_explicit_with_and_without_capture(self) -> None:
        for capture_after in (False, True):
            with self.subTest(capture_after=capture_after), \
                    patch.object(BridgeClient, "update_primitive", side_effect=BridgeError("closed", transport_error=True)) as update, \
                    patch.object(BridgeClient, "capture_view") as capture:
                response = self._call({**self.base, "expected_revision": self.revision, "dry_run": False, "capture_after": capture_after})
            update.assert_called_once()
            capture.assert_not_called()
            self.assertTrue(response["isError"])
            self.assertTrue(response["structuredContent"]["outcome_uncertain"])
            self.assertFalse(response["structuredContent"]["retry_safe"])
            if capture_after:
                self.assertEqual(response["structuredContent"]["receipt"]["capture"]["reason"], "operation_outcome_unknown")
            else:
                self.assertNotIn("receipt", response["structuredContent"])

    def test_preview_transport_failure_never_claims_possible_mutation(self) -> None:
        for capture_after in (False, True):
            with self.subTest(capture_after=capture_after), \
                    patch.object(BridgeClient, "update_primitive", side_effect=BridgeError("closed", transport_error=True)) as update, \
                    patch.object(BridgeClient, "capture_view") as capture:
                response = self._call({**self.base, "capture_after": capture_after})
            update.assert_called_once()
            capture.assert_not_called()
            self.assertTrue(response["isError"])
            self.assertNotIn("outcome_uncertain", json.dumps(response))
            self.assertNotIn("retry_safe", json.dumps(response))

    def test_capture_failure_keeps_confirmed_update_without_repeating_it(self) -> None:
        with patch.object(BridgeClient, "update_primitive", return_value=self._success(refno="=1/2")) as update, \
                patch.object(BridgeClient, "capture_view", side_effect=BridgeError("no view")) as capture:
            response = self._call({**self.base, "expected_revision": self.revision, "dry_run": False, "capture_after": True})
        update.assert_called_once()
        capture.assert_called_once()
        self.assertFalse(response.get("isError", False), response)
        self.assertTrue(response["structuredContent"]["result"]["success"])
        self.assertEqual(response["structuredContent"]["receipt"]["capture"]["status"], "failed")

    def test_mock_read_preview_and_execution_report_unavailable_without_fabricated_native_state(self) -> None:
        mock = create_server(Settings(mode="mock", allow_commands=True, version="E3D21"))
        responses = [self._read({"refno": "/PUMP"}, server=mock),
                     self._call({**self.base, "capture_after": True}, server=mock),
                     self._call({**self.base, "expected_revision": self.revision, "dry_run": False, "capture_after": True}, server=mock)]
        for response in responses:
            self.assertTrue(response["isError"])
            self.assertEqual(response["structuredContent"]["version"], "E3D21")
            result = response["structuredContent"]["result"]
            self.assertFalse(result["success"])
            self.assertFalse(result["available"])
            self.assertEqual(result["source"], "mock")
            for name in ("revision", "readback", "children", "actual_residual", "rollback"):
                self.assertNotIn(name, result)
            self.assertFalse(any(part["type"] == "image" for part in response["content"]))
        for response in responses[1:]:
            self.assertFalse(response["structuredContent"]["result"]["native_validated"])
            self.assertFalse(response["structuredContent"]["result"]["write_attempted"])
            self.assertFalse(response["structuredContent"]["result"]["write_applied"])

    def test_bridge_get_and_post_use_same_route_and_selected_instance_without_null_options(self) -> None:
        client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.primitive_geometry("=1/2", version="E3D21")
            request.assert_called_once_with("GET", "/api/v1/element/primitive", query={"refno": "=1/2"}, version="E3D21")
            request.reset_mock()
            client.update_primitive("=1/2", dimensions={"DIAM": 30}, version="E3D21")
            request.assert_called_once_with("POST", "/api/v1/element/primitive", {
                "refno": "=1/2", "dimensions": {"DIAM": 30}, "wrt": "OWNER", "dry_run": True,
            }, version="E3D21")
            request.reset_mock()
            client.update_primitive("=1/2", dimensions={"DIAM": 30, "HEIG": 4}, position=[1, 2, 3],
                x_axis=[0, 1, 0], y_axis=[-1, 0, 0], wrt="WORLD", expected_revision=self.revision, dry_run=False, version="E3D21")
            request.assert_called_once_with("POST", "/api/v1/element/primitive", {
                "refno": "=1/2", "dimensions": {"DIAM": 30, "HEIG": 4}, "position": [1, 2, 3],
                "x_axis": [0, 1, 0], "y_axis": [-1, 0, 0], "wrt": "WORLD", "expected_revision": self.revision, "dry_run": False,
            }, version="E3D21")
            request.reset_mock()
            client.update_primitive("=1/2", position=[1, 2, 3])
            request.assert_called_once_with("POST", "/api/v1/element/primitive", {
                "refno": "=1/2", "position": [1, 2, 3], "wrt": "OWNER", "dry_run": True,
            }, version=None)

    def test_bridge_write_timeout_never_recovers_cancels_or_retries(self) -> None:
        client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        with patch.object(client, "_request", side_effect=BridgeError("timeout", transport_error=True)) as request, \
                patch.object(client, "_recover_command_timeout") as recover, patch.object(client, "cancel_command") as cancel:
            with self.assertRaises(BridgeError):
                client.update_primitive("/PUMP", dimensions={"DIAM": 20}, expected_revision=self.revision, dry_run=False)
        request.assert_called_once()
        recover.assert_not_called()
        cancel.assert_not_called()


if __name__ == "__main__":
    unittest.main()

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
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = (b"\x00" + bytes(width * 3)) * height
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
           + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    return {"ok": True, "result": {
        "success": True, "mime_type": "image/png", "image_base64": base64.b64encode(png).decode("ascii"),
        "width": width, "height": height, "source": "E3D View3D.GetBitmap", "drawlist_id": 17,
    }}


class CopyElementToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(Settings(mode="bridge", allow_commands=True))
        self.base = {"source_refno": "/SOURCE", "parent_refno": "/DESTINATION", "name": "/COPY"}
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
        return self._rpc("tools/call", {"name": "e3d_copy_element", "arguments": arguments}, server=server)

    @staticmethod
    def _success(**fields) -> dict:
        return {"ok": True, "version": "E3D21", "result": {"success": True, "dry_run": False, **fields}}

    def test_catalog_exposes_explicit_guarded_copy_and_omitted_transform_semantics(self) -> None:
        tools = {tool["name"]: tool for tool in self._rpc("tools/list", {})["tools"]}
        self.assertEqual(len(tools), 117)
        copy = tools["e3d_copy_element"]
        self.assertEqual(copy["annotations"], {
            "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False,
        })
        schema = copy["inputSchema"]
        self.assertEqual(schema["required"], ["source_refno", "parent_refno", "name"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["properties"]), {
            "source_refno", "parent_refno", "name", "position", "orientation", "wrt", "expected_revision", "dry_run",
            "capture_after", "capture_width", "capture_height",
        })
        self.assertTrue(schema["properties"]["dry_run"]["default"])
        self.assertEqual(schema["properties"]["wrt"]["default"], "OWNER")
        self.assertIn("destination parent", schema["properties"]["wrt"]["description"])
        self.assertFalse(schema["properties"]["capture_after"]["default"])
        self.assertEqual(schema["properties"]["name"]["maxLength"], 50)
        for name in ("source_refno", "parent_refno", "name", "position", "orientation", "expected_revision"):
            self.assertNotIn("default", schema["properties"][name])
        self.assertEqual(schema["properties"]["expected_revision"]["pattern"], "^[0-9a-fA-F]{64}$")
        self.assertIn("source WORLD", copy["description"])
        for kind in ("CYLI", "BOX", "CONE", "NCYL", "NBOX", "NCON", "SUBE", "PANE", "PLOO", "PAVE"):
            self.assertIn(kind, copy["description"])

    def test_preview_needs_no_write_permission_and_preserves_native_revision_and_mapping(self) -> None:
        read_only = create_server(Settings(mode="bridge", allow_commands=False))
        payload = self._success(dry_run=True, revision=self.revision, write_attempted=False,
            mapping=[{"source_refno": "=1/2", "name": "/COPY"}, {"source_refno": "=1/3", "name": "/COPY-C1"}],
            eligibility={"supported": True}, complete=True)
        with patch.object(BridgeClient, "copy_element", return_value=payload) as copy:
            response = self._call(self.base, server=read_only)
        copy.assert_called_once_with("/SOURCE", "/DESTINATION", "/COPY", wrt="OWNER", dry_run=True)
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"], payload)

    def test_execution_normalizes_references_frame_axes_and_revision_in_one_native_copy(self) -> None:
        with patch.object(BridgeClient, "copy_element", return_value=self._success()) as copy, \
                patch.object(BridgeClient, "create_element") as create, patch.object(BridgeClient, "set_transform") as transform, \
                patch.object(BridgeClient, "set_attribute") as attribute, patch.object(BridgeClient, "command") as command:
            response = self._call({"source_refno": " =1/2 ", "parent_refno": " /DESTINATION ", "name": "/COPY",
                "position": [10, -20, 30.5], "orientation": {"x_axis": [0, 1, 0], "y_axis": [-1, 0, 0]},
                "wrt": " world ", "expected_revision": self.revision.upper(), "dry_run": False})
        self.assertFalse(response.get("isError", False), response)
        copy.assert_called_once_with("=1/2", "/DESTINATION", "/COPY", position=[10.0, -20.0, 30.5],
            x_axis=[0.0, 1.0, 0.0], y_axis=[-1.0, 0.0, 0.0], wrt="WORLD", dry_run=False, expected_revision=self.revision)
        for operation in (create, transform, attribute, command):
            operation.assert_not_called()

    def test_optional_transform_components_remain_independently_absent(self) -> None:
        cases = [
            ({"position": [1, 2, 3]}, {"position": [1.0, 2.0, 3.0], "wrt": "OWNER"}),
            ({"orientation": {"x_axis": [1, 0, 0], "y_axis": [0, 1, 0]}, "wrt": " /FRAME "},
             {"x_axis": [1.0, 0.0, 0.0], "y_axis": [0.0, 1.0, 0.0], "wrt": "/FRAME"}),
            ({"wrt": "=2/3"}, {"wrt": "=2/3"}),
        ]
        with patch.object(BridgeClient, "copy_element", return_value=self._success(dry_run=True)) as copy:
            for arguments, expected in cases:
                with self.subTest(arguments=arguments):
                    response = self._call({**self.base, **arguments})
                    self.assertFalse(response.get("isError", False), response)
                    copy.assert_called_with("/SOURCE", "/DESTINATION", "/COPY", dry_run=True, **expected)
        self.assertEqual(copy.call_count, len(cases))

    def test_missing_or_implicit_source_and_destination_rejected_before_native(self) -> None:
        invalid = [None, True, 1, [], "", " ", "CE", "OWNER", "WORLD", "/", "=x/2", "=1/2/3", "/PUMP\nDELETE CE", "/PUMP\x80"]
        with patch.object(BridgeClient, "copy_element") as copy:
            for key in ("source_refno", "parent_refno"):
                missing = {name: value for name, value in self.base.items() if name != key}
                self.assertTrue(self._call(missing)["isError"])
                for value in invalid:
                    with self.subTest(key=key, value=value):
                        self.assertTrue(self._call({**self.base, key: value})["isError"])
        copy.assert_not_called()

    def test_required_name_rejects_implicit_database_refs_whitespace_and_controls(self) -> None:
        invalid = [None, True, 1, [], "", " ", "COPY", "CE", "=1/2", "/", " /COPY", "/COPY ",
                   "/COPY A", "/COPY\nDELETE CE", "/COPY\x00", "/COPY\x7f", "/COPY\x80"]
        with patch.object(BridgeClient, "copy_element") as copy:
            self.assertTrue(self._call({key: value for key, value in self.base.items() if key != "name"})["isError"])
            for name in invalid:
                with self.subTest(name=name):
                    self.assertTrue(self._call({**self.base, "name": name})["isError"])
        copy.assert_not_called()

    def test_name_length_matches_native_limit(self) -> None:
        with patch.object(BridgeClient, "copy_element", return_value=self._success(dry_run=True)) as copy:
            accepted = self._call({**self.base, "name": "/" + "A" * 49})
            rejected = self._call({**self.base, "name": "/" + "A" * 50})
        self.assertFalse(accepted.get("isError", False), accepted)
        self.assertTrue(rejected["isError"])
        self.assertIn("50", rejected["content"][0]["text"])
        copy.assert_called_once_with("/SOURCE", "/DESTINATION", "/" + "A" * 49, wrt="OWNER", dry_run=True)

    def test_named_descendant_length_rejection_remains_native_and_names_are_not_truncated(self) -> None:
        native = self._success(success=False, dry_run=True, write_attempted=False,
            error="Generated descendant name exceeds 50 characters including slash.")
        name = "/" + "A" * 44
        with patch.object(BridgeClient, "copy_element", return_value=native) as copy:
            response = self._call({**self.base, "name": name})
        self.assertTrue(response["isError"])
        self.assertEqual(response["structuredContent"], native)
        copy.assert_called_once_with("/SOURCE", "/DESTINATION", name, wrt="OWNER", dry_run=True)

    def test_unknown_controls_and_raw_axis_fields_never_reach_native(self) -> None:
        with patch.object(BridgeClient, "copy_element") as copy:
            for name, value in (("refno", "/SOURCE"), ("attributes", {}), ("element_type", "BOX"),
                                ("x_axis", [1, 0, 0]), ("y_axis", [0, 1, 0]), ("recursive", True),
                                ("add_to_drawlist", True), ("fit", True), ("preview", False)):
                with self.subTest(name=name):
                    self.assertTrue(self._call({**self.base, name: value})["isError"])
        copy.assert_not_called()

    def test_invalid_transform_vectors_or_frames_never_reach_native(self) -> None:
        invalid = [{"position": value} for value in (None, [], [1, 2], [True, 0, 0], [float("inf"), 0, 0], [0, "1", 2])]
        invalid.extend({"orientation": value} for value in (
            None, {}, {"x_axis": [1, 0, 0]}, {"x_axis": [1, 0, 0], "y_axis": [1, 0, 0]},
            {"x_axis": [2, 0, 0], "y_axis": [0, 1, 0]}, {"x_axis": [1, 0, 0], "y_axis": [0, float("nan"), 0]},
            {"x_axis": [1, 0, 0], "y_axis": [0, 1, 0], "z_axis": [0, 0, 1]}))
        invalid.extend({"wrt": value} for value in (None, True, 1, "", "CE", "=x/2", "/FRAME\nDELETE CE", "/FRAME\x80"))
        with patch.object(BridgeClient, "copy_element") as copy, patch.object(BridgeClient, "capture_view") as capture:
            for values in invalid:
                with self.subTest(values=values):
                    self.assertTrue(self._call({**self.base, **values, "capture_after": True})["isError"])
        copy.assert_not_called()
        capture.assert_not_called()

    def test_execution_requires_revision_and_command_permission(self) -> None:
        read_only = create_server(Settings(mode="bridge", allow_commands=False))
        with patch.object(BridgeClient, "copy_element") as copy:
            missing = self._call({**self.base, "dry_run": False})
            denied = self._call({**self.base, "dry_run": False, "expected_revision": self.revision}, server=read_only)
        self.assertTrue(missing["isError"])
        self.assertIn("expected_revision", missing["content"][0]["text"])
        self.assertTrue(denied["isError"])
        self.assertIn("E3D_MCP_ALLOW_COMMANDS", denied["content"][0]["text"])
        copy.assert_not_called()

    def test_invalid_revision_and_preview_flags_never_reach_native(self) -> None:
        with patch.object(BridgeClient, "copy_element") as copy:
            for dry_run in (True, False):
                for revision in (None, True, 1, "", "a" * 63, "a" * 65, "g" * 64, " " + self.revision):
                    with self.subTest(dry_run=dry_run, revision=revision):
                        self.assertTrue(self._call({**self.base, "dry_run": dry_run, "expected_revision": revision})["isError"])
            for dry_run in (None, 0, 1, "false"):
                with self.subTest(dry_run=dry_run):
                    self.assertTrue(self._call({**self.base, "dry_run": dry_run})["isError"])
        copy.assert_not_called()

    def test_preview_does_not_capture_even_if_native_omits_preview_flag(self) -> None:
        payload = {"ok": True, "result": {"success": True, "revision": self.revision, "mapping": []}}
        with patch.object(BridgeClient, "copy_element", return_value=payload) as copy, \
                patch.object(BridgeClient, "capture_view") as capture:
            response = self._call({**self.base, "capture_after": True})
        copy.assert_called_once()
        capture.assert_not_called()
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"]["result"], payload["result"])
        self.assertEqual(response["structuredContent"]["receipt"]["capture"]["reason"], "dry_run")

    def test_native_rejection_stale_revision_and_partial_cleanup_preserve_diagnostics(self) -> None:
        cases = [
            {"success": False, "eligible": False, "reasons": ["Rules are unsupported"], "write_attempted": False},
            {"success": False, "revision_match": False, "actual_revision": "b" * 64, "write_attempted": False},
            {"success": False, "partial": True, "write_attempted": True, "write_applied": True,
             "cleanup": {"attempted": True, "success": False}, "actual_residual": {"refno": "=1/8", "exists": True}},
        ]
        for fields in cases:
            payload = self._success(**fields)
            with self.subTest(fields=fields), patch.object(BridgeClient, "copy_element", return_value=payload) as copy, \
                    patch.object(BridgeClient, "capture_view") as capture:
                response = self._call({**self.base, "dry_run": False, "expected_revision": self.revision, "capture_after": True})
            copy.assert_called_once()
            capture.assert_not_called()
            self.assertTrue(response["isError"])
            self.assertEqual(response["structuredContent"]["result"], payload["result"])
            self.assertEqual(response["structuredContent"]["receipt"]["capture"]["reason"], "operation_failed")

    def test_confirmed_copy_returns_mapping_readback_and_one_native_image_without_view_changes(self) -> None:
        events = []
        payload = self._success(refno="=1/8", mapping=[{"source_refno": "=1/2", "refno": "=1/8"}],
                                readback={"complete": True}, source_unchanged=True)

        def copy(*args, **kwargs):
            events.append("copy")
            return payload

        def capture(width, height):
            events.append("capture")
            return _capture_payload(width, height)

        with patch.object(BridgeClient, "copy_element", side_effect=copy) as write, \
                patch.object(BridgeClient, "capture_view", side_effect=capture) as image, \
                patch.object(BridgeClient, "fit_view") as fit, patch.object(BridgeClient, "set_drawlist_visual") as drawlist, \
                patch.object(BridgeClient, "set_view") as view:
            response = self._call({**self.base, "dry_run": False, "expected_revision": self.revision,
                                  "capture_after": True, "capture_width": 64, "capture_height": 64})
        self.assertEqual(events, ["copy", "capture"])
        write.assert_called_once()
        image.assert_called_once_with(64, 64)
        for operation in (fit, drawlist, view):
            operation.assert_not_called()
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"]["result"], payload["result"])
        self.assertEqual([item["type"] for item in response["content"]], ["text", "image"])
        self.assertEqual(response["content"][1]["mimeType"], "image/png")
        self.assertEqual(response["structuredContent"]["receipt"]["capture"]["status"], "captured")
        self.assertFalse(response["structuredContent"]["receipt"]["operation_replayed"])
        self.assertNotIn("image_base64", json.dumps(response["structuredContent"]))

    def test_copy_transport_uncertainty_is_explicit_with_or_without_capture_and_never_replays(self) -> None:
        for capture_after in (False, True):
            with self.subTest(capture_after=capture_after), \
                    patch.object(BridgeClient, "copy_element", side_effect=BridgeError("closed", transport_error=True)) as copy, \
                    patch.object(BridgeClient, "capture_view") as capture:
                response = self._call({**self.base, "dry_run": False, "expected_revision": self.revision,
                                      "capture_after": capture_after})
            copy.assert_called_once()
            capture.assert_not_called()
            self.assertTrue(response["isError"])
            self.assertTrue(response["structuredContent"]["outcome_uncertain"])
            self.assertFalse(response["structuredContent"]["retry_safe"])
            if capture_after:
                self.assertEqual(response["structuredContent"]["receipt"]["capture"]["reason"], "operation_outcome_unknown")
            else:
                self.assertNotIn("receipt", response["structuredContent"])

    def test_preview_transport_failure_does_not_claim_possible_database_mutation(self) -> None:
        for capture_after in (False, True):
            with self.subTest(capture_after=capture_after), \
                    patch.object(BridgeClient, "copy_element", side_effect=BridgeError("closed", transport_error=True)) as copy, \
                    patch.object(BridgeClient, "capture_view") as capture:
                response = self._call({**self.base, "capture_after": capture_after})
            copy.assert_called_once()
            capture.assert_not_called()
            self.assertTrue(response["isError"])
            self.assertNotIn("outcome_uncertain", json.dumps(response))
            self.assertNotIn("retry_safe", json.dumps(response))

    def test_capture_failure_preserves_confirmed_copy_without_retry(self) -> None:
        payload = self._success(refno="=1/8", mapping=[{"source_refno": "=1/2", "refno": "=1/8"}])
        with patch.object(BridgeClient, "copy_element", return_value=payload) as copy, \
                patch.object(BridgeClient, "capture_view", side_effect=BridgeError("no view", transport_error=True)) as capture:
            response = self._call({**self.base, "dry_run": False, "expected_revision": self.revision, "capture_after": True})
        copy.assert_called_once()
        capture.assert_called_once()
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"]["result"], payload["result"])
        self.assertEqual(response["structuredContent"]["receipt"]["capture"]["status"], "failed")
        self.assertFalse(response["structuredContent"]["receipt"]["operation_replayed"])

    def test_mock_never_fabricates_native_eligibility_revision_copy_or_image(self) -> None:
        mock = create_server(Settings(mode="mock", allow_commands=True))
        with patch.object(BridgeClient, "_request") as request, patch.object(BridgeClient, "capture_view") as capture:
            responses = [self._call({**self.base, "capture_after": True}, server=mock),
                         self._call({**self.base, "dry_run": False, "expected_revision": self.revision,
                                     "capture_after": True}, server=mock)]
        request.assert_not_called()
        capture.assert_not_called()
        for response in responses:
            self.assertTrue(response["isError"])
            result = response["structuredContent"]["result"]
            self.assertTrue(result["mock"])
            for key in ("success", "available", "native_validated", "write_attempted", "write_applied"):
                self.assertFalse(result[key])
            for key in ("revision", "mapping", "readback", "actual_residual", "cleanup", "refno"):
                self.assertNotIn(key, result)
            self.assertEqual([item["type"] for item in response["content"]], ["text"])

    def test_bridge_uses_copy_route_selected_instance_and_omits_unprovided_values(self) -> None:
        client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.copy_element("=1/2", "=2/3", "/COPY", version="E3D21")
            request.assert_called_once_with("POST", "/api/v1/element/copy", {
                "source_refno": "=1/2", "parent_refno": "=2/3", "name": "/COPY", "wrt": "OWNER", "dry_run": True,
            }, version="E3D21")
            request.reset_mock()
            client.copy_element("=1/2", "=2/3", "/COPY", position=[1, 2, 3], x_axis=[0, 1, 0], y_axis=[-1, 0, 0],
                                wrt="WORLD", expected_revision=self.revision, dry_run=False)
            request.assert_called_once_with("POST", "/api/v1/element/copy", {
                "source_refno": "=1/2", "parent_refno": "=2/3", "name": "/COPY", "position": [1, 2, 3],
                "x_axis": [0, 1, 0], "y_axis": [-1, 0, 0], "wrt": "WORLD", "expected_revision": self.revision,
                "dry_run": False,
            }, version=None)

    def test_bridge_timeout_performs_one_http_attempt_without_command_recovery_or_cancellation(self) -> None:
        client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")) as request, \
                patch.object(client, "_recover_command_timeout") as recover, patch.object(client, "cancel_command") as cancel:
            with self.assertRaises(BridgeError) as caught:
                client.copy_element("/SOURCE", "/DESTINATION", "/COPY", expected_revision=self.revision, dry_run=False)
        self.assertTrue(caught.exception.transport_error)
        request.assert_called_once()
        sent = request.call_args.args[0]
        self.assertEqual(sent.get_method(), "POST")
        self.assertTrue(sent.full_url.endswith("/api/v1/element/copy"))
        self.assertFalse(json.loads(sent.data)["dry_run"])
        recover.assert_not_called()
        cancel.assert_not_called()


if __name__ == "__main__":
    unittest.main()

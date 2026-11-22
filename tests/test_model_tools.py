from __future__ import annotations

import base64
import copy
import json
import math
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


def _png_base64(width: int, height: int) -> str:
    """Create a valid black RGB PNG without optional imaging dependencies."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = (b"\x00" + bytes(width * 3)) * height
    image = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
             + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    return base64.b64encode(image).decode("ascii")


def _capture_payload(width: int = 1280, height: int = 960) -> dict:
    return {
        "ok": True,
        "version": "E3D21",
        "result": {
            "success": True,
            "mime_type": "image/png",
            "image_base64": _png_base64(width, height),
            "width": width,
            "height": height,
            "drawlist_id": 42,
            "source": "E3D View3D.GetBitmap",
        },
    }


class ModelToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(Settings(mode="mock", allow_commands=False))
        self.success = {"ok": True, "version": "E3D21", "result": {"success": True}}

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

    def test_new_tools_are_discoverable_with_preview_defaults(self) -> None:
        tools = {tool["name"]: tool for tool in self._rpc("tools/list", {})["tools"]}
        for name in ("e3d_create_element", "e3d_set_transform", "e3d_capture_view"):
            self.assertIn(name, tools)
        create = tools["e3d_create_element"]["inputSchema"]
        transform = tools["e3d_set_transform"]["inputSchema"]
        capture = tools["e3d_capture_view"]["inputSchema"]
        self.assertEqual(set(create["required"]), {"parent_refno", "element_type"})
        self.assertEqual(create["properties"]["expected_revision"]["pattern"], "^[0-9a-fA-F]{64}$")
        self.assertNotIn("expected_revision", create["required"])
        self.assertEqual(create["allOf"], [{
            "if": {"properties": {"dry_run": {"const": False}}, "required": ["dry_run"]},
            "then": {"required": ["expected_revision"]},
        }])
        self.assertIn("refno", transform["required"])
        self.assertEqual(transform["anyOf"], [{"required": ["position"]}, {"required": ["orientation"]}])
        self.assertEqual(create["properties"]["attributes"]["maxProperties"], 20)
        for definition in (create, transform):
            self.assertTrue(definition["properties"]["dry_run"]["default"])
            self.assertEqual(definition["properties"]["wrt"]["default"], "OWNER")
        for name, default in (("width", 1280), ("height", 960)):
            self.assertEqual(capture["properties"][name]["default"], default)
            self.assertEqual(capture["properties"][name]["minimum"], 64)
            self.assertEqual(capture["properties"][name]["maximum"], 2048)
        self.assertTrue(tools["e3d_capture_view"]["annotations"]["readOnlyHint"])
        self.assertFalse(tools["e3d_create_element"]["annotations"]["readOnlyHint"])
        self.assertFalse(tools["e3d_set_transform"]["annotations"]["readOnlyHint"])

    def test_create_defaults_to_preview_without_command_permission(self) -> None:
        with patch.object(BridgeClient, "create_element", return_value=self.success) as create:
            result = self._call("e3d_create_element", {"parent_refno": "/EQUIPMENT", "element_type": "CYLI"})
        self.assertFalse(result.get("isError", False), result)
        create.assert_called_once_with("/EQUIPMENT", "CYLI", "", {}, None, None, None, "OWNER", True)

    def test_create_forwards_named_attributes_and_explicit_transform(self) -> None:
        orientation = {"x_axis": [0, 1, 0], "y_axis": [-1, 0, 0]}
        attributes = {"HEIG": "150mm", "DIAM": "80mm", "DESC": "pump body"}
        arguments = {"parent_refno": "=123/456", "element_type": "CYLI", "name": "/PUMP-BODY",
                     "attributes": attributes, "position": [20, -30.5, 40],
                     "orientation": orientation, "wrt": "WORLD", "dry_run": True}
        with patch.object(BridgeClient, "create_element", return_value=self.success) as create:
            result = self._call("e3d_create_element", arguments)
        self.assertFalse(result.get("isError", False), result)
        create.assert_called_once_with("=123/456", "CYLI", "/PUMP-BODY", attributes,
                                       [20, -30.5, 40], [0, 1, 0], [-1, 0, 0], "WORLD", True)

    def test_create_preview_revision_is_optional_but_execution_requires_and_forwards_it(self) -> None:
        revision = "AB" * 32
        enabled = create_server(Settings(mode="bridge", allow_commands=True))
        with patch.object(BridgeClient, "create_element", return_value=self.success) as create:
            preview = self._call("e3d_create_element", {"parent_refno": "/PUMP", "element_type": "BOX"})
            self.assertFalse(preview.get("isError", False), preview)
            create.assert_called_once_with("/PUMP", "BOX", "", {}, None, None, None, "OWNER", True)
            missing = self._call("e3d_create_element", {
                "parent_refno": "/PUMP", "element_type": "BOX", "dry_run": False}, server=enabled)
            self.assertTrue(missing["isError"])
            create.assert_called_once()
            written = self._call("e3d_create_element", {
                "parent_refno": "/PUMP", "element_type": "BOX", "dry_run": False,
                "expected_revision": revision}, server=enabled)
        self.assertFalse(written.get("isError", False), written)
        create.assert_called_with("/PUMP", "BOX", "", {}, None, None, None, "OWNER", False,
                                  expected_revision=revision.lower())

    def test_create_missing_execution_revision_is_rejected_before_handler_or_native(self) -> None:
        with patch("aveva_e3d_mcp.tools._create_element") as handler, \
                patch.object(BridgeClient, "create_element") as create, \
                patch.object(BridgeClient, "capture_view") as capture:
            enabled = create_server(Settings(mode="bridge", allow_commands=True))
            response = self._call("e3d_create_element", {
                "parent_refno": "/PUMP", "element_type": "BOX", "dry_run": False,
                "capture_after": True,
            }, server=enabled)
        self.assertTrue(response["isError"])
        handler.assert_not_called()
        create.assert_not_called()
        capture.assert_not_called()

    def test_create_rejects_malformed_revision_before_native_call(self) -> None:
        enabled = create_server(Settings(mode="bridge", allow_commands=True))
        with patch.object(BridgeClient, "create_element") as create, patch.object(BridgeClient, "capture_view") as capture:
            for revision in (None, True, 7, [], {}, "", "a" * 63, "a" * 65, "g" * 64, "a" * 64 + "\n"):
                for dry_run in (True, False):
                    with self.subTest(revision=revision, dry_run=dry_run):
                        response = self._call("e3d_create_element", {
                            "parent_refno": "/PUMP", "element_type": "BOX", "expected_revision": revision,
                            "dry_run": dry_run, "capture_after": True,
                        }, server=enabled)
                        self.assertTrue(response["isError"])
            create.assert_not_called()

    def test_create_bridge_payload_forwards_revision_and_preserves_positional_version(self) -> None:
        bridge = BridgeClient(Settings(mode="bridge", allow_commands=True))
        payload = {"ok": True, "version": "E3D21", "result": {"success": True, "revision": "a" * 64}}
        with patch.object(bridge, "_request", return_value=payload) as request:
            actual = bridge.create_element("/PUMP", "BOX", "/CHILD", {"XLEN": "10"},
                [1, 2, 3], None, None, "OWNER", False, "E3D21", expected_revision="a" * 64)
        self.assertEqual(actual, payload)
        request.assert_called_once_with("POST", "/api/v1/element/create", {
            "parent_refno": "/PUMP", "element_type": "BOX", "name": "/CHILD", "attributes": {"XLEN": "10"},
            "position": [1, 2, 3], "x_axis": None, "y_axis": None, "wrt": "OWNER", "dry_run": False,
            "expected_revision": "a" * 64,
        }, version="E3D21")
        with patch.object(bridge, "_request", return_value=payload) as request:
            bridge.create_element("/PUMP", "BOX", "", {}, None, None, None, "OWNER", True)
        self.assertNotIn("expected_revision", request.call_args.args[2])

    def test_create_accepts_twenty_attributes_and_rejects_more(self) -> None:
        base = {"parent_refno": "/PUMP", "element_type": "CYLI"}
        with patch.object(BridgeClient, "create_element", return_value=self.success) as create:
            attributes = {f":A{index}": str(index) for index in range(20)}
            result = self._call("e3d_create_element", {**base, "attributes": attributes})
            self.assertFalse(result.get("isError", False), result)
            self.assertEqual(create.call_args.args[3], attributes)
            result = self._call("e3d_create_element", {**base, "attributes": {**attributes, ":EXTRA": "21"}})
            self.assertTrue(result["isError"])
            self.assertEqual(create.call_count, 1)

    def test_invalid_create_identity_or_attributes_never_reach_bridge(self) -> None:
        base = {"parent_refno": "/PUMP", "element_type": "CYLI"}
        invalid = [{}, {"parent_refno": "/PUMP"}, {"element_type": "CYLI"}]
        for field in ("parent_refno", "element_type"):
            invalid.extend({**base, field: value} for value in ("", "  ", None, 1, True))
        invalid.extend({**base, "name": value} for value in (None, 1, True, []))
        invalid.extend({**base, "attributes": value} for value in (
            None, [], "HEIG 10", {"HEIG": 10}, {"HEIG": True}, {"DESC": None}, {"": "value"},
        ))
        with patch.object(BridgeClient, "create_element", return_value=self.success) as create:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self._call("e3d_create_element", arguments)["isError"])
            create.assert_not_called()

    def test_transform_accepts_position_orientation_or_both_in_explicit_frames(self) -> None:
        orientation = {"x_axis": [0, 1, 0], "y_axis": [0, 0, 1]}
        cases = [
            ({"position": [5, 10, -15]}, ([5, 10, -15], None, None, "OWNER")),
            ({"orientation": orientation, "wrt": "WORLD"}, (None, [0, 1, 0], [0, 0, 1], "WORLD")),
            ({"position": [0, 0, 0], "orientation": orientation, "wrt": "/PUMP"},
             ([0, 0, 0], [0, 1, 0], [0, 0, 1], "/PUMP")),
            ({"position": [1, 2, 3], "wrt": "=123/456"}, ([1, 2, 3], None, None, "=123/456")),
        ]
        with patch.object(BridgeClient, "set_transform", return_value=self.success) as transform:
            for arguments, expected in cases:
                with self.subTest(arguments=arguments):
                    result = self._call("e3d_set_transform", {"refno": "=3/4", **arguments})
                    self.assertFalse(result.get("isError", False), result)
                    transform.assert_called_with("=3/4", *expected, True)
            self.assertEqual(transform.call_count, len(cases))

    def test_transform_requires_identity_and_at_least_one_transform(self) -> None:
        invalid = [{}, {"refno": "/PUMP"}, {"position": [0, 0, 0]}]
        invalid.extend({"refno": value, "position": [0, 0, 0]} for value in ("", "  ", None, 1, True))
        with patch.object(BridgeClient, "set_transform", return_value=self.success) as transform:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self._call("e3d_set_transform", arguments)["isError"])
            transform.assert_not_called()

    def test_transform_schema_rejects_missing_transform_before_handler(self) -> None:
        with patch("aveva_e3d_mcp.tools._set_transform") as handler:
            server = create_server(Settings(mode="bridge", allow_commands=True))
            response = self._call("e3d_set_transform", {"refno": "/PUMP"}, server=server)
        self.assertTrue(response["isError"])
        self.assertEqual(response["content"], [{"type": "text", "text": "Invalid e3d_set_transform arguments"}])
        handler.assert_not_called()

    def test_nonfinite_malformed_and_boolean_positions_are_rejected_before_bridge(self) -> None:
        invalid = [None, [], [1, 2], [1, 2, 3, 4], "1,2,3", {"x": 1, "y": 2, "z": 3},
                   [True, 0, 0], [0, False, 0], [0, 0, "3"], [None, 0, 0],
                   [float("nan"), 0, 0], [0, float("inf"), 0], [0, 0, float("-inf")]]
        for tool, method, identity in (
            ("e3d_create_element", "create_element", {"parent_refno": "/PUMP", "element_type": "CYLI"}),
            ("e3d_set_transform", "set_transform", {"refno": "/PUMP"}),
        ):
            with patch.object(BridgeClient, method, return_value=self.success) as edit:
                for position in invalid:
                    with self.subTest(tool=tool, position=position):
                        self.assertTrue(self._call(tool, {**identity, "position": position})["isError"])
                edit.assert_not_called()

    def test_invalid_orientation_axes_are_rejected_before_bridge(self) -> None:
        invalid = [
            None, [], {}, {"x_axis": [1, 0, 0]},
            {"x_axis": [1, 0, 0], "y_axis": [0, 1, 0], "z_axis": [0, 0, 1]},
            {"x_axis": [1, 0], "y_axis": [0, 1, 0]},
            {"x_axis": [True, 0, 0], "y_axis": [0, 1, 0]},
            {"x_axis": [1, 0, 0], "y_axis": [0, float("nan"), 0]},
            {"x_axis": [1, 0, 0], "y_axis": [0, float("inf"), 0]},
            {"x_axis": [0, 0, 0], "y_axis": [0, 1, 0]},
            {"x_axis": [2, 0, 0], "y_axis": [0, 1, 0]},
            {"x_axis": [1, 0, 0], "y_axis": [1, 0, 0]},
            {"x_axis": [1, 0, 0], "y_axis": [2e-6, math.sqrt(1 - (2e-6) ** 2), 0]},
            {"x_axis": [1 + 2e-6, 0, 0], "y_axis": [0, 1, 0]},
        ]
        for tool, method, identity in (
            ("e3d_create_element", "create_element", {"parent_refno": "/PUMP", "element_type": "CYLI"}),
            ("e3d_set_transform", "set_transform", {"refno": "/PUMP"}),
        ):
            with patch.object(BridgeClient, method, return_value=self.success) as edit:
                for orientation in invalid:
                    with self.subTest(tool=tool, orientation=orientation):
                        self.assertTrue(self._call(tool, {**identity, "orientation": orientation})["isError"])
                edit.assert_not_called()

    def test_unit_orthogonal_axes_allow_rounding_within_one_millionth(self) -> None:
        orientation = {"x_axis": [1 + 5e-7, 0, 0], "y_axis": [5e-7, math.sqrt(1 - (5e-7) ** 2), 0]}
        with patch.object(BridgeClient, "set_transform", return_value=self.success) as transform:
            result = self._call("e3d_set_transform", {"refno": "/PUMP", "orientation": orientation})
        self.assertFalse(result.get("isError", False), result)
        transform.assert_called_once_with("/PUMP", None, orientation["x_axis"], orientation["y_axis"], "OWNER", True)

    def test_real_writes_require_permission_and_explicit_false_preview_flag(self) -> None:
        enabled = create_server(Settings(mode="mock", allow_commands=True))
        for tool, method, arguments in (
            ("e3d_create_element", "create_element", {"parent_refno": "/PUMP", "element_type": "CYLI"}),
            ("e3d_set_transform", "set_transform", {"refno": "/PUMP", "position": [0, 0, 0]}),
        ):
            with self.subTest(tool=tool), patch.object(BridgeClient, method, return_value=self.success) as edit:
                denied = self._call(tool, {**arguments, "dry_run": False})
                self.assertTrue(denied["isError"])
                edit.assert_not_called()
                preview = self._call(tool, arguments, server=enabled)
                self.assertFalse(preview.get("isError", False), preview)
                self.assertIs(edit.call_args.args[-1], True)
                write_arguments = {**arguments, "dry_run": False}
                if tool == "e3d_create_element":
                    write_arguments["expected_revision"] = "a" * 64
                written = self._call(tool, write_arguments, server=enabled)
                self.assertFalse(written.get("isError", False), written)
                self.assertIs(edit.call_args.args[-1], False)
                if tool == "e3d_create_element":
                    self.assertEqual(edit.call_args.kwargs, {"expected_revision": "a" * 64})
                self.assertEqual(edit.call_count, 2)

    def test_invalid_preview_flags_and_frames_are_rejected_before_bridge(self) -> None:
        for tool, method, identity in (
            ("e3d_create_element", "create_element", {"parent_refno": "/PUMP", "element_type": "CYLI"}),
            ("e3d_set_transform", "set_transform", {"refno": "/PUMP", "position": [0, 0, 0]}),
        ):
            with patch.object(BridgeClient, method, return_value=self.success) as edit:
                for field, values in (("dry_run", (None, 0, 1, "false")), ("wrt", ("", "  ", None, 1, True))):
                    for value in values:
                        with self.subTest(tool=tool, field=field, value=value):
                            self.assertTrue(self._call(tool, {**identity, field: value})["isError"])
                edit.assert_not_called()

    def test_model_operation_failures_remain_mcp_errors_with_diagnostics(self) -> None:
        payload = {"ok": True, "version": "E3D21", "result": {"success": False,
                   "error": "SDK initialization failed", "cleanup_attempted": True, "residual_refno": "=1/2"}}
        for tool, method, arguments in (
            ("e3d_create_element", "create_element", {"parent_refno": "/PUMP", "element_type": "CYLI"}),
            ("e3d_set_transform", "set_transform", {"refno": "/PUMP", "position": [0, 0, 0]}),
        ):
            with self.subTest(tool=tool), patch.object(BridgeClient, method, return_value=payload):
                result = self._call(tool, arguments)
                self.assertTrue(result["isError"])
                self.assertEqual(result["structuredContent"], payload)

    def test_capture_returns_image_and_metadata_without_duplicating_base64(self) -> None:
        payload = _capture_payload()
        original = copy.deepcopy(payload)
        with patch.object(BridgeClient, "capture_view", return_value=payload) as capture:
            result = self._call("e3d_capture_view", {})
        capture.assert_called_once_with(1280, 960)
        self.assertFalse(result.get("isError", False), result)
        images = [block for block in result["content"] if block["type"] == "image"]
        self.assertEqual(images, [{"type": "image", "mimeType": "image/png", "data": original["result"]["image_base64"]}])
        expected = copy.deepcopy(original)
        del expected["result"]["image_base64"]
        self.assertEqual(result["structuredContent"], expected)
        texts = [block["text"] for block in result["content"] if block["type"] == "text"]
        self.assertTrue(texts)
        self.assertEqual(json.loads(texts[0]), expected)
        for metadata in (result["structuredContent"], texts):
            serialized = json.dumps(metadata)
            self.assertNotIn("image_base64", serialized)
            self.assertNotIn(original["result"]["image_base64"], serialized)
        self.assertEqual(payload, original, "Packaging an image must not consume or mutate the bridge response")

    def test_capture_accepts_both_dimension_boundaries_without_write_permission(self) -> None:
        for width, height in ((64, 64), (2048, 64), (64, 2048), (2048, 2048)):
            with self.subTest(width=width, height=height):
                with patch.object(BridgeClient, "capture_view", return_value=_capture_payload(width, height)) as capture:
                    result = self._call("e3d_capture_view", {"width": width, "height": height})
                self.assertFalse(result.get("isError", False), result)
                capture.assert_called_once_with(width, height)

    def test_invalid_capture_dimensions_are_rejected_before_bridge(self) -> None:
        with patch.object(BridgeClient, "capture_view", return_value=self.success) as capture:
            for field in ("width", "height"):
                for value in (True, False, None, 0, 63, 2049, 1280.0, "1280", [], float("inf")):
                    with self.subTest(field=field, value=value):
                        result = self._call("e3d_capture_view", {field: value})
                        self.assertTrue(result["isError"])
            capture.assert_not_called()

    def test_missing_failed_or_invalid_capture_never_returns_image_content(self) -> None:
        good = _capture_payload(64, 64)
        payloads = [
            {"ok": False, "error": "graphics unavailable"},
            {"ok": True, "version": "E3D21", "result": {"success": False, "error": "no current view"}},
            {"ok": True, "version": "E3D21", "result": {"success": True}},
        ]
        for field, value in (("image_base64", None), ("image_base64", ""), ("image_base64", "not base64!"),
                             ("image_base64", base64.b64encode(b"not a PNG").decode("ascii")),
                             ("mime_type", "image/jpeg"), ("width", 65), ("height", 65)):
            payload = copy.deepcopy(good)
            payload["result"][field] = value
            payloads.append(payload)
        for payload in payloads:
            with self.subTest(payload=payload), patch.object(BridgeClient, "capture_view", return_value=payload):
                result = self._call("e3d_capture_view", {})
                self.assertTrue(result["isError"])
                self.assertTrue(result["content"])
                self.assertTrue(all(block["type"] == "text" for block in result["content"]))

    def test_capture_bridge_exception_is_reported_as_tool_error(self) -> None:
        with patch.object(BridgeClient, "capture_view", side_effect=BridgeError("HTTP 409: E3D is busy")):
            result = self._call("e3d_capture_view", {})
        self.assertTrue(result["isError"])
        self.assertIn("E3D is busy", result["content"][0]["text"])
        self.assertTrue(all(block["type"] == "text" for block in result["content"]))


if __name__ == "__main__":
    unittest.main()

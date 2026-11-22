from __future__ import annotations

from copy import deepcopy
from contextlib import ExitStack
from io import StringIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class ConnectionPairToolTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(mode="bridge", version="E3D21", allow_commands=False)
        self.client = BridgeClient(self.settings)
        self.server = create_server(self.settings)

    def rpc(self, method, params, *, server=None):
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        output = StringIO()
        with patch("sys.stdout", output):
            (server or self.server).serve([request + "\n"])
        response = json.loads(output.getvalue())
        self.assertNotIn("error", response, response)
        return response["result"]

    def call(self, arguments, *, server=None):
        return self.rpc("tools/call", {"name": "e3d_check_connection_pair", "arguments": arguments}, server=server)

    @staticmethod
    def arguments(**changes):
        return {"first_refno": "=1/2", "first_point_number": 1,
                "second_refno": "=1/3", "second_point_number": 2,
                "tolerance_mm": 0.25, **changes}

    @staticmethod
    def payload(**changes):
        return {"ok": True, "version": "E3D21", "result": {
            "success": True, "complete": False, "source": "Single E3D21 native UI-thread dispatch",
            "position_comparison": {"status": "unknown", "available": False},
            "direction_relationship": {"status": "unknown", "available": False},
            "pcref_correspondence": {"status": "unknown"}, **changes,
        }}

    def test_discovery_exposes_explicit_read_only_pair_check_contract(self):
        tools = {item["name"]: item for item in self.rpc("tools/list", {})["tools"]}
        tool = tools["e3d_check_connection_pair"]
        self.assertEqual(tool["annotations"], {
            "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False,
        })
        schema = tool["inputSchema"]
        self.assertEqual(set(schema["properties"]), {
            "first_refno", "first_expected_type", "first_point_number", "second_refno",
            "second_expected_type", "second_point_number", "tolerance_mm",
        })
        self.assertEqual(set(schema["required"]), {
            "first_refno", "first_point_number", "second_refno", "second_point_number", "tolerance_mm",
        })
        self.assertEqual(schema["properties"]["first_point_number"], {"type": "integer", "minimum": 1, "maximum": 10000})
        self.assertEqual(schema["properties"]["tolerance_mm"]["minimum"], 0)
        self.assertNotIn("default", schema["properties"]["tolerance_mm"])
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("one native E3D21 UI-thread dispatch", tool["description"])
        self.assertIn("not a dimensional", tool["description"])
        self.assertIn("descriptive plus/minus 0.999 dot threshold", tool["description"])
        self.assertIn("Use e3d_check_connection_pair", self.server.instructions)

    def test_optional_expected_types_are_normalized_and_forwarded(self):
        payload = self.payload()
        arguments = self.arguments(first_expected_type=" bran ", second_expected_type="tubi")
        with patch.object(BridgeClient, "connection_pair_check", return_value=payload) as read:
            response = self.call(arguments)
        self.assertFalse(response.get("isError", False), response)
        read.assert_called_once_with("=1/2", 1, "=1/3", 2, 0.25,
                                     first_expected_type="BRAN", second_expected_type="TUBI")

    def test_expected_type_validation_happens_before_native_call(self):
        for key, value in (("first_expected_type", 7), ("second_expected_type", "BR\nAN"),
                           ("first_expected_type", "" + "A" * 65)):
            with self.subTest(key=key, value=value), patch.object(BridgeClient, "connection_pair_check") as read:
                response = self.call(self.arguments(**{key: value}))
            self.assertTrue(response["isError"])
            read.assert_not_called()

    def test_strict_inputs_are_validated_before_native_call(self):
        invalid = [
            {},
            {"first_refno": "/A", "first_point_number": 1, "second_refno": "/B", "second_point_number": 2},
            self.arguments(first_refno="CE"), self.arguments(second_refno="/B\nDELETE CE"),
            self.arguments(first_point_number=True), self.arguments(second_point_number=1.0),
            self.arguments(first_point_number=0), self.arguments(second_point_number=10001),
            self.arguments(tolerance_mm=True), self.arguments(tolerance_mm=float("nan")),
            self.arguments(tolerance_mm=float("inf")), self.arguments(tolerance_mm=-0.1),
            self.arguments(tolerance_mm=1000001), self.arguments(tolerance_mm=10 ** 400),
            self.arguments(write=True),
        ]
        with patch.object(BridgeClient, "connection_pair_check") as read:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self.call(arguments)["isError"])
        read.assert_not_called()

    def test_valid_explicit_pair_and_zero_tolerance_are_forwarded(self):
        payload = self.payload()
        for arguments, expected in [
            (self.arguments(), ("=1/2", 1, "=1/3", 2, 0.25)),
            (self.arguments(first_refno=" /A ", second_refno=" /B ", tolerance_mm=0), ("/A", 1, "/B", 2, 0.0)),
        ]:
            with self.subTest(arguments=arguments), patch.object(BridgeClient, "connection_pair_check", return_value=payload) as read:
                response = self.call(arguments)
            self.assertFalse(response.get("isError", False), response)
            read.assert_called_once_with(*expected)
            self.assertEqual(response["structuredContent"], payload)

    def test_partial_native_unknowns_are_preserved_without_inference(self):
        payload = self.payload(complete=False, position_comparison={"status": "unknown", "distance_mm": None},
                               pcref_correspondence={"status": "unknown"},
                               endpoints={"first": {"status": "exists", "complete": False,
                                                    "point": {"bore": {"available": False, "value_mm": None},
                                                              "connection_reference": {"resolution_complete": False}}},
                                         "second": {"status": "unknown", "complete": False}})
        original = deepcopy(payload)
        with patch.object(BridgeClient, "connection_pair_check", return_value=payload):
            response = self.call(self.arguments())
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"], original)
        self.assertEqual(json.loads(response["content"][0]["text"]), original)
        self.assertEqual(payload, original)
        serialized = json.dumps(response["structuredContent"])
        for forbidden in ("engineering_compatible", "connected_refno"):
            self.assertNotIn(forbidden, serialized)

    def test_bridge_posts_one_request_and_preserves_partial_result(self):
        payload = self.payload(extra={"raw": [None, "unset"]})
        with patch.object(self.client, "_request", return_value=payload) as request:
            result = self.client.connection_pair_check("=1/2", 1, "=1/3", 2, 0.25, version="E3D21")
        request.assert_called_once_with("POST", "/api/v1/element/connection-pair-check", self.arguments(), version="E3D21")
        self.assertEqual(result, payload)

    def test_bridge_posts_optional_expected_types_only_when_supplied(self):
        payload = self.payload()
        with patch.object(self.client, "_request", return_value=payload) as request:
            result = self.client.connection_pair_check("=1/2", 1, "=1/3", 2, 0.25,
                                                       version="E3D21", first_expected_type="bran",
                                                       second_expected_type="TUBI")
        request.assert_called_once_with("POST", "/api/v1/element/connection-pair-check", {
            "first_refno": "=1/2", "first_point_number": 1, "second_refno": "=1/3",
            "second_point_number": 2, "tolerance_mm": 0.25,
            "first_expected_type": "BRAN", "second_expected_type": "TUBI",
        }, version="E3D21")
        self.assertEqual(result, payload)

    def test_read_does_not_call_mutating_or_view_operations(self):
        forbidden = ("command", "command_batch", "create_element", "copy_element", "set_transform", "set_attribute",
                     "set_drawlist_visual", "set_primitive_visual", "fit_view", "set_view", "restore_view", "capture_view")
        with ExitStack() as stack:
            writes = [stack.enter_context(patch.object(BridgeClient, name)) for name in forbidden]
            read = stack.enter_context(patch.object(BridgeClient, "connection_pair_check", return_value=self.payload()))
            response = self.call(self.arguments())
        self.assertFalse(response.get("isError", False), response)
        read.assert_called_once_with("=1/2", 1, "=1/3", 2, 0.25)
        for write in writes:
            write.assert_not_called()

    def test_bridge_transport_failure_is_not_retried(self):
        with patch.object(BridgeClient, "_request", side_effect=BridgeError("Connection closed", transport_error=True)) as request:
            response = self.call(self.arguments())
        request.assert_called_once_with("POST", "/api/v1/element/connection-pair-check", self.arguments(), version=None)
        self.assertTrue(response["isError"])

    def test_mock_is_explicitly_unavailable(self):
        mock = create_server(Settings(mode="mock", version="E3D21", allow_commands=False))
        with patch.object(BridgeClient, "_request") as native:
            response = self.call(self.arguments(), server=mock)
        native.assert_not_called()
        self.assertTrue(response["isError"])
        result = response["structuredContent"]["result"]
        self.assertFalse(result["success"])
        self.assertFalse(result["available"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["status"], "unavailable")
        self.assertNotIn("endpoints", result)


if __name__ == "__main__":
    unittest.main()

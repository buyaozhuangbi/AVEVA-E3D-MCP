from __future__ import annotations
from catalog_compatibility import CURRENT_MOCK_TOOL_COUNT

from contextlib import ExitStack
from copy import deepcopy
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


class ConnectionPointsToolTests(unittest.TestCase):
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
        return self.rpc("tools/call", {"name": "e3d_connection_points", "arguments": arguments}, server=server)

    @staticmethod
    def native(**changes):
        return {"ok": True, "version": "E3D21", "result": {
            "success": True, "available": True, "complete": True,
            "refno": "=1/2", "type": "NOZZ", "source": "E3D SDK connection points",
            "frame": {"requested": "WORLD", "refno": "=1/1", "type": "WORLD"},
            "points": [], "read_errors": [], **changes,
        }}

    def test_discovery_exposes_one_read_only_tool_with_bounded_explicit_inputs(self):
        tools = {item["name"]: item for item in self.rpc("tools/list", {})["tools"]}
        self.assertEqual(len(tools), CURRENT_MOCK_TOOL_COUNT)
        self.assertEqual(sum("capture_after" in item["inputSchema"].get("properties", {})
                             for item in tools.values()), 19)
        tool = tools["e3d_connection_points"]
        self.assertEqual(tool["annotations"], {
            "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False,
        })
        self.assertIn("ILEAVE TUBE", tool["description"])
        self.assertIn("only a TUBI result is marked resolved", tool["description"])
        self.assertIn("Does not dispatch PML commands", tool["description"])
        schema = tool["inputSchema"]
        self.assertEqual(schema["required"], ["refno"])
        self.assertEqual(set(schema["properties"]), {"refno", "wrt", "point_numbers", "expected_type"})
        self.assertNotIn("default", schema["properties"]["refno"])
        self.assertEqual(schema["properties"]["wrt"]["default"], "WORLD")
        points = schema["properties"]["point_numbers"]
        self.assertEqual(points["type"], "array")
        self.assertEqual(points["default"], [])
        self.assertEqual(points["maxItems"], 64)
        self.assertTrue(points["uniqueItems"])
        self.assertEqual(points["items"], {"type": "integer", "minimum": 1, "maximum": 10000})
        self.assertFalse(schema["additionalProperties"])

    def test_read_uses_world_and_empty_point_selection_without_write_permission(self):
        payload = self.native()
        with patch.object(BridgeClient, "connection_points", return_value=payload) as read:
            response = self.call({"refno": " =1/2 "})
        read.assert_called_once_with("=1/2", "WORLD", [])
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"], payload)
        self.assertEqual([item["type"] for item in response["content"]], ["text"])
        self.assertEqual(json.loads(response["content"][0]["text"]), payload)

    def test_explicit_frames_and_point_boundaries_forward_without_reordering(self):
        cases = [
            ({"wrt": " owner ", "point_numbers": []}, "OWNER", []),
            ({"wrt": " world ", "point_numbers": [10000, 1]}, "WORLD", [10000, 1]),
            ({"wrt": " /Equipment/Frame ", "point_numbers": [1]}, "/Equipment/Frame", [1]),
            ({"wrt": " =123/456 ", "point_numbers": list(range(64, 0, -1))},
             "=123/456", list(range(64, 0, -1))),
        ]
        for options, frame, points in cases:
            with self.subTest(options=options), patch.object(BridgeClient, "connection_points", return_value=self.native()) as read:
                response = self.call({"refno": "/Equipment/Nozzle", **options})
            self.assertFalse(response.get("isError", False), response)
            read.assert_called_once_with("/Equipment/Nozzle", frame, points)

    def test_missing_implicit_and_malformed_target_references_are_rejected_before_native(self):
        invalid = [{}, *({"refno": value} for value in (
            None, True, 1, [], {}, "", " ", "CE", "OWNER", "WORLD", "/", "=x/2", "=1/2/3",
            "/NOZZLE\nDELETE CE", "/NOZZLE\x00", "/NOZZLE\x80", "/PUMP NOZZLE",
        ))]
        with patch.object(BridgeClient, "connection_points") as read:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self.call(arguments)["isError"])
        read.assert_not_called()

    def test_invalid_coordinate_frames_are_rejected_before_native(self):
        invalid = (None, True, 1, [], {}, "", " ", "CE", "ALL", "/", "=x/2", "=1/2/3",
                   "/FRAME\nDELETE CE", "/FRAME\x00", "/FRAME\x80", "/PUMP FRAME")
        with patch.object(BridgeClient, "connection_points") as read:
            for frame in invalid:
                with self.subTest(frame=frame):
                    self.assertTrue(self.call({"refno": "/NOZZLE", "wrt": frame})["isError"])
        read.assert_not_called()

    def test_invalid_point_selections_are_rejected_before_native(self):
        invalid = [None, True, 1, "1", {}, [0], [-1], [10001], [True], [False], [1.0], ["1"],
                   [None], [[]], [1, 1], [1, 2, 1], list(range(1, 66))]
        with patch.object(BridgeClient, "connection_points") as read:
            for points in invalid:
                with self.subTest(points=points):
                    self.assertTrue(self.call({"refno": "/NOZZLE", "point_numbers": points})["isError"])
        read.assert_not_called()

    def test_unknown_capture_and_write_controls_are_rejected_before_native(self):
        options = [("dry_run", True), ("dry_run", False), ("capture_after", False), ("capture_after", True),
                   ("capture_width", 64), ("capture_height", 64), ("command", "Q PCREF"),
                   ("add", False), ("fit", "target"), ("scope", "members"), ("limit", 64),
                   ("point_number", 1), ("expected_revision", "a" * 64), ("version", "E3D21")]
        with patch.object(BridgeClient, "connection_points") as read:
            for name, value in options:
                with self.subTest(name=name, value=value):
                    self.assertTrue(self.call({"refno": "/NOZZLE", name: value})["isError"])
        read.assert_not_called()

    def test_native_unknowns_and_opaque_pcref_encoding_survive_without_inference(self):
        payload = self.native(complete=False, partial=True, points=[{
            "number": 1, "available": True,
            "position": {"available": True, "unit": "mm", "x": 10.5, "y": 20.0, "z": 30.0},
            "direction": {"available": True, "x": 0.0, "y": 1.0, "z": 0.0},
            "bore": {"available": False, "reason": "unset", "value": None},
            "pcref": {"available": True, "raw_encoded": [2000000001, 8240, 3],
                      "refno": None, "reason": "encoded_reference_not_resolved"},
        }], read_errors=[])
        original = deepcopy(payload)
        with patch.object(BridgeClient, "connection_points", return_value=payload):
            response = self.call({"refno": "/NOZZLE", "point_numbers": [1]})
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"], original)
        self.assertEqual(json.loads(response["content"][0]["text"]), original)
        self.assertEqual(payload, original)
        point = response["structuredContent"]["result"]["points"][0]
        for invented in ("dn", "DN", "nominal_diameter", "connected", "connected_refno", "connected_point"):
            self.assertNotIn(invented, point)
        self.assertIsNone(point["bore"]["value"])
        self.assertIsNone(point["pcref"]["refno"])

    def test_native_read_failure_keeps_other_point_fields_and_diagnostics(self):
        payload = self.native(success=False, complete=False, partial=True, points=[{
            "number": 1, "position": {"available": True, "x": 10, "y": 20, "z": 30, "unit": "mm"},
            "direction": {"available": False, "reason": "read_failed", "error": "PDIR unavailable"},
            "bore": {"available": True, "value": 80.0, "unit": "mm"},
            "pcref": {"available": False, "reason": "unset", "raw_encoded": None},
        }], read_errors=[{"point_number": 1, "field": "direction", "error": "PDIR unavailable"}])
        original = deepcopy(payload)
        with patch.object(BridgeClient, "connection_points", return_value=payload):
            response = self.call({"refno": "/NOZZLE"})
        self.assertTrue(response["isError"])
        self.assertEqual(response["structuredContent"], original)
        self.assertEqual(json.loads(response["content"][0]["text"]), original)
        self.assertEqual(payload, original)

    def test_native_empty_or_unsupported_points_are_not_fabricated(self):
        payload = self.native(complete=False, points=[], point_numbers=[],
                              reason="No native connection-point descriptors are supported for this element")
        with patch.object(BridgeClient, "connection_points", return_value=payload):
            response = self.call({"refno": "/BOX"})
        self.assertEqual(response["structuredContent"], payload)
        self.assertEqual(response["structuredContent"]["result"]["points"], [])

    def test_read_never_invokes_commands_geometry_display_camera_or_image_calls(self):
        forbidden = ("command", "command_batch", "create_element", "copy_element", "set_transform", "set_attribute",
                     "set_drawlist_visual", "set_primitive_visual", "fit_view", "set_view", "restore_view", "capture_view")
        with ExitStack() as stack:
            writes = [stack.enter_context(patch.object(BridgeClient, name)) for name in forbidden]
            read = stack.enter_context(patch.object(BridgeClient, "connection_points", return_value=self.native()))
            response = self.call({"refno": "/NOZZLE"})
        self.assertFalse(response.get("isError", False), response)
        read.assert_called_once_with("/NOZZLE", "WORLD", [])
        for write in writes:
            write.assert_not_called()

    def test_bridge_uses_one_native_read_request_and_preserves_selected_instance(self):
        payload = self.native(complete=False, extension={"raw": [1, None, "unset"]})
        original = deepcopy(payload)
        with patch.object(self.client, "_request", return_value=payload) as request:
            response = self.client.connection_points("=1/2", "OWNER", [10000, 1], version="E3D21")
        request.assert_called_once_with("POST", "/api/v1/element/connection-points",
                                        {"refno": "=1/2", "wrt": "OWNER", "point_numbers": [10000, 1]}, version="E3D21")
        self.assertEqual(response, original)
        self.assertEqual(payload, original)
        with patch.object(self.client, "_request", return_value=payload) as request:
            self.client.connection_points("/NOZZLE", version="E3D21")
        request.assert_called_once_with("POST", "/api/v1/element/connection-points",
                                        {"refno": "/NOZZLE", "wrt": "WORLD", "point_numbers": []}, version="E3D21")

    def test_transport_failure_is_not_retried_or_reported_as_an_uncertain_write(self):
        with patch.object(BridgeClient, "_request", side_effect=BridgeError("Connection closed", transport_error=True)) as request:
            response = self.call({"refno": "/NOZZLE"})
        request.assert_called_once_with("POST", "/api/v1/element/connection-points",
                                        {"refno": "/NOZZLE", "wrt": "WORLD", "point_numbers": []}, version=None)
        self.assertTrue(response["isError"])
        self.assertIn("Connection closed", json.dumps(response))
        for forbidden in ("outcome_uncertain", "retry_safe", "receipt"):
            self.assertNotIn(forbidden, json.dumps(response))

    def test_mock_is_explicitly_unavailable_without_native_points_or_connections(self):
        mock = create_server(Settings(mode="mock", version="E3D21", allow_commands=False))
        with patch.object(BridgeClient, "_request") as native:
            response = self.call({"refno": "/NOZZLE", "point_numbers": [1, 10000]}, server=mock)
        native.assert_not_called()
        self.assertTrue(response["isError"])
        metadata = response["structuredContent"]
        self.assertEqual(metadata["version"], "E3D21")
        result = metadata["result"]
        self.assertFalse(result["success"])
        self.assertFalse(result["available"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["source"], "mock")
        self.assertFalse(result.get("points"))
        for field in ("pcref", "connected_refno", "connected", "position", "direction", "bore", "dn"):
            self.assertNotIn(field, result)
        self.assertFalse(any(item["type"] == "image" for item in response["content"]))


if __name__ == "__main__":
    unittest.main()

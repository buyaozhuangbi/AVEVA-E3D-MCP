from __future__ import annotations

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


class GeometryRelationToolTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(mode="bridge", version="E3D21", allow_commands=False)
        self.server = create_server(self.settings)

    def rpc(self, method, params, *, server=None):
        output = StringIO()
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        with patch("sys.stdout", output):
            (server or self.server).serve([request + "\n"])
        return json.loads(output.getvalue())["result"]

    def call(self, arguments, *, server=None):
        return self.rpc("tools/call", {"name": "e3d_geometry_relation", "arguments": arguments}, server=server)

    @staticmethod
    def payload():
        return {"ok": True, "version": "E3D21", "result": {
            "success": True, "complete": False, "partial": True,
            "test": {"refno": "=1/2", "type": "BOX"},
            "against": {"refno": "=1/3", "type": "CYLI"},
            "exact": {"available": True, "value": False, "b_fully": False,
                      "predicate": "Spatial.ElementInElementExact(test, against, false)"},
            "box": {"available": False, "value": None, "status": "not_requested",
                    "predicate": None},
            "ce_before": "=1/9", "ce_after": "=1/9", "ce_unchanged": True,
            "measurement_scope": "raw native predicates only; no clash/clear verdict",
        }}

    def test_catalog_exposes_one_ordered_read_only_pair(self):
        tools = {item["name"]: item for item in self.rpc("tools/list", {})["tools"]}
        tool = tools["e3d_geometry_relation"]
        self.assertEqual(tool["annotations"], {
            "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False,
        })
        schema = tool["inputSchema"]
        self.assertEqual(set(schema["properties"]), {"test_refno", "against_refno", "include_box"})
        self.assertEqual(schema["required"], ["test_refno", "against_refno"])
        self.assertFalse(schema["additionalProperties"])
        self.assertIs(schema["properties"]["include_box"]["default"], False)
        self.assertIn("false value is only the SDK result", tool["description"])
        self.assertIn("e3d_geometry_relation", self.server.instructions)

    def test_invalid_or_implicit_references_and_controls_fail_before_bridge(self):
        invalid = [
            {}, {"test_refno": "=1/2"},
            {"test_refno": "CE", "against_refno": "=1/3"},
            {"test_refno": "/A\nDELETE CE", "against_refno": "/B"},
            {"test_refno": "/A", "against_refno": "/B", "include_box": 1},
            {"test_refno": "/A", "against_refno": "/B", "dry_run": False},
        ]
        with patch.object(BridgeClient, "geometry_relation") as read:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self.call(arguments)["isError"])
        read.assert_not_called()

    def test_valid_call_forwards_order_and_preserves_raw_sdk_evidence(self):
        payload = self.payload()
        for arguments, expected in (
            ({"test_refno": " =1/2 ", "against_refno": " =1/3 "}, ("=1/2", "=1/3", False)),
            ({"test_refno": "/BOX", "against_refno": "/CYLI", "include_box": True}, ("/BOX", "/CYLI", True)),
        ):
            with self.subTest(arguments=arguments), patch.object(
                    BridgeClient, "geometry_relation", return_value=payload) as read:
                result = self.call(arguments)
            self.assertFalse(result.get("isError", False), result)
            read.assert_called_once_with(*expected)
            self.assertEqual(result["structuredContent"], payload)
            self.assertIs(result["structuredContent"]["result"]["exact"]["value"], False)

    def test_bridge_route_is_single_post_and_keeps_order_and_include_box(self):
        client = BridgeClient(self.settings)
        with patch.object(client, "_request", return_value=self.payload()) as request:
            result = client.geometry_relation("=1/2", "=1/3", True)
        request.assert_called_once_with("POST", "/api/v1/element/geometry-relation", {
            "test_refno": "=1/2", "against_refno": "=1/3", "include_box": True,
        }, version="E3D21")
        self.assertEqual(result, self.payload())

    def test_partial_unknowns_and_native_errors_are_never_coerced_to_false(self):
        payload = self.payload()
        payload["result"]["exact"] = {"available": False, "value": None, "error": "native read failed",
                                      "b_fully": False}
        payload["result"]["box"] = {"available": False, "value": None, "error": "not requested",
                                     "status": "not_requested"}
        original = deepcopy(payload)
        with patch.object(BridgeClient, "geometry_relation", return_value=payload):
            result = self.call({"test_refno": "=1/2", "against_refno": "=1/3", "include_box": True})
        self.assertFalse(result.get("isError", False), result)
        self.assertEqual(result["structuredContent"], original)
        self.assertIsNone(result["structuredContent"]["result"]["exact"]["value"])

    def test_mock_and_other_versions_report_unavailable_without_fabrication(self):
        for settings, source in (
            (Settings(mode="mock", version="E3D21"), "mock"),
            (Settings(mode="bridge", version="E3D31"), "unsupported"),
        ):
            server = create_server(settings)
            with patch.object(BridgeClient, "_request") as request:
                result = self.call({"test_refno": "/A", "against_refno": "/B"}, server=server)
            request.assert_not_called()
            content = result["structuredContent"]
            self.assertFalse(content["result"]["success"])
            self.assertFalse(content["result"]["available"])
            self.assertEqual(content["result"]["source"], source)
            self.assertIsNone(content["result"].get("exact"))


if __name__ == "__main__":
    unittest.main()

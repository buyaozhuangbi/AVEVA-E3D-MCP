from __future__ import annotations

from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class GeometryRelationBatchToolTests(unittest.TestCase):
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
        return self.rpc("tools/call", {"name": "e3d_geometry_relation_batch", "arguments": arguments}, server=server)

    @staticmethod
    def payload():
        return {"ok": True, "version": "E3D21", "result": {
            "success": True, "complete": False, "partial": True,
            "single_dispatch": True, "requested_pair_count": 2,
            "failed_pair_count": 0, "complete_pair_count": 1,
            "ce_before": "=1/9", "ce_after": "=1/9", "ce_unchanged": True,
            "rows": [
                {"requested_test_refno": "=1/2", "requested_against_refno": "=1/3",
                 "success": True, "complete": True, "partial": False,
                 "exact": {"available": True, "value": False, "b_fully": False},
                 "box": {"available": True, "value": True}, "errors": []},
                {"requested_test_refno": "/A", "requested_against_refno": "/B",
                 "success": True, "complete": False, "partial": True,
                 "exact": {"available": False, "value": None, "error": "exact unavailable", "b_fully": False},
                 "box": {"available": True, "value": False}, "errors": []},
            ],
        }}

    def test_catalog_exposes_bounded_read_only_ordered_pairs(self):
        tools = {item["name"]: item for item in self.rpc("tools/list", {})["tools"]}
        self.assertEqual(len(tools), 126)
        tool = tools["e3d_geometry_relation_batch"]
        self.assertEqual(tool["annotations"], {
            "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False,
        })
        schema = tool["inputSchema"]
        self.assertEqual(set(schema["properties"]), {"pairs", "include_box"})
        self.assertEqual(schema["required"], ["pairs"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["pairs"]["minItems"], 1)
        self.assertEqual(schema["properties"]["pairs"]["maxItems"], 32)
        self.assertEqual(schema["properties"]["pairs"]["items"]["minItems"], 2)
        self.assertEqual(schema["properties"]["pairs"]["items"]["maxItems"], 2)
        self.assertEqual(schema["properties"]["pairs"]["items"]["items"]["type"], "string")
        self.assertIs(schema["properties"]["include_box"]["default"], False)
        self.assertIn("one E3D21 UI callback", tool["description"])
        self.assertIn("e3d_geometry_relation_batch", self.server.instructions)

    def test_invalid_pairs_and_controls_fail_before_bridge(self):
        invalid = [
            {}, {"pairs": []}, {"pairs": [["/A", "/B"]] * 33},
            {"pairs": [["/A"]]}, {"pairs": [["/A", "/B", "/C"]]},
            {"pairs": [["/A", 2]]}, {"pairs": [{"test_refno": "/A"}]},
            {"pairs": [{"test_refno": "CE", "against_refno": "/B"}]},
            {"pairs": [{"test_refno": "/A\nDELETE CE", "against_refno": "/B"}]},
            {"pairs": [{"test_refno": "/A", "against_refno": "/B", "dry_run": False}]},
            {"pairs": [["/A", "/B"]], "include_box": 1},
            {"pairs": [["/A", "/B"]], "dry_run": False},
        ]
        with patch.object(BridgeClient, "geometry_relation_batch") as read:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self.call(arguments)["isError"])
        read.assert_not_called()

    def test_valid_call_preserves_pair_order_and_raw_unknowns(self):
        pairs = [[" =1/2 ", " =1/3 "], [" /A ", " /B "]]
        payload = self.payload()
        with patch.object(BridgeClient, "geometry_relation_batch", return_value=payload) as read:
            result = self.call({"pairs": pairs, "include_box": True})
        self.assertFalse(result.get("isError", False), result)
        read.assert_called_once_with([("=1/2", "=1/3"), ("/A", "/B")], True)
        self.assertEqual(result["structuredContent"], payload)
        self.assertIs(result["structuredContent"]["result"]["rows"][0]["exact"]["value"], False)
        self.assertIsNone(result["structuredContent"]["result"]["rows"][1]["exact"]["value"])

    def test_bridge_route_uses_one_ordered_batch_request(self):
        client = BridgeClient(self.settings)
        pairs = [("=1/2", "=1/3"), ("/A", "/B")]
        with patch.object(client, "_request", return_value=self.payload()) as request:
            result = client.geometry_relation_batch(pairs, True)
        request.assert_called_once_with("POST", "/api/v1/element/geometry-relation-batch", {
            "pairs": [["=1/2", "=1/3"], ["/A", "/B"]],
            "include_box": True,
        }, version="E3D21")
        self.assertEqual(result, self.payload())

    def test_pair_resolution_error_preserves_other_rows_in_mcp_error_receipt(self):
        payload = self.payload()
        result = payload["result"]
        result.update(success=False, failed_pair_count=1)
        missing = result["rows"][1]
        missing.update(success=False, requested_test_refno="/MISSING")
        missing["exact"].update(status="not_attempted", error=None)
        missing["box"].update(available=False, value=None, status="not_attempted")
        missing["errors"] = [{"code": "pair_read_failed", "message": "Element was not found: /MISSING"}]
        original = deepcopy(payload)
        with patch.object(BridgeClient, "geometry_relation_batch", return_value=payload):
            receipt = self.call({"pairs": [["=1/2", "=1/3"], ["/MISSING", "/B"]], "include_box": True})
        self.assertTrue(receipt.get("isError", False), receipt)
        self.assertEqual(receipt["structuredContent"], original)
        rows = receipt["structuredContent"]["result"]["rows"]
        self.assertIs(rows[0]["exact"]["value"], False)
        self.assertIsNone(rows[1]["exact"]["value"])

    def test_mock_and_unsupported_versions_report_unavailable(self):
        for settings, source in (
            (Settings(mode="mock", version="E3D21"), "mock"),
            (Settings(mode="bridge", version="E3D31"), "unsupported"),
        ):
            server = create_server(settings)
            with patch.object(BridgeClient, "_request") as request:
                result = self.call({"pairs": [["/A", "/B"]]}, server=server)
            request.assert_not_called()
            content = result["structuredContent"]["result"]
            self.assertFalse(content["success"])
            self.assertFalse(content["available"])
            self.assertEqual(content["source"], source)
            self.assertNotIn("rows", content)


if __name__ == "__main__":
    unittest.main()

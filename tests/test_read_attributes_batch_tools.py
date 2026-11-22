from __future__ import annotations

import json
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class ReadAttributesBatchToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(mode="bridge", version="E3D21", allow_commands=False)
        self.server = create_server(self.settings)

    def _rpc(self, method: str, params: dict) -> dict:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        output = StringIO()
        with patch("sys.stdout", output):
            self.server.serve([request + "\n"])
        return json.loads(output.getvalue())["result"]

    def _call(self, arguments: dict, settings: Settings | None = None) -> dict:
        if settings is None:
            return self._rpc("tools/call", {"name": "e3d_read_attributes_batch", "arguments": arguments})
        output = StringIO()
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "e3d_read_attributes_batch", "arguments": arguments}})
        with patch("sys.stdout", output):
            create_server(settings).serve([request + "\n"])
        return json.loads(output.getvalue())["result"]

    @staticmethod
    def _row(refno: str, *, success: bool = True) -> dict:
        return {
            "requested_refno": refno,
            "refno": refno,
            "type": "PIPE",
            "name": refno.rsplit("/", 1)[-1],
            "success": success,
            "values": {"NAME": refno.rsplit("/", 1)[-1]},
            "typed_values": {"NAME": refno.rsplit("/", 1)[-1]},
            "errors": {} if success else {"NAME": "read failed"},
            "typed_read_errors": {},
        }

    def test_schema_is_bounded_strict_and_read_only(self) -> None:
        tools = self._rpc("tools/list", {})["tools"]
        tool = next(item for item in tools if item["name"] == "e3d_read_attributes_batch")
        schema = tool["inputSchema"]
        self.assertEqual(set(schema["properties"]), {"refnos", "attributes"})
        self.assertEqual(schema["required"], ["refnos", "attributes"])
        self.assertFalse(schema["additionalProperties"])
        for name in ("refnos", "attributes"):
            self.assertEqual(schema["properties"][name]["type"], "array")
            self.assertEqual(schema["properties"][name]["minItems"], 1)
            self.assertEqual(schema["properties"][name]["maxItems"], 50)
            self.assertEqual(schema["properties"][name]["items"]["type"], "string")
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["destructiveHint"])
        self.assertTrue(tool["annotations"]["idempotentHint"])

    def test_validates_all_inputs_before_calling_bridge(self) -> None:
        invalid = [
            {},
            {"refnos": [], "attributes": ["NAME"]},
            {"refnos": ["/PUMP"], "attributes": []},
            {"refnos": ["/PUMP"] * 2, "attributes": ["NAME"]},
            {"refnos": ["PUMP"], "attributes": ["NAME"]},
            {"refnos": ["/"], "attributes": ["NAME"]},
            {"refnos": ["=x/2"], "attributes": ["NAME"]},
            {"refnos": ["/PUMP", None], "attributes": ["NAME"]},
            {"refnos": ["/PUMP", True], "attributes": ["NAME"]},
            {"refnos": ["/PUMP"], "attributes": [None]},
            {"refnos": ["/PUMP"], "attributes": [" "]},
            {"refnos": ["/PUMP"], "attributes": ["BAD-NAME"]},
            {"refnos": [f"/E{i}" for i in range(51)], "attributes": ["NAME"]},
            {"refnos": ["/PUMP"], "attributes": [f"A{i}" for i in range(51)]},
            {"refnos": ["/PUMP"], "attributes": ["NAME"], "dry_run": True},
        ]
        invalid.append({"refnos": [f"/E{i}" for i in range(23)],
                        "attributes": [f"A{i}" for i in range(22)]})
        with patch.object(BridgeClient, "read_attributes_batch") as read:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self._call(arguments).get("isError"))
            read.assert_not_called()

    def test_normalizes_and_deduplicates_attributes_preserving_refno_order(self) -> None:
        refnos = ["/SECOND", "=123/45", "/FIRST"]
        response = {"ok": True, "result": {"success": True, "complete": True,
                    "rows": [self._row(refno) for refno in refnos]}}
        with patch.object(BridgeClient, "read_attributes_batch", return_value=response) as read:
            result = self._call({"refnos": refnos, "attributes": [" name ", "POS", "NAME", " pos "]})
        read.assert_called_once_with(refnos, ["NAME", "POS"])
        self.assertFalse(result.get("isError", False))
        self.assertEqual(result["structuredContent"], response)
        self.assertEqual([row["requested_refno"] for row in result["structuredContent"]["result"]["rows"]], refnos)

    def test_accepts_exactly_500_unique_reference_attribute_pairs(self) -> None:
        refnos = [f"/E{i}" for i in range(50)]
        attributes = [f"A{i}" for i in range(10)]
        response = {"ok": True, "result": {"success": True, "complete": True, "rows": []}}
        with patch.object(BridgeClient, "read_attributes_batch", return_value=response) as read:
            result = self._call({"refnos": refnos, "attributes": attributes})
        read.assert_called_once_with(refnos, attributes)
        self.assertFalse(result.get("isError", False))

    def test_mcp_marks_partial_native_failure_as_error_and_keeps_rows(self) -> None:
        rows = [self._row("/GOOD"), self._row("/BAD", success=False), self._row("/ALSO-GOOD")]
        response = {"ok": True, "result": {"success": False, "complete": False, "rows": rows,
                    "errors": {"/BAD": "read failed"}}}
        with patch.object(BridgeClient, "read_attributes_batch", return_value=response) as read:
            result = self._call({"refnos": ["/GOOD", "/BAD", "/ALSO-GOOD"], "attributes": ["NAME"]})
        read.assert_called_once_with(["/GOOD", "/BAD", "/ALSO-GOOD"], ["NAME"])
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"], response)
        self.assertEqual([row["requested_refno"] for row in result["structuredContent"]["result"]["rows"]],
                         ["/GOOD", "/BAD", "/ALSO-GOOD"])

    def test_mock_reports_unavailable_without_fabricating_rows(self) -> None:
        result = self._call({"refnos": ["/PUMP"], "attributes": ["NAME"]}, Settings(mode="mock"))
        self.assertTrue(result["isError"])
        batch = result["structuredContent"]["result"]
        self.assertFalse(batch["available"])
        self.assertFalse(batch["complete"])
        self.assertNotIn("rows", batch)

    def test_bridge_posts_one_ordered_batch_payload_to_native_route(self) -> None:
        client = BridgeClient(self.settings)
        refnos = ["/SECOND", "=123/45", "/FIRST"]
        attributes = ["NAME", "POS"]
        with patch.object(client, "_request", return_value={"ok": True}) as request:
            response = client.read_attributes_batch(refnos, attributes)
        self.assertEqual(response, {"ok": True})
        request.assert_called_once_with("POST", "/api/v1/elements/attributes/batch",
                                        {"refnos": refnos, "attributes": attributes}, version=None)


if __name__ == "__main__":
    unittest.main()

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


class ExpectedTypeReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(mode="bridge", version="E3D21", allow_commands=False)
        self.server = create_server(self.settings)

    def rpc(self, method: str, params: dict) -> dict:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        output = StringIO()
        with patch("sys.stdout", output):
            self.server.serve([request + "\n"])
        response = json.loads(output.getvalue())
        self.assertNotIn("error", response, response)
        return response["result"]

    def call(self, name: str, arguments: dict) -> dict:
        return self.rpc("tools/call", {"name": name, "arguments": arguments})

    def test_expected_type_is_discoverable_but_optional_for_both_read_tools(self) -> None:
        tools = {item["name"]: item for item in self.rpc("tools/list", {})["tools"]}
        for name in ("e3d_element_schema", "e3d_read_attributes"):
            with self.subTest(name=name):
                tool = tools[name]
                field = tool["inputSchema"]["properties"]["expected_type"]
                self.assertEqual(field["type"], "string")
                self.assertEqual(field["maxLength"], 64)
                self.assertNotIn("expected_type", tool["inputSchema"].get("required", []))
                self.assertIn("shortname", field["description"].lower())
                self.assertIn("only implicit tubi", field["description"].lower())

    def test_handlers_normalize_type_and_forward_it_to_both_bridge_reads(self) -> None:
        schema_payload = {"ok": True, "result": {
            "success": True, "element": {"refno": "=1/2", "type": "TUBI"}, "attributes": []}}
        with patch.object(BridgeClient, "element_schema", return_value=schema_payload) as schema_read:
            result = self.call("e3d_element_schema", {
                "refno": "=1/2", "expected_type": " tubi ", "attributes": ["pos"],
            })
        self.assertFalse(result.get("isError", False), result)
        schema_read.assert_called_once_with("=1/2", ["POS"], 0, 50, expected_type="TUBI")

        attributes_payload = {"ok": True, "result": {
            "success": True, "refno": "=1/2", "type": "TUBI", "values": {"POS": "0 0 0"}}}
        with patch.object(BridgeClient, "read_attributes", return_value=attributes_payload) as attribute_read:
            result = self.call("e3d_read_attributes", {
                "refno": "=1/2", "expected_type": " tubi ", "attributes": ["pos"],
            })
        self.assertFalse(result.get("isError", False), result)
        attribute_read.assert_called_once_with("=1/2", ["POS"], expected_type="TUBI")

    def test_invalid_type_values_are_rejected_before_bridge_reads(self) -> None:
        invalid = [None, 7, "TU\nBI", "TUBI; DELETE CE", "T" * 65]
        with patch.object(BridgeClient, "element_schema") as schema_read, \
                patch.object(BridgeClient, "read_attributes") as attribute_read:
            for value in invalid:
                with self.subTest(value=value):
                    self.assertTrue(self.call("e3d_element_schema", {
                        "expected_type": value, "attributes": ["POS"],
                    }).get("isError", False))
                    self.assertTrue(self.call("e3d_read_attributes", {
                        "expected_type": value, "attributes": ["POS"],
                    }).get("isError", False))
            schema_read.assert_not_called()
            attribute_read.assert_not_called()

    def test_bridge_bodies_normalize_type_and_keep_legacy_default_shape(self) -> None:
        client = BridgeClient(self.settings)
        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.element("=1/2", 1, 20, expected_type=" tubi ")
        request.assert_called_once_with("GET", "/api/v1/element", query={
            "refno": "=1/2", "expected_type": "TUBI", "depth": "1", "limit": "20",
        }, version=None)

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.list_members("=1/2", "", 7, expected_type=" tubi ")
        request.assert_called_once_with("GET", "/api/v1/element/members", query={
            "refno": "=1/2", "expected_type": "TUBI", "cursor": "", "limit": "7",
        }, version=None)

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.read_attributes("=1/2", ["POS"], expected_type=" tubi ")
        request.assert_called_once_with("POST", "/api/v1/element/attributes", {
            "refno": "=1/2", "attributes": ["POS"], "expected_type": "TUBI",
        }, version=None)

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.element_schema("=1/2", ["POS"], 0, 50, "E3D21", expected_type=" tubi ")
        request.assert_called_once_with("POST", "/api/v1/element/schema", {
            "refno": "=1/2", "attributes": ["POS"], "offset": 0, "limit": 50,
            "expected_type": "TUBI",
        }, version="E3D21")

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.trace_connection_network([{
                "refno": "=1/2", "point_number": 2, "expected_type": " tubi ",
            }])
        request.assert_called_once_with("POST", "/api/v1/element/connection-trace", {
            "starts": [{"refno": "=1/2", "point_number": 2, "expected_type": "TUBI"}],
            "max_hops": 8, "max_nodes": 64,
        }, version="E3D21")

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.read_attributes("=1/2", ["POS"], "E3D21")
            request.assert_called_once_with("POST", "/api/v1/element/attributes", {
                "refno": "=1/2", "attributes": ["POS"],
            }, version="E3D21")
            request.reset_mock()
            client.element_schema("=1/2", ["POS"], 0, 50, "E3D21")
            request.assert_called_once_with("POST", "/api/v1/element/schema", {
                "refno": "=1/2", "attributes": ["POS"], "offset": 0, "limit": 50,
            }, version="E3D21")

    def test_mock_mode_reports_alias_resolution_unavailable(self) -> None:
        mock = BridgeClient(Settings(mode="mock", version="E3D21"))
        schema = mock.element_schema("=1/2", ["POS"], 0, 50, expected_type="TUBI")["result"]
        self.assertFalse(schema["success"])
        self.assertFalse(schema["available"])
        self.assertIsNone(schema["element"]["type"])
        self.assertIn("cannot resolve", schema["read_errors"]["element"].lower())

        attributes = mock.read_attributes("=1/2", ["POS"], expected_type="TUBI")["result"]
        self.assertFalse(attributes["success"])
        self.assertFalse(attributes["available"])
        self.assertIsNone(attributes["type"])
        self.assertEqual(attributes["values"], {})
        self.assertIn("cannot resolve", attributes["errors"]["$element"].lower())

    def test_bridge_rejects_silent_type_mismatch_from_an_old_addin(self) -> None:
        client = BridgeClient(self.settings)
        old_schema = {"ok": True, "result": {
            "success": True, "element": {"refno": "=1/2", "type": "BRAN"},
            "element_type": {"name": "BRANCH", "short_name": "BRAN"},
        }}
        with patch.object(client, "_request", return_value=old_schema):
            response = client.element_schema("=1/2", [], 0, 50, expected_type="TUBI")
        self.assertFalse(response["ok"])
        self.assertIn("returned BRAN", response["error"])
        self.assertEqual(response["result"], old_schema["result"])

        old_attributes = {"ok": True, "result": {
            "success": True, "refno": "=1/2", "type": "BRAN", "values": {"NAME": "/BRAN"}}}
        with patch.object(client, "_request", return_value=old_attributes):
            response = client.read_attributes("=1/2", ["NAME"], expected_type="TUBI")
        self.assertFalse(response["ok"])
        self.assertIn("returned BRAN", response["error"])
        self.assertEqual(response["result"], old_attributes["result"])

        missing_type = {"ok": True, "result": {"success": True, "values": {}}}
        with patch.object(client, "_request", return_value=missing_type):
            response = client.read_attributes("=1/2", ["NAME"], expected_type="TUBI")
        self.assertFalse(response["ok"])
        self.assertIn("returned unknown", response["error"])


if __name__ == "__main__":
    unittest.main()

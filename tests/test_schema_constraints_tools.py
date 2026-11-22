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


class ElementSchemaConstraintsToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(mode="bridge", version="E3D21", allow_commands=False)
        self.client = BridgeClient(self.settings)
        self.server = create_server(self.settings)

    def rpc(self, method: str, params: dict) -> dict:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        output = StringIO()
        with patch("sys.stdout", output):
            self.server.serve([request + "\n"])
        response = json.loads(output.getvalue())
        self.assertNotIn("error", response, response)
        return response["result"]

    def call(self, arguments: dict) -> dict:
        return self.rpc("tools/call", {"name": "e3d_element_schema", "arguments": arguments})

    def test_discovery_describes_optional_constraints_and_attribute_limit(self) -> None:
        tools = {item["name"]: item for item in self.rpc("tools/list", {})["tools"]}
        tool = tools["e3d_element_schema"]
        schema = tool["inputSchema"]
        include_constraints = schema["properties"]["include_constraints"]

        self.assertEqual(include_constraints["type"], "boolean")
        self.assertIs(include_constraints["default"], False)
        contract_text = " ".join((tool["description"], include_constraints.get("description", ""),
                                   schema["properties"]["attributes"].get("description", ""))).lower()
        self.assertIn("constraint", contract_text)
        self.assertIn("attributes", contract_text)
        self.assertIn("10", contract_text)
        self.assertIn("explicit", contract_text)
        self.assertTrue(tool["annotations"]["readOnlyHint"])

    def test_constraint_flag_is_forwarded_as_boolean_in_native_body(self) -> None:
        with patch.object(self.client, "_request", return_value={"ok": True}) as request:
            self.client.element_schema("/PUMP", ["POS", "HEIG"], 2, 10, "E3D21", include_constraints=True)
        request.assert_called_once_with("POST", "/api/v1/element/schema", {
            "refno": "/PUMP", "attributes": ["POS", "HEIG"], "offset": 2, "limit": 10,
            "include_constraints": True,
        }, version="E3D21")

    def test_constraint_read_requires_bridge_confirmation_and_preserves_confirmed_response(self) -> None:
        unconfirmed = {"ok": True, "version": "E3D21", "result": {"success": True, "attributes": []}}
        with patch.object(self.client, "_request", return_value=unconfirmed) as request:
            response = self.client.element_schema("/PUMP", ["POS"], 0, 50, include_constraints=True)
        self.assertIs(response["ok"], False)
        self.assertIn("update", response["error"].lower())
        self.assertIn("add-in", response["error"].lower())
        self.assertEqual(response["result"], unconfirmed["result"])
        request.assert_called_once_with("POST", "/api/v1/element/schema", {
            "refno": "/PUMP", "attributes": ["POS"], "offset": 0, "limit": 50,
            "include_constraints": True,
        }, version=None)

        confirmed = {"ok": True, "version": "E3D21", "result": {
            "success": True, "constraints_requested": True, "extension": {"preserved": [1, 2, 3]},
        }}
        with patch.object(self.client, "_request", return_value=confirmed):
            response = self.client.element_schema("/PUMP", ["POS"], 0, 50, include_constraints=True)
        self.assertIs(response, confirmed)
        self.assertEqual(response, confirmed)

    def test_old_addin_constraint_response_is_mcp_error_with_result_preserved(self) -> None:
        old_response = {
            "ok": True,
            "version": "E3D21",
            "result": {"success": True, "attributes": [{"name": "POS", "supported": True}]},
        }
        with patch.object(BridgeClient, "_request", return_value=old_response):
            response = self.call({"refno": "=1/2", "attributes": ["POS"], "include_constraints": True})

        self.assertTrue(response["isError"], response)
        self.assertIn("add-in", response["content"][0]["text"].lower())
        self.assertFalse(response["structuredContent"]["ok"])
        self.assertEqual(response["structuredContent"]["result"], old_response["result"])

    def test_partial_constraint_failure_is_mcp_error_with_successful_fields_preserved(self) -> None:
        partial_response = {
            "ok": True,
            "version": "E3D21",
            "result": {
                "success": False,
                "constraints_requested": True,
                "constraints_complete": False,
                "attributes": [{"name": "DESC", "constraints": {
                    "status": "partial",
                    "values_or_ranges_optional": {"status": "complete", "value": False},
                    "allowed_values": {"status": "read_error", "values": []},
                }}],
                "read_errors": {"DESC.allowed_values": "native read failed"},
            },
        }
        with patch.object(BridgeClient, "_request", return_value=partial_response):
            response = self.call({"refno": "=1/2", "attributes": ["DESC"], "include_constraints": True})

        self.assertTrue(response["isError"], response)
        self.assertEqual(response["structuredContent"]["result"], partial_response["result"])
        self.assertEqual(
            response["structuredContent"]["result"]["attributes"][0]["constraints"]["values_or_ranges_optional"],
            {"status": "complete", "value": False},
        )
        self.assertEqual(response["structuredContent"]["result"]["read_errors"], partial_response["result"]["read_errors"])

    def test_default_and_false_preserve_legacy_bridge_request_and_positional_version(self) -> None:
        for options in ({}, {"include_constraints": False}):
            with self.subTest(options=options), patch.object(self.client, "_request", return_value={"ok": True}) as request:
                self.client.element_schema("/PUMP", [], 0, 50, "E3D21", **options)
            request.assert_called_once_with("POST", "/api/v1/element/schema", {
                "refno": "/PUMP", "attributes": [], "offset": 0, "limit": 50,
            }, version="E3D21")

    def test_constraints_require_explicit_unique_attributes_and_forward_normalized_names(self) -> None:
        success = {"ok": True, "result": {"success": True}}
        with patch.object(BridgeClient, "element_schema", return_value=success) as read:
            result = self.call({"refno": " /PUMP ", "include_constraints": True,
                                "attributes": [" pos ", "POS", "heig", "HEIG"]})
        self.assertFalse(result.get("isError", False), result)
        read.assert_called_once_with("/PUMP", ["POS", "HEIG"], 0, 50, include_constraints=True)

    def test_constraint_attribute_limit_counts_distinct_normalized_names(self) -> None:
        attributes = [f":A{index}" for index in range(10)]
        supplied = attributes + [f" {name.lower()} " for name in attributes]
        success = {"ok": True, "result": {"success": True}}
        with patch.object(BridgeClient, "element_schema", return_value=success) as read:
            result = self.call({"refno": " =1/2 ", "include_constraints": True, "attributes": supplied})
        self.assertFalse(result.get("isError", False), result)
        read.assert_called_once_with("=1/2", attributes, 0, 50, include_constraints=True)

    def test_invalid_constraint_flags_or_attribute_sets_never_reach_bridge(self) -> None:
        invalid = [
            {"include_constraints": None},
            {"include_constraints": 0},
            {"include_constraints": 1},
            {"include_constraints": "true", "attributes": ["POS"]},
            {"include_constraints": "false", "attributes": ["POS"]},
            {"include_constraints": [], "attributes": ["POS"]},
            {"include_constraints": {}, "attributes": ["POS"]},
            {"refno": "/PUMP", "include_constraints": True},
            {"refno": "/PUMP", "include_constraints": True, "attributes": []},
            {"refno": "/PUMP", "include_constraints": True, "attributes": None},
            {"refno": "/PUMP", "include_constraints": True, "attributes": [f":A{index}" for index in range(11)]},
        ]
        with patch.object(BridgeClient, "element_schema", return_value={"ok": True}) as read:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    result = self.call(arguments)
                    self.assertTrue(result.get("isError", False), result)
            read.assert_not_called()

    def test_constraint_reads_require_explicit_reference_before_bridge(self) -> None:
        invalid = [None, "", " ", "CE", "OWNER", "PUMP", "/PUMP NAME", "=1", "=1/", 7]
        with patch.object(BridgeClient, "element_schema", return_value={"ok": True}) as read:
            result = self.call({"include_constraints": True, "attributes": ["POS"]})
            self.assertTrue(result.get("isError", False), result)
            for refno in invalid:
                with self.subTest(refno=refno):
                    result = self.call({"refno": refno, "include_constraints": True, "attributes": ["POS"]})
                    self.assertTrue(result.get("isError", False), result)
            read.assert_not_called()

    def test_direct_bridge_rejects_invalid_flags_before_native_request(self) -> None:
        with patch.object(self.client, "_request") as request:
            for value in (None, 0, 1, "true", "false", [], {}):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    self.client.element_schema("/PUMP", ["POS"], 0, 50, include_constraints=value)
            request.assert_not_called()

    def test_direct_bridge_rejects_unbounded_or_implicit_constraint_read(self) -> None:
        invalid = [("/PUMP", []), ("/PUMP", [f":A{index}" for index in range(11)]),
                   ("", ["POS"]), ("CE", ["POS"])]
        with patch.object(self.client, "_request") as request:
            for refno, attributes in invalid:
                with self.subTest(refno=refno, attributes=attributes), self.assertRaises(ValueError):
                    self.client.element_schema(refno, attributes, 0, 50, include_constraints=True)
            request.assert_not_called()

    def test_default_and_explicit_false_preserve_existing_tool_call_shape(self) -> None:
        with patch.object(BridgeClient, "element_schema", return_value={"ok": True}) as read:
            self.call({"refno": "/PUMP", "attributes": ["DESC"]})
            read.assert_called_once_with("/PUMP", ["DESC"], 0, 50)
            read.reset_mock()
            self.call({"refno": "/PUMP", "attributes": ["DESC"], "include_constraints": False})
            read.assert_called_once_with("/PUMP", ["DESC"], 0, 50)

    def test_disabled_constraints_keep_empty_and_more_than_ten_attributes_valid(self) -> None:
        for options in ({}, {"include_constraints": False}):
            for attributes in ([], [f":A{index}" for index in range(11)]):
                with self.subTest(options=options, attributes=attributes), \
                        patch.object(BridgeClient, "element_schema", return_value={"ok": True}) as read:
                    result = self.call({"attributes": attributes, **options})
                self.assertFalse(result.get("isError", False), result)
                read.assert_called_once_with("", attributes, 0, 50)


if __name__ == "__main__":
    unittest.main()

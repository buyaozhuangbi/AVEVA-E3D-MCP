from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server
from test_capture_receipts import _capture_payload


class AttributeBatchToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(Settings(mode="bridge", allow_commands=True))
        self.revision = "ab" * 32
        self.base = {"refno": "=1/2", "values": {"DESC": "frame", " DIAM ": "125.5"}}

    def rpc(self, method: str, params: dict, server=None) -> dict:
        line = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        output = StringIO()
        with patch("sys.stdout", output):
            (server or self.server).serve([line + "\n"])
        response = json.loads(output.getvalue())
        self.assertNotIn("error", response, response)
        return response["result"]

    def call(self, arguments: dict, server=None) -> dict:
        return self.rpc("tools/call", {"name": "e3d_set_attributes", "arguments": arguments}, server)

    def test_discovery_schema_and_operation_receipt(self):
        tools = {item["name"]: item for item in self.rpc("tools/list", {})["tools"]}
        tool = tools["e3d_set_attributes"]
        self.assertEqual(tool["annotations"], {"readOnlyHint": False, "destructiveHint": True,
                                                 "idempotentHint": False, "openWorldHint": False})
        schema = tool["inputSchema"]
        self.assertEqual(schema["required"], ["refno", "values"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["properties"]), {
            "refno", "values", "expected_revision", "dry_run", "capture_after", "capture_width", "capture_height"})
        self.assertEqual(schema["properties"]["values"]["minProperties"], 1)
        self.assertEqual(schema["properties"]["values"]["maxProperties"], 20)
        self.assertEqual(schema["properties"]["expected_revision"]["pattern"], "^[0-9a-fA-F]{64}$")
        self.assertEqual(schema["allOf"][0]["then"], {"required": ["expected_revision"]})
        self.assertEqual(schema["properties"]["capture_after"]["default"], False)
        names = schema["properties"]["values"]["propertyNames"]
        for codepoint in (*range(32), *range(127, 160)):
            for name in (chr(codepoint) + "DESC", "DESC" + chr(codepoint)):
                self.assertIsNotNone(re.search(names["not"]["pattern"], name))
        self.assertIsNotNone(re.fullmatch(names["pattern"], "\u00a0DESC\u2003"))
        self.assertIsNone(re.search(names["not"]["pattern"], "\u00a0DESC\u2003"))

    def test_preview_normalizes_keys_and_uses_one_native_call(self):
        payload = {"ok": True, "version": "E3D21", "result": {
            "success": True, "dry_run": True, "revision": self.revision, "attributes": []}}
        with patch.object(BridgeClient, "set_attributes", return_value=payload) as write:
            response = self.call(self.base)
        write.assert_called_once_with("=1/2", {"DESC": "frame", "DIAM": "125.5"}, dry_run=True)
        self.assertEqual(response["structuredContent"], payload)

    def test_noncontrol_unicode_name_whitespace_is_preserved_as_supported(self):
        with patch.object(BridgeClient, "set_attributes", return_value={"ok": True, "result": {"success": True}}) as write:
            response = self.call({"refno": "/PUMP", "values": {"\u00a0DESC\u2003": "frame"}})
        self.assertFalse(response.get("isError", False))
        write.assert_called_once_with("/PUMP", {"DESC": "frame"}, dry_run=True)

    def test_execute_requires_permission_revision_and_forwards_once(self):
        denied = create_server(Settings(mode="bridge", allow_commands=False))
        with patch.object(BridgeClient, "set_attributes") as write:
            response = self.call({**self.base, "dry_run": False, "expected_revision": self.revision}, denied)
        self.assertTrue(response["isError"])
        self.assertIn("E3D_MCP_ALLOW_COMMANDS", response["content"][0]["text"])
        write.assert_not_called()

        with patch.object(BridgeClient, "set_attributes", return_value={"ok": True, "result": {"success": True}}) as write:
            response = self.call({**self.base, "expected_revision": self.revision.upper(), "dry_run": False})
        self.assertFalse(response.get("isError", False))
        write.assert_called_once_with("=1/2", {"DESC": "frame", "DIAM": "125.5"},
                                      dry_run=False, expected_revision=self.revision)

    def test_invalid_input_never_reaches_native_or_capture(self):
        invalid = [
            {}, {"refno": "/PUMP"}, {"refno": "CE", "values": {"DESC": "x"}},
            {"refno": "/PUMP", "values": {}}, {"refno": "/PUMP", "values": {"DESC": None}},
            {"refno": "/PUMP", "values": {"DESC": 1}},
            {"refno": "/PUMP", "values": {"POS": "1 2 3"}},
            {"refno": "/PUMP", "values": {"DESC": "x", " desc ": "y"}},
            {"refno": "/PUMP", "values": {"DIAM": "1,25"}},
            {"refno": "/PUMP", "values": {"DIAM": "NaN"}},
            {"refno": "/PUMP", "values": {"DIAM": "1e9999"}},
            {**self.base, "extra": True}, {**self.base, "dry_run": 0},
            {**self.base, "dry_run": False},
        ]
        invalid += [{"refno": "/PUMP", "values": {f"DESC{i}": "x" for i in range(21)}}]
        invalid += [{"refno": "/PUMP", "values": {name: "x"}}
                    for codepoint in (*range(32), *range(127, 160))
                    for name in (chr(codepoint) + "DESC", "DESC" + chr(codepoint))]
        invalid += [{**self.base, "expected_revision": token} for token in
                    (None, 1, "", "a" * 63, "g" * 64, " " + self.revision, self.revision + "\n")]
        with patch.object(BridgeClient, "set_attributes") as write, patch.object(BridgeClient, "capture_view") as capture:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self.call(arguments)["isError"])
            write.assert_not_called()
            capture.assert_not_called()

    def test_mock_never_fabricates_native_preflight_or_revision(self):
        server = create_server(Settings(mode="mock", allow_commands=True))
        with patch.object(BridgeClient, "_request") as request:
            response = self.call(self.base, server)
        request.assert_not_called()
        result = response["structuredContent"]["result"]
        self.assertFalse(result["success"])
        self.assertFalse(result["available"])
        self.assertFalse(result["native_validated"])
        self.assertFalse(result["write_attempted"])
        self.assertNotIn("revision", result)

    def test_native_partial_failure_remains_error_and_capture_is_skipped(self):
        payload = {"ok": True, "result": {"success": False, "dry_run": False,
            "write_attempted": True, "write_applied": None, "outcome_uncertain": True,
            "attributes": [{"attribute": "DESC", "status": "verified"},
                           {"attribute": "DIAM", "status": "outcome_unknown"}]}}
        with patch.object(BridgeClient, "set_attributes", return_value=payload) as write, \
                patch.object(BridgeClient, "capture_view") as capture:
            response = self.call({**self.base, "expected_revision": self.revision, "dry_run": False,
                                  "capture_after": True})
        self.assertTrue(response["isError"])
        self.assertEqual(response["structuredContent"]["result"], payload["result"])
        self.assertEqual(response["structuredContent"]["receipt"]["capture"]["reason"], "operation_failed")
        write.assert_called_once()
        capture.assert_not_called()

    def test_confirmed_execution_can_return_one_after_image(self):
        operation = {"ok": True, "result": {"success": True, "dry_run": False,
            "write_attempted": True, "write_applied": True, "readback_verified": True}}
        with patch.object(BridgeClient, "set_attributes", return_value=operation) as write, \
                patch.object(BridgeClient, "capture_view", return_value=_capture_payload()) as capture:
            response = self.call({**self.base, "expected_revision": self.revision, "dry_run": False,
                                  "capture_after": True, "capture_width": 64, "capture_height": 64})
        write.assert_called_once()
        capture.assert_called_once_with(64, 64)
        self.assertFalse(response.get("isError", False))
        self.assertEqual([part["type"] for part in response["content"]], ["text", "image"])
        self.assertEqual(response["structuredContent"]["receipt"]["capture"]["status"], "captured")

    def test_transport_uncertainty_is_not_replayed(self):
        with patch.object(BridgeClient, "set_attributes", side_effect=BridgeError("timeout", transport_error=True)) as write, \
                patch.object(BridgeClient, "capture_view") as capture:
            response = self.call({**self.base, "expected_revision": self.revision, "dry_run": False,
                                  "capture_after": True})
        self.assertTrue(response["isError"])
        self.assertTrue(response["structuredContent"]["outcome_uncertain"])
        self.assertFalse(response["structuredContent"]["retry_safe"])
        write.assert_called_once()
        capture.assert_not_called()

    def test_bridge_posts_same_route_and_revision(self):
        client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        expected = {"ok": True, "result": {"success": True}}
        with patch.object(client, "_request", return_value=expected) as request:
            result = client.set_attributes("/PUMP", {"DESC": "x"}, dry_run=False,
                                           expected_revision=self.revision)
        self.assertIs(result, expected)
        request.assert_called_once_with("POST", "/api/v1/element/attributes/set", {
            "refno": "/PUMP", "values": {"DESC": "x"}, "dry_run": False,
            "expected_revision": self.revision,
        }, version=None)


if __name__ == "__main__":
    unittest.main()

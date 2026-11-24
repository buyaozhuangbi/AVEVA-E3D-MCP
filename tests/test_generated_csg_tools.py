"""Descriptor discovery and dispatch without any model or view writes."""
import json
from io import StringIO
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class GeneratedCsgToolTests(unittest.TestCase):
    def rpc(self, method, params):
        server = create_server(Settings(mode="bridge", allow_commands=False))
        output = StringIO()
        with patch("sys.stdout", output):
            server.serve([json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}) + "\n"])
        return json.loads(output.getvalue())["result"]

    def test_discovery_is_additive_readonly_and_bounded(self):
        tools = {row["name"]: row for row in self.rpc("tools/list", {})["tools"]}
        self.assertEqual(len(tools), 122)
        tool = tools["e3d_generated_csg"]
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["destructiveHint"])
        schema = tool["inputSchema"]
        self.assertEqual(schema["required"], ["refno"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["max_items"]["maximum"], 256)

    def test_failure_reaches_validator_without_any_write_or_image(self):
        failure = {"ok": False, "error": "unsupported source"}
        with patch.object(BridgeClient, "generated_csg", return_value=failure) as read, \
             patch.object(BridgeClient, "update_primitive") as write, \
             patch.object(BridgeClient, "capture_view") as capture:
            result = self.rpc("tools/call", {"name": "e3d_generated_csg", "arguments": {"refno": " =1/2 "}})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"], failure)
        read.assert_called_once_with("=1/2", max_trees=8, max_items=64)
        write.assert_not_called()
        capture.assert_not_called()

    def test_invalid_arguments_do_not_dispatch(self):
        for arguments in ({}, {"refno": ""}, {"refno": "=1/2", "max_items": True},
                          {"refno": "=1/2", "max_trees": 17}, {"refno": "=1/2", "bad": 1}):
            with self.subTest(arguments=arguments), patch.object(BridgeClient, "generated_csg") as read:
                result = self.rpc("tools/call", {"name": "e3d_generated_csg", "arguments": arguments})
                self.assertTrue(result["isError"])
                read.assert_not_called()

    def test_bridge_requires_feature_and_matching_session(self):
        bridge = BridgeClient(Settings(mode="bridge"))
        health = {"ok": True, "connected": True, "session_id": "S", "api_features": ["generated_csg"]}
        with patch.object(bridge, "health", return_value={**health, "api_features": []}), \
             patch.object(bridge, "_request") as request:
            self.assertFalse(bridge.generated_csg("=1/2")["ok"])
            request.assert_not_called()
        response = {"ok": True, "session_id": "S", "result": {"success": True}}
        with patch.object(bridge, "health", return_value=health), \
             patch.object(bridge, "_request", return_value=response) as request:
            self.assertEqual(bridge.generated_csg("=1/2", max_trees=2, max_items=3), response)
            request.assert_called_once_with("GET", "/api/v1/element/generated-csg",
                query={"refno": "=1/2", "max_trees": 2, "max_items": 3}, version=None)
        with patch.object(bridge, "health", return_value=health), \
             patch.object(bridge, "_request", return_value={**response, "session_id": "OTHER"}):
            result = bridge.generated_csg("=1/2")
            self.assertFalse(result["ok"])
            self.assertEqual(result["result"]["trees"], [])


if __name__ == "__main__":
    unittest.main()

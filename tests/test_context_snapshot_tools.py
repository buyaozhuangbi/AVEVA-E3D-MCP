from __future__ import annotations

import json
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class ContextSnapshotToolTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(mode="mock", version="E3D21", allow_commands=False)
        self.server = create_server(self.settings)

    def call(self, arguments=None):
        return self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "e3d_context_snapshot", "arguments": arguments or {}}})["result"]

    def test_catalog_exposes_bounded_read_only_context_workflow(self):
        catalog = self.server._dispatch({"jsonrpc": "2.0", "id": 1,
            "method": "tools/list", "params": {}})["result"]["tools"]
        self.assertEqual(len(catalog), 117)
        tool = next(tool for tool in catalog if tool["name"] == "e3d_context_snapshot")
        self.assertEqual(tool["inputSchema"], {"type": "object", "properties": {}, "additionalProperties": False})
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["destructiveHint"])
        self.assertFalse(tool["annotations"]["openWorldHint"])
        for text in ("health -> project -> health", "not atomic", "session-bound", "future readiness"):
            self.assertIn(text, tool["description"])
        self.assertIn("e3d_context_snapshot", self.server.instructions)

    def test_mock_observation_reports_unknown_without_tool_error(self):
        response = self.call()
        self.assertFalse(response.get("isError", False))
        payload = response["structuredContent"]
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mcp"], {"mode": "mock", "commands_enabled": False})
        result = payload["result"]
        self.assertEqual(result["readiness"]["before"], "unknown")
        self.assertFalse(result["context_complete"])
        self.assertEqual(result["calls"], ["health_before"])
        self.assertEqual(json.loads(response["content"][0]["text"]), payload)

    def test_unknown_arguments_are_rejected_before_workflow(self):
        with patch("aveva_e3d_mcp.tools.context_snapshot") as workflow:
            for arguments in ({"version": "E3D21"}, {"capture_after": True}, {"refno": "/WORLD"}):
                with self.subTest(arguments=arguments):
                    response = self.call(arguments)
                    self.assertTrue(response["isError"])
                    self.assertIn("takes no arguments", response["content"][0]["text"])
            workflow.assert_not_called()

    def test_read_failure_is_error_but_retains_stage_evidence(self):
        failure = {"ok": False, "version": "E3D21", "result": {
            "workflow_completed": False, "context_complete": False,
            "evidence": {"health_before": {"exception": {"message": "disconnected"}}},
            "errors": [{"stage": "health_before", "code": "read_error"}],
        }}
        with patch("aveva_e3d_mcp.tools.context_snapshot", return_value=failure) as workflow:
            response = self.call()
        self.assertTrue(response["isError"])
        self.assertEqual(response["structuredContent"], failure)
        self.assertIs(workflow.call_args.args[1], self.settings)

    def test_real_stdio_lifecycle_admits_context_tool_after_handshake(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "context-test", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "e3d_context_snapshot", "arguments": {}}},
        ]
        output = StringIO()
        with patch("sys.stdout", output):
            self.server.serve((json.dumps(request) + "\n" for request in requests), enforce_lifecycle=True)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([response["id"] for response in responses], [1, 2])
        self.assertFalse(responses[1]["result"].get("isError", False))
        self.assertFalse(responses[1]["result"]["structuredContent"]["result"]["context_complete"])


if __name__ == "__main__":
    unittest.main()

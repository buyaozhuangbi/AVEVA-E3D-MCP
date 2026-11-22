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


class ConnectionTraceToolsTests(unittest.TestCase):
    def _rpc(self, server, method: str, params: dict) -> dict:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        output = StringIO()
        with patch("sys.stdout", output):
            server.serve([request + "\n"])
        response = json.loads(output.getvalue())
        self.assertNotIn("error", response, response)
        return response["result"]

    def _call(self, arguments: dict, settings: Settings | None = None) -> dict:
        server = create_server(settings or Settings(mode="bridge", version="E3D21", allow_commands=False))
        return self._rpc(server, "tools/call", {"name": "e3d_trace_piping_connectivity", "arguments": arguments})

    def test_tool_is_read_only_and_has_bounded_explicit_starts(self) -> None:
        server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))
        tools = {tool["name"]: tool for tool in self._rpc(server, "tools/list", {})["tools"]}
        tool = tools["e3d_trace_piping_connectivity"]
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertEqual(set(tool["inputSchema"]["properties"]), {"starts", "max_hops", "max_nodes"})
        self.assertEqual(tool["inputSchema"]["properties"]["starts"]["maxItems"], 16)
        self.assertEqual(set(tool["inputSchema"]["properties"]["starts"]["items"]["properties"]),
                         {"refno", "point_number", "expected_type"})
        self.assertFalse(tool["inputSchema"]["additionalProperties"])

    def test_validation_rejects_bad_starts_limits_and_extra_fields_before_native_read(self) -> None:
        invalid = [
            {}, {"starts": []}, {"starts": [{"refno": "/PIPE"}]},
            {"starts": [{"refno": "/PIPE", "point_number": True}]},
            {"starts": [{"refno": "=0/2", "point_number": 1}]},
            {"starts": [{"refno": "/PIPE", "point_number": 0}]},
            {"starts": [{"refno": "/PIPE", "point_number": 1}, {"refno": "/pipe", "point_number": 1}]},
            {"starts": [{"refno": "/PIPE", "point_number": 1, "expected_type": "TUBI"},
                         {"refno": "/PIPE", "point_number": 1, "expected_type": "tubi"}]},
            {"starts": [{"refno": "/PIPE", "point_number": 1, "expected_type": "bad type"}]},
            {"starts": [{"refno": "/PIPE", "point_number": 1}], "max_hops": -1},
            {"starts": [{"refno": "/PIPE", "point_number": 1}], "max_hops": 17},
            {"starts": [{"refno": "/PIPE", "point_number": 1}], "max_nodes": 0},
            {"starts": [{"refno": "/PIPE", "point_number": 1}, {"refno": "/PIPE", "point_number": 2}], "max_nodes": 1},
            {"starts": [{"refno": "/PIPE", "point_number": 1}], "unexpected": 1},
            {"starts": [{"refno": "/PIPE", "point_number": 1}] * 17},
        ]
        with patch.object(BridgeClient, "trace_connection_network") as trace:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self._call(arguments)["isError"])
            trace.assert_not_called()

    def test_handler_normalizes_and_forwards_explicit_starts_and_limits(self) -> None:
        native = {"ok": True, "version": "E3D21", "result": {"success": True, "complete": True}}
        args = {"starts": [{"refno": " =1/2 ", "point_number": 3}], "max_hops": 4, "max_nodes": 12}
        with patch.object(BridgeClient, "trace_connection_network", return_value=native) as trace:
            result = self._call(args)
        self.assertFalse(result.get("isError", False))
        self.assertEqual(result["structuredContent"], native)
        trace.assert_called_once_with([{"refno": "=1/2", "point_number": 3}], 4, 12)

        typed_args = {"starts": [
            {"refno": "=1/2", "point_number": 3, "expected_type": "elbo"},
            {"refno": "=1/2", "point_number": 3, "expected_type": "tubi"},
        ]}
        with patch.object(BridgeClient, "trace_connection_network", return_value=native) as trace:
            self._call(typed_args)
        trace.assert_called_once_with([
            {"refno": "=1/2", "point_number": 3, "expected_type": "ELBO"},
            {"refno": "=1/2", "point_number": 3, "expected_type": "TUBI"},
        ], 8, 64)

    def test_bridge_posts_once_and_unsupported_version_does_not_contact_http(self) -> None:
        client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        starts = [{"refno": "=1/2", "point_number": 3}]
        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.trace_connection_network(starts, 4, 12)
        request.assert_called_once_with("POST", "/api/v1/element/connection-trace", {
            "starts": starts, "max_hops": 4, "max_nodes": 12,
        }, version="E3D21")

        typed_starts = [{"refno": "=1/2", "point_number": 2, "expected_type": "TUBI"}]
        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.trace_connection_network(typed_starts)
        request.assert_called_once_with("POST", "/api/v1/element/connection-trace", {
            "starts": typed_starts, "max_hops": 8, "max_nodes": 64,
        }, version="E3D21")

        unsupported = BridgeClient(Settings(mode="bridge", version="E3D31"))
        with patch.object(unsupported, "_request") as request:
            response = unsupported.trace_connection_network(starts)
        request.assert_not_called()
        self.assertEqual(response["result"]["read_errors"][0]["code"], "unsupported_version")

    def test_mock_mode_is_explicitly_unavailable(self) -> None:
        response = self._call({"starts": [{"refno": "/PIPE", "point_number": 1}]},
                              Settings(mode="mock", version="E3D21"))
        self.assertTrue(response["isError"])
        result = response["structuredContent"]["result"]
        self.assertFalse(result["available"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["read_errors"][0]["code"], "native_unavailable")


if __name__ == "__main__":
    unittest.main()

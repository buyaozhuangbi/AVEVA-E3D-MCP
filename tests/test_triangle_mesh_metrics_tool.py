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


class TriangleMeshMetricsToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(Settings(mode="mock"))

    def _rpc(self, method: str, params: dict) -> dict:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        output = StringIO()
        with patch("sys.stdout", output):
            self.server.serve([request + "\n"])
        return json.loads(output.getvalue())["result"]

    def test_tool_is_read_only_and_bounded(self) -> None:
        listed = self._rpc("tools/list", {})["tools"]
        tool = next(item for item in listed if item["name"] == "e3d_measure_triangle_mesh")
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertTrue(tool["annotations"]["idempotentHint"])
        self.assertFalse(tool["annotations"]["destructiveHint"])
        self.assertEqual(tool["inputSchema"]["required"], ["vertices", "triangles"])
        self.assertFalse(tool["inputSchema"]["additionalProperties"])
        self.assertEqual(tool["inputSchema"]["properties"]["vertices"]["maxItems"], 10000)
        self.assertEqual(tool["inputSchema"]["properties"]["triangles"]["maxItems"], 20000)

    def test_returns_measurement_and_validation_without_bridge(self) -> None:
        result = self._rpc("tools/call", {"name": "e3d_measure_triangle_mesh", "arguments": {
            "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
            "triangles": [[0, 1, 2]],
        }})
        self.assertFalse(result.get("isError", False), result)
        payload = result["structuredContent"]
        self.assertEqual(payload["version"], "offline")
        self.assertTrue(payload["result"]["available"])
        self.assertEqual(payload["result"]["bounds"]["max"], [1.0, 1.0, 0.0])
        self.assertEqual(payload["result"]["surface_area"], 0.5)
        self.assertIn("validation", payload["result"])

    def test_rejects_unknown_argument(self) -> None:
        result = self._rpc("tools/call", {"name": "e3d_measure_triangle_mesh", "arguments": {
            "vertices": [], "triangles": [], "extra": True,
        }})
        self.assertTrue(result.get("isError", False))


if __name__ == "__main__":
    unittest.main()

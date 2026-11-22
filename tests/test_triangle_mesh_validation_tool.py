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


class TriangleMeshValidationToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(Settings(mode="bridge", allow_commands=False))

    def _call(self, arguments: dict) -> dict:
        output = StringIO()
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "e3d_validate_triangle_mesh", "arguments": arguments}})
        with patch("sys.stdout", output):
            self.server.serve([request + "\n"])
        return json.loads(output.getvalue())

    def _list(self) -> dict:
        output = StringIO()
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        with patch("sys.stdout", output):
            self.server.serve([request + "\n"])
        return json.loads(output.getvalue())["result"]["tools"]

    def test_schema_is_read_only_bounded_and_self_describing(self):
        tools = {item["name"]: item for item in self._list()}
        tool = tools["e3d_validate_triangle_mesh"]
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertTrue(tool["annotations"]["idempotentHint"])
        self.assertEqual(tool["inputSchema"]["properties"]["vertices"]["maxItems"], 10000)
        self.assertEqual(tool["inputSchema"]["properties"]["triangles"]["maxItems"], 20000)

    def test_validates_locally_without_native_bridge_call(self):
        arguments = {"vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
                     "triangles": [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]}
        with patch.object(BridgeClient, "_request", side_effect=AssertionError("must not call E3D")):
            response = self._call(arguments)
        self.assertNotIn("error", response)
        result = response["result"]["structuredContent"]["result"]
        self.assertTrue(result["valid"])
        self.assertEqual(result["components"][0]["orientation"], "outward")

    def test_rejects_unknown_arguments(self):
        response = self._call({"vertices": [], "triangles": [], "refno": "/NOT-USED"})
        self.assertTrue(response["result"]["isError"])


if __name__ == "__main__":
    unittest.main()

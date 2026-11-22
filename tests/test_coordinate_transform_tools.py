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


class CoordinateTransformToolsTests(unittest.TestCase):
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
        return self._rpc(server, "tools/call", {"name": "e3d_transform_coordinates", "arguments": arguments})

    def test_tool_is_read_only_and_schema_is_bounded(self) -> None:
        server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))
        tools = {tool["name"]: tool for tool in self._rpc(server, "tools/list", {})["tools"]}
        tool = tools["e3d_transform_coordinates"]
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertEqual(set(tool["inputSchema"]["properties"]), {"from_wrt", "to_wrt", "points", "vectors"})
        self.assertFalse(tool["inputSchema"]["additionalProperties"])
        self.assertEqual(tool["inputSchema"]["properties"]["points"]["maxItems"], 128)

    def test_validation_rejects_unknown_frames_and_bad_or_unbounded_coordinates(self) -> None:
        invalid = [
            {"from_wrt": "OWNER", "to_wrt": "WORLD", "points": [[0, 0, 0]]},
            {"from_wrt": "WORLD", "to_wrt": "NOT A FRAME", "points": [[0, 0, 0]]},
            {"from_wrt": "=0/1", "to_wrt": "WORLD", "points": [[0, 0, 0]]},
            {"from_wrt": "=2147483648/1", "to_wrt": "WORLD", "points": [[0, 0, 0]]},
            {"from_wrt": "WORLD", "to_wrt": "WORLD"},
            {"from_wrt": "WORLD", "to_wrt": "WORLD", "points": [[True, 0, 0]]},
            {"from_wrt": "WORLD", "to_wrt": "WORLD", "points": [[0, float("nan"), 0]]},
            {"from_wrt": "WORLD", "to_wrt": "WORLD", "vectors": [[0, 0]]},
            {"from_wrt": "WORLD", "to_wrt": "WORLD", "points": [[0, 0, 0]], "extra": 1},
            {"from_wrt": "WORLD", "to_wrt": "WORLD", "points": [[0, 0, 0]] * 129},
        ]
        with patch.object(BridgeClient, "transform_coordinates") as transform:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self._call(arguments)["isError"])
            transform.assert_not_called()

    def test_bridge_posts_one_bounded_payload_to_selected_version(self) -> None:
        arguments = {"from_wrt": " /SOURCE ", "to_wrt": "WORLD", "points": [[1, 2, 3]], "vectors": [[0, 0, 1]]}
        native = {"ok": True, "version": "E3D21", "result": {"success": True, "complete": True}}
        with patch.object(BridgeClient, "transform_coordinates", return_value=native) as transform:
            result = self._call(arguments)
        self.assertFalse(result.get("isError", False))
        self.assertEqual(result["structuredContent"], native)
        transform.assert_called_once_with("/SOURCE", "WORLD", [[1.0, 2.0, 3.0]], [[0.0, 0.0, 1.0]])

    def test_bridge_method_uses_single_post_and_unsupported_versions_do_not_contact_http(self) -> None:
        client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.transform_coordinates("/SOURCE", "WORLD", [[1.0, 2.0, 3.0]], [])
        request.assert_called_once_with("POST", "/api/v1/coordinates/transform", {
            "from_wrt": "/SOURCE", "to_wrt": "WORLD", "points": [[1.0, 2.0, 3.0]], "vectors": [],
        }, version="E3D21")

        unsupported = BridgeClient(Settings(mode="bridge", version="E3D31"))
        with patch.object(unsupported, "_request") as request:
            result = unsupported.transform_coordinates("WORLD", "WORLD", [[0.0, 0.0, 0.0]], [])
        request.assert_not_called()
        self.assertEqual(result["result"]["read_errors"][0]["code"], "unsupported_version")

    def test_mock_mode_is_explicitly_unavailable(self) -> None:
        result = self._call({"from_wrt": "WORLD", "to_wrt": "WORLD", "vectors": [[1, 0, 0]]},
                            Settings(mode="mock", version="E3D21"))
        self.assertTrue(result["isError"])
        native = result["structuredContent"]["result"]
        self.assertFalse(native["available"])
        self.assertFalse(native["complete"])
        self.assertEqual(native["read_errors"][0]["code"], "native_unavailable")


if __name__ == "__main__":
    unittest.main()

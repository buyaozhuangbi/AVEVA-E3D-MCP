from __future__ import annotations

import json
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class GraphicalSelectionToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(mode="bridge", version="E3D21", allow_commands=False)
        self.server = create_server(self.settings)

    def rpc(self, method: str, params: dict, *, server=None) -> dict:
        response = (server or self.server)._dispatch({"jsonrpc": "2.0", "id": 1,
                                                     "method": method, "params": params})
        self.assertNotIn("error", response, response)
        return response["result"]

    def call(self, arguments: dict, *, server=None) -> dict:
        return self.rpc("tools/call", {"name": "e3d_graphical_selection", "arguments": arguments}, server=server)

    def test_schema_is_bounded_strict_and_read_only(self) -> None:
        tool = next(item for item in self.rpc("tools/list", {})["tools"]
                    if item["name"] == "e3d_graphical_selection")
        schema = tool["inputSchema"]
        self.assertEqual(set(schema["properties"]), {"limit"})
        self.assertEqual(schema["properties"]["limit"], {"type": "integer", "minimum": 1, "maximum": 200, "default": 50})
        self.assertFalse(schema["additionalProperties"])
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["destructiveHint"])
        self.assertTrue(tool["annotations"]["idempotentHint"])
        for phrase in ("native interaction selection", "current view", "tree selection", "highlighted", "last picked", "empty selection"):
            self.assertIn(phrase, tool["description"])

    def test_default_and_both_limit_boundaries_forward_without_command_permission(self) -> None:
        with patch.object(BridgeClient, "graphical_selection", return_value={"ok": True}) as selection:
            for arguments, expected in (({}, 50), ({"limit": 1}, 1), ({"limit": 200}, 200)):
                with self.subTest(arguments=arguments):
                    response = self.call(arguments)
                    self.assertFalse(response.get("isError", False), response)
                    selection.assert_called_with(expected)
        self.assertEqual(selection.call_count, 3)

    def test_invalid_and_unsupported_arguments_never_reach_bridge(self) -> None:
        invalid = [{"limit": value} for value in (None, True, False, 0, -1, 201, 1.0, "50", [], {})]
        invalid.extend({key: value} for key, value in (
            ("refno", "/PUMP"), ("view", "current"), ("dry_run", True), ("capture_after", False),
            ("version", "E3D21"), ("cursor", "opaque"), ("all_highlights", True),
        ))
        with patch.object(BridgeClient, "graphical_selection") as selection:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self.call(arguments).get("isError"))
            selection.assert_not_called()

    def test_native_partial_failure_keeps_elements_unknown_count_and_read_errors(self) -> None:
        native = {"ok": True, "result": {"success": False, "available": True, "complete": False,
                  "scope": "native interaction selection", "elements": [{"refno": "=1/2", "type": "BOX"}],
                  "total_count": None, "read_errors": [{"path": "selection[1]", "message": "Native read failed"}]}}
        with patch.object(BridgeClient, "graphical_selection", return_value=native):
            response = self.call({})
        self.assertTrue(response["isError"])
        self.assertEqual(response["structuredContent"], native)
        self.assertEqual(json.loads(response["content"][0]["text"]), native)

    def test_known_empty_selection_and_truncated_selection_retain_native_semantics(self) -> None:
        for native in (
            {"ok": True, "result": {"success": True, "available": True, "complete": True,
                                     "elements": [], "total_count": 0, "truncated": False, "read_errors": []}},
            {"ok": True, "result": {"success": True, "available": True, "complete": False,
                                     "elements": [{"refno": "=1/2"}], "total_count": 2, "truncated": True, "read_errors": []}},
        ):
            with self.subTest(native=native), patch.object(BridgeClient, "graphical_selection", return_value=native):
                response = self.call({"limit": 1})
            self.assertFalse(response.get("isError", False), response)
            self.assertEqual(response["structuredContent"], native)

    def test_bridge_uses_get_query_and_optional_positional_version(self) -> None:
        client = BridgeClient(self.settings)
        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.graphical_selection(200, "E3D21")
        request.assert_called_once_with("GET", "/api/v1/view/graphical-selection", query={"limit": "200"}, version="E3D21")
        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.graphical_selection()
        self.assertEqual(request.call_args.kwargs["query"], {"limit": "50"})

    def test_http_query_contains_only_the_limit(self) -> None:
        client = BridgeClient(self.settings)
        with patch("urllib.request.urlopen", return_value=BytesIO(b'{"ok":true,"result":{"success":true}}')) as urlopen:
            client.graphical_selection(7)
        request = urlopen.call_args.args[0]
        parsed = urlparse(request.full_url)
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(parsed.path, "/api/v1/view/graphical-selection")
        self.assertEqual(parse_qs(parsed.query), {"limit": ["7"]})
        self.assertIsNone(request.data)

    def test_mock_is_unavailable_and_does_not_fabricate_empty_elements(self) -> None:
        server = create_server(Settings(mode="mock", version="E3D21"))
        with patch.object(BridgeClient, "_request") as request:
            response = self.call({}, server=server)
        self.assertTrue(response["isError"])
        selection = response["structuredContent"]["result"]
        self.assertFalse(selection["available"])
        self.assertFalse(selection["complete"])
        self.assertEqual(selection["limit"], 50)
        self.assertNotIn("elements", selection)
        self.assertNotIn("total_count", selection)
        self.assertEqual(selection["read_errors"][0]["code"], "native_unavailable")
        request.assert_not_called()

    def test_native_fault_keeps_diagnostics_without_retry_mutation_or_capture(self) -> None:
        diagnostics = {"state": {"database_ready": False, "recovery_required": True}}
        with patch.object(BridgeClient, "_request", side_effect=BridgeError("native fault", diagnostics=diagnostics)) as request, \
                patch.object(BridgeClient, "command") as command, patch.object(BridgeClient, "capture_view") as capture:
            response = self.call({})
        self.assertTrue(response["isError"])
        self.assertEqual(response["structuredContent"]["state"], diagnostics["state"])
        self.assertNotIn("outcome_uncertain", json.dumps(response))
        request.assert_called_once()
        command.assert_not_called()
        capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()

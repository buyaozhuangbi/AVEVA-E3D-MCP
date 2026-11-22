from __future__ import annotations

import json
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class GeometryVolumeToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(Settings(mode="bridge", allow_commands=False))

    def _rpc(self, method: str, params: dict, *, server=None) -> dict:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        output = StringIO()
        with patch("sys.stdout", output):
            (server or self.server).serve([request + "\n"])
        response = json.loads(output.getvalue())
        self.assertNotIn("error", response, response)
        return response["result"]

    def _call(self, arguments: dict, *, server=None) -> dict:
        return self._rpc("tools/call", {"name": "e3d_geometry_volume", "arguments": arguments}, server=server)

    def test_volume_schema_requires_explicit_reference_and_exposes_only_read_controls(self) -> None:
        tools = {tool["name"]: tool for tool in self._rpc("tools/list", {})["tools"]}
        self.assertEqual(len(tools), 114)
        volume = tools["e3d_geometry_volume"]
        self.assertEqual(volume["annotations"], {
            "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False,
        })
        schema = volume["inputSchema"]
        self.assertEqual(schema["required"], ["refno"])
        self.assertEqual(set(schema["properties"]), {"refno", "scope"})
        self.assertEqual(schema["properties"]["scope"]["anyOf"][0]["enum"], ["element", "members"])
        self.assertIn("pattern", schema["properties"]["scope"]["anyOf"][1])
        self.assertEqual(schema["properties"]["scope"]["default"], "element")
        self.assertFalse(schema["additionalProperties"])

    def test_read_defaults_to_element_without_write_permission_and_preserves_native_data(self) -> None:
        # Response values are deliberately opaque to Python; native fields and
        # units must survive without assumed conversion or inferred net volume.
        payload = {"ok": True, "version": "E3D21", "result": {
            "success": True, "available": True, "gross": {"available": True, "value": 120},
            "net": {"available": True, "value": 80}, "unit": "native-volume-unit",
        }}
        with patch.object(BridgeClient, "geometry_volume", return_value=payload) as read:
            result = self._call({"refno": " =123/456 "})
        read.assert_called_once_with("=123/456", "element")
        self.assertFalse(result.get("isError", False), result)
        self.assertEqual(result["structuredContent"], payload)

    def test_negative_native_values_preserve_success_and_nonapplicable_nulls_without_owner_inference(self) -> None:
        payload = {"ok": True, "result": {
            "success": True, "read_complete": True,
            "volume_pair_applicable": False, "volume_pair_coherent": None,
            "volume_pair_tolerance_mm3": None, "removed_volume_mm3": None,
            "gross": {"available": True, "value_mm3": 0},
            "net": {"available": True, "value_mm3": 12844457.566329282}, "read_errors": {},
        }}
        with patch.object(BridgeClient, "geometry_volume", return_value=payload) as read:
            result = self._call({"refno": "=2000000003/44"})
        read.assert_called_once_with("=2000000003/44", "element")
        self.assertFalse(result.get("isError", False), result)
        self.assertEqual(result["structuredContent"], payload)
        self.assertEqual(json.loads(result["content"][0]["text"]), payload)
        self.assertNotIn("cut_volume_mm3", json.dumps(result))
        self.assertNotIn("parent_removed_volume", json.dumps(result))

    def test_explicit_members_scope_is_forwarded_with_no_mutation_or_view_calls(self) -> None:
        payload = {"ok": True, "result": {"success": True, "scope": "members"}}
        with patch.object(BridgeClient, "geometry_volume", return_value=payload) as read, \
                patch.object(BridgeClient, "command") as command, patch.object(BridgeClient, "capture_view") as capture, \
                patch.object(BridgeClient, "set_drawlist_visual") as membership:
            result = self._call({"refno": "/PUMP/BODY", "scope": " MEMBERS "})
        read.assert_called_once_with("/PUMP/BODY", "members")
        command.assert_not_called()
        capture.assert_not_called()
        membership.assert_not_called()
        self.assertEqual(result["structuredContent"], payload)

    def test_invalid_references_scopes_and_extra_controls_never_reach_native(self) -> None:
        invalid = [{}]
        invalid.extend({"refno": value} for value in (
            None, True, 1, [], "", " ", "CE", "OWNER", "WORLD", "/", "=x/2", "=1/2/3", "/PUMP\nDELETE CE",
        ))
        invalid.extend({"refno": "/PUMP", "scope": value} for value in (
            None, True, 1, [], "", "ONLY", "all", "world",
        ))
        invalid.extend({"refno": "/PUMP", key: value} for key, value in (
            ("dry_run", True), ("capture_after", False), ("capture_width", 64), ("capture_height", 64),
            ("wrt", "WORLD"), ("command", "Q VOLUME"), ("limit", 10), ("fit", True),
        ))
        with patch.object(BridgeClient, "geometry_volume") as read:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self._call(arguments)["isError"])
        read.assert_not_called()

    def test_partial_failure_preserves_available_values_errors_and_nulls(self) -> None:
        payload = {"ok": True, "result": {
            "success": False, "partial": True, "gross": {"available": True, "value": 125},
            "net": {"available": False, "value": None}, "errors": {"net": "SDK geometry unavailable"},
        }}
        with patch.object(BridgeClient, "geometry_volume", return_value=payload):
            result = self._call({"refno": "/PUMP", "scope": "members"})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"], payload)
        self.assertIsNone(result["structuredContent"]["result"]["net"]["value"])

    def test_read_transport_failure_never_claims_possible_mutation_or_replays(self) -> None:
        with patch.object(BridgeClient, "geometry_volume", side_effect=BridgeError("connection closed", transport_error=True)) as read, \
                patch.object(BridgeClient, "capture_view") as capture:
            result = self._call({"refno": "/PUMP"})
        read.assert_called_once_with("/PUMP", "element")
        capture.assert_not_called()
        self.assertTrue(result["isError"])
        serialized = json.dumps(result)
        self.assertNotIn("outcome_uncertain", serialized)
        self.assertNotIn("retry_safe", serialized)
        self.assertNotIn("receipt", serialized)

    def test_mock_reports_unavailable_without_fabricating_volumes_or_units(self) -> None:
        mock = create_server(Settings(mode="mock", version="E3D21"))
        for scope in ("element", "members"):
            with self.subTest(scope=scope):
                response = self._call({"refno": "/PUMP", "scope": scope}, server=mock)
                self.assertTrue(response["isError"])
                metadata = response["structuredContent"]
                self.assertEqual(metadata["version"], "E3D21")
                result = metadata["result"]
                self.assertFalse(result["success"])
                self.assertFalse(result["available"])
                self.assertEqual(result["source"], "mock")
                for field in ("gross", "net", "gross_volume", "net_volume", "unit", "receipt"):
                    self.assertNotIn(field, result)
                self.assertFalse(any(part["type"] == "image" for part in response["content"]))

    def test_bridge_routes_preserve_scope_and_selected_instance(self) -> None:
        client = BridgeClient(Settings(mode="bridge", version="E3D21"))
        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.geometry_volume("=123/456", version="E3D21")
            request.assert_called_once_with("GET", "/api/v1/element/volume",
                                            query={"refno": "=123/456", "scope": "element"}, version="E3D21")
            request.reset_mock()
            client.geometry_volume("/PUMP", "members", version="E3D21")
            request.assert_called_once_with("GET", "/api/v1/element/volume",
                                            query={"refno": "/PUMP", "scope": "members"}, version="E3D21")


if __name__ == "__main__":
    unittest.main()

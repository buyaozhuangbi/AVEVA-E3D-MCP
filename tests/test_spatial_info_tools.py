from __future__ import annotations

from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


# Actual structuredContent from pump-guard-mount-executed-20261116.json's
# final successful native read. Keep the regression independent of live files.
PAVE_RESPONSE = {"ok": True, "version": "E3D21", "result": {
    "success": True, "complete": False, "source": "E3D SDK WRT qualifier",
    "refno": "=2000000001/3956", "type": "PAVE",
    "frame": {"requested": "=2000000001/3954", "refno": "=2000000001/3954",
              "type": "PANE", "name": "/CODEX-PUMP-NEXT-GUARD-MOUNT-S-HEX"},
    "position": {"unit": "mm", "x": 9.5, "y": 5.484827557301445, "z": 0.0, "available": True},
    "orientation": {"available": False, "reason": "attribute_not_supported"}, "errors": {},
}}
ORIENTATION = {"available": True, "x_axis": [1.0, 0.0, 0.0],
               "y_axis": [0.0, 1.0, 0.0], "z_axis": [0.0, 0.0, 1.0]}


class SpatialInfoToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = BridgeClient(Settings(mode="bridge", version="E3D21", allow_commands=False))
        self.server = create_server(self.client.settings)

    def _rpc(self, method: str, params: dict) -> dict:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        output = StringIO()
        with patch("sys.stdout", output):
            self.server.serve([request + "\n"])
        return json.loads(output.getvalue())["result"]

    def _read(self, response: dict) -> dict:
        with patch.object(self.client, "_request", return_value=response) as read:
            result = self.client.spatial_info("=2000000001/3956", "=2000000001/3954", version="E3D21")
        read.assert_called_once_with("GET", "/api/v1/element/spatial",
                                    query={"refno": "=2000000001/3956", "wrt": "=2000000001/3954"}, version="E3D21")
        return result

    def test_actual_pave_position_is_complete_without_orientation_or_mcp_error(self) -> None:
        original = deepcopy(PAVE_RESPONSE)
        with patch.object(BridgeClient, "_request", return_value=original) as read:
            result = self._rpc("tools/call", {"name": "e3d_spatial_info", "arguments": {
                "refno": "=2000000001/3956", "wrt": "=2000000001/3954"}})
        read.assert_called_once()
        self.assertFalse(result.get("isError", False))
        spatial = result["structuredContent"]["result"]
        self.assertTrue(spatial.pop("position_complete"))
        self.assertFalse(spatial.pop("orientation_complete"))
        self.assertEqual(result["structuredContent"], PAVE_RESPONSE)
        self.assertEqual(original, PAVE_RESPONSE)

    def test_full_transform_marks_both_fields_complete(self) -> None:
        response = deepcopy(PAVE_RESPONSE)
        response["result"].update(complete=True, orientation=deepcopy(ORIENTATION))
        result = self._read(response)["result"]
        self.assertTrue(result["complete"])
        self.assertTrue(result["position_complete"])
        self.assertTrue(result["orientation_complete"])

    def test_sdk_failure_preserves_other_field_and_mcp_error(self) -> None:
        for failed, remaining in (("position", "orientation"), ("orientation", "position")):
            with self.subTest(failed=failed):
                response = deepcopy(PAVE_RESPONSE)
                native = response["result"]
                native["orientation"] = deepcopy(ORIENTATION)
                native[failed] = {"available": False, "reason": "read_failed", "error": "SDK read failed"}
                native["success"] = False
                native["errors"] = {failed: "SDK read failed"}
                before = deepcopy(response)
                with patch.object(BridgeClient, "_request", return_value=response):
                    result = self._rpc("tools/call", {"name": "e3d_spatial_info", "arguments": {}})
                self.assertTrue(result["isError"])
                spatial = result["structuredContent"]["result"]
                self.assertFalse(spatial.pop(failed + "_complete"))
                self.assertTrue(spatial.pop(remaining + "_complete"))
                self.assertEqual(result["structuredContent"], before)
                self.assertEqual(response, before)

    def test_unavailable_or_malformed_fields_never_gain_completeness(self) -> None:
        for field in ("position", "orientation"):
            for value in (None, False, [], "available"):
                with self.subTest(field=field, value=value):
                    response = deepcopy(PAVE_RESPONSE)
                    response["result"][field] = value
                    self.assertFalse(self._read(response)["result"][field + "_complete"])
            for available in (None, False, 0, 1, "true", [], {}):
                with self.subTest(field=field, available=available):
                    response = deepcopy(PAVE_RESPONSE)
                    response["result"]["orientation"] = deepcopy(ORIENTATION)
                    response["result"][field]["available"] = available
                    self.assertFalse(self._read(response)["result"][field + "_complete"])

    def test_invalid_position_components_or_units_do_not_claim_completion(self) -> None:
        for component in (None, True, "9.5", [], float("nan"), float("inf"), -float("inf")):
            with self.subTest(component=component):
                response = deepcopy(PAVE_RESPONSE)
                response["result"]["position"]["x"] = component
                result = self._read(response)["result"]
                self.assertFalse(result["position_complete"])
                self.assertTrue(result["position"]["available"])
                self.assertFalse(result["complete"])
        for unit in (None, "m", "MM", 1):
            response = deepcopy(PAVE_RESPONSE)
            response["result"]["position"]["unit"] = unit
            self.assertFalse(self._read(response)["result"]["position_complete"])
        response = deepcopy(PAVE_RESPONSE)
        del response["result"]["position"]["z"]
        self.assertFalse(self._read(response)["result"]["position_complete"])

    def test_invalid_orientation_vectors_do_not_claim_completion(self) -> None:
        for vector in (None, [], [1, 0], [1, 0, 0, 0], "1,0,0", [True, 0, 0],
                       ["1", 0, 0], [float("nan"), 0, 0], [1, float("inf"), 0]):
            with self.subTest(vector=vector):
                response = deepcopy(PAVE_RESPONSE)
                response["result"]["orientation"] = deepcopy(ORIENTATION)
                response["result"]["orientation"]["y_axis"] = vector
                result = self._read(response)["result"]
                self.assertFalse(result["orientation_complete"])
                self.assertTrue(result["position_complete"])

    def test_field_errors_override_legacy_availability_without_hiding_diagnostics(self) -> None:
        for field in ("position", "orientation"):
            response = deepcopy(PAVE_RESPONSE)
            response["result"]["orientation"] = deepcopy(ORIENTATION)
            response["result"]["errors"] = {field: "inconsistent legacy field"}
            result = self._read(response)["result"]
            self.assertFalse(result[field + "_complete"])
            self.assertEqual(result["errors"], response["result"]["errors"])
            self.assertEqual(result[field], response["result"][field])

    def test_native_completeness_and_extensions_are_preserved(self) -> None:
        response = deepcopy(PAVE_RESPONSE)
        response["result"].update(position_complete=False, orientation_complete=None,
                                  extension={"diagnostic": "retain"})
        self.assertEqual(self._read(response), response)

    def test_missing_fields_fail_closed_without_inventing_native_complete(self) -> None:
        response = {"ok": True, "result": {"success": True}}
        result = self._read(response)["result"]
        self.assertEqual(result, {"success": True, "position_complete": False, "orientation_complete": False})
        for result in (None, [], "unexpected"):
            response = {"ok": True, "result": result}
            self.assertEqual(self._read(response), response)

    def test_mock_exposes_both_flags_without_claiming_native_source(self) -> None:
        result = BridgeClient(Settings(mode="mock")).spatial_info("/EXAMPLE", "WORLD")["result"]
        self.assertEqual(result["source"], "mock")
        self.assertTrue(result["position_complete"])
        self.assertTrue(result["orientation_complete"])
        self.assertTrue(result["complete"])

    def test_tool_description_explains_position_only_checks_and_is_read_only(self) -> None:
        definition = next(tool for tool in self._rpc("tools/list", {})["tools"]
                          if tool["name"] == "e3d_spatial_info")
        for term in ("position_complete", "orientation_complete", "PAVE", "complete=false", "errors"):
            self.assertIn(term, definition["description"])
        self.assertTrue(definition["annotations"]["readOnlyHint"])
        self.assertEqual(set(definition["inputSchema"]["properties"]), {"refno", "wrt"})

    def test_transport_failure_is_not_retried_or_converted_to_available(self) -> None:
        with patch.object(self.client, "_request", side_effect=BridgeError("offline", transport_error=True)) as read:
            with self.assertRaises(BridgeError):
                self.client.spatial_info("/EXAMPLE", "WORLD")
        read.assert_called_once()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class MeasureElementsBatchToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(mode="bridge", version="E3D21", allow_commands=False)
        self.server = create_server(self.settings)

    def rpc(self, method: str, params: dict, *, server=None) -> dict:
        response = (server or self.server)._dispatch({"jsonrpc": "2.0", "id": 1,
                                                     "method": method, "params": params})
        self.assertNotIn("error", response, response)
        return response["result"]

    def call(self, arguments: dict, *, server=None) -> dict:
        return self.rpc("tools/call", {"name": "e3d_measure_elements_batch", "arguments": arguments}, server=server)

    def test_schema_is_bounded_strict_and_read_only(self) -> None:
        tool = next(item for item in self.rpc("tools/list", {})["tools"]
                    if item["name"] == "e3d_measure_elements_batch")
        schema = tool["inputSchema"]
        self.assertEqual(set(schema["properties"]), {"refnos", "measurements", "wrt", "volume_scope"})
        self.assertEqual(schema["required"], ["refnos"])
        self.assertFalse(schema["additionalProperties"])
        refs = schema["properties"]["refnos"]
        self.assertEqual((refs["minItems"], refs["maxItems"], refs["uniqueItems"]), (1, 32, True))
        measurements = schema["properties"]["measurements"]
        self.assertEqual(measurements["items"]["enum"], ["spatial", "bounds", "volume"])
        self.assertEqual((measurements["minItems"], measurements["maxItems"], measurements["uniqueItems"]), (1, 3, True))
        self.assertEqual(measurements["default"], ["spatial", "bounds", "volume"])
        self.assertEqual(schema["properties"]["wrt"]["default"], "WORLD")
        self.assertEqual(schema["properties"]["volume_scope"]["default"], "element")
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["destructiveHint"])
        self.assertTrue(tool["annotations"]["idempotentHint"])
        for phrase in ("one E3D UI callback", "database lock", "SDK native (unverified)", "PAVE"):
            self.assertIn(phrase, tool["description"])

    def test_defaults_are_forwarded_without_command_permission(self) -> None:
        with patch.object(BridgeClient, "measure_elements_batch", return_value={"ok": True}) as measure:
            response = self.call({"refnos": [" /PUMP ", " =123/45 "]})
        self.assertFalse(response.get("isError", False), response)
        measure.assert_called_once_with(["/PUMP", "=123/45"], ["spatial", "bounds", "volume"], "WORLD", "element")

    def test_maximum_batch_and_measurement_order_are_preserved(self) -> None:
        refnos = [f"/PUMP-{index}" for index in range(32)]
        with patch.object(BridgeClient, "measure_elements_batch", return_value={"ok": True}) as measure:
            response = self.call({"refnos": refnos, "measurements": ["volume", "spatial"],
                                  "wrt": " /FRAME ", "volume_scope": "members"})
        self.assertFalse(response.get("isError", False), response)
        measure.assert_called_once_with(refnos, ["volume", "spatial"], "/FRAME", "members")

    def test_special_frames_normalize_and_explicit_numeric_frames_are_preserved(self) -> None:
        with patch.object(BridgeClient, "measure_elements_batch", return_value={"ok": True}) as measure:
            for supplied, expected in ((" owner ", "OWNER"), ("world", "WORLD"), (" =123/0 ", "=123/0")):
                with self.subTest(wrt=supplied):
                    self.assertFalse(self.call({"refnos": ["/PUMP"], "wrt": supplied}).get("isError", False))
                    measure.assert_called_with(["/PUMP"], ["spatial", "bounds", "volume"], expected, "element")

    def test_invalid_inputs_never_reach_the_bridge(self) -> None:
        invalid = [{}, {"refnos": []}, {"refnos": [f"/E{i}" for i in range(33)]}]
        invalid.extend({"refnos": value} for value in (None, "/PUMP", {}, True))
        invalid.extend({"refnos": [value]} for value in (None, 1, True, "", " ", "/", "PUMP", "=1", "=x/2", "=1/-2", "/A B", "/A\x7f"))
        invalid.extend({"refnos": value} for value in (["/PUMP", "/PUMP"], [" /Pump ", "/pump"], ["=1/2", " =1/2 "]))
        base = {"refnos": ["/PUMP"]}
        invalid.extend({**base, "measurements": value} for value in (
            None, "spatial", {}, [], ["spatial"] * 2, ["spatial", "bounds", "volume", "spatial"],
            ["SPATIAL"], ["spatial "], ["unknown"], [True], [None], [["spatial"]],
        ))
        invalid.extend({**base, "wrt": value} for value in (None, 1, True, "", "CE", "FRAME", "/A B", "=1/no"))
        invalid.extend({**base, "volume_scope": value} for value in (None, 1, True, "", "all", "ELEMENT", " members "))
        invalid.extend({**base, key: value} for key, value in (
            ("dry_run", True), ("capture_after", False), ("capture_width", 100), ("version", "E3D21"), ("limit", 5),
        ))
        with patch.object(BridgeClient, "measure_elements_batch") as measure:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self.call(arguments).get("isError"))
            measure.assert_not_called()

    def test_partial_rows_and_failed_measurements_preserve_native_diagnostics(self) -> None:
        native = {"ok": True, "result": {"success": False, "complete": False,
                  "rows": [{"requested_refno": "/GOOD", "success": True,
                            "bounds": {"available": True, "coordinate_frame": "SDK native (unverified)", "min": [0, 0, 0], "max": [1, 1, 1]}},
                           {"requested_refno": "/BAD", "success": False,
                            "volume": {"gross": {"available": True, "value_mm3": 100}, "net": {"available": False}},
                            "read_errors": {"NVOL": "SDK read failed"}}],
                  "read_errors": {"rows[1].volume.NVOL": "SDK read failed"}}}
        with patch.object(BridgeClient, "measure_elements_batch", return_value=native):
            response = self.call({"refnos": ["/GOOD", "/BAD"]})
        self.assertTrue(response["isError"])
        self.assertEqual(response["structuredContent"], native)
        self.assertEqual(json.loads(response["content"][0]["text"]), native)

    def test_negative_read_completeness_is_distinct_from_material_removal(self) -> None:
        native = {"ok": True, "result": {"success": True, "complete": True, "rows": [{
            "refno": "=2000000003/44", "success": True, "complete": True,
            "measurements": {"volume": {"success": True, "complete": True, "result": {
                "success": True, "read_complete": True, "volume_pair_applicable": False,
                "volume_pair_coherent": None, "removed_volume_mm3": None,
                "gross": {"available": True, "value_mm3": 0},
                "net": {"available": True, "value_mm3": 12844457.566329282}}}}}]}}
        with patch.object(BridgeClient, "measure_elements_batch", return_value=native) as measure:
            result = self.call({"refnos": ["=2000000003/44"], "measurements": ["volume"]})
        measure.assert_called_once_with(["=2000000003/44"], ["volume"], "WORLD", "element")
        self.assertFalse(result.get("isError", False), result)
        self.assertEqual(result["structuredContent"], native)

    def test_unsupported_pave_orientation_can_be_successful_but_incomplete(self) -> None:
        native = {"ok": True, "result": {"success": True, "complete": False,
                  "rows": [{"refno": "=1/2", "type": "PAVE", "success": True, "complete": False,
                            "spatial": {"success": True, "complete": False, "position_complete": True,
                                        "orientation_complete": False, "position": {"available": True, "unit": "mm", "x": 1, "y": 2, "z": 0},
                                        "orientation": {"available": False, "reason": "attribute_not_supported"}, "errors": {}}}]}}
        with patch.object(BridgeClient, "measure_elements_batch", return_value=native):
            response = self.call({"refnos": ["=1/2"], "measurements": ["spatial"]})
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"], native)

    def test_bridge_posts_ordered_payload_and_preserves_positional_version(self) -> None:
        client = BridgeClient(self.settings)
        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.measure_elements_batch(["/B", "=123/45"], ["volume", "bounds"], "OWNER", "members", "E3D21")
        request.assert_called_once_with("POST", "/api/v1/elements/measure", {
            "refnos": ["/B", "=123/45"], "measurements": ["volume", "bounds"], "wrt": "OWNER", "volume_scope": "members",
        }, version="E3D21")

    def test_http_uses_json_body_and_the_assigned_version(self) -> None:
        client = BridgeClient(self.settings)
        with patch("urllib.request.urlopen", return_value=BytesIO(b'{"ok":true,"result":{"success":true}}')) as urlopen:
            response = client.measure_elements_batch(["/PUMP"], ["spatial"])
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertTrue(request.full_url.endswith("/api/v1/elements/measure"))
        self.assertEqual(json.loads(request.data), {"refnos": ["/PUMP"], "measurements": ["spatial"], "wrt": "WORLD", "volume_scope": "element"})
        self.assertEqual(response["version"], "E3D21")

    def test_mock_is_unavailable_without_fabricating_rows_or_measurements(self) -> None:
        server = create_server(Settings(mode="mock", version="E3D21"))
        with patch.object(BridgeClient, "_request") as request:
            response = self.call({"refnos": ["/PUMP"]}, server=server)
        self.assertTrue(response["isError"])
        native = response["structuredContent"]["result"]
        self.assertFalse(native["available"])
        self.assertFalse(native["complete"])
        self.assertNotIn("rows", native)
        self.assertNotIn("values", native)
        self.assertEqual(native["requested_refnos"], ["/PUMP"])
        self.assertEqual(native["read_errors"][0]["code"], "native_unavailable")
        request.assert_not_called()

    def test_transport_fault_preserves_state_without_retries_writes_or_capture(self) -> None:
        fault = {"state": {"database_ready": False, "recovery_required": True}}
        with patch.object(BridgeClient, "_request", side_effect=BridgeError("closed", transport_error=True, diagnostics=fault)) as request, \
                patch.object(BridgeClient, "command") as command, patch.object(BridgeClient, "capture_view") as capture:
            response = self.call({"refnos": ["/PUMP"]})
        self.assertTrue(response["isError"])
        self.assertEqual(response["structuredContent"]["state"], fault["state"])
        self.assertNotIn("outcome_uncertain", json.dumps(response))
        request.assert_called_once()
        command.assert_not_called()
        capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()

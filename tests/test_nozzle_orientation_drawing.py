from __future__ import annotations

import base64
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.nozzle_orientation_drawing import build_nozzle_orientation_drawing
from aveva_e3d_mcp.tools import create_server


class NozzleOrientationDrawingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))

    def call(self, arguments: dict) -> dict:
        return self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": "e3d_nozzle_orientation_drawing",
                                                  "arguments": arguments}})["result"]

    @staticmethod
    def native(*, complete: bool = True) -> dict:
        return {"ok": True, "version": "E3D21", "result": {
            "success": True, "complete": complete,
            "element": {"refno": "=2000000004/46", "type": "NOZZ", "name": "/NOZZ"},
            "frame": {"refno": "=2013278556/0", "type": "WORL", "name": "/*", "requested": "WORLD"},
            "requested_point_numbers": [], "point_numbers": [1],
            "enumeration": {"source": "PPLS", "available": True, "complete": True,
                            "native_point_numbers": [1]},
            "points": [{"number": 1,
                        "exists": {"available": True, "value": True}, "complete": True, "errors": {},
                        "position": {"available": True, "unit": "mm", "x": 10.0, "y": 20.0, "z": 30.0},
                        "direction": {"available": True, "x": 1.0, "y": 0.0, "z": 0.0}}],
            "errors": {}, "ce_unchanged": True,
        }}

    def test_complete_native_points_return_plan_elevation_and_image(self) -> None:
        with patch.object(BridgeClient, "connection_points", return_value=self.native()) as read:
            response = self.call({"targets": [{"refno": "/NOZZ"}]})
        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertTrue(result["success"])
        drawing = result["drawing"]
        self.assertEqual([view["name"] for view in drawing["views"]], ["TOP", "FRONT"])
        nozzle = drawing["nozzles"][0]
        self.assertAlmostEqual(nozzle["bearing_deg"], 90.0)
        self.assertAlmostEqual(nozzle["tilt_deg"], 0.0)
        self.assertNotIn("svg", drawing)
        self.assertNotIn("dxf", drawing)
        image = next(item for item in response["content"] if item["type"] == "image")
        self.assertTrue(base64.b64decode(image["data"]).startswith(b"\x89PNG\r\n\x1a\n"))
        read.assert_called_once_with("/NOZZ", "WORLD", [])

    def test_native_contract_without_aggregate_available_emits_all_requested_formats(self) -> None:
        native = self.native()
        self.assertNotIn("available", native["result"])
        with patch.object(BridgeClient, "connection_points", return_value=native):
            response = self.call({"targets": [{"refno": "/NOZZ"}],
                                  "include_svg": True, "include_dxf": True})
        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertTrue(result["success"])
        self.assertTrue(result["complete"])
        self.assertIn("svg", result["drawing"])
        self.assertIn("dxf", result["drawing"])
        self.assertTrue(any(item["type"] == "image" for item in response["content"]))
        self.assertNotIn("available", result["evidence"]["targets"][0]["native"]["result"])

    def test_explicit_native_unavailable_never_emits_drawing_from_complete_points(self) -> None:
        native = self.native()
        native["result"]["available"] = False
        with patch.object(BridgeClient, "connection_points", return_value=native):
            response = self.call({"targets": [{"refno": "/NOZZ"}],
                                  "include_svg": True, "include_dxf": True})
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertFalse(result["success"])
        self.assertFalse(result["complete"])
        self.assertIsNone(result["drawing"])
        self.assertFalse(any(item["type"] == "image" for item in response["content"]))
        self.assertIs(result["evidence"]["targets"][0]["native"]["result"]["available"], False)

    def test_optional_svg_and_dxf_exports_share_the_preview_geometry(self) -> None:
        with patch.object(BridgeClient, "connection_points", return_value=self.native()):
            response = self.call({"targets": [{"refno": "/NOZZ"}],
                                  "include_svg": True, "include_dxf": True})
        self.assertFalse(response.get("isError", False), response)
        drawing = response["structuredContent"]["result"]["drawing"]
        self.assertTrue(drawing["svg"].startswith("<svg "))
        self.assertIn("TOP —", drawing["svg"])
        self.assertIn("FRONT —", drawing["svg"])
        self.assertIn("$ACADVER\n1\nAC1015", drawing["dxf"])
        self.assertIn("8\nTOP_FEATURE\n", drawing["dxf"])
        self.assertIn("8\nFRONT_FEATURE\n", drawing["dxf"])
        self.assertNotIn("8\nTOP_OUTLINE\n", drawing["dxf"])
        self.assertNotIn("8\nFRONT_OUTLINE\n", drawing["dxf"])
        self.assertEqual(drawing["dxf"].count("\n0\nLINE\n"), 9)

    def test_incomplete_native_data_never_emits_vector_exports(self) -> None:
        with patch.object(BridgeClient, "connection_points", return_value=self.native(complete=False)):
            response = self.call({"targets": [{"refno": "/NOZZ"}],
                                  "include_svg": True, "include_dxf": True})
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertIsNone(result["drawing"])
        self.assertEqual(result["evidence"]["targets"][0]["native"]["result"]["complete"], False)
        self.assertNotIn("svg", result)
        self.assertNotIn("dxf", result)

    def test_export_options_must_be_booleans_before_native_reads(self) -> None:
        with patch.object(BridgeClient, "connection_points") as read:
            response = self.call({"targets": [{"refno": "/NOZZ"}], "include_svg": "yes"})
        self.assertTrue(response["isError"])
        read.assert_not_called()

    def test_incomplete_native_data_keeps_evidence_without_partial_image(self) -> None:
        with patch.object(BridgeClient, "connection_points", return_value=self.native(complete=False)):
            response = self.call({"targets": [{"refno": "/NOZZ", "point_numbers": [1]}]})
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertFalse(result["success"])
        self.assertNotIn("image", {item["type"] for item in response["content"]})
        self.assertEqual(result["evidence"]["targets"][0]["native"]["result"]["complete"], False)

    def test_duplicate_targets_are_rejected_before_native_reads(self) -> None:
        with patch.object(BridgeClient, "connection_points") as read:
            response = self.call({"targets": [{"refno": "/NOZZ"}, {"refno": "/nozz"}]})
        self.assertTrue(response["isError"])
        read.assert_not_called()

    def test_vertical_direction_does_not_fabricate_an_azimuth(self) -> None:
        native = self.native()
        native["result"]["points"][0]["direction"] = {"available": True, "x": 0.0, "y": 0.0, "z": 1.0}
        with patch.object(BridgeClient, "connection_points", return_value=native):
            response = self.call({"targets": [{"refno": "/NOZZ"}]})
        nozzle = response["structuredContent"]["result"]["drawing"]["nozzles"][0]
        self.assertIsNone(nozzle["bearing_deg"])
        self.assertFalse(nozzle["bearing_available"])
        self.assertAlmostEqual(nozzle["tilt_deg"], 90.0)

    def test_projected_zero_length_direction_glyphs_are_omitted_from_dxf(self) -> None:
        for direction in ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (2e-9, 0.0, 1.0)):
            with self.subTest(direction=direction):
                native = self.native()
                native["result"]["points"][0]["direction"] = {
                    "available": True, "x": direction[0], "y": direction[1], "z": direction[2]}
                with patch.object(BridgeClient, "connection_points", return_value=native):
                    response = self.call({"targets": [{"refno": "/NOZZ"}],
                                          "include_svg": True, "include_dxf": True})
                self.assertFalse(response.get("isError", False), response)
                drawing = response["structuredContent"]["result"]["drawing"]
                self.assertIn("svg", drawing)
                self.assertIn("dxf", drawing)
                self.assertNotIn("export_error", response["structuredContent"]["result"])

    def test_missing_native_direction_overrides_inconsistent_complete_envelope(self) -> None:
        native = self.native()
        native["result"]["points"][0]["direction"] = {"available": False, "error": "unavailable"}
        with patch.object(BridgeClient, "connection_points", return_value=native):
            response = self.call({"targets": [{"refno": "/NOZZ"}],
                                  "include_svg": True, "include_dxf": True})
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertFalse(result["complete"])
        self.assertIsNone(result["drawing"])
        self.assertEqual(result["drawing_diagnostics"]["nozzles"][0]["error"], "direction_unavailable")

    def test_missing_native_position_overrides_inconsistent_complete_envelope(self) -> None:
        native = self.native()
        native["result"]["points"][0]["position"] = {"available": False, "error": "unavailable"}
        with patch.object(BridgeClient, "connection_points", return_value=native):
            response = self.call({"targets": [{"refno": "/NOZZ"}],
                                  "include_svg": True, "include_dxf": True})
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertFalse(result["complete"])
        self.assertIsNone(result["drawing"])
        self.assertEqual(result["drawing_diagnostics"]["nozzles"][0]["error"], "position_unavailable")
        self.assertFalse(any(item["type"] == "image" for item in response["content"]))

    def test_source_indices_link_every_line_to_its_row_across_unavailable_targets(self) -> None:
        mixed = self.native()
        missing_position = self.native()["result"]["points"][0]
        missing_position["number"] = 2
        missing_position["position"] = {"available": False, "error": "unavailable"}
        mixed["result"]["points"].append(missing_position)
        drawing, png = build_nozzle_orientation_drawing([
            {"refno": "/INVALID", "native": {"ok": False}},
            {"refno": "/NOZZ-A", "native": mixed},
            {"refno": "/NOZZ-B", "native": self.native()},
        ])
        self.assertIsNone(png)
        self.assertEqual([row["source_index"] for row in drawing["nozzles"]], [0, 1, 2, 3])
        self.assertEqual([row["refno"] for row in drawing["nozzles"]],
                         ["/INVALID", "/NOZZ-A", "/NOZZ-A", "/NOZZ-B"])
        for view in drawing["views"]:
            self.assertEqual({line["source_index"] for line in view["lines"]}, {1, 3})

    def test_empty_target_points_are_not_silently_omitted(self) -> None:
        empty = self.native()
        empty["result"]["points"] = []
        with patch.object(BridgeClient, "connection_points", side_effect=[self.native(), empty]):
            response = self.call({"targets": [{"refno": "/NOZZ-A"}, {"refno": "/NOZZ-B"}]})
        self.assertTrue(response.get("isError", False))
        result = response["structuredContent"]["result"]
        self.assertFalse(result["complete"])
        self.assertIsNone(result["drawing"])
        self.assertEqual(result["drawing_diagnostics"]["nozzles"][-1]["refno"], "/NOZZ-B")
        self.assertEqual(result["drawing_diagnostics"]["nozzles"][-1]["error"], "no_connection_points")
        self.assertFalse(any(item["type"] == "image" for item in response["content"]))

    def test_error_envelope_never_emits_image_from_optimistic_result(self) -> None:
        native = self.native()
        native["ok"] = False
        with patch.object(BridgeClient, "connection_points", return_value=native):
            response = self.call({"targets": [{"refno": "/NOZZ"}]})
        self.assertTrue(response.get("isError", False))
        result = response["structuredContent"]["result"]
        self.assertFalse(result["complete"])
        self.assertIsNone(result["drawing"])
        self.assertFalse(any(item["type"] == "image" for item in response["content"]))

    def test_bridge_error_is_preserved_in_structured_native_evidence(self) -> None:
        error = BridgeError("connection point read failed", diagnostics={"native_diagnostics": {"stage": "PPLS"}})
        with patch.object(BridgeClient, "connection_points", side_effect=error):
            response = self.call({"targets": [{"refno": "/NOZZ"}]})
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        native = result["evidence"]["targets"][0]["native"]
        self.assertFalse(native["ok"])
        self.assertEqual(native["error"], "connection point read failed")
        self.assertEqual(native["native_diagnostics"], {"stage": "PPLS"})
        self.assertFalse(result["complete"])

    def test_oversized_preview_is_rejected_before_native_read(self) -> None:
        with patch.object(BridgeClient, "connection_points") as read:
            response = self.call({"targets": [{"refno": "/NOZZ"}],
                                  "preview_width": 2048, "preview_height": 2048})
        self.assertTrue(response.get("isError", False))
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import base64
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class NozzleOrientationSheetToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))

    def call(self, **arguments: object) -> dict:
        return self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "e3d_nozzle_orientation_drawing", "arguments": arguments}})["result"]

    @staticmethod
    def native(count: int = 2, *, complete: bool = True) -> dict:
        # Match the native contract: availability belongs to each PPOS/PDIR,
        # rather than an invented top-level `available` flag.
        return {"ok": True, "version": "E3D21", "result": {
            "success": True, "complete": complete,
            "element": {"refno": "=1/2", "type": "NOZZ", "name": "/NOZZ"},
            "frame": {"refno": "=1/0", "type": "WORL", "requested": "WORLD"},
            "points": [{"number": index + 1,
                        "position": {"available": True, "unit": "mm", "x": 1000.0 + index * 75,
                                     "y": 2000.0 + index * 30, "z": 3000.0 + index * 15},
                        "direction": {"available": True, "x": 0.0, "y": 0.0, "z": 1.0}}
                       for index in range(count)]}}

    def test_printable_sheet_reuses_native_points_in_png_svg_dxf(self) -> None:
        with patch.object(BridgeClient, "connection_points", return_value=self.native()) as read:
            response = self.call(targets=[{"refno": "/NOZZ"}], sheet={}, include_svg=True, include_dxf=True)
        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        drawing = result["drawing"]
        self.assertEqual(drawing["sheet_count"], 1)
        scene = drawing["sheet"]
        self.assertEqual(scene, drawing["sheets"][0])
        self.assertEqual(scene["coordinate_space"], "paper")
        self.assertEqual(scene["model_coordinate_frame"], "WORLD")
        self.assertEqual(scene["source_indices"], [0, 1])
        self.assertEqual([row["id"] for row in scene["source_rows"]], ["N001", "N002"])
        self.assertEqual(scene["source_rows"][0]["position"]["x"], 1000.0)
        self.assertIn('width="420mm"', scene["svg"])
        self.assertIn("N001", scene["svg"])
        self.assertIn("N002", scene["dxf"])
        self.assertIn("$ACADVER\n1\nAC1015", scene["dxf"])
        image = next(item for item in response["content"] if item["type"] == "image")
        self.assertTrue(base64.b64decode(image["data"]).startswith(b"\x89PNG\r\n\x1a\n"))
        read.assert_called_once_with("/NOZZ", "WORLD", [])

    def test_pages_preserve_global_point_ids_and_return_one_image_per_page(self) -> None:
        with patch.object(BridgeClient, "connection_points", return_value=self.native(17)):
            response = self.call(targets=[{"refno": "/NOZZ"}], sheet={"points_per_page": 8})
        self.assertFalse(response.get("isError", False), response)
        drawing = response["structuredContent"]["result"]["drawing"]
        self.assertEqual(drawing["sheet_count"], 3)
        self.assertEqual([scene["source_indices"] for scene in drawing["sheets"]],
                         [list(range(8)), list(range(8, 16)), [16]])
        self.assertEqual(drawing["sheets"][2]["source_rows"][0]["id"], "N017")
        self.assertNotIn("sheet", drawing)
        self.assertEqual(sum(item["type"] == "image" for item in response["content"]), 3)

    def test_invalid_sheet_options_are_rejected_before_native_reads(self) -> None:
        with patch.object(BridgeClient, "connection_points") as read:
            for sheet in (None, [], {"size": "A0"}, {"points_per_page": 7},
                          {"title": "a" * 49}, {"drawing_number": "a" * 33}, {"unknown": True}):
                with self.subTest(sheet=sheet):
                    self.assertTrue(self.call(targets=[{"refno": "/NOZZ"}], sheet=sheet).get("isError", False))
        read.assert_not_called()

    def test_incomplete_native_point_set_never_becomes_a_printable_sheet(self) -> None:
        with patch.object(BridgeClient, "connection_points", return_value=self.native(complete=False)):
            response = self.call(targets=[{"refno": "/NOZZ"}], sheet={}, include_svg=True, include_dxf=True)
        self.assertTrue(response.get("isError", False), response)
        self.assertIsNone(response["structuredContent"]["result"]["drawing"])
        self.assertFalse(any(item["type"] == "image" for item in response["content"]))

    def test_total_preview_budget_is_checked_before_rendering_any_sheet(self) -> None:
        with patch.object(BridgeClient, "connection_points", return_value=self.native(41)), \
             patch("aveva_e3d_mcp.tools.rasterize_drawing_sheet_png") as render:
            response = self.call(targets=[{"refno": "/NOZZ"}], sheet={}, preview_width=1600,
                                 preview_height=1000)
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["drawing_diagnostics"]["error_code"], "nozzle_sheet_pixel_limit_exceeded")
        self.assertIsNone(result["drawing"])
        render.assert_not_called()


if __name__ == "__main__":
    unittest.main()

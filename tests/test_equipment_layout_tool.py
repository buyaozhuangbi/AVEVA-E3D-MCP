from __future__ import annotations

import base64
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class EquipmentLayoutToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))

    def call(self, arguments: dict) -> dict:
        return self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": "e3d_equipment_layout_drawing", "arguments": arguments}})["result"]

    @staticmethod
    def native(refnos: list[str], *, complete: bool = True) -> dict:
        rows = []
        for index, refno in enumerate(refnos):
            resolved_refno = refno if refno.startswith("=") else f"=2000000007/{index + 1}"
            rows.append({
                "requested_refno": refno,
                "refno": resolved_refno,
                "element": {"refno": resolved_refno, "type": "EQUI", "name": refno},
                "status": "returned",
                "success": True,
                "complete": complete,
                "measurements": {
                    "spatial": {
                        "success": True,
                        "complete": complete,
                        "result": {
                            "success": True,
                            "complete": complete,
                            "position": {"available": True, "x": index * 100.0, "y": 20.0, "z": 30.0},
                            "orientation": {
                                "available": True,
                                "x_axis": [1, 0, 0],
                                "y_axis": [0, 1, 0],
                                "z_axis": [0, 0, 1],
                            },
                        },
                    },
                },
            })
        return {"ok": True, "version": "E3D21", "result": {"success": True, "complete": complete,
                "rows": rows, "requested_refnos": refnos}}

    def test_batches_large_explicit_layout_and_returns_image(self) -> None:
        refs = [f"/EQUI-{index}" for index in range(33)]
        with patch.object(BridgeClient, "measure_elements_batch", side_effect=lambda batch, *args: self.native(batch)) as read:
            response = self.call({"refnos": refs, "include_svg": True, "include_dxf": True})
        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertTrue(result["success"])
        self.assertTrue(result["complete"])
        self.assertEqual([len(call.args[0]) for call in read.call_args_list], [32, 1])
        self.assertEqual(len(result["evidence"]["batch_receipts"]), 2)
        self.assertIn("svg", result["drawing"])
        self.assertIn("WORLD pose only - equipment extents not included.", result["drawing"]["svg"])
        self.assertIn(">X</text>", result["drawing"]["svg"])
        self.assertNotIn("Parameter-derived approximate mesh", result["drawing"]["svg"])
        self.assertIn("dxf", result["drawing"])
        self.assertNotIn("_DIMENSION", result["drawing"]["dxf"])
        self.assertNotIn("FRONT_OUTLINE", result["drawing"]["dxf"])
        self.assertIn("\n1\nX\n", result["drawing"]["dxf"])
        image = next(item for item in response["content"] if item["type"] == "image")
        self.assertTrue(base64.b64decode(image["data"]).startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn(b"POSE ONLY - EQUIPMENT EXTENTS NOT INCLUDED", base64.b64decode(image["data"]))

    def test_missing_native_rows_keep_evidence_and_no_image(self) -> None:
        with patch.object(BridgeClient, "measure_elements_batch", return_value={"ok": True, "version": "E3D21",
                "result": {"success": False, "complete": False, "rows": []}}):
            response = self.call({"refnos": ["/EQUI"]})
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertFalse(result["success"])
        self.assertNotIn("image", {item["type"] for item in response["content"]})
        self.assertEqual(result["evidence"]["batch_complete"], False)
        receipt = result["evidence"]["batch_receipts"][0]
        self.assertFalse(receipt["rows_match_request"])
        self.assertEqual(receipt["missing_refnos"], ["/EQUI"])

    def test_bridge_error_is_returned_as_structured_batch_evidence(self) -> None:
        error = BridgeError("native read failed", diagnostics={"native_diagnostics": {"stage": "measure"}})
        with patch.object(BridgeClient, "measure_elements_batch", side_effect=error):
            response = self.call({"refnos": ["/EQUI"]})
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        native = result["evidence"]["batch_receipts"][0]["native"]
        self.assertFalse(native["ok"])
        self.assertEqual(native["error"], "native read failed")
        self.assertEqual(native["native_diagnostics"], {"stage": "measure"})
        self.assertFalse(result["complete"])

    def test_oversized_preview_is_rejected_before_native_read(self) -> None:
        with patch.object(BridgeClient, "measure_elements_batch") as read:
            response = self.call({"refnos": ["/EQUI"], "preview_width": 2048, "preview_height": 2048})
        self.assertTrue(response.get("isError", False))
        read.assert_not_called()

    def test_duplicate_refs_rejected_before_native_read(self) -> None:
        with patch.object(BridgeClient, "measure_elements_batch") as read:
            response = self.call({"refnos": ["/EQUI", "/equi"]})
        self.assertTrue(response["isError"])
        read.assert_not_called()

    def test_duplicate_and_unexpected_native_rows_are_not_drawn(self) -> None:
        native = self.native(["/EQUI-1", "/EQUI-2", "/EQUI-2", "/NOT-REQUESTED"])
        with patch.object(BridgeClient, "measure_elements_batch", return_value=native):
            response = self.call({"refnos": ["/EQUI-1", "/EQUI-2"], "include_svg": True, "include_dxf": True})
        result = response["structuredContent"]["result"]
        self.assertFalse(result["complete"])
        self.assertTrue(result["success"])
        self.assertTrue(result["evidence"]["partial_drawing"])
        receipt = result["evidence"]["batch_receipts"][0]
        self.assertEqual(receipt["unexpected_refnos"], ["/NOT-REQUESTED"])
        self.assertEqual(receipt["duplicate_refnos"], ["/equi-2"])
        positions = [row["position"] for row in result["drawing"]["equipment"] if row["position"] is not None]
        self.assertEqual(positions, [[0.0, 20.0, 30.0]])
        self.assertIn("svg", result["drawing"])
        self.assertNotIn("dxf", result["drawing"])
        self.assertEqual(result["export_error"], "complete_native_layout_required_for_dxf")

    def test_incomplete_but_available_poses_are_reported_as_partial(self) -> None:
        native = self.native(["/EQUI-1", "/EQUI-2"], complete=False)
        # Preserve usable positions while confirming the native batch is not complete.
        with patch.object(BridgeClient, "measure_elements_batch", return_value=native):
            response = self.call({"refnos": ["/EQUI-1", "/EQUI-2"], "include_svg": True, "include_dxf": True})
        result = response["structuredContent"]["result"]
        self.assertTrue(result["success"])
        self.assertFalse(result["complete"])
        self.assertTrue(result["evidence"]["partial_drawing"])
        self.assertIn("image", {item["type"] for item in response["content"]})
        image = next(item for item in response["content"] if item["type"] == "image")
        self.assertIn(b"PARTIAL POSE ONLY - EQUIPMENT EXTENTS NOT INCLUDED", base64.b64decode(image["data"]))
        self.assertIn("svg", result["drawing"])
        self.assertIn("partial pose only", result["drawing"]["svg"])
        self.assertNotIn("dxf", result["drawing"])

    def test_malformed_native_rows_prevent_complete_receipt(self) -> None:
        native = self.native(["/EQUI-1"])
        native["result"]["rows"].append(None)
        with patch.object(BridgeClient, "measure_elements_batch", return_value=native):
            response = self.call({"refnos": ["/EQUI-1"]})
        result = response["structuredContent"]["result"]
        receipt = result["evidence"]["batch_receipts"][0]
        self.assertEqual(receipt["malformed_row_count"], 1)
        self.assertFalse(receipt["rows_match_request"])
        self.assertFalse(result["complete"])
        self.assertTrue(result["evidence"]["partial_drawing"])

    def test_resolved_rows_are_bound_to_requested_name_and_keep_both_identities(self) -> None:
        native = self.native(["/EQUI-1"])
        with patch.object(BridgeClient, "measure_elements_batch", return_value=native):
            response = self.call({"refnos": ["/EQUI-1"]})
        result = response["structuredContent"]["result"]
        self.assertTrue(result["complete"])
        self.assertEqual(result["drawing"]["equipment"][0]["requested_refno"], "/EQUI-1")
        self.assertEqual(result["drawing"]["equipment"][0]["resolved_refno"], "=2000000007/1")

    def test_mismatched_resolved_name_is_not_drawn(self) -> None:
        native = self.native(["/EQUI-1"])
        native["result"]["rows"][0]["element"]["name"] = "/OTHER"
        with patch.object(BridgeClient, "measure_elements_batch", return_value=native):
            response = self.call({"refnos": ["/EQUI-1"]})
        result = response["structuredContent"]["result"]
        self.assertFalse(result["complete"])
        self.assertFalse(result["success"])
        self.assertFalse(result["evidence"]["partial_drawing"])
        self.assertEqual(result["evidence"]["batch_receipts"][0]["identity_mismatch_refnos"], ["/EQUI-1"])
        self.assertIsNone(result["drawing"])
        self.assertFalse(any(row["position_available"]
                             for row in result["drawing_diagnostics"]["equipment"]))


if __name__ == "__main__":
    unittest.main()

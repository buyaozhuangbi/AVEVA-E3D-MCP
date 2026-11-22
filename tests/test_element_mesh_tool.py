from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class ElementMeshToolTests(unittest.TestCase):
    def setUp(self):
        self.server = create_server(Settings(mode="bridge", version="E3D21"))
        panel = patch.object(BridgeClient, "panel_geometry")
        primitive = patch.object(BridgeClient, "primitive_geometry")
        self.panel = panel.start()
        self.addCleanup(panel.stop)
        self.primitive = primitive.start()
        self.addCleanup(primitive.stop)

    def rpc(self, method, params):
        return self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})["result"]

    def call(self, **args):
        return self.rpc("tools/call", {"name": "e3d_element_mesh", "arguments": args})

    def test_discovery_and_invalid_controls_do_not_read_e3d(self):
        tool = next(row for row in self.rpc("tools/list", {})["tools"] if row["name"] == "e3d_element_mesh")
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertEqual(tool["inputSchema"]["required"], ["refno", "expected_type"])
        self.assertEqual(tool["inputSchema"]["properties"]["mesh_detail"]["enum"], ["full", "summary"])
        self.assertEqual(tool["inputSchema"]["properties"]["radial_segments"]["maximum"], 99)
        for args in (
            {"refno": "=1/2"},
            {"refno": "=1/2", "expected_type": "TUBI"},
            {"refno": "=1/2", "expected_type": "PANEL"},
            {"refno": "CE", "expected_type": "PANE"},
            {"refno": "=1/2", "expected_type": "PANE", "radial_segments": 48},
            {"refno": "=1/2", "expected_type": "CYLI", "coordinate_space": "OWNER"},
            {"refno": "=1/2", "expected_type": "CYLI", "radial_segments": 129},
            {"refno": "=1/2", "expected_type": "DISH", "radial_segments": 100},
            {"refno": "=1/2", "expected_type": "CYLI", "mesh_detail": "arrays"},
        ):
            with self.subTest(args=args):
                self.assertTrue(self.call(**args)["isError"])
        self.panel.assert_not_called()
        self.primitive.assert_not_called()

    def test_panel_uses_one_native_world_read_and_preserves_sdk_long_type(self):
        vertices = [{"position": [x, y, 0], "fillet_radius_mm": 0,
                     "world_position_complete": True, "position_world_mm": [x + 11, y + 12, 13]}
                    for x, y in ((0, 0), (5, 0), (0, 7))]
        geometry = {"success": True, "complete": True, "topology_valid": True,
                    "element": {"refno": "=1/2", "type": "PANEL"}, "loop_count": 1,
                    "read_errors": {}, "unsupported_features": [], "thickness_mm": 2, "native_justification": "BOTT",
                    "loops": [{"complete": True, "polygon_valid": True, "read_errors": {},
                               "unsupported_features": [], "blind": False, "thickness_mm": 2,
                               "native_justification": "BOTT", "planar_parallel_xy": True,
                               "coplanar_in_local_xy": True, "world_positions_complete": True,
                               "world_positions_requested": True, "world_position_count": 3,
                               "world_position_vertex_count": 3, "vertices": vertices}],
                    "world_positions_complete": True, "world_positions_requested": True,
                    "world_position_count": 3, "world_position_vertex_count": 3}
        self.panel.return_value = {"ok": True, "version": "E3D21", "result": geometry}
        receipt = self.call(refno="=01/002", expected_type="PANE", coordinate_space="WORLD")
        self.assertNotIn("isError", receipt)
        result = receipt["structuredContent"]["result"]
        self.assertEqual(result["requested_refno"], "=01/002")
        self.assertEqual(result["resolved_refno"], "=1/2")
        self.assertEqual(result["resolved_type"], "PANE")
        self.assertEqual(result["source_geometry"]["element"]["type"], "PANEL")
        self.assertIn([11.0, 12.0, 15.0], result["derived_mesh"]["vertices"])
        self.panel.assert_called_once_with("=01/002", include_world=True)
        self.primitive.assert_not_called()

        compact = self.call(refno="=1/2", expected_type="PANE", coordinate_space="WORLD", mesh_detail="summary")
        self.assertNotIn("isError", compact)
        summary = compact["structuredContent"]["result"]
        self.assertTrue(summary["mesh_metrics"]["available"])
        self.assertEqual(summary["source_geometry"]["loops"][0]["vertex_count"], 3)
        self.assertIsNone(summary["source_geometry"]["loops"][0]["vertices"])
        self.assertFalse(summary["source_geometry"]["geometry_arrays_included"])
        self.assertIsNone(summary["derived_mesh"]["vertices"])
        self.assertEqual(self.panel.call_count, 2)

    def test_primitive_preserves_native_geometry_and_local_resolution(self):
        geometry = {"success": True, "complete": True, "shape": "CYLI", "negative": False,
                    "element": {"refno": "=1/2", "type": "CYLI", "name": "/CYL"},
                    "negative_children": [], "read_errors": {},
                    "dimensions": {"DIAM": 10, "HEIG": 20}}
        self.primitive.return_value = {"ok": True, "version": "E3D21", "result": geometry}
        receipt = self.call(refno="/CYL", expected_type="CYLI", radial_segments=16)
        self.assertNotIn("isError", receipt)
        result = receipt["structuredContent"]["result"]
        self.assertTrue(result["available"])
        self.assertEqual(result["resolved_refno"], "=1/2")
        self.assertEqual(result["source_geometry"], geometry)
        self.assertEqual(result["coordinate_space"], "local")
        self.assertIn("primitive local", result["derived_mesh"]["coordinate_frame"])
        self.assertEqual(result["derived_mesh"]["counts"], {"vertices": 34, "triangles": 64})
        self.primitive.assert_called_once_with("/CYL")
        self.panel.assert_not_called()

    def test_summary_omits_mesh_arrays_and_returns_bounded_metrics(self):
        geometry = {"success": True, "complete": True, "shape": "CYLI", "negative": False,
                    "element": {"refno": "=1/2", "type": "CYLI", "name": "/CYL"},
                    "negative_children": [], "read_errors": {},
                    "dimensions": {"DIAM": 10, "HEIG": 20}}
        self.primitive.return_value = {"ok": True, "version": "E3D21", "result": geometry}
        summary = self.call(refno="=1/2", expected_type="CYLI", radial_segments=24, mesh_detail="summary")
        self.assertNotIn("isError", summary)
        result = summary["structuredContent"]["result"]
        self.assertTrue(result["available"])
        self.assertEqual(result["mesh_detail"], "summary")
        self.assertIsNone(result["derived_mesh"]["vertices"])
        self.assertIsNone(result["derived_mesh"]["triangles"])
        self.assertFalse(result["derived_mesh"]["geometry_arrays_included"])
        self.assertEqual(result["mesh_metrics"]["bounds"],
                         {"min": [-5.0, -5.0, -10.0], "max": [5.0, 5.0, 10.0]})
        self.assertEqual(result["mesh_metrics"]["components"][0]["orientation"], "outward")
        self.assertTrue(result["mesh_metrics"]["complete"])
        self.assertTrue(result["mesh_metrics"]["available"])
        self.assertEqual(result["mesh_metrics"]["coordinate_unit"], "mm")
        self.assertEqual(result["mesh_metrics"]["area_unit"], "mm2")
        self.assertEqual(result["mesh_metrics"]["volume_unit"], "mm3")
        self.assertNotIn("vertices", result["mesh_metrics"])

    def test_bad_identity_and_partial_read_never_return_mesh_or_retry(self):
        for geometry, error in (
            ({"success": True, "complete": True, "element": {"refno": "=1/2", "type": "NCYL"},
              "shape": "CYLI"}, "element_type_mismatch"),
            ({"success": True, "complete": True, "element": {"refno": "=1/3", "type": "CYLI"}},
             "element_reference_mismatch"),
            ({"success": True, "complete": True}, "native_element_identity_missing"),
            ({"success": False, "complete": False, "read_errors": {"owner": "Unavailable"}},
             "native_geometry_incomplete"),
        ):
            with self.subTest(error=error):
                self.primitive.reset_mock()
                self.primitive.return_value = {"ok": True, "version": "E3D21", "result": geometry}
                receipt = self.call(refno="=1/2", expected_type="CYLI")
                self.assertTrue(receipt["isError"])
                result = receipt["structuredContent"]["result"]
                self.assertFalse(result["available"])
                self.assertEqual(result["error_code"], error)
                self.assertIsNone(result["derived_mesh"])
                self.assertEqual(result["source_geometry"], geometry)
                self.primitive.assert_called_once_with("=1/2")
        self.panel.assert_not_called()

    def test_named_reference_must_match_returned_name(self):
        geometry = {"success": True, "complete": True, "shape": "CYLI", "negative": False,
                    "element": {"refno": "=1/2", "type": "CYLI", "name": "/OTHER"},
                    "negative_children": [], "read_errors": {},
                    "dimensions": {"DIAM": 10, "HEIG": 20}}
        self.primitive.return_value = {"ok": True, "version": "E3D21", "result": geometry}
        receipt = self.call(refno="/CYL", expected_type="CYLI")
        self.assertTrue(receipt["isError"])
        self.assertEqual(receipt["structuredContent"]["result"]["error_code"], "element_name_mismatch")
        self.assertIsNone(receipt["structuredContent"]["result"]["derived_mesh"])
        self.primitive.assert_called_once_with("/CYL")

    def test_malformed_geometry_response_is_a_tool_error(self):
        self.primitive.return_value = {"ok": True}
        receipt = self.call(refno="=1/2", expected_type="BOX")
        self.assertTrue(receipt["isError"])
        self.assertEqual(receipt["structuredContent"]["error_code"], "invalid_geometry_response")

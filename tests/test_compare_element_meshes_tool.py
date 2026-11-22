from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp import tools as tools_module
from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class CompareElementMeshesToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))

    def rpc(self, method: str, params: dict) -> dict:
        return self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})["result"]

    def call(self, **args: object) -> dict:
        return self.rpc("tools/call", {"name": "e3d_compare_element_meshes", "arguments": args})

    @staticmethod
    def box(refno: str, name: str, x: float, *, shape: str = "BOX") -> dict:
        dimensions = {"XLEN": 10, "YLEN": 20, "ZLEN": 30}
        if shape == "CYLI":
            dimensions = {"DIAM": 10, "HEIG": 30}
        return {"success": True, "complete": True, "shape": shape, "negative": False,
                "negative_children": [], "children": [], "read_errors": {},
                "element": {"refno": refno, "type": shape, "name": name},
                "dimensions": dimensions,
                "frame": {"world": {"position": [x, 0, 0], "orientation": {
                    "x_axis": [1, 0, 0], "y_axis": [0, 1, 0], "z_axis": [0, 0, 1]}}}}

    def test_catalog_schema_is_bounded_explicit_and_read_only(self) -> None:
        tools = {row["name"]: row for row in self.rpc("tools/list", {})["tools"]}
        tool = tools["e3d_compare_element_meshes"]
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["destructiveHint"])
        schema = tool["inputSchema"]
        self.assertEqual(set(schema["properties"]), {
            "first_refno", "first_expected_type", "second_refno", "second_expected_type",
            "coordinate_space", "radial_segments",
        })
        self.assertEqual(schema["required"], [
            "first_refno", "first_expected_type", "second_refno", "second_expected_type",
        ])
        self.assertEqual(schema["properties"]["coordinate_space"]["default"], "WORLD")
        self.assertEqual(schema["properties"]["radial_segments"]["minimum"], 12)
        self.assertEqual(schema["properties"]["radial_segments"]["maximum"], 99)
        self.assertFalse(schema["additionalProperties"])

    def test_world_comparison_reports_metrics_and_same_index_displacement_without_arrays(self) -> None:
        geometry = {
            "=1/1": self.box("=1/1", "/A", 0),
            "=1/2": self.box("=1/2", "/B", 5),
        }
        with patch.object(BridgeClient, "primitive_geometry",
                          side_effect=lambda refno: {"ok": True, "version": "E3D21", "result": geometry[refno]}) as read, \
                patch.object(BridgeClient, "update_primitive") as write, \
                patch.object(BridgeClient, "capture_view") as capture:
            response = self.call(first_refno="=1/1", first_expected_type="BOX",
                                 second_refno="=1/2", second_expected_type="BOX")

        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertTrue(result["success"])
        self.assertEqual(read.call_count, 2)
        self.assertEqual([call.args[0] for call in read.call_args_list], ["=1/1", "=1/2"])
        write.assert_not_called()
        capture.assert_not_called()
        comparison = result["comparison"]
        self.assertTrue(comparison["index_topology_comparable"])
        self.assertEqual(comparison["bounds_mm"]["delta_second_minus_first"], {
            "min": [5.0, 0.0, 0.0], "max": [5.0, 0.0, 0.0], "complete": True,
        })
        self.assertEqual(comparison["surface_area_mm2"]["delta_second_minus_first"], 0.0)
        self.assertEqual(comparison["volume_mm3"]["delta_second_minus_first"], 0.0)
        self.assertEqual(comparison["indexed_vertex_displacement_mm"], {
            "available": True, "complete": True, "max": 5.0, "rms": 5.0,
        })
        self.assertNotIn("vertices", str(result))
        self.assertNotIn("triangles", str(result))
        self.assertNotIn("triangle_normals", str(result))
        self.assertEqual(result["targets"][0]["vertex_count"], 8)
        self.assertEqual(result["targets"][0]["triangle_count"], 12)

    def test_different_index_topology_omits_vertex_displacement_and_uses_segments_only_for_round_shape(self) -> None:
        geometries = {
            "=1/1": self.box("=1/1", "/A", 0),
            "=1/2": self.box("=1/2", "/B", 0, shape="CYLI"),
        }
        original = tools_module._element_mesh
        with patch.object(BridgeClient, "primitive_geometry",
                          side_effect=lambda refno: {"ok": True, "version": "E3D21", "result": geometries[refno]}), \
                patch("aveva_e3d_mcp.tools._element_mesh", wraps=original) as mesh_reader:
            response = self.call(first_refno="=1/1", first_expected_type="BOX",
                                 second_refno="=1/2", second_expected_type="CYLI",
                                 coordinate_space="local", radial_segments=24)

        self.assertFalse(response.get("isError", False), response)
        calls = mesh_reader.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertNotIn("radial_segments", calls[0].args[1])
        self.assertEqual(calls[1].args[1]["radial_segments"], 24)
        comparison = response["structuredContent"]["result"]["comparison"]
        self.assertFalse(comparison["index_topology_comparable"])
        self.assertNotIn("indexed_vertex_displacement_mm", comparison)

    def test_local_matching_topology_does_not_claim_common_spatial_displacement(self) -> None:
        geometries = {
            "=1/1": self.box("=1/1", "/A", 0),
            "=1/2": self.box("=1/2", "/B", 100),
        }
        with patch.object(BridgeClient, "primitive_geometry",
                          side_effect=lambda refno: {"ok": True, "version": "E3D21", "result": geometries[refno]}):
            response = self.call(first_refno="=1/1", first_expected_type="BOX",
                                 second_refno="=1/2", second_expected_type="BOX", coordinate_space="local")
        self.assertFalse(response.get("isError", False), response)
        comparison = response["structuredContent"]["result"]["comparison"]
        self.assertTrue(comparison["index_topology_comparable"])
        self.assertNotIn("indexed_vertex_displacement_mm", comparison)
        self.assertEqual(comparison["indexed_vertex_displacement_unavailable_reason"],
                         "element_local_frames_are_not_common")
        self.assertIn("not a common spatial displacement", comparison["bounds_mm"]["delta_frame_scope"])

    def test_failed_identity_still_attempts_second_target_and_returns_no_comparison(self) -> None:
        first = self.box("=1/999", "/WRONG", 0)
        second = self.box("=1/2", "/B", 0)
        with patch.object(BridgeClient, "primitive_geometry", side_effect=[
                {"ok": True, "version": "E3D21", "result": first},
                {"ok": True, "version": "E3D21", "result": second},
        ]) as read:
            response = self.call(first_refno="=1/1", first_expected_type="BOX",
                                 second_refno="=1/2", second_expected_type="BOX")

        self.assertTrue(response.get("isError", False))
        result = response["structuredContent"]["result"]
        self.assertFalse(result["success"])
        self.assertIsNone(result["comparison"])
        self.assertEqual(read.call_count, 2)
        self.assertEqual(result["targets"][0]["errors"][0]["code"], "element_reference_mismatch")
        self.assertTrue(result["targets"][1]["success"])
        self.assertNotIn("vertices", str(result))
        self.assertNotIn("triangles", str(result))

    def test_volume_is_omitted_when_mesh_is_not_one_closed_component(self) -> None:
        geometry = self.box("=1/1", "/A", 0)
        open_mesh = {"success": True, "available": True, "complete": True,
                     "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]], "triangles": [[0, 1, 2]]}
        with patch.object(BridgeClient, "primitive_geometry", return_value={
                "ok": True, "version": "E3D21", "result": geometry}), \
                patch("aveva_e3d_mcp.tools.derive_primitive_mesh", return_value=open_mesh):
            response = self.call(first_refno="=1/1", first_expected_type="BOX",
                                 second_refno="=1/1", second_expected_type="BOX", coordinate_space="local")

        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertTrue(result["success"])
        self.assertFalse(result["targets"][0]["volume_available"])
        self.assertFalse(result["comparison"]["volume_mm3"]["available"])
        self.assertNotIn("first", result["comparison"]["volume_mm3"])

    def test_invalid_arguments_fail_before_any_native_read(self) -> None:
        invalid = [
            {"first_refno": "CE", "first_expected_type": "BOX", "second_refno": "=1/2", "second_expected_type": "BOX"},
            {"first_refno": "=1/1", "first_expected_type": "PANEL", "second_refno": "=1/2", "second_expected_type": "BOX"},
            {"first_refno": "=1/1", "first_expected_type": "BOX", "second_refno": "=1/2", "second_expected_type": "BOX", "coordinate_space": "OWNER"},
            {"first_refno": "=1/1", "first_expected_type": "BOX", "second_refno": "=1/2", "second_expected_type": "BOX", "radial_segments": 100},
            {"first_refno": "=1/1", "first_expected_type": "BOX", "second_refno": "=1/2", "second_expected_type": "BOX", "extra": True},
        ]
        with patch.object(BridgeClient, "primitive_geometry") as read, patch.object(BridgeClient, "panel_geometry") as panel:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self.call(**arguments).get("isError", False))
        read.assert_not_called()
        panel.assert_not_called()


if __name__ == "__main__":
    unittest.main()

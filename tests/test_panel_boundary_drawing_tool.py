from __future__ import annotations

import base64
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.panel_boundary_drawing import build_panel_boundary_drawing
from aveva_e3d_mcp.tools import create_server


class PanelBoundaryDrawingToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))

    def rpc(self, method: str, params: dict) -> dict:
        return self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})["result"]

    def call(self, **args: object) -> dict:
        return self.rpc("tools/call", {"name": "e3d_project_panel_boundary_drawing", "arguments": args})

    @staticmethod
    def loop(points: list[list[float]], *, world: list[list[float]] | None = None,
             polygon_valid: bool | None = True, unsupported: list[dict] | None = None) -> dict:
        vertices = []
        for index, point in enumerate(points):
            vertex = {"refno": f"=1/{20 + index}", "position": point,
                      "world_position_complete": True}
            vertex["position_world_mm"] = (world or points)[index]
            vertices.append(vertex)
        return {"complete": True, "read_errors": {}, "unsupported_features": unsupported or [],
                "polygon_valid": polygon_valid, "vertices": vertices, "loop_index": 0,
                "role": "not inferred"}

    @classmethod
    def geometry(cls, *, loops: list[dict] | None = None, world_complete: bool = True) -> dict:
        rings = loops or [cls.loop([[0, 0, 0], [100, 0, 0], [100, 50, 0], [0, 50, 0]])]
        total = sum(len(loop["vertices"]) for loop in rings)
        return {"success": True, "complete": True, "vertex_count": total,
                "topology_valid": True if len(rings) == 1 else None,
                "world_positions_requested": True, "world_positions_complete": world_complete,
                "world_position_count": total if world_complete else total - 1,
                "world_position_vertex_count": total,
                "world_position_frame": "=1/0", "world_read_errors": {},
                "coordinate_frame": "=1/2", "element": {"refno": "=1/2", "type": "PANEL", "name": "/PANE-1"},
                "loops": rings}

    def test_catalog_and_direct_pave_projection_return_png_svg_and_boundary_dxf(self) -> None:
        catalog = {item["name"]: item for item in self.rpc("tools/list", {})["tools"]}
        self.assertIn("e3d_project_panel_boundary_drawing", catalog)
        self.assertTrue(catalog["e3d_project_panel_boundary_drawing"]["annotations"]["readOnlyHint"])
        with patch.object(BridgeClient, "panel_geometry", return_value={"ok": True, "version": "E3D21",
                    "result": self.geometry()}) as read, \
             patch("aveva_e3d_mcp.tools.derive_panel_mesh") as mesh:
            response = self.call(refno="=1/2", include_svg=True, include_dxf=True)
        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        drawing = result["drawing"]
        self.assertEqual(read.call_args.args, ("=1/2",))
        self.assertEqual(read.call_args.kwargs, {"include_world": True})
        mesh.assert_not_called()
        self.assertTrue(result["success"])
        self.assertFalse(drawing["native_tessellation"])
        self.assertFalse(drawing["visibility_classified"])
        self.assertFalse(drawing["hole_semantics_inferred"])
        self.assertEqual(drawing["coordinate_frame"], "E3D Design WORLD (=1/0)")
        self.assertIn("thickness, holes and visibility not resolved", drawing["svg"])
        self.assertIn("EXTENT", drawing["svg"])
        self.assertIn("FRONT_BOUNDARY", drawing["dxf"])
        self.assertIn("PANE control-point boundaries; thickness, holes and visibility not resolved", drawing["dxf"])
        self.assertNotIn("_DIMENSION", drawing["dxf"])
        self.assertEqual([view["line_count"] for view in drawing["views"]], [2, 4, 2])
        image = next(item for item in response["content"] if item["type"] == "image")
        png = base64.b64decode(image["data"])
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(int.from_bytes(png[16:20], "big"), 1200)
        self.assertEqual(int.from_bytes(png[20:24], "big"), 440)

    def test_multiple_loops_keep_native_roles_unassigned_and_topology_unknown(self) -> None:
        first = self.loop([[0, 0, 0], [100, 0, 0], [100, 100, 0], [0, 100, 0]])
        second = self.loop([[25, 25, 0], [75, 25, 0], [75, 75, 0], [25, 75, 0]])
        result = build_panel_boundary_drawing(self.geometry(loops=[first, second]), coordinate_space="WORLD")
        self.assertTrue(result["success"])
        self.assertIsNone(result["topology_valid"])
        self.assertEqual([row["role"] for row in result["source"]["loops"]], ["not inferred", "not inferred"])
        self.assertEqual(result["source"]["loop_count"], 2)
        self.assertEqual(result["views"][1]["line_count"], 8)

    def test_world_point_gaps_and_incomplete_native_reads_fail_closed(self) -> None:
        incomplete = self.geometry(world_complete=False)
        with patch.object(BridgeClient, "panel_geometry", return_value={"ok": True, "result": incomplete}):
            response = self.call(refno="=1/2")
        self.assertTrue(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"]["result"]["error_code"], "world_panel_positions_incomplete")
        self.assertIsNone(response["structuredContent"]["result"]["drawing"])

        incomplete = self.geometry()
        incomplete["success"] = False
        with self.assertRaises(ValueError):
            build_panel_boundary_drawing(incomplete)
        incomplete = self.geometry()
        incomplete["world_position_frame"] = None
        with self.assertRaisesRegex(ValueError, "world_panel_position_frame_unavailable"):
            build_panel_boundary_drawing(incomplete)

    def test_world_read_errors_fail_even_when_native_positions_are_complete(self) -> None:
        native = self.geometry()
        native["world_read_errors"] = {"=1/20": "native WORLD position read failed"}
        with self.assertRaisesRegex(ValueError, "^world_panel_position_read_errors$"):
            build_panel_boundary_drawing(native, coordinate_space="WORLD")
        with patch.object(BridgeClient, "panel_geometry", return_value={"ok": True, "result": native}):
            response = self.call(refno="=1/2")
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "world_panel_position_read_errors")
        self.assertIsNone(result["drawing"])
        self.assertTrue(result["native_geometry"]["world_positions_complete"])
        self.assertEqual(result["native_geometry"]["world_read_errors"], native["world_read_errors"])
        self.assertFalse(any(item["type"] == "image" for item in response["content"]))

    def test_world_positions_with_explicit_non_mm_unit_are_rejected(self) -> None:
        for unit in ("m", "inch"):
            with self.subTest(unit=unit):
                native = self.geometry()
                native["world_position_unit"] = unit
                with self.assertRaisesRegex(ValueError, "^world_panel_position_unit_mismatch$"):
                    build_panel_boundary_drawing(native, coordinate_space="WORLD")
                with patch.object(BridgeClient, "panel_geometry", return_value={"ok": True, "result": native}):
                    response = self.call(refno="=1/2")
                self.assertTrue(response.get("isError", False), response)
                result = response["structuredContent"]["result"]
                self.assertFalse(result["success"])
                self.assertEqual(result["error_code"], "world_panel_position_unit_mismatch")
                self.assertIsNone(result["drawing"])
                self.assertFalse(any(item["type"] == "image" for item in response["content"]))

    def test_world_positions_accept_optional_mm_unit(self) -> None:
        for explicit_unit in (False, True):
            with self.subTest(explicit_unit=explicit_unit):
                native = self.geometry()
                if explicit_unit:
                    native["world_position_unit"] = "mm"
                drawing = build_panel_boundary_drawing(native, coordinate_space="WORLD", views=["TOP"])
                self.assertTrue(drawing["success"])
                self.assertEqual(drawing["views"][0]["line_count"], 4)

    def test_native_polygon_findings_are_preserved_but_not_recast_as_mesh_validity(self) -> None:
        ring = self.loop([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]],
                         polygon_valid=None,
                         unsupported=[{"code": "fillet_radius", "radius_mm": 3}])
        result = build_panel_boundary_drawing(self.geometry(loops=[ring]))
        self.assertTrue(result["success"])
        self.assertIsNone(result["source"]["loops"][0]["polygon_valid"])
        self.assertEqual(result["source"]["loops"][0]["native_features"][0]["code"], "fillet_radius")
        self.assertFalse(result["native_polygon_validity_required"])

    def test_edge_on_projection_reports_dropped_zero_length_edges(self) -> None:
        result = build_panel_boundary_drawing(self.geometry(), views=["FRONT"])
        self.assertEqual(result["views"][0]["line_count"], 2)
        self.assertEqual(result["dropped_projected_edges"]["FRONT"], 2)

    def test_local_mode_does_not_request_world_positions(self) -> None:
        native = self.geometry(world_complete=False)
        native["world_position_frame"] = None
        native["world_position_unit"] = "m"
        native["world_read_errors"] = {"position": "WORLD positions were not requested"}
        with patch.object(BridgeClient, "panel_geometry", return_value={"ok": True, "result": native}) as read:
            response = self.call(refno="=1/2", coordinate_space="panel_local", views=["TOP"])
        self.assertFalse(response.get("isError", False), response)
        read.assert_called_once_with("=1/2", include_world=False)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["coordinate_space"], "panel_local")
        self.assertEqual(result["drawing"]["views"][0]["line_count"], 4)

    def test_bad_options_are_rejected_before_native_read(self) -> None:
        with patch.object(BridgeClient, "panel_geometry") as read:
            for args in ({"refno": "=1/2", "views": []},
                         {"refno": "=1/2", "views": ["FRONT", "FRONT"]},
                         {"refno": "=1/2", "coordinate_space": "owner"},
                         {"refno": "=1/2", "preview_width": 2048, "preview_height": 2048},
                         {"refno": "=1/2", "include_dxf": "yes"},
                         {"refno": "=1/2", "extra": True}):
                with self.subTest(args=args):
                    self.assertTrue(self.call(**args).get("isError", False))
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()

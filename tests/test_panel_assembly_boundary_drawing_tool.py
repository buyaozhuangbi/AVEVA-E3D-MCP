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


class PanelAssemblyBoundaryDrawingToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.health_value = {
            "ok": True, "connected": True, "version": "E3D21", "session_id": "test-session",
            "state": {"busy": False, "database_ready": True, "recovery_required": False,
                      "native_database_fault": {"has_fault": False}},
        }
        self.project_value = {"ok": True, "version": "E3D21",
                              "project": {"connected": True, "recovery_only": False,
                                          "name": "APS", "mdb": "ALL"}}
        health_patch = patch.object(BridgeClient, "health", return_value=self.health_value)
        project_patch = patch.object(BridgeClient, "project", return_value=self.project_value)
        health_patch.start()
        project_patch.start()
        self.addCleanup(health_patch.stop)
        self.addCleanup(project_patch.stop)
        self.server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))

    def rpc(self, method: str, params: dict) -> dict:
        return self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})["result"]

    def call(self, **args: object) -> dict:
        return self.rpc("tools/call", {"name": "e3d_project_panel_assembly_boundary_drawing", "arguments": args})

    @staticmethod
    def geometry(refno: str, offset: float = 0) -> dict:
        points = [[offset, 0, 0], [offset + 100, 0, 0], [offset + 100, 50, 0], [offset, 50, 0]]
        vertices = [{"refno": f"=1/{20 + index}", "position": point,
                     "position_world_mm": point, "world_position_complete": True}
                    for index, point in enumerate(points)]
        return {"success": True, "complete": True, "vertex_count": 4, "loop_count": 1,
                "read_errors": {}, "world_positions_complete": True, "world_position_count": 4,
                "world_position_vertex_count": 4, "world_position_frame": "=1/0", "world_read_errors": {},
                "revision": f"native-revision:{refno}",
                "element": {"refno": refno, "type": "PANEL", "name": refno},
                "loops": [{"complete": True, "vertices": vertices, "read_errors": {},
                           "unsupported_features": [], "polygon_valid": True, "loop_index": 0,
                           "role": "not inferred"}]}

    def test_catalog_and_two_panel_overlay_preserve_source_refs_and_world_evidence(self) -> None:
        tools = {row["name"]: row for row in self.rpc("tools/list", {})["tools"]}
        tool = tools["e3d_project_panel_assembly_boundary_drawing"]
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertEqual(tool["inputSchema"]["properties"]["targets"]["maxItems"], 128)
        self.assertEqual(tool["inputSchema"]["properties"]["panels_per_page"]["enum"], [16, 32, 64, 128])
        with patch.object(BridgeClient, "panel_geometry", side_effect=[
                {"ok": True, "version": "E3D21", "result": self.geometry("=1/2")},
                {"ok": True, "version": "E3D21", "result": self.geometry("=1/3", 150)},
        ]) as read, patch("aveva_e3d_mcp.tools.derive_panel_mesh") as mesh:
            response = self.call(targets=["=1/2", "=1/3"], include_svg=True, include_dxf=True)
        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertTrue(result["success"], result)
        self.assertTrue(result["complete"])
        self.assertFalse(result["atomic_snapshot"])
        self.assertEqual(result["observed_counts"], {"panels": 2, "vertices": 8, "projected_segments": 16})
        self.assertEqual([item["resolved_refno"] for item in result["sources"]], ["=1/2", "=1/3"])
        self.assertEqual([item["revision"] for item in result["sources"]],
                         ["native-revision:=1/2", "native-revision:=1/3"])
        drawing = result["drawing"]
        self.assertEqual(result["world_position_frame"], "=1/0")
        self.assertEqual(drawing["world_position_frame"], result["world_position_frame"])
        self.assertEqual([item["world_position_frame"] for item in result["sources"]], ["=1/0"] * 2)
        self.assertEqual([item["world_position_frame"] for item in drawing["source_drawings"]], ["=1/0"] * 2)
        self.assertEqual([item["revision"] for item in drawing["source_drawings"]],
                         [item["revision"] for item in result["sources"]])
        self.assertFalse(drawing["visibility_classified"])
        self.assertEqual(drawing["drawing_kind"], "native PANE control-point polygon")
        self.assertEqual([line["source_refno"] for line in drawing["views"][1]["lines"]], ["=1/2"] * 4 + ["=1/3"] * 4)
        self.assertIn("FRONT_BOUNDARY", drawing["dxf"])
        self.assertIn("native boundary, thickness, holes and visibility not resolved", drawing["dxf"])
        self.assertNotIn("_DIMENSION", drawing["dxf"])
        self.assertEqual(read.call_args_list[0].args, ("=1/2",))
        self.assertTrue(all(call.kwargs == {"include_world": True} for call in read.call_args_list))
        mesh.assert_not_called()
        image = next(item for item in response["content"] if item["type"] == "image")
        self.assertTrue(base64.b64decode(image["data"]).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_duplicate_and_bad_arguments_fail_before_native_reads(self) -> None:
        with patch.object(BridgeClient, "panel_geometry") as read:
            for args in ({"targets": []}, {"targets": ["=1/2"] * 129},
                         {"targets": ["=1/2", "=1/2"]}, {"targets": ["=1/2"], "views": []},
                         {"targets": ["=1/2"], "panels_per_page": 8},
                         {"targets": ["=1/2"], "preview_width": 2048, "preview_height": 2048},
                         {"targets": ["=1/2"], "extra": True}):
                with self.subTest(args=args):
                    self.assertTrue(self.call(**args).get("isError", False))
        read.assert_not_called()

    def test_incomplete_world_source_fails_without_any_drawing(self) -> None:
        incomplete = self.geometry("=1/2")
        incomplete["world_positions_complete"] = False
        with patch.object(BridgeClient, "panel_geometry", return_value={"ok": True, "version": "E3D21",
                    "result": incomplete}):
            response = self.call(targets=["=1/2"])
        result = response["structuredContent"]["result"]
        self.assertTrue(response.get("isError", False))
        self.assertFalse(result["success"])
        self.assertEqual(result["sources"][0]["error_code"], "world_panel_positions_incomplete")
        self.assertNotIn("drawing", result)

    def test_second_panel_with_different_world_frame_fails_without_any_drawing(self) -> None:
        first = self.geometry("=1/2")
        second = self.geometry("=1/3", 150)
        second["world_position_frame"] = "=2/0"
        with patch.object(BridgeClient, "panel_geometry", side_effect=[
                {"ok": True, "version": "E3D21", "result": first},
                {"ok": True, "version": "E3D21", "result": second},
        ]) as read:
            response = self.call(targets=["=1/2", "=1/3"], include_svg=True, include_dxf=True)
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertFalse(result["success"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["error_code"], "assembly_panel_world_frame_mismatch")
        self.assertTrue(result["sources"][0]["success"])
        self.assertFalse(result["sources"][1]["success"])
        self.assertEqual(result["sources"][1]["error_code"], "world_position_frame_mismatch")
        self.assertEqual([item["world_position_frame"] for item in result["sources"]], ["=1/0", "=2/0"])
        self.assertEqual([item["revision"] for item in result["sources"]], [first["revision"], second["revision"]])
        self.assertNotIn("drawing", result)
        self.assertNotIn("pages", result)
        self.assertFalse(any(item["type"] == "image" for item in response["content"]))
        self.assertEqual(read.call_count, 2)

    def test_context_change_after_native_reads_suppresses_drawing(self) -> None:
        health = {"ok": True, "connected": True, "version": "E3D21", "session_id": "test-session",
                  "state": self.health_value["state"]}
        project_before = self.project_value
        project_after = {**project_before, "project": {**project_before["project"], "mdb": "OTHER"}}
        with patch.object(BridgeClient, "health", side_effect=[health, health]), \
             patch.object(BridgeClient, "project", side_effect=[project_before, project_after]), \
             patch.object(BridgeClient, "panel_geometry", return_value={"ok": True, "version": "E3D21",
                    "result": self.geometry("=1/2")}):
            response = self.call(targets=["=1/2"])
        result = response["structuredContent"]["result"]
        self.assertTrue(response.get("isError", False))
        self.assertEqual(result["error_code"], "assembly_panel_context_changed")
        self.assertIs(result["context"]["boundary_agreement"], False)

    def test_large_panel_set_is_paged_and_line_source_indices_remain_global(self) -> None:
        targets = [f"=1/{100 + index}" for index in range(17)]
        responses = [{"ok": True, "version": "E3D21", "result": self.geometry(refno)}
                     for refno in targets]
        with patch.object(BridgeClient, "panel_geometry", side_effect=responses) as read:
            response = self.call(targets=targets, panels_per_page=16, preview_width=400, preview_height=400)
        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertTrue(result["success"])
        self.assertEqual((result["page_count"], result["completed_page_count"]), (2, 2))
        self.assertEqual([page["source_range"] for page in result["pages"]], [[0, 16], [16, 17]])
        self.assertEqual(result["world_position_frame"], "=1/0")
        self.assertEqual([item["revision"] for item in result["sources"]],
                         [native["result"]["revision"] for native in responses])
        for page in result["pages"]:
            self.assertEqual(page["drawing"]["world_position_frame"], result["world_position_frame"])
            self.assertEqual([item["world_position_frame"] for item in page["drawing"]["source_drawings"]],
                             [result["world_position_frame"]] * len(page["sources"]))
            self.assertEqual([item["revision"] for item in page["drawing"]["source_drawings"]],
                             [item["revision"] for item in page["sources"]])
        self.assertEqual(result["pages"][0]["drawing"]["views"][1]["lines"][0]["source_index"], 0)
        last_line = result["pages"][1]["drawing"]["views"][1]["lines"][0]
        self.assertEqual(last_line["source_index"], 16)
        self.assertEqual(last_line["source_refno"], targets[16])
        images = [item for item in response["content"] if item["type"] == "image"]
        self.assertEqual(len(images), 2)
        self.assertEqual(read.call_count, 17)


if __name__ == "__main__":
    unittest.main()

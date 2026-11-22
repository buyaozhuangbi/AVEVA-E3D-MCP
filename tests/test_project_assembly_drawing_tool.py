from __future__ import annotations

import base64
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


class ProjectAssemblyDrawingToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.health_value = {
            "ok": True, "connected": True, "version": "E3D21", "session_id": "test-session",
            "state": {"busy": False, "database_ready": True, "recovery_required": False,
                      "native_database_fault": {"has_fault": False}},
        }
        self.project_value = {
            "ok": True, "version": "E3D21",
            "project": {"connected": True, "recovery_only": False, "name": "APS", "mdb": "ALL"},
        }
        health_patch = patch.object(BridgeClient, "health", return_value=self.health_value)
        project_patch = patch.object(BridgeClient, "project", return_value=self.project_value)
        self.health = health_patch.start()
        self.project = project_patch.start()
        self.addCleanup(health_patch.stop)
        self.addCleanup(project_patch.stop)
        self.server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))

    def rpc(self, method: str, params: dict) -> dict:
        return self.server._dispatch({
            "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
        })["result"]

    def call(self, **arguments: object) -> dict:
        return self.rpc("tools/call", {
            "name": "e3d_project_assembly_drawing", "arguments": arguments,
        })

    @staticmethod
    def target(refno="=1/2", expected_type="BOX") -> dict:
        return {"refno": refno, "expected_type": expected_type}

    @staticmethod
    def geometry(refno="=1/2", name="/OUTER", *, dimensions=None, position=None, shape="BOX") -> dict:
        if dimensions is None:
            dimensions = {"XLEN": 10, "YLEN": 20, "ZLEN": 30}
            if shape == "CYLI":
                dimensions = {"DIAM": 10, "HEIG": 30}
        return {
            "success": True, "complete": True, "shape": shape, "negative": False,
            "negative_children": [], "children": [], "read_errors": {},
            "element": {"refno": refno, "type": shape, "name": name},
            "dimensions": dimensions,
            "frame": {"world": {"position": position or [0, 0, 0], "orientation": {
                "x_axis": [1, 0, 0], "y_axis": [0, 1, 0], "z_axis": [0, 0, 1],
            }}},
        }

    @classmethod
    def occluded_pair(cls) -> list[dict]:
        # FRONT looks from +Y: a smaller, disjoint box behind the large box is
        # fully hidden even though it is not contained in the large solid.
        return [
            cls.geometry(),
            cls.geometry(refno="=1/3", name="/BACK", dimensions={"XLEN": 4, "YLEN": 2, "ZLEN": 6},
                         position=[0, -20, 0]),
        ]

    @staticmethod
    def envelopes(geometries: list[dict]) -> list[dict]:
        return [{"ok": True, "version": "E3D21", "result": row} for row in geometries]

    def test_discovery_is_read_only_explicit_and_bounded(self) -> None:
        catalog = {row["name"]: row for row in self.rpc("tools/list", {})["tools"]}
        tool = catalog["e3d_project_assembly_drawing"]
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        schema = tool["inputSchema"]
        self.assertEqual(schema["required"], ["targets"])
        self.assertFalse(schema["additionalProperties"])
        targets = schema["properties"]["targets"]
        self.assertEqual((targets["minItems"], targets["maxItems"]), (1, 16))
        self.assertEqual(set(targets["items"]["required"]), {"refno", "expected_type"})
        self.assertFalse(targets["items"]["additionalProperties"])
        for key, default in (
            ("hidden_line_removal", True), ("show_hidden_lines", False),
            ("include_svg", False), ("include_dxf", False),
            ("preview_width", 1200), ("preview_height", 440), ("radial_segments", 48),
        ):
            self.assertEqual(schema["properties"][key]["default"], default)

    def test_joint_hidden_line_removal_uses_all_sources_and_keeps_world_bounds(self) -> None:
        with patch.object(BridgeClient, "primitive_geometry",
                          side_effect=self.envelopes(self.occluded_pair())) as read, \
                patch.object(BridgeClient, "capture_view") as capture, \
                patch.object(BridgeClient, "update_primitive") as update:
            response = self.call(targets=[self.target(), self.target("=1/3")], views=["FRONT"])

        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertTrue(result["success"], result)
        self.assertTrue(result["complete"], result)
        self.assertEqual(result["coordinate_space"], "WORLD")
        self.assertEqual([source["requested_refno"] for source in result["sources"]], ["=1/2", "=1/3"])
        self.assertEqual([source["resolved_refno"] for source in result["sources"]], ["=1/2", "=1/3"])
        drawing = result["drawing"]
        self.assertEqual(drawing["source_bounds"]["min"], [-5.0, -21.0, -15.0])
        self.assertEqual(drawing["source_bounds"]["max"], [5.0, 10.0, 15.0])
        self.assertEqual(drawing["views"][0]["dimensions"], {"width": 10.0, "height": 30.0, "unit": "mm"})
        # Concatenating two individual drawings would leave 8 outline edges.
        # Joint occlusion must retain only the front box's four outer edges.
        lines = drawing["views"][0]["lines"]
        self.assertEqual(len(lines), 4)
        self.assertTrue(all(line["visibility"] == "visible" for line in lines))
        self.assertEqual(drawing["segment_count"], 4)
        self.assertNotIn("svg", drawing)
        self.assertNotIn("dxf", drawing)
        self.assertEqual([call.args[0] for call in read.call_args_list], ["=1/2", "=1/3"])
        capture.assert_not_called()
        update.assert_not_called()
        images = [item for item in response["content"] if item["type"] == "image"]
        self.assertEqual(len(images), 1)
        self.assertTrue(base64.b64decode(images[0]["data"]).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_hidden_sources_are_dashed_in_svg_dxf_and_custom_png_has_requested_size(self) -> None:
        with patch.object(BridgeClient, "primitive_geometry",
                          side_effect=self.envelopes(self.occluded_pair())) as read:
            response = self.call(targets=[self.target(), self.target("=1/3")], views=["FRONT"],
                                 show_hidden_lines=True, include_svg=True, include_dxf=True,
                                 preview_width=800, preview_height=600)
        self.assertFalse(response.get("isError", False), response)
        drawing = response["structuredContent"]["result"]["drawing"]
        lines = drawing["views"][0]["lines"]
        self.assertEqual(sum(line["visibility"] == "visible" for line in lines), 4)
        self.assertEqual(sum(line["visibility"] == "hidden" for line in lines), 4)
        self.assertIn('class="hidden"', drawing["svg"])
        self.assertIn('stroke-dasharray="6 4"', drawing["svg"])
        self.assertIn("8\nFRONT_HIDDEN", drawing["dxf"])
        self.assertIn("2\nE3D_HIDDEN", drawing["dxf"])
        image = next(item for item in response["content"] if item["type"] == "image")
        png = base64.b64decode(image["data"])
        self.assertEqual((int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")), (800, 600))
        self.assertEqual(read.call_count, 2)

    def test_all_malformed_inputs_are_rejected_before_first_native_read(self) -> None:
        first = self.target()
        invalid = (
            {}, {"targets": []}, {"targets": None}, {"targets": [first] * 17},
            {"targets": [first, first]},
            {"targets": [first, self.target("=0001/02")]},
            {"targets": [self.target("/PUMP/PART"), self.target("/pump/part")]},
            {"targets": [first, {"refno": "=1/3"}]},
            {"targets": [first, self.target("=1/3", "PANEL")]},
            {"targets": [first, {"refno": "=1/3", "expected_type": "BOX", "extra": 1}]},
            {"targets": [first], "coordinate_space": "local"},
            {"targets": [first], "views": ["FRONT", "FRONT"]},
            {"targets": [first], "feature_angle_degrees": True},
            {"targets": [first], "feature_angle_degrees": 181},
            {"targets": [first], "hidden_line_removal": "true"},
            {"targets": [first], "show_hidden_lines": True, "hidden_line_removal": False},
            {"targets": [first], "include_dxf": "true"},
            {"targets": [first], "preview_width": 2048, "preview_height": 2048},
            {"targets": [first], "preview_height": True},
            {"targets": [first], "radial_segments": 100},
            {"targets": [first], "extra": 1},
        )
        with patch.object(BridgeClient, "primitive_geometry") as primitive, \
                patch.object(BridgeClient, "panel_geometry") as panel:
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self.call(**arguments).get("isError", False))
        primitive.assert_not_called()
        panel.assert_not_called()
        self.health.assert_not_called()
        self.project.assert_not_called()

    def test_native_identity_failure_keeps_source_evidence_without_partial_drawing(self) -> None:
        pair = self.occluded_pair()
        pair[1]["element"]["refno"] = "=1/9"
        with patch.object(BridgeClient, "primitive_geometry", side_effect=self.envelopes(pair)) as read:
            response = self.call(targets=[self.target(), self.target("=1/3")])
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertFalse(result["success"])
        self.assertIsNone(result["drawing"])
        self.assertEqual(len(result["sources"]), 2)
        self.assertEqual(result["sources"][0]["resolved_refno"], "=1/2")
        second = result["sources"][1]
        self.assertEqual(second["requested_refno"], "=1/3")
        self.assertEqual(second["resolved_refno"], "=1/9")
        self.assertIn("element_reference_mismatch", str(second))
        self.assertFalse(any(item["type"] == "image" for item in response["content"]))
        self.assertEqual(read.call_count, 2)

    def test_partial_or_negative_source_never_returns_a_partial_drawing(self) -> None:
        for field, value in (("complete", False), ("negative", True)):
            pair = self.occluded_pair()
            pair[1][field] = value
            with self.subTest(field=field), patch.object(
                    BridgeClient, "primitive_geometry", side_effect=self.envelopes(pair)):
                response = self.call(targets=[self.target(), self.target("=1/3")])
                self.assertTrue(response.get("isError", False), response)
                result = response["structuredContent"]["result"]
                self.assertFalse(result["success"])
                self.assertIsNone(result["drawing"])
                self.assertEqual(len(result["sources"]), 2)

    def test_resolved_alias_duplicates_cannot_double_count_one_element(self) -> None:
        geometry = self.geometry(name="/OUTER")
        with patch.object(BridgeClient, "primitive_geometry",
                          side_effect=self.envelopes([geometry, copy.deepcopy(geometry)])) as read:
            response = self.call(targets=[self.target(), self.target("/OUTER")])
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertFalse(result["success"])
        self.assertIsNone(result["drawing"])
        self.assertEqual([source["resolved_refno"] for source in result["sources"]], ["=1/2", "=1/2"])
        self.assertEqual(read.call_count, 2)

    def test_global_curve_resolution_applies_to_curves_without_rejecting_box_target(self) -> None:
        pair = [
            self.geometry(),
            self.geometry(refno="=1/3", name="/CYLINDER", shape="CYLI", position=[30, 0, 0]),
        ]
        targets = [self.target(), self.target("=1/3", "CYLI")]
        with patch.object(BridgeClient, "primitive_geometry",
                          side_effect=self.envelopes(pair) + self.envelopes(pair)) as read:
            low = self.call(targets=targets, views=["TOP"], radial_segments=12)
            high = self.call(targets=targets, views=["TOP"], radial_segments=24)
        self.assertFalse(low.get("isError", False), low)
        self.assertFalse(high.get("isError", False), high)
        self.assertLess(low["structuredContent"]["result"]["drawing"]["segment_count"],
                        high["structuredContent"]["result"]["drawing"]["segment_count"])
        self.assertEqual(read.call_count, 4)

    def test_context_unavailable_prevents_source_reads(self) -> None:
        self.health.return_value = {**self.health_value, "session_id": ""}
        with patch.object(BridgeClient, "primitive_geometry") as read:
            response = self.call(targets=[self.target()])
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertIsNone(result["drawing"])
        self.assertEqual(result["error_code"], "assembly_context_unavailable")
        read.assert_not_called()

    def test_context_session_or_mdb_change_discards_drawing_but_preserves_sources(self) -> None:
        for field in ("session", "mdb"):
            with self.subTest(field=field):
                self.health.side_effect = [
                    self.health_value,
                    {**self.health_value, "session_id": "new-session"} if field == "session"
                    else self.health_value,
                ]
                self.project.side_effect = [
                    self.project_value,
                    {**self.project_value, "project": {**self.project_value["project"], "mdb": "OTHER"}}
                    if field == "mdb" else self.project_value,
                ]
                with patch.object(BridgeClient, "primitive_geometry",
                                  return_value=self.envelopes([self.geometry()])[0]) as read:
                    response = self.call(targets=[self.target()])
                self.assertTrue(response.get("isError", False), response)
                result = response["structuredContent"]["result"]
                self.assertIsNone(result["drawing"])
                self.assertFalse(result["success"])
                self.assertEqual(result["error_code"], "assembly_context_changed")
                self.assertEqual(result["sources"][0]["resolved_refno"], "=1/2")
                self.assertFalse(any(item["type"] == "image" for item in response["content"]))
                read.assert_called_once_with("=1/2")

    def test_first_source_failure_marks_remaining_sources_unread_without_replay(self) -> None:
        failed = self.geometry()
        failed["complete"] = False
        with patch.object(BridgeClient, "primitive_geometry",
                          return_value=self.envelopes([failed])[0]) as read, \
                patch.object(BridgeClient, "cancel_command") as cancel:
            response = self.call(targets=[self.target(), self.target("=1/3"), self.target("=1/4")])
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertIsNone(result["drawing"])
        self.assertEqual(len(result["sources"]), 3)
        self.assertIn("native_geometry_incomplete", str(result["sources"][0]))
        self.assertIn("not_read", str(result["sources"][1]))
        self.assertIn("not_read", str(result["sources"][2]))
        read.assert_called_once_with("=1/2")
        cancel.assert_not_called()

    def test_combined_mesh_limit_rejects_assembly_without_returning_partial_image(self) -> None:
        # Both boxes fit the lowered per-test limit, but their 16 combined
        # vertices exceed it. This exercises aggregate admission, not schema.
        with patch.object(BridgeClient, "primitive_geometry",
                          side_effect=self.envelopes(self.occluded_pair())) as read, \
                patch("aveva_e3d_mcp.drawing_assembly.MAX_VERTICES", 15):
            response = self.call(targets=[self.target(), self.target("=1/3")])
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertIsNone(result["drawing"])
        self.assertFalse(result["success"])
        self.assertEqual(len(result["sources"]), 2)
        self.assertIn("drawing_assembly_mesh_limit_exceeded", str(result))
        self.assertFalse(any(item["type"] == "image" for item in response["content"]))
        self.assertEqual(read.call_count, 2)


if __name__ == "__main__":
    unittest.main()

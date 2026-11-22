"""Optional native PAVE point evidence stays separate from panel validation."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server


def native_boundary_report(*, include_world: bool = False, failed_frame: str | None = None) -> dict:
    def frame_report(frame_refno: str, *, world: bool = False) -> dict:
        points = []
        for number in (0, 4, 6, 8):
            point = {"number": number, "exists": True, "exists_checked": True,
                     "position_mm": ([100.0 - number, -50.0 + number, 300.0] if world else
                                     [float(number), float(number), 0.0]),
                     "complete": True, "errors": {}}
            if number == 0:
                point["direction"] = [0.0, 1.0, 0.0] if world else [1.0, 0.0, 0.0]
            points.append(point)
        failed = failed_frame == ("WORLD" if world else "panel_local")
        errors = {}
        if failed:
            points[2].update(position_mm=None, complete=False, errors={"PPOS": "Native P6 is unavailable"})
            errors["P6.PPOS"] = "Native P6 is unavailable"
        return {"frame_refno": frame_refno, "points": points, "complete": not failed, "errors": errors}

    boundary = {"required": True, "panel_local": frame_report("=1/2")}
    if include_world:
        boundary["WORLD"] = frame_report("=1/0", world=True)
    result = {
        "success": True, "complete": True, "partial": False, "unit": "mm",
        "element": {"refno": "=1/2", "type": "PANE", "name": "/PANEL"},
        "coordinate_frame": "=1/2", "coordinate_source": "PAVE POS qualified WRT PANE",
        "loop_count": 1, "vertex_count": 1, "topology_valid": None,
        "read_errors": {}, "revision": "native-panel-revision",
        "boundary_points_requested": True, "boundary_points_complete": failed_frame is None,
        "boundary_points_vertex_count": 1, "boundary_points_curved_vertex_count": 1,
        "boundary_points_errors": {} if failed_frame is None else {"=1/4:P6": "Native P6 is unavailable"},
        "loops": [{"element": {"refno": "=1/3", "type": "PLOO"}, "complete": True,
                   "polygon_valid": None, "unsupported_features": [{"code": "fillet_radius", "radius_mm": 30.0}],
                   "vertices": [{"refno": "=1/4", "vertex_index": 0, "position": [10.0, 20.0, 0.0],
                                 "fillet_radius_mm": 30.0, "BANG": "45degree", "boundary_points": boundary}]}],
    }
    if include_world:
        # Existing PAVE POS reads have their own completeness. A failed WORLD
        # boundary P-point must not overwrite this independent observation.
        result.update(world_positions_requested=True, world_positions_complete=True,
                      world_position_frame="=1/0", world_read_errors={})
        result["loops"][0]["vertices"][0].update(position_world_mm=[80.0, -40.0, 300.0],
                                                 world_position_complete=True)
    return {"ok": True, "version": "E3D21", "result": result}


class PanelBoundaryPointsToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(mode="bridge", version="E3D21", allow_commands=False)
        self.server = create_server(self.settings)
        self.client = BridgeClient(self.settings)

    def call(self, arguments: dict, *, server=None) -> dict:
        return (server or self.server)._dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "e3d_panel_geometry", "arguments": arguments},
        })["result"]

    def test_tool_forwards_only_true_flags_in_one_read_only_call(self) -> None:
        for world in (False, True):
            for boundary in (False, True):
                with self.subTest(include_world=world, include_boundary=boundary):
                    payload = native_boundary_report(include_world=world)
                    with patch.object(BridgeClient, "panel_geometry", return_value=payload) as geometry, \
                            patch.object(BridgeClient, "command") as command, \
                            patch.object(BridgeClient, "capture_view") as capture:
                        response = self.call({"refno": " =1/2 ", "include_world": world,
                                              "include_boundary": boundary})
                    self.assertFalse(response.get("isError", False), response)
                    kwargs = {}
                    if world:
                        kwargs["include_world"] = True
                    if boundary:
                        kwargs["include_boundary"] = True
                    geometry.assert_called_once_with("=1/2", **kwargs)
                    command.assert_not_called()
                    capture.assert_not_called()

    def test_bridge_all_flag_combinations_use_one_get_and_omit_false_query_flags(self) -> None:
        for world in (False, True):
            for boundary in (False, True):
                with self.subTest(include_world=world, include_boundary=boundary):
                    payload = native_boundary_report(include_world=world) if boundary else {
                        "ok": True, "result": {"success": True, "complete": True}}
                    if world:
                        payload["result"]["world_positions_requested"] = True
                    original = deepcopy(payload)
                    query = {"refno": "=1/2"}
                    if world:
                        query["include_world"] = "true"
                    if boundary:
                        query["include_boundary"] = "true"
                    with patch.object(self.client, "_request", return_value=payload) as request:
                        response = self.client.panel_geometry("=1/2", version="E3D21", include_world=world,
                                                              include_boundary=boundary)
                    request.assert_called_once_with("GET", "/api/v1/panel/geometry", query=query, version="E3D21")
                    self.assertEqual(response, original)
                    self.assertEqual(payload, original)

    def test_both_flags_require_booleans_before_tool_or_bridge_reads(self) -> None:
        for field in ("include_world", "include_boundary"):
            for value in (None, 0, 1, "true", "false", [], {}):
                with self.subTest(field=field, value=value):
                    with patch.object(BridgeClient, "panel_geometry") as geometry:
                        response = self.call({"refno": "/PANEL", field: value})
                    self.assertTrue(response.get("isError", False), response)
                    geometry.assert_not_called()
                    with patch.object(self.client, "_request") as request:
                        with self.assertRaises(ValueError):
                            self.client.panel_geometry("/PANEL", **{field: value})
                    request.assert_not_called()

    def test_partial_boundary_evidence_is_preserved_without_changing_panel_completeness(self) -> None:
        for world, failed_frame in ((False, "panel_local"), (True, "panel_local"), (True, "WORLD")):
            with self.subTest(include_world=world, failed_frame=failed_frame):
                payload = native_boundary_report(include_world=world, failed_frame=failed_frame)
                original = deepcopy(payload)
                with patch.object(BridgeClient, "_request", return_value=payload) as request:
                    response = self.call({"refno": "/PANEL", "include_boundary": True, "include_world": world})
                request.assert_called_once()
                self.assertFalse(response.get("isError", False), response)
                self.assertEqual(response["structuredContent"], original)
                self.assertEqual(json.loads(response["content"][0]["text"]), original)
                self.assertEqual(payload, original)
                result = response["structuredContent"]["result"]
                self.assertTrue(result["complete"])
                self.assertFalse(result["boundary_points_complete"])
                self.assertIsNone(result["topology_valid"])
                self.assertEqual(result["revision"], "native-panel-revision")
                if world:
                    self.assertTrue(result["world_positions_complete"])
                self.assertFalse(any(item["type"] == "image" for item in response["content"]))

    def test_boundary_evidence_does_not_replace_an_existing_panel_read_failure(self) -> None:
        payload = native_boundary_report()
        payload["result"].update(success=False, complete=False, partial=True,
                                  read_errors={"PANE.THICKN": "native thickness is unavailable"},
                                  topology_valid=False)
        original = deepcopy(payload)
        with patch.object(BridgeClient, "_request", return_value=payload):
            response = self.call({"refno": "/PANEL", "include_boundary": True})
        self.assertTrue(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"], original)
        self.assertFalse(response["structuredContent"]["result"]["complete"])
        self.assertTrue(response["structuredContent"]["result"]["boundary_points_complete"])

    def test_mock_mode_never_fabricates_native_boundary_points(self) -> None:
        settings = Settings(mode="mock", version="E3D21", allow_commands=False)
        client, server = BridgeClient(settings), create_server(settings)
        for world in (False, True):
            with self.subTest(include_world=world), patch.object(client, "_request") as request:
                payload = client.panel_geometry("/PANEL", include_boundary=True, include_world=world)
                response = self.call({"refno": "/PANEL", "include_boundary": True,
                                      "include_world": world}, server=server)
            request.assert_not_called()
            self.assertTrue(response.get("isError", False), response)
            self.assertFalse(payload["result"]["available"])
            self.assertFalse(payload["result"]["success"])
            self.assertFalse(payload["result"].get("boundary_points_complete", False))
            self.assertNotIn("loops", payload["result"])
            self.assertNotIn("boundary_points", payload["result"])
            self.assertFalse(any(item["type"] == "image" for item in response["content"]))

    def test_old_adapter_without_boundary_marker_cannot_silently_satisfy_the_request(self) -> None:
        for world in (False, True):
            with self.subTest(include_world=world):
                old_payload = {"ok": True, "result": {"success": True, "complete": True}}
                if world:
                    old_payload["result"]["world_positions_requested"] = True
                with patch.object(self.client, "_request", return_value=old_payload) as request:
                    with self.assertRaises(BridgeError) as raised:
                        self.client.panel_geometry("/PANEL", include_boundary=True, include_world=world)
                request.assert_called_once()
                self.assertIn("boundary", str(raised.exception).lower())

    def test_native_error_without_feature_markers_is_not_relabelled_as_unsupported(self) -> None:
        failure = {"ok": False, "version": "E3D21", "error": "Native database is unavailable",
                   "error_code": "native_database_fault", "native_diagnostics": {"stage": "PAVE.PPOS"}}
        for world in (False, True):
            with self.subTest(include_world=world):
                original = deepcopy(failure)
                with patch.object(self.client, "_request", return_value=failure) as request:
                    response = self.client.panel_geometry("/PANEL", include_boundary=True, include_world=world)
                request.assert_called_once()
                self.assertEqual(response, original)
                with patch.object(BridgeClient, "_request", return_value=failure) as request:
                    response = self.call({"refno": "/PANEL", "include_boundary": True, "include_world": world})
                request.assert_called_once()
                self.assertTrue(response.get("isError", False), response)
                self.assertEqual(response["structuredContent"], original)
                self.assertNotIn("not support", response["content"][0]["text"].lower())

    def test_transport_error_keeps_its_original_diagnostics_without_recovery(self) -> None:
        error = BridgeError("Native PAVE read timed out", transport_error=True,
                            diagnostics={"native_diagnostics": {"stage": "PAVE.PPOS"}})
        with patch.object(self.client, "_request", side_effect=error) as request, \
                patch.object(self.client, "_recover_command_timeout") as recover, \
                patch.object(self.client, "cancel_command") as cancel:
            with self.assertRaises(BridgeError) as raised:
                self.client.panel_geometry("/PANEL", include_boundary=True, include_world=True)
        self.assertIs(raised.exception, error)
        request.assert_called_once()
        recover.assert_not_called()
        cancel.assert_not_called()


if __name__ == "__main__":
    unittest.main()

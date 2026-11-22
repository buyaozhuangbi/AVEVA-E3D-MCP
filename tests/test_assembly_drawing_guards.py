"""Failure-path checks around the native read boundaries for assembly drawings."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server
from tests import test_project_assembly_drawing_tool as fixtures


class AssemblyDrawingGuardTests(unittest.TestCase):
    def setUp(self):
        self.ready = {
            "ok": True, "connected": True, "version": "E3D21", "session_id": "assembly-test",
            "state": {"busy": False, "database_ready": True, "recovery_required": False,
                      "native_database_fault": {"has_fault": False}},
        }
        self.project_value = {
            "ok": True, "version": "E3D21",
            "project": {"connected": True, "recovery_only": False, "name": "APS", "mdb": "ALL"},
        }
        for name, value in (("health", self.ready), ("project", self.project_value)):
            patcher = patch.object(BridgeClient, name, return_value=value)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        self.server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))

    @staticmethod
    def target(refno="=1/2"):
        return {"refno": refno, "expected_type": "BOX"}

    @staticmethod
    def geometry_envelope():
        return {"ok": True, "version": "E3D21",
                "result": fixtures.ProjectAssemblyDrawingToolTests.geometry()}

    def call(self, **arguments):
        response = self.server._dispatch({
            "jsonrpc": "2.0", "id": 10, "method": "tools/call",
            "params": {"name": "e3d_project_assembly_drawing", "arguments": {
                "targets": [self.target()], **arguments,
            }},
        })["result"]
        return response

    def assert_failed_without_drawing(self, response):
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertFalse(result["success"])
        self.assertIsNone(result["drawing"])
        self.assertFalse(any(item["type"] == "image" for item in response["content"]))
        return result

    def test_unknown_or_unready_initial_state_prevents_geometry_reads(self):
        changes = [
            {"connected": False}, {"session_id": None}, {"version": None},
            {"state": {**self.ready["state"], "busy": True}},
            {"state": {**self.ready["state"], "database_ready": None}},
            {"state": {**self.ready["state"], "recovery_required": True}},
            {"state": {**self.ready["state"], "native_database_fault": {}}},
            {"state": {**self.ready["state"], "native_database_fault": {"has_fault": True}}},
        ]
        with patch.object(BridgeClient, "primitive_geometry") as read:
            for change in changes:
                with self.subTest(change=change):
                    self.health.return_value = {**self.ready, **change}
                    result = self.assert_failed_without_drawing(self.call())
                    self.assertEqual(result["error_code"], "assembly_context_unavailable")
                    self.assertEqual(result["sources"][0]["read_status"], "not_read")
            read.assert_not_called()
            self.project.assert_not_called()

    def test_missing_or_mismatched_initial_project_identity_prevents_reads(self):
        cases = [
            {"ok": False}, {"version": "E3D22"}, {"project": None},
            {"project": {**self.project_value["project"], "connected": False}},
            {"project": {**self.project_value["project"], "recovery_only": True}},
            {"project": {**self.project_value["project"], "name": ""}},
            {"project": {**self.project_value["project"], "mdb": None}},
        ]
        with patch.object(BridgeClient, "primitive_geometry") as read:
            for changes in cases:
                with self.subTest(changes=changes):
                    self.project.return_value = {**self.project_value, **changes}
                    result = self.assert_failed_without_drawing(self.call())
                    self.assertEqual(result["error_code"], "assembly_context_unavailable")
            read.assert_not_called()

    def test_source_bridge_error_retains_diagnostics_and_never_replays_or_cancels(self):
        diagnostics = {"state": {"recovery_required": True},
                       "result": {"success": False, "error": "Native fault", "routine": "DbRead"}}
        error = BridgeError("Native geometry transport failed", transport_error=True,
                            diagnostics=diagnostics)
        with patch.object(BridgeClient, "primitive_geometry", side_effect=error) as read, \
                patch.object(BridgeClient, "cancel_command") as cancel:
            result = self.assert_failed_without_drawing(self.call(
                targets=[self.target(), self.target("=1/3")]))
        self.assertEqual(result["error_code"], "assembly_source_unavailable")
        self.assertEqual(result["sources"][0]["native_diagnostics"], diagnostics)
        self.assertEqual(result["sources"][0]["read_status"], "failed")
        self.assertEqual(result["sources"][1]["read_status"], "not_read")
        read.assert_called_once_with("=1/2")
        cancel.assert_not_called()

    def test_returned_native_failure_keeps_entire_error_envelope(self):
        failure = {
            "ok": False, "version": "E3D21", "error": "Native database fault",
            "error_code": "native_database_fault",
            "state": {"recovery_required": True, "native_database_fault": {"has_fault": True}},
            "result": {"success": False, "routine": "DbRead", "read_errors": {"POS": "unavailable"}},
        }
        with patch.object(BridgeClient, "primitive_geometry", return_value=failure) as read, \
                patch.object(BridgeClient, "cancel_command") as cancel:
            result = self.assert_failed_without_drawing(self.call(
                targets=[self.target(), self.target("=1/3")]))
        self.assertEqual(result["sources"][0]["error_code"], "native_database_fault")
        self.assertEqual(result["sources"][0]["read_error"], failure)
        self.assertEqual(result["sources"][1]["read_status"], "not_read")
        read.assert_called_once_with("=1/2")
        cancel.assert_not_called()

    def test_after_context_failure_preserves_completed_sources_and_refuses_export(self):
        error = BridgeError("Health disconnected", transport_error=True,
                            diagnostics={"state": {"recovery_required": True}})
        self.health.side_effect = [self.ready, error]
        with patch.object(BridgeClient, "primitive_geometry",
                          return_value=self.geometry_envelope()) as read, \
                patch("aveva_e3d_mcp.tools.export_orthographic_dxf") as export:
            result = self.assert_failed_without_drawing(self.call(include_dxf=True))
        self.assertEqual(result["error_code"], "assembly_context_unavailable")
        self.assertTrue(result["sources"][0]["success"])
        self.assertIsNone(result["context"]["boundary_agreement"])
        self.assertEqual(result["context"]["after"]["error_code"], "context_read_failed")
        read.assert_called_once_with("=1/2")
        export.assert_not_called()

    def test_changed_project_name_and_version_are_not_treated_as_same_world_context(self):
        for changed_field in ("name", "version"):
            with self.subTest(changed_field=changed_field):
                later_health = copy.deepcopy(self.ready)
                later_project = copy.deepcopy(self.project_value)
                if changed_field == "name":
                    later_project["project"]["name"] = "OTHER"
                else:
                    later_health["version"] = later_project["version"] = "E3D22"
                self.health.side_effect = [self.ready, later_health]
                self.project.side_effect = [self.project_value, later_project]
                with patch.object(BridgeClient, "primitive_geometry",
                                  return_value=self.geometry_envelope()):
                    result = self.assert_failed_without_drawing(self.call())
                self.assertEqual(result["error_code"], "assembly_context_changed")
                self.assertFalse(result["context"]["boundary_agreement"])
                self.assertTrue(result["sources"][0]["success"])

    def test_source_version_mismatch_fails_before_drawing(self):
        source = self.geometry_envelope()
        source["version"] = "E3D22"
        with patch.object(BridgeClient, "primitive_geometry", return_value=source):
            result = self.assert_failed_without_drawing(self.call())
        self.assertEqual(result["sources"][0]["error_code"], "source_version_mismatch")
        self.assertIsNone(result["source_mesh"])

    def test_export_failure_keeps_read_evidence_without_partial_image_or_drawing(self):
        with patch.object(BridgeClient, "primitive_geometry", return_value=self.geometry_envelope()) as read, \
                patch("aveva_e3d_mcp.tools.export_orthographic_dxf",
                      side_effect=ValueError("geometry_below_dxf_precision")) as export, \
                patch("aveva_e3d_mcp.tools.rasterize_orthographic_png") as raster:
            result = self.assert_failed_without_drawing(self.call(include_dxf=True))
        self.assertEqual(result["error_code"], "drawing_export_failed")
        self.assertEqual(result["export_error"], "geometry_below_dxf_precision")
        self.assertTrue(result["sources"][0]["success"])
        self.assertTrue(result["context"]["boundary_agreement"])
        read.assert_called_once_with("=1/2")
        export.assert_called_once()
        raster.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""JSON-RPC regression coverage for the bounded diagnostic MCP workflow.

All native responses are frozen synthetic fixtures. No artifact files, live MCP
registration, E3D process, or HTTP transport are used by these tests.
"""
from __future__ import annotations

import base64
import copy
import json
import struct
import sys
import unittest
import zlib
from contextlib import ExitStack, contextmanager
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.clipping_observation import clipping_observation_result
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.element_diagnosis import diagnose_element
from aveva_e3d_mcp.tools import create_server


REFNO = "=2000000001/2633"
PANE_REFNO = "=2000000001/3610"
NEGATIVE_REFNO = "=2000000001/2634"
OLD_TOOLS = set("""
e3d_status e3d_project_info e3d_database_state e3d_spatial_map_status e3d_query
e3d_read_logs e3d_read_messages e3d_command_state e3d_cancel_command e3d_drawlist_snapshot
e3d_fit_view e3d_view_state e3d_view_clipping_state e3d_observe_view e3d_display_state e3d_display_snapshot
e3d_set_primitive_visual e3d_remove_drawlist_member e3d_set_view e3d_set_camera e3d_restore_view
e3d_set_drawlist_visual e3d_element_snapshot e3d_list_members e3d_find_descendants e3d_validate_model
e3d_read_attributes e3d_read_attributes_batch e3d_element_access e3d_measure_elements_batch e3d_element_schema
e3d_connection_points e3d_check_connection_pair e3d_spatial_info e3d_graphical_selection
e3d_set_graphical_selection e3d_delete_element e3d_set_attribute e3d_execute_pml e3d_execute_pml_script
e3d_pml_script_status e3d_reenter_design e3d_execute_pml_batch e3d_create_element e3d_copy_element
e3d_create_panel e3d_panel_geometry e3d_update_panel e3d_primitive_geometry e3d_update_primitive
e3d_set_transform e3d_capture_view e3d_geometry_bounds e3d_geometry_overlap_candidates
e3d_native_clash_check e3d_geometry_volume
""".split())
READS = ("health", "project", "view_state", "view_clipping_state", "graphical_selection", "element", "element_schema",
         "geometry_bounds", "panel_geometry", "primitive_geometry", "display_state", "capture_view")
ELEMENT_READS = {"element", "element_schema", "geometry_bounds", "panel_geometry",
                 "primitive_geometry", "display_state"}


def envelope(result):
    return {"ok": True, "version": "E3D21", "result": result}


def healthy_status():
    return {"ok": True, "connected": True, "version": "E3D21", "session_id": "frozen-session",
            "bridge_build": "frozen-build", "state": {
                "database_ready": True, "busy": False, "recovery_required": False,
                "native_database_fault": {"has_fault": False},
                "execution": {"native_operation_active": False, "dispatcher_ready": True},
                "native_dialogs": {"complete": True, "standard_dialog_present": False, "dialogs": []},
                "pml_alerts": {"complete": True, "alert_present": False, "alerts": []},
            }}


def camera(refno=REFNO):
    return {"success": True, "available": True, "partial": False, "errors": {},
            "view_id": 11, "drawlist_id": 17, "centre": [12.0, 24.0, 36.0],
            "direction": [1.0, 1.0, -1.0], "radius": 300.0,
            "bearing_degrees": 45.0, "elevation_degrees": -35.26438968,
            "extra_twist_degrees": 0.0, "perspective": False, "shaded": True,
            "width": 1600, "height": 1200, "current_element_refno": refno,
            "drawlist_member_count": 2, "drawlist_membership_fingerprint": "a" * 64,
            "drawlist_visuals_fingerprint": "b" * 64}


def clipping_state(view_id=11):
    # A disabled native clip-box can legitimately retain zero vectors and a
    # colour outside an assumed 0..255 palette. Do not invent geometry rules.
    return {"success": True, "available": True, "complete": True, "status": "complete",
            "source": "E3D SDK View3D.Clipping public getters", "view_id": view_id,
            "clipping_enabled": False, "capping": False, "cap_colour": 271,
            "box_data": {"available": True, "lengths": [0.0, 0.0, 0.0],
                "position": [0.0, 0.0, 0.0], "orientation": [0.0, 0.0, 0.0],
                "source": "View3DClipping.GetClipBoxData",
                "interpretation": "SDK stored clip-box data; it is not verified to describe planes defined individually."},
            "box_faces": [{"face": face, "defined": False, "active": False} for face in ("W", "S", "D", "E", "N", "U")],
            "read_errors": {}, "coordinate_frame": "SDK native (unverified)",
            "units": "SDK native (unverified)", "view_unchanged": True,
            "side_effects": "May initialize or warm the current view's managed clipping cache."}


def capture_payload(width=1280, height=960, refno=REFNO):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress((b"\x00" + bytes(width * 3)) * height)) + chunk(b"IEND", b""))
    native = {"schema": 1, "complete": True, "view_object_unchanged": True, "read_errors": {},
              "before_refresh": camera(refno), "before_bitmap": camera(refno), "after_bitmap": camera(refno)}
    return envelope({"success": True, "mime_type": "image/png", "width": width, "height": height,
        "image_base64": base64.b64encode(png).decode("ascii"), "source": "E3D View3D.GetBitmap",
        "view_id": 11, "drawlist_id": 17, "native_capture_observation": native,
        "observation": {"schema": 1, "status": "consistent", "complete": True, "consistent": True,
            "changed_fields": [], "read_errors": {}, "native": copy.deepcopy(native),
            "before": envelope({"success": True, "complete": True, "stable": True,
                "states": [envelope(camera(refno)), envelope(camera(refno)), envelope(camera(refno))],
                "context_before": healthy_status(), "context_after": healthy_status(),
                "changed_fields": [], "read_errors": {}}),
            "after": envelope(camera(refno)), "context_after": healthy_status()}})


class FrozenBridge:
    """Method-level bridge fixture, with unexpected transport/write access denied."""

    def __init__(self, native_type="CYLI"):
        self.events = []
        self.counts = {}
        self.overrides = {}
        self.refno = PANE_REFNO if native_type == "PANE" else REFNO
        self.identity = {"refno": self.refno, "type": native_type, "name": "/FROZEN-PUMP-PART"}
        negative = {"element": {"refno": NEGATIVE_REFNO, "type": "NCYL", "name": "/FROZEN-INNER-BORE"},
                    "shape": "NCYL", "negative": True, "complete": True,
                    "dimensions": {"DIAM": 60.0, "HEIG": 120.0}, "children": []}
        self.responses = {
            "health": healthy_status(),
            "project": {"ok": True, "version": "E3D21", "project": {"connected": True, "recovery_only": False,
                "name": "APS", "mdb": "ALL", "user": "SYSTEM", "current_element": self.refno}},
            "view_state": envelope(camera(self.refno)),
            "view_clipping_state": envelope(clipping_state()),
            "graphical_selection": envelope({"success": True, "available": True, "complete": True,
                "ce_unchanged": True, "context_unchanged": True, "view_id": 11, "drawlist_id": 17,
                "ce_before": self.refno, "ce_after": self.refno, "elements": [], "selection_count": 0,
                "returned_count": 0, "read_errors": []}),
            "element": {"ok": True, "version": "E3D21", "element": {
                **self.identity, "members": [], "snapshot_info": {"complete": True, "read_errors": [],
                    "truncated": False, "element_count": 1, "depth": 2, "limit": 100}}},
            "element_schema": envelope({"success": True, "element": copy.deepcopy(self.identity),
                "child_types": [], "owner_types": [], "read_errors": {},
                "attribute_page": {"offset": 0, "limit": 50, "has_more": False, "returned_count": 6,
                                   "total_count": 6, "next_offset": None},
                "attributes": [{"requested_name": name, "known": True, "supported": True}
                               for name in ("NAME", "POS", "ORI", "DIAM", "HEIG", "THICKN")]}),
            "geometry_bounds": envelope({"success": True, "available": True, "partial": False,
                "refno": self.refno, "type": native_type, "min": [-60.0, -60.0, -75.0],
                "max": [60.0, 60.0, 75.0], "unit": "mm", "coordinate_frame": "SDK native (unverified)",
                "errors": {}, "native_attribute_errors": {}}),
            "primitive_geometry": envelope({"success": True, "complete": True, "partial": False,
                "element": copy.deepcopy(self.identity), "shape": native_type, "negative": False,
                "dimensions": {"DIAM": 120.0, "HEIG": 150.0}, "negative_children": [negative],
                "target_geometry": {"element": copy.deepcopy(self.identity), "children": [copy.deepcopy(negative)]},
                "editable": True, "revision": "c" * 64, "read_errors": {}}),
            "panel_geometry": envelope({"success": True, "complete": True, "partial": False,
                "element": copy.deepcopy(self.identity), "coordinate_frame": self.refno,
                "coordinate_source": "PAVE POS qualified WRT PANE", "thickness_mm": 3.0,
                "loops": [{"element": {"refno": "=2000000001/3611", "type": "PLOO"},
                    "complete": True, "polygon_valid": True, "vertices": [
                        {"local_xyz": [0.0, 0.0, 0.0]}, {"local_xyz": [100.0, 0.0, 0.0]},
                        {"local_xyz": [100.0, 40.0, 0.0]}, {"local_xyz": [0.0, 40.0, 0.0]}],
                    "read_errors": {}}], "topology_valid": True, "loop_count": 1,
                "vertex_count": 4, "unsupported_features": [], "read_errors": {}, "revision": "d" * 64}),
            # The actual historical targets were absent from the current Drawlist.
            # This frozen state must never be rewritten as a positive visibility claim.
            "display_state": envelope({"success": True, "available": True, "complete": True,
                "refno": self.refno, "element": copy.deepcopy(self.identity), "sdk_exists": False,
                "exact_member": False, "member_scan_complete": True, "primitive_scan_complete": True,
                "primitive_count": 0, "primitive_match_count": 0, "primitive_matches": [],
                "resolved_member": None, "resolved_member_matches_target": False,
                "pixel_visibility_verified": False, "read_errors": [], "ce_unchanged": True,
                "ce_before": self.refno, "ce_after": self.refno}),
        }

    def read(self, name, *args, **kwargs):
        self.events.append((name, args, kwargs))
        self.counts[name] = self.counts.get(name, 0) + 1
        if name in self.overrides:
            value = self.overrides[name](self, *args, **kwargs)
        elif name == "capture_view":
            value = capture_payload(*args[:2], refno=self.refno)
        else:
            value = self.responses[name]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)

    @property
    def native_events(self):
        return [name for name, _, _ in self.events if name not in {"health", "project"}]

    @contextmanager
    def installed(self):
        with ExitStack() as stack:
            for name in READS:
                stack.enter_context(patch.object(BridgeClient, name,
                    side_effect=lambda *args, _name=name, **kwargs: self.read(_name, *args, **kwargs)))
            # Any accidental new API or mutating helper eventually reaches _request.
            stack.enter_context(patch.object(BridgeClient, "_request", side_effect=AssertionError("Live transport forbidden")))
            for name in ("command", "cancel_command", "set_view", "set_camera", "restore_view",
                         "set_drawlist_visual", "set_transform", "create_element", "update_panel", "update_primitive"):
                stack.enter_context(patch.object(BridgeClient, name,
                    side_effect=AssertionError("Diagnostic must not call " + name)))
            yield self


class ElementDiagnosisToolTests(unittest.TestCase):
    def setUp(self):
        self.server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))

    def rpc(self, method, params, server=None):
        output = StringIO()
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        with patch("sys.stdout", output):
            (server or self.server).serve([request + "\n"])
        response = json.loads(output.getvalue())
        self.assertNotIn("error", response, response)
        return response["result"]

    def call(self, arguments, server=None):
        return self.rpc("tools/call", {"name": "e3d_diagnose_element", "arguments": arguments}, server)

    def result(self, response):
        self.assertIn("structuredContent", response, response)
        self.assertTrue(response["structuredContent"]["ok"], response)
        return response["structuredContent"]["result"]

    def assert_no_image_binary(self, value):
        if isinstance(value, dict):
            self.assertNotIn("image_base64", value)
            for item in value.values():
                self.assert_no_image_binary(item)
        elif isinstance(value, list):
            for item in value:
                self.assert_no_image_binary(item)

    def assert_stopped(self, response, fixture, last_native=None):
        self.assertTrue(response.get("isError"), response)
        result = self.result(response)
        self.assertFalse(result["success"])
        self.assertFalse(result["workflow_completed"])
        self.assertTrue(result["errors"])
        self.assertTrue(result["stopped_stage"])
        self.assertEqual(result["retries"], 0)
        if last_native is None:
            self.assertFalse(fixture.native_events)
        else:
            self.assertTrue(fixture.native_events, result["errors"])
            self.assertEqual(fixture.native_events[-1], last_native)
        return result

    def test_catalog_includes_expected_tools_and_preserves_old_names(self):
        catalog = self.rpc("tools/list", {})["tools"]
        self.assertEqual(len(OLD_TOOLS), 56)
        self.assertEqual({item["name"] for item in catalog}, OLD_TOOLS | {"e3d_context_snapshot", "e3d_set_attributes", "e3d_diagnose_element", "e3d_diagnostic_receipt", "e3d_compare_diagnostic_receipts", "e3d_transform_coordinates", "e3d_trace_piping_connectivity", "e3d_read_message_window", "e3d_geometry_relation", "e3d_geometry_relation_batch", "e3d_panel_loop_relations", "e3d_primitive_mesh", "e3d_primitive_csg_mesh", "e3d_project_primitive_csg_drawing", "e3d_panel_mesh", "e3d_panel_curve_boundary", "e3d_project_panel_curve_drawing", "e3d_project_panel_curve_assembly_drawing", "e3d_project_panel_boundary_drawing", "e3d_project_panel_assembly_boundary_drawing", "e3d_validate_triangle_mesh", "e3d_measure_triangle_mesh", "e3d_element_mesh", "e3d_compare_element_meshes", "e3d_project_element_drawing", "e3d_project_assembly_drawing", "e3d_project_material_assembly_drawing", "e3d_project_material_assembly_sheet", "e3d_project_material_assembly_tiles", "e3d_equipment_layout_sheet", "e3d_nozzle_datum_sheet", "e3d_equipment_outline_sheet", "e3d_nozzle_axis_sheet", "e3d_equipment_layout_tiles", "e3d_nozzle_outline_sheet", "e3d_compose_drawing_sheet", "e3d_compose_drawing_atlas", "e3d_section_element_drawing", "e3d_section_assembly_drawing", "e3d_nozzle_orientation_drawing", "e3d_equipment_layout_drawing", "e3d_generated_csg", "e3d_database_identity_snapshot", "e3d_database_preflight", "e3d_piping_branch_state", "e3d_equipment_manifest", "e3d_pipe_member_manifest", "e3d_equipment_model_plan", "e3d_piping_route_plan", "e3d_paragon_catalogue_manifest", "e3d_module_context", "e3d_paragon_catalogue_schema", "e3d_piping_connectivity_snapshot", "e3d_equipment_child_schema", "e3d_piping_specification_read", "e3d_team_database_identity_manifest", "e3d_paragon_generation_plan", "e3d_paragon_component_template", "e3d_paragon_reference_trace", "e3d_paragon_component_copy", "e3d_paragon_component_copy_status", "e3d_paragon_component_parameters_set", "e3d_paragon_component_parameters_status", "e3d_paragon_generate_from_standard", "e3d_paragon_component_create", "e3d_paragon_component_create_status", "e3d_piping_route_create", "e3d_piping_route_verify", "e3d_modeling_team_plan", "e3d_modeling_team_execute"})
        self.assertEqual(len(catalog), 126)
        definition = next(item for item in catalog if item["name"] == "e3d_diagnose_element")
        schema = definition["inputSchema"]
        self.assertEqual(schema["required"], ["refno"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["properties"]), {"refno", "include_image", "include_clipping", "capture_width", "capture_height", "evidence_detail", "retain_receipt"})
        self.assertFalse(schema["properties"]["include_image"]["default"])
        self.assertFalse(schema["properties"]["include_clipping"]["default"])
        for field, default in (("capture_width", 1280), ("capture_height", 960)):
            value = schema["properties"][field]
            self.assertEqual((value["type"], value["minimum"], value["maximum"], value["default"]),
                             ("integer", 64, 2048, default))
        self.assertTrue(definition["annotations"]["readOnlyHint"])
        self.assertFalse(definition["annotations"]["destructiveHint"])

    def test_default_runs_without_write_permission_and_does_not_capture(self):
        fixture = FrozenBridge()
        with fixture.installed():
            response = self.call({"refno": " =2000000001/2633 "})
        self.assertFalse(response.get("isError", False), response)
        result = self.result(response)
        self.assertTrue(result["success"])
        self.assertTrue(result["workflow_completed"])
        self.assertEqual(result["target"], fixture.identity)
        self.assertEqual(result["requested_refno"], REFNO)
        self.assertTrue(result["read_only"])
        for field in ("model_writes", "view_setters", "restarts", "retries"):
            self.assertEqual(result[field], 0)
        self.assertNotIn("capture_view", fixture.native_events)
        self.assertNotIn("view_clipping_state", fixture.native_events)
        self.assertFalse(result["clipping_requested"])
        self.assertNotIn("baseline_clipping", result["evidence"])
        self.assertNotIn("final_clipping", result["evidence"])
        self.assertNotIn("clipping_samples_unchanged", result["observations"])
        self.assertEqual(result["observations"]["capture"]["status"], "not_requested")
        self.assertIs(result["observations"]["observed_window_unchanged"], True)
        self.assertFalse(any(part["type"] == "image" for part in response["content"]))
        self.assertEqual(json.loads(response["content"][0]["text"]), response["structuredContent"])

    def test_explicit_false_clipping_preserves_default_native_stages_and_observations(self):
        default, disabled = FrozenBridge(), FrozenBridge()
        with default.installed():
            default_result = self.result(self.call({"refno": REFNO}))
        with disabled.installed():
            disabled_result = self.result(self.call({"refno": REFNO, "include_clipping": False}))
        self.assertEqual(default.events, disabled.events)
        self.assertEqual(default_result, disabled_result)

    def test_clipping_opt_in_reads_twice_with_fresh_gates_and_retains_full_samples(self):
        fixture = FrozenBridge()
        with fixture.installed():
            response = self.call({"refno": REFNO, "include_clipping": True})
        self.assertFalse(response.get("isError", False), response)
        result = self.result(response)
        self.assertTrue(result["clipping_requested"])
        self.assertEqual(fixture.counts["view_clipping_state"], 2)
        self.assertEqual(fixture.native_events, ["view_state", "view_clipping_state", "graphical_selection",
            "element", "element_schema", "geometry_bounds", "primitive_geometry", "display_state",
            "view_state", "view_clipping_state", "graphical_selection"])
        for index, (name, _, _) in enumerate(fixture.events):
            if name == "view_clipping_state":
                self.assertEqual([row[0] for row in fixture.events[index - 2:index]], ["health", "project"])
        for key in ("baseline_clipping", "final_clipping"):
            self.assertEqual(result["evidence"][key], fixture.responses["view_clipping_state"])
        self.assertIs(result["observations"]["clipping_samples_unchanged"], True)
        self.assertIs(result["observations"]["observed_window_unchanged"], True)
        self.assertFalse(result["scope"]["pixel_visibility_verified"])
        self.assertIn("unverified", result["scope"]["clipping"])
        self.assertNotIn("view_setters", json.dumps(result["evidence"]))

    def test_clipping_change_and_image_are_reported_without_clipping_or_visibility_claims(self):
        fixture = FrozenBridge()
        def clipping(current):
            value = copy.deepcopy(current.responses["view_clipping_state"])
            if current.counts["view_clipping_state"] > 1:
                value["result"]["clipping_enabled"] = True
            return value
        fixture.overrides["view_clipping_state"] = clipping
        with fixture.installed():
            response = self.call({"refno": REFNO, "include_clipping": True, "include_image": True,
                                  "capture_width": 64, "capture_height": 64})
        self.assertFalse(response.get("isError", False), response)
        result = self.result(response)
        self.assertTrue(result["workflow_completed"])
        self.assertIs(result["observations"]["clipping_samples_unchanged"], False)
        self.assertIs(result["observations"]["observed_window_unchanged"], False)
        self.assertEqual(result["observations"]["capture"]["status"], "consistent")
        self.assertIn("observed_context_changed", json.dumps(result["unknown"]))
        self.assertFalse(result["scope"]["pixel_visibility_verified"])
        self.assertEqual(sum(part["type"] == "image" for part in response["content"]), 1)
        self.assertEqual(fixture.counts["capture_view"], 1)
        self.assertEqual(fixture.counts["view_clipping_state"], 2)
        self.assert_no_image_binary(response["structuredContent"])

    def test_partial_equal_clipping_stays_unknown_even_if_native_success_is_claimed(self):
        fixture = FrozenBridge()
        fixture.responses["view_clipping_state"]["result"]["complete"] = False
        with fixture.installed():
            response = self.call({"refno": REFNO, "include_clipping": True})
        self.assertFalse(response.get("isError", False), response)
        result = self.result(response)
        self.assertTrue(result["workflow_completed"])
        self.assertIsNone(result["observations"]["clipping_samples_unchanged"])
        self.assertIsNone(result["observations"]["observed_window_unchanged"])
        self.assertIn("clipping_observation_unverified", json.dumps(result["unknown"]))

    def test_clipping_view_identity_mismatch_is_observed_drift(self):
        fixture = FrozenBridge()
        fixture.responses["view_clipping_state"]["result"]["view_id"] = 99
        with fixture.installed():
            result = self.result(self.call({"refno": REFNO, "include_clipping": True}))
        self.assertTrue(result["workflow_completed"])
        self.assertIs(result["observations"]["clipping_samples_unchanged"], False)
        self.assertIs(result["observations"]["observed_window_unchanged"], False)

    def test_native_clipping_view_drift_stops_but_preserves_false_observation(self):
        fixture = FrozenBridge()
        value = fixture.responses["view_clipping_state"]["result"]
        value.update(success=False, complete=False, status="context_changed", view_unchanged=False,
                     read_errors={"view_unchanged": "The current E3D view changed or became unavailable during the read."})
        with fixture.installed():
            response = self.call({"refno": REFNO, "include_clipping": True})
        result = self.assert_stopped(response, fixture, "view_clipping_state")
        self.assertEqual(result["stopped_stage"], "baseline_clipping")
        self.assertIs(result["observations"]["clipping_samples_unchanged"], False)
        self.assertIs(result["observations"]["observed_window_unchanged"], False)
        self.assertNotIn("snapshot", result["evidence"])

    def test_clipping_read_failures_stop_without_retry_or_capture(self):
        for failure in (BridgeError("clipping socket closed", transport_error=True),
                        RuntimeError("native clipping getter failed"),
                        envelope({"success": False, "read_errors": {"box_data": "SDK getter failed"}}),
                        envelope({"success": False, "native_database_fault": {"has_fault": True}}),
                        envelope(None)):
            with self.subTest(failure=repr(failure)):
                fixture = FrozenBridge()
                fixture.overrides["view_clipping_state"] = lambda *_, value=failure: value
                with fixture.installed():
                    response = self.call({"refno": REFNO, "include_clipping": True, "include_image": True})
                result = self.assert_stopped(response, fixture, "view_clipping_state")
                self.assertEqual(result["stopped_stage"], "baseline_clipping")
                self.assertEqual(fixture.counts["view_clipping_state"], 1)
                self.assertNotIn("capture_view", fixture.counts)
                self.assertIsNone(result["observations"]["clipping_samples_unchanged"])
                self.assertIsNone(result["observations"]["observed_window_unchanged"])
                self.assertIn("baseline_clipping", result["evidence"])

    def test_final_clipping_failure_keeps_png_and_initial_clipping_evidence(self):
        fixture = FrozenBridge()
        def clipping(current):
            return (current.responses["view_clipping_state"] if current.counts["view_clipping_state"] == 1
                    else BridgeError("final clipping transport failed", transport_error=True))
        fixture.overrides["view_clipping_state"] = clipping
        with fixture.installed():
            response = self.call({"refno": REFNO, "include_clipping": True, "include_image": True,
                                  "capture_width": 64, "capture_height": 64})
        result = self.assert_stopped(response, fixture, "view_clipping_state")
        self.assertEqual(result["stopped_stage"], "final_clipping")
        self.assertEqual(result["evidence"]["baseline_clipping"], fixture.responses["view_clipping_state"])
        self.assertIn("exception", result["evidence"]["final_clipping"])
        self.assertEqual(sum(part["type"] == "image" for part in response["content"]), 1)
        self.assertEqual(fixture.counts["capture_view"], 1)
        self.assertEqual(fixture.counts["view_clipping_state"], 2)
        self.assertIsNone(result["observations"]["clipping_samples_unchanged"])
        self.assert_no_image_binary(response["structuredContent"])

    def test_clipping_boolean_is_validated_before_direct_bridge_access(self):
        for value in (None, "true", 0, 1, [], {}):
            with self.subTest(value=value):
                fixture = FrozenBridge()
                with fixture.installed(), self.assertRaisesRegex(ValueError, "include_clipping must be a boolean"):
                    diagnose_element(BridgeClient(Settings(mode="bridge", version="E3D21")), REFNO,
                                     include_clipping=value)
                self.assertEqual(fixture.events, [])

    def test_each_native_stage_has_fresh_status_and_project_gates(self):
        fixture = FrozenBridge()
        with fixture.installed():
            response = self.call({"refno": REFNO})
        self.assertFalse(response.get("isError", False), response)
        for index, (name, _, _) in enumerate(fixture.events):
            if name not in {"health", "project"}:
                self.assertGreaterEqual(index, 2)
                self.assertEqual([row[0] for row in fixture.events[index - 2:index]], ["health", "project"])

    def test_mock_and_unsupported_version_are_unavailable_without_any_bridge_call(self):
        for settings in (Settings(mode="mock", version="E3D21"), Settings(mode="bridge", version="E3D31")):
            with self.subTest(mode=settings.mode, version=settings.version):
                fixture = FrozenBridge()
                with fixture.installed():
                    response = self.call({"refno": REFNO, "include_image": True}, create_server(settings))
                self.assertTrue(response.get("isError"), response)
                result = self.result(response)
                self.assertFalse(result["available"])
                self.assertFalse(result["success"])
                self.assertEqual(fixture.events, [])
                self.assertFalse(any(part["type"] == "image" for part in response["content"]))

    def test_primitive_negative_child_evidence_is_preserved_without_retargeting(self):
        fixture = FrozenBridge()
        with fixture.installed():
            result = self.result(self.call({"refno": REFNO}))
        self.assertIn("primitive_geometry", fixture.native_events)
        self.assertNotIn("panel_geometry", fixture.native_events)
        self.assertIn(NEGATIVE_REFNO, json.dumps(result["evidence"]))
        self.assertIn('"DIAM": 60.0', json.dumps(result["evidence"]))
        for name, args, _ in fixture.events:
            if name in ELEMENT_READS:
                self.assertEqual(args[0], REFNO)

    def test_confirmed_panel_uses_panel_geometry_only_and_retains_local_coordinates(self):
        fixture = FrozenBridge("PANE")
        with fixture.installed():
            response = self.call({"refno": PANE_REFNO})
        self.assertFalse(response.get("isError", False), response)
        result = self.result(response)
        self.assertEqual(result["target"]["type"], "PANE")
        self.assertIn("panel_geometry", fixture.native_events)
        self.assertNotIn("primitive_geometry", fixture.native_events)
        self.assertIn("PAVE POS qualified WRT PANE", json.dumps(result["evidence"]))

    def test_invalid_inputs_are_rejected_before_any_bridge_read(self):
        invalid = [{}, {"refno": None}, {"refno": True}, {"refno": 1}, {"refno": ""},
                   {"refno": "/"}, {"refno": "CE"}, {"refno": "PUMP"}, {"refno": "/A B"},
                   {"refno": "/x\ud800"},
                   {"refno": "=0/1"}, {"refno": "=1/-1"}, {"refno": "=2147483648/1"}]
        base = {"refno": REFNO}
        invalid += [{**base, "include_image": value} for value in (None, "true", 0, 1, [], {})]
        invalid += [{**base, "include_clipping": value} for value in (None, "true", 0, 1, [], {})]
        for field in ("capture_width", "capture_height"):
            invalid += [{**base, field: value} for value in (None, True, False, "1280", 64.0, 63, 2049)]
        invalid += [{**base, name: value} for name, value in (
            ("dry_run", True), ("capture_after", True), ("expected_type", "CYLI"), ("symptom", "test"),
            ("command", "DESIGN"), ("restore", True), ("limit", 100))]
        fixture = FrozenBridge()
        with fixture.installed():
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    self.assertTrue(self.call(arguments).get("isError"))
        self.assertEqual(fixture.events, [])

    def test_include_image_returns_png_once_and_metadata_never_contains_base64(self):
        fixture = FrozenBridge()
        with fixture.installed():
            response = self.call({"refno": REFNO, "include_image": True, "capture_width": 64, "capture_height": 96})
        self.assertFalse(response.get("isError", False), response)
        images = [part for part in response["content"] if part["type"] == "image"]
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["mimeType"], "image/png")
        raw = base64.b64decode(images[0]["data"], validate=True)
        self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual((int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")), (64, 96))
        self.assertEqual(fixture.counts["capture_view"], 1)
        metadata = json.dumps(response["structuredContent"])
        self.assert_no_image_binary(response["structuredContent"])
        self.assertNotIn(images[0]["data"], metadata)
        self.assertEqual(json.loads(response["content"][0]["text"]), response["structuredContent"])

    def test_fault_after_capture_retains_png_and_all_earlier_evidence(self):
        fixture = FrozenBridge()
        def health(current):
            value = healthy_status()
            if current.counts.get("capture_view", 0):
                value["state"].update(database_ready=False, recovery_required=True)
                value["state"]["native_database_fault"]["has_fault"] = True
            return value
        fixture.overrides["health"] = health
        with fixture.installed():
            response = self.call({"refno": REFNO, "include_image": True, "capture_width": 64, "capture_height": 64})
        result = self.assert_stopped(response, fixture, "capture_view")
        self.assertEqual(sum(part["type"] == "image" for part in response["content"]), 1)
        self.assertIn(NEGATIVE_REFNO, json.dumps(result["evidence"]))
        self.assertIn("native_capture_observation", json.dumps(result["evidence"]))
        self.assertEqual(fixture.counts["capture_view"], 1)
        self.assert_no_image_binary(response["structuredContent"])

    def test_native_fault_reported_with_valid_capture_still_retains_the_png(self):
        fixture = FrozenBridge()
        def capture(current, width, height):
            value = capture_payload(width, height)
            value["result"].update(success=False, native_database_fault={"has_fault": True},
                                   error="Native fault observed after bitmap")
            return value
        fixture.overrides["capture_view"] = capture
        with fixture.installed():
            response = self.call({"refno": REFNO, "include_image": True, "capture_width": 64, "capture_height": 64})
        result = self.assert_stopped(response, fixture, "capture_view")
        self.assertEqual(sum(part["type"] == "image" for part in response["content"]), 1)
        self.assertIn("Native fault observed after bitmap", json.dumps(result["evidence"]))
        self.assert_no_image_binary(response["structuredContent"])

    def test_capture_native_fault_without_png_is_not_hidden_by_invalid_image(self):
        fixture = FrozenBridge()
        fixture.overrides["capture_view"] = lambda *args: envelope({
            "success": False, "native_database_fault": {"has_fault": True},
            "error": "Native capture fault before bitmap returned"})
        with fixture.installed():
            response = self.call({"refno": REFNO, "include_image": True, "capture_width": 64, "capture_height": 64})
        result = self.assert_stopped(response, fixture, "capture_view")
        codes = {item["code"] for item in result["errors"]}
        self.assertIn("native_fault", codes)
        self.assertNotIn("invalid_image", codes)
        self.assertFalse(any(part["type"] == "image" for part in response["content"]))

    def test_invalid_png_stops_without_attaching_or_replaying_the_image(self):
        fixture = FrozenBridge()
        def capture(current, width, height):
            value = capture_payload(width, height)
            value["result"]["image_base64"] = base64.b64encode(b"not a PNG").decode("ascii")
            return value
        fixture.overrides["capture_view"] = capture
        with fixture.installed():
            response = self.call({"refno": REFNO, "include_image": True, "capture_width": 64, "capture_height": 64})
        result = self.assert_stopped(response, fixture, "capture_view")
        self.assertEqual(fixture.counts["capture_view"], 1)
        self.assertFalse(any(part["type"] == "image" for part in response["content"]))
        self.assertIn("invalid_image", json.dumps(result["errors"]))

    def test_initial_readiness_must_be_positively_established(self):
        def missing(value): value["state"].pop("database_ready")
        variants = {
            "missing": missing,
            "busy": lambda value: value["state"].update(database_ready=False, busy=True),
            "disconnected": lambda value: value.update(connected=False),
            "fault": lambda value: value["state"]["native_database_fault"].update(has_fault=True),
            "dialog_unknown": lambda value: value["state"]["native_dialogs"].update(complete=False),
            "pml_alert": lambda value: value["state"]["pml_alerts"].update(alert_present=True),
        }
        for label, mutate in variants.items():
            with self.subTest(label=label):
                fixture = FrozenBridge()
                mutate(fixture.responses["health"])
                with fixture.installed():
                    response = self.call({"refno": REFNO})
                self.assert_stopped(response, fixture)
                self.assertNotIn("project", fixture.counts)

    def test_midstream_popup_session_and_mdb_changes_stop_before_next_native_read(self):
        for kind in ("popup", "session", "mdb"):
            with self.subTest(kind=kind):
                fixture = FrozenBridge()
                def health(current):
                    value = healthy_status()
                    if current.native_events:
                        if kind == "popup":
                            value["state"]["database_ready"] = False
                            value["state"]["native_dialogs"]["standard_dialog_present"] = True
                        if kind == "session": value["session_id"] = "different-session"
                    return value
                def project(current):
                    value = copy.deepcopy(current.responses["project"])
                    if kind == "mdb" and current.native_events: value["project"]["mdb"] = "OTHER"
                    return value
                fixture.overrides.update(health=health, project=project)
                with fixture.installed():
                    response = self.call({"refno": REFNO})
                self.assert_stopped(response, fixture, fixture.native_events[0])
                self.assertEqual(len(fixture.native_events), 1)

    def test_same_reference_with_different_type_is_never_combined(self):
        for method in ("element_schema", "geometry_bounds", "primitive_geometry", "panel_geometry", "display_state"):
            with self.subTest(method=method):
                fixture = FrozenBridge("PANE" if method == "panel_geometry" else "CYLI")
                result = fixture.responses[method]["result"]
                (result if method == "geometry_bounds" else result["element"])["type"] = "BOX"
                with fixture.installed():
                    response = self.call({"refno": fixture.refno, "include_image": True})
                stopped = self.assert_stopped(response, fixture, method)
                self.assertIn("type", json.dumps(stopped["errors"]).lower())
                self.assertNotIn("capture_view", fixture.native_events)

    def test_snapshot_reference_mismatch_stops_before_schema_or_any_shape_reader(self):
        fixture = FrozenBridge()
        fixture.responses["element"]["element"]["refno"] = "=2000000001/9999"
        with fixture.installed():
            response = self.call({"refno": REFNO})
        self.assert_stopped(response, fixture, "element")
        self.assertNotIn("element_schema", fixture.native_events)

    def test_named_target_mismatch_never_falls_back_to_the_resolved_object(self):
        fixture = FrozenBridge()
        with fixture.installed():
            response = self.call({"refno": "/A-DIFFERENT-PUMP"})
        result = self.assert_stopped(response, fixture, "element")
        self.assertEqual(result["requested_refno"], "/A-DIFFERENT-PUMP")
        self.assertIn("target_identity_mismatch", json.dumps(result["errors"]))

    def test_bounded_snapshot_unsupported_schema_and_bounds_frame_remain_unknown(self):
        fixture = FrozenBridge()
        fixture.responses["element"]["element"]["snapshot_info"].update(complete=False, truncated=True,
            truncation_reasons=["depth"], omitted_descendants=True)
        fixture.responses["element_schema"]["result"]["attributes"][1].update(supported=False)
        with fixture.installed():
            response = self.call({"refno": REFNO})
        self.assertFalse(response.get("isError", False), response)
        result = self.result(response)
        self.assertFalse(result["complete"])
        text = json.dumps(result["unknown"]).lower()
        for code in ("snapshot_incomplete", "schema_attributes_unsupported", "bounds_coordinate_frame_unverified"):
            self.assertIn(code, text)
        self.assertEqual(result["target"]["refno"], REFNO)

    def test_unsupported_shape_does_not_substitute_a_panel_owner_or_primitive(self):
        fixture = FrozenBridge("PAVE")
        with fixture.installed():
            response = self.call({"refno": REFNO})
        self.assertFalse(response.get("isError", False), response)
        result = self.result(response)
        self.assertEqual(result["target"]["type"], "PAVE")
        self.assertNotIn("panel_geometry", fixture.native_events)
        self.assertNotIn("primitive_geometry", fixture.native_events)
        self.assertIn("unsupported", json.dumps(result["unknown"]).lower())

    def test_absent_display_entries_never_prove_pixel_visibility_or_invisibility(self):
        fixture = FrozenBridge()
        with fixture.installed():
            result = self.result(self.call({"refno": REFNO}))
        text = json.dumps(result)
        self.assertIn('"sdk_exists": false', text)
        self.assertNotIn('"pixel_visibility_verified": true', text)
        self.assertNotIn('"pixel_visible": true', text)
        self.assertNotIn('"pixel_visible": false', text)
        self.assertIs(result["scope"]["pixel_visibility_verified"], False)

    def test_camera_drift_is_retained_and_never_claimed_unchanged(self):
        fixture = FrozenBridge()
        def view(current):
            result = copy.deepcopy(current.responses["view_state"])
            if current.counts["view_state"] > 1: result["result"]["centre"] = [100.0, 24.0, 36.0]
            return result
        fixture.overrides["view_state"] = view
        with fixture.installed():
            response = self.call({"refno": REFNO})
        result = self.result(response)
        self.assertTrue(result["unknown"])
        self.assertFalse(result["complete"])
        self.assertIs(result["observations"]["camera_and_top_level_drawlist_samples_unchanged"], False)
        self.assertIs(result["observations"]["observed_window_unchanged"], False)

    def test_partial_camera_observation_remains_unknown_instead_of_unchanged(self):
        fixture = FrozenBridge()
        fixture.responses["view_state"]["result"]["partial"] = True
        with fixture.installed():
            response = self.call({"refno": REFNO})
        result = self.result(response)
        self.assertIsNot(result["observations"]["camera_and_top_level_drawlist_samples_unchanged"], True)
        self.assertIsNot(result["observations"]["observed_window_unchanged"], True)
        self.assertIn("view_observation_unverified", json.dumps(result["unknown"]))

    def test_capture_changed_or_unknown_never_verifies_an_unchanged_window(self):
        for status in ("changed", "unverified"):
            with self.subTest(status=status):
                fixture = FrozenBridge()
                def capture(current, width, height):
                    value = capture_payload(width, height)
                    value["result"]["observation"].update(status=status, consistent=False if status == "changed" else None,
                        complete=status == "changed", changed_fields=["centre"] if status == "changed" else [])
                    return value
                fixture.overrides["capture_view"] = capture
                with fixture.installed():
                    response = self.call({"refno": REFNO, "include_image": True, "capture_width": 64, "capture_height": 64})
                result = self.result(response)
                self.assertFalse(result["complete"])
                self.assertEqual(result["observations"]["capture"]["status"], status)
                self.assertIsNot(result["observations"]["observed_window_unchanged"], True)
                self.assertEqual(sum(part["type"] == "image" for part in response["content"]), 1)
                self.assertEqual(fixture.counts["capture_view"], 1)

    def completed_capture_with_drift(self, mutate):
        fixture = FrozenBridge()
        def capture(current, width, height):
            value = capture_payload(width, height)
            mutate(value["result"])
            return value
        fixture.overrides["capture_view"] = capture
        with fixture.installed():
            response = self.call({"refno": REFNO, "include_image": True, "capture_width": 64, "capture_height": 64})
        self.assertFalse(response.get("isError", False), response)
        result = self.result(response)
        self.assertTrue(result["success"])
        self.assertTrue(result["workflow_completed"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(sum(part["type"] == "image" for part in response["content"]), 1)
        self.assertIs(result["observations"]["observed_window_unchanged"], False)
        self.assertIn("observed_context_changed", json.dumps(result["unknown"]))
        self.assertEqual(fixture.native_events, ["view_state", "graphical_selection", "element", "element_schema",
            "geometry_bounds", "primitive_geometry", "display_state", "view_state", "capture_view",
            "view_state", "graphical_selection"])
        self.assertEqual((fixture.counts["health"], fixture.counts["project"]), (13, 13))
        return result

    def test_capture_internal_ce_change_is_detected_even_when_outer_ce_returns_to_baseline(self):
        def mutate(result):
            # The capture consistency implementation does not compare CE; its
            # consistent=true can coexist with this raw internal CE observation.
            result["native_capture_observation"]["before_bitmap"]["current_element_refno"] = "=2000000001/9999"
            result["observation"]["native"]["before_bitmap"]["current_element_refno"] = "=2000000001/9999"
        result = self.completed_capture_with_drift(mutate)
        self.assertIs(result["observations"]["current_element_unchanged"], False)
        self.assertIs(result["observations"]["camera_and_top_level_drawlist_samples_unchanged"], True)
        self.assertTrue(result["evidence"]["capture"]["result"]["observation"]["consistent"])
        self.assertEqual(result["evidence"]["baseline_view"]["result"]["current_element_refno"], REFNO)
        self.assertEqual(result["evidence"]["final_view"]["result"]["current_element_refno"], REFNO)

    def test_capture_locally_consistent_camera_must_be_compared_with_outer_view_samples(self):
        def mutate(result):
            inside_centre = [900.0, 24.0, 36.0]
            for key in ("before_refresh", "before_bitmap", "after_bitmap"):
                result["native_capture_observation"][key]["centre"] = inside_centre.copy()
                result["observation"]["native"][key]["centre"] = inside_centre.copy()
            for sample in result["observation"]["before"]["result"]["states"]:
                sample["result"]["centre"] = inside_centre.copy()
            result["observation"]["after"]["result"]["centre"] = inside_centre.copy()
        result = self.completed_capture_with_drift(mutate)
        self.assertIs(result["observations"]["camera_and_top_level_drawlist_samples_unchanged"], False)
        self.assertTrue(result["evidence"]["capture"]["result"]["observation"]["consistent"])
        self.assertEqual(result["evidence"]["baseline_view"]["result"]["centre"], [12.0, 24.0, 36.0])
        self.assertEqual(result["evidence"]["final_view"]["result"]["centre"], [12.0, 24.0, 36.0])

    def test_capture_internal_session_change_is_detected_even_when_outer_session_matches(self):
        def mutate(result):
            result["observation"]["before"]["result"]["context_before"]["session_id"] = "temporary-capture-session"
        result = self.completed_capture_with_drift(mutate)
        self.assertIs(result["observations"]["session_build_version_unchanged"], False)
        self.assertIs(result["observations"]["current_element_unchanged"], True)
        self.assertTrue(result["evidence"]["capture"]["result"]["observation"]["consistent"])
        self.assertEqual(result["evidence"]["initial_status"]["session_id"], "frozen-session")
        self.assertEqual(result["evidence"]["final_status"]["session_id"], "frozen-session")

    def test_transport_error_stops_once_retains_diagnostics_and_never_captures(self):
        fixture = FrozenBridge()
        fixture.overrides["geometry_bounds"] = lambda *_: BridgeError("socket closed", transport_error=True,
            diagnostics={"state": {"database_ready": False, "recovery_required": True}})
        with fixture.installed():
            response = self.call({"refno": REFNO, "include_image": True})
        result = self.assert_stopped(response, fixture, "geometry_bounds")
        self.assertEqual(fixture.counts["geometry_bounds"], 1)
        self.assertNotIn("capture_view", fixture.native_events)
        self.assertIn("socket closed", json.dumps(result["errors"]))
        self.assertIn("recovery_required", json.dumps(result))
        self.assertIn("snapshot", json.dumps(result["evidence"]))

    def test_malformed_native_shapes_keep_the_failure_envelope_and_raw_evidence(self):
        scenarios = ("baseline_view", "final_selection", "project_object", "project_field", "capture_none", "capture_list")
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                fixture = FrozenBridge()
                if scenario == "baseline_view":
                    fixture.responses["view_state"] = envelope(None)
                    expected_last = "view_state"
                elif scenario == "final_selection":
                    def selection(current, *args):
                        return envelope([]) if current.counts["graphical_selection"] > 1 else current.responses["graphical_selection"]
                    fixture.overrides["graphical_selection"] = selection
                    expected_last = "graphical_selection"
                elif scenario == "project_object":
                    fixture.responses["project"]["project"] = []
                    expected_last = None
                elif scenario == "project_field":
                    fixture.responses["project"]["project"]["mdb"] = []
                    expected_last = None
                else:
                    malformed_capture = [] if scenario == "capture_list" else None
                    fixture.overrides["capture_view"] = lambda *args: envelope(malformed_capture)
                    expected_last = "capture_view"
                with fixture.installed():
                    response = self.call({"refno": REFNO, "include_image": True,
                                          "capture_width": 64, "capture_height": 64})
                result = self.assert_stopped(response, fixture, expected_last)
                self.assertTrue(result["evidence"]["initial_status"])
                self.assertTrue(result["evidence"]["gates"])
                self.assertEqual(any(part["type"] == "image" for part in response["content"]),
                                 scenario == "final_selection")
                if scenario == "baseline_view":
                    self.assertIsNone(result["evidence"]["baseline_view"]["result"])
                elif scenario == "final_selection":
                    self.assertEqual(result["evidence"]["final_selection"]["result"], [])
                    self.assertIn(NEGATIVE_REFNO, json.dumps(result["evidence"]))
                elif scenario.startswith("capture_"):
                    self.assertEqual(result["evidence"]["capture"]["result"], malformed_capture)
                    self.assertIn(NEGATIVE_REFNO, json.dumps(result["evidence"]))
                else:
                    self.assertEqual(result["evidence"]["gates"][0]["project"], fixture.responses["project"])


class ClippingObservationTests(unittest.TestCase):
    """Exercise the same raw-evidence validator used by diagnosis compaction."""

    def observe(self, baseline=None, final=None, baseline_view=None, final_view=None, *, complete=True):
        return clipping_observation_result(
            envelope(clipping_state()) if baseline is None else baseline,
            envelope(clipping_state()) if final is None else final,
            envelope(camera()) if baseline_view is None else baseline_view,
            envelope(camera()) if final_view is None else final_view, complete=complete)

    def test_complete_equal_native_samples_are_valid_without_inventing_geometric_units(self):
        first, last = envelope(clipping_state()), envelope(clipping_state())
        before = copy.deepcopy((first, last))
        result = self.observe(first, last)
        self.assertIs(result["unchanged"], True)
        self.assertTrue(result["complete"])
        self.assertEqual(result["changed_fields"], [])
        self.assertEqual(result["read_errors"], {})
        self.assertEqual((first, last), before)
        self.assertEqual(result["scope"]["coordinate_frame"], "SDK native (unverified)")
        self.assertEqual(result["scope"]["units"], "SDK native (unverified)")
        self.assertFalse(result["scope"]["pixel_visibility_verified"])
        self.assertFalse(result["scope"]["target_clipped_verified"])

    def test_all_valid_sample_fields_can_independently_report_changed_values(self):
        cases = {
            "clipping_enabled": lambda result: result.update(clipping_enabled=True),
            "capping": lambda result: result.update(capping=True),
            "cap_colour": lambda result: result.update(cap_colour=-1),
            "box_data.lengths": lambda result: result["box_data"].update(lengths=[1.0, 0.0, 0.0]),
            "box_data.position": lambda result: result["box_data"].update(position=[0.0, 1.0, 0.0]),
            "box_data.orientation": lambda result: result["box_data"].update(orientation=[0.0, 0.0, 1.0]),
            "box_faces": lambda result: result["box_faces"][0].update(defined=True, active=True),
        }
        for field, mutate in cases.items():
            with self.subTest(field=field):
                final = envelope(clipping_state())
                mutate(final["result"])
                result = self.observe(final=final)
                self.assertIs(result["unchanged"], False)
                self.assertTrue(result["complete"])
                self.assertIn(field, result["changed_fields"])

    def test_int32_colours_and_zero_view_identity_are_accepted(self):
        for colour in (-2147483648, 2147483647):
            with self.subTest(colour=colour):
                clip, view = envelope(clipping_state(0)), envelope(camera())
                clip["result"]["cap_colour"] = colour
                view["result"]["view_id"] = 0
                result = self.observe(clip, copy.deepcopy(clip), view, copy.deepcopy(view))
                self.assertIs(result["unchanged"], True)
                self.assertTrue(result["complete"])

    def test_native_face_order_does_not_change_the_observed_face_flags(self):
        final = envelope(clipping_state())
        final["result"]["box_faces"].reverse()
        result = self.observe(final=final)
        self.assertIs(result["unchanged"], True)
        self.assertTrue(result["complete"])

    def test_unknown_clipping_fields_never_establish_sampled_equality(self):
        for location in ("envelope", "result", "box_data", "box_face"):
            for after in ("same", "changed", "missing"):
                with self.subTest(location=location, after=after):
                    first, last = envelope(clipping_state()), envelope(clipping_state())
                    def destination(value):
                        if location == "envelope": return value
                        if location == "result": return value["result"]
                        if location == "box_data": return value["result"]["box_data"]
                        return value["result"]["box_faces"][0]
                    destination(first)["future_field"] = {"offset": 10.0}
                    if after != "missing":
                        destination(last)["future_field"] = {"offset": 10.0 if after == "same" else 20.0}
                    result = self.observe(first, last)
                    self.assertIsNone(result["unchanged"])
                    self.assertFalse(result["complete"])
                    self.assertTrue(any("future_field" in path for path in result["read_errors"]))

    def test_known_fault_flags_must_be_strict_false_at_every_depth(self):
        for flag in ("has_fault", "native_fault"):
            for value in (None, 0, 1, "false", [], {}, True):
                for location in ("envelope", "result", "nested"):
                    with self.subTest(flag=flag, value=value, location=location):
                        sample = envelope(clipping_state())
                        if location == "envelope": destination = sample
                        elif location == "result": destination = sample["result"]
                        else:
                            sample["result"]["native_database_fault"] = {"has_fault": False}
                            destination = sample["result"]["native_database_fault"]
                        destination[flag] = value
                        result = self.observe(sample, copy.deepcopy(sample))
                        self.assertIsNone(result["unchanged"])
                        self.assertFalse(result["complete"])
                        self.assertTrue(result["read_errors"])

    def test_native_diagnostic_and_envelope_metadata_are_not_unknown_clipping_geometry(self):
        sample = envelope(clipping_state())
        sample.update(session_id="frozen-session", bridge_build="frozen-build", native_fault=False)
        sample["result"].update(has_fault=False, native_fault=False,
            native_database_fault={"has_fault": False, "native_fault": False})
        result = self.observe(sample, copy.deepcopy(sample))
        self.assertIs(result["unchanged"], True)
        self.assertTrue(result["complete"])
        self.assertEqual(result["read_errors"], {})

    def test_changed_unknown_native_field_keeps_complete_workflow_stability_unknown(self):
        fixture = FrozenBridge()
        def clipping(current):
            value = copy.deepcopy(current.responses["view_clipping_state"])
            value["result"]["future_planes"] = [{"offset": float(current.counts["view_clipping_state"])}]
            return value
        fixture.overrides["view_clipping_state"] = clipping
        with fixture.installed():
            response = diagnose_element(BridgeClient(Settings(mode="bridge", version="E3D21")), REFNO,
                                        include_clipping=True)
        result = response["result"]
        self.assertTrue(result["workflow_completed"])
        self.assertIsNone(result["observations"]["clipping_samples_unchanged"])
        self.assertIsNone(result["observations"]["observed_window_unchanged"])
        self.assertIn("clipping_observation_unverified", json.dumps(result["unknown"]))
        self.assertNotEqual(result["evidence"]["baseline_clipping"]["result"]["future_planes"],
                            result["evidence"]["final_clipping"]["result"]["future_planes"])

    def test_equal_malformed_samples_never_prove_unchanged_clipping(self):
        cases = {
            "success": lambda result: result.update(success=1),
            "available": lambda result: result.update(available=False),
            "complete": lambda result: result.update(complete=False),
            "status": lambda result: result.update(status="partial"),
            "view_unchanged": lambda result: result.update(view_unchanged=None),
            "partial": lambda result: result.update(partial=True),
            "view_id_bool": lambda result: result.update(view_id=True),
            "view_id_negative": lambda result: result.update(view_id=-1),
            "clipping_bool": lambda result: result.update(clipping_enabled=0),
            "capping_bool": lambda result: result.update(capping=1),
            "colour_bool": lambda result: result.update(cap_colour=True),
            "colour_float": lambda result: result.update(cap_colour=271.0),
            "colour_large": lambda result: result.update(cap_colour=2147483648),
            "box_unavailable": lambda result: result["box_data"].update(available=False),
            "vector_length": lambda result: result["box_data"].update(lengths=[0.0, 0.0]),
            "vector_bool": lambda result: result["box_data"].update(position=[False, 0.0, 0.0]),
            "vector_nan": lambda result: result["box_data"].update(orientation=[float("nan"), 0.0, 0.0]),
            "vector_infinite": lambda result: result["box_data"].update(lengths=[float("inf"), 0.0, 0.0]),
            "vector_overflow": lambda result: result["box_data"].update(lengths=[10 ** 1000, 0.0, 0.0]),
            "missing_face": lambda result: result["box_faces"].pop(),
            "duplicate_face": lambda result: result["box_faces"][-1].update(face="W"),
            "bad_face": lambda result: result["box_faces"][0].update(face=[]),
            "face_flag": lambda result: result["box_faces"][0].update(active=1),
            "face_defined": lambda result: result["box_faces"][0].update(defined=None),
            "errors_absent": lambda result: result.pop("read_errors"),
            "errors_wrong_type": lambda result: result.update(read_errors=[]),
            "errors_present": lambda result: result.update(read_errors={"box_data": "getter failed"}),
            "error_code": lambda result: result.update(error_code="native_unavailable"),
            "nested_fault": lambda result: result.update(native_database_fault={"has_fault": True}),
            "coordinate_frame": lambda result: result.update(coordinate_frame="WORLD"),
            "units": lambda result: result.pop("units"),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                malformed = envelope(clipping_state())
                mutate(malformed["result"])
                result = self.observe(malformed, copy.deepcopy(malformed))
                self.assertIsNone(result["unchanged"])
                self.assertFalse(result["complete"])
                self.assertTrue(result["read_errors"])

    def test_invalid_envelopes_and_camera_identity_keep_comparison_unknown(self):
        invalid_envelopes = ([], {"ok": False, "version": "E3D21", "result": clipping_state()},
                             {"ok": True, "version": "E3D31", "result": clipping_state()},
                             envelope(None), envelope([]), {"exception": {"message": "transport failed"}})
        for value in invalid_envelopes:
            with self.subTest(value=value):
                result = self.observe(final=value)
                self.assertIsNone(result["unchanged"])
                self.assertFalse(result["complete"])
        for field, value in (("view_id", True), ("view_id", -1), ("view_id", None),
                             ("success", False), ("available", False), ("partial", True), ("errors", None)):
            with self.subTest(camera_field=field, value=value):
                view = envelope(camera())
                view["result"][field] = value
                result = self.observe(final_view=view)
                self.assertIsNone(result["unchanged"])
                self.assertFalse(result["complete"])

    def test_incomplete_workflow_prevents_equal_samples_from_proving_stability(self):
        for complete in (False, None, 1):
            with self.subTest(complete=complete):
                result = self.observe(complete=complete)
                self.assertIsNone(result["unchanged"])
                self.assertFalse(result["complete"])
                self.assertIn("workflow_completed", result["read_errors"])

    def test_observed_view_drift_remains_false_with_partial_sample_coverage(self):
        final = envelope(clipping_state())
        final["result"].update(success=False, complete=False, status="context_changed", view_unchanged=False,
                               read_errors={"view_unchanged": "View changed"})
        result = self.observe(final=final, complete=False)
        self.assertIs(result["unchanged"], False)
        self.assertFalse(result["complete"])
        self.assertIn("view_object", result["changed_fields"])
        self.assertTrue(result["read_errors"])
        final["result"].update(view_unchanged=True, status="partial", view_id=12)
        result = self.observe(final=final, complete=False)
        self.assertIs(result["unchanged"], False)
        self.assertIn("view_id", result["changed_fields"])


if __name__ == "__main__":
    unittest.main()

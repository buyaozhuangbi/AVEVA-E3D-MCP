from __future__ import annotations
from catalog_compatibility import MODELING_TEAM_TOOLS, E3D_IDENTITY_TOOLS

import json
import os
import sys
import unittest
from io import StringIO
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.tools import create_server


class McpServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(mode="mock")
        self.server = create_server(self.settings)

    def _call(self, name: str, arguments: dict) -> dict:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
        output = StringIO()
        with patch("sys.stdout", output):
            self.server.serve([request + "\n"])
        return json.loads(output.getvalue())["result"]

    def _list_tools(self) -> list[dict]:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        output = StringIO()
        with patch("sys.stdout", output):
            self.server.serve([request + "\n"])
        return json.loads(output.getvalue())["result"]["tools"]

    def test_initialize_and_tool_list(self) -> None:
        output = StringIO()
        requests = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "server-test", "version": "1.0"}}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
        ]
        with patch("sys.stdout", output):
            self.server.serve([line + "\n" for line in requests])
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(responses[0]["result"]["serverInfo"]["name"], "aveva-e3d-mcp")
        self.assertIn("resources", responses[0]["result"]["capabilities"])
        self.assertIn("prompts", responses[0]["result"]["capabilities"])
        self.assertEqual(
            {tool["name"] for tool in responses[1]["result"]["tools"]},
            {
                "e3d_status", "e3d_context_snapshot", "e3d_project_info", "e3d_database_state", "e3d_database_identity_snapshot", "e3d_database_preflight", "e3d_piping_branch_state", "e3d_equipment_manifest", "e3d_pipe_member_manifest", "e3d_equipment_model_plan", "e3d_piping_route_plan", "e3d_piping_batch_route_plan", "e3d_paragon_catalogue_manifest", "e3d_module_context", "e3d_paragon_catalogue_schema", "e3d_piping_connectivity_snapshot", "e3d_equipment_child_schema", "e3d_piping_specification_read", "e3d_team_database_identity_manifest", "e3d_paragon_generation_plan", "e3d_paragon_component_template", "e3d_paragon_reference_trace", "e3d_paragon_component_copy", "e3d_paragon_component_copy_status", "e3d_paragon_component_parameters_set", "e3d_paragon_component_parameters_status", "e3d_paragon_generate_from_standard", "e3d_paragon_component_create", "e3d_paragon_component_create_status", "e3d_spatial_map_status", "e3d_query", "e3d_read_logs", "e3d_read_messages", "e3d_read_message_window",
                "e3d_command_state", "e3d_element_snapshot", "e3d_validate_model", "e3d_delete_element",
                "e3d_cancel_command", "e3d_drawlist_snapshot", "e3d_fit_view", "e3d_restore_view", "e3d_set_drawlist_visual", "e3d_set_attribute", "e3d_set_attributes",
                "e3d_execute_pml", "e3d_execute_pml_script", "e3d_pml_script_status", "e3d_reenter_design", "e3d_execute_pml_batch", "e3d_read_attributes", "e3d_spatial_info", "e3d_transform_coordinates", "e3d_connection_points", "e3d_check_connection_pair", "e3d_trace_piping_connectivity", "e3d_list_members", "e3d_element_schema",
                "e3d_create_element", "e3d_copy_element", "e3d_set_transform", "e3d_capture_view",
                "e3d_geometry_bounds", "e3d_geometry_overlap_candidates", "e3d_geometry_relation", "e3d_geometry_relation_batch", "e3d_native_clash_check", "e3d_geometry_volume",
                "e3d_read_attributes_batch", "e3d_element_access", "e3d_measure_elements_batch", "e3d_graphical_selection", "e3d_set_graphical_selection",
                "e3d_view_state", "e3d_view_clipping_state", "e3d_observe_view", "e3d_display_state", "e3d_display_snapshot", "e3d_set_primitive_visual", "e3d_set_view", "e3d_set_camera", "e3d_remove_drawlist_member",
                "e3d_create_panel", "e3d_panel_geometry", "e3d_panel_curve_boundary", "e3d_project_panel_curve_drawing", "e3d_project_panel_curve_assembly_drawing", "e3d_project_panel_boundary_drawing", "e3d_project_panel_assembly_boundary_drawing", "e3d_panel_mesh", "e3d_panel_loop_relations", "e3d_update_panel", "e3d_validate_triangle_mesh", "e3d_measure_triangle_mesh",
                "e3d_primitive_mesh", "e3d_primitive_csg_mesh", "e3d_project_primitive_csg_drawing", "e3d_element_mesh", "e3d_compare_element_meshes", "e3d_project_element_drawing", "e3d_project_assembly_drawing", "e3d_project_material_assembly_drawing", "e3d_project_material_assembly_sheet", "e3d_project_material_assembly_tiles", "e3d_equipment_layout_sheet", "e3d_nozzle_datum_sheet", "e3d_equipment_outline_sheet", "e3d_nozzle_axis_sheet", "e3d_equipment_layout_tiles", "e3d_nozzle_outline_sheet", "e3d_compose_drawing_sheet", "e3d_compose_drawing_atlas", "e3d_section_element_drawing", "e3d_section_assembly_drawing",
                "e3d_primitive_geometry", "e3d_generated_csg", "e3d_update_primitive", "e3d_find_descendants", "e3d_diagnose_element", "e3d_diagnostic_receipt", "e3d_compare_diagnostic_receipts",
                "e3d_nozzle_orientation_drawing", "e3d_equipment_layout_drawing",
                "e3d_piping_route_create", "e3d_piping_route_verify", *MODELING_TEAM_TOOLS, *E3D_IDENTITY_TOOLS,
            },
        )
        definitions = {tool["name"]: tool for tool in responses[1]["result"]["tools"]}
        self.assertTrue(definitions["e3d_spatial_info"]["annotations"]["readOnlyHint"])
        self.assertFalse(definitions["e3d_fit_view"]["annotations"]["readOnlyHint"])
        self.assertTrue(definitions["e3d_delete_element"]["annotations"]["destructiveHint"])
        self.assertIn("Command execution is disabled", responses[0]["result"]["instructions"])

    def test_e3d_resources_are_discoverable_and_read_fresh_mock_context(self) -> None:
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "resources/list", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": "e3d://default/status"}},
            {"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": "e3d://default/project"}},
        ]
        output = StringIO()
        with patch("sys.stdout", output):
            self.server.serve([json.dumps(item) + "\n" for item in requests])
        listed, status, project = [json.loads(line)["result"] for line in output.getvalue().splitlines()]
        resources = {item["uri"]: item for item in listed["resources"]}
        self.assertEqual(set(resources), {"e3d://default/status", "e3d://default/project"})
        self.assertEqual(resources["e3d://default/status"]["mimeType"], "application/json")
        status_value = json.loads(status["contents"][0]["text"])
        self.assertTrue(status_value["connected"])
        self.assertEqual(status_value["version"], "default")
        self.assertEqual(json.loads(project["contents"][0]["text"])["project"]["name"], "DEMO")

    def test_e3d_status_resource_reads_bridge_on_each_request(self) -> None:
        settings = Settings(mode="bridge", version="E3D21", bridge_url="http://127.0.0.1:8766")
        server = create_server(settings)
        with patch.object(BridgeClient, "health", side_effect=[
                {"ok": True, "session_id": "session-one"}, {"ok": True, "session_id": "session-two"}]) as health:
            contents = []
            for request_id in (1, 2):
                response = server._dispatch({"jsonrpc": "2.0", "id": request_id, "method": "resources/read",
                    "params": {"uri": "e3d://E3D21/status"}})
                contents.append(json.loads(response["result"]["contents"][0]["text"]))
        self.assertEqual([item["session_id"] for item in contents], ["session-one", "session-two"])
        self.assertEqual(health.call_count, 2)

    def test_resource_uri_encodes_configured_version_label(self) -> None:
        settings = Settings(mode="mock", version="E3D 2.1/dev")
        server = create_server(settings)
        response = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "resources/list", "params": {}})
        uris = {item["uri"] for item in response["result"]["resources"]}
        self.assertEqual(uris, {"e3d://E3D%202.1%2Fdev/status", "e3d://E3D%202.1%2Fdev/project"})

    def test_e3d_diagnosis_prompt_is_discoverable_and_contains_only_read_only_steps(self) -> None:
        listed = self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "prompts/list", "params": {}})
        prompts = {prompt["name"]: prompt for prompt in listed["result"]["prompts"]}
        self.assertEqual(set(prompts), {"e3d_inspect_context", "e3d_diagnose_element"})
        self.assertEqual([arg["name"] for arg in prompts["e3d_diagnose_element"]["arguments"]], ["refno", "symptom"])
        response = self.server._dispatch({"jsonrpc": "2.0", "id": 2, "method": "prompts/get", "params": {
            "name": "e3d_diagnose_element", "arguments": {"refno": "/PUMP", "symptom": "面片存在但画面为空"}}})
        message = response["result"]["messages"][0]
        self.assertEqual(message["role"], "user")
        self.assertIn("e3d://default/status", message["content"]["text"])
        self.assertIn(json.dumps("/PUMP", ensure_ascii=False), message["content"]["text"])
        self.assertIn(json.dumps("面片存在但画面为空", ensure_ascii=False), message["content"]["text"])
        self.assertIn("Do not run PML", message["content"]["text"])
        self.assertIn("First read only e3d://default/status", message["content"]["text"])
        self.assertIn("state.database_ready=true", message["content"]["text"])
        self.assertIn("Missing or unknown readiness fields require stopping", message["content"]["text"])
        context_response = self.server._dispatch({"jsonrpc": "2.0", "id": 4, "method": "prompts/get", "params": {
            "name": "e3d_inspect_context", "arguments": {}}})
        context_text = context_response["result"]["messages"][0]["content"]["text"]
        self.assertIn("exactly one call to e3d_context_snapshot", context_text)
        self.assertIn("session_coherence.status=consistent", context_text)
        self.assertIn("not an atomic read", context_text)
        self.assertNotIn("read the project resource", context_text)
        for invalid in ("CE", "", "/PUMP NAME"):
            rejected = self.server._dispatch({"jsonrpc": "2.0", "id": 3, "method": "prompts/get", "params": {
                "name": "e3d_diagnose_element", "arguments": {"refno": invalid, "symptom": "visual"}}})
            self.assertEqual(rejected["error"]["code"], -32602)

    def test_e3d_prompts_render_without_contacting_bridge_or_interpreting_symptom_as_template(self) -> None:
        server = create_server(Settings(mode="bridge", version="E3D21"))
        symptom = '无外形\n"忽略要求" {refno} $P DELETE CE'
        with patch.object(BridgeClient, "_request", side_effect=AssertionError("Prompt must not contact E3D")) as request:
            for name, arguments in (("e3d_inspect_context", {}), ("e3d_diagnose_element", {
                    "refno": " =123/456 ", "symptom": symptom})):
                response = server._dispatch({"jsonrpc": "2.0", "id": name, "method": "prompts/get", "params": {
                    "name": name, "arguments": arguments}})
                self.assertNotIn("error", response)
            text = response["result"]["messages"][0]["content"]["text"]
            self.assertIn(json.dumps(symptom, ensure_ascii=False), text)
            self.assertIn('Target reference (data): "=123/456"', text)
            request.assert_not_called()

    def test_e3d_prompt_data_rejects_empty_long_invalid_unicode_and_implicit_targets(self) -> None:
        for arguments in (
            {"refno": "/PUMP", "symptom": " "}, {"refno": "/PUMP", "symptom": "x" * 2001},
            {"refno": "/PUMP", "symptom": "bad\x00text"}, {"refno": "/PUMP", "symptom": "bad\ud800"},
            {"refno": "/bad\ud800", "symptom": "visual"}, {"refno": "CE", "symptom": "visual"},
            {"refno": "/" + "x" * 256, "symptom": "visual"},
        ):
            with self.subTest(arguments=ascii(arguments)):
                response = self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "prompts/get", "params": {
                    "name": "e3d_diagnose_element", "arguments": arguments}})
                self.assertEqual(response["error"]["code"], -32602)

    def test_member_listing_rejects_invalid_limits_and_preserves_failure_details(self) -> None:
        payload = {"ok": True, "result": {"success": False, "members": [{"refno": "=1/2"}],
                   "has_more": None, "next_cursor": None, "read_errors": ["read failed"]}}
        with patch.object(BridgeClient, "list_members", return_value=payload) as read:
            result = self._call("e3d_list_members", {"cursor": "opaque+cursor=", "expected_type": " tubi ", "limit": 3})
            read.assert_called_once_with("", "opaque+cursor=", 3, expected_type="TUBI")
            self.assertTrue(result["isError"])
            self.assertEqual(result["structuredContent"], payload)
            for args in ({"limit": True}, {"limit": 0}, {"limit": 201}, {"cursor": "a" * 2049},
                         {"cursor": 7}, {"expected_type": "TU\nBI"}):
                self.assertTrue(self._call("e3d_list_members", args)["isError"])
            self.assertEqual(read.call_count, 1)

    def test_member_listing_schema_discloses_typed_parent_aliases(self) -> None:
        definitions = {tool["name"]: tool for tool in self._list_tools()}
        tool = definitions["e3d_list_members"]
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertIn("expected_type", tool["inputSchema"]["properties"])
        self.assertEqual(tool["inputSchema"]["properties"]["expected_type"]["maxLength"], 64)
        self.assertIn("TUBI", tool["description"])

    def test_element_snapshot_schema_and_bridge_support_typed_aliases(self) -> None:
        definitions = {tool["name"]: tool for tool in self._list_tools()}
        tool = definitions["e3d_element_snapshot"]
        self.assertIn("expected_type", tool["inputSchema"]["properties"])
        self.assertIn("TUBI", tool["description"])
        with patch.object(BridgeClient, "element", return_value={"ok": True, "element": {"type": "TUBI"}}) as read:
            result = self._call("e3d_element_snapshot", {"refno": "=1/2", "expected_type": " tubi "})
        read.assert_called_once_with("=1/2", 2, 100, expected_type="TUBI")
        self.assertFalse(result.get("isError", False))
        self.assertTrue(self._call("e3d_element_snapshot", {"expected_type": "TU\nBI"})["isError"])

    def test_element_snapshot_bridge_forwards_type_and_mock_does_not_invent_alias(self) -> None:
        bridge = BridgeClient(Settings(mode="bridge", version="E3D21"))
        with patch.object(bridge, "_request", return_value={"ok": True}) as request:
            bridge.element("=1/2", 1, 20, expected_type="TUBI")
        request.assert_called_once_with("GET", "/api/v1/element", query={
            "refno": "=1/2", "expected_type": "TUBI", "depth": "1", "limit": "20",
        }, version=None)
        mock = BridgeClient(Settings(mode="mock", version="E3D21")).element(
            "=1/2", 1, 20, expected_type="TUBI")
        self.assertNotIn("type", mock["element"])
        self.assertFalse(mock["element"]["snapshot_info"]["complete"])

    def test_member_listing_bridge_forwards_type_selector_and_mock_does_not_fabricate_alias(self) -> None:
        bridge = BridgeClient(Settings(mode="bridge", version="E3D21"))
        with patch.object(bridge, "_request", return_value={"ok": True}) as request:
            bridge.list_members("=1/2", "", 7, expected_type="TUBI")
        request.assert_called_once_with("GET", "/api/v1/element/members", query={
            "refno": "=1/2", "expected_type": "TUBI", "cursor": "", "limit": "7",
        }, version=None)
        mock = BridgeClient(Settings(mode="mock", version="E3D21")).list_members(
            "=1/2", "", 7, expected_type="TUBI")
        self.assertFalse(mock["result"]["success"])
        self.assertNotIn("type", mock["result"]["parent"])
        self.assertIsNone(mock["result"]["has_more"])

    def test_spatial_frame_validation_and_no_write_permission_required(self) -> None:
        with patch.object(BridgeClient, "spatial_info", return_value={"ok": True}) as read:
            for supplied, normalized in ((" world ", "WORLD"), ("owner", "OWNER"), ("/PUMP", "/PUMP"), ("=1/2", "=1/2")):
                result = self._call("e3d_spatial_info", {"refno": " =3/4 ", "wrt": supplied})
                self.assertFalse(result.get("isError", False))
                read.assert_called_with("=3/4", normalized)
            for invalid in ("", "POS WRT WORLD", "WORLD; DELETE CE", 1, None):
                result = self._call("e3d_spatial_info", {"wrt": invalid})
                self.assertTrue(result["isError"])
            self.assertEqual(read.call_count, 4)
        payload = self._call("e3d_spatial_info", {})["structuredContent"]["result"]
        self.assertEqual(payload["frame"]["requested"], "WORLD")
        self.assertEqual(payload["position"]["unit"], "mm")

    def test_schema_distinguishes_sdk_support_from_mcp_write_permission(self) -> None:
        result = self._call("e3d_element_schema", {"attributes": ["desc", " DESC ", "CODEXNOATTRIBUTE"]})
        self.assertFalse(result.get("isError", False))
        schema = result["structuredContent"]["result"]
        self.assertFalse(schema["mcp_commands_enabled"])
        self.assertTrue(schema["attributes"][0]["sdk_settable_now"])
        self.assertFalse(schema["attributes"][0]["direct_edit_enabled"])
        self.assertFalse(schema["attributes"][1]["known"])
        self.assertFalse(schema["attributes"][1]["direct_edit_enabled"])
        self.assertEqual(schema["attribute_page"]["total_count"], 2)

    def test_schema_pagination_validation_and_partial_metadata(self) -> None:
        with patch.object(BridgeClient, "element_schema", return_value={"ok": True}) as read:
            self._call("e3d_element_schema", {"refno": "/PUMP", "offset": 5, "limit": 10})
            read.assert_called_once_with("/PUMP", [], 5, 10)
            for args in ({"offset": -1}, {"limit": 0}, {"limit": True}, {"limit": 201},
                         {"attributes": ["POS; DELETE CE"]}, {"attributes": ["POS"] * 51}, {"attributes": None}):
                self.assertTrue(self._call("e3d_element_schema", args)["isError"])
            self.assertEqual(read.call_count, 1)
        payload = {"ok": True, "result": {"success": False,
            "attributes": [{"name": "DESC", "supported": True, "sdk_settable_now": None,
                            "read_error": "SDK error", "direct_edit_supported": True}],
            "read_errors": {"DESC": "SDK error"}}}
        with patch.object(BridgeClient, "element_schema", return_value=payload):
            result = self._call("e3d_element_schema", {})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["result"]["read_errors"], {"DESC": "SDK error"})
        self.assertFalse(result["structuredContent"]["result"]["attributes"][0]["direct_edit_enabled"])

    def test_spatial_partial_error_preserves_available_coordinates(self) -> None:
        payload = {"ok": True, "result": {"success": False,
            "position": {"available": True, "x": 125.0, "y": 50.0, "z": 20.0},
            "orientation": {"available": False, "reason": "read_failed"}, "errors": {"orientation": "SDK error"}}}
        with patch.object(BridgeClient, "spatial_info", return_value=payload):
            result = self._call("e3d_spatial_info", {})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"], payload)
        payload["result"]["success"] = True
        payload["result"]["errors"] = {}
        payload["result"]["orientation"]["reason"] = "attribute_not_supported"
        with patch.object(BridgeClient, "spatial_info", return_value=payload):
            result = self._call("e3d_spatial_info", {})
        self.assertFalse(result.get("isError", False))

    def test_query_is_available_in_mock_mode(self) -> None:
        result = self._call("e3d_query", {"expression": "PIPE"})
        self.assertFalse(result.get("isError", False))
        payload = result["structuredContent"]
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["elements"][0]["type"], "PIPE")

    def test_query_requires_a_nonempty_expression_before_dispatch(self) -> None:
        server = create_server(Settings(mode="bridge"))
        with patch.object(BridgeClient, "query", return_value={"ok": True}) as query:
            for arguments in ({}, {"expression": "   "}):
                with self.subTest(arguments=arguments):
                    response = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "e3d_query", "arguments": arguments}})
                    self.assertTrue(response["result"]["isError"])
            query.assert_not_called()

    def test_mutating_pml_is_gated(self) -> None:
        result = self._call("e3d_execute_pml", {"command": "$P STATUS", "dry_run": False})
        self.assertTrue(result["isError"])
        self.assertIn("disabled", result["content"][0]["text"])

    def test_command_session_guard_is_forwarded_and_http_conflicts_keep_their_status(self) -> None:
        with patch.object(BridgeClient, "command", return_value={"ok": True}) as command:
            result = self._call("e3d_execute_pml", {"command": "$P CHECK", "expected_session_id": "observed-session"})
            self.assertFalse(result.get("isError", False))
            command.assert_called_once_with("$P CHECK", True, expected_session_id="observed-session")
        client = BridgeClient(Settings(mode="bridge"))
        with patch.object(client, "_request", side_effect=BridgeError("HTTP 409: session changed", http_status=409)) as request:
            with self.assertRaises(BridgeError) as caught:
                client.command("$P CHECK", False, expected_session_id="old-session")
        self.assertEqual(caught.exception.http_status, 409)
        self.assertFalse(caught.exception.transport_error)
        request.assert_called_once_with("POST", "/api/v1/command",
            {"command": "$P CHECK", "dry_run": False, "expected_session_id": "old-session"}, version=None)

    def test_misspelled_pml_session_guard_cannot_degrade_into_an_unguarded_write(self) -> None:
        server = create_server(Settings(mode="bridge", allow_commands=True))
        with patch.object(BridgeClient, "command", return_value={"ok": True,
                "result": {"success": True}}) as command:
            response = server._dispatch({"jsonrpc": "2.0", "id": 99, "method": "tools/call",
                "params": {"name": "e3d_execute_pml", "arguments": {
                    "command": "DELETE CE", "dry_run": False, "expected_session": "stale-session"}}})
        self.assertTrue(response["result"]["isError"])
        self.assertIn("Unknown e3d_execute_pml argument", response["result"]["content"][0]["text"])
        command.assert_not_called()

    def test_pml_batch_is_available_in_mock_mode(self) -> None:
        result = self._call("e3d_execute_pml_batch", {"commands": ["NEW EQUIPMENT", "END"]})
        self.assertFalse(result.get("isError", False))
        self.assertEqual(len(result["structuredContent"]["results"]), 2)

    def test_reenter_design_is_available_in_mock_mode(self) -> None:
        server = create_server(Settings(mode="mock", allow_commands=True))
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "e3d_reenter_design", "arguments": {}}})
        output = StringIO()
        with patch("sys.stdout", output):
            server.serve([request + "\n"])
        result = json.loads(output.getvalue())["result"]
        self.assertFalse(result.get("isError", False))
        self.assertTrue(result["structuredContent"]["reconnected"])
        self.assertEqual(result["structuredContent"]["command"], "DESIGN")

    def test_reenter_design_rejects_unknown_arguments_before_restart(self) -> None:
        server = create_server(Settings(mode="bridge", allow_commands=True))
        for arguments in ({"dry_run": True}, {"unexpected": "value"}):
            with self.subTest(arguments=arguments), patch.object(BridgeClient, "reenter_design") as restart:
                response = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "e3d_reenter_design", "arguments": arguments}})
                self.assertTrue(response["result"].get("isError", False))
                self.assertIn("Unknown e3d_reenter_design argument", response["result"]["content"][0]["text"])
                restart.assert_not_called()

    def test_internal_messages_are_available_in_mock_mode(self) -> None:
        result = self._call("e3d_read_messages", {"tail": 10})
        self.assertFalse(result.get("isError", False))
        self.assertEqual(result["structuredContent"]["messages"], [])

    def test_attribute_reads_normalize_names_and_reject_commands(self) -> None:
        with patch.object(BridgeClient, "read_attributes", return_value={"ok": True}) as read:
            result = self._call("e3d_read_attributes", {"refno": "/PUMP", "attributes": ["pos", " POS ", "HEIG", ":custom"]})
            self.assertFalse(result.get("isError", False))
            read.assert_called_once_with("/PUMP", ["POS", "HEIG", ":CUSTOM"])
            for invalid in ([], [""], ["POS; DELETE CE"], [1], ["x"] * 51):
                result = self._call("e3d_read_attributes", {"attributes": invalid})
                self.assertTrue(result["isError"])
            self.assertEqual(read.call_count, 1)

    def test_set_attribute_requires_value_before_dispatch(self) -> None:
        server = create_server(Settings(mode="bridge", allow_commands=True))
        with patch.object(BridgeClient, "set_attribute", return_value={"ok": True}) as write:
            response = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "e3d_set_attribute", "arguments": {
                    "refno": "=1/2", "attribute": "DESC", "dry_run": False}}})
        self.assertTrue(response["result"]["isError"])
        self.assertIn("value is required", response["result"]["content"][0]["text"])
        write.assert_not_called()

    def test_failed_batch_is_mcp_error_and_preserves_partial_results(self) -> None:
        payload = {"ok": True, "results": [
            {"command": "$P before", "success": True},
            {"command": "UP", "success": False, "error": "interactive command blocked"},
        ]}
        with patch.object(BridgeClient, "command_batch", return_value=payload):
            result = self._call("e3d_execute_pml_batch", {"commands": ["$P before", "UP", "$P after"]})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"], payload)
        self.assertEqual(json.loads(result["content"][0]["text"]), payload)

    def test_operation_failure_and_model_findings_are_distinct(self) -> None:
        for payload in ({"ok": False, "timeout": True}, {"ok": True, "result": {"success": False}}):
            with patch.object(BridgeClient, "command", return_value=payload):
                result = self._call("e3d_execute_pml", {"command": "UP"})
            self.assertTrue(result["isError"])
        for validity in (False, None):
            payload = {"ok": True, "validation": {"valid": validity, "issues": ["example finding"]}}
            with patch.object(BridgeClient, "validate", return_value=payload):
                result = self._call("e3d_validate_model", {})
            self.assertFalse(result.get("isError", False))
            self.assertEqual(result["structuredContent"], payload)

    def test_partial_attribute_read_preserves_values_and_errors(self) -> None:
        payload = {"ok": True, "result": {"success": False,
            "values": {"HEIG": "40mm"}, "errors": {"BOGUS": "Unknown attribute"}}}
        with patch.object(BridgeClient, "read_attributes", return_value=payload):
            result = self._call("e3d_read_attributes", {"attributes": ["HEIG", "BOGUS"]})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"], payload)

    def test_drawlist_and_cancel_tools_are_available_in_mock_mode(self) -> None:
        drawlist = self._call("e3d_drawlist_snapshot", {})
        self.assertFalse(drawlist.get("isError", False))
        self.assertTrue(drawlist["structuredContent"]["drawlist"]["available"])
        visual = self._call("e3d_set_drawlist_visual", {"refno": "/CODEX-AI-PUMP-SURFACE", "colour": "RED", "edges": True})
        self.assertFalse(visual.get("isError", False))
        fitted = self._call("e3d_fit_view", {"shaded": True, "isometric": True, "auto_add_current": True})
        self.assertFalse(fitted.get("isError", False))
        self.assertTrue(fitted["structuredContent"]["result"]["auto_added_current"])
        restored = self._call("e3d_restore_view", {"fit": True})
        self.assertFalse(restored.get("isError", False))
        self.assertTrue(restored["structuredContent"]["result"]["ready"])
        cancelled = self._call("e3d_cancel_command", {})
        self.assertFalse(cancelled.get("isError", False))

    def test_model_inspection_and_database_mutation_tools_are_available_in_mock_mode(self) -> None:
        snapshot = self._call("e3d_element_snapshot", {"refno": "/CODEX-AI-PUMP-001"})
        self.assertFalse(snapshot.get("isError", False))
        validation = self._call("e3d_validate_model", {"refno": "/CODEX-AI-PUMP-001", "required_types": ["EQUI"]})
        self.assertIsNone(validation["structuredContent"]["validation"]["valid"])
        self.assertFalse(validation["structuredContent"]["validation"]["complete"])
        deletion = self._call("e3d_delete_element", {"refno": "/CODEX-AI-PUMP-001"})
        self.assertFalse(deletion.get("isError", False))

    def test_invalid_json_returns_json_rpc_error(self) -> None:
        output = StringIO()
        with patch("sys.stdout", output):
            self.server.serve(["this is not json\n"])
        response = json.loads(output.getvalue())
        self.assertEqual(response["error"]["code"], -32700)

    def test_bridge_mode_uses_http_adapter(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == "/health":
                    self._send({"ok": True, "connected": True})
                    return
                self._send({"ok": False, "error": "not found"}, 404)

            def do_POST(self) -> None:
                size = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(size))
                self._send({"ok": True, "expression": body["expression"], "count": 0, "elements": []})

            def _send(self, value: dict, status: int = 200) -> None:
                raw = json.dumps(value).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *_: object) -> None:
                return

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            settings = Settings(mode="bridge", bridge_url=f"http://127.0.0.1:{httpd.server_port}")
            client = BridgeClient(settings)
            self.assertTrue(client.health()["connected"])
            self.assertEqual(client.query("/SITE", 5)["expression"], "/SITE")
            with self.assertRaises(BridgeError) as caught:
                client._request("GET", "/missing")
            self.assertIn("HTTP 404: not found", str(caught.exception))
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_command_timeout_automatically_cancels_busy_e3d_command(self) -> None:
        settings = Settings(mode="bridge", bridge_url="http://127.0.0.1:8766", bridge_timeout=0.2)
        client = BridgeClient(settings)
        busy = {"ok": True, "version": "E3D21", "state": {"busy": True, "command": "BAD"}}
        idle = {"ok": True, "version": "E3D21", "state": {"busy": False, "command": ""}}
        with patch.object(client, "_request", side_effect=BridgeError("request timed out", transport_error=True)):
            with patch.object(client, "command_state", side_effect=[busy, idle]) as state:
                with patch.object(client, "cancel_command", return_value={"ok": True, "result": {"requested": True}}) as cancel:
                    result = client.command("BAD", False)
        self.assertTrue(result["timeout"])
        self.assertTrue(result["cancel_attempted"])
        self.assertEqual(result["initial_state"]["command"], "BAD")
        self.assertFalse(result["final_state"]["busy"])
        cancel.assert_called_once_with(version=None)
        self.assertEqual(state.call_count, 2)

    def test_command_batch_timeout_reports_cancel_failure_and_final_state(self) -> None:
        settings = Settings(mode="bridge", bridge_url="http://127.0.0.1:8766", bridge_timeout=0.2)
        client = BridgeClient(settings)
        busy = {"ok": True, "version": "E3D21", "state": {"busy": True, "command": "BAD_BATCH"}}
        with patch.object(client, "_request", side_effect=BridgeError("request timed out", transport_error=True)):
            with patch.object(client, "command_state", return_value=busy):
                with patch.object(client, "cancel_command", side_effect=BridgeError("cancel failed")):
                    result = client.command_batch(["BAD_BATCH"], False)
        self.assertTrue(result["timeout"])
        self.assertTrue(result["cancel_attempted"])
        self.assertEqual(result["cancel_error"], "cancel failed")
        self.assertTrue(result["final_state"]["busy"])

    def test_cancel_command_waits_for_idle_but_legacy_state_cannot_prove_recovery(self) -> None:
        settings = Settings(mode="bridge", bridge_url="http://127.0.0.1:8766", bridge_timeout=0.2)
        client = BridgeClient(settings)
        busy = {"ok": True, "version": "E3D21", "state": {"busy": True, "command": "WAITING"}}
        idle = {"ok": True, "version": "E3D21", "state": {"busy": False, "command": ""}}
        with patch.object(client, "_request", return_value={"ok": True, "version": "E3D21", "result": {"requested": True}}) as request:
            with patch.object(client, "command_state", side_effect=[busy, idle]):
                result = client.cancel_command()
        self.assertTrue(result["command_idle"])
        self.assertFalse(result["settled"])
        self.assertIsNone(result["database_ready"])
        self.assertFalse(result["final_state"]["busy"])
        request.assert_called_once_with("POST", "/api/v1/command-cancel", {}, version=None)

    def test_multiple_versions_are_selected_by_label(self) -> None:
        with patch.dict(
            os.environ,
            {
                "E3D_MCP_MODE": "bridge",
                "E3D_BRIDGES": json.dumps({"E3D21": "http://127.0.0.1:8766", "E3D31": "http://127.0.0.1:8767"}),
                "E3D_MCP_VERSION": "E3D31",
            },
            clear=False,
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.versions(), ["E3D31"])
        self.assertEqual(settings.url_for(), ("http://127.0.0.1:8767", "E3D31"))
        with self.assertRaises(ValueError):
            settings.url_for("E3D4")


if __name__ == "__main__":
    unittest.main()

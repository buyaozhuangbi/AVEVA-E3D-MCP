"""Assembly-section API contracts: read guards, provenance and no partial output."""

from __future__ import annotations

import base64
import copy
import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server
from tests import test_project_assembly_drawing_tool as assembly_fixtures
from tests import test_panel_mesh as panel_fixtures


class SectionAssemblyDrawingToolTests(unittest.TestCase):
    TOOL = "e3d_section_assembly_drawing"
    PLANE = {"origin": [0, 0, 0], "normal": [0, 0, 1]}

    def setUp(self):
        self.server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))
        self.ready = {"ok": True, "connected": True, "version": "E3D21", "session_id": "assembly-section-test",
                      "state": {"busy": False, "database_ready": True, "recovery_required": False,
                                "native_database_fault": {"has_fault": False}}}
        self.project_value = {"ok": True, "version": "E3D21",
                              "project": {"connected": True, "recovery_only": False, "name": "APS", "mdb": "ALL"}}
        for name in ("primitive_geometry", "panel_geometry", "health", "project", "capture_view", "update_primitive",
                     "update_panel", "command", "cancel_command", "_request"):
            patcher = patch.object(BridgeClient, name)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        self.health.return_value = self.ready
        self.project.return_value = self.project_value
        self.primitive_geometry.side_effect = self.envelopes(self.separated_pair())

    @staticmethod
    def target(refno="=1/2", expected_type="BOX"):
        return {"refno": refno, "expected_type": expected_type}

    @staticmethod
    def geometry(*args, **kwargs):
        return assembly_fixtures.ProjectAssemblyDrawingToolTests.geometry(*args, **kwargs)

    @classmethod
    def separated_pair(cls):
        return [cls.geometry(), cls.geometry(refno="=1/3", name="/SECOND", position=[30, 0, 0])]

    @staticmethod
    def envelopes(geometries):
        return [{"ok": True, "version": "E3D21", "result": geometry} for geometry in geometries]

    def rpc(self, method, params):
        return self.server._dispatch({"jsonrpc": "2.0", "id": 9, "method": method, "params": params})["result"]

    def call(self, **options):
        return self.rpc("tools/call", {"name": self.TOOL, "arguments": {
            "targets": [self.target(), self.target("=1/3")], "plane": copy.deepcopy(self.PLANE), **options}})

    def assert_no_writes(self):
        for name in ("capture_view", "update_primitive", "update_panel", "command", "cancel_command", "_request"):
            getattr(self, name).assert_not_called()

    def assert_no_image(self, response):
        self.assertFalse(any(row["type"] == "image" for row in response["content"]))

    @staticmethod
    def png(response):
        return base64.b64decode(next(row["data"] for row in response["content"] if row["type"] == "image"), validate=True)

    def test_catalog_is_readonly_bounded_world_only_and_reuses_plane_hatch_sheet_contracts(self):
        catalog = {row["name"]: row for row in self.rpc("tools/list", {})["tools"]}
        tool = catalog[self.TOOL]
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        schema = tool["inputSchema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), {"targets", "plane"})
        properties = schema["properties"]
        self.assertEqual(properties["coordinate_space"]["enum"], ["WORLD"])
        self.assertEqual(properties["coordinate_space"]["default"], "WORLD")
        targets = properties["targets"]
        self.assertEqual((targets["minItems"], targets["maxItems"]), (1, 16))
        self.assertFalse(targets["items"]["additionalProperties"])
        self.assertEqual(set(targets["items"]["required"]), {"refno", "expected_type"})
        self.assertEqual(set(targets["items"]["properties"]["expected_type"]["enum"]), {"PANE", "BOX", "CYLI", "CONE", "DISH"})
        single = catalog["e3d_section_element_drawing"]["inputSchema"]["properties"]
        for name in ("plane", "hatch", "sheet", "include_svg", "include_dxf", "preview_width", "preview_height", "radial_segments"):
            self.assertEqual(properties[name], single[name])
        for name in ("views", "hidden_line_removal", "show_hidden_lines", "feature_angle_degrees", "dry_run", "mesh_detail"):
            self.assertNotIn(name, properties)
        self.health.assert_not_called()
        self.project.assert_not_called()
        self.primitive_geometry.assert_not_called()
        self.assert_no_writes()

    def test_invalid_options_and_duplicate_requests_fail_before_context_or_geometry_reads(self):
        first = self.target()
        invalid = [
            {"targets": []}, {"targets": None}, {"targets": [first] * 17},
            {"targets": [first, first]}, {"targets": [first, self.target("=0001/02")]},
            {"targets": [self.target("/Pump"), self.target("/PUMP")]},
            {"targets": [first, {"refno": "=1/3"}]},
            {"targets": [first, self.target("=1/3", "PANEL")]},
            {"targets": [{**first, "extra": 1}]}, {"targets": [self.target("CE")]},
            {"coordinate_space": "local"}, {"views": ["SECTION"]}, {"hidden_line_removal": False},
            {"show_hidden_lines": True}, {"feature_angle_degrees": 30}, {"dry_run": True},
            {"mesh_detail": "full"}, {"plane": None}, {"plane": {"origin": [0, 0, 0], "normal": [0, 0, 0]}},
            {"plane": {"origin": [math.inf, 0, 0], "normal": [0, 0, 1]}},
            {"hatch": {}}, {"hatch": {"spacing_mm": 0}}, {"sheet": {"scale_denominator": 0}},
            {"include_svg": 1}, {"include_dxf": "true"}, {"preview_height": True},
            {"preview_width": 2048, "preview_height": 2048}, {"radial_segments": 100},
        ]
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                self.assertTrue(self.call(**arguments).get("isError", False))
        for missing in ("targets", "plane"):
            arguments = {"targets": [first], "plane": self.PLANE}
            del arguments[missing]
            self.assertTrue(self.rpc("tools/call", {"name": self.TOOL, "arguments": arguments}).get("isError", False))
        self.health.assert_not_called()
        self.project.assert_not_called()
        self.primitive_geometry.assert_not_called()
        self.panel_geometry.assert_not_called()
        self.assert_no_writes()

    def test_two_boxes_produce_independent_contours_and_trace_global_triangles_to_each_source(self):
        response = self.call()
        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertTrue(all(result[name] is True for name in ("success", "available", "complete")))
        self.assertTrue(result["context"]["boundary_agreement"])
        self.assertEqual(result["coordinate_space"], "WORLD")
        self.assertEqual(result["section"]["coordinate_unit"], "mm")
        self.assertEqual(result["section"]["area_unit"], "mm2")
        self.assertEqual(result["section"]["length_unit"], "mm")
        self.assertEqual(result["section"]["counts"]["loops"], 2)
        self.assertEqual(result["section"]["counts"]["segments"], 16)
        self.assertEqual(sorted(abs(loop["signed_area"]) for loop in result["section"]["loops"]), [200.0, 200.0])
        ranges = result["source_mesh"]["source_ranges"]
        self.assertEqual([row["triangle_range"] for row in ranges], [[0, 12], [12, 24]])
        self.assertEqual([row["mesh_range"] for row in result["sources"]], ranges)
        source_usage = set()
        for segment in result["section"]["segments"]:
            owners = {index for face in segment["source_triangles"] for index, source in enumerate(ranges)
                      if source["triangle_range"][0] <= face < source["triangle_range"][1]}
            self.assertEqual(len(owners), 1)
            source_usage.update(owners)
            owner = next(iter(owners))
            self.assertEqual(segment["source_index"], owner)
            self.assertEqual(segment["refno"], ranges[owner]["refno"])
            self.assertEqual(segment["source_triangle_indices"], [
                index - ranges[owner]["triangle_range"][0] for index in segment["source_triangles"]])
        self.assertEqual(source_usage, {0, 1})
        memberships = result["section"]["source_sections"]
        self.assertEqual(len(memberships), 2)
        covered_segments, covered_loops = [], []
        for index, membership in enumerate(memberships):
            self.assertEqual(membership["source_index"], index)
            self.assertEqual(membership["refno"], ranges[index]["refno"])
            self.assertEqual(membership["resolved_type"], "BOX")
            self.assertTrue(membership["intersection"])
            self.assertEqual(len(membership["segment_indices"]), 8)
            self.assertEqual(len(membership["loop_indices"]), 1)
            covered_segments.extend(membership["segment_indices"])
            covered_loops.extend(membership["loop_indices"])
            for segment_index in membership["segment_indices"]:
                self.assertEqual(result["section"]["segments"][segment_index]["source_index"], index)
            for loop_index in membership["loop_indices"]:
                loop = result["section"]["loops"][loop_index]
                self.assertEqual(loop["source_index"], index)
                self.assertEqual(loop["refno"], membership["refno"])
                self.assertEqual(set(loop["segment_indices"]), set(membership["segment_indices"]))
        self.assertEqual(sorted(covered_segments), list(range(16)))
        self.assertEqual(sorted(covered_loops), [0, 1])
        self.assertFalse(result["source_mesh"]["boolean_union_applied"])
        self.assertFalse(result["source_mesh"]["vertices_welded"])
        drawing = result["drawing"]
        self.assertEqual(drawing["views"][0]["dimensions"], {"width": 40.0, "height": 20.0, "unit": "mm"})
        self.assertEqual(drawing["segment_count"], 16)
        for line in drawing["views"][0]["lines"]:
            section_segment = result["section"]["segments"][line["section_segment_index"]]
            for field in ("source_index", "refno", "source_triangles", "source_triangle_indices"):
                self.assertEqual(line[field], section_segment[field])
        self.assertNotIn("vertices", result["source_mesh"])
        self.assertNotIn("triangles", result["source_mesh"])
        self.assertNotIn("hatching", result)
        self.assertTrue(self.png(response).startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual([row.args[0] for row in self.primitive_geometry.call_args_list], ["=1/2", "=1/3"])
        self.assertEqual(self.health.call_count, 2)
        self.assertEqual(self.project.call_count, 2)
        self.panel_geometry.assert_not_called()
        self.assert_no_writes()

    def test_hatch_keeps_disjoint_source_gap_and_exports_joint_sheet_in_three_formats(self):
        response = self.call(hatch={"spacing_mm": 5, "angle_degrees": 0}, include_svg=True, include_dxf=True,
                             sheet={"paper_size": "A4", "scale_denominator": 2}, preview_width=800, preview_height=600)
        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertTrue(result["hatching"]["success"])
        scan = [line for line in result["hatching"]["lines"] if line["start"][1] == 0]
        self.assertEqual([(line["start"], line["end"]) for line in scan], [([-5.0, 0.0], [5.0, 0.0]), ([25.0, 0.0], [35.0, 0.0])])
        drawing = result["drawing"]
        self.assertTrue(drawing["hatching_applied"])
        self.assertEqual(drawing["sheet"]["scale_denominator"], 2.0)
        self.assertIn('data-role="hatch"', drawing["svg"])
        self.assertIn("8\nSECTION_HATCH", drawing["dxf"])
        png = self.png(response)
        self.assertEqual((int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")), (800, 600))
        self.assertEqual(self.primitive_geometry.call_count, 2)
        self.assert_no_writes()

    def test_one_target_matches_single_element_section_and_default_png(self):
        geometry = self.geometry()
        self.primitive_geometry.side_effect = self.envelopes([geometry, geometry])
        single = self.rpc("tools/call", {"name": "e3d_section_element_drawing", "arguments": {
            **self.target(), "plane": self.PLANE}})
        assembly = self.call(targets=[self.target()])
        self.assertFalse(single.get("isError", False), single)
        self.assertFalse(assembly.get("isError", False), assembly)
        first, second = [response["structuredContent"]["result"] for response in (single, assembly)]
        for field in ("vertices", "bounds", "plane"):
            self.assertEqual(first["section"][field], second["section"][field])
        for field in ("segments", "loops"):
            for single_item, assembly_item in zip(first["section"][field], second["section"][field]):
                self.assertEqual(single_item, {key: value for key, value in assembly_item.items() if key in single_item})
        self.assertEqual(self.png(single), self.png(assembly))
        self.assert_no_writes()

    def test_mixed_panel_and_box_read_world_once_and_keep_source_type_resolution(self):
        geometry = panel_fixtures._native(points=[[0, 0], [10, 0], [10, 20], [0, 20]])
        geometry["element"]["refno"] = "=1/3"
        geometry["world_positions_requested"] = geometry["world_positions_complete"] = True
        for vertex in geometry["loops"][0]["vertices"]:
            x, y, _ = vertex["position"]
            vertex["position_world_mm"] = [x + 30, y, -1]
            vertex["world_position_complete"] = True
        self.panel_geometry.return_value = self.envelopes([geometry])[0]
        self.primitive_geometry.side_effect = self.envelopes([self.geometry()])
        response = self.call(targets=[self.target(), self.target("=1/3", "PANE")])
        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["sources"][1]["resolved_type"], "PANE")
        self.assertEqual(result["section"]["counts"]["loops"], 2)
        self.primitive_geometry.assert_called_once_with("=1/2")
        self.panel_geometry.assert_called_once_with("=1/3", include_world=True)
        self.assert_no_writes()

    def test_global_radial_resolution_applies_only_to_curved_sources(self):
        pair = [self.geometry(), self.geometry(refno="=1/3", shape="CYLI", position=[30, 0, 0])]
        self.primitive_geometry.side_effect = self.envelopes(pair + pair)
        targets = [self.target(), self.target("=1/3", "CYLI")]
        low = self.call(targets=targets, radial_segments=12)
        high = self.call(targets=targets, radial_segments=24)
        self.assertFalse(low.get("isError", False), low)
        self.assertFalse(high.get("isError", False), high)
        self.assertLess(low["structuredContent"]["result"]["section"]["counts"]["segments"],
                        high["structuredContent"]["result"]["section"]["counts"]["segments"])
        self.assertEqual(self.primitive_geometry.call_count, 4)
        self.assert_no_writes()

    def test_native_fault_stops_without_later_sources_context_reads_or_recovery(self):
        fault = {"ok": False, "version": "E3D21", "error_code": "native_database_fault",
                 "error": "native read failed", "native_diagnostics": {"request_id": "original-fault"}}
        self.primitive_geometry.side_effect = [fault]
        response = self.call(targets=[self.target(), self.target("=1/3"), self.target("=1/4")])
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["error_code"], "assembly_source_unavailable")
        self.assertEqual(result["sources"][0]["read_error"], fault)
        self.assertEqual([row["read_status"] for row in result["sources"]][1:], ["not_read", "not_read"])
        self.assertIsNone(result["context"]["after"])
        self.assertIsNone(result["section"])
        self.assertIsNone(result["drawing"])
        self.assert_no_image(response)
        self.primitive_geometry.assert_called_once_with("=1/2")
        self.assertEqual(self.health.call_count, 1)
        self.assertEqual(self.project.call_count, 1)
        self.assert_no_writes()

    def test_identity_failure_preserves_earlier_source_and_unread_later_target(self):
        pair = self.separated_pair()
        pair[1]["element"]["refno"] = "=1/9"
        self.primitive_geometry.side_effect = self.envelopes(pair)
        response = self.call(targets=[self.target(), self.target("=1/3"), self.target("=1/4")])
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertTrue(result["sources"][0]["success"])
        self.assertEqual(result["sources"][1]["error_code"], "element_reference_mismatch")
        self.assertEqual(result["sources"][1]["resolved_refno"], "=1/9")
        self.assertEqual(result["sources"][2]["read_status"], "not_read")
        self.assertIsNone(result["section"])
        self.assertIsNone(result["drawing"])
        self.assert_no_image(response)
        self.assertEqual(self.primitive_geometry.call_count, 2)
        self.assert_no_writes()

    def test_alias_resolution_cannot_duplicate_one_element_in_section(self):
        first = self.geometry(name="/OUTER")
        self.primitive_geometry.side_effect = self.envelopes([first, copy.deepcopy(first)])
        response = self.call(targets=[self.target(), self.target("/OUTER"), self.target("=1/4")])
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["error_code"], "duplicate_drawing_source")
        self.assertEqual([row.get("resolved_refno") for row in result["sources"][:2]], ["=1/2", "=1/2"])
        self.assertEqual(result["sources"][2]["read_status"], "not_read")
        self.assertIsNone(result["section"])
        self.assertIsNone(result["drawing"])
        self.assert_no_image(response)
        self.assertEqual(self.primitive_geometry.call_count, 2)
        self.assert_no_writes()

    def test_unready_initial_context_prevents_all_source_reads(self):
        self.health.return_value = {**self.ready, "session_id": ""}
        response = self.call()
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["error_code"], "assembly_context_unavailable")
        self.assertTrue(all(row["read_status"] == "not_read" for row in result["sources"]))
        self.assertIsNone(result["section"])
        self.primitive_geometry.assert_not_called()
        self.assert_no_writes()

    def test_changed_context_or_source_version_prevents_section_without_retry(self):
        for changed in ("session", "mdb", "source_version"):
            with self.subTest(changed=changed):
                self.health.reset_mock()
                self.project.reset_mock()
                self.primitive_geometry.reset_mock()
                self.health.side_effect = [self.ready, {**self.ready, "session_id": "changed"} if changed == "session" else self.ready]
                self.project.side_effect = [self.project_value, {**self.project_value, "project": {
                    **self.project_value["project"], "mdb": "OTHER"}} if changed == "mdb" else self.project_value]
                envelopes = self.envelopes(self.separated_pair())
                if changed == "source_version":
                    envelopes[0]["version"] = "E3D31"
                self.primitive_geometry.side_effect = envelopes
                response = self.call()
                self.assertTrue(response.get("isError", False), response)
                result = response["structuredContent"]["result"]
                if changed == "source_version":
                    self.assertEqual(result["error_code"], "assembly_source_unavailable")
                    self.assertEqual(result["sources"][0]["error_code"], "source_version_mismatch")
                    self.assertEqual(self.primitive_geometry.call_count, 1)
                else:
                    self.assertEqual(result["error_code"], "assembly_context_changed")
                    self.assertFalse(result["context"]["boundary_agreement"])
                    self.assertEqual(self.primitive_geometry.call_count, 2)
                self.assertIsNone(result["section"])
                self.assertIsNone(result["drawing"])
                self.assert_no_image(response)
                self.assert_no_writes()

    def test_aggregate_mesh_budget_stops_before_reading_remaining_source(self):
        with patch("aveva_e3d_mcp.tools.MAX_VERTICES", 15):
            response = self.call(targets=[self.target(), self.target("=1/3"), self.target("=1/4")])
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["error_code"], "drawing_assembly_mesh_limit_exceeded")
        self.assertEqual(result["sources"][2]["read_status"], "not_read")
        self.assertEqual(result["observed_counts"]["vertices"], 16)
        self.assertIsNone(result["section"])
        self.assertIsNone(result["drawing"])
        self.assert_no_image(response)
        self.assertEqual(self.primitive_geometry.call_count, 2)
        self.assertEqual(self.health.call_count, 1)
        self.assert_no_writes()

    def test_joint_miss_returns_complete_empty_section_hatching_and_no_image(self):
        response = self.call(plane={"origin": [0, 0, 100], "normal": [0, 0, 1]}, hatch={"spacing_mm": 5})
        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertTrue(all(result[key] is True for key in ("success", "available", "complete")))
        self.assertFalse(result["section"]["intersection"])
        self.assertTrue(result["hatching"]["success"])
        self.assertEqual(result["hatching"]["lines"], [])
        self.assertIsNone(result["drawing"])
        self.assertTrue(all(row["success"] for row in result["sources"]))
        self.assertEqual(result["section"]["source_sections"], [
            {"source_index": index, "refno": f"=1/{index + 2}", "resolved_type": "BOX", "intersection": False,
             "segment_indices": [], "loop_indices": []} for index in range(2)])
        self.assert_no_image(response)
        self.assertEqual(self.primitive_geometry.call_count, 2)
        self.assert_no_writes()

    def test_partial_miss_keeps_all_sources_and_marks_only_intersected_membership(self):
        pair = self.separated_pair()
        pair[1]["frame"]["world"]["position"] = [30, 0, 100]
        self.primitive_geometry.side_effect = self.envelopes(pair)
        response = self.call()
        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(len(result["sources"]), 2)
        self.assertEqual(result["section"]["counts"]["loops"], 1)
        self.assertEqual(result["section"]["counts"]["segments"], 8)
        memberships = result["section"]["source_sections"]
        self.assertEqual([member["intersection"] for member in memberships], [True, False])
        self.assertEqual(memberships[1], {"source_index": 1, "refno": "=1/3", "resolved_type": "BOX",
                                         "intersection": False, "segment_indices": [], "loop_indices": []})
        self.assertTrue(all(segment["source_index"] == 0 for segment in result["section"]["segments"]))
        self.assertTrue(all(source["success"] for source in result["sources"]))
        self.assertTrue(self.png(response).startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(self.primitive_geometry.call_count, 2)
        self.assert_no_writes()

    def test_one_coplanar_source_rejects_joint_section_and_preserves_all_source_reads(self):
        pair = self.separated_pair()
        # The common Z=0 plane cuts the first box but is the second box's top.
        pair[1]["frame"]["world"]["position"] = [30, 0, -15]
        self.primitive_geometry.side_effect = self.envelopes(pair)
        response = self.call(hatch={"spacing_mm": 5})
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["error_code"], "section_coplanar_triangles_unsupported")
        self.assertTrue(all(source["success"] for source in result["sources"]))
        self.assertTrue(result["context"]["boundary_agreement"])
        self.assertFalse(result["section"]["success"])
        self.assertEqual(result["section"]["vertices"], [])
        self.assertEqual(result["section"]["segments"], [])
        self.assertEqual(result["section"]["loops"], [])
        for diagnostic in result["section"]["diagnostics"]:
            self.assertGreaterEqual(diagnostic["triangle"], 12)
            self.assertTrue(diagnostic["source_mapping_available"])
            self.assertEqual(diagnostic["source_evidence"], [{
                "source_index": 1, "refno": "=1/3", "local_triangle_indices": [diagnostic["triangle"] - 12]}])
        self.assertIsNone(result["drawing"])
        self.assert_no_image(response)
        self.assertEqual(self.primitive_geometry.call_count, 2)
        self.assert_no_writes()

    def test_overlapping_independent_contours_are_not_silently_unioned_for_hatching(self):
        pair = [self.geometry(), self.geometry(refno="=1/3", position=[3, 0, 0])]
        self.primitive_geometry.side_effect = self.envelopes(pair)
        response = self.call(hatch={"spacing_mm": 5})
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["error_code"], "section_hatching_failed")
        self.assertTrue(result["section"]["success"])
        self.assertEqual(result["section"]["counts"]["loops"], 2)
        self.assertFalse(result["source_mesh"]["boolean_union_applied"])
        self.assertFalse(result["hatching"]["success"])
        self.assertEqual(result["hatching"]["lines"], [])
        self.assertIsNone(result["drawing"])
        self.assert_no_image(response)
        self.assert_no_writes()

    def test_export_failure_keeps_joint_section_hatching_sources_and_context(self):
        response = self.call(hatch={"spacing_mm": 5, "angle_degrees": 0},
                             sheet={"paper_size": "A4", "scale_denominator": .001})
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["error_code"], "drawing_export_failed")
        self.assertTrue(result["section"]["success"])
        self.assertTrue(result["hatching"]["success"])
        self.assertTrue(all(row["success"] for row in result["sources"]))
        self.assertTrue(result["context"]["boundary_agreement"])
        self.assertIsNone(result["drawing"])
        self.assert_no_image(response)
        self.assert_no_writes()


if __name__ == "__main__":
    unittest.main()

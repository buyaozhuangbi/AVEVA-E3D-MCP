"""End-to-end MCP sheet contracts: preflight, paper/model units and evidence."""
from __future__ import annotations

import base64
import math
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.tools import create_server
from tests import test_project_element_drawing_tool as element_fixtures
from tests import test_orthographic_dxf as dxf_fixtures


class DrawingSheetToolTests(unittest.TestCase):
    TOOLS = ("e3d_project_element_drawing", "e3d_project_assembly_drawing")

    def setUp(self):
        self.server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))
        self.ready = {
            "ok": True, "connected": True, "version": "E3D21", "session_id": "sheet-test",
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

    def rpc(self, method, params):
        return self.server._dispatch({"jsonrpc": "2.0", "id": 5, "method": method, "params": params})["result"]

    def call(self, tool, **options):
        target = {"refno": "=1/2", "expected_type": "BOX"}
        arguments = target if tool == self.TOOLS[0] else {"targets": [target]}
        return self.rpc("tools/call", {"name": tool, "arguments": {**arguments, **options}})

    @staticmethod
    def envelope():
        return {"ok": True, "version": "E3D21",
                "result": element_fixtures.ProjectElementDrawingToolTests.geometry()}

    @staticmethod
    def png(response):
        block = next(block for block in response["content"] if block["type"] == "image")
        return base64.b64decode(block["data"], validate=True)

    def test_both_catalog_entries_expose_same_optional_closed_sheet_object(self):
        catalog = {row["name"]: row for row in self.rpc("tools/list", {})["tools"]}
        first = catalog[self.TOOLS[0]]["inputSchema"]["properties"]["sheet"]
        second = catalog[self.TOOLS[1]]["inputSchema"]["properties"]["sheet"]
        self.assertEqual(first, second)
        self.assertEqual(first["type"], "object")
        self.assertFalse(first["additionalProperties"])
        self.assertEqual(set(first["properties"]["paper_size"]["enum"]), {"A4", "A3"})
        self.assertEqual(set(first["properties"]["orientation"]["enum"]), {"portrait", "landscape"})
        self.assertEqual(first["properties"]["paper_size"]["default"], "A3")
        self.assertEqual(first["properties"]["orientation"]["default"], "landscape")
        self.assertEqual(first["properties"]["title"]["default"], "E3D ORTHOGRAPHIC DRAWING")
        for tool in self.TOOLS:
            self.assertNotIn("sheet", catalog[tool]["inputSchema"]["required"])
            self.assertTrue(catalog[tool]["annotations"]["readOnlyHint"])

    def test_invalid_sheet_settings_fail_before_context_or_geometry_reads(self):
        invalid = [
            None, False, "A4", [], {"unexpected": 1},
            {"paper_size": "A2"}, {"orientation": "diagonal"},
            {"scale_denominator": 0}, {"scale_denominator": -1},
            {"scale_denominator": True}, {"scale_denominator": "2"},
            {"scale_denominator": math.nan}, {"scale_denominator": math.inf},
            {"title": "中文标题"}, {"title": "PUMP;DRAWING"},
            {"title": "<PUMP>"}, {"title": "PUMP\nDRAWING"},
            {"title": "A" * 121}, {"title": None},
            {"drawing_number": "A" * 41}, {"drawing_number": 123},
        ]
        with patch.object(BridgeClient, "primitive_geometry") as read, \
                patch.object(BridgeClient, "panel_geometry") as panel:
            for tool in self.TOOLS:
                for sheet in invalid:
                    with self.subTest(tool=tool, sheet=sheet):
                        response = self.call(tool, sheet=sheet)
                        self.assertTrue(response.get("isError", False), response)
            read.assert_not_called()
            panel.assert_not_called()
        self.health.assert_not_called()
        self.project.assert_not_called()

    def test_empty_sheet_options_produce_a3_landscape_and_auto_scale(self):
        with patch.object(BridgeClient, "primitive_geometry", return_value=self.envelope()) as read:
            for tool in self.TOOLS:
                with self.subTest(tool=tool):
                    response = self.call(tool, sheet={})
                    self.assertFalse(response.get("isError", False), response)
                    result = response["structuredContent"]["result"]
                    scene = result["drawing"]["sheet"]
                    self.assertEqual(scene["paper"]["width_mm"], 420.0)
                    self.assertEqual(scene["paper"]["height_mm"], 297.0)
                    self.assertEqual(scene["paper"]["size"], "A3")
                    self.assertEqual(scene["paper"]["orientation"], "landscape")
                    self.assertGreater(scene["scale_denominator"], 0)
                    self.assertEqual(scene["coordinate_unit"], "mm")
                    self.assertEqual(scene["model_coordinate_unit"], "mm")
                    self.assertTrue(any(text["text"] == "E3D ORTHOGRAPHIC DRAWING" for text in scene["texts"]))
                    self.assertTrue(self.png(response).startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(read.call_count, 2)

    def test_explicit_scale_changes_paper_geometry_without_changing_model_bounds(self):
        for tool in self.TOOLS:
            with self.subTest(tool=tool), patch.object(
                    BridgeClient, "primitive_geometry", return_value=self.envelope()) as read:
                response = self.call(tool, views=["FRONT"], include_svg=True, include_dxf=True,
                                     preview_width=1000, preview_height=700,
                                     sheet={"paper_size": "A4", "orientation": "landscape",
                                            "scale_denominator": 2, "title": "Pump_Sheet",
                                            "drawing_number": "P-001"})
            self.assertFalse(response.get("isError", False), response)
            result = response["structuredContent"]["result"]
            drawing = result["drawing"]
            self.assertEqual(drawing["coordinate_unit"], "mm")
            self.assertEqual(drawing["source_bounds"]["dimensions"], [10.0, 20.0, 30.0])
            self.assertEqual(drawing["views"][0]["dimensions"], {"width": 10.0, "height": 30.0, "unit": "mm"})
            sheet = drawing["sheet"]
            self.assertEqual(sheet["scale_denominator"], 2.0)
            self.assertEqual(sheet["paper"]["width_mm"], 297.0)
            self.assertEqual(sheet["paper"]["height_mm"], 210.0)
            self.assertTrue(any(text["text"] == "PUMP_SHEET" for text in sheet["texts"]))
            svg = ET.fromstring(drawing["svg"])
            self.assertEqual(svg.attrib["width"], "297mm")
            self.assertEqual(svg.attrib["height"], "210mm")
            self.assertEqual(tuple(map(float, svg.attrib["viewBox"].split())), (0.0, 0.0, 297.0, 210.0))
            dxf = drawing["dxf"]
            self.assertIn("9\n$INSUNITS\n70\n4", dxf)
            entities = dxf_fixtures.OrthographicDxfTests.entities(dxf)
            outline = [row for row in entities if row["type"] == "LINE" and row.get("8") == ["FRONT_OUTLINE"]]
            lengths = sorted(math.hypot(float(row["11"][0]) - float(row["10"][0]),
                                        float(row["21"][0]) - float(row["20"][0])) for row in outline)
            self.assertEqual(lengths, [5.0, 5.0, 15.0, 15.0])
            self.assertIn("PUMP_SHEET", [row["1"][0] for row in entities if row["type"] == "TEXT"])
            self.assertTrue(any(row.get("8") == ["SHEET_BORDER"] for row in entities))
            png = self.png(response)
            self.assertEqual((int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")), (1000, 700))
            read.assert_called_once_with("=1/2")

    def test_non_fitting_scale_has_structured_failure_and_retains_source_evidence(self):
        for tool in self.TOOLS:
            with self.subTest(tool=tool), patch.object(
                    BridgeClient, "primitive_geometry", return_value=self.envelope()) as read:
                response = self.call(tool, sheet={"paper_size": "A4", "scale_denominator": .01})
            self.assertTrue(response.get("isError", False), response)
            result = response["structuredContent"]["result"]
            self.assertEqual(result["error_code"], "drawing_export_failed")
            self.assertIn("drawing_sheet_scale_does_not_fit", result["export_error"])
            self.assertIsNone(result["drawing"])
            self.assertFalse(result["success"])
            self.assertFalse(any(row["type"] == "image" for row in response["content"]))
            if tool == self.TOOLS[0]:
                self.assertEqual(result["resolved_refno"], "=1/2")
                self.assertTrue(result["source_mesh"]["counts"]["vertices"] > 0)
            else:
                self.assertTrue(result["sources"][0]["success"])
                self.assertEqual(result["sources"][0]["resolved_refno"], "=1/2")
                self.assertTrue(result["context"]["boundary_agreement"])
            read.assert_called_once_with("=1/2")

    def test_sheet_omission_keeps_existing_model_drawing_and_has_no_scene(self):
        for tool in self.TOOLS:
            with self.subTest(tool=tool), patch.object(
                    BridgeClient, "primitive_geometry", return_value=self.envelope()):
                first = self.call(tool, include_svg=True, include_dxf=True)
                second = self.call(tool, include_svg=True, include_dxf=True)
            self.assertFalse(first.get("isError", False), first)
            self.assertEqual(first, second)
            drawing = first["structuredContent"]["result"]["drawing"]
            self.assertNotIn("sheet", drawing)
            self.assertEqual(drawing["source_bounds"]["dimensions"], [10.0, 20.0, 30.0])
            self.assertEqual((int.from_bytes(self.png(first)[16:20], "big"),
                              int.from_bytes(self.png(first)[20:24], "big")), (1200, 440))


if __name__ == "__main__":
    unittest.main()

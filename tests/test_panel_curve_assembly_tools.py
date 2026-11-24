from copy import deepcopy
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.panel_curve_boundary import derive_panel_curve_boundary
from aveva_e3d_mcp.tools import create_server
from test_panel_curve_boundary import install_unset_existence, install_world, rectangle_native


TOOL = "e3d_project_panel_curve_assembly_drawing"
READ = "aveva_e3d_mcp.panel_curve_assembly_workflow.read_panel_curve_assembly"


def observations(count):
    boundary = derive_panel_curve_boundary(install_world(install_unset_existence(rectangle_native())),
                                          coordinate_space="WORLD", chord_tolerance_mm=0.1)
    assert boundary["success"]
    boundaries = []
    for index in range(count):
        item = deepcopy(boundary)
        item["source"]["refno"] = f"=2/{index + 1}"
        boundaries.append(item)
    targets = [item["source"]["refno"] for item in boundaries]
    return {"success": True, "available": True, "complete": True,
            "requested_targets": targets, "boundaries": boundaries,
            "sources": [{"source_index": i, "requested_refno": target, "resolved_refno": target,
                         "read_status": "returned", "success": True, "available": True, "complete": True,
                         "curve_summary": {"native_boundary_points_complete": False,
                                           "native_existence_verified": False}}
                        for i, target in enumerate(targets)],
            "context": {"before": {"version": "E3D21"}, "after": {"version": "E3D21"},
                        "boundary_agreement": True}, "atomic_snapshot": False}


class PanelCurveAssemblyToolTests(unittest.TestCase):
    def setUp(self):
        self.server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))

    def call(self, args):
        return self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": TOOL, "arguments": args}})["result"]

    def test_tool_is_discoverable_and_explicitly_read_only(self):
        catalog = self.server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})["result"]["tools"]
        self.assertEqual(len(catalog), 120)
        definition = next(row for row in catalog if row["name"] == TOOL)
        self.assertTrue(definition["annotations"]["readOnlyHint"])
        self.assertEqual(definition["inputSchema"]["required"], ["targets"])
        self.assertFalse(definition["inputSchema"]["additionalProperties"])
        self.assertEqual(definition["inputSchema"]["properties"]["targets"]["maxItems"], 128)

    def test_invalid_arguments_stop_before_reading(self):
        cases = [{"targets": []}, {"targets": ["CE"]}, {"targets": ["=1/2", "=01/002"]},
                 {"targets": ["/PANEL", "/panel"]}, {"targets": ["=1/2"] * 129},
                 {"panels_per_page": 1}, {"panels_per_page": True}, {"chord_tolerance_mm": True},
                 {"chord_tolerance_mm": 10**400}, {"chord_tolerance_mm": float("inf")},
                 {"chord_tolerance_mm": 0}, {"views": [["TOP"]]}, {"views": ["TOP", "TOP"]},
                 {"include_svg": 1}, {"coordinate_space": "panel_local"},
                 {"preview_width": 2048, "preview_height": 2048},
                 {"targets": [f"=2/{i}" for i in range(128)], "preview_width": 1200, "preview_height": 1100}]
        for overrides in cases:
            with self.subTest(overrides=overrides), patch(READ) as read:
                response = self.call({"targets": ["=1/2"], **overrides})
                self.assertTrue(response.get("isError"), response)
                read.assert_not_called()

    def test_real_multi_page_composition_keeps_provenance_and_native_unknowns(self):
        observed = observations(17)
        with patch(READ, return_value=observed) as read:
            response = self.call({"targets": observed["requested_targets"], "include_svg": True,
                                  "include_dxf": True, "preview_width": 900, "preview_height": 450})
        self.assertFalse(response.get("isError", False), response)
        self.assertEqual(read.call_args.kwargs["chord_tolerance_mm"], 0.1)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["page_count"], 2)
        self.assertNotIn("boundaries", result)
        self.assertFalse(result["atomic_snapshot"])
        self.assertEqual(len(result["sources"]), 17)
        for source in result["sources"]:
            self.assertFalse(source["curve_summary"]["native_existence_verified"])
        images = [item for item in response["content"] if item["type"] == "image"]
        self.assertEqual(len(images), 2)
        import base64
        for image in images:
            png = base64.b64decode(image["data"], validate=True)
            self.assertEqual(struct.unpack(">II", png[16:24]), (900, 450))
        pages = result["pages"]
        self.assertEqual(pages[0]["source_range"], [0, 16])
        self.assertEqual(pages[1]["source_range"], [16, 17])
        for first, second in zip(pages[0]["drawing"]["views"], pages[1]["drawing"]["views"]):
            self.assertEqual(first["bounds"], second["bounds"])
        self.assertEqual({line["source_index"] for view in pages[1]["drawing"]["views"] for line in view["lines"]}, {16})
        self.assertIn("svg", pages[0]["drawing"])
        self.assertIn("dxf", pages[0]["drawing"])

    def test_single_page_still_returns_a_png_and_drawing_alias(self):
        observed = observations(1)
        with patch(READ, return_value=observed):
            response = self.call({"targets": observed["requested_targets"]})
        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["drawing"], result["pages"][0]["drawing"])
        self.assertEqual(sum(item["type"] == "image" for item in response["content"]), 1)

    def test_partial_native_read_returns_evidence_without_composing_or_images(self):
        observed = observations(1)
        observed.update(success=False, available=False, complete=False, boundaries=[], error_code="source_changed")
        with patch(READ, return_value=observed), patch("aveva_e3d_mcp.panel_curve_assembly.compose_panel_curve_pages") as compose:
            response = self.call({"targets": observed["requested_targets"]})
        self.assertTrue(response.get("isError"), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["error_code"], "source_changed")
        self.assertEqual(result["sources"], observed["sources"])
        self.assertEqual(result["pages"], [])
        compose.assert_not_called()
        self.assertFalse(any(item["type"] == "image" for item in response["content"]))

    def test_failed_export_discards_images_already_rendered(self):
        observed = observations(17)
        with patch(READ, return_value=observed), \
             patch("aveva_e3d_mcp.panel_curve_drawing.render_panel_curve_png", side_effect=[b"earlier-page", ValueError("late_export_error")]):
            response = self.call({"targets": observed["requested_targets"]})
        self.assertTrue(response.get("isError"), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["drawing_error"], "late_export_error")
        self.assertEqual(result["pages"], [])
        self.assertFalse(any(item["type"] == "image" for item in response["content"]))

    def test_output_budget_applies_to_single_page_and_retains_only_source_summaries(self):
        observed = observations(1)
        observed["sources"][0]["source_evidence"] = {"raw_payload": "x" * (8 * 1024 * 1024)}
        with patch(READ, return_value=observed):
            response = self.call({"targets": observed["requested_targets"]})
        self.assertTrue(response.get("isError"), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["error_code"], "panel_curve_assembly_output_limit_exceeded")
        self.assertEqual(result["sources"][0]["resolved_refno"], observed["requested_targets"][0])
        self.assertFalse(result["sources"][0]["curve_summary"]["native_existence_verified"])
        self.assertNotIn("source_evidence", result["sources"][0])
        self.assertGreater(result["observed_output_bytes"]["json"], 8 * 1024 * 1024)
        self.assertEqual(result["pages"], [])
        self.assertFalse(any(item["type"] == "image" for item in response["content"]))


if __name__ == "__main__":
    unittest.main()

"""Public composition discovery, exports and stage-failure evidence."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aveva_e3d_mcp import tools as production
from tests import test_section_assembly_drawing_tool as fixtures


class CompositeSheetToolTests(unittest.TestCase):
    TOOL = "e3d_compose_drawing_sheet"

    def setUp(self):
        self.fixture = fixtures.SectionAssemblyDrawingToolTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def call(self, **options):
        return self.fixture.rpc("tools/call", {"name": self.TOOL, "arguments": {
            "targets": [self.fixture.target(), self.fixture.target("=1/3")],
            "plane": copy.deepcopy(self.fixture.PLANE), "sheet": {}, **options}})

    def call_atlas(self, pages, **options):
        return self.fixture.rpc("tools/call", {"name": "e3d_compose_drawing_atlas", "arguments": {
            "pages": copy.deepcopy(pages), "sheet": {"scale_denominator": 20, "paper_size": "A3"},
            **options}})

    def reset_sources(self):
        self.fixture.primitive_geometry.side_effect = self.fixture.envelopes(self.fixture.separated_pair())

    def result(self, response):
        self.assertFalse(response.get("isError"), response)
        self.fixture.assert_no_writes()
        return response["structuredContent"]["result"]

    def failed(self, response):
        self.assertTrue(response.get("isError"), response)
        result = response["structuredContent"]["result"]
        self.assertIsNone(result["drawing"])
        self.fixture.assert_no_image(response)
        self.fixture.assert_no_writes()
        return result

    def test_catalog_exposes_required_sheet_and_fixed_views_with_default_third_angle(self):
        catalog = {row["name"]: row for row in self.fixture.rpc("tools/list", {})["tools"]}
        self.assertEqual(len(catalog), 120)
        tool = catalog[self.TOOL]
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        schema = tool["inputSchema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), {"targets", "plane", "sheet"})
        props = schema["properties"]
        self.assertNotIn("views", props)
        self.assertEqual(props["coordinate_space"]["enum"], ["WORLD"])
        self.assertEqual(props["targets"]["maxItems"], 16)
        method = props["sheet"]["properties"]["projection_method"]
        self.assertEqual(method["default"], "third_angle")
        self.assertEqual(method["enum"], ["first_angle", "third_angle"])
        for key in ("plane", "hatch", "dimensions"):
            self.assertEqual(props[key], catalog["e3d_section_assembly_drawing"]["inputSchema"]["properties"][key])
        atlas = catalog["e3d_compose_drawing_atlas"]
        self.assertTrue(atlas["annotations"]["readOnlyHint"])
        self.assertEqual(set(atlas["inputSchema"]["required"]), {"pages", "sheet"})
        self.assertEqual(atlas["inputSchema"]["properties"]["sheet"]["required"], ["scale_denominator"])
        atlas_properties = atlas["inputSchema"]["properties"]
        self.assertNotIn("plane", atlas_properties)
        self.assertNotIn("dimensions", atlas_properties)
        page_schema = atlas_properties["pages"]
        self.assertEqual(page_schema["minItems"], 2)
        self.assertEqual(page_schema["maxItems"], 24)
        self.assertEqual(page_schema["items"]["properties"]["plane"],
                         catalog["e3d_section_assembly_drawing"]["inputSchema"]["properties"]["plane"])
        self.assertIn("dimensions", page_schema["items"]["properties"])
        for missing in ("targets", "plane", "sheet"):
            args = {"targets": [self.fixture.target()], "plane": self.fixture.PLANE, "sheet": {}}
            del args[missing]
            self.assertTrue(self.fixture.rpc("tools/call", {"name": self.TOOL, "arguments": args}).get("isError"))
        self.fixture.health.assert_not_called()

    def test_both_methods_share_model_linework_and_export_all_four_views(self):
        outputs = []
        for method in ("first_angle", "third_angle"):
            self.reset_sources()
            options = {"paper_size": "A4", "projection_method": method}
            before = copy.deepcopy(options)
            response = self.call(sheet=options, include_svg=True, include_dxf=True, show_hidden_lines=True,
                                 hatch={"spacing_mm": 5}, preview_width=800, preview_height=600)
            result = self.result(response)
            outputs.append(result)
            self.assertEqual(options, before)
            self.assertEqual(result["drawing"]["sheet"]["projection_method"], method)
            self.assertIn("<svg", result["drawing"]["svg"])
            self.assertNotIn("svg", result["projection"])
            for name in ("FRONT", "TOP", "SIDE", "SECTION"):
                self.assertIn(name + "_OUTLINE", result["drawing"]["dxf"])
            self.assertIn("SECTION_HATCH", result["drawing"]["dxf"])
            png = self.fixture.png(response)
            self.assertEqual((int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")), (800, 600))
            self.assertEqual(result["drawing"]["views"][:3], result["projection"]["views"])
            self.assertFalse(result["drawing"]["retained_solid_projection"])
        self.assertEqual(outputs[0]["drawing"]["views"], outputs[1]["drawing"]["views"])
        self.assertEqual(outputs[0]["section"]["dimension_revision"], outputs[1]["section"]["dimension_revision"])

    def test_default_only_affects_new_tool_without_mutating_sheet_or_leaking_unused_exports(self):
        sheet = {}
        result = self.result(self.call(sheet=sheet))
        self.assertEqual(sheet, {})
        self.assertEqual(result["drawing"]["sheet"]["projection_method"], "third_angle")
        for key in ("projection", "drawing"):
            self.assertNotIn("svg", result[key])
            self.assertNotIn("dxf", result[key])
        self.assertNotIn("dimensions", result)
        self.assertNotIn("hatching", result)

    def test_standalone_section_and_composition_accept_same_revision_and_selectors(self):
        args = {"targets": [self.fixture.target(), self.fixture.target("=1/3")], "plane": self.fixture.PLANE}
        first = self.result(self.fixture.rpc("tools/call", {"name": "e3d_section_assembly_drawing", "arguments": args}))
        section = first["section"]
        points = section["vertices"]
        selectors = {"expected_section_revision": section["dimension_revision"], "items": [{
            "first_vertex": min(range(len(points)), key=lambda i: points[i][0]),
            "second_vertex": max(range(len(points)), key=lambda i: points[i][0]), "axis": "horizontal"}]}
        self.reset_sources()
        result = self.result(self.call(dimensions=selectors))
        self.assertEqual(result["section"], section)
        self.assertEqual(result["dimensions"]["items"][0]["value_mm"], 40)

    def test_plane_miss_keeps_successful_empty_section_and_projection_without_partial_image(self):
        result = self.failed(self.call(plane={"origin": [0, 0, 1000], "normal": [0, 0, 1]}))
        self.assertEqual(result["error_code"], "composition_section_no_intersection")
        self.assertTrue(result["projection"]["success"])
        self.assertTrue(result["section"]["success"])
        self.assertFalse(result["section"]["intersection"])
        self.assertEqual(result["section"]["vertices"], [])

    def test_returned_or_raised_projection_failure_does_not_slice(self):
        for value in ({"success": False, "available": False, "complete": False, "views": [],
                       "error_code": "visibility_work_budget_exceeded"}, RuntimeError("project failed")):
            self.reset_sources()
            with patch.object(production, "generate_orthographic_drawing") as project, \
                    patch.object(production, "slice_triangle_mesh") as section:
                if isinstance(value, Exception):
                    project.side_effect = value
                else:
                    project.return_value = value
                result = self.failed(self.call())
            self.assertTrue(all(row["success"] for row in result["sources"]))
            self.assertIsNone(result["section"])
            section.assert_not_called()
            self.assertEqual(result["error_code"], "composition_projection_failed" if isinstance(value, Exception) else value["error_code"])

    def test_raised_section_error_keeps_completed_projection(self):
        with patch.object(production, "slice_triangle_mesh", side_effect=RuntimeError("slice failed")):
            result = self.failed(self.call())
        self.assertEqual(result["error_code"], "composition_section_failed")
        self.assertTrue(result["projection"]["success"])
        self.assertTrue(all(row["success"] for row in result["sources"]))

    def test_export_failure_keeps_all_successful_stages_without_partial_image(self):
        for function in ("build_drawing_sheet", "render_drawing_sheet_svg", "export_drawing_sheet_dxf", "rasterize_drawing_sheet_png"):
            self.reset_sources()
            with patch.object(production, function, side_effect=RuntimeError("export failed")):
                result = self.failed(self.call(include_svg=True, include_dxf=True, hatch={"spacing_mm": 5}))
            self.assertEqual(result["error_code"], "drawing_export_failed")
            for key in ("projection", "section", "hatching"):
                self.assertTrue(result[key]["success"])
            self.assertTrue(all(row["success"] for row in result["sources"]))

    def test_multi_page_atlas_emits_separate_images_with_one_verified_scale_and_manifest(self):
        pages = [
            {"targets": [self.fixture.target()], "plane": copy.deepcopy(self.fixture.PLANE),
             "title": "PUMP FRONT", "drawing_number": "DWG-01"},
            {"targets": [self.fixture.target("=1/3")], "plane": copy.deepcopy(self.fixture.PLANE),
             "title": "PUMP REAR", "drawing_number": "DWG-02"},
        ]
        response = self.call_atlas(pages, include_svg=True, include_dxf=True,
                                   preview_width=640, preview_height=480)
        self.assertFalse(response.get("isError"), response)
        images = [item for item in response["content"] if item["type"] == "image"]
        self.assertEqual(len(images), 2)
        self.assertEqual([item["type"] for item in response["content"]].count("text"), 3)
        result = response["structuredContent"]["result"]
        self.assertTrue(result["complete"])
        self.assertEqual((result["page_count"], result["completed_page_count"]), (2, 2))
        self.assertEqual(result["total_requested_sources"], 2)
        self.assertEqual(len(result["manifest_sha256"]), 64)
        self.assertEqual([page["title"] for page in result["pages"]], ["PUMP FRONT", "PUMP REAR"])
        self.assertTrue(all(page["complete"] for page in result["pages"]))
        self.assertTrue(all(page["result"]["drawing"]["sheet"]["scale_denominator"] == 20
                            for page in result["pages"]))
        self.assertTrue(all("<svg" in page["result"]["drawing"]["svg"] and
                            "$ACADVER" in page["result"]["drawing"]["dxf"] for page in result["pages"]))
        self.fixture.assert_no_writes()

        self.reset_sources()
        changed_options = self.call_atlas(pages, include_svg=True, include_dxf=False,
                                          preview_width=640, preview_height=480)
        self.assertNotEqual(result["manifest_sha256"],
                            changed_options["structuredContent"]["result"]["manifest_sha256"])

    def test_atlas_generated_drawing_numbers_keep_unique_suffix_with_long_shared_base(self):
        pages = [
            {"targets": [self.fixture.target()], "plane": copy.deepcopy(self.fixture.PLANE)},
            {"targets": [self.fixture.target("=1/3")], "plane": copy.deepcopy(self.fixture.PLANE)},
        ]
        response = self.call_atlas(pages, sheet={"paper_size": "A3", "scale_denominator": 20,
                                                  "drawing_number": "X" * 40})
        result = self.result(response)
        numbers = [page["drawing_number"] for page in result["pages"]]
        self.assertEqual([len(number) for number in numbers], [40, 40])
        self.assertEqual(numbers[0][-4:], "-P01")
        self.assertEqual(numbers[1][-4:], "-P02")
        self.assertEqual(len(set(numbers)), 2)

    def test_multi_page_aggregate_output_limit_suppresses_images_and_compacts_evidence(self):
        pages = [
            {"targets": [self.fixture.target()], "plane": copy.deepcopy(self.fixture.PLANE)},
            {"targets": [self.fixture.target("=1/3")], "plane": copy.deepcopy(self.fixture.PLANE)},
        ]
        real_dumps = production.json.dumps

        def oversized_metadata(value, *args, **kwargs):
            if isinstance(value, dict) and value.get("ok") is True and isinstance(value.get("result"), dict) and \
                    value["result"].get("page_count") == 2:
                return "x" * (8 * 1024 * 1024 + 1)
            return real_dumps(value, *args, **kwargs)

        with patch.object(production.json, "dumps", side_effect=oversized_metadata):
            response = self.call_atlas(pages)
        result = response["structuredContent"]["result"]
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "multi_page_output_limit_exceeded")
        self.assertEqual(len(result["pages"]), 2)
        self.assertTrue(all("drawing" not in page["result"] for page in result["pages"]))
        self.fixture.assert_no_image(response)
        self.fixture.assert_no_writes()

    def test_multi_page_atlas_requires_explicit_scale_before_native_reads(self):
        arguments = {"pages": [
            {"targets": [self.fixture.target()], "plane": self.fixture.PLANE},
            {"targets": [self.fixture.target("=1/3")], "plane": self.fixture.PLANE},
        ], "sheet": {}}
        response = self.fixture.rpc("tools/call", {"name": "e3d_compose_drawing_atlas", "arguments": arguments})
        self.assertTrue(response.get("isError"), response)
        self.fixture.health.assert_not_called()
        self.fixture.primitive_geometry.assert_not_called()
        self.assertFalse(any(item["type"] == "image" for item in response["content"]))

    def test_multi_page_atlas_accepts_large_page_count_under_shared_pixel_budget(self):
        pages = [{"targets": [self.fixture.target()], "plane": copy.deepcopy(self.fixture.PLANE)}
                 for _ in range(9)]
        self.fixture.primitive_geometry.side_effect = None
        self.fixture.primitive_geometry.return_value = self.fixture.envelopes(self.fixture.separated_pair())[0]
        response = self.call_atlas(pages, preview_width=128, preview_height=128)
        self.assertFalse(response.get("isError"), response)
        result = response["structuredContent"]["result"]
        self.assertTrue(result["complete"])
        self.assertEqual((result["page_count"], result["completed_page_count"]), (9, 9))
        self.assertEqual(sum(item["type"] == "image" for item in response["content"]), 9)
        self.fixture.assert_no_writes()

    def test_multi_page_failure_suppresses_all_images_and_keeps_failure_receipt(self):
        self.fixture.primitive_geometry.side_effect = [
            *self.fixture.envelopes(self.fixture.separated_pair())[:1],
            {"ok": False, "version": "E3D21", "error": "native page read failed"},
        ]
        pages = [
            {"targets": [self.fixture.target()], "plane": copy.deepcopy(self.fixture.PLANE)},
            {"targets": [self.fixture.target("=1/3")], "plane": copy.deepcopy(self.fixture.PLANE)},
        ]
        response = self.call_atlas(pages)
        self.assertTrue(response.get("isError"), response)
        result = response["structuredContent"]["result"]
        self.assertFalse(result["complete"])
        self.assertEqual(result["completed_page_count"], 1)
        self.assertEqual(len(result["pages"]), 2)
        self.assertFalse(any(item["type"] == "image" for item in response["content"]))
        self.fixture.assert_no_writes()

    def test_multi_page_atlas_rejects_duplicate_targets_within_one_page_before_reads(self):
        duplicated = {"targets": [self.fixture.target("/DUP"), self.fixture.target("/dup")],
                      "plane": copy.deepcopy(self.fixture.PLANE)}
        pages = [duplicated, {"targets": [self.fixture.target("=1/3")],
                              "plane": copy.deepcopy(self.fixture.PLANE)}]
        response = self.call_atlas(pages)
        self.assertTrue(response.get("isError"), response)
        self.fixture.health.assert_not_called()
        self.fixture.primitive_geometry.assert_not_called()

    def test_multi_page_atlas_rejects_total_preview_budget_before_reads(self):
        pages = [{"targets": [self.fixture.target(f"=1/{index + 2}")],
                  "plane": copy.deepcopy(self.fixture.PLANE)} for index in range(8)]
        arguments = {"pages": pages, "sheet": {"scale_denominator": 20},
                     "preview_width": 2048, "preview_height": 976}
        response = self.fixture.rpc("tools/call", {"name": "e3d_compose_drawing_atlas", "arguments": arguments})
        self.assertTrue(response.get("isError"), response)
        self.fixture.health.assert_not_called()
        self.fixture.primitive_geometry.assert_not_called()

    def test_multi_page_atlas_preserves_unexpected_page_exception_and_suppresses_images(self):
        original = production._compose_drawing_sheet
        calls = 0

        def compose(bridge, arguments):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("unexpected export failure")
            return original(bridge, arguments)

        pages = [
            {"targets": [self.fixture.target()], "plane": copy.deepcopy(self.fixture.PLANE)},
            {"targets": [self.fixture.target("=1/3")], "plane": copy.deepcopy(self.fixture.PLANE)},
        ]
        with patch.object(production, "_compose_drawing_sheet", side_effect=compose):
            response = self.call_atlas(pages)
        self.assertTrue(response.get("isError"), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["pages"][1]["error_code"], "page_composition_exception")
        self.assertEqual(result["pages"][1]["error_type"], "RuntimeError")
        self.assertTrue(result["pages"][0]["complete"])
        self.assertEqual(result["completed_page_count"], 1)
        self.assertFalse(any(item["type"] == "image" for item in response["content"]))

    def test_multi_page_atlas_rejects_context_change_between_pages(self):
        projects = [copy.deepcopy(self.fixture.project_value) for _ in range(4)]
        projects[2]["project"]["mdb"] = "DESIGN"
        projects[3]["project"]["mdb"] = "DESIGN"
        self.fixture.project.side_effect = projects
        pages = [
            {"targets": [self.fixture.target()], "plane": copy.deepcopy(self.fixture.PLANE)},
            {"targets": [self.fixture.target("=1/3")], "plane": copy.deepcopy(self.fixture.PLANE)},
        ]
        response = self.call_atlas(pages)
        self.assertTrue(response.get("isError"), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["error_code"], "multi_page_context_changed")
        self.assertEqual(result["completed_page_count"], 1)
        self.assertEqual(len(result["pages"]), 2)
        self.assertFalse(any(item["type"] == "image" for item in response["content"]))


if __name__ == "__main__":
    unittest.main()

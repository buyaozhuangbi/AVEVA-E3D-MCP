"""Section-hatching MCP preflight, failure evidence and export contracts."""

from __future__ import annotations

import base64
import hashlib
import math
import re
import struct
import sys
import unittest
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp import tools as tools_module
from aveva_e3d_mcp.tools import create_server
from tests import test_project_element_drawing_tool as element_fixtures
from tests import test_orthographic_dxf as dxf_fixtures
from tests import test_panel_mesh as panel_fixtures


class SectionHatchingToolTests(unittest.TestCase):
    TOOL = "e3d_section_element_drawing"
    HATCH = {"spacing_mm": 5, "angle_degrees": 0, "phase_mm": 0}
    OLD_BOX_PNG_SHA256 = "f62a13ca3289b68d29ac9e30bd425153e9fd4dbc8d0d75bbb298bb1cc233d3e5"

    def setUp(self):
        self.server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))
        for name in ("primitive_geometry", "panel_geometry", "health", "project", "capture_view",
                     "update_primitive", "update_panel", "command", "_request"):
            patcher = patch.object(BridgeClient, name)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        self.primitive_geometry.return_value = {"ok": True, "version": "E3D21", "result":
            element_fixtures.ProjectElementDrawingToolTests.geometry()}

    def rpc(self, method, params):
        return self.server._dispatch({"jsonrpc": "2.0", "id": 8, "method": method, "params": params})["result"]

    def call(self, **options):
        arguments = {"refno": "=1/2", "expected_type": "BOX",
                     "plane": {"origin": [0, 0, 0], "normal": [0, 0, 1]}, **options}
        return self.rpc("tools/call", {"name": self.TOOL, "arguments": arguments})

    def assert_only_primitive_reads(self, count=1):
        self.assertEqual(self.primitive_geometry.call_count, count)
        for name in ("panel_geometry", "health", "project", "capture_view", "update_primitive",
                     "update_panel", "command", "_request"):
            getattr(self, name).assert_not_called()

    @staticmethod
    def png(response):
        block = next(row for row in response["content"] if row["type"] == "image")
        return base64.b64decode(block["data"], validate=True)

    @staticmethod
    def png_rgb(png):
        """Decode ordinary non-interlaced RGB PNG independently of the renderer."""
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", png[16:29])
        assert (depth, color, compression, filtering, interlace) == (8, 2, 0, 0, 0)
        data = bytearray()
        offset = 8
        while offset < len(png):
            length = int.from_bytes(png[offset:offset + 4], "big")
            if png[offset + 4:offset + 8] == b"IDAT":
                data.extend(png[offset + 8:offset + 8 + length])
            offset += length + 12
        raw = zlib.decompress(data)
        stride = width * 3
        rows, previous = [], bytearray(stride)
        for y in range(height):
            method = raw[y * (stride + 1)]
            row = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
            for index in range(stride):
                left = row[index - 3] if index >= 3 else 0
                above = previous[index]
                upper_left = previous[index - 3] if index >= 3 else 0
                if method == 0:
                    correction = 0
                elif method == 1:
                    correction = left
                elif method == 2:
                    correction = above
                elif method == 3:
                    correction = (left + above) // 2
                elif method == 4:
                    prediction = left + above - upper_left
                    correction = min((left, above, upper_left), key=lambda value: abs(prediction - value))
                else:
                    raise AssertionError("Unsupported PNG row filter")
                row[index] = (row[index] + correction) % 256
            rows.append([tuple(row[x:x + 3]) for x in range(0, stride, 3)])
            previous = row
        return rows

    def test_hatch_schema_is_optional_closed_and_exclusive_to_section_tool(self):
        catalog = {row["name"]: row for row in self.rpc("tools/list", {})["tools"]}
        schema = catalog[self.TOOL]["inputSchema"]
        self.assertNotIn("hatch", schema["required"])
        hatch = schema["properties"]["hatch"]
        self.assertFalse(hatch["additionalProperties"])
        self.assertEqual(hatch["required"], ["spacing_mm"])
        properties = hatch["properties"]
        self.assertEqual(set(properties), {"spacing_mm", "angle_degrees", "phase_mm", "fill_rule"})
        self.assertEqual(properties["spacing_mm"]["type"], "number")
        self.assertEqual(properties["spacing_mm"]["minimum"], 1e-6)
        self.assertEqual(properties["spacing_mm"]["maximum"], 1e9)
        self.assertEqual(properties["angle_degrees"]["minimum"], 0)
        self.assertEqual(properties["angle_degrees"]["exclusiveMaximum"], 180)
        self.assertEqual(properties["angle_degrees"]["default"], 45)
        self.assertEqual(properties["phase_mm"]["default"], 0)
        self.assertEqual(properties["fill_rule"]["enum"], ["nonzero"])
        self.assertEqual(properties["fill_rule"]["default"], "nonzero")
        for tool in ("e3d_project_element_drawing", "e3d_project_assembly_drawing"):
            self.assertNotIn("hatch", catalog[tool]["inputSchema"]["properties"])
        self.assert_only_primitive_reads(0)

    def test_all_invalid_hatching_is_rejected_before_any_native_read(self):
        invalid = [None, False, [], "5", {}, {"spacing_mm": 5, "extra": True}]
        for value in (None, False, "5", 0, -1, 1e-7, 1e9 + 1, math.nan, math.inf, 10 ** 400):
            invalid.append({"spacing_mm": value})
        for value in (None, False, "45", -1, 180, 360, math.nan, math.inf):
            invalid.append({"spacing_mm": 5, "angle_degrees": value})
        for value in (None, False, "0", -1e9 - 1, 1e9 + 1, math.nan, math.inf):
            invalid.append({"spacing_mm": 5, "phase_mm": value})
        for value in (None, False, 1, "evenodd", "NONZERO", ""):
            invalid.append({"spacing_mm": 5, "fill_rule": value})
        for value in invalid:
            with self.subTest(hatch=value):
                response = self.call(hatch=value)
                self.assertTrue(response.get("isError", False), response)
        for tool in ("e3d_project_element_drawing", "e3d_project_assembly_drawing"):
            target = {"refno": "=1/2", "expected_type": "BOX"}
            arguments = target if tool == "e3d_project_element_drawing" else {"targets": [target]}
            response = self.rpc("tools/call", {"name": tool, "arguments": {**arguments, "hatch": self.HATCH}})
            self.assertTrue(response.get("isError", False), response)
        self.assert_only_primitive_reads(0)

    def test_box_horizontal_scans_preserve_contours_and_expose_exact_generation_evidence(self):
        response = self.call(hatch=self.HATCH)
        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        hatching = result["hatching"]
        self.assertTrue(all(hatching[field] is True for field in ("success", "available", "complete")))
        self.assertEqual(hatching["coordinate_unit"], "mm")
        self.assertEqual(hatching["options"]["fill_rule"], "nonzero")
        self.assertIsNone(hatching["error_code"])
        self.assertEqual(sorted((tuple(line["start"]), tuple(line["end"]), line["scanline_index"])
                                for line in hatching["lines"]), [
            ((-5.0, -10.0), (5.0, -10.0), -2), ((-5.0, -5.0), (5.0, -5.0), -1),
            ((-5.0, 0.0), (5.0, 0.0), 0), ((-5.0, 5.0), (5.0, 5.0), 1),
        ])
        drawing = result["drawing"]
        self.assertTrue(drawing["hatching_applied"])
        self.assertFalse(drawing["hole_semantics_inferred"])
        self.assertEqual(drawing["segment_count"], 12)
        view = drawing["views"][0]
        self.assertEqual(view["dimensions"], {"width": 10.0, "height": 20.0, "unit": "mm"})
        self.assertEqual(len([line for line in view["lines"] if line["kind"] == "silhouette"]), 8)
        hatch_lines = [line for line in view["lines"] if line["kind"] == "hatch"]
        self.assertEqual(len(hatch_lines), 4)
        for line in hatch_lines:
            source = hatching["lines"][line["hatch_segment_index"]]
            self.assertEqual(line["start"], source["start"])
            self.assertEqual(line["end"], source["end"])
        self.assert_only_primitive_reads()

    def test_omission_keeps_old_png_bytes_and_does_not_call_hatching_or_add_evidence(self):
        with patch.object(tools_module, "build_section_hatching") as build, \
                patch.object(tools_module, "normalize_section_hatch") as normalize:
            response = self.call(include_svg=True, include_dxf=True)
        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertNotIn("hatching", result)
        self.assertFalse(result["drawing"]["hatching_applied"])
        self.assertEqual(result["drawing"]["segment_count"], 8)
        self.assertEqual(hashlib.sha256(self.png(response)).hexdigest(), self.OLD_BOX_PNG_SHA256)
        normalize.assert_not_called()
        build.assert_not_called()
        self.assert_only_primitive_reads()

    def test_spacing_only_uses_declared_angle_phase_and_nonzero_defaults(self):
        response = self.call(hatch={"spacing_mm": 5})
        self.assertFalse(response.get("isError", False), response)
        options = response["structuredContent"]["result"]["hatching"]["options"]
        self.assertEqual(options, {"spacing_mm": 5.0, "angle_degrees": 45.0, "phase_mm": 0.0, "fill_rule": "nonzero"})
        self.assert_only_primitive_reads()

    def test_only_requested_plain_svg_is_built_and_paper_svg_uses_paper_renderer(self):
        for options in ({}, {"sheet": {"paper_size": "A4"}, "include_svg": True}):
            with self.subTest(options=options):
                self.primitive_geometry.reset_mock()
                with patch.object(tools_module, "_make_svg", side_effect=AssertionError("unused plain SVG")) as plain_svg:
                    response = self.call(hatch=self.HATCH, **options)
                self.assertFalse(response.get("isError", False), response)
                drawing = response["structuredContent"]["result"]["drawing"]
                self.assertTrue(self.png(response).startswith(b"\x89PNG\r\n\x1a\n"))
                if options.get("include_svg"):
                    self.assertIn("sheet", drawing)
                    self.assertTrue(drawing["svg"].startswith("<svg"))
                    self.assertIn('data-role="hatch"', drawing["svg"])
                else:
                    self.assertNotIn("svg", drawing)
                plain_svg.assert_not_called()
                self.assert_only_primitive_reads()
        self.primitive_geometry.reset_mock()
        with patch.object(tools_module, "_make_svg", wraps=tools_module._make_svg) as plain_svg:
            response = self.call(hatch=self.HATCH, include_svg=True)
        self.assertFalse(response.get("isError", False), response)
        self.assertIn('class="hatch"', response["structuredContent"]["result"]["drawing"]["svg"])
        plain_svg.assert_called_once()
        self.assert_only_primitive_reads()

    def test_missed_section_returns_successful_empty_hatching_without_drawing_or_image(self):
        response = self.call(plane={"origin": [0, 0, 100], "normal": [0, 0, 1]}, hatch=self.HATCH,
                             include_svg=True, include_dxf=True, sheet={})
        self.assertFalse(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertTrue(all(result[key] is True for key in ("success", "available", "complete")))
        self.assertFalse(result["section"]["intersection"])
        self.assertTrue(result["hatching"]["success"])
        self.assertEqual(result["hatching"]["lines"], [])
        self.assertIsNone(result["drawing"])
        self.assertFalse(any(row["type"] == "image" for row in response["content"]))
        self.assert_only_primitive_reads()

    def test_kernel_failure_preserves_successful_section_and_rejects_partial_fallback(self):
        failure = {"success": False, "available": False, "complete": True, "lines": [],
                   "error_code": "section_hatch_scanline_limit_exceeded", "diagnostics": [{"limit": 20000}]}
        with patch.object(tools_module, "build_section_hatching", return_value=failure) as build:
            response = self.call(hatch=self.HATCH, include_svg=True, include_dxf=True)
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["error_code"], "section_hatching_failed")
        self.assertTrue(result["section"]["success"])
        self.assertTrue(result["section"]["intersection"])
        self.assertEqual(result["hatching"], failure)
        self.assertEqual(result["resolved_refno"], "=1/2")
        self.assertGreater(result["source_mesh"]["counts"]["vertices"], 0)
        self.assertIsNone(result["drawing"])
        self.assertFalse(any(row["type"] == "image" for row in response["content"]))
        build.assert_called_once()
        self.assert_only_primitive_reads()

    def test_unexpected_hatching_exception_retains_section_and_returns_no_fallback(self):
        with patch.object(tools_module, "build_section_hatching", side_effect=RuntimeError("local hatch fault")):
            response = self.call(hatch=self.HATCH)
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["error_code"], "section_hatching_failed")
        self.assertEqual(result["hatching"]["lines"], [])
        self.assertEqual(result["hatching"]["error_type"], "RuntimeError")
        self.assertTrue(result["section"]["success"])
        self.assertIsNone(result["drawing"])
        self.assertFalse(any(row["type"] == "image" for row in response["content"]))
        self.assert_only_primitive_reads()

    def test_export_failure_retains_complete_hatching_and_section_evidence(self):
        response = self.call(hatch=self.HATCH, sheet={"paper_size": "A4", "scale_denominator": .001},
                             include_svg=True, include_dxf=True)
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["error_code"], "drawing_export_failed")
        self.assertTrue(result["section"]["success"])
        self.assertTrue(result["hatching"]["success"])
        self.assertEqual(len(result["hatching"]["lines"]), 4)
        self.assertIsNone(result["drawing"])
        self.assertFalse(any(row["type"] == "image" for row in response["content"]))
        self.assert_only_primitive_reads()

    def test_valid_extremely_dense_spacing_returns_bounded_failure_without_partial_image(self):
        response = self.call(hatch={"spacing_mm": 1e-6, "angle_degrees": 0})
        self.assertTrue(response.get("isError", False), response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["error_code"], "section_hatching_failed")
        self.assertEqual(result["hatching"]["error_code"], "section_hatch_scanline_limit_exceeded")
        self.assertEqual(result["hatching"]["lines"], [])
        self.assertFalse(result["hatching"]["complete"])
        self.assertTrue(result["section"]["success"])
        self.assertIsNone(result["drawing"])
        self.assertFalse(any(row["type"] == "image" for row in response["content"]))
        self.assert_only_primitive_reads()

    def test_native_panel_narrow_notch_keeps_exact_hatch_but_rejects_quantized_gap_closure(self):
        # The narrow neck opens into a wide lower cavity. No contour segment
        # is itself microscopic: this isolates topology lost between distinct
        # edges, which checking individual zero-length exports cannot detect.
        half_gap = 1e-7
        points = [[0, 0], [10, 0], [10, 10], [5 + half_gap, 10], [5 + half_gap, 3],
                  [6, 2], [4, 2], [5 - half_gap, 3], [5 - half_gap, 10], [0, 10]]
        geometry = panel_fixtures._native(points=points)
        self.panel_geometry.return_value = {"ok": True, "version": "E3D21", "result": geometry}
        for export_options in (
                {"include_dxf": True},
                {"sheet": {"paper_size": "A4", "orientation": "landscape", "scale_denominator": 2}},
                {"include_svg": True, "include_dxf": True,
                 "sheet": {"paper_size": "A4", "orientation": "landscape", "scale_denominator": 2}}):
            with self.subTest(exports=export_options):
                self.panel_geometry.reset_mock()
                response = self.call(expected_type="PANE", coordinate_space="local",
                                     plane={"origin": [0, 0, 1], "normal": [0, 0, 1]},
                                     hatch=self.HATCH, **export_options)
                self.assertTrue(response.get("isError", False), response)
                result = response["structuredContent"]["result"]
                self.assertEqual(result["error_code"], "drawing_export_failed")
                self.assertEqual(result["export_error"], "section_hatch_output_contour_topology_changed")
                self.assertEqual(result["resolved_refno"], "=1/2")
                self.assertEqual(result["resolved_type"], "PANE")
                self.assertTrue(result["source"]["success"])
                self.assertGreater(result["source_mesh"]["counts"]["vertices"], 0)
                section, hatching = result["section"], result["hatching"]
                self.assertTrue(all(section[key] is True for key in ("success", "available", "complete")))
                self.assertEqual(section["counts"]["loops"], 1)
                self.assertTrue(all(hatching[key] is True for key in ("success", "available", "complete")))
                scan = [line for line in hatching["lines"] if line["start"][1] == 5]
                self.assertEqual(len(scan), 2)
                scan.sort(key=lambda line: line["start"][0])
                exact_gap = scan[1]["start"][0] - scan[0]["end"][0]
                self.assertGreater(exact_gap, 0)
                self.assertAlmostEqual(exact_gap, 2 * half_gap, places=14)
                self.assertEqual(format(scan[0]["end"][0], ".6f"), format(scan[1]["start"][0], ".6f"))
                # Each supplied section edge remains long; only the separation
                # of two different walls disappears at serialized precision.
                for segment in section["segments"]:
                    a, b = [section["vertices"][index] for index in segment["vertices"]]
                    self.assertGreater(math.dist(a, b), 0.1)
                self.assertIsNone(result["drawing"])
                self.assertFalse(any(row["type"] == "image" for row in response["content"]))
                self.panel_geometry.assert_called_once_with("=1/2", include_world=False)
                self.primitive_geometry.assert_not_called()
                for name in ("health", "project", "capture_view", "update_primitive", "update_panel", "command", "_request"):
                    getattr(self, name).assert_not_called()

    def test_native_failure_is_unchanged_and_never_calls_hatching_or_more_native_endpoints(self):
        failure = {"ok": False, "version": "E3D21", "error_code": "native_database_fault",
                   "native_diagnostics": {"id": "original-native-read"}, "error": "Read failed"}
        self.primitive_geometry.return_value = failure
        with patch.object(tools_module, "build_section_hatching") as build:
            response = self.call(hatch=self.HATCH)
        self.assertTrue(response.get("isError", False), response)
        self.assertEqual(response["structuredContent"], failure)
        build.assert_not_called()
        self.assert_only_primitive_reads()

    def test_svg_dxf_and_png_show_the_same_four_horizontal_scans_plain_and_on_paper(self):
        for sheet in (None, {"paper_size": "A4", "orientation": "landscape", "scale_denominator": 2}):
            with self.subTest(sheet=sheet):
                self.primitive_geometry.reset_mock()
                options = {"sheet": sheet} if sheet is not None else {}
                response = self.call(hatch=self.HATCH, include_svg=True, include_dxf=True, **options)
                self.assertFalse(response.get("isError", False), response)
                drawing = response["structuredContent"]["result"]["drawing"]
                svg = ET.fromstring(drawing["svg"])
                paths = list(svg.iter("{http://www.w3.org/2000/svg}path"))
                role = lambda path: path.attrib.get("class", path.attrib.get("data-role"))
                hatch_paths = [path for path in paths if role(path) == "hatch"]
                silhouette = [path for path in paths if role(path) == "silhouette"]
                self.assertEqual(len(hatch_paths), 4)
                self.assertEqual(len(silhouette), 8)
                def endpoints(path):
                    return tuple(map(float, re.findall(r"[-+]?(?:\d*\.)?\d+(?:[eE][-+]?\d+)?", path.attrib["d"])))
                bounds = [endpoints(path) for path in silhouette]
                xmin = min(point[axis] for point in bounds for axis in (0, 2))
                xmax = max(point[axis] for point in bounds for axis in (0, 2))
                ymin = min(point[axis] for point in bounds for axis in (1, 3))
                ymax = max(point[axis] for point in bounds for axis in (1, 3))
                scanned_y = []
                for path in hatch_paths:
                    x1, y1, x2, y2 = endpoints(path)
                    self.assertAlmostEqual(x1, xmin, places=3)
                    self.assertAlmostEqual(x2, xmax, places=3)
                    self.assertAlmostEqual(y1, y2, places=3)
                    scanned_y.append(y1)
                for actual, expected in zip(sorted(scanned_y), sorted(ymax - (ymax - ymin) * k / 4 for k in range(4))):
                    self.assertAlmostEqual(actual, expected, places=3)
                entities = dxf_fixtures.OrthographicDxfTests.entities(drawing["dxf"])
                outlines = [row for row in entities if row["type"] == "LINE" and row.get("8") == ["SECTION_OUTLINE"]]
                hatches = [row for row in entities if row["type"] == "LINE" and row.get("8") == ["SECTION_HATCH"]]
                self.assertEqual(len(outlines), 8)
                self.assertEqual(len(hatches), 4)
                xmin = min(float(row[key][0]) for row in outlines for key in ("10", "11"))
                xmax = max(float(row[key][0]) for row in outlines for key in ("10", "11"))
                ymin = min(float(row[key][0]) for row in outlines for key in ("20", "21"))
                ymax = max(float(row[key][0]) for row in outlines for key in ("20", "21"))
                actual_y = []
                for row in hatches:
                    self.assertAlmostEqual(float(row["10"][0]), xmin, places=6)
                    self.assertAlmostEqual(float(row["11"][0]), xmax, places=6)
                    self.assertAlmostEqual(float(row["20"][0]), float(row["21"][0]), places=6)
                    actual_y.append(float(row["20"][0]))
                self.assertEqual(sorted(actual_y), [ymin + (ymax - ymin) * k / 4 for k in range(4)])
                pixels = self.png_rgb(self.png(response))
                visible_rows = [(y, sum(pixel == (176, 176, 176) for pixel in row)) for y, row in enumerate(pixels)
                                if (176, 176, 176) in row]
                # The lower-boundary scan is covered by the original outline;
                # the three interior scans remain visibly separate in PNG.
                self.assertEqual(len(visible_rows), 3)
                self.assertTrue(all(count > 1 for _, count in visible_rows))
                gaps = [right[0] - left[0] for left, right in zip(visible_rows, visible_rows[1:])]
                self.assertTrue(all(gap > 1 for gap in gaps))
                self.assertLessEqual(max(gaps) - min(gaps), 1)
                self.assert_only_primitive_reads()


if __name__ == "__main__":
    unittest.main()

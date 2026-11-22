"""Fixed paper placement, truthful window notes and source-aware tile scenes."""

from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.drawing_sheet_render import render_drawing_sheet_svg
from aveva_e3d_mcp.material_assembly_drawing import combine_material_drawing_meshes, generate_material_assembly_drawing
from aveva_e3d_mcp.material_drawing_tiles import plan_and_clip_material_tiles
from aveva_e3d_mcp.material_tile_sheet import build_material_tile_sheet
from aveva_e3d_mcp.orthographic_dxf import export_drawing_sheet_dxf
from test_material_assembly_drawing import parameter_source


FOOTER = "PARAMETER ASSEMBLY - NATIVE CUT SCOPE UNVERIFIED"


def geometry(scene):
    return [stroke for stroke in scene["strokes"] if "page_line_index" in stroke]


def dxf_lines(document):
    rows = document.splitlines()
    records, current, in_entities = [], None, False
    for code, value in zip(rows[0::2], rows[1::2]):
        if code == "2" and value == "ENTITIES":
            in_entities = True
        elif in_entities and code == "0":
            if current is not None:
                records.append(current)
            if value == "ENDSEC":
                break
            current = {} if value == "LINE" else None
        elif in_entities and current is not None:
            current[code] = value
    return records


class MaterialTileSheetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = parameter_source(dimensions={"XLEN": 450, "YLEN": 550, "ZLEN": 30})
        cls.combined = combine_material_drawing_meshes([source])
        if not cls.combined.get("success"):
            raise AssertionError(cls.combined)
        cls.drawing = generate_material_assembly_drawing(cls.combined, views=("TOP",),
                         projection_convention="right_handed", show_hidden_lines=True)
        if not cls.drawing.get("success"):
            raise AssertionError(cls.drawing)
        cls.options = {"paper_size": "A4", "orientation": "portrait", "scale_denominator": 1,
                       "title": "TILED MATERIAL", "drawing_number": "M-001"}
        cls.manifest = plan_and_clip_material_tiles(cls.drawing, cls.options)

    def scene(self, page, drawing=None, manifest=None, options=None):
        return build_material_tile_sheet(self.drawing if drawing is None else drawing,
            self.manifest if manifest is None else manifest, page, self.options if options is None else options)

    def test_all_pages_keep_one_absolute_projection_to_paper_transform(self):
        self.assertEqual((self.manifest["rows"], self.manifest["columns"]), (3, 3))
        for page in self.manifest["pages"]:
            with self.subTest(page=page["id"]):
                scene = self.scene(page)
                placed = scene["views"][0]
                self.assertEqual(placed["paper_bounds_mm"], {"min": [25, 67], "max": [185, 270]})
                self.assertEqual(placed["transform"]["paper_origin_mm"], [25, 67])
                self.assertEqual(placed["transform"]["model_min"], page["bounds"]["min"])
                self.assertEqual(placed["transform"]["scale_denominator"], 1)
                self.assertEqual(len(geometry(scene)), len(page["lines"]))
                for stroke in geometry(scene):
                    line = page["lines"][stroke["page_line_index"]]
                    for endpoint in ("start", "end"):
                        expected = [origin + line[endpoint][axis] - page["bounds"]["min"][axis]
                                    for axis, origin in enumerate((25, 67))]
                        for actual, value in zip(stroke[endpoint], expected):
                            self.assertAlmostEqual(actual, value, delta=1e-12)

    def test_adjacent_overlap_uses_the_same_global_line_at_fixed_paper_step(self):
        pages = {(page["row_index"], page["column_index"]): page for page in self.manifest["pages"]}
        for first_key, second_key, axis, sign in (((0, 0), (0, 1), 0, 1), ((0, 0), (1, 0), 1, -1)):
            first, second = pages[first_key], pages[second_key]
            scenes = (self.scene(first), self.scene(second))
            maps = [{stroke["global_line_index"]: stroke for stroke in geometry(scene)} for scene in scenes]
            shared = set(maps[0]) & set(maps[1])
            self.assertTrue(shared)
            index = next(iter(shared))
            one, two = maps[0][index], maps[1][index]
            low = max(one["source_interval"][0], two["source_interval"][0])
            high = min(one["source_interval"][1], two["source_interval"][1])
            self.assertLess(low, high)
            source = self.drawing["views"][0]["lines"][index]
            parameter = (low + high) / 2
            point = [a + (b - a) * parameter for a, b in zip(source["start"], source["end"])]
            projected = [[origin + (point[dim] - page["bounds"]["min"][dim]) / self.manifest["scale_denominator"]
                          for dim, origin in enumerate((25, 67))] for page in (first, second)]
            paper_step = self.manifest["viewport_mm"][("width", "height")[axis]] - self.manifest["overlap_mm"]
            self.assertAlmostEqual(projected[0][axis] - projected[1][axis], sign * paper_step, delta=1e-12)
            self.assertAlmostEqual(projected[0][1 - axis], projected[1][1 - axis], delta=1e-12)

    def test_visible_notes_identify_crop_coordinates_and_neighbors_without_model_dimensions(self):
        page = self.manifest["pages"][0]
        scene = self.scene(page)
        texts = [item["text"] for item in scene["texts"]]
        self.assertFalse(any(item["role"] == "dimension" for item in (*scene["strokes"], *scene["texts"])))
        self.assertNotIn("model_dimensions_mm", scene["views"][0])
        self.assertNotIn("dimension_label_resolution_mm", scene)
        self.assertTrue(any(text.startswith("PROJECTION WINDOW U MIN ") and " MAX " in text for text in texts))
        self.assertTrue(any(text.startswith("PROJECTION WINDOW V MIN ") and " MAX " in text for text in texts))
        self.assertIn("TOP / PAGE 1/9 / ROW 1 COL 1", texts)
        self.assertIn(FOOTER + " / NOT NATIVE DRAFT", texts)
        self.assertIn("SCALE 1:1", texts)
        neighbor = next(text for text in texts if text.startswith("NEIGHBORS "))
        for side, identifier in page["neighbors"].items():
            label = "BOT" if side == "bottom" else side.upper()
            self.assertIn(label + " " + (identifier or "NONE"), neighbor)
        self.assertEqual(scene["tile_page"]["neighbors"], page["neighbors"])
        self.assertIn("not WORLD XYZ", scene["tile_page"]["coordinate_scope"])
        self.assertIn("not overall model dimensions", scene["views"][0]["bounds_scope"])

    def test_source_clip_metadata_is_preserved_without_repeating_shared_proofs(self):
        page = self.manifest["pages"][0]
        scene = self.scene(page)
        for stroke in geometry(scene):
            line = page["lines"][stroke["page_line_index"]]
            self.assertEqual(stroke["source_line_index"], stroke["page_line_index"])
            for key in ("global_line_index", "source_interval", "source_index", "source_refno", "source_geometry_mode", "edge"):
                self.assertEqual(stroke[key], line[key])
            self.assertEqual(stroke["tile_clip"]["start_clipped"], line["source_interval"][0] != 0)
            self.assertEqual(stroke["tile_clip"]["end_clipped"], line["source_interval"][1] != 1)
            self.assertFalse(stroke["tile_clip"]["creates_model_edges"])
        self.assertEqual(scene["geometry_sha256"], self.drawing["geometry_sha256"])
        self.assertEqual(scene["global_linework_sha256"], self.manifest["global_linework_sha256"])
        self.assertEqual(scene["shared_source_proof"]["source_proof_sha256"], self.manifest["source_proof_sha256"])
        for key in ("source_ranges", "validation", "material_boundary_validation", "source_proof", "engine"):
            self.assertNotIn(key, scene)
        self.assertFalse(scene["source_attribution"]["vector_source_metadata_embedded"])
        self.assertFalse(scene["native_cut_application_verified"])
        svg = render_drawing_sheet_svg(scene, coordinate_decimal_places=15)
        dxf = export_drawing_sheet_dxf(scene, coordinate_decimal_places=15)
        for private in ("source_refno", "global_line_index", self.drawing["source_ranges"][0]["refno"]):
            self.assertNotIn(private, svg)
            self.assertNotIn(private, dxf)

    def test_blank_tile_still_renders_window_notes_and_neighbor_pages(self):
        page = next(page for page in self.manifest["pages"] if not page["lines"])
        scene = self.scene(page)
        self.assertTrue(scene["tile_empty"])
        self.assertFalse(scene["empty"])
        self.assertTrue(scene["complete"])
        self.assertEqual(geometry(scene), [])
        self.assertEqual(sum(stroke.get("paper_guide") == "projection tile window" for stroke in scene["strokes"]), 4)
        root = ET.fromstring(render_drawing_sheet_svg(scene, coordinate_decimal_places=15))
        text = " ".join(item.text or "" for item in root.findall("{http://www.w3.org/2000/svg}text"))
        self.assertIn("NO GLOBAL LINEWORK IN THIS TILE", text)
        for identifier in page["neighbors"].values():
            if identifier:
                self.assertIn(identifier, text)
        self.assertEqual(len(dxf_lines(export_drawing_sheet_dxf(scene, coordinate_decimal_places=15))), 12)

    def test_every_view_and_paper_orientation_keeps_fixed_window_and_bounded_title_suffix(self):
        for name in ("FRONT", "TOP", "SIDE"):
            drawing = generate_material_assembly_drawing(self.combined, views=(name,), projection_convention="right_handed")
            for size in ("A3", "A4"):
                for orientation in ("portrait", "landscape"):
                    options = {"paper_size": size, "orientation": orientation, "scale_denominator": 50,
                               "title": "A" * 120, "drawing_number": "D" * 40}
                    manifest = plan_and_clip_material_tiles(drawing, options)
                    with self.subTest(view=name, paper=size, orientation=orientation):
                        page = manifest["pages"][0]
                        scene = self.scene(page, drawing, manifest, options)
                        self.assertEqual(scene["views"][0]["transform"]["paper_origin_mm"], [25, 67])
                        self.assertTrue(scene["options"]["title"].endswith(page["id"]))
                        self.assertTrue(scene["options"]["drawing_number"].endswith(page["id"]))
                        self.assertLessEqual(len(scene["options"]["title"]), 120)
                        self.assertLessEqual(len(scene["options"]["drawing_number"]), 40)
                        self.assertEqual(scene["views"][0]["horizontal_axis"], manifest["horizontal_axis"])
                        render_drawing_sheet_svg(scene, coordinate_decimal_places=15)

    def test_larger_notes_fit_all_papers_with_long_coordinates_and_four_neighbors(self):
        source = parameter_source(dimensions={"XLEN": 550, "YLEN": 550, "ZLEN": 550},
            position=(999998000.1234567, -999998000.7654321, 999998000.2345679))
        combined = combine_material_drawing_meshes([source])
        self.assertTrue(combined["success"], combined.get("error_code"))
        four_neighbors_seen = False

        def box(item):
            width = (len(item["text"]) * 6 - 1) / 7 * item["height"]
            dx, dy = (item["height"], width) if item["rotation"] else (width, item["height"])
            x, y = item["position"]
            return (x - dx / 2, y - dy / 2, x + dx / 2, y + dy / 2)

        for name in ("FRONT", "TOP", "SIDE"):
            drawing = generate_material_assembly_drawing(combined, views=(name,), projection_convention="right_handed")
            for size in ("A3", "A4"):
                for orientation in ("portrait", "landscape"):
                    options = {"paper_size": size, "orientation": orientation, "scale_denominator": 1}
                    manifest = plan_and_clip_material_tiles(drawing, options)
                    for page in manifest["pages"]:
                        with self.subTest(view=name, size=size, orientation=orientation, page=page["id"]):
                            scene = self.scene(page, drawing, manifest, options)
                            notes = [item for item in scene["texts"] if item["role"] in ("view_label", "disclaimer")
                                     or item["text"].startswith(("NEIGHBORS ", "PROJECTION WINDOW "))]
                            self.assertTrue(notes)
                            for item in notes:
                                self.assertGreaterEqual(item["height"], 2.5)
                                self.assertLessEqual(item["height"], 3)
                            heading = next(item for item in notes if item["role"] == "view_label")
                            self.assertEqual(heading["height"], 3)
                            boxes = [box(item) for item in scene["texts"]]
                            width, height = scene["paper_size_mm"]
                            for index, current in enumerate(boxes):
                                self.assertGreaterEqual(current[0], 10)
                                self.assertGreaterEqual(current[1], 10)
                                self.assertLessEqual(current[2], width - 10)
                                self.assertLessEqual(current[3], height - 10)
                                for other in boxes[index + 1:]:
                                    self.assertFalse(min(current[2], other[2]) > max(current[0], other[0])
                                                     and min(current[3], other[3]) > max(current[1], other[1]))
                            four_neighbors_seen |= all(page["neighbors"].values())
        self.assertTrue(four_neighbors_seen)

    def _with_tiny_hidden_line(self, *, collapse=False):
        drawing = deepcopy(self.drawing)
        view = drawing["views"][0]
        original = next(line for line in view["lines"] if line["start"][0] != line["end"][0])
        line = deepcopy(original)
        a, b = line["start"][0], line["end"][0]
        endpoint = math.nextafter(a, b) if collapse else a + math.copysign(5.7e-10, b - a)
        line.update(kind="feature", visibility="hidden", end=[endpoint, line["start"][1]])
        self.assertNotEqual(line["start"], line["end"])
        view["lines"].append(line)
        drawing["segment_count"] += 1
        return drawing, len(view["lines"]) - 1

    def test_fifteen_places_preserve_scaled_tiny_hidden_fragment_in_svg_and_dxf(self):
        drawing, global_index = self._with_tiny_hidden_line()
        options = {**self.options, "scale_denominator": 50}
        manifest = plan_and_clip_material_tiles(drawing, options)
        page = manifest["pages"][0]
        scene = self.scene(page, drawing, manifest, options)
        line = next(stroke for stroke in geometry(scene) if stroke["global_line_index"] == global_index)
        self.assertEqual(line["visibility"], "hidden")
        self.assertNotEqual(line["start"], line["end"])
        self.assertAlmostEqual(math.dist(line["start"], line["end"]),
                               math.dist(drawing["views"][0]["lines"][-1]["start"], drawing["views"][0]["lines"][-1]["end"]) / 50,
                               delta=2e-14)
        root = ET.fromstring(render_drawing_sheet_svg(scene, coordinate_decimal_places=15))
        paths = [item for item in root.findall("{http://www.w3.org/2000/svg}path") if item.attrib.get("stroke-dasharray")]
        self.assertTrue(any(path.attrib["d"].split()[1:3] != path.attrib["d"].split()[4:6] for path in paths))
        records = dxf_lines(export_drawing_sheet_dxf(scene, coordinate_decimal_places=15))
        expected = [format(value, ".15f") for value in (*line["start"], *line["end"])]
        self.assertTrue(any([record[key] for key in ("10", "20", "11", "21")] == expected for record in records))

    def test_real_float_collapse_fails_without_dropping_the_source_fragment(self):
        drawing, index = self._with_tiny_hidden_line(collapse=True)
        options = {**self.options, "scale_denominator": 50}
        manifest = plan_and_clip_material_tiles(drawing, options)
        self.assertTrue(any(line["global_line_index"] == index for page in manifest["pages"] for line in page["lines"]))
        with self.assertRaisesRegex(ValueError, "geometry_below_precision"):
            self.scene(manifest["pages"][0], drawing, manifest, options)

    def test_malformed_mapping_window_neighbors_coverage_or_options_fail_closed(self):
        for fault in ("source_index", "source_refno", "edge", "global_index", "interval", "endpoint",
                      "neighbors", "span", "coverage", "scale", "paper", "projection_method"):
            manifest = deepcopy(self.manifest)
            page, options = manifest["pages"][0], dict(self.options)
            if fault == "source_index": page["lines"][0]["source_index"] = True
            elif fault == "source_refno": page["lines"][0]["source_refno"] = "=9/999"
            elif fault == "edge": page["lines"][0]["edge"][0] = 999999
            elif fault == "global_index": page["lines"][0]["global_line_index"] = True
            elif fault == "interval": page["lines"][0]["source_interval"] = [1, 0]
            elif fault == "endpoint": page["lines"][0]["start"][0] += 1
            elif fault == "neighbors": page["neighbors"]["right"] = None
            elif fault == "span": page["bounds"]["max"][0] += 1
            elif fault == "coverage": manifest["coverage"]["returned_source_interval_union_verified"] = False
            elif fault == "scale": options["scale_denominator"] = 2
            elif fault == "paper": options["paper_size"] = "A3"
            else: options["projection_method"] = "third_angle"
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                self.scene(page, manifest=manifest, options=options)

    def test_inputs_and_shared_evidence_are_not_mutated_or_aliased(self):
        drawing, manifest, options = deepcopy(self.drawing), deepcopy(self.manifest), dict(self.options)
        before = deepcopy((drawing, manifest, options))
        scene = self.scene(manifest["pages"][0], drawing, manifest, options)
        geometry(scene)[0]["edge"][0] = -1
        geometry(scene)[0]["source_interval"][0] = -1
        scene["tile_page"]["global_projection_bounds"]["min"][0] = -999
        scene["tile_page"]["neighbors"]["right"] = "CHANGED"
        self.assertEqual((drawing, manifest, options), before)


if __name__ == "__main__":
    unittest.main()

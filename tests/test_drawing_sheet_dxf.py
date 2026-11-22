"""Independent printable-sheet DXF interchange checks; optional ezdxf only."""

from __future__ import annotations

import io
import hashlib
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    import ezdxf
except ImportError:
    ezdxf = None

from aveva_e3d_mcp.drawing_sheet import build_drawing_sheet
from aveva_e3d_mcp.orthographic_dxf import export_drawing_sheet_dxf


def sheet_drawing(*, hidden: bool = False) -> dict:
    # Translated coordinates exercise both model-origin removal and the paper
    # transform. The model is always 10 x 30 mm, regardless of the sheet scale.
    corners = ((11, 22), (21, 22), (21, 52), (11, 52))
    lines = [
        {"kind": "silhouette", "start": list(point), "end": list(corners[(index + 1) % 4])}
        for index, point in enumerate(corners)
    ]
    lines.append({"kind": "feature", "start": [11, 22], "end": [21, 52]})
    if hidden:
        lines.append({"kind": "feature", "start": [11, 37], "end": [21, 37], "visibility": "hidden"})
    return {
        "success": True, "complete": True, "coordinate_unit": "mm",
        "coordinate_frame": "test model mm", "show_hidden_lines": hidden,
        "views": [{
            "name": "FRONT", "bounds": {"min": [11, 22], "max": [21, 52]},
            "dimensions": {"width": 10, "height": 30, "unit": "mm"}, "lines": lines,
        }],
    }


def tiny_paper_scene():
    return {
        "success": True, "complete": True, "coordinate_space": "paper", "coordinate_unit": "mm",
        "model_coordinate_unit": "mm", "scale_denominator": 50,
        "paper": {"size": "A4", "orientation": "landscape", "width_mm": 297, "height_mm": 210},
        "strokes": [{"start": [60.0 + index * 10, 60.0],
                     "end": [60.0 + index * 10 + model_length / 50, 60.0],
                     "role": "silhouette", "visibility": "hidden", "view_name": "FRONT"}
                    for index, model_length in enumerate((5.7e-10, 1.2e-9, 2.8e-9, 4.5e-9))],
        "texts": [],
    }


class DrawingSheetDxfPrecisionTests(unittest.TestCase):
    @staticmethod
    def lines(source):
        rows = source.splitlines()
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
                current.setdefault(code, []).append(value)
        return records

    @staticmethod
    def ordinary_scene():
        return build_drawing_sheet(sheet_drawing(hidden=True), {"paper_size": "A4", "orientation": "landscape",
            "scale_denominator": 2, "title": "PUMP_SHEET", "drawing_number": "P-001"})

    def test_default_sheet_dxf_preserves_existing_bytes(self):
        sample = self.ordinary_scene()
        default = export_drawing_sheet_dxf(sample)
        self.assertEqual(hashlib.sha256(default.encode("ascii")).hexdigest(),
                         "65091faffcfe2cd133cf1b0d3b6321b37b0b4841fbfe6622d7f15a1e9d84b0e8")
        self.assertEqual(default, export_drawing_sheet_dxf(sample, coordinate_decimal_places=6))

    def test_fifteen_places_preserve_all_scaled_tiny_hidden_dxf_intervals(self):
        sample = tiny_paper_scene()
        result = export_drawing_sheet_dxf(sample, coordinate_decimal_places=15)
        lines = self.lines(result)
        self.assertEqual(len(lines), 4)
        for line, original in zip(lines, sample["strokes"]):
            self.assertEqual(line["8"], ["FRONT_HIDDEN"])
            start = tuple(float(line[code][0]) for code in ("10", "20", "30"))
            end = tuple(float(line[code][0]) for code in ("11", "21", "31"))
            self.assertNotEqual(start, end)
            self.assertGreater(end[0] - start[0], 0)
            self.assertAlmostEqual(end[0] - start[0], original["end"][0] - original["start"][0], delta=1e-14)
            for code in ("10", "20", "30", "11", "21", "31"):
                self.assertEqual(len(line[code][0].split(".")[1]), 15)

    def test_default_dxf_still_rejects_tiny_lines_and_precision_does_not_leak(self):
        sample = tiny_paper_scene()
        baseline = export_drawing_sheet_dxf(self.ordinary_scene())
        sample["serialization_precision"] = {"coordinate_decimal_places": 15}
        with self.assertRaisesRegex(ValueError, "sheet_geometry_below_dxf_precision"):
            export_drawing_sheet_dxf(sample)
        export_drawing_sheet_dxf(sample, coordinate_decimal_places=15)
        with self.assertRaisesRegex(ValueError, "sheet_geometry_below_dxf_precision"):
            export_drawing_sheet_dxf(sample)
        self.assertEqual(export_drawing_sheet_dxf(self.ordinary_scene()), baseline)

    def test_sheet_dxf_precision_requires_strict_integer_from_six_to_fifteen(self):
        for value in (True, False, None, "15", 6.0, 15.0, 5, 16, [], {}, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "invalid_coordinate_decimal_places"):
                export_drawing_sheet_dxf(tiny_paper_scene(), coordinate_decimal_places=value)

    def test_sheet_dxf_rejects_strokes_that_cannot_be_preserved_at_selected_precision(self):
        sample = tiny_paper_scene()
        sample["strokes"] = [{**sample["strokes"][0], "start": [0, 0], "end": [2e-16, 0]}]
        with self.assertRaisesRegex(ValueError, "sheet_geometry_below_dxf_precision"):
            export_drawing_sheet_dxf(sample, coordinate_decimal_places=15)

    def test_higher_dxf_precision_retains_existing_section_hatch_guard(self):
        sample = tiny_paper_scene()
        sample["strokes"] = [{**sample["strokes"][0], "role": "hatch", "view_name": "SECTION", "visibility": "visible"}]
        with self.assertRaisesRegex(ValueError, "section_hatch_output_geometry_below_precision"):
            export_drawing_sheet_dxf(sample, coordinate_decimal_places=15)


@unittest.skipIf(ezdxf is None, "optional ezdxf parser is not installed")
class DrawingSheetDxfInteropTests(unittest.TestCase):
    def read_export(self, *, hidden: bool = False, **options):
        options = {"paper_size": "A4", "orientation": "landscape", "scale_denominator": 2,
                   "title": "PUMP_SHEET", "drawing_number": "P-001", **options}
        scene = build_drawing_sheet(sheet_drawing(hidden=hidden), options)
        # No recovery reader: repairs would mask broken table ownership or
        # incomplete DXF subclass records in the producer.
        document = ezdxf.read(io.StringIO(export_drawing_sheet_dxf(scene)))
        self.assert_clean(document)
        return scene, document

    def assert_clean(self, document):
        audit = document.audit()
        self.assertEqual(audit.errors, [], "reader reported unrecoverable DXF errors")
        self.assertEqual(audit.fixes, [], "reader repaired the exported DXF")

    def assert_point(self, actual, expected):
        self.assertEqual(len(actual), len(expected))
        for left, right in zip(actual, expected):
            self.assertAlmostEqual(left, right, places=6)

    @staticmethod
    def geometry(document):
        rows = []
        for entity in document.modelspace():
            if entity.dxftype() == "LINE":
                rows.append(("LINE", entity.dxf.layer, tuple(entity.dxf.start), tuple(entity.dxf.end)))
            elif entity.dxftype() == "TEXT":
                rows.append(("TEXT", entity.dxf.layer, entity.dxf.text, tuple(entity.dxf.insert),
                             tuple(entity.dxf.align_point), entity.dxf.height, entity.dxf.rotation,
                             entity.dxf.halign, entity.dxf.valign))
        return rows

    def test_normal_reader_preserves_paper_units_page_extents_and_entity_ownership(self):
        for paper_size, orientation, expected in (
                ("A4", "landscape", (297.0, 210.0)), ("A4", "portrait", (210.0, 297.0)),
                ("A3", "landscape", (420.0, 297.0)), ("A3", "portrait", (297.0, 420.0))):
            with self.subTest(paper_size=paper_size, orientation=orientation):
                scene, document = self.read_export(paper_size=paper_size, orientation=orientation)
                self.assertEqual(document.dxfversion, "AC1015")
                self.assertEqual(document.units, 4)
                self.assertEqual(document.header["$INSUNITS"], 4)
                self.assertEqual(document.header["$MEASUREMENT"], 1)
                self.assert_point(tuple(document.header["$INSBASE"]), (0.0, 0.0, 0.0))
                self.assert_point(tuple(document.header["$LIMMIN"]), (0.0, 0.0))
                self.assert_point(tuple(document.header["$LIMMAX"]), expected)
                space = document.modelspace()
                self.assertEqual(len(space), len(scene["strokes"]) + len(scene["texts"]))
                self.assertEqual(len(document.paperspace()), 0)
                for layout in document.layouts:
                    self.assertEqual(layout.dxf.paper_size, paper_size)
                    self.assertEqual(layout.dxf.paper_width, expected[0])
                    self.assertEqual(layout.dxf.paper_height, expected[1])
                    self.assert_point(tuple(layout.dxf.limmax)[:2], expected)
                    self.assertEqual(layout.dxf.plot_paper_units, 1)
                    self.assertEqual(layout.dxf.plot_rotation, 0)
                    self.assertEqual(layout.dxf.plot_type, 2)
                    self.assertEqual(layout.dxf.scale_numerator, 1.0)
                    self.assertEqual(layout.dxf.scale_denominator, 1.0)
                    self.assertEqual((layout.dxf.left_margin, layout.dxf.bottom_margin,
                                      layout.dxf.right_margin, layout.dxf.top_margin), (0.0,) * 4)
                    self.assertEqual(layout.block_record.dxf.layout, layout.dxf_layout.dxf.handle)
                    self.assertEqual(layout.dxf.block_record_handle, layout.block_record_handle)
                self.assertTrue(all(entity.dxf.owner == space.block_record_handle for entity in space))
                handles = [entity.dxf.handle for entity in space]
                self.assertTrue(all(handles))
                self.assertEqual(len(handles), len(set(handles)))
                borders = list(space.query('LINE[layer=="SHEET_BORDER"]'))
                self.assertEqual(len(borders), 4)
                points = [tuple(point) for entity in borders for point in (entity.dxf.start, entity.dxf.end)]
                self.assertEqual(min(point[0] for point in points), 10.0)
                self.assertEqual(min(point[1] for point in points), 10.0)
                self.assertEqual(max(point[0] for point in points), expected[0] - 10.0)
                self.assertEqual(max(point[1] for point in points), expected[1] - 10.0)

    def test_scale_reduces_paper_geometry_but_preserves_true_model_dimension_labels(self):
        for denominator in (0.5, 1, 2, 5):
            with self.subTest(denominator=denominator):
                scene, document = self.read_export(scale_denominator=denominator)
                outlines = list(document.modelspace().query('LINE[layer=="FRONT_OUTLINE"]'))
                lengths = sorted((line.dxf.end - line.dxf.start).magnitude for line in outlines)
                expected = sorted([10 / denominator, 30 / denominator] * 2)
                self.assertEqual(len(lengths), 4)
                for actual, wanted in zip(lengths, expected):
                    self.assertAlmostEqual(actual, wanted, places=6)
                features = list(document.modelspace().query('LINE[layer=="FRONT_FEATURE"]'))
                self.assertEqual(len(features), 1)
                self.assertAlmostEqual((features[0].dxf.end - features[0].dxf.start).magnitude,
                                       math.hypot(10, 30) / denominator, places=6)
                labels = list(document.modelspace().query("TEXT"))
                self.assertIn("10 MM", [label.dxf.text for label in labels])
                self.assertIn("30 MM", [label.dxf.text for label in labels])
                self.assertIn(f"SCALE 1:{denominator:g}", [label.dxf.text for label in labels])
                vertical = next(label for label in labels if label.dxf.text == "30 MM")
                self.assertEqual(vertical.dxf.layer, "FRONT_DIMENSION")
                self.assertEqual(vertical.dxf.rotation, 90.0)
                self.assertEqual((vertical.dxf.halign, vertical.dxf.valign), (1, 2))
                self.assertEqual(scene["views"][0]["model_dimensions_mm"], {"width": 10.0, "height": 30.0})

    def test_all_scene_strokes_text_positions_and_rotations_survive_independent_parse(self):
        scene, document = self.read_export(hidden=True)
        space = document.modelspace()
        actual_lines = list(space.query("LINE"))
        actual_texts = list(space.query("TEXT"))
        self.assertEqual(len(actual_lines), len(scene["strokes"]))
        self.assertEqual(len(actual_texts), len(scene["texts"]))
        for entity, stroke in zip(actual_lines, scene["strokes"]):
            self.assert_point(tuple(entity.dxf.start), (*stroke["start"], 0.0))
            self.assert_point(tuple(entity.dxf.end), (*stroke["end"], 0.0))
        for entity, text in zip(actual_texts, scene["texts"]):
            self.assertEqual(entity.dxf.text, text["text"])
            self.assert_point(tuple(entity.dxf.insert), (*text["position"], 0.0))
            self.assert_point(tuple(entity.dxf.align_point), (*text["position"], 0.0))
            self.assertAlmostEqual(entity.dxf.height, text["height"], places=6)
            self.assertEqual(entity.dxf.rotation, text["rotation"])
            self.assertEqual((entity.dxf.halign, entity.dxf.valign), (1, 2))

    def test_hidden_layer_pattern_is_constant_in_paper_mm_as_scale_changes(self):
        for denominator in (0.5, 2, 5):
            with self.subTest(denominator=denominator):
                scene, document = self.read_export(hidden=True, scale_denominator=denominator)
                hidden = list(document.modelspace().query('LINE[layer=="FRONT_HIDDEN"]'))
                self.assertEqual(len(hidden), 1)
                self.assertAlmostEqual((hidden[0].dxf.end - hidden[0].dxf.start).magnitude,
                                       10 / denominator, places=6)
                self.assertEqual(hidden[0].dxf.linetype, "BYLAYER")
                self.assertEqual(document.layers.get("FRONT_HIDDEN").dxf.linetype, "E3D_HIDDEN")
                self.assertEqual(document.linetypes.get("E3D_HIDDEN").simplified_line_pattern(), (2.0, 1.0))
                self.assertEqual(document.layers.get("FRONT_OUTLINE").dxf.linetype, "CONTINUOUS")
                stroke = next(stroke for stroke in scene["strokes"] if stroke["visibility"] == "hidden")
                self.assert_point(tuple(hidden[0].dxf.start), (*stroke["start"], 0.0))
                self.assert_point(tuple(hidden[0].dxf.end), (*stroke["end"], 0.0))
        _, visible_document = self.read_export()
        self.assertNotIn("FRONT_HIDDEN", visible_document.layers)
        self.assertNotIn("E3D_HIDDEN", visible_document.linetypes)

    def test_normal_file_roundtrip_preserves_paper_and_model_semantics_without_repairs(self):
        for hidden, paper_size, orientation, expected in (
                (hidden, paper, orientation, size)
                for hidden in (False, True)
                for paper, orientation, size in (
                    ("A4", "landscape", (297.0, 210.0)), ("A4", "portrait", (210.0, 297.0)),
                    ("A3", "landscape", (420.0, 297.0)), ("A3", "portrait", (297.0, 420.0)))):
            with self.subTest(hidden=hidden, paper=paper_size, orientation=orientation), \
                    tempfile.TemporaryDirectory(prefix="sheet-dxf-interop-") as directory:
                _, original = self.read_export(hidden=hidden, paper_size=paper_size, orientation=orientation)
                path = Path(directory) / "paper-sheet.dxf"
                original.saveas(path)
                reread = ezdxf.readfile(path)
                self.assert_clean(reread)
                self.assertEqual(reread.dxfversion, "AC1015")
                self.assertEqual(reread.units, 4)
                self.assertEqual(self.geometry(reread), self.geometry(original))
                self.assert_point(tuple(reread.header["$LIMMAX"]), expected)
                self.assertEqual(len(reread.paperspace()), 0)
                for layout in reread.layouts:
                    self.assertEqual(layout.dxf.paper_size, paper_size)
                    self.assertEqual(layout.dxf.paper_width, expected[0])
                    self.assertEqual(layout.dxf.paper_height, expected[1])
                    self.assert_point(tuple(layout.dxf.limmax)[:2], expected)
                    # Geometry is already paper-scaled; CAD plotting must not
                    # apply the model 1:N scale a second time.
                    self.assertEqual(layout.dxf.scale_numerator, 1.0)
                    self.assertEqual(layout.dxf.scale_denominator, 1.0)
                if hidden:
                    self.assertEqual(reread.layers.get("FRONT_HIDDEN").dxf.linetype, "E3D_HIDDEN")
                    self.assertEqual(reread.linetypes.get("E3D_HIDDEN").simplified_line_pattern(), (2.0, 1.0))


if __name__ == "__main__":
    unittest.main()

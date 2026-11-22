import hashlib
import math
import unittest

from aveva_e3d_mcp.orthographic_dxf import export_orthographic_dxf


def drawing(*, visibility=False):
    line = {"kind": "silhouette", "start": [10, 20], "end": [30, 20]}
    if visibility:
        line["visibility"] = "visible"
    hidden = {"kind": "feature", "start": [10, 21], "end": [30, 21], "visibility": "hidden"}
    return {
        "success": True,
        "complete": True,
        "coordinate_unit": "mm",
        "hidden_line_removal": True,
        "views": [
            {"name": "FRONT", "bounds": {"min": [10, 20], "max": [50, 60]},
             "dimensions": {"width": 40, "height": 40, "unit": "mm"},
             "lines": [line, hidden] if visibility else [line]},
            {"name": "TOP", "bounds": {"min": [0, 0], "max": [100, 10]},
             "dimensions": {"width": 100, "height": 10, "unit": "mm"},
             "lines": [{"kind": "feature", "start": [0, 0], "end": [100, 10]}]},
        ],
    }


def micro_interval_drawing():
    lengths = (5.7e-10, 1.2e-9, 2.8e-9, 4.5e-9)
    return {
        "success": True, "complete": True, "coordinate_unit": "mm", "show_hidden_lines": True,
        "views": [{
            "name": "FRONT", "bounds": {"min": [0, 0], "max": [100, 40]},
            "dimensions": {"width": 100, "height": 40, "unit": "mm"},
            "lines": [{"kind": "silhouette", "visibility": "hidden", "start": [10.0 * (index + 1), 20.0],
                       "end": [10.0 * (index + 1) + length, 20.0]} for index, length in enumerate(lengths)],
        }],
    }


class OrthographicDxfTests(unittest.TestCase):
    @staticmethod
    def entities(source):
        rows = source.splitlines()
        pairs = list(zip(rows[0::2], rows[1::2]))
        in_entities = False
        records = []
        current = None
        for code, value in pairs:
            if code == "0" and value == "SECTION":
                continue
            if code == "2" and value == "ENTITIES":
                in_entities = True
                continue
            if not in_entities:
                continue
            if code == "0" and value == "ENDSEC":
                if current is not None:
                    records.append(current)
                break
            if code == "0":
                if current is not None:
                    records.append(current)
                current = {"type": value}
            elif current is not None:
                current.setdefault(code, []).append(value)
        return records

    def test_header_units_layers_entities_and_offsets(self):
        result = export_orthographic_dxf(drawing())
        self.assertIn("9\n$ACADVER\n1\nAC1015", result)
        self.assertIn("9\n$INSUNITS\n70\n4", result)
        self.assertIn("2\nFRONT_OUTLINE", result)
        self.assertIn("2\nTOP_FEATURE", result)
        # Two source lines plus 14 dimension-line/extension/arrow strokes per view.
        self.assertEqual(result.count("\n0\nLINE\n"), 30)
        # The dimension layout gives each view room for labels and extension lines.
        self.assertIn("11\n80.000000\n21\n60.000000", result)
        self.assertIn("11\n340.000000\n21\n70.000000", result)
        self.assertIn("1\nFRONT 40.000000 x 40.000000 mm", result)
        self.assertIn("1\nTOP 100.000000 x 10.000000 mm", result)
        records = self.entities(result)
        dimensions = [entity for entity in records if entity.get("8") == ["FRONT_DIMENSION"]]
        self.assertEqual(sum(entity["type"] == "LINE" for entity in dimensions), 14)
        dimension_labels = [entity["1"][0] for entity in dimensions if entity["type"] == "TEXT"]
        self.assertEqual(dimension_labels, ["40.000000 mm", "40.000000 mm"])
        vertical = [entity for entity in dimensions if entity["type"] == "TEXT" and entity.get("50") == ["90.000000"]]
        self.assertEqual(len(vertical), 1)
        # Drawing view geometry is separated by padding plus the 50 mm layout gap.
        front_points = [(float(entity["10"][0]), float(entity["20"][0]))
                        for entity in records if entity.get("8") == ["FRONT_OUTLINE"]]
        top_points = [(float(entity["10"][0]), float(entity["20"][0]))
                      for entity in records if entity.get("8") == ["TOP_FEATURE"]]
        self.assertLess(max(x for x, _ in front_points), min(x for x, _ in top_points))

    def test_hidden_segments_are_omitted_and_explicit_visibility_works(self):
        result = export_orthographic_dxf(drawing(visibility=True))
        self.assertEqual(result.count("\n0\nLINE\n"), 30)
        self.assertIn("TOP_OUTLINE", result)  # layers are declared for every supplied view

    def test_explicit_hidden_output_uses_separate_dashed_layer(self):
        source = drawing(visibility=True)
        source["show_hidden_lines"] = True
        result = export_orthographic_dxf(source)
        records = self.entities(result)
        hidden = [entity for entity in records if entity.get("8") == ["FRONT_HIDDEN"]]
        self.assertEqual(len(hidden), 1)
        self.assertEqual(hidden[0]["type"], "LINE")
        self.assertIn("2\nE3D_HIDDEN", result)
        self.assertIn("73\n2\n40\n4.5\n49\n3.0\n74\n0\n49\n-1.5", result)
        self.assertEqual(result.count("\n0\nLINE\n"), 31)

    def test_r2000_handles_owners_and_entity_subclasses_are_complete(self):
        result = export_orthographic_dxf(drawing())
        rows = result.splitlines()
        tags = list(zip(rows[::2], rows[1::2]))
        handles = [value for code, value in tags if code == "5"]
        self.assertEqual(len(handles), len(set(handles)))
        self.assertTrue(handles)
        owners = [value for code, value in tags if code == "330"]
        self.assertTrue(all(owner == "0" or owner in handles for owner in owners))
        self.assertIn("2\nBLOCKS", result)
        self.assertIn("2\nOBJECTS", result)
        self.assertIn("2\n*Model_Space", result)
        self.assertIn("2\n*Paper_Space", result)
        entities = self.entities(result)
        self.assertEqual(len({entity["330"][0] for entity in entities}), 1)
        for entity in entities:
            expected = ["AcDbEntity", "AcDbLine"] if entity["type"] == "LINE" else [
                "AcDbEntity", "AcDbText", "AcDbText"]
            self.assertEqual(entity["100"], expected)

    def test_deterministic(self):
        self.assertEqual(export_orthographic_dxf(drawing()), export_orthographic_dxf(drawing()))

    def test_default_precision_preserves_existing_document_bytes(self):
        # Captured before adding optional precision, covering ordinary labels,
        # omitted dimensions and explicitly requested hidden linework.
        cases = (
            ("default", "c855a97ac0973e3f26383061583212e418d2105540f6c62045b68fd0175ba6d8"),
            ("without_dimensions", "df4baf2e4aa9ba3821aad79a688db4206d1e58416275a9f6cca7edc6a57b2dac"),
            ("hidden", "b0df0482a9fc5cc715833542eda4207fb08621c9d072c9f0af97230e90320b7d"),
        )
        for mode, expected in cases:
            with self.subTest(mode=mode):
                source = drawing(visibility=mode == "hidden")
                if mode == "hidden":
                    source["show_hidden_lines"] = True
                options = {"include_dimensions": mode != "without_dimensions"}
                exported = export_orthographic_dxf(source, **options)
                self.assertEqual(hashlib.sha256(exported.encode("ascii")).hexdigest(), expected)
                self.assertEqual(exported, export_orthographic_dxf(source, coordinate_decimal_places=6, **options))

    def test_nine_and_twelve_places_preserve_every_tiny_hidden_interval(self):
        for precision in (9, 12):
            with self.subTest(precision=precision):
                source = micro_interval_drawing()
                result = export_orthographic_dxf(source, coordinate_decimal_places=precision)
                records = self.entities(result)
                hidden = [entity for entity in records if entity.get("8") == ["FRONT_HIDDEN"]]
                self.assertEqual(len(hidden), len(source["views"][0]["lines"]))
                for entity, original in zip(hidden, source["views"][0]["lines"]):
                    self.assertEqual(entity["type"], "LINE")
                    encoded_length = float(entity["11"][0]) - float(entity["10"][0])
                    input_length = original["end"][0] - original["start"][0]
                    self.assertGreater(encoded_length, 0.0)
                    self.assertAlmostEqual(encoded_length, input_length, delta=10.0 ** -precision)
                for entity in records:
                    if entity["type"] == "LINE":
                        start = tuple(float(entity[code][0]) for code in ("10", "20", "30"))
                        end = tuple(float(entity[code][0]) for code in ("11", "21", "31"))
                        self.assertNotEqual(start, end)
                    for code in ("10", "20", "30", "11", "21", "31", "40", "50"):
                        for value in entity.get(code, []):
                            self.assertEqual(len(value.split(".")[1]), precision)

    def test_default_rejects_tiny_hidden_intervals_after_high_precision_export(self):
        baseline = export_orthographic_dxf(drawing())
        with self.assertRaisesRegex(ValueError, "geometry_below_dxf_precision"):
            export_orthographic_dxf(micro_interval_drawing())
        export_orthographic_dxf(micro_interval_drawing(), coordinate_decimal_places=12)
        self.assertEqual(export_orthographic_dxf(drawing()), baseline)
        with self.assertRaisesRegex(ValueError, "geometry_below_dxf_precision"):
            export_orthographic_dxf(micro_interval_drawing())

    def test_layout_float_collapse_still_rejects_at_maximum_precision(self):
        delta = 2e-15
        self.assertNotEqual(format(0.0, ".15f"), format(delta, ".15f"))
        self.assertEqual(60.0 + delta, 60.0, "The source interval must collapse during layout addition.")
        source = {
            "success": True, "complete": True, "coordinate_unit": "mm",
            "views": [{"name": "FRONT", "bounds": {"min": [0, 0], "max": [1, 1]},
                       "dimensions": {"width": 1, "height": 1, "unit": "mm"},
                       "lines": [{"kind": "silhouette", "start": [0, 0], "end": [delta, 0]}]}],
        }
        with self.assertRaisesRegex(ValueError, "geometry_below_dxf_precision"):
            export_orthographic_dxf(source, coordinate_decimal_places=15)

    def test_coordinate_precision_requires_an_integer_between_six_and_fifteen(self):
        for value in (True, False, None, "9", 6.0, 9.0, 5, 16, -1, math.nan, math.inf, [], {}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "invalid_coordinate_decimal_places"):
                export_orthographic_dxf(drawing(), coordinate_decimal_places=value)
        for precision in (6, 15):
            with self.subTest(precision=precision):
                self.assertTrue(export_orthographic_dxf(drawing(), coordinate_decimal_places=precision).endswith("0\nEOF\n"))

    def test_pose_layout_can_omit_unverified_extent_dimensions(self):
        result = export_orthographic_dxf(drawing(), include_dimensions=False)
        self.assertNotIn("_DIMENSION", result)
        self.assertIn("1\nFRONT\n", result)
        self.assertNotIn("x 40.000000 mm", result)

    def test_rejects_invalid_or_unsafe_inputs(self):
        invalid = [
            {"success": False, "complete": True, "views": []},
            {**drawing(), "views": [{**drawing()["views"][0], "bounds": {"min": [0, 0], "max": [1, 1]}}]},
            {**drawing(), "views": [{**drawing()["views"][0], "lines": [{"kind": "silhouette", "start": [math.nan, 0], "end": [1, 1]}]}]},
            {**drawing(), "views": [{**drawing()["views"][0], "lines": [{"kind": "other", "start": [10, 20], "end": [30, 20]}]}]},
        ]
        for candidate in invalid:
            with self.assertRaises(ValueError):
                export_orthographic_dxf(candidate)

    def test_rejects_line_count_limit(self):
        source = drawing()
        source["views"] = [dict(source["views"][0], lines=[
            {"kind": "feature", "start": [10, 20], "end": [30, 20]}
        ] * 20001)]
        with self.assertRaisesRegex(ValueError, "line_limit"):
            export_orthographic_dxf(source)

    def test_converts_units(self):
        source = drawing()
        source["coordinate_unit"] = "cm"
        source["views"] = [{"name": "FRONT", "bounds": {"min": [0, 0], "max": [2, 3]},
                            "dimensions": {"width": 2, "height": 3, "unit": "cm"},
                            "lines": [{"kind": "feature", "start": [0, 0], "end": [2, 3]}]}]
        result = export_orthographic_dxf(source)
        self.assertIn("1\nFRONT 20.000000 x 30.000000 mm", result)
        self.assertIn("11\n80.000000\n21\n90.000000", result)

    def test_zero_width_or_height_omits_only_degenerate_dimension_axis(self):
        cases = [
            ("vertical", [5, 10], [5, 30], [
                {"kind": "silhouette", "start": [5, 10], "end": [5, 30]}
            ], ["20.000000 mm"]),
            ("horizontal", [5, 10], [25, 10], [
                {"kind": "silhouette", "start": [5, 10], "end": [25, 10]}
            ], ["20.000000 mm"]),
        ]
        for _name, minimum, maximum, lines, expected_labels in cases:
            with self.subTest(minimum=minimum, maximum=maximum):
                width, height = maximum[0] - minimum[0], maximum[1] - minimum[1]
                source = {
                    "success": True,
                    "complete": True,
                    "coordinate_unit": "mm",
                    "views": [{
                        "name": "FRONT",
                        "bounds": {"min": minimum, "max": maximum},
                        "dimensions": {"width": width, "height": height, "unit": "mm"},
                        "lines": lines,
                    }],
                }
                records = self.entities(export_orthographic_dxf(source))
                dimensions = [entity for entity in records if entity.get("8") == ["FRONT_DIMENSION"]]
                labels = [entity["1"][0] for entity in dimensions if entity["type"] == "TEXT"]
                self.assertEqual(labels, expected_labels)
                self.assertEqual(sum(entity["type"] == "LINE" for entity in dimensions), 7)
                for entity in records:
                    if entity["type"] == "LINE":
                        self.assertNotEqual((entity["10"], entity["20"]), (entity["11"], entity["21"]))

    def test_point_projection_has_label_without_zero_dimension_entities(self):
        source = {
            "success": True,
            "complete": True,
            "coordinate_unit": "mm",
            "views": [{
                "name": "FRONT",
                "bounds": {"min": [7, 9], "max": [7, 9]},
                "dimensions": {"width": 0, "height": 0, "unit": "mm"},
                "lines": [],
            }],
        }
        result = export_orthographic_dxf(source)
        records = self.entities(result)
        dimensions = [entity for entity in records if entity.get("8") == ["FRONT_DIMENSION"]]
        self.assertEqual(dimensions, [])
        self.assertIn("1\nFRONT 0.000000 x 0.000000 mm", result)

    def test_rejects_geometry_that_collapses_at_dxf_precision(self):
        source = {
            "success": True,
            "complete": True,
            "coordinate_unit": "mm",
            "views": [{
                "name": "FRONT",
                "bounds": {"min": [0, 0], "max": [0.0000001, 1]},
                "dimensions": {"width": 0.0000001, "height": 1, "unit": "mm"},
                "lines": [{"kind": "feature", "start": [0, 0], "end": [0.0000001, 0]}],
            }],
        }
        with self.assertRaisesRegex(ValueError, "geometry_below_dxf_precision"):
            export_orthographic_dxf(source)


if __name__ == "__main__":
    unittest.main()

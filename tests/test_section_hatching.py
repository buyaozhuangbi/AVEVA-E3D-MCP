from __future__ import annotations

import copy
import json
import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.section_hatching import build_section_hatching, normalize_section_hatch


def section_of(*contours):
    vertices, segments, loops = [], [], []
    for contour in contours:
        first_vertex, first_segment = len(vertices), len(segments)
        vertices.extend([list(point) for point in contour])
        indices = list(range(first_vertex, len(vertices))) + [first_vertex]
        for a, b in zip(indices, indices[1:]):
            segments.append({"vertices": [a, b]})
        loops.append({"vertices": indices, "segment_indices": list(range(first_segment, len(segments)))})
    return {"success": True, "available": True, "complete": True,
            "intersection": bool(contours), "vertices": vertices, "segments": segments, "loops": loops,
            "coordinate_unit": "mm"}


RECTANGLE = [[0, 0], [10, 0], [10, 10], [0, 10]]
HOLE = [[2, 2], [2, 8], [8, 8], [8, 2]]


class HatchOptionsTests(unittest.TestCase):
    def test_explicit_spacing_defaults_and_modulo_phase(self):
        self.assertEqual(normalize_section_hatch({"spacing_mm": 2}),
                         {"spacing_mm": 2.0, "angle_degrees": 45.0, "phase_mm": 0.0, "fill_rule": "nonzero"})
        self.assertEqual(normalize_section_hatch({"spacing_mm": 3, "phase_mm": -1})["phase_mm"], 2)
        self.assertEqual(normalize_section_hatch({"spacing_mm": 3, "phase_mm": 7})["phase_mm"], 1)
        value = {"spacing_mm": .001, "angle_degrees": 179.999, "phase_mm": -23, "fill_rule": "nonzero"}
        before = copy.deepcopy(value)
        normalized = normalize_section_hatch(value)
        self.assertEqual(value, before)
        self.assertEqual(normalize_section_hatch(normalized), normalized)

    def test_closed_schema_bounds_types_and_fill_rule(self):
        values = [None, [], {}, {"spacing_mm": 1, "extra": 0}]
        for field, bad_values in (("spacing_mm", [True, "1", 0, 1e-7, 1e9 + 1, math.inf, math.nan]),
                                  ("angle_degrees", [True, -1, 180, math.inf, "45"]),
                                  ("phase_mm", [True, 1e9 + 1, -1e9 - 1, math.nan, None]),
                                  ("fill_rule", ["evenodd", "NONZERO", 0, None])):
            values.extend({"spacing_mm": 1, field: value} for value in bad_values)
        for options in values:
            with self.subTest(options=options), self.assertRaises(ValueError):
                normalize_section_hatch(options)


class SectionHatchingTests(unittest.TestCase):
    def call(self, section=None, **options):
        return build_section_hatching(section if section is not None else section_of(RECTANGLE),
                                      {"spacing_mm": 5, "angle_degrees": 0, **options})

    def assertFailure(self, result, code=None):
        self.assertFalse(result["success"], result)
        self.assertFalse(result["available"])
        self.assertEqual(result["lines"], [])
        self.assertEqual(result["counts"]["hatch_lines"], 0)
        if code:
            self.assertEqual(result["error_code"], code)

    def intervals(self, result, y):
        return [(line["start"][0], line["end"][0]) for line in result["lines"] if line["start"][1] == y]

    def test_rectangle_uses_half_open_boundaries_and_source_provenance(self):
        result = self.call()
        self.assertTrue(result["success"], result)
        self.assertEqual([(line["start"], line["end"], line["scanline_index"]) for line in result["lines"]],
                         [([0.0, 0.0], [10.0, 0.0], 0), ([0.0, 5.0], [10.0, 5.0], 1)])
        self.assertEqual(result["lines"][0]["boundary_source_segments"], {"start": [3], "end": [1]})
        self.assertEqual(result["counts"]["total_lines"], 6)
        self.assertEqual(result["coordinate_unit"], "mm")
        self.assertTrue(result["geometric_self_intersection_checked"])
        self.assertFalse(result["hole_semantics_inferred"])

    def test_reversed_single_contour_fills_identically(self):
        normal = self.call()
        reversed_result = self.call(section_of(RECTANGLE[::-1]))
        self.assertTrue(reversed_result["success"], reversed_result)
        self.assertEqual([(line["start"], line["end"]) for line in normal["lines"]],
                         [(line["start"], line["end"]) for line in reversed_result["lines"]])
        self.assertEqual(reversed_result["loop_winding"], [-1])

    def test_concave_contour_splits_lines_without_bridging_empty_notch(self):
        contour = [[0, 0], [10, 0], [10, 10], [7, 10], [7, 3], [3, 3], [3, 10], [0, 10]]
        result = self.call(section_of(contour))
        self.assertTrue(result["success"], result)
        self.assertEqual(self.intervals(result, 0), [(0.0, 10.0)])
        self.assertEqual(self.intervals(result, 5), [(0.0, 3.0), (7.0, 10.0)])

    def test_opposite_inner_winding_leaves_hole_same_direction_does_not(self):
        hole = self.call(section_of(RECTANGLE, HOLE))
        nested = self.call(section_of(RECTANGLE, HOLE[::-1]))
        self.assertTrue(hole["success"], hole)
        self.assertTrue(nested["success"], nested)
        self.assertEqual(self.intervals(hole, 5), [(0.0, 2.0), (8.0, 10.0)])
        self.assertEqual(self.intervals(nested, 5), [(0.0, 10.0)])
        self.assertEqual(hole["loop_winding"], [1, -1])
        self.assertEqual(nested["loop_winding"], [1, 1])

    def test_island_and_disconnected_contours_follow_nonzero_fill(self):
        island = [[4, 4], [6, 4], [6, 6], [4, 6]]
        separate = [[20, 0], [24, 0], [24, 10], [20, 10]]
        result = self.call(section_of(RECTANGLE, HOLE, island, separate))
        self.assertTrue(result["success"], result)
        self.assertEqual(self.intervals(result, 5), [(0.0, 2.0), (4.0, 6.0), (8.0, 10.0), (20.0, 24.0)])

    def test_scanline_vertex_crossings_and_collinear_subdivisions(self):
        triangle = [[0, 0], [10, 5], [0, 10]]
        result = self.call(section_of(triangle))
        self.assertTrue(result["success"], result)
        self.assertEqual(self.intervals(result, 0), [])
        self.assertEqual(self.intervals(result, 5), [(0.0, 10.0)])
        subdivided = [[0, 0], [5, 0], [10, 0], [10, 10], [0, 10]]
        split = self.call(section_of(subdivided))
        self.assertTrue(split["success"], split)
        self.assertEqual([(line["start"], line["end"]) for line in split["lines"]],
                         [(line["start"], line["end"]) for line in self.call()["lines"]])

    def test_horizontal_vertical_rotated_angles_and_phase(self):
        vertical = self.call(angle_degrees=90)
        self.assertTrue(vertical["success"], vertical)
        self.assertEqual([(line["start"], line["end"]) for line in vertical["lines"]],
                         [([10.0, 0.0], [10.0, 10.0]), ([5.0, 0.0], [5.0, 10.0])])
        diagonal = self.call(angle_degrees=45, spacing_mm=4, phase_mm=1)
        self.assertTrue(diagonal["success"], diagonal)
        for line in diagonal["lines"]:
            self.assertAlmostEqual(line["end"][0] - line["start"][0], line["end"][1] - line["start"][1])
            self.assertTrue(all(-1e-12 <= value <= 10 + 1e-12 for point in (line["start"], line["end"]) for value in point))
        phase = self.call(phase_mm=-1)
        self.assertTrue(phase["success"], phase)
        self.assertEqual([line["start"][1] for line in phase["lines"]], [4.0, 9.0])

    def test_translation_with_matching_phase_preserves_lines(self):
        before = self.call(spacing_mm=3, phase_mm=1)
        moved = [[x + 20, y + 7] for x, y in RECTANGLE]
        after = self.call(section_of(moved), spacing_mm=3, phase_mm=8)
        self.assertTrue(after["success"], after)
        for first, second in zip(before["lines"], after["lines"]):
            self.assertEqual([[x + 20, y + 7] for x, y in (first["start"], first["end"])], [second["start"], second["end"]])

    def test_exact_thin_hole_is_not_tolerance_merged(self):
        hole = [[5, 2], [5, 8], [5 + 1e-12, 8], [5 + 1e-12, 2]]
        result = self.call(section_of(RECTANGLE, hole))
        self.assertTrue(result["success"], result)
        self.assertEqual(self.intervals(result, 5), [(0.0, 5.0), (5 + 1e-12, 10.0)])

    def test_touching_crossing_overlapping_and_self_crossing_contours_fail(self):
        contours = [
            (RECTANGLE, [[10, 3], [12, 3], [12, 7], [10, 7]]),
            (RECTANGLE, [[5, 5], [12, 5], [12, 12], [5, 12]]),
            (RECTANGLE, RECTANGLE),
            ([[0, 0], [10, 10], [0, 10], [10, 0]],),
            ([[0, 0], [10, 0], [5, 0], [5, 10], [0, 10]],),
        ]
        for contours in contours:
            with self.subTest(contours=contours):
                self.assertFailure(self.call(section_of(*contours)))

    def test_bad_indices_incomplete_and_mismatched_loops_are_rejected(self):
        mutations = [lambda s: s["loops"][0]["vertices"].pop(),
                     lambda s: s["loops"][0]["vertices"].__setitem__(1, True),
                     lambda s: s["loops"][0]["segment_indices"].__setitem__(1, 0),
                     lambda s: s["segments"][0]["vertices"].reverse(),
                     lambda s: s["segments"].append({"vertices": [0, 2]}),
                     lambda s: s["vertices"].append([100, 100]),
                     lambda s: s["vertices"][0].__setitem__(0, math.nan),
                     lambda s: s["vertices"][0].__setitem__(0, 1e9 + 1),
                     lambda s: s.__setitem__("complete", False),
                     lambda s: s.__setitem__("intersection", False)]
        for mutation in mutations:
            section = section_of(RECTANGLE)
            mutation(section)
            with self.subTest(section=section):
                self.assertFailure(self.call(section))

    def test_empty_section_succeeds_without_lines_or_work(self):
        result = self.call(section_of())
        self.assertTrue(result["success"], result)
        self.assertEqual(result["lines"], [])
        self.assertEqual(result["counts"]["edge_scanline_checks"], 0)

    def test_units_must_be_explicit_model_millimetres_including_empty_sections(self):
        for value in (None, "cm", "caller_supplied; not inferred", "MM", True):
            for section in (section_of(), section_of(RECTANGLE)):
                if value is None:
                    section.pop("coordinate_unit")
                else:
                    section["coordinate_unit"] = value
                with self.subTest(value=value, intersection=section["intersection"]):
                    self.assertFailure(self.call(section), "section_hatch_coordinate_unit_mm_required")

    def test_limits_and_work_budgets_fail_without_partial_lines(self):
        cases = [("MAX_SOURCE_SEGMENTS", 3, "section_hatch_source_limit_exceeded"),
                 ("MAX_TOTAL_LINES", 5, "section_hatch_total_line_limit_exceeded"),
                 ("MAX_SCANLINES", 1, "section_hatch_scanline_limit_exceeded"),
                 ("MAX_TOPOLOGY_PAIR_CHECKS", 0, "section_hatch_topology_work_budget_exceeded"),
                 ("MAX_EDGE_SCANLINE_CHECKS", 5, "section_hatch_scan_work_budget_exceeded")]
        for name, limit, error in cases:
            with self.subTest(name=name), patch("aveva_e3d_mcp.section_hatching." + name, limit):
                result = self.call()
                self.assertFailure(result, error)
                self.assertFalse(result["complete"])

    def test_scanline_count_is_bounded_before_iteration(self):
        self.assertFailure(self.call(spacing_mm=1e-6), "section_hatch_scanline_limit_exceeded")

    def test_result_is_deterministic_json_safe_and_inputs_unchanged(self):
        section = section_of(RECTANGLE, HOLE)
        options = {"spacing_mm": .75, "angle_degrees": 31, "phase_mm": -.5}
        before = copy.deepcopy((section, options))
        first = build_section_hatching(section, options)
        second = build_section_hatching(section, options)
        self.assertTrue(first["success"], first)
        self.assertEqual(json.dumps(first, allow_nan=False), json.dumps(second, allow_nan=False))
        self.assertEqual((section, options), before)


if __name__ == "__main__":
    unittest.main()

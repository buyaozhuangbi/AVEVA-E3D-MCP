from __future__ import annotations

import copy
import math
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.mesh_section import slice_triangle_mesh
from aveva_e3d_mcp.section_dimensions import (
    normalize_section_dimensions, resolve_section_dimensions, section_dimension_revision,
)


IDENTITY = {"resolved_refno": "=1/2", "source_revision": "mesh-observation-1"}


def section_of(points=None):
    points = points if points is not None else [[0, 0], [10, 3], [10, 20], [0, 20]]
    indices = list(range(len(points))) + [0]
    return {"success": True, "available": True, "complete": True, "coordinate_unit": "mm", "intersection": True,
            "plane": {"origin": [0, 0, 0], "normal": [0, 0, 1], "right": [1, 0, 0], "up": [0, 1, 0]},
            "vertices": copy.deepcopy(points),
            "segments": [{"vertices": [a, b], "source_triangles": [index]} for index, (a, b) in enumerate(zip(indices, indices[1:]))],
            "loops": [{"vertices": indices, "segment_indices": list(range(len(points)))}]}


def bound_section(points=None):
    section = section_of(points)
    section["dimension_revision"] = section_dimension_revision(section, IDENTITY)
    return section


def options(section, *items):
    return {"expected_section_revision": section["dimension_revision"],
            "items": list(items) or [{"first_vertex": 0, "second_vertex": 1, "axis": "horizontal"}]}


class SectionDimensionOptionsTests(unittest.TestCase):
    def test_defaults_uppercase_revision_and_input_is_not_mutated(self):
        request = {"expected_section_revision": "ABCDEF01" * 8,
                   "items": [{"first_vertex": 0, "second_vertex": 1, "axis": "horizontal"},
                             {"first_vertex": 0, "second_vertex": 2, "axis": "vertical"}]}
        before = copy.deepcopy(request)
        result = normalize_section_dimensions(request)
        self.assertEqual(result["expected_section_revision"], ("abcdef01" * 8))
        self.assertEqual([item["side"] for item in result["items"]], ["top", "right"])
        self.assertEqual(request, before)
        result["items"][0]["first_vertex"] = 99
        self.assertEqual(request, before)

    def test_closed_schemas_strict_types_bounds_and_one_to_eight_items(self):
        section = bound_section()
        valid = options(section)
        bad = [None, [], {}, {**valid, "extra": True}, {**valid, "items": []}, {**valid, "items": valid["items"] * 9},
               {**valid, "expected_section_revision": "a" * 63}, {**valid, "expected_section_revision": "g" * 64},
               {**valid, "expected_section_revision": 1}, {**valid, "expected_section_revision": "a" * 64 + "\n"}]
        base = valid["items"][0]
        for field, values in (("first_vertex", [True, -1, 0.0, "0", None]),
                              ("second_vertex", [False, -1, 1.0]),
                              ("axis", ["x", None, True, []]),
                              ("side", ["left", "right", None, []])):
            bad.extend({**valid, "items": [{**base, field: value}]} for value in values)
        bad.extend({**valid, "items": [item]} for item in (None, {}, {**base, "extra": 1},
                                                          {"first_vertex": 0, "second_vertex": 1}))
        for request in bad:
            with self.subTest(request=request), self.assertRaises(ValueError):
                normalize_section_dimensions(request)

    def test_reverse_duplicate_is_rejected_only_for_same_axis_and_side(self):
        section = bound_section()
        first = {"first_vertex": 0, "second_vertex": 1, "axis": "horizontal"}
        reverse = {"first_vertex": 1, "second_vertex": 0, "axis": "horizontal", "side": "top"}
        with self.assertRaisesRegex(ValueError, "duplicate_section_dimension"):
            normalize_section_dimensions(options(section, first, reverse))
        normalized = normalize_section_dimensions(options(section, first, {**reverse, "side": "bottom"},
                                                          {**reverse, "axis": "vertical", "side": "left"}))
        self.assertEqual(len(normalized["items"]), 3)
        with self.assertRaisesRegex(ValueError, "identical_vertices"):
            normalize_section_dimensions(options(section, {**first, "second_vertex": 0}))


class SectionDimensionRevisionTests(unittest.TestCase):
    def test_revision_is_stable_for_metadata_order_numeric_types_and_negative_zero(self):
        section = section_of()
        original = copy.deepcopy(section)
        digest = section_dimension_revision(section, IDENTITY)
        self.assertRegex(digest, re.compile(r"^[a-f0-9]{64}$"))
        changed = copy.deepcopy(section)
        changed.update(dimension_revision="ignored", diagnostics=[{"duration": 99}], elapsed_ms=99,
                       source_sections=[{"extra": "metadata"}])
        changed["vertices"] = [[float(value) for value in point] for point in changed["vertices"]]
        changed["vertices"][0][0] = -0.0
        changed["plane"]["up_source"] = "ignored"
        changed["loops"][0]["signed_area"] = 999
        changed["segments"][0]["source_index"] = 0
        identity = dict(reversed(list(IDENTITY.items())))
        self.assertEqual(section_dimension_revision(changed, identity), digest)
        self.assertEqual(section, original)

    def test_revision_changes_for_plane_vertex_source_triangle_loop_and_source_identity(self):
        section = section_of()
        original = section_dimension_revision(section, IDENTITY)
        for mutation in (lambda s: s["plane"]["origin"].__setitem__(0, 1),
                         lambda s: s["vertices"][1].__setitem__(0, 11),
                         lambda s: s["segments"][0].__setitem__("source_triangles", [999])):
            changed = copy.deepcopy(section)
            mutation(changed)
            self.assertNotEqual(section_dimension_revision(changed, IDENTITY), original)
        rotated = copy.deepcopy(section)
        rotated["loops"][0]["vertices"] = [1, 2, 3, 0, 1]
        rotated["loops"][0]["segment_indices"] = [1, 2, 3, 0]
        self.assertNotEqual(section_dimension_revision(rotated, IDENTITY), original)
        for identity in ({**IDENTITY, "resolved_refno": "=1/3"}, {**IDENTITY, "source_revision": "mesh-observation-2"}):
            self.assertNotEqual(section_dimension_revision(section, identity), original)

    def test_invalid_section_identity_and_coordinates_are_rejected(self):
        section = section_of()
        for identity in (None, {}, [], "ref", {"ref": math.nan}, {1: "bad"}, {"object": object()}):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                section_dimension_revision(section, identity)
        for mutation in (lambda s: s.__setitem__("complete", False),
                         lambda s: s.__setitem__("coordinate_unit", "cm"),
                         lambda s: s["vertices"][0].__setitem__(0, 1e9 + 1),
                         lambda s: s["plane"]["origin"].__setitem__(0, math.inf),
                         lambda s: s["segments"][0].__setitem__("source_triangles", [True]),
                         lambda s: s["segments"][0]["vertices"].reverse(),
                         lambda s: s["loops"][0]["vertices"].pop()):
            changed = copy.deepcopy(section)
            mutation(changed)
            with self.assertRaises(ValueError):
                section_dimension_revision(changed, IDENTITY)

    def test_empty_complete_section_can_be_revision_bound(self):
        section = section_of()
        section.update(intersection=False, vertices=[], segments=[], loops=[])
        revision = section_dimension_revision(section, IDENTITY)
        self.assertEqual(len(revision), 64)


class ResolveSectionDimensionsTests(unittest.TestCase):
    def test_resolves_axis_distance_in_model_plane_mm_with_requested_sides(self):
        section = bound_section()
        request = options(section, {"first_vertex": 0, "second_vertex": 1, "axis": "horizontal", "side": "bottom"},
                          {"first_vertex": 0, "second_vertex": 1, "axis": "vertical", "side": "left"})
        before = copy.deepcopy((section, request))
        result = resolve_section_dimensions(section, request)
        self.assertEqual([item["value_mm"] for item in result], [10.0, 3.0])
        self.assertEqual(result[0], {"first_vertex": 0, "second_vertex": 1, "axis": "horizontal", "side": "bottom",
                                     "start": [0.0, 0.0], "end": [10.0, 3.0], "value_mm": 10.0})
        result[0]["start"][0] = 999
        self.assertEqual((section, request), before)

    def test_missing_mismatched_revision_and_out_of_bounds_indices_fail_clearly(self):
        section = bound_section()
        request = options(section)
        for value, error in ((None, "revision_unavailable"), ("a" * 64, "revision_mismatch")):
            changed = copy.deepcopy(section)
            changed["dimension_revision"] = value
            with self.assertRaisesRegex(ValueError, error):
                resolve_section_dimensions(changed, request)
        for index in (4, 10 ** 100):
            with self.assertRaisesRegex(ValueError, "vertex_out_of_bounds"):
                resolve_section_dimensions(section, options(section, {"first_vertex": 0, "second_vertex": index, "axis": "horizontal"}))

    def test_zero_axis_positive_value_rounded_to_zero_and_large_value_are_rejected(self):
        section = bound_section()
        with self.assertRaisesRegex(ValueError, "zero_axis_distance"):
            resolve_section_dimensions(section, options(section, {"first_vertex": 1, "second_vertex": 2, "axis": "horizontal"}))
        tiny = bound_section([[0, 0], [1e-8, 1], [2, 3], [0, 4]])
        with self.assertRaisesRegex(ValueError, "below_label_precision"):
            resolve_section_dimensions(tiny, options(tiny))
        large = bound_section([[-1e9, 0], [1e9, 1], [1e9, 3], [-1e9, 4]])
        with self.assertRaisesRegex(ValueError, "value_out_of_bounds"):
            resolve_section_dimensions(large, options(large))

    def test_actual_oblique_slice_uses_returned_two_dimensional_axis_values(self):
        vertices = [[-1, -2, -3], [1, -2, -3], [1, 2, -3], [-1, 2, -3],
                    [-1, -2, 3], [1, -2, 3], [1, 2, 3], [-1, 2, 3]]
        triangles = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
                     [3, 7, 6], [3, 6, 2], [0, 4, 7], [0, 7, 3], [1, 2, 6], [1, 6, 5]]
        section = slice_triangle_mesh(vertices, triangles, plane={"origin": [0, 0, 0], "normal": [1, 1, 0]})
        section["coordinate_unit"] = "mm"
        section["dimension_revision"] = section_dimension_revision(section, IDENTITY)
        low = min(range(len(section["vertices"])), key=lambda i: section["vertices"][i][0])
        high = max(range(len(section["vertices"])), key=lambda i: section["vertices"][i][0])
        result = resolve_section_dimensions(section, options(section, {"first_vertex": low, "second_vertex": high, "axis": "horizontal"}))
        self.assertAlmostEqual(result[0]["value_mm"], 2 * math.sqrt(2))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.panel_topology import analyze_panel_loop_relations


def loop(*points: tuple[float, ...], **metadata: object) -> dict[str, object]:
    xyz = [list(point) if len(point) == 3 else [point[0], point[1], 0.0] for point in points]
    return {
        "complete": True,
        "vertices": [{"position": point} for point in xyz],
        "planar_parallel_xy": True,
        "coplanar_in_local_xy": True,
        "polygon_valid": True,
        **metadata,
    }


def analyze(*loops: dict[str, object], complete: bool = True) -> dict[str, object]:
    return analyze_panel_loop_relations({"complete": complete, "loops": list(loops)})


class PanelTopologyTests(unittest.TestCase):
    def test_pairwise_relations_cover_containment_disjoint_crossing_and_contact(self) -> None:
        outer = loop((0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0))
        inner = loop((2, 2, 0), (4, 2, 0), (4, 4, 0), (2, 4, 0))
        outside = loop((12, 1, 0), (14, 1, 0), (14, 3, 0), (12, 3, 0))
        crossing = loop((8, 2, 0), (12, 2, 0), (12, 5, 0), (8, 5, 0))
        contact = loop((10, 2, 0), (12, 2, 0), (12, 4, 0), (10, 4, 0))

        result = analyze(outer, inner, outside, crossing, contact)

        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["complete"])
        self.assertEqual(result["pair_count"], 10)
        self.assertEqual(result["known_pair_count"], 10)
        self.assertEqual(result["unknown_pair_count"], 0)
        relations = {
            (pair["first_loop_index"], pair["second_loop_index"]): pair["relation"]
            for pair in result["pairs"]
        }
        self.assertEqual(relations[(0, 1)], "contains")
        self.assertEqual(relations[(0, 2)], "disjoint")
        self.assertEqual(relations[(0, 3)], "boundary_crossing")
        self.assertEqual(relations[(0, 4)], "boundary_contact")
        self.assertTrue(all(pair["complete"] for pair in result["pairs"]))

    def test_coincident_loops_are_boundary_contact_without_inferred_roles(self) -> None:
        first = loop((0, 0), (6, 0), (6, 6), (0, 6))
        same_boundary_reversed = loop((0, 6), (6, 6), (6, 0), (0, 0))
        result = analyze(first, same_boundary_reversed)
        self.assertEqual(result["pairs"][0]["relation"], "boundary_contact")
        self.assertNotIn("loop_roles", result)

    def test_native_false_polygon_validity_is_an_invalid_loop_not_a_missing_read(self) -> None:
        invalid = loop((0, 0), (4, 0), (4, 4), (0, 4), polygon_valid=False)
        valid = loop((8, 0), (10, 0), (10, 2), (8, 2))
        result = analyze(invalid, valid)
        self.assertEqual(result["pairs"][0]["relation"], "unknown")
        self.assertEqual(result["pairs"][0]["reason"], "invalid_polygon")

    def test_concave_ring_and_reverse_containment_are_geometric_only(self) -> None:
        concave = loop((0, 0), (8, 0), (8, 2), (2, 2), (2, 8), (0, 8))
        inside_arm = loop((0.5, 4), (1.5, 4), (1.5, 6), (0.5, 6))
        enclosing = loop((-1, -1), (9, -1), (9, 9), (-1, 9))

        result = analyze(concave, inside_arm, enclosing)

        relations = [pair["relation"] for pair in result["pairs"]]
        self.assertEqual(relations, ["contains", "contained_by", "contained_by"])
        self.assertTrue(any("outer, hole" in item.lower() for item in result["limitations"]))

    def test_invalid_or_incomplete_loop_makes_only_affected_pairs_unknown(self) -> None:
        good = loop((0, 0), (5, 0), (5, 5), (0, 5))
        incomplete = loop((1, 1), (2, 1), (2, 2), (1, 2), complete=False)
        unsupported = loop((8, 1), (9, 1), (9, 2), (8, 2), unsupported_features=[{"code": "fillet_radius"}])
        disjoint = loop((20, 0), (21, 0), (21, 1), (20, 1))

        result = analyze(good, incomplete, unsupported, disjoint)
        pairs = {(row["first_loop_index"], row["second_loop_index"]): row for row in result["pairs"]}

        self.assertEqual(pairs[(0, 1)]["relation"], "unknown")
        self.assertFalse(pairs[(0, 1)]["complete"])
        self.assertEqual(pairs[(0, 1)]["reason"], "loop_incomplete")
        self.assertEqual(pairs[(0, 3)]["relation"], "disjoint")
        self.assertEqual(pairs[(1, 2)]["reason"], "loop_incomplete")
        self.assertEqual(result["unknown_pair_count"], 5)
        self.assertEqual(result["known_pair_count"], 1)
        self.assertFalse(result["complete"])
        self.assertEqual(result["status"], "partial")

    def test_read_errors_polygon_validity_and_native_incompleteness_are_preserved(self) -> None:
        with_read_error = loop((0, 0), (2, 0), (2, 2), read_errors={"POS": "failed"})
        with_unknown_polygon = loop((4, 0), (6, 0), (6, 2), polygon_valid=None)
        normal = loop((8, 0), (10, 0), (10, 2))

        result = analyze(with_read_error, with_unknown_polygon, normal, complete=False)

        self.assertEqual([row["relation"] for row in result["pairs"]], ["unknown", "unknown", "unknown"])
        self.assertEqual(result["unknown_pair_count"], 3)
        self.assertFalse(result["complete"])
        issue_codes = {issue["code"] for issue in result["issues"]}
        self.assertIn("native_geometry_incomplete", issue_codes)
        self.assertEqual(result["pairs"][0]["reason"], "loop_read_errors")
        self.assertEqual(result["pairs"][1]["reason"], "loop_read_errors")
        self.assertEqual(result["pairs"][2]["reason"], "polygon_validity_unknown")

    def test_self_intersection_duplicate_vertices_non_xy_and_nonfinite_values_fail_closed(self) -> None:
        bowtie = loop((0, 0), (4, 4), (0, 4), (4, 0))
        repeated_close = loop((0, 0), (4, 0), (4, 4), (0, 4), (0, 0))
        offset_plane = loop((0, 0, 1), (4, 0, 1), (4, 4, 1), (0, 4, 1))
        nonfinite = loop((0, 0), (math.inf, 0), (0, 1))
        good = loop((10, 0), (11, 0), (11, 1))

        result = analyze(bowtie, repeated_close, offset_plane, nonfinite, good)

        self.assertTrue(all(row["relation"] == "unknown" for row in result["pairs"]))
        self.assertEqual(result["unknown_pair_count"], 10)
        issue_codes = {(issue["loop_index"], issue["code"]) for issue in result["issues"] if "loop_index" in issue}
        self.assertIn((0, "invalid_polygon"), issue_codes)
        self.assertIn((1, "invalid_polygon"), issue_codes)
        self.assertIn((2, "unsupported_plane"), issue_codes)
        self.assertIn((3, "non_finite_coordinate"), issue_codes)

    def test_boundary_tolerance_and_large_coordinate_translation(self) -> None:
        offset = 1.0e12
        first = loop((offset, -offset), (offset + 100, -offset),
                     (offset + 100, -offset + 100), (offset, -offset + 100))
        separated = loop((offset + 100.001, -offset), (offset + 110, -offset),
                         (offset + 110, -offset + 10), (offset + 100.001, -offset + 10))
        near_touch = loop((100.000005, 0), (110, 0), (110, 10), (100.000005, 10))
        origin = loop((0, 0), (100, 0), (100, 100), (0, 100))

        result = analyze(first, separated, origin, near_touch)
        relations = {(row["first_loop_index"], row["second_loop_index"]): row["relation"]
                     for row in result["pairs"]}

        self.assertEqual(relations[(0, 1)], "disjoint")
        self.assertEqual(relations[(2, 3)], "boundary_contact")
        self.assertEqual(result["tolerance_mm"], 0.00001)

    def test_loop_and_vertex_limits_mark_unexamined_relationships_unknown(self) -> None:
        square = loop((0, 0), (4, 0), (4, 4), (0, 4))
        too_many_loops = analyze(*(square for _ in range(33)))
        self.assertEqual(too_many_loops["pair_count"], 528)
        self.assertEqual(too_many_loops["analyzed_pair_count"], 496)
        self.assertEqual(too_many_loops["known_pair_count"], 496)
        self.assertEqual(too_many_loops["unknown_pair_count"], 32)
        self.assertFalse(too_many_loops["complete"])
        self.assertIn({"code": "loop_limit"}, too_many_loops["issues"])

        repeated = loop(*([(0, 0)] * 2048))
        vertex_limited = analyze(repeated, square)
        self.assertEqual(vertex_limited["pairs"][0]["relation"], "unknown")
        self.assertIn({"loop_index": 1, "code": "vertex_limit"}, vertex_limited["issues"])

    def test_bad_top_level_input_returns_json_safe_summary(self) -> None:
        for value in (None, [], {"loops": None}, {"complete": True, "loops": "bad"}):
            result = analyze_panel_loop_relations(value)  # type: ignore[arg-type]
            self.assertEqual(result["status"], "unavailable")
            self.assertFalse(result["available"])
            json.dumps(result, allow_nan=False)

    def test_missing_required_native_flags_top_level_errors_and_unsupported_data_fail_closed(self) -> None:
        valid = loop((0, 0), (4, 0), (4, 4), (0, 4))
        missing_planarity = {key: value for key, value in valid.items() if key != "planar_parallel_xy"}
        missing_polygon = {key: value for key, value in valid.items() if key != "polygon_valid"}
        top_level_error = {"complete": True, "loops": [valid], "read_errors": {"PANE": "read failed"}}
        top_level_unsupported = {"complete": True, "loops": [valid], "unsupported_features": [{"code": "curved"}]}

        for invalid, reason in ((missing_planarity, "unsupported_plane"),
                                (missing_polygon, "polygon_validity_unknown")):
            result = analyze(invalid, valid)
            self.assertEqual(result["pairs"][0]["relation"], "unknown")
            self.assertEqual(result["pairs"][0]["reason"], reason)
        for geometry, issue in ((top_level_error, "native_geometry_read_errors"),
                                (top_level_unsupported, "native_geometry_unsupported_features")):
            result = analyze_panel_loop_relations(geometry)
            self.assertFalse(result["complete"])
            self.assertIn({"code": issue}, result["issues"])

    def test_positions_require_numeric_xyz_without_coercion(self) -> None:
        valid = loop((0, 0), (4, 0), (4, 4), (0, 4))
        for position in ((0, 0), ("0", 0, 0), (False, 0, 0), (0, math.inf, 0)):
            invalid = loop((1, 1), (2, 1), (2, 2), (1, 2))
            invalid["vertices"][0]["position"] = list(position)
            result = analyze(invalid, valid)
            self.assertEqual(result["pairs"][0]["relation"], "unknown")

    def test_zero_loop_list_is_explicitly_incomplete(self) -> None:
        result = analyze_panel_loop_relations({"complete": True, "loops": []})
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["complete"])
        self.assertEqual(result["pair_count"], 0)
        self.assertIn({"code": "no_loops"}, result["issues"])

    def test_results_are_json_safe_and_do_not_emit_semantic_loop_roles(self) -> None:
        result = analyze(
            loop((0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)),
            loop((2, 2, 0), (4, 2, 0), (4, 4, 0), (2, 4, 0)),
        )
        encoded = json.dumps(result, allow_nan=False)
        self.assertIn('"relation": "contains"', encoded)
        self.assertNotIn('"role"', encoded)
        self.assertNotIn('"hole"', encoded.lower())
        self.assertNotIn('"outer"', encoded.lower())


if __name__ == "__main__":
    unittest.main()

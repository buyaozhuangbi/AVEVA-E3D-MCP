from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.drawing_assembly import WORLD_FRAME, combine_drawing_meshes
from aveva_e3d_mcp.orthographic_drawing import generate_orthographic_drawing


TETRA_POINTS = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]]
TETRA_FACES = [[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]]


def source(refno="=1/2", *, offset=(0, 0, 0), scale=1, panel=False):
    mesh = {
        "success": True, "available": True, "complete": True, "refno": refno,
        "coordinate_frame": WORLD_FRAME, "native_tessellation": False, "cuts_applied": False,
        "vertices": [[point[axis] * scale + offset[axis] for axis in range(3)] for point in TETRA_POINTS],
        "triangles": copy.deepcopy(TETRA_FACES),
    }
    if panel:
        mesh["coordinate_space"] = "WORLD"
    return {
        "success": True, "available": True, "complete": True, "coordinate_space": "WORLD",
        "requested_refno": refno, "resolved_refno": refno, "resolved_type": "PANE" if panel else "BOX",
        "derived_mesh": mesh,
    }


class DrawingAssemblyTests(unittest.TestCase):
    def assert_failure(self, result, code):
        self.assertFalse(result["success"], result)
        self.assertFalse(result["available"], result)
        self.assertEqual(result["error_code"], code)
        self.assertEqual(result["vertices"], [])
        self.assertEqual(result["triangles"], [])
        self.assertEqual(result["source_ranges"], [])

    def test_independent_world_coordinates_and_exact_index_ranges_are_preserved(self):
        first = source()
        second = source("=2/3", offset=(10, -5, 4), scale=2, panel=True)
        merged = combine_drawing_meshes([first, second])
        self.assertTrue(merged["success"], merged)
        self.assertEqual(merged["coordinate_unit"], "mm")
        self.assertEqual(merged["vertices"], [
            [0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1],
            [10, -5, 4], [12, -5, 4], [10, -3, 4], [10, -5, 6],
        ])
        self.assertEqual(merged["triangles"], [
            [0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3],
            [4, 6, 5], [4, 5, 7], [5, 6, 7], [6, 4, 7],
        ])
        self.assertEqual(merged["source_ranges"], [
            {"source_index": 0, "refno": "=1/2", "resolved_type": "BOX", "vertex_range": [0, 4],
             "triangle_range": [0, 4], "vertex_count": 4, "triangle_count": 4, "component_count": 1},
            {"source_index": 1, "refno": "=2/3", "resolved_type": "PANE", "vertex_range": [4, 8],
             "triangle_range": [4, 8], "vertex_count": 4, "triangle_count": 4, "component_count": 1},
        ])
        self.assertEqual(merged["counts"], {"sources": 2, "vertices": 8, "triangles": 8})
        self.assertEqual(merged["validation"]["checks"]["component_count"], 2)

    def test_returned_geometry_and_source_inputs_share_no_mutable_arrays(self):
        original = source()
        before = copy.deepcopy(original)
        merged = combine_drawing_meshes([original])
        self.assertEqual(original, before)
        merged["vertices"][0][0] = 50
        merged["triangles"][0][0] = 3
        self.assertEqual(original, before)

    def test_coincident_and_nested_shells_remain_separate_without_boolean_claims(self):
        for scale, offset in ((1, (0, 0, 0)), (.1, (.1, .1, .1))):
            with self.subTest(scale=scale):
                merged = combine_drawing_meshes([source(), source("=1/3", scale=scale, offset=offset)])
                self.assertTrue(merged["success"], merged)
                self.assertEqual(len(merged["vertices"]), 8)
                self.assertEqual(merged["validation"]["checks"]["component_count"], 2)
                for flag in ("boolean_union_applied", "vertices_welded", "nested_shell_semantics_inferred",
                             "cuts_applied", "native_tessellation"):
                    self.assertFalse(merged[flag])

    def test_combined_output_can_drive_joint_projection_at_known_world_bounds(self):
        merged = combine_drawing_meshes([source(), source("=1/3", offset=(10, 2, 3))])
        drawing = generate_orthographic_drawing(merged["vertices"], merged["triangles"],
                                                coordinate_frame=merged["coordinate_frame"],
                                                coordinate_unit=merged["coordinate_unit"])
        self.assertTrue(drawing["success"], drawing)
        self.assertEqual(drawing["source_bounds"], {"min": [0, 0, 0], "max": [11, 3, 4],
                                                   "dimensions": [11, 3, 4]})
        self.assertEqual(drawing["views"][0]["dimensions"], {"width": 11, "height": 4, "unit": "mm"})

    def test_source_count_is_bounded_and_all_sixteen_valid_sources_are_accepted(self):
        for candidate in (None, [], {}, [source()] * 17):
            with self.subTest(candidate_type=type(candidate).__name__):
                self.assert_failure(combine_drawing_meshes(candidate),
                                    "drawing_sources_must_contain_one_to_sixteen_elements")
        sources = [source(f"=1/{index + 1}", offset=(index * 2, 0, 0)) for index in range(16)]
        self.assertTrue(combine_drawing_meshes(sources)["success"])

    def test_duplicate_aliases_and_mesh_reference_mismatch_fail_without_partial_geometry(self):
        alias = source("=01/0002")
        self.assert_failure(combine_drawing_meshes([source(), alias]), "duplicate_drawing_source")
        mismatched = source("=1/3")
        mismatched["derived_mesh"]["refno"] = "=1/4"
        result = combine_drawing_meshes([source(), mismatched])
        self.assert_failure(result, "drawing_source_mesh_reference_mismatch")
        self.assertEqual(result["source_index"], 1)
        self.assertEqual(result["source_refno"], "=1/3")

    def test_missing_local_or_conflicting_coordinate_metadata_is_not_inferred(self):
        changes = [
            ("source", "coordinate_space", "local", "drawing_source_world_coordinates_required"),
            ("source", "coordinate_space", None, "drawing_source_world_coordinates_required"),
            ("mesh", "coordinate_frame", "WORLD", "drawing_source_mesh_world_millimetres_required"),
            ("mesh", "coordinate_frame", None, "drawing_source_mesh_world_millimetres_required"),
            ("mesh", "coordinate_space", "primitive_local", "drawing_source_mesh_world_millimetres_required"),
            ("mesh", "coordinate_unit", "m", "drawing_source_mesh_world_millimetres_required"),
        ]
        for location, key, value, code in changes:
            with self.subTest(location=location, key=key, value=value):
                candidate = source()
                target = candidate if location == "source" else candidate["derived_mesh"]
                target[key] = value
                self.assert_failure(combine_drawing_meshes([candidate]), code)

    def test_incomplete_source_and_bad_topology_keep_failure_evidence(self):
        incomplete = source()
        incomplete["complete"] = False
        result = combine_drawing_meshes([incomplete])
        self.assert_failure(result, "drawing_source_unavailable_or_incomplete")
        self.assertFalse(result["complete"])
        for bad_faces in (TETRA_FACES[:-1], [[a, c, b] for a, b, c in TETRA_FACES], [[0, 1, 9]]):
            candidate = source("=1/3")
            candidate["derived_mesh"]["triangles"] = bad_faces
            result = combine_drawing_meshes([source(), candidate])
            with self.subTest(bad_faces=bad_faces):
                self.assert_failure(result, "closed_valid_outward_source_mesh_required")
                self.assertEqual(result["source_index"], 1)
                self.assertFalse(result["validation"]["valid"])

    def test_unknown_mesh_flags_and_invalid_source_objects_fail_closed(self):
        for flag, value in (("cuts_applied", True), ("native_tessellation", None)):
            candidate = source()
            candidate["derived_mesh"][flag] = value
            self.assert_failure(combine_drawing_meshes([candidate]), "drawing_source_parameter_mesh_required")
        self.assert_failure(combine_drawing_meshes([None]), "invalid_drawing_source")
        invalid_type = source()
        invalid_type["resolved_type"] = []
        self.assert_failure(combine_drawing_meshes([invalid_type]), "unsupported_drawing_source_type")

    def test_aggregate_limits_are_checked_before_validation_and_exact_limits_are_allowed(self):
        sources = [source(), source("=1/3", offset=(2, 0, 0))]
        for variable in ("MAX_VERTICES", "MAX_TRIANGLES"):
            with self.subTest(variable=variable), \
                    patch(f"aveva_e3d_mcp.drawing_assembly.{variable}", 7), \
                    patch("aveva_e3d_mcp.drawing_assembly.validate_triangle_mesh") as validate:
                result = combine_drawing_meshes(sources)
                self.assert_failure(result, "drawing_assembly_mesh_limit_exceeded")
                self.assertFalse(result["complete"])
                validate.assert_not_called()
        with patch("aveva_e3d_mcp.drawing_assembly.MAX_VERTICES", 8), \
                patch("aveva_e3d_mcp.drawing_assembly.MAX_TRIANGLES", 8):
            self.assertTrue(combine_drawing_meshes(sources)["success"])

    def test_scale_sensitive_combined_validation_does_not_promote_individually_valid_sources(self):
        result = combine_drawing_meshes([source(), source("=1/3", offset=(1e9, 0, 0))])
        self.assert_failure(result, "combined_mesh_not_suitable_for_drawing")
        self.assertTrue(result["complete"])
        self.assertFalse(result["validation"]["valid"])


if __name__ == "__main__":
    unittest.main()

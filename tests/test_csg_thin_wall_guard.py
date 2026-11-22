"""Narrow admission and geometric regression for thin CYLI through-holes."""

from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aveva_e3d_mcp import primitive_csg_mesh as csg_mesh
from test_primitive_csg_mesh import CSG_ENGINE_AVAILABLE, csg_native, cut, material_contains


MODULE = "aveva_e3d_mcp.primitive_csg_mesh"
TOLERANCE_MM = 0.2
RADIUS_MM = 100.0
CUTTER_RADIUS_MM = 5.0
CENTER_DISTANCE_MM = 94.9
PARENT_HEIGHT_MM = 20.0
CUTTER_HEIGHT_MM = 30.0
PHI = math.pi / 52.0


def thin_wall_geometry(*, root_position=(0.0, 0.0, 0.0), cutter_height=CUTTER_HEIGHT_MM,
                       cutter_axes=None, center_distance=CENTER_DISTANCE_MM):
    cutter_axes = cutter_axes or (
        (math.cos(PHI), math.sin(PHI), 0.0),
        (-math.sin(PHI), math.cos(PHI), 0.0),
        (0.0, 0.0, 1.0),
    )
    return csg_native(
        "CYLI", {"DIAM": 2.0 * RADIUS_MM, "HEIG": PARENT_HEIGHT_MM},
        position=root_position,
        cutters=[cut(
            dimensions={"DIAM": 2.0 * CUTTER_RADIUS_MM, "HEIG": cutter_height},
            position=(center_distance * math.cos(PHI), center_distance * math.sin(PHI), 0.0),
            axes=cutter_axes,
        )],
    )


def edge_uses(mesh):
    uses = {}
    for face in mesh["triangles"]:
        for first, second in zip(face, face[1:] + face[:1]):
            edge = tuple(sorted((first, second)))
            uses.setdefault(edge, []).append((first, second))
    return uses


@unittest.skipUnless(CSG_ENGINE_AVAILABLE, "Install the geometry extra for Mesh64 integration tests.")
class ThinWallGeometryTests(unittest.TestCase):
    def test_parallel_through_hole_refines_both_operands_and_keeps_a_closed_wall(self):
        native = thin_wall_geometry()
        with patch(f"{MODULE}._run_csg_worker", wraps=csg_mesh._run_csg_worker) as worker:
            mesh = csg_mesh.derive_primitive_csg_mesh(
                native, radial_segments=12, chord_tolerance_mm=TOLERANCE_MM)
        worker.assert_called_once()
        self.assertTrue(mesh["success"], mesh.get("diagnostics"))
        self.assertTrue(mesh["material_boundary_validated"])
        self.assertTrue(mesh["validation"]["checks"]["closed_manifold"])
        self.assertTrue(mesh["validation"]["checks"]["winding_consistent"])

        positive, cutter = mesh["source_operands"]
        positive_plan, cutter_plan = positive["tessellation"], cutter["tessellation"]
        self.assertEqual(positive_plan["radial_segments"], 100)
        self.assertGreater(cutter_plan["radial_segments"], 12)
        self.assertLessEqual(positive_plan["max_surface_deviation_mm"],
                             positive_plan["thin_wall_guard"]["obligations"][0]["required_tolerance_mm"])
        self.assertLessEqual(cutter_plan["max_surface_deviation_mm"],
                             cutter_plan["thin_wall_guard"]["obligations"][0]["required_tolerance_mm"])
        self.assertEqual(positive_plan["thin_wall_guard"]["obligations"],
                         cutter_plan["thin_wall_guard"]["obligations"])

        uses = edge_uses(mesh)
        self.assertTrue(all(len(directions) == 2 for directions in uses.values()))
        self.assertTrue(all(directions[0] == tuple(reversed(directions[1]))
                            for directions in uses.values()))
        self.assertEqual(len(mesh["vertices"]) - len(uses) + len(mesh["triangles"]), 0,
                         "A single through-hole in the cylinder forms a closed genus-one boundary.")

        cutter_center = (CENTER_DISTANCE_MM * math.cos(PHI), CENTER_DISTANCE_MM * math.sin(PHI))
        for height in (-9.5, 0.0, 9.5):
            self.assertFalse(material_contains(mesh, [*cutter_center, height]))
            for angular_offset in (-0.04, -0.02, 0.02, 0.04):
                angle = PHI + angular_offset
                point = [99.875 * math.cos(angle), 99.875 * math.sin(angle), height]
                with self.subTest(height=height, angular_offset=angular_offset):
                    self.assertTrue(material_contains(mesh, point),
                                    "Material must remain between the through-hole and the exterior wall.")
            for angular_offset in (-math.pi / 2, -math.pi / 4, 0.0, math.pi / 4, math.pi / 2):
                angle = PHI + math.pi + angular_offset
                point = [cutter_center[0] + 5.2 * math.cos(angle),
                         cutter_center[1] + 5.2 * math.sin(angle), height]
                with self.subTest(height=height, cutter_angle=angular_offset):
                    self.assertTrue(material_contains(mesh, point),
                                    "The material immediately outside the bore wall must be retained.")


class ThinWallAdmissionTests(unittest.TestCase):
    def derive_to_sentinel_worker(self, native):
        with patch(f"{MODULE}._run_csg_worker", return_value={"success": False,
                                                             "error_code": "sentinel_worker"}) as worker:
            mesh = csg_mesh.derive_primitive_csg_mesh(
                native, radial_segments=12, chord_tolerance_mm=TOLERANCE_MM)
        worker.assert_called_once()
        return worker.call_args.args[0]

    def test_blind_and_nonparallel_cutters_keep_the_requested_tessellation(self):
        blind = self.derive_to_sentinel_worker(thin_wall_geometry(cutter_height=19.9))
        self.assertEqual(len(blind[0]["vertices"]), 106)
        self.assertEqual(len(blind[1]["vertices"]), 26)

        tilt = 0.01
        tilted_axes = (
            (math.cos(tilt), 0.0, -math.sin(tilt)),
            (0.0, 1.0, 0.0),
            (math.sin(tilt), 0.0, math.cos(tilt)),
        )
        tilted = self.derive_to_sentinel_worker(thin_wall_geometry(cutter_axes=tilted_axes))
        self.assertEqual(len(tilted[0]["vertices"]), 106)
        self.assertEqual(len(tilted[1]["vertices"]), 26)

    def test_guard_segment_and_mesh_budgets_refuse_before_worker(self):
        native = thin_wall_geometry()
        with patch(f"{MODULE}._MAX_ADAPTIVE_RADIAL_SEGMENTS", 96), \
                patch(f"{MODULE}._run_csg_worker") as worker:
            result = csg_mesh.derive_primitive_csg_mesh(
                native, radial_segments=12, chord_tolerance_mm=TOLERANCE_MM)
        worker.assert_not_called()
        self.assertEqual(result["error_code"], "csg_thin_wall_guard_tessellation_budget")

        # The guarded plan is 202 positive vertices plus 50 cutter vertices.
        with patch(f"{MODULE}.MAX_VERTICES", 227), patch(f"{MODULE}._run_csg_worker") as worker:
            result = csg_mesh.derive_primitive_csg_mesh(
                native, radial_segments=12, chord_tolerance_mm=TOLERANCE_MM)
        worker.assert_not_called()
        self.assertEqual(result["error_code"], "csg_thin_wall_guard_mesh_budget")
        self.assertEqual(result["diagnostics"]["vertices"], 252)

    def test_clearance_below_world_numeric_reserve_refuses_before_worker(self):
        native = thin_wall_geometry(root_position=(999_999_900.0, 0.0, 0.0),
                                    center_distance=94.999999)
        with patch(f"{MODULE}._run_csg_worker") as worker:
            result = csg_mesh.derive_primitive_csg_mesh(
                native, radial_segments=12, chord_tolerance_mm=TOLERANCE_MM)
        worker.assert_not_called()
        self.assertEqual(result["error_code"], "csg_thin_wall_guard_clearance_ambiguous")
        self.assertGreater(result["diagnostics"]["reserve_mm"], 0.0)

    @unittest.skipUnless(CSG_ENGINE_AVAILABLE, "Install the geometry extra for Mesh64 integration tests.")
    def test_omitting_chord_tolerance_preserves_the_legacy_mesh_contract(self):
        native = thin_wall_geometry()
        default = csg_mesh.derive_primitive_csg_mesh(native, radial_segments=12)
        explicit_none = csg_mesh.derive_primitive_csg_mesh(native, radial_segments=12,
                                                           chord_tolerance_mm=None)
        self.assertEqual(default, explicit_none)
        self.assertTrue(default["success"], default.get("diagnostics"))
        self.assertNotIn("tessellation", default)
        self.assertTrue(all("tessellation" not in operand for operand in default["source_operands"]))


if __name__ == "__main__":
    unittest.main()

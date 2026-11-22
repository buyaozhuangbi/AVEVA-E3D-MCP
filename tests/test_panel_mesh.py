from __future__ import annotations

import math
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.panel_mesh import derive_panel_mesh


def _native(points=None, justification="BOTT"):
    points = points or [[0, 0], [4, 0], [4, 3], [2, 1], [0, 3]]
    return {
        "success": True, "complete": True, "topology_valid": True, "read_errors": {},
        "unsupported_features": [], "loop_count": 1, "revision": "r-panel-1",
        "element": {"refno": "=1/2", "type": "PANEL"},
        "thickness_mm": 2.0,
        "native_justification": "BOTT" if justification == "BOTTOM" else justification,
        "loops": [{"complete": True, "polygon_valid": True, "read_errors": {},
                   "unsupported_features": [], "blind": False, "thickness_mm": 2.0,
                   "native_justification": justification, "planar_parallel_xy": True,
                   "coplanar_in_local_xy": True,
                   "vertices": [{"position": [x, y, 0], "fillet_radius_mm": 0.0,
                                 "GTYP": "unset"} for x, y in points]}],
    }


class PanelMeshTests(unittest.TestCase):
    def test_concave_outline_produces_closed_outward_prism(self):
        mesh = derive_panel_mesh(_native())
        self.assertTrue(mesh["success"], mesh["errors"])
        self.assertTrue(mesh["closed_manifold"])
        self.assertEqual(mesh["counts"], {"vertices": 10, "triangles": 16})
        edges, directions = Counter(), Counter()
        for tri, normal in zip(mesh["triangles"], mesh["triangle_normals"]):
            self.assertAlmostEqual(math.hypot(*normal), 1.0)
            a, b, c = (mesh["vertices"][index] for index in tri)
            cross = ((b[1]-a[1])*(c[2]-a[2])-(b[2]-a[2])*(c[1]-a[1]),
                     (b[2]-a[2])*(c[0]-a[0])-(b[0]-a[0])*(c[2]-a[2]),
                     (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]))
            self.assertGreater(sum(cross[i] * normal[i] for i in range(3)), 0)
            for left, right in zip(tri, tri[1:] + tri[:1]):
                edge = tuple(sorted((left, right)))
                edges[edge] += 1
                directions[edge] += 1 if left < right else -1
        self.assertEqual(set(edges.values()), {2})
        self.assertEqual(set(directions.values()), {0})
        self.assertTrue(all(math.isfinite(value) for point in mesh["vertices"] for value in point))
        self.assertTrue(mesh["available"])

    def test_small_valid_outline_is_scale_invariant_for_ear_clipping(self):
        points = [[0.0, 0.0], [0.004, 0.0], [0.004, 0.003], [0.002, 0.001], [0.0, 0.003]]
        mesh = derive_panel_mesh(_native(points=points))
        self.assertTrue(mesh["success"], mesh["errors"])
        self.assertTrue(mesh["closed_manifold"])
        self.assertEqual(mesh["counts"], {"vertices": 10, "triangles": 16})

    def test_world_mesh_uses_validated_native_pave_positions(self):
        native = _native()
        translation = [120.0, -35.0, 8.0]
        x_axis, y_axis, z_axis = [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [1.0, 0.0, 0.0]
        loop = native["loops"][0]
        for vertex in loop["vertices"]:
            x, y, _ = vertex["position"]
            vertex["position_world_mm"] = [translation[0] + z_axis[0] * 0 + x_axis[0] * x + y_axis[0] * y,
                                            translation[1] + x_axis[1] * x + y_axis[1] * y,
                                            translation[2] + x_axis[2] * x + y_axis[2] * y]
            vertex["world_position_complete"] = True
        native["world_positions_requested"] = True
        native["world_positions_complete"] = True

        mesh = derive_panel_mesh(native, coordinate_space="WORLD")
        self.assertTrue(mesh["success"], mesh["errors"])
        self.assertTrue(mesh["available"])
        self.assertEqual(mesh["coordinate_frame"], "WORLD coordinates; millimetres")
        self.assertEqual(mesh["world_frame"]["origin"], translation)
        self.assertEqual(mesh["world_frame"]["z_axis"], [1.0, 0.0, 0.0])
        self.assertEqual(mesh["counts"], {"vertices": 10, "triangles": 16})
        self.assertEqual((min(vertex[0] for vertex in mesh["vertices"]),
                          max(vertex[0] for vertex in mesh["vertices"])), (120.0, 122.0))
        for normal in mesh["triangle_normals"]:
            self.assertAlmostEqual(math.hypot(*normal), 1.0)
        self.assertTrue(mesh["closed_manifold"])

    def test_documented_justifications_map_to_expected_z_bounds(self):
        for token, expected in (("BOTT", (0, 2)), ("BOTTOM", (0, 2)),
                                ("CENT", (-1, 1)), ("TOP", (-2, 0))):
            with self.subTest(token=token):
                mesh = derive_panel_mesh(_native(justification=token))
                self.assertTrue(mesh["success"], mesh["errors"])
                z_values = [point[2] for point in mesh["vertices"]]
                self.assertEqual((min(z_values), max(z_values)), expected)

    def test_fail_closed_for_unsupported_evidence(self):
        variants = []
        native = _native(); native["topology_valid"] = None; variants.append(native)
        native = _native(); native["loops"].append(native["loops"][0]); native["loop_count"] = 2; variants.append(native)
        native = _native(); native["loops"][0]["blind"] = True; variants.append(native)
        native = _native(); native["loops"][0]["fillet"] = 1; native["loops"][0]["unsupported_features"] = [{"code": "fillet"}]; variants.append(native)
        native = _native(justification="UTOP"); variants.append(native)
        native = _native(); native["loops"][0]["thickness_mm"] = 0; variants.append(native)
        native = _native(); native["loops"][0]["vertices"][0]["position"][2] = 1; variants.append(native)
        native = _native(); native["loops"][0]["vertices"][0]["fillet_radius_mm"] = None; variants.append(native)
        native = _native(); native["loops"][0]["vertices"][0]["position"][0] = 10**10000; variants.append(native)
        for geometry in variants:
            with self.subTest(geometry=geometry):
                mesh = derive_panel_mesh(geometry)
                self.assertFalse(mesh["success"])
                self.assertFalse(mesh["available"])
                self.assertEqual(mesh["vertices"], [])
                self.assertEqual(mesh["triangles"], [])
                self.assertTrue(mesh["errors"])

    def test_world_mesh_fails_closed_for_missing_or_non_rigid_positions(self):
        incomplete = _native()
        incomplete["world_positions_requested"] = True
        incomplete["world_positions_complete"] = False
        mesh = derive_panel_mesh(incomplete, coordinate_space="WORLD")
        self.assertFalse(mesh["success"])
        self.assertEqual(mesh["vertices"], [])
        self.assertIn("complete_native_world_positions_required", mesh["errors"])

        for vertex in incomplete["loops"][0]["vertices"]:
            x, y, _ = vertex["position"]
            vertex["position_world_mm"] = [x * 2.0, y, 0.0]
            vertex["world_position_complete"] = True
        incomplete["world_positions_complete"] = True
        mesh = derive_panel_mesh(incomplete, coordinate_space="WORLD")
        self.assertFalse(mesh["success"])
        self.assertEqual(mesh["vertices"], [])
        self.assertIn("world_positions_do_not_define_valid_panel_frame", mesh["errors"])

    def test_world_mesh_rejects_a_single_inconsistent_native_point(self):
        native = _native()
        native["world_positions_requested"] = True
        native["world_positions_complete"] = True
        for vertex in native["loops"][0]["vertices"]:
            x, y, _ = vertex["position"]
            vertex["position_world_mm"] = [x, y, 0.0]
            vertex["world_position_complete"] = True
        native["loops"][0]["vertices"][-1]["position_world_mm"][2] = 0.1
        mesh = derive_panel_mesh(native, coordinate_space="WORLD")
        self.assertFalse(mesh["success"])
        self.assertEqual(mesh["vertices"], [])
        self.assertIn("world_positions_do_not_define_valid_panel_frame", mesh["errors"])


if __name__ == "__main__":
    unittest.main()

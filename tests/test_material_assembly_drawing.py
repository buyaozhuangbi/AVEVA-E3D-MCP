"""Independent visibility and provenance checks for mixed material assemblies."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.drawing_assembly import combine_drawing_meshes
from aveva_e3d_mcp.material_assembly_drawing import (
    combine_material_drawing_meshes, generate_material_assembly_drawing,
)
from aveva_e3d_mcp.orthographic_drawing import generate_orthographic_drawing
from aveva_e3d_mcp.primitive_csg_mesh import derive_primitive_csg_mesh
from aveva_e3d_mcp.primitive_mesh import derive_primitive_mesh
from test_primitive_csg_mesh import CSG_ENGINE_AVAILABLE, csg_native, cut, sync_target


MODULE = "aveva_e3d_mcp.material_assembly_drawing"
FOOTER = "PARAMETER ASSEMBLY - NATIVE CUT SCOPE UNVERIFIED"
WORLD_FRAME = "WORLD coordinates; millimetres"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def geometry_digest(mesh):
    return digest({"vertices": mesh["vertices"], "triangles": mesh["triangles"]})


def material_context():
    return {"available": True, "session_id": "material-session-A", "version": "E3D21",
            "project": {"name": "APS", "mdb": "ALL", "connected": True, "recovery_only": False}}


def material_native(refno="=1/10", *, dimensions=None, position=(0, 0, 0), cutters=None):
    native = csg_native(dimensions=dimensions, position=position, cutters=[] if cutters is None else cutters)
    native["element"].update(refno=refno, name=f"/MATERIAL-{int(refno.split('/')[1])}")
    native["owner_member_refnos"] = [refno]
    for child in native["negative_children"]:
        child["frame"]["storage_frame_refno"] = refno
    return sync_target(native)


def material_source(native, mesh, mode):
    refno = native["element"]["refno"]
    context = material_context()
    evidence = {"resolved_refno": refno, "resolved_type": native["element"]["type"],
        "atomic_snapshot": False, "native_geometry_reads": 2, "native_reads_agree": True,
        "native_cut_application_verified": False,
        "native_read_before_sha256": digest(native), "native_read_after_sha256": digest(native),
        "context": {"before": deepcopy(context), "after": deepcopy(context), "boundary_agreement": True}}
    result = {"success": True, "available": True, "complete": True, "coordinate_space": "WORLD",
        "coordinate_unit": "mm", "requested_refno": refno, "resolved_refno": refno,
        "resolved_type": native["element"]["type"], "expected_type": native["element"]["type"],
        "geometry_mode": mode, "source_geometry": deepcopy(native), "derived_mesh": deepcopy(mesh),
        "source_evidence": evidence, "atomic_snapshot": False,
        "native_tessellation": False, "native_cut_application_verified": False, "mesh_detail": "full"}
    if mode == "parameter_mesh":
        del result["source_evidence"]
    return result


def parameter_source(refno="=1/10", *, dimensions=None, position=(0, 0, 0)):
    native = material_native(refno, dimensions=dimensions, position=position)
    mesh = derive_primitive_mesh(native, radial_segments=24, coordinate_space="WORLD")
    if mesh.get("success") is not True:
        raise AssertionError(mesh)
    return material_source(native, mesh, "parameter_mesh")


def csg_source(refno="=1/10", *, dimensions=None, position=(0, 0, 0), cutters=None):
    native = material_native(refno, dimensions=dimensions, position=position, cutters=[cut()] if cutters is None else cutters)
    mesh = derive_primitive_csg_mesh(native, radial_segments=24)
    if mesh.get("success") is not True:
        raise AssertionError(mesh)
    return material_source(native, mesh, "owner_member_difference")


def lines_for(drawing, index, *, visibility=None):
    return [line for line in drawing["views"][0]["lines"] if line["source_index"] == index
            and (visibility is None or line.get("visibility") == visibility)]


def polygon_chord_half_width(radius, segments, y):
    """Intersect a horizontal line with an independently sampled regular polygon."""
    vertices = [(radius * math.cos(2 * math.pi * i / segments), radius * math.sin(2 * math.pi * i / segments))
                for i in range(segments)]
    intersections = []
    for first, second in zip(vertices, vertices[1:] + vertices[:1]):
        if min(first[1], second[1]) <= y <= max(first[1], second[1]) and first[1] != second[1]:
            ratio = (y - first[1]) / (second[1] - first[1])
            intersections.append(first[0] + ratio * (second[0] - first[0]))
    return max(intersections)


class _MaterialAssemblyAssertions:
    maxDiff = 1500

    def combined(self, sources):
        before = deepcopy(sources)
        result = combine_material_drawing_meshes(sources)
        self.assertTrue(result["success"], result.get("error_code"))
        self.assertTrue(result["available"])
        self.assertTrue(result["complete"])
        self.assertEqual(sources, before)
        self.assertEqual(result["coordinate_frame"], WORLD_FRAME)
        self.assertEqual(result["coordinate_space"], "WORLD")
        self.assertEqual(result["coordinate_unit"], "mm")
        self.assertEqual(result["geometry_sha256"], geometry_digest(result))
        for flag in ("vertices_welded", "boolean_union_applied", "native_tessellation", "native_cut_application_verified"):
            self.assertIs(result[flag], False, flag)
        return result

    def assert_refused(self, result):
        for flag in ("success", "available", "complete"):
            self.assertFalse(result[flag], {"error_code": result.get("error_code"), "counts": result.get("counts")})
        self.assertTrue(result["error_code"])
        for field in ("vertices", "triangles", "source_ranges"):
            self.assertEqual(result[field], [], field)


class MaterialAssemblyDrawingTests(_MaterialAssemblyAssertions, unittest.TestCase):
    def test_parameter_sources_preserve_world_arrays_offsets_and_exact_source_ranges(self):
        sources = [parameter_source(dimensions={"XLEN": 10, "YLEN": 8, "ZLEN": 4}),
                   parameter_source("=1/11", dimensions={"XLEN": 6, "YLEN": 4, "ZLEN": 2}, position=(30, 10, 5))]
        sources[0]["source_geometry_sha256"] = digest(sources[0]["source_geometry"])
        combined = self.combined(sources)
        first, second = (source["derived_mesh"] for source in sources)
        self.assertEqual(combined["vertices"], first["vertices"] + second["vertices"])
        self.assertEqual(combined["triangles"], first["triangles"] + [[index + 8 for index in face] for face in second["triangles"]])
        self.assertEqual(combined["counts"], {"sources": 2, "vertices": 16, "triangles": 24})
        for index, span in enumerate(combined["source_ranges"]):
            self.assertEqual(span["source_index"], index)
            self.assertEqual(span["refno"], f"=1/{10 + index}")
            self.assertEqual(span["geometry_mode"], "parameter_mesh")
            self.assertEqual(span["vertex_range"], [8 * index, 8 * (index + 1)])
            self.assertEqual(span["triangle_range"], [12 * index, 12 * (index + 1)])
            self.assertEqual(span["geometry_sha256"], geometry_digest(sources[index]["derived_mesh"]))
            self.assertEqual(span["source_mesh_sha256"], geometry_digest(sources[index]["derived_mesh"]))
        combined["vertices"][0][0] += 500
        self.assertNotEqual(combined["vertices"][0], first["vertices"][0])

    def test_nested_and_coincident_sources_stay_separate_without_union_or_welding(self):
        for dimensions in ({"XLEN": 100, "YLEN": 80, "ZLEN": 40}, {"XLEN": 10, "YLEN": 8, "ZLEN": 4}):
            with self.subTest(dimensions=dimensions):
                combined = self.combined([parameter_source(), parameter_source("=1/11", dimensions=dimensions)])
                self.assertEqual(len(combined["vertices"]), 16)
                self.assertEqual(combined["validation"]["checks"]["component_count"], 2)
                self.assertEqual([span["vertex_range"] for span in combined["source_ranges"]], [[0, 8], [8, 16]])

    def test_joint_occlusion_and_source_attribution_use_all_parameter_sources(self):
        front = parameter_source(dimensions={"XLEN": 20, "YLEN": 20, "ZLEN": 4}, position=(0, 0, 10))
        behind = parameter_source("=1/11", dimensions={"XLEN": 4, "YLEN": 4, "ZLEN": 4}, position=(0, 0, -10))
        combined = self.combined([front, behind])
        drawing = generate_material_assembly_drawing(combined, views=["TOP"], show_hidden_lines=True)
        self.assertTrue(drawing["success"], drawing)
        self.assertEqual(drawing["footer"], FOOTER)
        self.assertIn(FOOTER, drawing["svg"])
        self.assertEqual(drawing["geometry_sha256"], combined["geometry_sha256"])
        self.assertEqual(drawing["source_ranges"], combined["source_ranges"])
        self.assertTrue(lines_for(drawing, 0, visibility="visible"))
        self.assertEqual(lines_for(drawing, 1, visibility="visible"), [])
        self.assertTrue(lines_for(drawing, 1, visibility="hidden"))
        for line in drawing["views"][0]["lines"]:
            index = line["source_index"]
            span = combined["source_ranges"][index]
            self.assertEqual(line["source_refno"], span["refno"])
            self.assertEqual(line["source_geometry_mode"], span["geometry_mode"])
            self.assertTrue(all(span["vertex_range"][0] <= vertex < span["vertex_range"][1] for vertex in line["edge"]))
        without_removal = generate_material_assembly_drawing(combined, views=["TOP"], hidden_line_removal=False)
        self.assertTrue(without_removal["success"], without_removal)
        self.assertTrue(lines_for(without_removal, 1))

    def test_source_count_one_through_128_and_129th_is_rejected(self):
        for sources in (None, [], {}, [parameter_source()] * 129):
            with self.subTest(source_type=type(sources).__name__):
                self.assert_refused(combine_material_drawing_meshes(sources))
        sources = [parameter_source(f"=1/{100 + index}", dimensions={"XLEN": 1, "YLEN": 1, "ZLEN": 1},
                                    position=(3 * index, 0, 0)) for index in range(128)]
        combined = self.combined(sources)
        self.assertEqual(combined["counts"], {"sources": 128, "vertices": 1024, "triangles": 1536})

    def test_alias_duplicates_and_invalid_source_contracts_return_no_partial_geometry(self):
        first = parameter_source()
        duplicate = parameter_source("=0001/0010")
        self.assert_refused(combine_material_drawing_meshes([first, duplicate]))
        for fault in ("mode", "incomplete", "local", "unit", "frame", "identity", "native", "cut", "arrays"):
            with self.subTest(fault=fault):
                source = parameter_source("=1/11")
                mesh = source["derived_mesh"]
                if fault == "mode": source["geometry_mode"] = "native_csg"
                elif fault == "incomplete": source["complete"] = False
                elif fault == "local": source["coordinate_space"] = "local"
                elif fault == "unit": mesh["coordinate_unit"] = "m"
                elif fault == "frame": mesh["coordinate_frame"] = "SDK native unverified"
                elif fault == "identity": mesh["refno"] = "=1/12"
                elif fault == "native": mesh["native_tessellation"] = True
                elif fault == "cut": mesh["cuts_applied"] = True
                else: mesh["vertices"] = None
                self.assert_refused(combine_material_drawing_meshes([first, source]))

    def test_legacy_parameter_source_without_native_parameters_stays_a_narrow_geometry_input(self):
        source = parameter_source()
        del source["source_geometry"]
        combined = self.combined([source])
        self.assertFalse(combined["native_cut_application_verified"])
        self.assertEqual(combined["source_ranges"][0]["source_mesh_sha256"], geometry_digest(source["derived_mesh"]))

    def test_parameter_topology_and_cumulative_mesh_budgets_are_checked(self):
        first = parameter_source()
        for fault in ("open", "inward"):
            source = parameter_source("=1/11")
            if fault == "open": source["derived_mesh"]["triangles"].pop()
            else: source["derived_mesh"]["triangles"] = [list(reversed(face)) for face in source["derived_mesh"]["triangles"]]
            with self.subTest(fault=fault):
                self.assert_refused(combine_material_drawing_meshes([first, source]))
        for budget, value in (("MAX_VERTICES", 15), ("MAX_TRIANGLES", 23)):
            with self.subTest(budget=budget), patch(f"{MODULE}.{budget}", value):
                self.assert_refused(combine_material_drawing_meshes([first, parameter_source("=1/11")]))
        with patch(f"{MODULE}.MAX_VERTICES", 16), patch(f"{MODULE}.MAX_TRIANGLES", 24):
            self.assertTrue(combine_material_drawing_meshes([first, parameter_source("=1/11")])["success"])

    def test_combined_hash_ranges_and_validation_tampering_is_rejected_before_projection(self):
        combined = self.combined([parameter_source()])
        for fault in ("hash", "geometry", "open_rehashed", "ranges", "mode", "validation", "native",
                      "boolean_index", "boolean_component", "whole_engine"):
            with self.subTest(fault=fault):
                candidate = deepcopy(combined)
                if fault == "hash": candidate["geometry_sha256"] = "0" * 64
                elif fault == "geometry": candidate["vertices"][0][0] += 1
                elif fault == "open_rehashed":
                    candidate["triangles"].pop()
                    candidate["geometry_sha256"] = geometry_digest(candidate)
                elif fault == "ranges": candidate["source_ranges"][0]["vertex_range"] = [1, 8]
                elif fault == "mode": candidate["source_ranges"][0]["geometry_mode"] = "owner_member_difference"
                elif fault == "validation": candidate["validation"]["checks"]["closed_manifold"] = False
                elif fault == "native": candidate["native_cut_application_verified"] = True
                elif fault == "boolean_index": candidate["source_ranges"][0]["source_index"] = False
                elif fault == "boolean_component": candidate["source_ranges"][0]["component_map"][0]["source_component"] = False
                else: candidate["engine"] = {"name": "manifold3d", "mesh_api": "Mesh64", "status": "NoError"}
                drawing = generate_material_assembly_drawing(candidate, views=["TOP"])
                self.assertFalse(drawing["success"], drawing)
                self.assertEqual(drawing["views"], [])
                self.assertNotIn("svg", drawing)

    def test_projection_budget_failure_has_no_partial_views_or_svg(self):
        combined = self.combined([parameter_source()])
        with patch("aveva_e3d_mcp.orthographic_drawing.MAX_TOTAL_DRAWING_SEGMENTS", 3):
            drawing = generate_material_assembly_drawing(combined, views=["TOP"])
        self.assertFalse(drawing["success"], drawing)
        self.assertEqual(drawing["views"], [])
        self.assertNotIn("svg", drawing)


@unittest.skipUnless(CSG_ENGINE_AVAILABLE, "Install the geometry extra for Mesh64 integration tests.")
class MaterialAssemblyBooleanDrawingTests(_MaterialAssemblyAssertions, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.through = csg_source()
        cls.cavity = csg_source("=1/11", dimensions={"XLEN": 20, "YLEN": 20, "ZLEN": 20},
                                cutters=[cut("BOX", {"XLEN": 4, "YLEN": 4, "ZLEN": 4})])
        cls.empty_source = csg_source("=1/12", cutters=[cut("BOX", {"XLEN": 200, "YLEN": 200, "ZLEN": 100})])
        cls.second_empty = csg_source("=1/14", cutters=[cut("BOX", {"XLEN": 200, "YLEN": 200, "ZLEN": 100})])

    def test_rear_bar_is_visible_through_cylinder_hole_and_occluded_outside_it(self):
        # The cutter reaches z=-30, so this bar lies inside its tool extent.
        # The cutter belongs only to the plate and must not remove this source.
        behind = parameter_source("=1/11", dimensions={"XLEN": 60, "YLEN": 4, "ZLEN": 4}, position=(0, 0, -25))
        combined = self.combined([self.through, behind])
        drawing = generate_material_assembly_drawing(combined, views=["TOP"], show_hidden_lines=True)
        self.assertTrue(drawing["success"], drawing)
        for flag in ("native_tessellation", "native_cut_application_verified", "boolean_union_applied", "vertices_welded"):
            self.assertFalse(drawing[flag], flag)
        visible = lines_for(drawing, 1, visibility="visible")
        hidden = lines_for(drawing, 1, visibility="hidden")
        self.assertTrue(visible)
        self.assertTrue(hidden)
        half_width = polygon_chord_half_width(10, 24, 2)
        for y in (-2, 2):
            chords = [line for line in visible if all(abs(line[key][1] - y) < 1e-7 for key in ("start", "end"))]
            self.assertTrue(chords, (y, visible))
            projected = [point[0] for line in chords for point in (line["start"], line["end"])]
            self.assertAlmostEqual(min(projected), -half_width, delta=1e-6)
            self.assertAlmostEqual(max(projected), half_width, delta=1e-6)
        self.assertTrue(all(abs(point[0]) < 10.000001 for line in visible for point in (line["start"], line["end"])))
        self.assertTrue(any(abs(point[0]) >= 29.999 for line in hidden for point in (line["start"], line["end"])))
        default = generate_material_assembly_drawing(combined, views=["TOP"])
        self.assertEqual(lines_for(default, 1), visible)
        self.assertEqual([span["geometry_mode"] for span in combined["source_ranges"]],
                         ["owner_member_difference", "parameter_mesh"])

    def test_cavity_acceptance_is_scoped_to_its_source_and_preserves_component_orientation(self):
        parameter = parameter_source("=1/13", dimensions={"XLEN": 4, "YLEN": 4, "ZLEN": 4}, position=(30, 0, 0))
        combined = self.combined([parameter, self.cavity])
        self.assertFalse(combined["validation"]["valid"])
        cavity = combined["source_ranges"][1]
        self.assertEqual(len(cavity["source_cavity_shells"]), 1)
        self.assertEqual(len(cavity["cavity_shells"]), 1)
        self.assertNotEqual(cavity["source_cavity_shells"], cavity["cavity_shells"])
        self.assertEqual(combined["validation"]["checks"]["component_count"], 3)
        drawing = generate_material_assembly_drawing(combined, views=["TOP"], show_hidden_lines=True)
        self.assertTrue(drawing["success"], drawing)
        self.assertFalse(generate_orthographic_drawing(combined["vertices"], combined["triangles"])["success"])
        self.assertFalse(combine_drawing_meshes([self.cavity])["success"])
        forged_parameter = deepcopy(parameter)
        forged_parameter["derived_mesh"]["triangles"] = [list(reversed(face)) for face in parameter["derived_mesh"]["triangles"]]
        self.assert_refused(combine_material_drawing_meshes([self.cavity, forged_parameter]))
        forged_cavity = deepcopy(self.cavity)
        forged_cavity["geometry_mode"] = "parameter_mesh"
        forged_cavity["derived_mesh"]["cuts_applied"] = False
        self.assert_refused(combine_material_drawing_meshes([forged_cavity]))

    def test_empty_csg_source_keeps_its_zero_range_and_other_source_indices(self):
        parameter = parameter_source("=1/13")
        combined = self.combined([self.empty_source, parameter])
        self.assertFalse(combined["empty"])
        self.assertEqual(combined["counts"], {"sources": 2, "vertices": 8, "triangles": 12})
        empty, retained = combined["source_ranges"]
        self.assertTrue(empty["empty"])
        self.assertEqual(empty["vertex_range"], [0, 0])
        self.assertEqual(empty["triangle_range"], [0, 0])
        self.assertEqual(retained["source_index"], 1)
        drawing = generate_material_assembly_drawing(combined, views=["TOP"])
        self.assertTrue(drawing["success"], drawing)
        self.assertTrue(lines_for(drawing, 1))
        self.assertEqual(lines_for(drawing, 0), [])

    def test_all_empty_material_succeeds_with_no_views_or_image(self):
        combined = self.combined([self.empty_source, self.second_empty])
        self.assertTrue(combined["empty"])
        self.assertEqual(combined["counts"], {"sources": 2, "vertices": 0, "triangles": 0})
        self.assertEqual(combined["vertices"], [])
        self.assertEqual(combined["triangles"], [])
        drawing = generate_material_assembly_drawing(combined)
        self.assertTrue(drawing["success"], drawing)
        self.assertTrue(drawing["empty"])
        self.assertEqual(drawing["views"], [])
        self.assertEqual(drawing["segment_count"], 0)
        self.assertNotIn("svg", drawing)

    def test_csg_geometry_provenance_and_cavity_list_cannot_be_forged(self):
        for fault in ("hash", "native", "precision", "cavity", "validation", "open_rehashed"):
            source = deepcopy(self.cavity)
            mesh = source["derived_mesh"]
            if fault == "hash": mesh["geometry_sha256"] = "0" * 64
            elif fault == "native": mesh["native_cut_application_verified"] = True
            elif fault == "precision": mesh["engine"]["numeric_precision"] = "float32"
            elif fault == "cavity": mesh["material_boundary_validation"]["cavity_shells"] = []
            elif fault == "validation": mesh["validation"]["valid"] = True
            else:
                mesh["triangles"].pop()
                mesh["geometry_sha256"] = geometry_digest(mesh)
            with self.subTest(fault=fault):
                self.assert_refused(combine_material_drawing_meshes([source]))


if __name__ == "__main__":
    unittest.main()

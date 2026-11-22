"""Printable aligned sheets for verified mixed material assembly linework."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import re
from typing import Any

from .drawing_sheet import build_drawing_sheet, normalize_sheet_options, _fitted_height
from .material_assembly_drawing import MAX_MATERIAL_DRAWING_SOURCES, WORLD_FRAME
from .projection_sheet import _text_box, _validate_annotations


_FOOTER = "PARAMETER ASSEMBLY - NATIVE CUT SCOPE UNVERIFIED"
_PRECISION = 15
_GEOMETRY_ROLES = {"silhouette", "feature"}
_PROOF_FIELDS = (
    "source_ranges", "geometry_sha256", "geometry_hash_scope", "validation", "material_boundary_validation",
    "native_tessellation", "native_cut_application_verified", "boolean_union_applied", "vertices_welded",
    "source_intersections_checked", "global_negative_cut_scope_verified", "native_csg_evaluated",
    "cuts_applied", "source_parameter_boolean_evaluated", "material_boundary_validated",
    "closed_manifold", "winding_consistent",
)


def _material_sources(drawing: Any) -> list[Mapping[str, Any]]:
    if (not isinstance(drawing, Mapping)
            or any(drawing.get(flag) is not True for flag in ("success", "available", "complete",
                       "material_boundary_validated", "closed_manifold", "winding_consistent"))
            or drawing.get("drawing_kind") != "parameter material assembly linework"
            or drawing.get("empty") is not False):
        raise ValueError("successful_nonempty_material_assembly_drawing_required")
    if any(field not in drawing for field in _PROOF_FIELDS):
        raise ValueError("material_assembly_sheet_source_evidence_required")
    if (drawing.get("coordinate_space") != "WORLD" or drawing.get("coordinate_frame") != WORLD_FRAME
            or drawing.get("coordinate_unit") != "mm"):
        raise ValueError("material_assembly_sheet_WORLD_millimetres_required")
    if (any(drawing.get(flag) is not False for flag in ("native_tessellation", "native_cut_application_verified",
                "boolean_union_applied", "vertices_welded", "source_intersections_checked",
                "global_negative_cut_scope_verified", "native_csg_evaluated"))
            or drawing.get("footer") != _FOOTER or drawing.get("scope") != _FOOTER or "engine" in drawing):
        raise ValueError("material_assembly_sheet_source_scope_required")
    if not isinstance(drawing.get("geometry_sha256"), str) or re.fullmatch(r"[0-9a-f]{64}", drawing["geometry_sha256"]) is None:
        raise ValueError("material_assembly_sheet_source_mesh_hash_required")
    sources, counts = drawing.get("source_ranges"), drawing.get("counts")
    if (not isinstance(sources, list) or not 1 <= len(sources) <= MAX_MATERIAL_DRAWING_SOURCES
            or not isinstance(counts, Mapping) or counts.get("sources") != len(sources)):
        raise ValueError("material_assembly_sheet_source_ranges_required")
    validation, material = drawing.get("validation"), drawing.get("material_boundary_validation")
    if (not isinstance(validation, Mapping) or not isinstance(material, Mapping)
            or any(validation.get(flag) is not True for flag in ("success", "available", "complete"))
            or material.get("validated") is not True or material.get("empty") is not False):
        raise ValueError("material_assembly_sheet_material_evidence_required")
    seen: set[str] = set()
    any_difference = False
    for index, source in enumerate(sources):
        if (not isinstance(source, Mapping) or type(source.get("source_index")) is not int
                or source["source_index"] != index):
            raise ValueError("material_assembly_sheet_source_identity_invalid")
        refno, mode = source.get("refno"), source.get("geometry_mode")
        identity = source.get("source_identity")
        if (not isinstance(refno, str) or re.fullmatch(r"=[0-9]+/[0-9]+", refno) is None
                or refno in seen or not isinstance(identity, Mapping)
                or identity.get("refno") != refno or identity.get("resolved_type") != source.get("resolved_type")
                or not isinstance(mode, str) or mode not in {"parameter_mesh", "owner_member_difference"}
                or source.get("cuts_applied") is not (mode == "owner_member_difference")
                or type(source.get("empty")) is not bool):
            raise ValueError("material_assembly_sheet_source_identity_invalid")
        seen.add(refno)
        any_difference |= mode == "owner_member_difference"
        digest = source.get("source_mesh_sha256")
        if (not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                or source.get("geometry_sha256") != digest):
            raise ValueError("material_assembly_sheet_source_mesh_hash_required")
        vr = source.get("vertex_range")
        if (not isinstance(vr, list) or len(vr) != 2 or any(type(value) is not int for value in vr)
                or not 0 <= vr[0] <= vr[1] or source["empty"] != (vr[0] == vr[1])):
            raise ValueError("material_assembly_sheet_source_ranges_invalid")
    if (drawing.get("cuts_applied") is not any_difference
            or drawing.get("source_parameter_boolean_evaluated") is not any_difference):
        raise ValueError("material_assembly_sheet_source_mode_mismatch")
    return sources


def build_material_assembly_sheet(drawing: Any, options: Any) -> dict[str, Any]:
    """Lay out verified right-handed FRONT/TOP/SIDE material views on paper.

    ``projection_method`` must explicitly be first_angle or third_angle. Source
    material evidence is preserved; this layout does not rerun source Boolean
    geometry. Structured geometry strokes retain their original per-view line
    index and source identity. Coordinate serialization uses 15 decimal places
    in paper mm, independently of model accuracy and dimension-label precision.
    Empty material drawings belong to the caller's no-image path.
    """
    normalized = normalize_sheet_options(options)
    if "projection_method" not in normalized:
        raise ValueError("material_assembly_sheet_projection_method_required")
    sources = _material_sources(drawing)
    views = drawing.get("views")
    if (not isinstance(views, (list, tuple))
            or any(not isinstance(view, Mapping) or not isinstance(view.get("name"), str) for view in views)):
        raise ValueError("material_assembly_sheet_views_required")
    by_view = {view.get("name"): view for view in views}
    line_count = 0
    for view in views:
        lines = view.get("lines")
        if not isinstance(lines, (list, tuple)):
            raise ValueError("material_assembly_sheet_lines_required")
        for line in lines:
            if (not isinstance(line, Mapping) or not isinstance(line.get("kind"), str)
                    or line["kind"] not in _GEOMETRY_ROLES
                    or type(line.get("source_index")) is not int
                    or not 0 <= line["source_index"] < len(sources)):
                raise ValueError("material_assembly_sheet_line_source_invalid")
            source = sources[line["source_index"]]
            edge = line.get("edge")
            if (line.get("source_refno") != source["refno"] or line.get("source_geometry_mode") != source["geometry_mode"]
                    or source["empty"] or not isinstance(edge, (list, tuple)) or len(edge) != 2
                    or any(type(vertex) is not int or not source["vertex_range"][0] <= vertex < source["vertex_range"][1]
                           for vertex in edge)):
                raise ValueError("material_assembly_sheet_line_source_invalid")
        line_count += len(lines)
    if type(drawing.get("segment_count")) is not int or drawing["segment_count"] != line_count:
        raise ValueError("material_assembly_sheet_line_count_mismatch")
    scene = build_drawing_sheet(drawing, normalized, coordinate_decimal_places=_PRECISION)
    geometry_count = 0
    for stroke in scene["strokes"]:
        if stroke["role"] not in _GEOMETRY_ROLES:
            continue
        view = by_view.get(stroke.get("view_name"))
        source_line_index = stroke.get("source_line_index")
        if (not isinstance(view, Mapping) or type(source_line_index) is not int
                or not 0 <= source_line_index < len(view["lines"])):
            raise ValueError("material_assembly_sheet_line_mapping_invalid")
        line = view["lines"][source_line_index]
        stroke.update(source_index=line["source_index"], source_refno=line["source_refno"],
                      source_geometry_mode=line["source_geometry_mode"])
        geometry_count += 1
    if geometry_count != line_count:
        raise ValueError("material_assembly_sheet_line_mapping_incomplete")
    disclaimer = [text for text in scene["texts"] if text["role"] == "disclaimer"]
    if len(disclaimer) != 1:
        raise ValueError("material_assembly_sheet_disclaimer_required")
    label = "FIRST ANGLE" if normalized["projection_method"] == "first_angle" else "THIRD ANGLE"
    item = disclaimer[0]
    item["text"] = label + " / " + _FOOTER + " / NOT NATIVE DRAFT"
    item["height"] = _fitted_height(item["text"], scene["paper"]["width_mm"] - 24.0, 2.0)
    left, bottom, right, top = _text_box(item)
    if (left < 10 or bottom < 10 or right > scene["paper"]["width_mm"] - 10
            or top > scene["paper"]["height_mm"] - 10):
        raise ValueError("material_assembly_sheet_disclaimer_does_not_fit")
    _validate_annotations(scene["strokes"], scene["texts"], {view["name"]: view["cell_bounds_mm"] for view in scene["views"]})
    scene.update({key: deepcopy(drawing[key]) for key in _PROOF_FIELDS})
    scene.update(drawing_kind="parameter material assembly sheet", empty=False, scope=_FOOTER, footer=_FOOTER,
                 source_range_scope="Half-open ranges refer to the source combined WORLD mesh arrays, not paper strokes.",
                 source_attribution={"representation": "structured scene geometry strokes",
                                     "source_line_index_scope": "per-view drawing lines",
                                     "vector_source_metadata_embedded": False},
                 serialization_precision={"coordinate_decimal_places": _PRECISION, "coordinate_unit": "paper mm",
                     "engineering_accuracy_claim": False,
                     "scope": "Fixed decimal endpoint serialization after paper placement, including SVG H-Y; dimension-label precision and model accuracy are unchanged."})
    return scene

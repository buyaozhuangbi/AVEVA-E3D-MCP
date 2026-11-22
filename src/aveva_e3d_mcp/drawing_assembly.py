"""Bounded concatenation of resolved WORLD/mm meshes for joint line drawings.

Sources remain independent indexed shells. Concatenation neither welds vertices
nor evaluates Boolean unions, intersections, cuts, or nested-shell semantics.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .triangle_mesh_validation import MAX_TRIANGLES, MAX_VERTICES, validate_triangle_mesh


MAX_DRAWING_SOURCES = 16
WORLD_FRAME = "WORLD coordinates; millimetres"
_SUPPORTED_TYPES = {"PANE", "BOX", "CYLI", "CONE", "DISH"}


def _reference(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 128 or re.fullmatch(r"=[0-9]+/[0-9]+", value) is None:
        return None
    return "=" + "/".join(part.lstrip("0") or "0" for part in value[1:].split("/"))


def _failure(error_code: str, *, complete: bool = True, **diagnostics: Any) -> dict[str, Any]:
    return {
        "success": False, "available": False, "complete": complete,
        "error_code": error_code, "errors": [error_code],
        "coordinate_space": "WORLD", "coordinate_frame": WORLD_FRAME, "coordinate_unit": "mm",
        "vertices": [], "triangles": [], "source_ranges": [],
        "counts": {"sources": 0, "vertices": 0, "triangles": 0}, **diagnostics,
    }


def combine_drawing_meshes(sources: Any) -> dict[str, Any]:
    """Combine 1–16 successful ``_element_mesh`` result records for drawing.

    Source records must carry an explicit resolved numeric reference and their
    complete WORLD parameter mesh. The exact WORLD frame string is the shared
    primitive/panel adapter contract; metadata is validated, never inferred
    from coordinate magnitudes. Every source and the final combined mesh must
    pass the bounded closed/outward indexed-mesh validator used by drawing.

    Half-open source vertex/triangle ranges refer to the returned arrays. No
    caller-owned list is reused. On failure geometry arrays are empty, so an
    earlier valid source cannot be mistaken for a complete assembly drawing.
    """
    if not isinstance(sources, (list, tuple)) or not 1 <= len(sources) <= MAX_DRAWING_SOURCES:
        return _failure("drawing_sources_must_contain_one_to_sixteen_elements")

    prepared: list[tuple[Mapping[str, Any], Mapping[str, Any], str]] = []
    seen: set[str] = set()
    total_vertices = total_triangles = 0
    for index, source in enumerate(sources):
        if not isinstance(source, Mapping):
            return _failure("invalid_drawing_source", source_index=index)
        if any(source.get(flag) is not True for flag in ("success", "available", "complete")):
            return _failure("drawing_source_unavailable_or_incomplete", complete=False, source_index=index)
        if source.get("coordinate_space") != "WORLD":
            return _failure("drawing_source_world_coordinates_required", source_index=index)
        refno = _reference(source.get("resolved_refno"))
        if refno is None:
            return _failure("drawing_source_resolved_reference_required", source_index=index)
        if refno in seen:
            return _failure("duplicate_drawing_source", source_index=index, source_refno=refno)
        seen.add(refno)
        source_type = source.get("resolved_type")
        if not isinstance(source_type, str) or source_type not in _SUPPORTED_TYPES:
            return _failure("unsupported_drawing_source_type", source_index=index, source_refno=refno)
        mesh = source.get("derived_mesh")
        if not isinstance(mesh, Mapping):
            return _failure("drawing_source_mesh_missing", complete=False, source_index=index, source_refno=refno)
        if any(mesh.get(flag) is not True for flag in ("success", "available", "complete")):
            return _failure("drawing_source_mesh_unavailable_or_incomplete", complete=False,
                            source_index=index, source_refno=refno)
        if _reference(mesh.get("refno")) != refno:
            return _failure("drawing_source_mesh_reference_mismatch", source_index=index, source_refno=refno)
        if (mesh.get("coordinate_frame") != WORLD_FRAME or
                mesh.get("coordinate_space", "WORLD") != "WORLD" or
                mesh.get("coordinate_unit", "mm") != "mm"):
            return _failure("drawing_source_mesh_world_millimetres_required", source_index=index, source_refno=refno)
        if mesh.get("native_tessellation") is not False or mesh.get("cuts_applied") is not False:
            return _failure("drawing_source_parameter_mesh_required", source_index=index, source_refno=refno)
        vertices, triangles = mesh.get("vertices"), mesh.get("triangles")
        if not isinstance(vertices, (list, tuple)) or not isinstance(triangles, (list, tuple)):
            return _failure("drawing_source_geometry_arrays_required", source_index=index, source_refno=refno)
        total_vertices += len(vertices)
        total_triangles += len(triangles)
        if total_vertices > MAX_VERTICES or total_triangles > MAX_TRIANGLES:
            return _failure("drawing_assembly_mesh_limit_exceeded", complete=False,
                            max_vertices=MAX_VERTICES, max_triangles=MAX_TRIANGLES,
                            observed_counts={"sources": len(sources), "vertices": total_vertices,
                                             "triangles": total_triangles})
        prepared.append((source, mesh, refno))

    vertices_out: list[list[float]] = []
    triangles_out: list[list[int]] = []
    source_ranges: list[dict[str, Any]] = []
    for index, (source, mesh, refno) in enumerate(prepared):
        vertices, triangles = mesh["vertices"], mesh["triangles"]
        validation = validate_triangle_mesh(vertices, triangles)
        if validation.get("complete") is not True or validation.get("valid") is not True:
            return _failure("closed_valid_outward_source_mesh_required",
                            complete=validation.get("complete") is True,
                            source_index=index, source_refno=refno, validation=validation)
        vertex_start, triangle_start = len(vertices_out), len(triangles_out)
        vertices_out.extend([[float(value) for value in point] for point in vertices])
        triangles_out.extend([[value + vertex_start for value in face] for face in triangles])
        source_ranges.append({
            "source_index": index, "refno": refno, "resolved_type": source["resolved_type"],
            "vertex_range": [vertex_start, len(vertices_out)],
            "triangle_range": [triangle_start, len(triangles_out)],
            "vertex_count": len(vertices), "triangle_count": len(triangles),
            "component_count": validation["checks"]["component_count"],
        })

    # Sources can be valid individually but too small relative to the combined
    # spatial extent for the drawing validator's scale-relative tolerances.
    validation = validate_triangle_mesh(vertices_out, triangles_out)
    if validation.get("complete") is not True or validation.get("valid") is not True:
        return _failure("combined_mesh_not_suitable_for_drawing", complete=validation.get("complete") is True,
                        validation=validation)
    return {
        "success": True, "available": True, "complete": True, "errors": [],
        "coordinate_space": "WORLD", "coordinate_frame": WORLD_FRAME, "coordinate_unit": "mm",
        "native_tessellation": False, "cuts_applied": False, "vertices_welded": False,
        "boolean_union_applied": False, "nested_shell_semantics_inferred": False,
        "vertices": vertices_out, "triangles": triangles_out, "source_ranges": source_ranges,
        "range_convention": "zero-based half-open [start, end) in merged arrays",
        "counts": {"sources": len(sources), "vertices": len(vertices_out), "triangles": len(triangles_out)},
        "validation": validation,
        "scope": "Independent indexed source shells in a shared WORLD/mm frame; no welding, Boolean operations, intersection checks or native tessellation.",
    }

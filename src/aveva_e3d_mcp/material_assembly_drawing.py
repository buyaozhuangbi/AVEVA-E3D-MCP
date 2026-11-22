"""Joint drawings of independent, validated WORLD/mm material boundaries.

Parameter meshes remain strict outward shells. Owner-member differences retain
only cavity shells verified by the existing Mesh64 receipt validator. Sources
are concatenated without welding, union, intersection or native-cut inference.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import json
import math
import re
from typing import Any

from .orthographic_drawing import _generate_orthographic_drawing_from_validated_mesh, _make_svg
from .primitive_csg_drawing import _validate_primitive_csg_material_boundary
from .primitive_csg_mesh import csg_geometry_sha256
from .triangle_mesh_validation import MAX_TRIANGLES, MAX_VERTICES, validate_triangle_mesh


MAX_MATERIAL_DRAWING_SOURCES = 128
WORLD_FRAME = "WORLD coordinates; millimetres"
_FOOTER = "PARAMETER ASSEMBLY - NATIVE CUT SCOPE UNVERIFIED"
_KIND = "parameter material assembly linework"
_MODES = {"parameter_mesh", "owner_member_difference"}
_TYPES = {"PANE", "BOX", "CYLI", "CONE", "DISH"}
_CHECKS = ("finite_coordinates", "valid_indices", "nondegenerate_triangles", "duplicate_faces",
           "edge_manifold", "vertex_manifold", "winding_consistent", "closed_manifold")
_AGGREGATE_PROVENANCE = "independently validated source material boundaries; indexed concatenation without Boolean union"
_HASH_SCOPE = "canonical ordered vertices and triangles; metadata excluded"
_AGGREGATE_FIELDS = (
    "empty", "native_tessellation", "native_cut_application_verified", "boolean_union_applied",
    "vertices_welded", "source_intersections_checked", "global_negative_cut_scope_verified",
    "cuts_applied", "source_parameter_boolean_evaluated", "native_csg_evaluated",
    "material_boundary_validated", "closed_manifold", "winding_consistent",
)


def _failure(code: str, **evidence: Any) -> dict[str, Any]:
    return {**evidence, "success": False, "available": False, "complete": False,
            "error_code": code, "errors": [code], "drawing_kind": _KIND,
            "coordinate_space": "WORLD", "coordinate_frame": WORLD_FRAME, "coordinate_unit": "mm",
            "vertices": [], "triangles": [], "source_ranges": [], "views": [],
            "counts": {"sources": 0, "vertices": 0, "triangles": 0},
            "scope": _FOOTER, "footer": _FOOTER}


def _reference(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 128 or re.fullmatch(r"=[0-9]+/[0-9]+", value) is None:
        return None
    return "=" + "/".join(part.lstrip("0") or "0" for part in value[1:].split("/"))


def _world_metadata(source: Mapping[str, Any], mesh: Mapping[str, Any]) -> bool:
    # Legacy parameter producers omit mesh coordinate_space/unit. Their exact
    # frame string explicitly supplies both; contradictory optional fields fail.
    return (source.get("coordinate_space") == "WORLD" and mesh.get("coordinate_frame") == WORLD_FRAME
            and all(name not in source or source[name] == value for name, value in (
                ("coordinate_frame", WORLD_FRAME), ("coordinate_unit", "mm")))
            and all(name not in mesh or mesh[name] == value for name, value in (
                ("coordinate_space", "WORLD"), ("coordinate_unit", "mm"))))


def _identity_consistent(source: Mapping[str, Any], mesh: Mapping[str, Any], refno: str,
                         source_type: str) -> bool:
    if _reference(mesh.get("refno")) != refno:
        return False
    if "expected_type" in source and source["expected_type"] != source_type:
        return False
    if "shape" in mesh and mesh["shape"] != source_type:
        return False
    for native in (source.get("source_geometry"), mesh.get("source_geometry")):
        if isinstance(native, Mapping) and "element" in native:
            element = native["element"]
            if not isinstance(element, Mapping) or _reference(element.get("refno")) != refno:
                return False
            native_type = element.get("type")
            if native_type != source_type and not (source_type == "PANE" and native_type == "PANEL"):
                return False
    return True


def _component_memberships(triangles: Sequence[Sequence[int]]) -> list[tuple[int, ...]]:
    """Enumerate edge-connected face sets in the validator's seed order."""
    parents = list(range(len(triangles)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    first_use: dict[tuple[int, int], int] = {}
    for face_index, face in enumerate(triangles):
        for left, right in zip(face, (*face[1:], face[0])):
            edge = (left, right) if left < right else (right, left)
            other = first_use.setdefault(edge, face_index)
            first, second = root(face_index), root(other)
            if first != second:
                parents[first] = second
    groups: dict[int, list[int]] = {}
    for face_index in range(len(triangles)):
        groups.setdefault(root(face_index), []).append(face_index)
    return sorted((tuple(group) for group in groups.values()), key=lambda group: group[0])


def _material_inward_components(validation: Mapping[str, Any],
                                memberships: Sequence[Sequence[int]],
                                triangles: Sequence[Sequence[int]]) -> list[int] | None:
    checks, findings, components = validation.get("checks"), validation.get("findings"), validation.get("components")
    if (any(validation.get(flag) is not True for flag in ("success", "available", "complete"))
            or validation.get("findings_truncated") is not False or not isinstance(checks, Mapping)
            or any(checks.get(flag) is not True for flag in _CHECKS)
            or not isinstance(findings, list) or any(
                not isinstance(finding, Mapping) or finding.get("code") != "component_winding_is_inward"
                for finding in findings)
            or not isinstance(components, list) or not components
            or len(components) != len(memberships) or checks.get("component_count") != len(memberships)):
        return None
    inward: list[int] = []
    for index, (component, members) in enumerate(zip(components, memberships)):
        signed = component.get("signed_volume") if isinstance(component, Mapping) else None
        if (not isinstance(component, Mapping) or component.get("component") != index
                or component.get("closed") is not True or component.get("winding_consistent") is not True
                or component.get("volume_available") is not True or isinstance(signed, bool)
                or not isinstance(signed, (int, float)) or not math.isfinite(signed) or signed == 0
                or component.get("orientation") != ("outward" if signed > 0 else "inward")
                or component.get("triangle_count") != len(members)
                or component.get("vertex_count") != len({vertex for face in members for vertex in triangles[face]})):
            return None
        if signed < 0:
            inward.append(index)
    if [finding.get("component") for finding in findings] != inward:
        return None
    return inward


def _empty_validation() -> dict[str, Any]:
    return {"success": True, "available": True, "complete": True, "valid": True, "empty": True,
            "coordinate_unit": "mm", "volume_unit": "mm3",
            "counts": {"vertices": 0, "triangles": 0, "valid_triangles": 0},
            "checks": {**{name: True for name in _CHECKS}, "orientation_outward": True, "component_count": 0},
            "components": [], "findings": [], "findings_truncated": False,
            "scope": "Empty assembly of independently verified empty material sources; no boundary shells."}


def combine_material_drawing_meshes(sources: Any) -> dict[str, Any]:
    """Concatenate 1–128 complete mesh result records plus ``geometry_mode``.

    ``parameter_mesh`` uses the strict closed/outward contract. An
    ``owner_member_difference`` must pass the existing no-projection Mesh64
    material receipt checks. Ranges are zero-based and half-open. No caller
    geometry or evidence is mutated or shared with the returned record.
    """
    if not isinstance(sources, (list, tuple)) or not 1 <= len(sources) <= MAX_MATERIAL_DRAWING_SOURCES:
        return _failure("material_drawing_sources_must_contain_one_to_128_elements")
    prepared: list[tuple[Mapping[str, Any], Mapping[str, Any], str, str, str]] = []
    seen: set[str] = set()
    total_vertices = total_triangles = 0
    for index, source in enumerate(sources):
        if not isinstance(source, Mapping):
            return _failure("invalid_material_drawing_source", source_index=index)
        if any(source.get(flag) is not True for flag in ("success", "available", "complete")):
            return _failure("material_drawing_source_unavailable_or_incomplete", source_index=index)
        mode = source.get("geometry_mode")
        if not isinstance(mode, str) or mode not in _MODES:
            return _failure("material_drawing_source_geometry_mode_required", source_index=index)
        refno, source_type = _reference(source.get("resolved_refno")), source.get("resolved_type")
        if refno is None:
            return _failure("material_drawing_source_resolved_reference_required", source_index=index)
        if refno in seen:
            return _failure("duplicate_material_drawing_source", source_index=index, source_refno=refno)
        seen.add(refno)
        if (not isinstance(source_type, str) or source_type not in _TYPES
                or mode == "owner_member_difference" and source_type == "PANE"):
            return _failure("unsupported_material_drawing_source_type", source_index=index, source_refno=refno)
        mesh = source.get("derived_mesh")
        if not isinstance(mesh, Mapping):
            return _failure("material_drawing_source_mesh_missing", source_index=index, source_refno=refno)
        if any(mesh.get(flag) is not True for flag in ("success", "available", "complete")):
            return _failure("material_drawing_source_mesh_unavailable_or_incomplete", source_index=index, source_refno=refno)
        if not _identity_consistent(source, mesh, refno, source_type):
            return _failure("material_drawing_source_mesh_identity_mismatch", source_index=index, source_refno=refno)
        if not _world_metadata(source, mesh):
            return _failure("material_drawing_source_world_millimetres_required", source_index=index, source_refno=refno)
        if (mesh.get("native_tessellation") is not False
                or mesh.get("cuts_applied") is not (mode == "owner_member_difference")):
            return _failure("material_drawing_source_mode_mismatch", source_index=index, source_refno=refno)
        difference = mode == "owner_member_difference"
        if (any(name in source and source[name] is not value for name, value in (
                ("native_tessellation", False), ("native_cut_application_verified", False),
                ("cuts_applied", difference), ("csg_evaluated", difference)))
                or "empty" in source and source["empty"] is not mesh.get("empty", False)
                or "mesh_detail" in source and source["mesh_detail"] != "full"
                or mesh.get("geometry_arrays_included", True) is not True
                or mode == "parameter_mesh" and any(mesh.get(name, False) is not False
                                                   for name in ("csg_evaluated", "parameter_boolean_evaluated"))):
            return _failure("material_drawing_source_mode_mismatch", source_index=index, source_refno=refno)
        vertices, triangles = mesh.get("vertices"), mesh.get("triangles")
        if not isinstance(vertices, (list, tuple)) or not isinstance(triangles, (list, tuple)):
            return _failure("material_drawing_source_geometry_arrays_required", source_index=index, source_refno=refno)
        counts = mesh.get("counts")
        if "counts" in mesh and (not isinstance(counts, Mapping)
                or type(counts.get("vertices")) is not int or counts["vertices"] != len(vertices)
                or type(counts.get("triangles")) is not int or counts["triangles"] != len(triangles)):
            return _failure("material_drawing_source_geometry_counts_mismatch", source_index=index, source_refno=refno)
        total_vertices += len(vertices)
        total_triangles += len(triangles)
        if total_vertices > MAX_VERTICES or total_triangles > MAX_TRIANGLES:
            return _failure("material_drawing_assembly_mesh_limit_exceeded", max_vertices=MAX_VERTICES,
                            max_triangles=MAX_TRIANGLES,
                            observed_counts={"sources": len(sources), "vertices": total_vertices, "triangles": total_triangles})
        prepared.append((source, mesh, refno, source_type, mode))

    vertices_out: list[list[float]] = []
    triangles_out: list[list[int]] = []
    source_ranges: list[dict[str, Any]] = []
    expected_memberships: list[tuple[int, ...]] = []
    cavity_map: list[dict[str, int]] = []
    expected_inward: list[int] = []
    any_difference = False
    for index, (_source, mesh, refno, source_type, mode) in enumerate(prepared):
        vertices, triangles = mesh["vertices"], mesh["triangles"]
        empty = False
        if mode == "owner_member_difference":
            receipt = _validate_primitive_csg_material_boundary(mesh)
            if receipt.get("success") is not True:
                return _failure("material_drawing_source_csg_material_invalid", source_index=index,
                                source_refno=refno, source_error_code=receipt.get("error_code"),
                                source_validation=deepcopy(receipt.get("validation")))
            validation, empty = receipt["validation"], receipt["empty"]
            source_cavities = list(mesh["material_boundary_validation"]["cavity_shells"])
            any_difference = True
        else:
            validation = validate_triangle_mesh(vertices, triangles)
            if (mesh.get("closed_manifold") is not True or mesh.get("empty", False) is not False
                    or any(validation.get(flag) is not True for flag in ("success", "available", "complete", "valid"))):
                return _failure("closed_valid_outward_source_mesh_required", source_index=index,
                                source_refno=refno, source_validation=validation)
            source_cavities = []
        try:
            source_digest = csg_geometry_sha256(vertices, triangles)
        except (TypeError, ValueError, OverflowError):
            return _failure("material_drawing_source_geometry_hash_unavailable", source_index=index, source_refno=refno)
        if (any(name in mesh and mesh[name] != source_digest for name in ("geometry_sha256", "source_mesh_sha256"))
                or any(name in _source and _source[name] != source_digest for name in ("geometry_sha256", "source_mesh_sha256"))):
            return _failure("material_drawing_source_geometry_hash_mismatch", source_index=index, source_refno=refno)
        memberships = _component_memberships(triangles) if triangles else []
        if not empty and _material_inward_components(validation, memberships, triangles) != source_cavities:
            return _failure("material_drawing_source_component_proof_mismatch", source_index=index, source_refno=refno)
        vertex_start, triangle_start = len(vertices_out), len(triangles_out)
        component_start = len(expected_memberships)
        vertices_out.extend([list(point) for point in vertices])
        triangles_out.extend([[vertex + vertex_start for vertex in face] for face in triangles])
        expected_memberships.extend(tuple(face + triangle_start for face in members) for members in memberships)
        combined_cavities = [component_start + component for component in source_cavities]
        expected_inward.extend(combined_cavities)
        cavity_map.extend({"source_index": index, "source_component": source_component,
                           "combined_component": combined_component}
                          for source_component, combined_component in zip(source_cavities, combined_cavities))
        source_range = {
            "source_index": index, "refno": refno, "resolved_type": source_type, "geometry_mode": mode,
            "source_identity": {"refno": refno, "resolved_type": source_type},
            "geometry_sha256": source_digest, "source_mesh_sha256": source_digest,
            "cuts_applied": mode == "owner_member_difference", "empty": empty,
            "vertex_range": [vertex_start, len(vertices_out)], "triangle_range": [triangle_start, len(triangles_out)],
            "vertex_count": len(vertices), "triangle_count": len(triangles),
            "component_range": [component_start, len(expected_memberships)], "component_count": len(memberships),
            "source_cavity_shells": source_cavities, "cavity_shells": combined_cavities,
            "component_map": [{"source_component": component, "combined_component": component_start + component,
                               "triangle_indices": [face + triangle_start for face in members]}
                              for component, members in enumerate(memberships)],
            "source_validation": deepcopy(validation),
        }
        if mode == "owner_member_difference":
            source_range.update(engine=deepcopy(dict(mesh["engine"])),
                                material_boundary_validation=deepcopy(dict(mesh["material_boundary_validation"])))
        source_ranges.append(source_range)

    empty = not vertices_out and not triangles_out
    validation = _empty_validation() if empty else validate_triangle_mesh(vertices_out, triangles_out)
    if not empty:
        combined_memberships = _component_memberships(triangles_out)
        inward = _material_inward_components(validation, combined_memberships, triangles_out)
        if combined_memberships != expected_memberships or inward != expected_inward:
            return _failure("combined_material_boundary_invalid", validation=validation)
    return {
        "success": True, "available": True, "complete": True, "errors": [], "empty": empty,
        "coordinate_space": "WORLD", "coordinate_frame": WORLD_FRAME, "coordinate_unit": "mm",
        "drawing_kind": _KIND, "native_tessellation": False, "native_cut_application_verified": False,
        "boolean_union_applied": False, "vertices_welded": False, "source_intersections_checked": False,
        "global_negative_cut_scope_verified": False, "native_csg_evaluated": False,
        "cuts_applied": any_difference, "source_parameter_boolean_evaluated": any_difference,
        "material_boundary_validated": True, "closed_manifold": True, "winding_consistent": True,
        "vertices": vertices_out, "triangles": triangles_out, "source_ranges": source_ranges,
        "range_convention": "zero-based half-open [start, end) in combined arrays",
        "counts": {"sources": len(sources), "vertices": len(vertices_out), "triangles": len(triangles_out)},
        "geometry_sha256": csg_geometry_sha256(vertices_out, triangles_out), "geometry_hash_scope": _HASH_SCOPE,
        "validation": validation,
        "material_boundary_validation": {"validated": True, "provenance": _AGGREGATE_PROVENANCE,
            "empty": empty, "closed_manifold": True, "winding_consistent": True,
            "cavity_shells": expected_inward, "source_cavity_map": cavity_map},
        "scope": _FOOTER, "footer": _FOOTER,
        "scope_detail": "Independent source material boundaries in WORLD/mm; no welding, Boolean union, intersection checks, global negative-cut inference or native tessellation.",
    }


def _range(value: Any, start: int, maximum: int) -> tuple[int, int] | None:
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or any(type(item) is not int for item in value)
            or value[0] != start or not start <= value[1] <= maximum):
        return None
    return value[0], value[1]


def _same_evidence(left: Any, right: Any) -> bool:
    # JSON scalar types remain distinct: True cannot replace component 1, and
    # False cannot replace an empty count or the first source index.
    try:
        options = {"sort_keys": True, "separators": (",", ":"), "allow_nan": False}
        return json.dumps(left, **options) == json.dumps(right, **options)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False


def _revalidate_combined(combined: Any) -> dict[str, Any]:
    if (not isinstance(combined, Mapping)
            or any(combined.get(flag) is not True for flag in ("success", "available", "complete"))):
        return _failure("successful_material_drawing_assembly_required")
    if (combined.get("coordinate_space") != "WORLD" or combined.get("coordinate_frame") != WORLD_FRAME
            or combined.get("coordinate_unit") != "mm"):
        return _failure("material_drawing_assembly_world_millimetres_required")
    vertices, triangles = combined.get("vertices"), combined.get("triangles")
    if not isinstance(vertices, (list, tuple)) or not isinstance(triangles, (list, tuple)):
        return _failure("material_drawing_assembly_geometry_arrays_required")
    if len(vertices) > MAX_VERTICES or len(triangles) > MAX_TRIANGLES:
        return _failure("material_drawing_assembly_mesh_limit_exceeded")
    digest = combined.get("geometry_sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return _failure("material_assembly_geometry_hash_required")
    try:
        observed = csg_geometry_sha256(vertices, triangles)
    except (TypeError, ValueError, OverflowError):
        return _failure("material_assembly_geometry_hash_unavailable")
    if observed != digest:
        return _failure("material_assembly_geometry_hash_mismatch")
    ranges = combined.get("source_ranges")
    if not isinstance(ranges, list) or not 1 <= len(ranges) <= MAX_MATERIAL_DRAWING_SOURCES:
        return _failure("material_assembly_source_ranges_required")
    sources: list[dict[str, Any]] = []
    vertex_cursor = triangle_cursor = 0
    for index, item in enumerate(ranges):
        if (not isinstance(item, Mapping) or type(item.get("source_index")) is not int
                or item["source_index"] != index):
            return _failure("material_assembly_source_range_invalid", source_index=index)
        vr = _range(item.get("vertex_range"), vertex_cursor, len(vertices))
        tr = _range(item.get("triangle_range"), triangle_cursor, len(triangles))
        if vr is None or tr is None:
            return _failure("material_assembly_source_range_invalid", source_index=index)
        source_triangles = triangles[tr[0]:tr[1]]
        if any(not isinstance(face, (list, tuple)) or len(face) != 3 or any(
                type(vertex) is not int or not vr[0] <= vertex < vr[1] for vertex in face)
               for face in source_triangles):
            return _failure("material_assembly_source_range_geometry_mismatch", source_index=index)
        mode = item.get("geometry_mode")
        identity = item.get("source_identity")
        if not isinstance(identity, Mapping):
            return _failure("material_assembly_source_identity_required", source_index=index)
        mesh = {"success": True, "available": True, "complete": True, "refno": identity.get("refno"),
                "coordinate_space": "WORLD", "coordinate_frame": WORLD_FRAME, "coordinate_unit": "mm",
                "native_tessellation": False, "cuts_applied": item.get("cuts_applied"), "closed_manifold": True,
                "empty": item.get("empty"), "vertices": [list(point) if isinstance(point, (list, tuple)) else point
                                                           for point in vertices[vr[0]:vr[1]]],
                "triangles": [[vertex - vr[0] for vertex in face] for face in source_triangles]}
        if mode == "owner_member_difference":
            mesh.update(csg_evaluated=True, material_boundary_validated=True, native_cut_application_verified=False,
                        geometry_sha256=item.get("source_mesh_sha256"), engine=deepcopy(item.get("engine")),
                        material_boundary_validation=deepcopy(item.get("material_boundary_validation")),
                        validation=deepcopy(item.get("source_validation")))
        sources.append({"success": True, "available": True, "complete": True, "coordinate_space": "WORLD",
                        "resolved_refno": item.get("refno"), "resolved_type": item.get("resolved_type"),
                        "expected_type": identity.get("resolved_type"), "geometry_mode": mode, "derived_mesh": mesh})
        vertex_cursor, triangle_cursor = vr[1], tr[1]
    if vertex_cursor != len(vertices) or triangle_cursor != len(triangles):
        return _failure("material_assembly_source_ranges_incomplete")
    verified = combine_material_drawing_meshes(sources)
    if verified.get("success") is not True:
        return _failure("material_assembly_source_proof_invalid", source_error_code=verified.get("error_code"),
                        source_index=verified.get("source_index"), source_validation=verified.get("source_validation"))
    if (any(combined.get(name) is not verified[name] for name in _AGGREGATE_FIELDS)
            or any(not _same_evidence(combined.get(name), verified[name]) for name in (
                "counts", "source_ranges", "validation", "material_boundary_validation", "geometry_sha256",
                "range_convention", "geometry_hash_scope", "scope", "footer"))
            or "engine" in combined):
        return _failure("material_assembly_provenance_mismatch")
    return verified


def generate_material_assembly_drawing(
    combined: Mapping[str, Any],
    *,
    views: Sequence[str] = ("FRONT", "TOP", "SIDE"),
    feature_angle_degrees: float = 30.0,
    hidden_line_removal: bool = True,
    show_hidden_lines: bool = False,
    projection_convention: str = "legacy",
) -> dict[str, Any]:
    """Revalidate a combined receipt, then jointly project all source shells.

    Visibility is evaluated against triangles from every nonempty source.
    Every retained stroke is attributed using both original edge vertex
    indices. Verified empty sources keep their ranges; an all-empty assembly
    succeeds without views or SVG.
    """
    if (not isinstance(views, (list, tuple)) or not 1 <= len(views) <= 3
            or any(not isinstance(view, str) or view not in {"FRONT", "TOP", "SIDE"} for view in views)):
        return _failure("unsupported_orthographic_views")
    if len(set(views)) != len(views):
        return _failure("duplicate_orthographic_view")
    if isinstance(feature_angle_degrees, bool) or not isinstance(feature_angle_degrees, (int, float)):
        return _failure("invalid_feature_angle")
    try:
        threshold = float(feature_angle_degrees)
    except (OverflowError, ValueError):
        return _failure("invalid_feature_angle")
    if not math.isfinite(threshold) or not 0 <= threshold <= 180:
        return _failure("invalid_feature_angle")
    if type(hidden_line_removal) is not bool:
        return _failure("invalid_hidden_line_removal")
    if type(show_hidden_lines) is not bool:
        return _failure("invalid_show_hidden_lines")
    if show_hidden_lines and not hidden_line_removal:
        return _failure("hidden_line_display_requires_removal")
    if not isinstance(projection_convention, str) or projection_convention not in {"legacy", "right_handed"}:
        return _failure("invalid_projection_convention")
    verified = _revalidate_combined(combined)
    if verified.get("success") is not True:
        return verified
    metadata = {key: deepcopy(value) for key, value in verified.items()
                if key not in {"vertices", "triangles", "success", "available", "complete"}}
    if verified["empty"]:
        return {"success": True, "available": True, "complete": True, **metadata, "views": [],
                "segment_count": 0, "hidden_line_removal": hidden_line_removal, "show_hidden_lines": show_hidden_lines,
                **({"projection_convention": projection_convention} if projection_convention != "legacy" else {})}
    try:
        drawing = _generate_orthographic_drawing_from_validated_mesh(
            verified["vertices"], verified["triangles"], views=views, feature_angle_degrees=threshold,
            coordinate_frame=WORLD_FRAME, coordinate_unit="mm", hidden_line_removal=hidden_line_removal,
            show_hidden_lines=show_hidden_lines, projection_convention=projection_convention,
            _material_boundary_prevalidated=True)
        if drawing.get("success") is not True:
            return _failure(drawing.get("error_code", "material_assembly_projection_failed"),
                            projection_evidence={key: value for key, value in drawing.items()
                                                 if key not in {"views", "svg", "vertices", "triangles"}})
        owners = [-1] * len(verified["vertices"])
        for item in verified["source_ranges"]:
            start, end = item["vertex_range"]
            owners[start:end] = [item["source_index"]] * (end - start)
        for view in drawing["views"]:
            for line in view["lines"]:
                edge = line.get("edge")
                if (not isinstance(edge, (list, tuple)) or len(edge) != 2
                        or any(type(vertex) is not int or not 0 <= vertex < len(owners) for vertex in edge)
                        or owners[edge[0]] < 0 or owners[edge[0]] != owners[edge[1]]):
                    return _failure("material_assembly_line_source_attribution_failed")
                item = verified["source_ranges"][owners[edge[0]]]
                line.update(source_index=item["source_index"], source_refno=item["refno"],
                            source_geometry_mode=item["geometry_mode"])
        drawing.update(metadata)
        drawing["svg"] = _make_svg(drawing["views"], hidden_line_removal=hidden_line_removal,
                                   show_hidden_lines=show_hidden_lines, footer=_FOOTER)
        return drawing
    except (TypeError, ValueError, OverflowError, KeyError, IndexError) as exc:
        return _failure("material_assembly_projection_failed", projection_error=str(exc)[:1024])

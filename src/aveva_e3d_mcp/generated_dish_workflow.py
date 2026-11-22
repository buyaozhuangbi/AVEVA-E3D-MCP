"""Reconstruct one standalone Design DISH using its audited BasicDIS branch.

Generated CSG independently witnesses identity/dimensions/placement and an opaque
0/1 export marker. Its third RData value is never a millimetre radius. Physical
profile values come from the installed Core3D BasicDIS Design-DISH branch.
No catalogue-DISH decoding, Boolean result or native tessellation is claimed.
"""
from __future__ import annotations
from copy import deepcopy
import math
import re
from typing import Any
from .generated_csg import validate_generated_csg_response
from .generated_dish_mesh import derive_generated_dish_mesh

_NUMERIC_REFERENCE = re.compile(r"=([0-9]+)/([0-9]+)")
_REVISION = re.compile(r"[a-fA-F0-9]{64}")
_FRAME_TOLERANCE = 1e-8
_PROFILE_SOURCE = "installed_core3d_basicdis_design_dish_branch"


class GeneratedDishWorkflowError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise GeneratedDishWorkflowError(code)


def _finite_number(value: Any, code: str) -> float:
    _require(not isinstance(value, bool) and isinstance(value, (int, float)), code)
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise GeneratedDishWorkflowError(code) from None
    _require(math.isfinite(number), code)
    return number


def _numeric_ref(value: Any) -> tuple[int, int] | None:
    match = _NUMERIC_REFERENCE.fullmatch(value) if isinstance(value, str) else None
    return tuple(int(part) for part in match.groups()) if match else None


def _same_reference(requested: str, identity: Any) -> bool:
    if not isinstance(identity, dict) or _numeric_ref(identity.get("refno")) is None:
        return False
    if requested.startswith("="):
        return _numeric_ref(requested) == _numeric_ref(identity["refno"])
    name = identity.get("name")
    return isinstance(name, str) and requested.casefold() == ("/" + name.lstrip("/")).casefold()


def _copy(value: Any) -> Any:
    try:
        return deepcopy(value)
    except Exception:
        return {"unavailable": True, "type": type(value).__name__}


def _error_observation(exc: Exception) -> dict[str, Any]:
    result = {"error_type": type(exc).__name__, "message": str(exc)[:2048]}
    if getattr(exc, "diagnostics", None) is not None:
        result["diagnostics"] = _copy(exc.diagnostics)
    return result


def _observe(evidence: dict[str, Any], key: str, callback: Any) -> Any:
    try:
        value = callback()
    except Exception as exc:
        evidence[key] = {"read_exception": _error_observation(exc)}
        raise
    evidence[key] = _copy(value)
    return value


def _read_context(bridge: Any, evidence: dict[str, Any], key: str) -> dict[str, Any]:
    observed = {"health": None, "project": None, "signature": None}
    evidence[key] = observed
    health = _observe(observed, "health", bridge.health)
    _require(isinstance(health, dict), "generated_dish_context_unavailable")
    state = health.get("state")
    _require(isinstance(state, dict), "generated_dish_context_unavailable")
    fault, quarantine = state.get("native_database_fault"), state.get("generated_csg_quarantine")
    _require(health.get("ok") is True and health.get("connected") is True
             and health.get("version") == "E3D21"
             and isinstance(health.get("session_id"), str) and bool(health["session_id"].strip())
             and state.get("database_ready") is True and state.get("busy") is False
             and state.get("recovery_required") is False
             and isinstance(fault, dict) and fault.get("has_fault") is False
             and (quarantine is None or isinstance(quarantine, dict) and quarantine.get("active") is False),
             "generated_dish_context_unavailable")
    # Health/fault failure must stop before issuing another native SDK read.
    project = _observe(observed, "project", bridge.project)
    _require(isinstance(project, dict) and project.get("ok") is True
             and project.get("version") == "E3D21", "generated_dish_context_unavailable")
    value = project.get("project")
    _require(isinstance(value, dict) and value.get("connected") is True
             and value.get("recovery_only") is False
             and all(isinstance(value.get(field), str) and bool(value[field].strip()) for field in ("name", "mdb")),
             "generated_dish_context_unavailable")
    if "session_id" in project:
        _require(project["session_id"] == health["session_id"], "generated_dish_context_changed")
    observed["signature"] = {"session_id": health["session_id"], "version": health["version"],
                             "project": value["name"], "mdb": value["mdb"]}
    return observed["signature"]


def _world_array12(frame: Any) -> list[float]:
    _require(isinstance(frame, dict) and isinstance(frame.get("world"), dict),
             "generated_dish_world_frame_incomplete")
    world = frame["world"]
    orientation = world.get("orientation")
    _require(isinstance(orientation, dict), "generated_dish_world_frame_incomplete")
    def vector(raw: Any) -> list[float]:
        _require(isinstance(raw, (list, tuple)) and len(raw) == 3, "generated_dish_world_frame_incomplete")
        return [_finite_number(member, "generated_dish_world_frame_nonfinite") for member in raw]
    origin = vector(world.get("position"))
    axes = [vector(orientation.get(key)) for key in ("x_axis", "y_axis", "z_axis")]
    gram = [[math.fsum(left[i] * right[i] for i in range(3)) for right in axes] for left in axes]
    x, y, z = axes
    cross = [x[1] * y[2] - x[2] * y[1], x[2] * y[0] - x[0] * y[2], x[0] * y[1] - x[1] * y[0]]
    _require(all(abs(gram[i][j] - (1.0 if i == j else 0.0)) <= _FRAME_TOLERANCE
                 for i in range(3) for j in range(3))
             and abs(math.fsum(cross[i] * z[i] for i in range(3)) - 1.0) <= _FRAME_TOLERANCE,
             "generated_dish_world_frame_not_rigid")
    return [*x, *y, *z, *origin]


def _primitive(response: Any, requested: str, context: dict[str, Any], stage: str) -> tuple[dict[str, Any], str]:
    _require(isinstance(response, dict) and response.get("ok") is True, f"generated_dish_{stage}_read_failed")
    _require(response.get("version") == context["version"], "generated_dish_primitive_version_mismatch")
    if "session_id" in response:
        _require(response["session_id"] == context["session_id"], "generated_dish_primitive_session_mismatch")
    result = response.get("result")
    _require(isinstance(result, dict), f"generated_dish_{stage}_geometry_missing")
    identity = result.get("element")
    _require(isinstance(identity, dict) and _numeric_ref(identity.get("refno")) is not None,
             f"generated_dish_{stage}_identity_missing")
    _require(_same_reference(requested, identity), f"generated_dish_{stage}_identity_mismatch")
    _require(identity.get("type") == "DISH" and result.get("shape") == "DISH", "generated_dish_source_type_mismatch")
    _require(result.get("success") is True and result.get("complete") is True
             and result.get("partial", False) is False and result.get("available", True) is True,
             f"generated_dish_{stage}_geometry_incomplete")
    _require("read_errors" in result and result["read_errors"] in ({}, []), f"generated_dish_{stage}_geometry_read_errors")
    _require(result.get("negative") is False and result.get("is_negative") is False, "generated_dish_positive_parent_required")
    _require(result.get("unit") == "mm", "generated_dish_native_unit_mismatch")
    _require(result.get("negative_children") == [], "generated_dish_negative_children_present")
    _require(result.get("children", []) == [], "generated_dish_children_present")
    target = result.get("target_geometry")
    _require(isinstance(target, dict) and target.get("complete") is True and "read_errors" in target
             and target["read_errors"] in ({}, []) and target.get("member_refnos") == []
             and target.get("negative_children") == [], "generated_dish_membership_unproven")
    _require(all(target.get(field) == result.get(field)
                 for field in ("element", "shape", "unit", "dimensions", "frame", "negative", "is_negative")),
             "generated_dish_target_geometry_mismatch")
    if "member_refnos" in result:
        _require(result["member_refnos"] == [], "generated_dish_membership_unproven")
    dimensions = result.get("dimensions")
    _require(isinstance(dimensions, dict), "generated_dish_dimensions_missing")
    for field in ("DIAM", "HEIG", "RADI"):
        _finite_number(dimensions.get(field), "generated_dish_dimensions_missing")
    _require(dimensions["DIAM"] > 0 and dimensions["HEIG"] > 0 and dimensions["RADI"] >= 0,
             "generated_dish_dimensions_invalid")
    _world_array12(result.get("frame"))
    revision = result.get("revision")
    _require(isinstance(revision, str) and _REVISION.fullmatch(revision) is not None,
             f"generated_dish_{stage}_revision_missing")
    return result, identity["refno"]


def _match_scalar(left: float, right: float) -> bool:
    return abs(left - right) <= max(1e-6, 32.0 * max(math.ulp(left), math.ulp(right)))


def _descriptor(response: Any, resolved: str, source: dict[str, Any], context: dict[str, Any],
                evidence: dict[str, Any]) -> tuple[list[float], dict[str, Any]]:
    validated = validate_generated_csg_response(response, resolved, max_trees=1, max_items=1)
    evidence["generated_csg_validated"] = _copy(validated)
    value = validated.get("result") if isinstance(validated, dict) else None
    _require(isinstance(validated, dict) and validated.get("ok") is True and isinstance(value, dict)
             and value.get("success") is True and value.get("complete") is True, "generated_dish_descriptor_invalid")
    _require(validated.get("session_id") == context["session_id"], "generated_dish_descriptor_session_mismatch")
    native_context = value.get("context", {})
    _require(all(native_context.get(field + suffix) == context[field]
                 for field in ("project", "mdb") for suffix in ("_before", "_after")),
             "generated_dish_descriptor_context_mismatch")
    _require(value.get("source", {}).get("type") == "DISH" and _same_reference(resolved, value.get("source")),
             "generated_dish_descriptor_source_mismatch")
    _require(value.get("source_subtree_before") == [] and value.get("source_subtree_after") == [],
             "generated_dish_descriptor_members_present")
    trees = value["trees"]
    _require(len(trees) == 1 and trees[0].get("source_matches_requested") is True
             and trees[0].get("source", {}).get("type") == "DISH" and _same_reference(resolved, trees[0].get("source"))
             and trees[0].get("complete") is True and trees[0].get("item_count") == 1
             and len(trees[0]["items"]) == 1, "generated_dish_descriptor_single_item_required")
    item = trees[0]["items"][0]
    _require(item.get("type") == {"name": "CSG_DISH", "value": 9, "known": True},
             "generated_dish_descriptor_positive_dish_required")
    d = source["dimensions"]
    limits = item.get("local_limits")
    _require(isinstance(limits, dict) and set(limits) == {"min", "max", "frame"},
             "generated_dish_descriptor_local_limits_invalid")
    _require(limits["frame"] == "untransformed",
             "generated_dish_descriptor_local_limits_frame_mismatch")
    local_limits: dict[str, list[float]] = {}
    for field in ("min", "max"):
        raw_limits = limits[field]
        _require(isinstance(raw_limits, list) and len(raw_limits) == 3,
                 "generated_dish_descriptor_local_limits_invalid")
        local_limits[field] = [_finite_number(member, "generated_dish_descriptor_local_limits_nonfinite")
                               for member in raw_limits]
    expected_limits = {
        "min": [-float(d["DIAM"]) / 2.0, -float(d["DIAM"]) / 2.0, 0.0],
        "max": [float(d["DIAM"]) / 2.0, float(d["DIAM"]) / 2.0, float(d["HEIG"])],
    }
    _require(all(_match_scalar(observed, expected)
                 for field in ("min", "max")
                 for observed, expected in zip(local_limits[field], expected_limits[field])),
             "generated_dish_descriptor_local_limits_mismatch")
    raw = item.get("rdata")
    _require(isinstance(raw, list) and len(raw) == 3, "generated_dish_descriptor_rdata_required")
    raw = [_finite_number(member, "generated_dish_descriptor_rdata_nonfinite") for member in raw]
    # Opaque native observations, not an inferred integer-data format.
    _require(item.get("idata") == [0, 0], "generated_dish_descriptor_idata_unrecognized")
    transform = item.get("transform_as_array12")
    _require(isinstance(transform, list) and len(transform) == 12, "generated_dish_descriptor_transform_required")
    expected = _world_array12(source["frame"])
    _require(all(abs(transform[i] - expected[i]) <= _FRAME_TOLERANCE for i in range(9))
             and all(_match_scalar(transform[i], expected[i]) for i in range(9, 12)),
             "generated_dish_descriptor_transform_source_mismatch")
    options = value.get("options")
    _require(isinstance(options, dict) and options.get("comparison_policy") == "enabled_geometry_options_v1"
             and options.get("restored") is True and options.get("effective_restored") is True,
             "generated_dish_descriptor_options_not_restored")
    for field in ("before", "requested", "after", "effective_before", "effective_after"):
        _require(isinstance(options.get(field), dict), "generated_dish_descriptor_options_incomplete")
    requested_options = options["requested"]
    bare = {"hard_obstructions": False, "soft_obstructions": False, "level": 6,
            "center_line": False, "tube": True, "insulation": False, "obstruction": False,
            "insulation_translucency": 0, "obstruction_translucency": 0,
            "holes": True, "solid_only": False, "pline": False, "wire_line": False}
    _require(all(type(requested_options.get(key)) is type(expected_value)
                 and requested_options[key] == expected_value for key, expected_value in bare.items()),
             "generated_dish_descriptor_options_not_bare")
    _require(_match_scalar(raw[0] * 2.0, float(d["DIAM"])), "generated_dish_descriptor_radius_diam_mismatch")
    _require(_match_scalar(raw[1], float(d["HEIG"])), "generated_dish_descriptor_height_mismatch")
    positive = d["RADI"] > 0
    _require(raw[2] == (1.0 if positive else 0.0), "generated_dish_descriptor_marker_branch_mismatch")
    a, h = float(d["DIAM"]) / 2.0, float(d["HEIG"])
    # Core3D CSG_BasicDIS::getPrimGeom RVA 0x665240. RADI magnitude is discarded.
    knuckle = h / (1.0 + (a - h) / math.hypot(a, h)) if positive else 0.0
    profile = {"profile_kind": "torispherical" if positive else "spherical_cap",
               "base_radius_mm": a, "height_mm": h, "effective_knuckle_radius_mm": knuckle}
    evidence["mapping"] = {
        "consumer_transform_mapping_verified": True, "source_world_frame_match": True,
        "descriptor_transform_convention_verified": False, "descriptor_raw_data_layout_verified": False,
        "coordinate_frame": "WORLD", "transform_order": "AsArray12=[x_axis.xyz,y_axis.xyz,z_axis.xyz,origin.xyz]",
        "source_contract": "standalone_positive_design_dish_without_members_or_negative_children",
        "profile_parameter_source": _PROFILE_SOURCE, "native_branch_rva": "Core3D.dll!0x665240",
        "profile_parameters": _copy(profile), "database_radi_selects_branch": True,
        "database_radi_magnitude_used_as_knuckle": False, "raw_third_value_used_as_radius": False,
        "observed_export_marker": raw[2], "export_marker_global_semantics_verified": False,
        "rdata_dimension_match": {"base_radius_to_diam": True, "height_to_heig": True},
        "limits_mapping_verified": True,
        "native_tessellation": False, "native_shape_equivalence_verified": False}
    return raw, profile


def read_generated_dish_mesh(bridge: Any, refno: str, radial_segments: int = 48,
                             chord_tolerance_mm: float | None = None) -> dict[str, Any]:
    """Observe and reconstruct one supported Design DISH, with no native retries."""
    result = {
        "success": False, "available": False, "complete": False,
        "requested_refno": refno if isinstance(refno, str) else None, "resolved_refno": None,
        "resolved_type": "DISH", "coordinate_space": "WORLD", "source_geometry": None, "derived_mesh": None,
        "native_csg_evaluated": False, "native_tessellation": False, "cuts_applied": False,
        "whole_equipment_geometry_verified": False, "native_shape_equivalence_verified": False,
        "consumer_transform_mapping_verified": False,
        "source_evidence": {"context_before": None, "primitive_before": None, "generated_csg": None,
                            "generated_csg_validated": None, "primitive_after": None, "context_after": None,
                            "mapping": None}, "errors": []}
    evidence = result["source_evidence"]
    try:
        _require(isinstance(refno, str) and bool(refno.strip()) and refno == refno.strip()
                 and (refno.startswith("/") and len(refno) > 1 or _numeric_ref(refno) is not None),
                 "generated_dish_reference_required")
        _require(type(radial_segments) is int and 12 <= radial_segments <= 128, "generated_dish_radial_segments_invalid")
        if chord_tolerance_mm is not None:
            _require(_finite_number(chord_tolerance_mm, "generated_dish_chord_tolerance_invalid") > 0,
                     "generated_dish_chord_tolerance_invalid")
        context = _read_context(bridge, evidence, "context_before")
        before = _observe(evidence, "primitive_before", lambda: bridge.primitive_geometry(refno))
        source, resolved = _primitive(before, refno, context, "before")
        generated = _observe(evidence, "generated_csg", lambda: bridge.generated_csg(resolved, max_trees=1, max_items=1))
        raw, profile = _descriptor(generated, resolved, source, context, evidence)
        after = _observe(evidence, "primitive_after", lambda: bridge.primitive_geometry(resolved))
        repeated, repeated_ref = _primitive(after, resolved, context, "after")
        _require(_numeric_ref(repeated_ref) == _numeric_ref(resolved) and repeated == source, "generated_dish_source_changed")
        after_context = _read_context(bridge, evidence, "context_after")
        _require(after_context == context, "generated_dish_context_changed")
        mesh = derive_generated_dish_mesh(profile, radial_segments, chord_tolerance_mm=chord_tolerance_mm,
                                          coordinate_space="WORLD", frame=source["frame"])
        evidence["mesh_derivation"] = {key: _copy(mesh.get(key)) for key in ("success", "errors", "tessellation")}
        _require(mesh.get("success") is True and mesh.get("available") is True and mesh.get("complete") is True,
                 "generated_dish_mesh_derivation_failed")
        mesh.update(refno=resolved, source_geometry_refno=resolved, source_csg_rdata=raw,
                    profile_parameter_provenance=_PROFILE_SOURCE, native_csg_evaluated=False,
                    native_shape_equivalence_verified=False)
        evidence["source_consistency"] = {"revision_unchanged": True, "primitive_observations_equal": True,
            "frame_observations_equal": True, "context_unchanged": True,
            "primitive_session_binding": "revision_and_context_boundaries; envelope_session_checked_when_present"}
        result.update(success=True, available=True, complete=True, resolved_refno=resolved,
                      source_geometry=_copy(source), derived_mesh=mesh, consumer_transform_mapping_verified=True)
    except GeneratedDishWorkflowError as exc:
        result["errors"] = [{"stage": "generated_dish_workflow", "code": exc.code, "message": str(exc)}]
    except Exception as exc:
        result["errors"] = [{"stage": "generated_dish_workflow", "code": "generated_dish_read_failed",
                              "exception": _error_observation(exc), "message": str(exc)[:2048]}]
    return {"ok": True, "version": "E3D21", "result": result}

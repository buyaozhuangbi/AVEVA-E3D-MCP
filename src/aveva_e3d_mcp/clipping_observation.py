"""Validate and compare sampled native clipping evidence without live reads.

Stored clip-box values are not verified individual clipping planes. These
comparisons establish neither target visibility nor the cause of a view change.
"""

from __future__ import annotations

import math
from typing import Any


_FACES = ("W", "S", "D", "E", "N", "U")
_NATIVE_UNVERIFIED = "SDK native (unverified)"
_ERROR_FIELDS = {"error", "errors", "error_code", "exception", "read_error", "read_errors", "diagnostic_error",
                 "native_attribute_errors", "typed_read_errors"}
_DIAGNOSTIC_FIELDS = _ERROR_FIELDS | {"has_fault", "native_fault", "native_database_fault"}
_ENVELOPE_FIELDS = {"ok", "version", "result", "session_id", "bridge_build"}
_CLIPPING_FIELDS = {
    "success", "available", "complete", "status", "source", "view_id", "clipping_enabled", "capping",
    "cap_colour", "box_data", "box_faces", "read_errors", "coordinate_frame", "units", "side_effects",
    "view_unchanged", "partial",
}
_BOX_FIELDS = {"available", "lengths", "position", "orientation", "source", "interpretation"}


def _integer(value: Any, minimum: int, maximum: int | None = None) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool) and value >= minimum and
            (maximum is None or value <= maximum))


def _vector(value: Any) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return False
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return False
        try:
            if not math.isfinite(item):
                return False
        except (OverflowError, ValueError):
            return False
    return True


def _problems(value: Any, label: str, errors: dict[str, str], depth: int = 0) -> None:
    if depth > 32:
        errors.setdefault(label, "Native evidence exceeds the supported validation depth")
    elif isinstance(value, dict):
        for key, item in value.items():
            path = label + "." + str(key)
            if ((key in _ERROR_FIELDS and item) or
                    (key in {"has_fault", "native_fault"} and item is not False)):
                errors.setdefault(path, "Native evidence reports a read error or fault")
            else:
                if key == "native_database_fault" and (
                        not isinstance(item, dict) or item.get("has_fault") is not False):
                    errors.setdefault(path, "Native fault evidence did not positively verify has_fault=false")
                _problems(item, path, errors, depth + 1)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _problems(item, f"{label}[{index}]", errors, depth + 1)


def _result(value: Any, label: str, errors: dict[str, str]) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("ok") is not True or value.get("version") != "E3D21":
        errors.setdefault(label, "Native E3D21 ok=true response is unavailable")
        return {}
    result = value.get("result")
    if not isinstance(result, dict):
        errors.setdefault(label + ".result", "Native response has no result object")
        return {}
    _problems(value, label, errors)
    return result


def _known_fields(value: dict[str, Any], allowed: set[str], label: str, errors: dict[str, str]) -> None:
    for field in value:
        if field not in allowed and field not in _DIAGNOSTIC_FIELDS:
            errors.setdefault(label + "." + str(field), "Unsupported native clipping field leaves sampled equality unverified")


def _view_identity(value: Any, label: str, errors: dict[str, str]) -> int | None:
    result = _result(value, label, errors)
    if (result.get("success") is not True or result.get("available") is not True or
            result.get("partial") is not False or not isinstance(result.get("errors"), dict) or
            result["errors"]):
        errors.setdefault(label, "Native view identity is not successfully and completely available")
    identity = result.get("view_id")
    if not _integer(identity, 0):
        errors.setdefault(label + ".view_id", "Native view identity is missing or invalid")
        return None
    return identity


def _clipping_sample(value: Any, label: str, errors: dict[str, str]) -> tuple[dict[str, Any], bool]:
    before = len(errors)
    result = _result(value, label, errors)
    if isinstance(value, dict):
        # The current HTTP clipping envelope has ok/version/result. Optional
        # standard session/build metadata is allowed without treating it as a
        # clipping plane or expanding the supported geometry contract.
        _known_fields(value, _ENVELOPE_FIELDS, label, errors)
    _known_fields(result, _CLIPPING_FIELDS, label + ".result", errors)
    fields: dict[str, Any] = {}
    for field in ("success", "available", "complete", "view_unchanged"):
        if result.get(field) is not True:
            errors.setdefault(label + "." + field, "Native clipping read did not positively verify " + field)
    if result.get("status") != "complete":
        errors.setdefault(label + ".status", "Native clipping read is not complete")
    if not isinstance(result.get("read_errors"), dict) or result["read_errors"]:
        errors.setdefault(label + ".read_errors", "Native clipping read has errors or no read-error evidence")
    # The native getter has no partial flag. A contradictory future value must
    # not turn an incomplete observation into a proof of equality.
    if "partial" in result and result["partial"] is not False:
        errors.setdefault(label + ".partial", "Native clipping read reports unknown or partial coverage")
    identity = result.get("view_id")
    if _integer(identity, 0):
        fields["view_id"] = identity
    else:
        errors.setdefault(label + ".view_id", "Native clipping view identity is missing or invalid")
    for field in ("clipping_enabled", "capping"):
        if isinstance(result.get(field), bool):
            fields[field] = result[field]
        else:
            errors.setdefault(label + "." + field, "Required native boolean is missing or invalid")
    if _integer(result.get("cap_colour"), -2147483648, 2147483647):
        fields["cap_colour"] = result["cap_colour"]
    else:
        errors.setdefault(label + ".cap_colour", "Native colour is not an Int32 value")
    box = result.get("box_data")
    if not isinstance(box, dict) or box.get("available") is not True:
        errors.setdefault(label + ".box_data", "Stored clip-box data is unavailable")
        box = {}
    _known_fields(box, _BOX_FIELDS, label + ".box_data", errors)
    for field in ("lengths", "position", "orientation"):
        if _vector(box.get(field)):
            fields["box_data." + field] = list(box[field])
        else:
            errors.setdefault(label + ".box_data." + field, "Required finite native three-vector is missing or invalid")
    faces = result.get("box_faces")
    face_values: dict[str, tuple[bool, bool]] = {}
    if not isinstance(faces, list) or len(faces) != len(_FACES):
        errors.setdefault(label + ".box_faces", "Exactly six native box-face observations are required")
    if isinstance(faces, list):
        for index, face in enumerate(faces):
            if isinstance(face, dict):
                _known_fields(face, {"face", "defined", "active"}, f"{label}.box_faces[{index}]", errors)
            if (not isinstance(face, dict) or not isinstance(face.get("face"), str) or
                    face["face"] not in _FACES or face["face"] in face_values or
                    not isinstance(face.get("defined"), bool) or not isinstance(face.get("active"), bool)):
                errors.setdefault(f"{label}.box_faces[{index}]", "Native box-face identity or boolean flags are invalid")
            else:
                face_values[face["face"]] = (face["defined"], face["active"])
    if set(face_values) != set(_FACES):
        errors.setdefault(label + ".box_faces", "Native box-face observations are missing or duplicated")
    else:
        fields["box_faces"] = [(face, *face_values[face]) for face in _FACES]
    for field in ("coordinate_frame", "units"):
        if result.get(field) != _NATIVE_UNVERIFIED:
            errors.setdefault(label + "." + field, "Native coordinate/unit interpretation is unavailable or unsupported")
    # An explicit native view-object drift is useful even when the rest of the
    # read is partial. Arbitrary error envelopes do not supply this evidence.
    drift = (bool(result) and "view_id" in fields and result.get("view_unchanged") is False and
             result.get("status") == "context_changed")
    return fields if len(errors) == before else {"view_id": fields["view_id"]} if "view_id" in fields else {}, drift


def clipping_observation_result(
    baseline: Any, final: Any, baseline_view: Any, final_view: Any, *, complete: bool,
) -> dict[str, Any]:
    """Return three-valued sample equality, validating the raw native evidence.

    Equality requires two complete clipping reads tied to their corresponding
    successful view identities and a completed enclosing workflow. A directly
    observed view drift remains false when other evidence is incomplete.
    """
    errors: dict[str, str] = {}
    first, first_drift = _clipping_sample(baseline, "baseline_clipping", errors)
    last, last_drift = _clipping_sample(final, "final_clipping", errors)
    first_view = _view_identity(baseline_view, "baseline_view", errors)
    last_view = _view_identity(final_view, "final_view", errors)
    changed: set[str] = set()
    if first_drift or last_drift:
        changed.add("view_object")
    identities = (first.get("view_id"), last.get("view_id"), first_view, last_view)
    known_ids = [identity for identity in identities if identity is not None]
    if known_ids and any(identity != known_ids[0] for identity in known_ids[1:]):
        changed.add("view_id")
    for field in first.keys() & last.keys():
        if first[field] != last[field]:
            changed.add(field)
    if complete is not True:
        errors["workflow_completed"] = "The enclosing workflow did not complete"
    verified = not errors
    return {
        "unchanged": False if changed else True if verified else None,
        "complete": verified,
        "changed_fields": sorted(changed),
        "read_errors": errors,
        "scope": {
            "comparison": "Exact finite native clipping values and sampled view identities only; not an atomic snapshot.",
            "supported_fields": ["view_id", "clipping_enabled", "capping", "cap_colour",
                                 "box_data.lengths", "box_data.position", "box_data.orientation",
                                 "box_faces.face", "box_faces.defined", "box_faces.active"],
            "unsupported_fields": "Unknown clipping-envelope, result, box-data or face fields leave equality unverified even when their raw values match.",
            "coordinate_frame": _NATIVE_UNVERIFIED, "units": _NATIVE_UNVERIFIED,
            "box_data": "Stored SDK clip-box data; individual clipping-plane geometry is not verified.",
            "pixel_visibility_verified": False, "target_clipped_verified": False,
            "change_cause": "not determined", "future_stability_guaranteed": False,
        },
    }

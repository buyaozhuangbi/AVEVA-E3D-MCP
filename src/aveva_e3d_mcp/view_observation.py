"""Conservative comparisons of read-only view observations and capture evidence.

Equal samples describe only the observation interval. They do not prove that a
renderer was idle, attribute a change to its source, or prevent later navigation.
"""

from __future__ import annotations

import math
import re
from typing import Any

from .clipping_observation import clipping_observation_result


CAMERA_FIELDS = (
    "centre", "direction", "radius", "bearing_degrees", "elevation_degrees",
    "extra_twist_degrees", "perspective", "shaded", "width", "height",
)
IDENTITY_FIELDS = (
    "view_id", "drawlist_id", "drawlist_member_count",
    "drawlist_membership_fingerprint", "drawlist_visuals_fingerprint",
)
CONTEXT_FIELDS = ("version", "session_id", "bridge_build")


def observation_scope() -> dict[str, Any]:
    return {
        "camera_fields": list(CAMERA_FIELDS),
        "identity_fields": list(IDENTITY_FIELDS),
        "context_fields": list(CONTEXT_FIELDS),
        "comparison": "exact finite native values across the observed samples",
        "drawlist_visuals": "top-level member properties only",
        "primitive_visuals_verified": False,
        "model_geometry_verified": False,
        "pixel_visibility_verified": False,
        "render_completion_verified": False,
        "future_stability_guaranteed": False,
        "change_cause": "not determined",
    }


def _number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def _integer(value: Any, minimum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _valid_field(field: str, value: Any) -> bool:
    if field in {"centre", "direction"}:
        return (isinstance(value, (list, tuple)) and len(value) == 3
                and all(_number(item) for item in value)
                and (field != "direction" or any(item != 0 for item in value)))
    if field in {"radius", "bearing_degrees", "elevation_degrees", "extra_twist_degrees"}:
        return _number(value) and (field != "radius" or value > 0)
    if field in {"perspective", "shaded"}:
        return isinstance(value, bool)
    if field in {"width", "height"}:
        return _integer(value, 1)
    if field in {"view_id", "drawlist_id", "drawlist_member_count"}:
        return _integer(value, 0)
    return isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value) is not None


def _view_fields(value: Any, label: str, errors: dict[str, str], *, envelope: bool) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.setdefault(label, "Native view observation is unavailable or not an object")
        return {}
    if envelope:
        if value.get("ok") is not True:
            errors.setdefault(label, "Native view observation did not confirm ok=true")
            return {}
        value = value.get("result")
        if not isinstance(value, dict):
            errors.setdefault(label, "Native view observation has no result object")
            return {}
    if value.get("success") is not True or value.get("available") is not True:
        errors.setdefault(label, "Native view state is not successfully available")
        return {}
    if value.get("partial") is not False:
        errors.setdefault(label + ".partial", "Native view state is partial or completeness is unknown")
    native_errors = value.get("errors")
    if not isinstance(native_errors, dict) or native_errors:
        errors.setdefault(label + ".errors", "Native view state has read errors or no read-error evidence")
    fields = {}
    for field in CAMERA_FIELDS + IDENTITY_FIELDS:
        item = value.get(field)
        if not _valid_field(field, item):
            errors.setdefault(label + "." + field, "Required native value is missing, invalid or non-finite")
        else:
            fields[field] = list(item) if isinstance(item, tuple) else item
    return fields


def _context_fields(value: Any, label: str, errors: dict[str, str]) -> dict[str, str]:
    if not isinstance(value, dict) or value.get("ok") is not True or value.get("connected") is not True:
        errors.setdefault(label, "Bridge health did not confirm ok=true and connected=true")
        return {}
    fields = {}
    for field in CONTEXT_FIELDS:
        item = value.get(field)
        if not isinstance(item, str) or not item.strip():
            errors.setdefault(label + "." + field, "Required bridge identity is unavailable")
        else:
            fields[field] = item
    return fields


def _compare(fields: dict[str, Any], baseline: dict[str, Any], changed: set[str], prefix: str = "") -> None:
    # Retain the first valid value of each field, including after a partial read.
    # This avoids missing accumulated drift by comparing only adjacent samples.
    for field, value in fields.items():
        if field in baseline and baseline[field] != value:
            changed.add(prefix + field)
        baseline.setdefault(field, value)


def _evidence(
    frames: list[tuple[str, Any, bool]], contexts: list[tuple[str, Any]], errors: dict[str, str],
) -> set[str]:
    changed: set[str] = set()
    baseline: dict[str, Any] = {}
    for label, frame, envelope in frames:
        _compare(_view_fields(frame, label, errors, envelope=envelope), baseline, changed)
    baseline = {}
    for label, context in contexts:
        _compare(_context_fields(context, label, errors), baseline, changed, "context.")
    return changed


def view_observation_result(
    states: list[Any], context_before: Any, context_after: Any, read_errors: dict[str, str],
    *, include_clipping: bool = False, baseline_clipping: Any = None, final_clipping: Any = None,
) -> dict[str, Any]:
    errors = dict(read_errors)
    changed = _evidence(
        [(f"states[{index}]", state, True) for index, state in enumerate(states)],
        [("context_before", context_before), ("context_after", context_after)], errors,
    )
    if len(states) < 2:
        errors["states"] = "At least two separate UI-dispatched samples are required"
    complete = not errors
    clipping = None
    if include_clipping:
        clipping = clipping_observation_result(
            baseline_clipping, final_clipping, states[0] if states else None,
            states[-1] if states else None, complete=complete,
        )
        changed.update("clipping." + field for field in clipping["changed_fields"])
        errors.update({"clipping." + key: value for key, value in clipping["read_errors"].items()})
        complete = complete and clipping["complete"]
    result = {
        "success": True,
        "stable": False if changed else True if complete else None,
        "complete": complete,
        "states": states,
        "context_before": context_before,
        "context_after": context_after,
        "changed_fields": sorted(changed),
        "read_errors": errors,
        "scope": observation_scope(),
    }
    if include_clipping:
        result.update(baseline_clipping=baseline_clipping, final_clipping=final_clipping,
                      clipping_observation=clipping)
        result["scope"]["database_readiness_verified"] = False
        result["scope"]["native_fault_absence_verified"] = False
        result["scope"]["clipping"] = (
            "Optional baseline/final native clipping getter samples tied to sampled view identities; "
            "not atomic. Getters may initialize managed caches. SDK coordinates and units remain "
            "unverified; individual clipping-plane geometry and target/pixel visibility are not verified.")
    return result


def capture_observation_result(
    before: Any, after: Any, context_after: Any, native: Any, read_errors: dict[str, str],
) -> dict[str, Any]:
    errors = dict(read_errors)
    previous = before.get("result") if isinstance(before, dict) and before.get("ok") is True else None
    if not isinstance(previous, dict):
        previous = {}
        errors.setdefault("before", "Pre-capture observations are unavailable")
    previous_errors = previous.get("read_errors")
    if isinstance(previous_errors, dict):
        errors.update({"before." + str(key): str(value) for key, value in previous_errors.items()})
    else:
        errors.setdefault("before.read_errors", "Pre-capture read-error evidence is unavailable")
    if previous.get("complete") is not True:
        errors.setdefault("before.complete", "Pre-capture observations are incomplete")
    states = previous.get("states")
    if not isinstance(states, list) or len(states) < 2:
        states = []
        errors.setdefault("before.states", "Pre-capture samples are unavailable")
    frames = [(f"before.states[{index}]", state, True) for index, state in enumerate(states)]
    object_changed = False
    if not isinstance(native, dict):
        errors.setdefault("native", "Adapter did not return observations bracketing the native bitmap call")
    else:
        if native.get("complete") is not True:
            errors.setdefault("native.complete", "Native bitmap observations are incomplete")
        native_errors = native.get("read_errors")
        if not isinstance(native_errors, dict):
            errors.setdefault("native.read_errors", "Native bitmap read-error evidence is unavailable")
        else:
            errors.update({"native." + str(key): str(value) for key, value in native_errors.items()})
        unchanged = native.get("view_object_unchanged")
        if unchanged is False:
            object_changed = True
        elif unchanged is not True:
            errors.setdefault("native.view_object_unchanged", "Captured view object identity was not verified")
        frames.extend(("native." + field, native.get(field), False)
                      for field in ("before_refresh", "before_bitmap", "after_bitmap"))
    frames.append(("after", after, True))
    changed = _evidence(frames, [
        ("before.context_before", previous.get("context_before")),
        ("before.context_after", previous.get("context_after")),
        ("context_after", context_after),
    ], errors)
    if object_changed:
        changed.add("view_object")
    # Preserve earlier positive evidence even if a future caller sends a
    # truncated set of raw samples. Missing coverage never turns into success.
    previous_changed = previous.get("changed_fields")
    if isinstance(previous_changed, list):
        changed.update(field for field in previous_changed if isinstance(field, str))
    complete = not errors
    consistent = False if changed else True if complete else None
    return {
        "schema": 1,
        "status": "changed" if changed else "consistent" if complete else "unverified",
        "consistent": consistent,
        "complete": complete,
        "before": before,
        "after": after,
        "context_after": context_after,
        "native": native,
        "changed_fields": sorted(changed),
        "read_errors": errors,
        "scope": observation_scope(),
    }

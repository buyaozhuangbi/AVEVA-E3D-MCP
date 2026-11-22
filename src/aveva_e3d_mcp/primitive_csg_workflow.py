"""Observed primitive parameters for derived CSG; never a native shape claim."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
from typing import Any, Callable

from .bridge import BridgeClient, BridgeError


class PrimitiveCsgReadError(ValueError):
    def __init__(self, code: str, evidence: dict[str, Any]):
        self.code = code
        self.evidence = evidence
        super().__init__(code)


def _ref(value: Any) -> tuple[str, str] | None:
    match = re.fullmatch(r"=([0-9]+)/([0-9]+)", value) if isinstance(value, str) else None
    return tuple(part.lstrip("0") or "0" for part in match.groups()) if match else None


def _digest(native: Any) -> str:
    return hashlib.sha256(json.dumps(native, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _finite_nonnegative(value: Any) -> bool:
    try:
        return type(value) in {int, float} and math.isfinite(value) and value >= 0
    except (ValueError, OverflowError):
        return False


def _context_available(context: Any) -> bool:
    return (isinstance(context, dict) and context.get("available") is True
            and all(isinstance(context.get(key), str) and context[key].strip()
                    for key in ("session_id", "version"))
            and isinstance(context.get("project"), dict)
            and all(isinstance(context["project"].get(key), str) and context["project"][key].strip()
                    for key in ("name", "mdb")))


def read_primitive_csg_source(
    bridge: BridgeClient, refno: str, *, include_native_volume: bool,
    context_reader: Callable[[], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence: dict[str, Any] = {
        "requested_refno": refno, "atomic_snapshot": False, "native_geometry_reads": 0,
        "context": {"before": None, "after": None, "boundary_agreement": None},
        "native_volume_requested": include_native_volume, "native_volume_observation": None,
        "consistency_scope": "Two matching complete primitive parameter observations with matching session/version/project/MDB boundaries; optional native volume lies between them. Intervening changes can remain unobserved.",
        "native_cut_application_verified": False,
    }

    observations: dict[str, Any] = {}

    def fail(code: str) -> None:
        evidence["native_observations"] = deepcopy(observations)
        raise PrimitiveCsgReadError(code, evidence)

    try:
        before = context_reader()
        evidence["context"]["before"] = before
        if not _context_available(before):
            fail("primitive_csg_context_unavailable")
        response = bridge.primitive_geometry(refno)
        observations["before"] = response
        evidence["native_geometry_reads"] += 1
        native = response.get("result") if isinstance(response, dict) else None
        if (not isinstance(response, dict) or response.get("ok") is not True
                or response.get("version") != before.get("version") or not isinstance(native, dict)):
            evidence["native_read_error"] = response
            fail("primitive_csg_native_read_failed")
        if native.get("success") is not True or native.get("complete") is not True or native.get("read_errors"):
            evidence["native_read_error"] = native
            fail("primitive_csg_native_geometry_incomplete")
        identity = native.get("element")
        if not isinstance(identity, dict) or _ref(identity.get("refno")) is None:
            fail("primitive_csg_native_identity_unavailable")
        if identity.get("type") not in {"BOX", "CYLI", "CONE", "DISH"} or native.get("negative") is not False:
            fail("primitive_csg_positive_parent_required")
        resolved = identity["refno"]
        if refno.startswith("=") and _ref(refno) != _ref(resolved):
            fail("primitive_csg_element_reference_mismatch")
        if refno.startswith("/") and (not isinstance(identity.get("name"), str)
                or ("/" + identity["name"].lstrip("/")).casefold() != refno.casefold()):
            fail("primitive_csg_element_name_mismatch")
        evidence.update(resolved_refno=resolved, resolved_type=identity["type"],
                        native_read_before_sha256=_digest(native), revision_before=native.get("revision"))
        if include_native_volume:
            volume_response = bridge.geometry_volume(resolved, scope="element")
            evidence["native_volume_observation"] = volume_response
            volume = volume_response.get("result") if isinstance(volume_response, dict) else None
            if (not isinstance(volume_response, dict) or volume_response.get("ok") is not True
                    or volume_response.get("version") != before.get("version") or not isinstance(volume, dict)
                    or volume.get("success") is not True or volume.get("read_errors")
                    or volume.get("unit") != "mm3" or volume.get("scope") != "element"
                    or volume.get("native_qualifier") != "ONLY" or volume.get("ce_unchanged") is not True
                    or volume.get("volume_pair_applicable") is not True or volume.get("volume_pair_coherent") is not True):
                fail("primitive_csg_native_volume_unavailable")
            volume_identity = volume.get("element")
            accepted_types = {"BOX": {"BOX"}, "CYLI": {"CYLI", "CYLINDER"},
                              "CONE": {"CONE"}, "DISH": {"DISH"}}
            if (not isinstance(volume_identity, dict) or _ref(volume_identity.get("refno")) != _ref(resolved)
                    or volume_identity.get("type") not in accepted_types[identity["type"]]):
                fail("primitive_csg_native_volume_identity_mismatch")
            for field in ("gross", "net"):
                measurement = volume.get(field)
                value = measurement.get("value_mm3") if isinstance(measurement, dict) else None
                if (not isinstance(measurement, dict) or measurement.get("available") is not True
                        or measurement.get("unit") != "mm3" or measurement.get("error")
                        or isinstance(value, bool) or not isinstance(value, (int, float))):
                    fail("primitive_csg_native_volume_unavailable")
                if not _finite_nonnegative(value):
                    fail("primitive_csg_native_volume_unavailable")
            if volume["net"]["value_mm3"] - volume["gross"]["value_mm3"] > max(
                    1e-6, volume["gross"]["value_mm3"] * 1e-9):
                fail("primitive_csg_native_volume_incoherent")
        repeated = bridge.primitive_geometry(resolved)
        observations["after"] = repeated
        evidence["native_geometry_reads"] += 1
        if (not isinstance(repeated, dict) or repeated.get("ok") is not True
                or repeated.get("version") != before.get("version") or not isinstance(repeated.get("result"), dict)):
            evidence["native_recheck_error"] = repeated
            fail("primitive_csg_native_recheck_failed")
        evidence["native_read_after_sha256"] = _digest(repeated["result"])
        evidence["native_reads_agree"] = native == repeated["result"]
        evidence["revision_after"] = repeated["result"].get("revision")
        after = context_reader()
        evidence["context"]["after"] = after
        agreement = _context_available(after) and all(
            before.get(key) == after.get(key) for key in ("session_id", "version", "project"))
        evidence["context"]["boundary_agreement"] = agreement
        if not agreement:
            fail("primitive_csg_context_changed")
        if not evidence["native_reads_agree"]:
            fail("primitive_csg_source_changed")
        return deepcopy(native), evidence
    except PrimitiveCsgReadError:
        raise
    except Exception as exc:
        evidence["read_error"] = {"error_type": type(exc).__name__, "message": str(exc)[:1024]}
        if isinstance(exc, BridgeError):
            evidence["read_error"]["native_diagnostics"] = exc.diagnostics
        fail("primitive_csg_read_failed")


def compare_primitive_csg_volumes(mesh: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """Keep native observations and parameter-polyhedron deltas independent."""
    response = evidence.get("native_volume_observation")
    native = response.get("result") if isinstance(response, dict) else None
    derived = mesh.get("volume")
    result: dict[str, Any] = {
        "available": False, "unit": "mm3", "native_cut_application_verified": False,
        "shape_equivalence_verified": False,
        "scope": "Observed native mass-volume values versus an independently sampled owner-member parameter difference. Representation settings can produce different volumes; agreement does not identify cuts or prove matching shapes.",
    }
    if not isinstance(native, dict) or not isinstance(derived, dict):
        return result
    comparisons = {}
    for field, derived_key in (("gross", "gross_polyhedral_mm3"), ("net", "net_mm3")):
        measurement = native.get(field)
        observed = measurement.get("value_mm3") if isinstance(measurement, dict) else None
        value = derived.get(derived_key)
        if not all(_finite_nonnegative(item) for item in (observed, value)):
            return result
        comparisons[field] = {"native_mm3": observed, "derived_polyhedral_mm3": value,
                              "derived_minus_native_mm3": value - observed,
                              "relative_delta": (value - observed) / observed if observed else None}
    result.update(available=True, **comparisons)
    return result

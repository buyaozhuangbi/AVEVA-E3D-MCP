"""Compose bounded native reads without editing, retrying or restoring E3D state."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from collections.abc import Callable
from typing import Any

from .clipping_observation import clipping_observation_result
from .protocol import ImageToolResult
from .view_observation import view_observation_result


_PRIMITIVE_DIMENSIONS = {
    "CYLI": ["DIAM", "HEIG"], "NCYL": ["DIAM", "HEIG"],
    "BOX": ["XLEN", "YLEN", "ZLEN"], "NBOX": ["XLEN", "YLEN", "ZLEN"],
    "CONE": ["DTOP", "DBOT", "HEIG"], "NCON": ["DTOP", "DBOT", "HEIG"],
    "DISH": ["DIAM", "HEIG", "RADI"],
}
_TYPE_ALIASES = {
    "CYLINDER": "CYLI", "NCYLINDER": "NCYL", "NCONE": "NCON",
    "PANEL": "PANE", "PAVERT": "PAVE", "PLOOP": "PLOO",
    "EQUIPMENT": "EQUI", "NOZZLE": "NOZZ", "BRANCH": "BRAN",
}
_ERROR_FIELDS = {
    "error", "errors", "read_error", "read_errors", "diagnostic_error",
    "native_attribute_errors", "typed_read_errors",
}
_CONTEXT_FIELDS = ("session_id", "bridge_build", "version")
_PROJECT_FIELDS = ("name", "mdb", "user")


class _Stop(Exception):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code, self.details = code, details


def _reference(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("refno must be an explicit /NAME or =database/element reference")
    value = value.strip()
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("refno must contain valid UTF-8 text") from exc
    if not 1 < len(value) <= 256 or any(
        char.isspace() or ord(char) < 32 or 127 <= ord(char) < 160 for char in value
    ):
        raise ValueError("refno must contain 2 to 256 characters without whitespace or controls")
    if value.startswith("/"):
        return value
    if re.fullmatch(r"=[0-9]+/[0-9]+", value) is None:
        raise ValueError("refno must be an explicit /NAME or =database/element reference")
    database, element = (int(part) for part in value[1:].split("/"))
    if not 1 <= database <= 2147483647 or not 0 <= element <= 2147483647:
        raise ValueError("refno requires a positive Int32 database and nonnegative Int32 element")
    return f"={database}/{element}"


def _native_type(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _Stop("target_identity_mismatch", "Native target type is unavailable")
    value = value.strip().upper()
    return _TYPE_ALIASES.get(value, value)


def _without_image(value: Any) -> Any:
    """Copy returned evidence while keeping image bytes only in ImageToolResult."""
    if isinstance(value, dict):
        result = {key: _without_image(item) for key, item in value.items() if key != "image_base64"}
        if "image_base64" in value:
            encoded = value["image_base64"]
            result["image_base64_omitted"] = True
            result["image_base64_characters"] = len(encoded) if isinstance(encoded, str) else None
        return result
    if isinstance(value, (list, tuple)):
        return [_without_image(item) for item in value]
    return value


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _native_fault(value: Any) -> bool:
    if isinstance(value, dict):
        return (value.get("has_fault") is True or value.get("native_fault") is True or
                any(_native_fault(item) for item in value.values()))
    return isinstance(value, list) and any(_native_fault(item) for item in value)


def _drift_only_error(value: Any) -> bool:
    # These exact native diagnostics report successfully observed changes rather
    # than failed SDK reads. Keep them in evidence and the three-valued comparison.
    if isinstance(value, str):
        return value == "Current element could not be verified unchanged during the display-state read."
    if isinstance(value, dict):
        return (value.get("path") == "ce_after" and
                value.get("error") == "The current element changed during selection inspection.") or (
                value.get("path") == "context" and
                value.get("error") == "The current view or Drawlist changed during selection inspection.")
    return False


def _reported_errors(value: Any, path: str = "response") -> list[str]:
    errors = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _ERROR_FIELDS and item:
                if isinstance(item, list) and all(_drift_only_error(row) for row in item):
                    continue
                errors.append(path + "." + key)
            elif key not in _ERROR_FIELDS:
                errors.extend(_reported_errors(item, path + "." + key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(_reported_errors(item, f"{path}[{index}]"))
    return errors


def _tri_compare(samples: list[Any], *, complete: bool) -> bool | None:
    known = [sample for sample in samples if sample is not None]
    if known and any(sample != known[0] for sample in known[1:]):
        return False
    return True if complete and len(known) >= 2 and len(known) == len(samples) else None


class _Diagnosis:
    def __init__(self, bridge: Any, reference: str, include_image: bool, width: int, height: int,
                 include_clipping: bool = False) -> None:
        self.bridge, self.reference = bridge, reference
        self.include_image, self.width, self.height = include_image, width, height
        self.include_clipping = include_clipping
        settings = bridge.settings
        self.mode = settings.mode
        self.version = settings.version or settings.default_version
        self.stage = "availability"
        self.image: str | None = None
        self.initial_context: tuple[Any, ...] | None = None
        self.initial_project: tuple[Any, ...] | None = None
        self.result: dict[str, Any] = {
            "schema": 1, "success": False, "workflow_completed": False, "complete": False,
            "available": False, "requested_refno": reference, "target": None,
            "evidence": {"gates": []}, "observations": {}, "unknown": [], "errors": [],
            "stopped_stage": None, "read_only": True, "model_writes": 0, "view_setters": 0,
            "restarts": 0, "retries": 0, "cancellations": 0,
            "capture_requested": include_image, "capture_attempted": False,
            "clipping_requested": include_clipping,
            "scope": {
                "snapshot_depth": 2, "snapshot_limit": 100, "selection_limit": 200,
                "comparison": "Sampled native context, camera, top-level Drawlist and graphical selection; not an atomic snapshot or a concurrency lock.",
                "geometry": "Exact target only. WORLD/SITE/ZONE roots are rejected. EQUI aggregate geometry is allowed; snapshot limits do not bound SDK bounds/display work or native execution time.",
                "capture": "Optional single existing BridgeClient.capture_view workflow, including its internal observations and native graphics refresh. Gates bracket stages, not each internal request; existing native fault gates remain responsible for SDK access.",
                "change_cause": "not determined", "model_geometry_verified": False,
                "pixel_visibility_verified": False, "render_completion_verified": False,
                "future_stability_guaranteed": False,
            },
        }
        if include_clipping:
            self.result["scope"]["clipping"] = (
                "Optional native clipping samples only; SDK coordinates and units remain unverified. "
                "Stored clip-box data does not verify individual plane geometry, target clipping, pixel visibility or change cause. "
                "The getters may warm the managed clipping cache; no clipping setter or view refresh is requested.")

    @property
    def evidence(self) -> dict[str, Any]:
        return self.result["evidence"]

    def unknown(self, code: str, message: str, **details: Any) -> None:
        row = {"stage": self.stage, "code": code, "message": message, **details}
        if row not in self.result["unknown"]:
            self.result["unknown"].append(row)

    def receive(self, operation: Callable[..., Any], *args: Any,
                destination: dict[str, Any], key: str, capture: bool = False) -> dict[str, Any]:
        try:
            response = operation(*args)
        except Exception as exc:
            diagnostics = _without_image(getattr(exc, "diagnostics", None))
            destination[key] = {"exception": {"type": type(exc).__name__, "message": str(exc),
                                             "diagnostics": diagnostics}}
            code = ("native_fault" if _native_fault(diagnostics) else "transport_error"
                    if getattr(exc, "transport_error", False) else "read_error")
            raise _Stop(code, str(exc), diagnostics=diagnostics) from exc
        destination[key] = _without_image(response)
        image_error = None
        if capture and isinstance(response, dict):
            try:
                self.extract_image(response)
            except _Stop as exc:
                image_error = exc
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise _Stop("invalid_response", "Native read did not return an ok=true object")
        if _native_fault(response):
            raise _Stop("native_fault", "A native database fault stopped diagnosis")
        errors = _reported_errors(response)
        if errors:
            raise _Stop("read_error", "Native read reported errors", paths=errors)
        if image_error is not None:
            raise image_error
        if response.get("version") != "E3D21":
            raise _Stop("context_changed", "Native read version is not E3D21")
        return response

    def extract_image(self, response: dict[str, Any]) -> None:
        result = response.get("result")
        if not isinstance(result, dict):
            raise _Stop("invalid_image", "Capture returned no result object")
        encoded = result.get("image_base64")
        if result.get("mime_type") != "image/png" or not isinstance(encoded, str) or len(encoded) > 32000000:
            raise _Stop("invalid_image", "Capture did not return a bounded PNG image")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise _Stop("invalid_image", "Capture returned invalid PNG base64") from exc
        if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR":
            raise _Stop("invalid_image", "Capture returned invalid PNG content")
        width, height = int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
        if (not 64 <= width <= 2048 or not 64 <= height <= 2048 or
                result.get("width") != width or result.get("height") != height or
                width != self.width or height != self.height):
            raise _Stop("invalid_image", "PNG dimensions differ from requested capture metadata")
        self.image = encoded
        self.result["image"] = {"mime_type": "image/png", "width": width, "height": height,
                                "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}

    def gate(self, stage: str, *, initial: bool = False, final: bool = False) -> None:
        self.stage = stage
        row: dict[str, Any] = {"stage": stage}
        self.evidence["gates"].append(row)
        status = self.receive(self.bridge.health, destination=row, key="status")
        if initial:
            self.evidence["initial_status"] = row["status"]
        if final:
            self.evidence["final_status"] = row["status"]
        state = status.get("state")
        if not isinstance(state, dict):
            raise _Stop("not_ready", "Native readiness state is unavailable")
        if (status.get("connected") is not True or state.get("database_ready") is not True or
                state.get("busy") is not False or state.get("recovery_required") is not False or
                state.get("native_database_fault", {}).get("has_fault") is not False):
            raise _Stop("not_ready", "Connected native database readiness was not positively established")
        execution = state.get("execution", {})
        if execution.get("native_operation_active") is not False or execution.get("dispatcher_ready") is not True:
            raise _Stop("not_ready", "Native dispatcher readiness was not positively established")
        for key, absent in (("native_dialogs", "standard_dialog_present"), ("pml_alerts", "alert_present")):
            dialogs = state.get(key, {})
            if dialogs.get("complete") is not True or dialogs.get(absent) is not False:
                raise _Stop("not_ready", "Absence of native dialogs/PML alerts was not positively established")
        context = tuple(status.get(key) for key in _CONTEXT_FIELDS)
        if any(not isinstance(item, str) or not item.strip() for item in context):
            raise _Stop("not_ready", "Session/build/version identity is incomplete")
        if self.initial_context is not None and context != self.initial_context:
            raise _Stop("context_changed", "Session/build/version changed before a native stage")
        project_response = self.receive(self.bridge.project, destination=row, key="project")
        if initial:
            self.evidence["initial_project"] = row["project"]
        if final:
            self.evidence["final_project"] = row["project"]
        project = project_response.get("project")
        if (not isinstance(project, dict) or project.get("connected") is not True or
                project.get("busy") is True or project.get("recovery_only") is not False or
                any(not isinstance(project.get(key), str) or not project[key].strip()
                    for key in _PROJECT_FIELDS + ("current_element",))):
            raise _Stop("not_ready", "Fresh project/MDB/user/current-element identity is incomplete")
        identity = tuple(project[key] for key in _PROJECT_FIELDS)
        if self.initial_project is not None and identity != self.initial_project:
            raise _Stop("context_changed", "Project/MDB/user changed before a native stage")
        if initial:
            self.initial_context, self.initial_project = context, identity

    def read(self, stage: str, operation: Callable[..., Any], *args: Any,
             payload: str = "result", capture: bool = False) -> dict[str, Any]:
        self.gate(stage)
        if capture:
            self.result["capture_attempted"] = True
        response = self.receive(operation, *args, destination=self.evidence, key=stage, capture=capture)
        value = response.get(payload)
        if not isinstance(value, dict):
            raise _Stop("invalid_response", "Native read has no " + payload + " object")
        return value

    def identity(self, value: Any) -> None:
        target = self.result["target"]
        try:
            actual_ref = _reference(value.get("refno")) if isinstance(value, dict) else None
            actual_type = _native_type(value.get("type")) if isinstance(value, dict) else None
        except (ValueError, _Stop) as exc:
            raise _Stop("target_identity_mismatch", "Target reference/type could not be verified") from exc
        if actual_ref != target["refno"] or actual_type != target["type"]:
            raise _Stop("target_identity_mismatch", "Exact target reference/type changed or could not be verified")

    def workflow(self) -> None:
        self.gate("initial", initial=True)
        self.result["available"] = True
        self.read("baseline_view", self.bridge.view_state)
        if self.include_clipping:
            self.read("baseline_clipping", self.bridge.view_clipping_state)
        self.read("baseline_selection", self.bridge.graphical_selection, 200)
        snapshot = self.read("snapshot", self.bridge.element, self.reference, 2, 100, payload="element")
        try:
            resolved = _reference(snapshot.get("refno"))
        except ValueError as exc:
            raise _Stop("target_identity_mismatch", "Snapshot numeric identity is unavailable") from exc
        if not resolved.startswith("=") or (self.reference.startswith("=") and resolved != self.reference):
            raise _Stop("target_identity_mismatch", "Snapshot does not identify the exact requested numeric target")
        if self.reference.startswith("/") and (
                "/" + str(snapshot.get("name", "")).lstrip("/")).casefold() != self.reference.casefold():
            raise _Stop("target_identity_mismatch", "Snapshot name does not exactly identify the requested named target")
        native_type = _native_type(snapshot.get("type"))
        self.result["target"] = {"refno": resolved, "type": native_type, "name": snapshot.get("name")}
        if native_type in {"WORLD", "WORL", "SITE", "ZONE"}:
            raise _Stop("unsupported_target", "WORLD/SITE/ZONE roots are outside bounded element diagnosis")
        if snapshot.get("snapshot_info", {}).get("complete") is not True:
            self.unknown("snapshot_incomplete", "Snapshot is bounded or incomplete; no full subtree claim")
        attributes = ["NAME", "POS"] + ([] if native_type == "PAVE" else ["ORI"])
        attributes += _PRIMITIVE_DIMENSIONS.get(native_type, [])
        if native_type == "PAVE":
            attributes.append("FRAD")
        schema = self.read("schema", self.bridge.element_schema, resolved, attributes, 0, 50)
        self.identity(schema.get("element"))
        unsupported = [row.get("requested_name") for row in schema.get("attributes", [])
                       if isinstance(row, dict) and row.get("supported") is not True]
        if unsupported:
            self.unknown("schema_attributes_unsupported", "Requested attributes are unsupported or unknown", attributes=unsupported)
        if schema.get("success") is not True or schema.get("attribute_page", {}).get("has_more") is not False:
            self.unknown("schema_incomplete", "Schema inspection completeness could not be verified")
        bounds = self.read("bounds", self.bridge.geometry_bounds, resolved)
        self.identity(bounds)
        self.unknown("bounds_coordinate_frame_unverified", "Bounds retain their SDK-native unverified coordinate frame; no WORLD or exact-solid claim")
        if bounds.get("success") is not True or bounds.get("available") is not True or bounds.get("partial") is not False:
            self.unknown("bounds_incomplete", "Native bounds are unavailable or incomplete")
        if native_type == "PANE":
            geometry = self.read("geometry", self.bridge.panel_geometry, resolved)
            self.identity(geometry.get("element"))
            if geometry.get("complete") is not True or geometry.get("success") is not True:
                self.unknown("geometry_incomplete", "Native panel inspection is incomplete")
            if geometry.get("topology_valid") is not True or geometry.get("unsupported_features"):
                self.unknown("panel_topology_unverified", "Panel topology is unsupported, incomplete or has findings")
        elif native_type in _PRIMITIVE_DIMENSIONS:
            geometry = self.read("geometry", self.bridge.primitive_geometry, resolved)
            self.identity(geometry.get("element"))
            if geometry.get("complete") is not True or geometry.get("success") is not True:
                self.unknown("geometry_incomplete", "Native primitive inspection is incomplete")
        else:
            self.unknown("geometry_unsupported", "No shape-specific reader for exact native type; no owner/PANE substitution", native_type=native_type)
        display = self.read("display", self.bridge.display_state, resolved)
        self.identity(display.get("element"))
        if (display.get("success") is not True or display.get("complete") is not True or
                display.get("primitive_scan_complete") is not True):
            self.unknown("display_incomplete", "Display enumeration is incomplete; absent entries do not prove invisibility")
        if self.include_image:
            self.read("pre_capture_view", self.bridge.view_state)
            self.read("capture", self.bridge.capture_view, self.width, self.height, capture=True)
        self.read("final_view", self.bridge.view_state)
        if self.include_clipping:
            self.read("final_clipping", self.bridge.view_clipping_state)
        self.read("final_selection", self.bridge.graphical_selection, 200)
        self.gate("final", final=True)
        self.result.update(success=True, workflow_completed=True)

    def observations(self) -> None:
        gates = self.evidence["gates"]
        done = self.result["workflow_completed"]
        statuses = [_object(row.get("status")) for row in gates]
        projects = [_object(_object(row.get("project")).get("project")) for row in gates]
        capture_result = _object(_object(self.evidence.get("capture")).get("result"))
        observation = _object(capture_result.get("observation"))
        native = _object(capture_result.get("native_capture_observation"))
        capture_states: list[Any] = []
        if "capture" in self.evidence:
            before = _object(_object(observation.get("before")).get("result"))
            previous_states = before.get("states")
            capture_states.extend(previous_states if isinstance(previous_states, list) and previous_states else [None])
            capture_states.append(observation.get("after"))
            capture_states.extend({"ok": True, "result": native.get(key)}
                                  for key in ("before_refresh", "before_bitmap", "after_bitmap"))
            statuses.extend(_object(value) for value in (
                before.get("context_before"), before.get("context_after"), observation.get("context_after")))
        contexts = [tuple(row.get(key) for key in _CONTEXT_FIELDS) if isinstance(row, dict) and
                    all(isinstance(row.get(key), str) and row[key] for key in _CONTEXT_FIELDS) else None
                    for row in statuses]
        identities = [tuple(row.get(key) for key in _PROJECT_FIELDS) if isinstance(row, dict) and
                      all(isinstance(row.get(key), str) and row[key] for key in _PROJECT_FIELDS) else None
                      for row in projects]
        states = [self.evidence[key] for key in ("baseline_view", "pre_capture_view", "final_view") if key in self.evidence]
        states.extend(capture_states)
        view = view_observation_result(states, self.evidence.get("initial_status"),
                                       self.evidence.get("final_status"), {})
        camera_unchanged = view["stable"] if done or view["stable"] is False else None
        if view["complete"] is not True:
            self.unknown("view_observation_unverified", "View comparison has incomplete sampled coverage", details=view["read_errors"])
        selections = [_object(_object(self.evidence.get(key)).get("result")) for key in ("baseline_selection", "final_selection")]
        selection_fields = ("elements", "selection_count", "view_id", "drawlist_id", "ce_before", "ce_after")
        selection_complete = done and all(isinstance(row, dict) and row.get("success") is True and
            row.get("available") is True and row.get("complete") is True and row.get("ce_unchanged") is True and
            row.get("context_unchanged") is True and all(key in row and row[key] is not None for key in selection_fields)
            for row in selections)
        selection_values = [tuple(row.get(key) for key in selection_fields) if isinstance(row, dict) and
                            all(key in row and row[key] is not None for key in selection_fields) else None for row in selections]
        selection_unchanged = _tri_compare(selection_values, complete=selection_complete)
        if any(isinstance(row, dict) and (row.get("ce_unchanged") is False or
                                         row.get("context_unchanged") is False) for row in selections):
            selection_unchanged = False
        if not selection_complete:
            self.unknown("selection_observation_unverified", "Graphical selection comparison is incomplete")
        ce_values = [row.get("current_element") if isinstance(row, dict) else None for row in projects]
        ce_values += [_object(_object(row).get("result")).get("current_element_refno") for row in states]
        ce_values += [row.get(key) if isinstance(row, dict) else None for row in selections for key in ("ce_before", "ce_after")]
        display = _object(_object(self.evidence.get("display")).get("result"))
        if isinstance(display, dict):
            ce_values += [display.get("ce_before"), display.get("ce_after")]
        if not self.include_image:
            capture = {"status": "not_requested", "consistent": None, "complete": None}
        else:
            changed = observation.get("consistent") is False or native.get("view_object_unchanged") is False
            verified = (self.image is not None and capture_result.get("success") is True and observation.get("complete") is True and
                        observation.get("consistent") is True and native.get("complete") is True and
                        native.get("view_object_unchanged") is True)
            capture = {"status": "changed" if changed else "consistent" if verified else "unverified",
                       "consistent": False if changed else True if verified else None,
                       "complete": observation.get("complete") is True and native.get("complete") is True}
            if not verified:
                self.unknown("capture_unverified", "Capture consistency changed or could not be verified; image bytes do not prove target visibility")
        observations = {
            "session_build_version_unchanged": _tri_compare(contexts, complete=done),
            "project_mdb_user_unchanged": _tri_compare(identities, complete=done),
            "current_element_unchanged": _tri_compare(ce_values, complete=done),
            "camera_and_top_level_drawlist_samples_unchanged": camera_unchanged,
            "selection_samples_unchanged": selection_unchanged,
            "capture": capture,
        }
        if self.include_clipping:
            clipping = clipping_observation_result(
                self.evidence.get("baseline_clipping"), self.evidence.get("final_clipping"),
                self.evidence.get("baseline_view"), self.evidence.get("final_view"), complete=done)
            observations["clipping_samples_unchanged"] = clipping["unchanged"]
            if clipping["complete"] is not True:
                self.unknown("clipping_observation_unverified", "Clipping comparison has incomplete sampled coverage",
                             details=clipping["read_errors"])
        values = [value for value in observations.values() if not isinstance(value, dict)]
        if self.include_image:
            values.append(capture["consistent"])
        observations["observed_window_unchanged"] = False if False in values else True if all(value is True for value in values) else None
        if False in values:
            self.unknown("observed_context_changed", "Native observation samples changed; cause is not attributed and no state was restored")
        self.result["observations"] = observations

    def run(self) -> dict[str, Any] | ImageToolResult:
        try:
            if self.mode != "bridge":
                self.result["source"] = "mock"
                raise _Stop("native_unavailable", "Native element diagnosis is unavailable in mock mode")
            if self.version != "E3D21":
                self.result["source"] = "unsupported"
                raise _Stop("unsupported_version", "Native element diagnosis is implemented only for E3D21")
            self.result["source"] = "bridge"
            self.workflow()
        except _Stop as exc:
            self.result["errors"].append({"stage": self.stage, "code": exc.code, "message": str(exc), **exc.details})
            self.result["stopped_stage"] = self.stage
        except Exception as exc:
            self.result["errors"].append({"stage": self.stage, "code": "invalid_response", "message": str(exc)})
            self.result["stopped_stage"] = self.stage
        try:
            self.observations()
        except Exception as exc:
            # A malformed native observation must not discard earlier evidence
            # or a usable PNG while constructing the diagnostic summary.
            self.result["errors"].append({"stage": "observations", "code": "observation_error", "message": str(exc)})
            self.result.update(success=False, workflow_completed=False, stopped_stage="observations")
            self.result["observations"] = {key: None for key in (
                "session_build_version_unchanged", "project_mdb_user_unchanged", "current_element_unchanged",
                "camera_and_top_level_drawlist_samples_unchanged", "selection_samples_unchanged", "observed_window_unchanged")}
            self.result["observations"]["capture"] = {
                "status": "unverified" if self.include_image else "not_requested", "consistent": None, "complete": None}
            if self.include_clipping:
                self.result["observations"]["clipping_samples_unchanged"] = None
        self.result["complete"] = self.result["workflow_completed"] and not self.result["unknown"] and not self.result["errors"]
        envelope = {"ok": True, "version": self.version, "result": self.result}
        if self.image is not None:
            return ImageToolResult(self.image, "image/png", envelope)
        return envelope


def diagnose_element(bridge: Any, refno: str, *, include_image: bool = False, include_clipping: bool = False,
                     capture_width: int = 1280, capture_height: int = 960) -> dict[str, Any] | ImageToolResult:
    """Diagnose an exact target with read-only stages and optional native image.

    Invalid input raises before any bridge call. Native failures instead return
    retained evidence and stop the composition; no stage is retried or restored.
    """
    reference = _reference(refno)
    if not isinstance(include_image, bool):
        raise ValueError("include_image must be a boolean")
    if not isinstance(include_clipping, bool):
        raise ValueError("include_clipping must be a boolean")
    for name, value in (("capture_width", capture_width), ("capture_height", capture_height)):
        if isinstance(value, bool) or not isinstance(value, int) or not 64 <= value <= 2048:
            raise ValueError(name + " must be an integer between 64 and 2048")
    return _Diagnosis(bridge, reference, include_image, capture_width, capture_height, include_clipping).run()

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import urllib.parse
from pathlib import Path
from typing import Any

from . import __version__
from .bridge import BridgeClient, BridgeError, PML_SCRIPT_MAX_BYTES, element_access_refnos, graphical_selection_parameters, native_clash_parameters, normalize_expected_element_type, pml_script_request_id, pml_script_utf8, spatial_map_status_parameters
from .config import Settings
from .context_snapshot import context_snapshot
from .diagnosis_receipt import compact_diagnosis
from .diagnostic_comparison import compare_diagnostic_receipts
from .diagnostic_receipts import DiagnosticReceiptStore
from .element_diagnosis import diagnose_element
from .log_tail import read_log_tail
from .panel_topology import analyze_panel_loop_relations
from .primitive_mesh import derive_primitive_mesh
from .generated_csg import validate_generated_csg_response
from .panel_mesh import derive_panel_mesh
from .orthographic_drawing import generate_orthographic_drawing, _make_svg
from .orthographic_png import rasterize_orthographic_png
from .nozzle_orientation_drawing import build_nozzle_orientation_drawing
from .nozzle_orientation_sheet import build_nozzle_orientation_sheets, normalize_nozzle_sheet_options
from .equipment_layout_drawing import build_equipment_layout_drawing
from .orthographic_dxf import export_orthographic_dxf, export_drawing_sheet_dxf
from .panel_boundary_drawing import build_panel_boundary_drawing, render_panel_boundary_png
from .panel_boundary_drawing import compose_panel_boundary_page
from .panel_curve_workflow import PanelCurveReadError, read_panel_curve_source
from .drawing_assembly import MAX_DRAWING_SOURCES, WORLD_FRAME, combine_drawing_meshes
from .drawing_sheet import build_drawing_sheet, normalize_sheet_options
from .drawing_sheet_render import render_drawing_sheet_svg, rasterize_drawing_sheet_png
from .mesh_section import normalize_section_plane, slice_triangle_mesh
from .section_hatching import normalize_section_hatch, build_section_hatching
from .section_assembly import annotate_section_sources, annotate_section_diagnostics
from .section_dimensions import normalize_section_dimensions, section_dimension_revision, resolve_section_dimensions
from .triangle_mesh_validation import MAX_TRIANGLES, MAX_VERTICES, validate_triangle_mesh
from .triangle_mesh_metrics import measure_triangle_mesh
from .protocol import ImageToolResult, MultiImageToolResult, McpServer


_ELEMENT_MESH_TYPES = ("PANE", "BOX", "CYLI", "CONE", "DISH")
MAX_DRAWING_ATLAS_PAGES = 24
MAX_DRAWING_ATLAS_SOURCES = MAX_DRAWING_SOURCES * MAX_DRAWING_ATLAS_PAGES

_CHORD_TOLERANCE_SCHEMA = {"type": "number", "exclusiveMinimum": 0,
    "description": "Optional maximum analytic operand surface deviation in model millimetres. Refines each curved parent and cutter separately, using radial_segments as a minimum. Impossible tolerance or geometry budget fails without partial geometry. Does not certify native tessellation or Boolean intersection accuracy."}

# Generated DISH mapping is an audited analytic reconstruction rather than a
# native tessellation. Keep its default visibly smooth at the scale used by
# equipment drawings while leaving the public, opt-in switch unchanged.
_GENERATED_DISH_DEFAULT_CHORD_TOLERANCE_MM = 0.5
# Engineering drawings need a tighter circular profile than the mesh tool's
# compatibility default of 48 facets.  Keep this default at the operation
# layer so callers can still opt into a stricter value explicitly and the
# standalone mesh contract remains unchanged.
_CSG_DRAWING_DEFAULT_CHORD_TOLERANCE_MM = 0.25


def _chord_tolerance(arguments):
    value = arguments.get("chord_tolerance_mm")
    if "chord_tolerance_mm" in arguments and (type(value) not in {int, float} or not math.isfinite(value) or value <= 0):
        raise ValueError("chord_tolerance_mm must be a finite positive number")
    return float(value) if value is not None else None


def _drawing_option_schema(*, projection_sheet: bool = True) -> dict[str, Any]:
    schema = {
        "views": {"type": "array", "minItems": 1, "maxItems": 3, "uniqueItems": True,
                  "items": {"type": "string", "enum": ["FRONT", "TOP", "SIDE"]},
                  "default": ["FRONT", "TOP", "SIDE"],
                  "description": "FRONT looks from +Y, TOP from +Z, SIDE from +X."},
        "feature_angle_degrees": {"type": "number", "minimum": 0, "maximum": 180, "default": 30,
                                  "description": "Minimum adjacent-face dihedral angle for feature edges; silhouettes are always retained."},
        "hidden_line_removal": {"type": "boolean", "default": True,
                                "description": "Use bounded projected-depth occlusion analysis. Work-budget exhaustion returns no partial drawing."},
        "show_hidden_lines": {"type": "boolean", "default": False,
                              "description": "Include occluded edge portions as dashed lines in PNG/SVG and on separate hidden-line layers in optional DXF. Requires hidden_line_removal=true."},
        "include_svg": {"type": "boolean", "default": False,
                        "description": "Include SVG source in structured data; the PNG preview is always returned on success."},
        "include_dxf": {"type": "boolean", "default": False,
                        "description": "Include ASCII DXF R2000 source, with layered millimetre linework and overall projected dimensions. Hidden edges use dashed per-view layers when show_hidden_lines=true."},
        "preview_width": {"type": "integer", "minimum": 64, "maximum": 2048, "default": 1200,
                          "description": "PNG width in pixels. Width × height is capped at 2,000,000 pixels."},
        "preview_height": {"type": "integer", "minimum": 64, "maximum": 2048, "default": 440,
                           "description": "PNG height in pixels. Width × height is capped at 2,000,000 pixels."},
        "radial_segments": {"type": "integer", "minimum": 12, "maximum": 99, "default": 48,
                            "description": "Curve resolution for CYLI, CONE and DISH only."},
        "sheet": {"type": "object", "properties": {
            "paper_size": {"type": "string", "enum": ["A4", "A3"], "default": "A3"},
            "orientation": {"type": "string", "enum": ["landscape", "portrait"], "default": "landscape"},
            "scale_denominator": {"type": "number", "exclusiveMinimum": 0,
                                  "description": "Model-to-paper scale 1:N. Omit for a fitting standard reduction scale; explicit scales that cannot fit fail without cropping."},
            "title": {"type": "string", "minLength": 1, "maxLength": 120, "default": "E3D ORTHOGRAPHIC DRAWING",
                      "pattern": "^[A-Za-z0-9 .+/:_-]*$",
                      "description": "Sheet title; normalized to uppercase. Supported characters are ASCII letters/digits, space and . + / : _ -."},
            "drawing_number": {"type": "string", "maxLength": 40, "default": "",
                               "pattern": "^[A-Za-z0-9 .+/:_-]*$"},
        }, "additionalProperties": False,
            "description": "Opt into a bordered printable sheet and title block, shared by PNG/SVG/DXF. Sheet coordinates and DXF geometry become paper millimetres at scale 1:N; structured model bounds and dimension values remain true model millimetres. Omit to retain ordinary 1:1 DXF linework."},
    }
    if projection_sheet:
        schema["sheet"]["properties"]["projection_method"] = {
            "type": "string", "enum": ["first_angle", "third_angle"],
            "description": "Opt into physically consistent, aligned three-view layout. Requires all FRONT/TOP/SIDE. FRONT is recomputed from -Y (screen +X/+Z), TOP from +Z (+X/+Y), right SIDE from +X (+Y/+Z). Third-angle puts TOP above FRONT and SIDE right; first-angle puts TOP below and SIDE left. All views share one scale. Omit to retain legacy +Y FRONT and horizontal columns. This does not create native Draft or certify a complete drawing standard."}
        schema["views"]["description"] = "Legacy FRONT looks from +Y, TOP from +Z, SIDE from +X; opting into sheet.projection_method changes FRONT to -Y and requires all three views."
    return schema


def _section_option_schema() -> dict[str, Any]:
    return {
        "plane": {"type": "object", "properties": {
            "origin": {"type": "array", "minItems": 3, "maxItems": 3,
                       "items": {"type": "number", "minimum": -1_000_000_000, "maximum": 1_000_000_000},
                       "description": "Point on the plane in source-frame millimetres; also the section 2D origin."},
            "normal": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"},
                       "description": "Nonzero plane normal, normalized by the tool; points toward the viewer."},
            "up": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"},
                   "description": "Optional screen-up hint, projected onto the plane. Must not be parallel to normal. Defaults to a stable principal-axis hint."},
        }, "required": ["origin", "normal"], "additionalProperties": False},
        "hatch": {"type": "object", "properties": {
            "spacing_mm": {"type": "number", "minimum": 0.000001, "maximum": 1_000_000_000,
                           "description": "Required parallel-line spacing in section model millimetres. Paper spacing is this value divided by the sheet scale denominator."},
            "angle_degrees": {"type": "number", "minimum": 0, "exclusiveMaximum": 180, "default": 45,
                              "description": "Counterclockwise line direction from the section right axis."},
            "phase_mm": {"type": "number", "minimum": -1_000_000_000, "maximum": 1_000_000_000, "default": 0,
                         "description": "Pattern offset along the perpendicular (-sin(angle), cos(angle)) axis from the section origin; reduced modulo spacing."},
            "fill_rule": {"type": "string", "enum": ["nonzero"], "default": "nonzero",
                          "description": "Fill where the summed oriented contour winding is nonzero; this does not infer native hole or CSG semantics."},
        }, "required": ["spacing_mm"], "additionalProperties": False,
            "description": "Opt into verified bounded section hatching. Invalid contour geometry or exceeded work/output limits fails without a partial drawing; section/source evidence is retained."},
        "dimensions": {"type": "object", "properties": {
            "expected_section_revision": {"type": "string", "minLength": 64, "maxLength": 64,
                "pattern": "^[0-9a-fA-F]{64}$", "description": "Copy section.dimension_revision from an earlier section receipt. Changed source geometry or plane rejects stale vertex selectors."},
            "items": {"type": "array", "minItems": 1, "maxItems": 8, "items": {
                "type": "object", "properties": {
                    "first_vertex": {"type": "integer", "minimum": 0, "description": "Zero-based index in section.vertices."},
                    "second_vertex": {"type": "integer", "minimum": 0, "description": "Zero-based index in section.vertices."},
                    "axis": {"type": "string", "enum": ["horizontal", "vertical"],
                             "description": "Measure absolute section-u or section-v separation, not 3D distance."},
                    "side": {"type": "string", "enum": ["top", "bottom", "left", "right"],
                             "description": "Horizontal dimensions use top/bottom (default top); vertical dimensions use left/right (default right). Paper lanes are assigned in item order."},
                }, "required": ["first_vertex", "second_vertex", "axis"], "additionalProperties": False}},
        }, "required": ["expected_section_revision", "items"], "additionalProperties": False,
            "description": "Optional vertex-referenced section axis dimensions on a printable sheet; requires sheet. Values remain model millimetres in all formats. Preflight validates structure; actual indices and revision are verified after the read. Unsupported, stale or unrepresentable dimensions fail without a partial drawing."},
        **{key: value for key, value in _drawing_option_schema(projection_sheet=False).items() if key in {
            "include_svg", "include_dxf", "preview_width", "preview_height", "radial_segments", "sheet"}},
    }


def _composite_option_schema() -> dict[str, Any]:
    schema = {key: value for key, value in _drawing_option_schema().items() if key != "views"}
    section = _section_option_schema()
    schema.update({key: section[key] for key in ("plane", "hatch", "dimensions")})
    schema["sheet"]["properties"]["projection_method"].update(
        default="third_angle",
        description="Aligned three-view arrangement; defaults to third_angle for this composition tool. SECTION occupies the remaining cell in its independent plane frame. All four views share one scale.")
    schema["sheet"]["description"] = "Required printable sheet shared by PNG/SVG/DXF. Fixed FRONT/TOP/SIDE plus an arbitrary-plane SECTION; coordinates are paper millimetres and dimension values are model millimetres."
    return schema



def _integer(arguments: dict[str, Any], name: str, default: int, minimum: int, maximum: int) -> int:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


def _string(arguments: dict[str, Any], name: str, default: str = "") -> str:
    value = arguments.get(name, default)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _with_capture_receipt(bridge: BridgeClient, name: str, handler: Any, settings: Settings | None = None):
    """Validate receipt options before work, then capture only after confirmed success."""
    def wrapped(arguments: dict[str, Any]) -> Any:
        capture_after = arguments.get("capture_after", False)
        if not isinstance(capture_after, bool):
            raise ValueError("capture_after must be a boolean")
        width = _integer(arguments, "capture_width", 1280, 64, 2048)
        height = _integer(arguments, "capture_height", 960, 64, 2048)
        operation: dict[str, Any]
        try:
            operation = handler(bridge, settings, arguments) if settings is not None else handler(bridge, arguments)
        except Exception as exc:
            operation = {"ok": False, "error": str(exc)}
            if isinstance(exc, BridgeError):
                operation.update(exc.diagnostics)
                if exc.http_status is not None:
                    operation["http_status"] = exc.http_status
                if exc.response_invalid:
                    operation["response_invalid"] = True
            reason = "operation_failed"
            may_have_mutated = name in {"e3d_fit_view", "e3d_restore_view"} or arguments.get("dry_run", True) is False
            native_result = operation.get("result")
            native_outcome_uncertain = (
                operation.get("outcome_uncertain") is True
                or operation.get("creation_result_unknown") is True
                or isinstance(native_result, dict) and (
                    native_result.get("outcome_uncertain") is True
                    or native_result.get("creation_result_unknown") is True
                )
            )
            native_retry_unsafe = (
                operation.get("retry_safe") is False
                or isinstance(native_result, dict) and native_result.get("retry_safe") is False
            )
            if native_retry_unsafe:
                operation["retry_safe"] = False
            elif operation.get("retry_safe") is not True and isinstance(native_result, dict) and native_result.get("retry_safe") is True:
                operation["retry_safe"] = True
            native_delete_not_attempted = (
                name == "e3d_delete_element"
                and isinstance(native_result, dict)
                and native_result.get("delete_attempted") is False
                and native_result.get("verification_status") == "not_attempted"
                and native_result.get("outcome_uncertain") is False
                and native_result.get("retry_safe") is True
            )
            bridge_failure_uncertain = isinstance(exc, BridgeError) and (
                exc.transport_error or exc.response_invalid or exc.outcome_uncertain
                or name == "e3d_delete_element" and not native_delete_not_attempted
                or native_outcome_uncertain
            )
            if may_have_mutated and bridge_failure_uncertain:
                recovery = (
                    "Query e3d_pml_script_status with the original pml_script_request identifiers; "
                    "do not automatically resend the script."
                    if name == "e3d_execute_pml_script" else
                    "Read back the target state only after command state confirms database_ready=true; "
                    "a latched fault must be recovered first."
                )
                operation.update({
                    "outcome_uncertain": True,
                    "retry_safe": False,
                    "error": (f"{exc} Operation outcome is unknown; the original operation may already have taken effect. "
                              f"Do not automatically resend the operation. {recovery}"),
                })
                reason = "operation_outcome_unknown"
            if not capture_after:
                if operation.get("outcome_uncertain") is True or "retry_safe" in operation:
                    return operation
                raise
            return _capture_receipt_result(operation, capture_after, width, height, reason)
        if not capture_after:
            return operation
        has_preview = name not in {"e3d_fit_view", "e3d_restore_view"}
        result = operation.get("result") if isinstance(operation, dict) else None
        dry_run = has_preview and (arguments.get("dry_run", True) or operation.get("dry_run") is True
                                  or isinstance(result, dict) and result.get("dry_run") is True)
        if dry_run:
            return _capture_receipt_result(operation, True, width, height, "dry_run")
        if not _receipt_operation_succeeded(name, operation):
            reason = ("operation_outcome_unknown" if operation.get("outcome_uncertain") else
                      "operation_not_ready" if name == "e3d_restore_view" and not McpServer._operation_failed(operation) else "operation_failed")
            return _capture_receipt_result(operation, True, width, height, reason)
        return _capture_after_operation(bridge, operation, width, height, name)

    return wrapped


def _receipt_operation_succeeded(name: str, operation: Any) -> bool:
    if not isinstance(operation, dict) or McpServer._operation_failed(operation) or operation.get("ok") is not True:
        return False
    if name == "e3d_restore_view":
        return isinstance(operation.get("result"), dict) and operation["result"].get("ready") is True
    if name == "e3d_execute_pml_batch":
        results = operation.get("results")
        return isinstance(results, list) and bool(results) and all(
            isinstance(item, dict) and item.get("success") is True for item in results
        )
    result = operation.get("result")
    return isinstance(result, dict) and result.get("success") is True


def _capture_receipt_result(operation: Any, requested: bool, width: int, height: int, reason: str) -> dict[str, Any]:
    metadata = dict(operation) if isinstance(operation, dict) else {"ok": False, "error": str(operation)}
    metadata["receipt"] = {
        "operation_replayed": False,
        "capture": {"success": False, "status": "skipped", "reason": reason,
                    "requested": requested, "width": width, "height": height},
    }
    return metadata


def _camera_capture_agreement(expected: Any, captured: Any, *, observed: bool) -> bool | None:
    """Compare a requested/read-back pose to the actual pre-bitmap SDK sample."""
    def components(value: Any, require_observation: bool) -> tuple | None:
        if not isinstance(value, dict):
            return None
        if require_observation:
            if value.get("success") is not True or value.get("available") is not True:
                return None
            if any(isinstance(value.get(key), bool) or not isinstance(value.get(key), int) or value[key] < 0
                   for key in ("view_id", "drawlist_id")):
                return None
        def number(item: Any) -> bool:
            if not isinstance(item, (int, float)) or isinstance(item, bool):
                return False
            try:
                return math.isfinite(item)
            except (OverflowError, ValueError):
                return False
        vectors = []
        for key in ("centre", "direction"):
            vector = value.get(key)
            if not isinstance(vector, (list, tuple)) or len(vector) != 3 or not all(number(x) for x in vector):
                return None
            vectors.append(tuple(vector))
        norm = math.hypot(*vectors[1])
        if not math.isfinite(norm) or norm <= 0:
            return None
        radius, twist = value.get("radius"), value.get("extra_twist_degrees")
        if not number(radius) or radius <= 0 or not number(twist) or not isinstance(value.get("perspective"), bool):
            return None
        return (*vectors[0], *(x / norm for x in vectors[1]), radius, twist, value["perspective"])

    request = components(expected, observed)
    actual = components(captured, True)
    if request is None or actual is None:
        return None
    if observed and any(expected[key] != captured[key] for key in ("view_id", "drawlist_id")):
        return False
    return (request[-1] == actual[-1] and all(
        abs(a - e) <= max(1e-5, abs(e) * 1e-6) for a, e in zip(actual[:-1], request[:-1])
    ))


def _capture_after_operation(bridge: BridgeClient, operation: dict[str, Any], width: int, height: int,
                             operation_name: str = "") -> Any:
    metadata = dict(operation)
    try:
        image = _capture_view(bridge, {"width": width, "height": height})
        if not isinstance(image, ImageToolResult) or McpServer._operation_failed(image.metadata):
            capture = image.metadata if isinstance(image, ImageToolResult) else image
            error = "Native view capture did not return an image"
            capture_state = None
            capture_details = {}
            if isinstance(capture, dict):
                capture_result = capture.get("result")
                if isinstance(capture_result, dict):
                    capture_details = {key: value for key, value in capture_result.items() if key != "image_base64"}
                error = str(capture.get("error") or
                            (capture_result.get("error") if isinstance(capture_result, dict) else None) or error)
                capture_state = capture.get("state")
            capture_failure = {**capture_details, "success": False, "status": "failed", "error": error,
                               "width": width, "height": height}
            if isinstance(capture_state, dict):
                capture_failure["state"] = capture_state
            metadata["receipt"] = {
                "operation_replayed": False,
                "capture": capture_failure,
            }
            return metadata
        image_info = image.metadata.get("result", {})
        capture_metadata = {**image_info, "success": True, "status": "captured"}
        if operation_name in {"e3d_set_view", "e3d_fit_view", "e3d_set_camera"}:
            result = operation.get("result", {})
            native = image_info.get("native_capture_observation")
            at_bitmap = native.get("before_bitmap") if isinstance(native, dict) else None
            requested = result.get("requested_camera", result.get("plan"))
            actual = result.get("actual_camera", result.get("after"))
            capture_metadata["operation_camera_agreement"] = {
                "requested_matches_capture": _camera_capture_agreement(requested, at_bitmap, observed=False),
                "readback_matches_capture": _camera_capture_agreement(actual, at_bitmap, observed=True),
                "scope": "Comparison with the native camera sampled immediately before GetBitmap; "
                         "later user navigation is not undone. Inspect observation for changes across the capture window.",
            }
        metadata["receipt"] = {"operation_replayed": False, "capture": capture_metadata}
        return ImageToolResult(image.data, image.mime_type, metadata)
    except Exception as exc:
        capture_failure = {"success": False, "status": "failed", "error": str(exc),
                           "width": width, "height": height}
        if isinstance(exc, BridgeError):
            capture_failure.update(exc.diagnostics)
        metadata["receipt"] = {
            "operation_replayed": False,
            "capture": capture_failure,
        }
        return metadata


def create_server(settings: Settings | None = None) -> McpServer:
    settings = settings or Settings.from_env()
    bridge = BridgeClient(settings)
    receipt_store = DiagnosticReceiptStore(settings.version or settings.default_version)
    server = McpServer("aveva-e3d-mcp", __version__, instructions=(
        f"This MCP instance serves only {settings.version or settings.default_version} in {settings.mode} mode. "
        f"Command execution is {'enabled' if settings.allow_commands else 'disabled'}. "
        "Read the e3d://<version>/status and e3d://<version>/project resources for the current connection and project snapshot, or use e3d_status and e3d_project_info tools. "
        "The e3d_inspect_context and e3d_diagnose_element prompts provide read-only workflows for context checks and explicit-target diagnosis. "
        "Use e3d_context_snapshot for a bounded connection/project/MDB check in one call. Inspect context_complete, "
        "readiness and session_coherence rather than treating ok=true as native readiness. Matching health brackets "
        "do not make the project observation atomic or authorize writes. "
        "The e3d_diagnose_element tool executes a bounded read-only diagnosis on E3D21, preserving exact-target geometry, display and context evidence in one response; include_image=true also returns the current native view, and include_clipping=true samples native clipping state. Inspect workflow completion, unknowns, errors and observed changes separately; a successful diagnostic does not prove engineering validity or pixel visibility. "
        "Request evidence_detail=compact to omit redundant successful gate/capture samples while retaining geometry and display evidence; full is the default. Failed, changed or unverified observations retain full evidence. This changes response detail only, not native reads or image bytes. "
        "Opt in with retain_receipt=true to retain the original full diagnosis and any PNG in this MCP process for up to 30 minutes, subject to FIFO capacity limits. Read that same historical evidence with e3d_diagnostic_receipt; lookup never contacts E3D. IDs do not survive MCP process restarts and missing receipts do not prove non-execution. "
        "Use e3d_compare_diagnostic_receipts with two retained IDs to compare historical target, geometry, display, camera/selection and image metadata without new native reads. Check compatibility, per-section availability, traversal limits and each diagnosis's unknowns; payload equality does not prove no model change or successful engineering work. "
        "Use e3d_database_state on E3D21 to inspect MDB database access flags and session identifiers; retain its context_id when paging and check page_complete/read_errors. These flags do not establish edit permission or SAVEWORK safety. "
        "Use e3d_element_ancestry with an explicit numeric refno to read raw SDK and formatted references, OWNER ancestry, WORLD frame and database identity/MDB membership. Preserve unknown identities and incomplete reads; formatted references must not replace raw references or supply inferred database numbers. "
        "Use e3d_element_ancestry_batch for 1 to 16 unique numeric references with one shared MDB scan and UI callback; inspect shared context/errors and ordered per-target status, including not_started after a native fault. "
        "Use e3d_element_access for current-session access and target claim observations on 1 to 32 explicit references. Preserve nullable value/status/source fields, scan_complete and all_values_available; success does not establish future write permission or claimability. Claims are not refreshed, and PRMOWN is returned only as a reference. "
        "e3d_spatial_map_status currently resolves database identity but suspends native CheckSpatialMap after an E3D21 stack-overflow crash; expect native_spatial_map_check_suspended, check_attempted=false and status=null. Do not retry via PML MAP CHECK or build maps as a workaround. Historical NOWARNINGS is distinct from UPTODATE and does not itself prove geometry correctness. "
        "Use e3d_query for direct references or a bounded global lookup; use e3d_find_descendants for explicit-root paged searches with completeness diagnostics. Use e3d_element_snapshot for hierarchy, e3d_list_members for paged direct children, e3d_read_attributes for native values, "
        "e3d_spatial_info for numeric coordinates in an explicit reference frame, and e3d_transform_coordinates to convert known points/vectors between frames. "
        "Use e3d_validate_model to check required types and the published per-shape dimension_check_rules: BOX/NBOX and CYLI/NCYL dimensions are positive, CONE/NCON allows one zero end diameter but not both, and DISH allows RADI=0. Inspect unverified_dimensions (including unsupported NOZZ.DIAM), check_read_errors, checks_complete and valid separately. A true result covers only listed dimension rules and required types, not Boolean subtraction, engineering design, surface topology or rendering. Dimension values are read through explicit SDK conversion to millimetres. "
        "Use e3d_measure_elements_batch to inspect spatial coordinates, native bounds and gross/net volume for up to 32 explicit elements in one E3D UI callback; inspect each measurement's success/complete and preserve partial results. A single callback is not a database lock and native bounds retain their unverified coordinate-frame contract. Use e3d_geometry_relation for one ordered pair or e3d_geometry_relation_batch for up to 32 ordered pairs in a single UI callback; preserve raw booleans and unknowns without turning false into an engineering clearance verdict. "
        "Use e3d_graphical_selection to identify the native interaction selection before inspecting selected objects. Unknown or incomplete selection reads do not mean nothing is selected; current view metadata does not prove the selection belongs to that view or to the Design Explorer. "
        "To replace or clear that selection, preview e3d_set_graphical_selection with explicit numeric refnos (empty clears), then execute with its revision. Only already displayed targets qualify; no graphics are added. Read readback_verified from the independent UI callback and preserve unknown outcomes without replay or rollback. "
        "Inspect e3d_connection_points for native PPOS/PDIR, PPBO bore and connection metadata in an explicit frame before planning piping. Use e3d_check_connection_pair when two explicit elements and P-point numbers need a single-read geometric proximity/direction/PCREF evidence check; it is not an engineering or catalogue compatibility verdict. "
        "Element POS, local axes and rendered flange dimensions do not establish connection-point position, direction, nominal size or engineering compatibility. "
        "Use e3d_element_schema before planning edits to discover legal child types, attribute data types and current SDK settable state. Set include_constraints=true with an explicit refno and 1 to 10 named attributes to inspect SDK allowed values, raw range boundaries and whether those constraints are optional; range units and boundary encoding remain unverified. "
        "Before creating negative geometry, use e3d_element_schema to check the explicit positive primitive's legal children; create an NCYL or NBOX inside that primitive with e3d_create_element only when current SDK parent compatibility permits it. "
        "Compare e3d_geometry_volume before and after negative creation and inspect the native view; a volume reduction alone does not prove a through hole. "
        "e3d_native_clash_check currently supports bounded input previews only; native_spatial_map_check_suspended blocks map checks and actual Clasher execution after an E3D21 stack-overflow crash. "
        "The following map admission and actual-check details describe the suspended experimental implementation, not current permission to execute: UPTODATE or NOWARNINGS was required; inspect input_spatial_maps_accepted and spatial_map_admission_policy. input_spatial_maps_up_to_date means exact UPTODATE. Direct BOX/CYLI/CONE roots remain preview-only. "
        "The NOWARNINGS admission is based on a local E3D21 MAP CHECK reporting zero discrepancies for the same DB; it does not rewrite status or prove native geometry classification. "
        "After the input-map and explicit-scope gates pass, actual native checks set and verify options.override=true; inspect override_policy and retained warnings from other MDB databases. A local PML comparison required OVERRIDE ON to get one known clash; SDK Check=true alone does not establish that geometry was computed. "
        "A local isolated EQUI/BOX positive SDK fixture has returned one HH clash with verified BOX endpoints; this evidence does not validate CYLI/CONE, negative cuts or general result units/frames. "
        "Db.IsExtract=true inputs are preview-only because CheckSpatialMap resolves DB Number without verifying ExtractNumber; a nonzero extract_number alone does not identify an extract DB. "
        "Real native checks require command permission and change global clash diagnostic settings, including obstruction/touch options; full state is not automatically restored. Preserve all four native classifications and no_clear_verdict; empty results never prove clearance. "
        "Its input/output limits do not bound native computation. Native-check transport failures must not trigger PML cancellation or automatic replay. "
        "Create geometry with e3d_create_element using an explicit parent, and position it with e3d_set_transform in an explicit frame. Transform writes now finish with one global Command.Update notification; inspect notification_update and its post-callback readback. write_applied and rollback_succeeded describe the observations before notification. Notification failure never causes automatic replay or another rollback. This does not prove spatial-map repair. "
        "For a solid polygon panel, use e3d_create_panel with local 2D vertices and inspect its native loops with e3d_panel_geometry. "
        "For a multi-loop PANE, use e3d_panel_loop_relations to inspect pairwise local-XY containment, separation, boundary intersection/contact, or unknown results; never infer outer/inner or hole semantics from containment alone. "
        "For supported simple positive primitives, e3d_primitive_mesh returns a parameter-derived local triangle approximation with native frame provenance; it rejects negatives/children and is not native CSG or rendered geometry. "
        "To reshape an existing panel, inspect e3d_panel_geometry, preview e3d_update_panel, and execute with the preview's revision token before checking readback or an image receipt. "
        "To resize or move a native primitive, inspect e3d_primitive_geometry, preview e3d_update_primitive, execute with its revision token, then check native volume and an image. "
        "Primitive updates can preserve negative children and their local cuts while the positive parent moves; native inspection determines eligibility. "
        "To copy supported native geometry, preview e3d_copy_element with explicit source, destination parent and new name, then execute with its revision token and inspect the source-to-copy mapping and readback. "
        "Inspect the rendered model with e3d_capture_view, which returns a native E3D image without desktop automation. "
        "Read the native camera with e3d_view_state; use e3d_set_view for explicit camera presets and optional fitting without changing Drawlist membership. "
         "Use e3d_view_clipping_state to inspect the current View3D clipping reader, clip-box data and six box-face flags. It is read-only; preserve null fields and read_errors when the SDK returns partial or context-changed evidence. "
        "Use e3d_observe_view for bounded read-only observations across UI callbacks. Its stable flag only covers the stated observation window. "
        "Inspect image observation and operation_camera_agreement: a valid PNG can accompany a changed or unverified view. Never replay an edit or reset a camera merely to make these observations agree. "
        "For an explicitly requested return to an observed pose, pass the five native camera fields from e3d_view_state to e3d_set_camera, preview the current context and execute with its revision; this restores only those fields within native tolerance. "
        "For missing geometry or unexpected appearance, inspect e3d_drawlist_snapshot and e3d_display_state with the explicit target reference; database existence does not imply independent view membership. "
        "e3d_display_state distinguishes the requested element, its exact membership, the SDK-resolved member and existing native primitive entries; an ancestor member is not the requested element. "
        "e3d_set_drawlist_visual only edits exact top-level members, and add=true does not guarantee that a primitive becomes one. Inspect display state before retrying a rejected add. Native display properties and absent primitive entries do not establish pixel visibility. "
        "To remove an exact top-level member from the current view, preview e3d_remove_drawlist_member and execute the same refno with its revision. This removes display membership without deleting database geometry. Member visibility=false can leave visible primitive overrides; do not treat that field as whole-equipment removal. Overlapping memberships or selected target relationships require explicit resolution, never an automatic ancestor substitution. "
        "When drawlist_visual_preparation is advertised, an actual member update first refreshes graphics before its visual setters and verifies existing observed values. Only newly generated entries inside the target database subtree are admitted; inspect preparation_verified and materialized_primitive_keys. Previews and exact no-ops do not perform this refresh. Later unexpected entries still fail strict verification, and preparation failure does not trigger compensating visual writes. "
        "Read e3d_display_snapshot for the complete observed display maps. For existing native primitive entries, preview e3d_set_primitive_visual and execute the same requested changes with its returned revision; it never adds members or creates entries. Multiple owner aliases for one database reference are verified together. Only the five observed visual properties are verified, not override masks or pixel visibility. "
        "Restoring primitive transparency to 0 can require the public aggregate setter on its SDK-resolved member, followed by compensation of all existing primitive values and verification of both complete maps. Inspect transparency_reset_members and setter_attempts entries prefixed member_visual_properties:. If the resolved member must finish with nonzero transparency while a primitive needs restoration to 0, preview and execution reject the unsupported combination before writing. A member transparency change to 100 is also rejected before writing because its individual setter clamps to 99; already-matching observations may remain unchanged. "
        "For command errors, inspect e3d_read_messages and e3d_command_state; e3d_cancel_command attempts recovery. "
        "Use e3d_reenter_design only when a debug restart is needed; it saves work and issues DESIGN. "
        "View restoration is explicit via e3d_restore_view, never automatic on startup. "
        "Tools with dry_run default to preview; real execution requires dry_run=false and command permission. "
        "A PML dry run does not prove syntax, geometry or successful execution. "
        "When pml_nested_scope is advertised, ordinary PML requests and individual batch items run in independent nested scopes; local ! variables must not be assumed to survive across calls/items. Exact SAVEWORK/DESIGN recovery retains current-scope execution. "
        "Use e3d_execute_pml_script for complete PML source with IF/DO blocks; the adapter owns its temporary macro file and runs it in an independent nested scope. "
        "Preview checks source and strict native encoding only, never syntax. Inspect source_sha256, encoding, native_run_returned, execution_completed, cleanup and error_metadata; failed scripts may have executed a prefix and are not rolled back or automatically retried. "
        "The full source is not echoed in script receipts. Local ! variables exist only within that script; !! globals remain explicit session state. "
        "Script execution assigns a request_id and guards the target session; transport timeout/disconnection preserves an uncertain outcome without automatic command-state inspection, cancellation or replay. Use e3d_pml_script_status with pml_script_request identifiers to read the original receipt. "
        "Status pending does not prove SDK execution began; unknown_or_expired does not prove non-execution. Receipts are session-local and bounded to 64 records, expiring 30 minutes after completion. "
        "pml_alerts reports observed Druid PML error forms. A cancelled/idle command does not prove pending PML input was cleared; inspect pml_input_cleared and timeout message_diagnostics without replaying uncertain writes. "
        "Never add elements to the Drawlist, restore saved view state, or fit the camera automatically after edits. "
        "When requesting an after-operation image, the target must already be in the active Drawlist. "
        "For a sequence of edits, set capture_after=true on the final editing step to return the updated image to the user. "
        "Native geometry notifications may change camera framing. If a requested image needs target framing, explicitly call e3d_set_view with fit=target and capture_after=true after the confirmed edit; never replay the edit just to obtain a better image. "
        "For a PML batch receipt, set capture_after=true on the batch call; capture happens once after the final command. "
        "Check isError, per-command results, snapshot_info and field availability before claiming success."
    ))

    server.add_tool(
        "e3d_status",
        "Start here: check the assigned E3D version, adapter connection, running session/build, MCP mode and command permission.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        lambda _: _status(bridge, settings),
        read_only=True,
    )
    server.add_tool(
        "e3d_context_snapshot",
        "Read connection, project, MDB and current-element evidence in one bounded read-only workflow. "
        "Uses at most health -> project -> health, with no retries or polling; project is skipped unless "
        "the first health sample positively establishes native readiness and version/session/build identity. "
        "Inspect context_complete, readiness, session_coherence, evidence and errors. Busy, disconnected "
        "or unknown readiness is observed data, not permission to continue SDK reads. Matching health "
        "samples establish bracket consistency only: reads are not atomic and the project response is not "
        "directly session-bound. Missing user/current-element fields stay unknown. Never establishes "
        "future readiness, edit permission, SAVEWORK safety or an empty PML input stack. "
        "Does not read retained messages, execute commands, cancel, restart, capture or change model/view state.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        lambda args: _context_snapshot(bridge, settings, args),
        read_only=True,
    )
    resource_version = urllib.parse.quote(settings.version or settings.default_version, safe="")
    server_status_uri = f"e3d://{resource_version}/status"
    server_project_uri = f"e3d://{resource_version}/project"
    server.add_resource(
        server_status_uri,
        "e3d_status",
        f"Current connection, E3D session/build, observed diagnostics and MCP mode for {resource_version}. Read on demand; not cached.",
        "application/json",
        lambda: _status(bridge, settings),
    )
    server.add_resource(
        server_project_uri,
        "e3d_project",
        f"Current project, MDB, connection and current element for {resource_version}. Read on demand; not cached.",
        "application/json",
        lambda: bridge.project(),
    )
    server.add_prompt(
        "e3d_inspect_context",
        "Read the assigned E3D connection and project context, then report readiness and identity without changing state.",
        [],
        lambda _: (
            f"Inspect this E3D MCP instance ({resource_version}) with exactly one call to e3d_context_snapshot. "
            "Do not combine separately sampled status and project resources: the project response has no session/build binding. "
            "Inspect workflow_completed, context_complete, readiness before and after, session_coherence, evidence and errors. "
            "Only report project/MDB/current-element fields when context_complete=true; require both readiness samples ready and "
            "session_coherence.status=consistent before describing the bracketed context. The health samples make this a bounded "
            "bracket, not an atomic read, session-bound project response, or guarantee of future readiness/write permission. "
            "If the snapshot is incomplete, not ready, changed, or contains errors, stop and report the raw unknown/failure evidence. "
            "Do not independently continue with the project resource after a failed snapshot. "
            "Do not dismiss dialogs, cancel commands, issue DESIGN, run PML, or modify the model. "
            "Keep missing fields unknown and separate direct observations from conclusions."
        ),
        title="Inspect E3D Context",
    )
    server.add_prompt(
        "e3d_diagnose_element",
        "Diagnose one explicitly identified E3D element and a reported geometry or display symptom using read-only evidence.",
        [
            {"name": "refno", "description": "Explicit /NAME or =database/element reference; never use current CE implicitly.", "required": True},
            {"name": "symptom", "description": "Observed geometry or display problem to investigate.", "required": True},
        ],
        lambda args: _e3d_diagnosis_prompt(args, resource_version, server_status_uri, server_project_uri),
        title="Diagnose E3D Element",
    )
    server.add_tool(
        "e3d_project_info",
        "Read the current project, MDB, and current element for this E3D version. Read-only.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        lambda _: bridge.project(),
        read_only=True,
    )
    from .database_tools import register_database_tools
    register_database_tools(server, bridge)
    from .element_ancestry import register_element_ancestry
    register_element_ancestry(server, bridge)
    from .element_ancestry_batch import register_element_ancestry_batch
    register_element_ancestry_batch(server, bridge)
    from .team_database_identity import register_team_database_identity_manifest
    register_team_database_identity_manifest(server, bridge)
    from .branch_snapshot import register_branch_tools
    register_branch_tools(server, bridge)
    from .equipment_manifest import register_equipment_manifest
    register_equipment_manifest(server, bridge)
    from .equipment_child_schema import register_equipment_child_schema
    register_equipment_child_schema(server, bridge)
    from .pipe_member_manifest import register_pipe_member_manifest
    register_pipe_member_manifest(server, bridge)
    from .equipment_model_plan import register_equipment_model_plan
    register_equipment_model_plan(server, bridge, settings)
    from .piping_route_plan import register_piping_route_plan
    register_piping_route_plan(server, bridge)
    from .piping_batch_route_plan import register_piping_batch_route_plan
    register_piping_batch_route_plan(server, bridge)
    from .piping_native_route import register_native_piping_route
    register_native_piping_route(server, bridge)
    from .modeling_team import register_modeling_team
    register_modeling_team(server, bridge, settings)
    from .paragon_catalogue import register_paragon_catalogue_manifest
    register_paragon_catalogue_manifest(server, bridge)
    from .module_context import register_module_context
    register_module_context(server, bridge)
    from .paragon_schema import register_paragon_catalogue_schema
    register_paragon_catalogue_schema(server, bridge)
    from .paragon_component_template import register_paragon_component_template
    register_paragon_component_template(server, bridge)
    from .paragon_reference_trace import register_paragon_reference_trace
    register_paragon_reference_trace(server, bridge)
    from .paragon_component_copy import register_paragon_component_copy
    register_paragon_component_copy(server, bridge)
    from .paragon_component_parameters import register_paragon_component_parameters
    register_paragon_component_parameters(server, bridge)
    from .paragon_component_create import register_paragon_component_create
    register_paragon_component_create(server, bridge)
    from .paragon_standard_generation import register_paragon_standard_generation
    register_paragon_standard_generation(server, bridge)
    from .paragon_generation_plan import register_paragon_generation_plan
    register_paragon_generation_plan(server, bridge)
    from .piping_connectivity_snapshot import register_connectivity_snapshot
    register_connectivity_snapshot(server, bridge)
    from .piping_specification_read import (
        PATH_CANDIDATE, PipingSpecificationReadRoute, register_piping_specification_read,
    )
    register_piping_specification_read(server, bridge, route=PipingSpecificationReadRoute(
        PATH_CANDIDATE, envelope_context_verified=True, native_fault_passthrough_verified=True,
    ))
    server.add_tool(
        "e3d_spatial_map_status",
        "Resolve one explicit Design database in the current E3D21 MDB. Native CheckSpatialMap is temporarily suspended following a verified E3D21 stack-overflow crash on 2026-11-16. "
        "An otherwise valid request returns native_spatial_map_check_suspended, check_attempted=false, check_completed=false and status=null; metadata may still be available. Do not substitute PML MAP CHECK or MAP BUILD to bypass this suspension. "
        "First use e3d_database_state to obtain database_number (its number), extract_number and context_id; pass context_id as expected_context to bind the session/project/MDB. "
        "Inspect check attempted/completed, status, its raw integer value, complete and errors; failed or unavailable fields remain null. "
        "NOWARNINGS is not equivalent to UPTODATE, does not necessarily mean damage, and no status proves geometry correctness or complete clash coverage. "
        "The SDK resolves CheckSpatialMap by database number only: extract databases and ambiguity from multiple extracts of the same number are rejected before Check. "
        "A primary database with extract_number=1 can be resolved; use native IsExtract rather than assuming every nonzero extract_number is an extract database. "
        "This read-only tool preserves native status and query messages, does not BuildSpatialMap, write model geometry, execute PML or capture images, and requires no command permission. "
        "The native query can emit diagnostics or affect internal diagnostic caches; read-only does not certify diagnostic-state invariance. "
        "Only E3D21 implements this tool; mock and other versions return explicit unavailability.",
        {"type": "object", "properties": {
            "database_number": {"type": "integer", "minimum": 1, "maximum": 2147483647,
                                "description": "number from e3d_database_state for a Design DB in the current MDB."},
            "extract_number": {"type": "integer", "minimum": 0, "maximum": 2147483647,
                               "description": "Exact extract_number from the same database-state row; nonzero does not imply IsExtract=true."},
            "expected_context": {"type": "string", "pattern": "^(?:[a-f0-9]{64})?$", "default": "",
                                 "description": "Optional context_id from e3d_database_state; empty omits this context guard."},
        }, "required": ["database_number", "extract_number"], "additionalProperties": False},
        lambda args: _spatial_map_status(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_query",
        "Look up a direct reference or perform the legacy bounded global type/name lookup. Global results do not certify complete coverage or provide continuation. Use e3d_find_descendants for a scoped paged search with explicit completeness and read errors.",
        {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "E3D query/refno expression, for example /SITE/PIPE-001 or PIPE"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
            },
            "required": ["expression"],
            "additionalProperties": False,
        },
        lambda args: _query(bridge, args),
        read_only=True,
    )
    server.add_tool(
        "e3d_read_logs",
        "Read recent E3D adapter diagnostics. The adapter controls which log source is exposed.",
        {
            "type": "object",
            "properties": {
                "tail": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
            },
            "additionalProperties": False,
        },
        lambda args: _read_logs(bridge, settings, _integer(args, "tail", 100, 1, 500)),
        read_only=True,
    )
    server.add_tool(
        "e3d_read_messages",
        "Read recent AVEVA internal message events captured inside the E3D Addin, including command errors that may otherwise appear in an E3D dialog.",
        {
            "type": "object",
            "properties": {
                "tail": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
            },
            "additionalProperties": False,
        },
        lambda args: bridge.messages(_integer(args, "tail", 100, 1, 500)),
        read_only=True,
    )
    server.add_tool(
        "e3d_read_message_window",
        "Read a session-bound, authenticated cursor window of captured E3D output in one managed-only request. "
        "Requires an E3D21 adapter advertising message_cursor_v1; an older tail response is never substituted. "
        "First call without a cursor reads the oldest retained output after sequence zero. Use checkpoint=true "
        "without a cursor to capture the current sequence without returning historical messages, then pass the "
        "returned next_cursor unchanged to collect subsequent output. Positions and overwrite counts are decimal "
        "strings to preserve Int64 precision. has_more/remaining_count describe pagination; overwritten_count "
        "describes messages already lost from the 500-entry ring. Empty output does not mean no error occurred. "
        "Cursor/session mismatch or future positions fail explicitly; do not silently reset the cursor. "
        "The window covers captured output only and never establishes command causation, database readiness "
        "or absence of faults. Does not use health, UI dispatch, SDK database reads, PML, cancellation, retries, "
        "capture, or model/view changes, including its native error path. Captured text may include PML output.",
        {"type": "object", "properties": {
            "cursor": {"type": "string", "maxLength": 1024, "default": "",
                       "description": "Opaque next_cursor returned by this adapter session; pass unchanged."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
            "checkpoint": {"type": "boolean", "default": False,
                           "description": "Capture the current position with no messages; cannot combine with a nonempty cursor."},
        }, "additionalProperties": False},
        lambda args: _read_message_window(bridge, args),
        read_only=True,
    )
    server.add_tool(
        "e3d_command_state",
        "Read whether the E3D Addin is busy executing a command or waiting on a dialog.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        lambda _: bridge.command_state(),
        read_only=True,
    )
    server.add_tool(
        "e3d_cancel_command",
        "Cancel an E3D command or dialog through E3D's internal CIE cancel event, prompt restoration, and modal-dialog recovery. This is intended for recovery when a command is waiting for input.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        lambda _: bridge.cancel_command(),
        destructive=False,
    )
    server.add_tool(
        "e3d_drawlist_snapshot",
        "Read the active E3D Drawlist and each top-level member's display properties.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        lambda _: bridge.drawlist(),
        read_only=True,
    )
    server.add_tool(
        "e3d_fit_view",
        "Fit the current E3D 3D view to the active Drawlist using E3D's graphics API; by default repopulate an empty Drawlist from the current EQUI and use an isometric direction so vertical face geometry is visible. Check result.persistence separately: a successful display operation can have a failed save, and an ordinary fit does not save the Drawlist. Do not replay a successful display operation to retry persistence.",
        {
            "type": "object",
            "properties": {
                "shaded": {"type": "boolean", "default": True},
                "isometric": {"type": "boolean", "default": True},
                "auto_add_current": {
                    "type": "boolean",
                    "default": True,
                    "description": "When the Drawlist is empty, add the current EQUI hierarchy before fitting.",
                },
            },
            "additionalProperties": False,
        },
        _with_capture_receipt(bridge, "e3d_fit_view", _fit_view),
        destructive=False,
        capture_receipt=True,
    )
    server.add_tool(
        "e3d_view_state",
        "Read the active native E3D camera, including centre, direction, radius, projection and view identifiers. Does not change the camera, current element or Drawlist.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        lambda args: _view_state(bridge, args),
        read_only=True,
    )
    server.add_tool(
        "e3d_view_clipping_state",
        "Read the active native E3D21 View3D clipping state, including global clipping, cap settings, SDK clip-box data and the six box-face flags. "
        "This may initialize the SDK's managed clipping cache but does not set clipping, refresh graphics, change the camera, modify the model or alter Drawlist membership. "
        "Preserve null fields, status, error_code and read_errors when clipping is unavailable, a getter fails or the view changes during the read; complete=true requires every read and view_unchanged=true. Unsupported versions report error_code=unsupported_version without contacting their bridge.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        lambda args: _view_clipping_state(bridge, args),
        read_only=True,
    )
    server.add_tool(
        "e3d_observe_view",
        "Observe the actual native camera, view identity and top-level Drawlist fingerprints across separate UI callbacks. "
        "Reports stable=true/false/null, completeness and observed differences across a bounded number of samples; null means unverified. HTTP timeouts may extend elapsed time. "
        "Optional include_clipping reads native clipping after the first and final view samples and includes their three-valued comparison in stable/complete; defaults false with no clipping reads. "
        "Retains raw baseline/final clipping receipts. Getters may initialize managed caches; coordinates and units remain SDK-native and unverified. Equal samples do not establish individual plane geometry or whether a target is clipped. "
        "Stable/complete samples do not certify database readiness or absence of native faults; inspect raw health and e3d_context_snapshot before further SDK work. "
        "Does not fit, restore, select, capture or change the view. Changes may include concurrent user interaction. "
        "Does not establish future stability, primitive appearance, model revisions or pixel visibility.",
        {"type": "object", "properties": {
            "samples": {"type": "integer", "minimum": 2, "maximum": 10, "default": 3},
            "interval_ms": {"type": "integer", "minimum": 50, "maximum": 500, "default": 100},
            "include_clipping": {"type": "boolean", "default": False,
                                 "description": "Add at most two native clipping reads and retain both receipts plus an unchanged true/false/unknown comparison. No clipping reads when false."},
        }, "additionalProperties": False},
        lambda args: _observe_view(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_display_state",
        "Read native display ownership and properties for one explicit E3D element. Reports the SDK top-level classification, exact Drawlist membership, resolved member identity and whether it matches the requested target, plus matching existing native primitive entries and their owner references. The SDK may resolve an ancestor member. Native primitive entries are reported as observed; no matching entry does not imply no rendering. Inspect complete, primitive_scan_complete and read_errors before drawing conclusions. Does not create primitive entries, change properties, add members, refresh graphics, save state, select CE or move the camera. Pixel visibility is not verified; use e3d_capture_view for an image. Before e3d_set_drawlist_visual, verify the target is an exact top-level member: add=true does not guarantee an independent primitive member.",
        {"type": "object", "properties": {
            "refno": {"type": "string", "description": "Required explicit /NAME or =database/element reference; current element is never implicit."},
        }, "required": ["refno"], "additionalProperties": False},
        lambda args: _display_state(bridge, args),
        read_only=True,
    )
    server.add_tool(
        "e3d_diagnose_element",
        "Collect bounded diagnostic evidence for one explicit E3D21 element: snapshot (depth 2, limit 100), selected attribute schema, native bounds, type-appropriate panel or primitive geometry, display ownership and sampled view/selection context. "
        "Checks readiness and session/project identity before each stage; stops on read failures and retains earlier evidence. Never substitutes an owner for the requested reference/type. "
        "Unsupported checks, truncated data and unverified coordinate frames remain unknown; workflow success is not an engineering or pixel-visibility verdict. "
        "Optional include_clipping samples native clipping before and after the diagnostic, retains both observations, and includes their three-valued comparison in observed_window_unchanged. Equal samples do not prove the target is visible, clipped or unaffected by individually defined planes; clipping coordinates and units remain SDK-native and unverified. Defaults false with no clipping reads. "
        "Optional include_image returns one native MCP PNG of the current view, which may not contain the target. Capture uses the existing graphics refresh and may process pending changes. "
        "evidence_detail defaults to full. Optional compact omits redundant successful gate/capture samples only after consistent observations, reports omissions, and keeps geometry/display evidence. Failure, drift or unverified observations retain full evidence; no native reads or image bytes are removed. "
        "Optional retain_receipt=true stores the original full evidence and any PNG before compaction. Inspect result.diagnostic_receipt: retention can fail independently of diagnosis; failed retention returns full evidence even if compact was requested. Storage is local to this MCP process, expires after 30 minutes, and has FIFO limits of 16 entries and 64 MiB of serialized metadata plus base64 image bytes. "
        "Observed view or selection changes are reported without restoration. Does not edit models, add display members, set the camera, change selection, execute PML, cancel, restart or retry. "
        "These are sequential live reads, not an atomic snapshot or a native execution-time bound. Available with a connected E3D21 bridge; mock mode reports unavailable. The same-named MCP prompt only renders guidance.",
        {"type": "object", "properties": {
            "refno": {"type": "string", "maxLength": 256,
                      "description": "Required explicit /NAME or =database/element reference; no implicit current element."},
            "include_image": {"type": "boolean", "default": False},
            "include_clipping": {"type": "boolean", "default": False,
                                 "description": "Read native clipping at baseline/final stages. Retains partial evidence and reports unchanged true/false/unknown without determining whether the target is clipped."},
            "retain_receipt": {"type": "boolean", "default": False,
                               "description": "Retain full historical evidence and any image in this MCP process for later lookup; no disk persistence."},
            "evidence_detail": {"type": "string", "enum": ["full", "compact"], "default": "full",
                                "description": "Response detail only. Compact retains full evidence on failed, changed or unverified observations."},
            "capture_width": {"type": "integer", "minimum": 64, "maximum": 2048, "default": 1280},
            "capture_height": {"type": "integer", "minimum": 64, "maximum": 2048, "default": 960},
        }, "required": ["refno"], "additionalProperties": False},
        lambda args: _diagnose_element(bridge, args, receipt_store), read_only=True,
    )
    server.add_tool(
        "e3d_diagnostic_receipt",
        "Read the original full diagnostic evidence from a receipt_id returned by e3d_diagnose_element with retain_receipt=true, optionally including the original PNG. "
        "Uses only this MCP process's bounded memory store and never contacts E3D, refreshes graphics or repeats a diagnosis. Evidence is historical and may describe an earlier E3D session. "
        "A successful lookup is distinct from the embedded diagnostic's success. Unknown, expired, evicted or other-process IDs return available=false and status=unknown_or_expired; missing evidence does not prove non-execution. Lookup does not extend expiry.",
        {"type": "object", "properties": {
            "receipt_id": {"type": "string", "pattern": "^diag_[0-9a-f]{32}$", "minLength": 37, "maxLength": 37},
            "include_image": {"type": "boolean", "default": False},
        }, "required": ["receipt_id"], "additionalProperties": False},
        lambda args: _diagnostic_receipt(receipt_store, args), read_only=True,
    )
    server.add_tool(
        "e3d_compare_diagnostic_receipts",
        "Compare two full retained diagnostic receipts in this MCP process without contacting E3D or extending retention. "
        "Requires completed diagnostic workflows with matching target reference/type, version, session/build and project/MDB/user context; names may differ. Original complete=false and unknown coverage remain visible. "
        "Returns bounded structural JSON differences for target, snapshot, schema, bounds, geometry, display, baseline/final view and selection, and image metadata. Baseline/final clipping sections are included only when either receipt requested clipping or contains clipping evidence. Dictionary key order is ignored; array order and boolean/integer/float types are significant. "
        "Missing stages are unknown, not model deletions. Inspect compatibility, sections, traversal_complete, truncated and original diagnostic summaries. Difference values may be bounded previews. "
        "Caller labels before/after do not establish chronology or causation. A same-ID comparison is only self-comparison. No tolerance, coordinate conversion, engineering verdict, visibility verdict or fresh screenshot is inferred; use e3d_diagnostic_receipt to retrieve original full evidence and PNG.",
        {"type": "object", "properties": {
            "before_receipt_id": {"type": "string", "pattern": "^diag_[0-9a-f]{32}$", "minLength": 37, "maxLength": 37},
            "after_receipt_id": {"type": "string", "pattern": "^diag_[0-9a-f]{32}$", "minLength": 37, "maxLength": 37},
            "difference_limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 100},
        }, "required": ["before_receipt_id", "after_receipt_id"], "additionalProperties": False},
        lambda args: _compare_diagnostic_receipts(receipt_store, args), read_only=True,
    )
    server.add_tool(
        "e3d_display_snapshot",
        "Read the current native display maps, including top-level members and their existing primitive entries. Takes no arguments and does not change the view, current element or saved state. Preserve and inspect complete, errors and nullable observations; partial reads do not prove absence. One database reference may have entries under multiple owner members. Observed visual properties do not prove override masks or pixel visibility, and observation hashes are not write revisions. Use e3d_set_primitive_visual preview to obtain a revision bound to a specific change request.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        lambda args: _display_snapshot(bridge, args),
        read_only=True,
    )
    server.add_tool(
        "e3d_set_primitive_visual",
        "Preview or update colour, transparency, edges, visibility or shallow on existing native primitive entries for an explicit reference. Native eligibility requires non-TOPF and PRMF; all owner aliases for that database reference are verified across the complete display maps. Never Add members, create primitive entries or substitute an ancestor as the requested target. Restoring transparency to 0 may use the public aggregate setter on the SDK-resolved member, then compensate all existing primitive values and verify that member values and all other primitive values are unchanged. Inspect transparency_reset_members and setter_attempts entries prefixed member_visual_properties: for attempted aggregate resets; those fields alone do not prove success. If the resolved member must finish nonzero while a primitive needs restoration to 0, both preview and execution reject before writing. At least one nonempty colour, transparency or non-null boolean is required; omitted/empty colour and null booleans preserve their current values. Preview defaults to true and returns a revision bound to the target, requested changes, session/context and complete display state. Execute the same changes with that preview revision as expected_revision and command permission. Read actual rollback/residual diagnostics after failure; only the five observed visual values are verified, not override masks or pixel visibility. Do not automatically replay uncertain writes.",
        {"type": "object", "properties": {
            "refno": {"type": "string", "description": "Required explicit /NAME or =database/element reference; never an implicit current element or ancestor."},
            "expected_revision": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$",
                                  "description": "Revision from the preview of the same target and visual changes; required for execution."},
            "colour": {"type": "string", "description": "Native colour name or index; omitted, empty or whitespace-only preserves the current colour."},
            "transparency": {"type": "integer", "minimum": 0, "maximum": 100},
            "edges": {"type": ["boolean", "null"]},
            "visibility": {"type": ["boolean", "null"]},
            "shallow": {"type": ["boolean", "null"],
                        "description": "Native on-top transient rendering; null preserves the current value. Does not control descendant inclusion."},
            "dry_run": {"type": "boolean", "default": True},
        }, "required": ["refno"], "additionalProperties": False,
            "anyOf": [
                {"required": ["colour"], "properties": {"colour": {"pattern": r"\S"}}},
                {"required": ["transparency"]},
                *({"required": [field], "properties": {field: {"type": "boolean"}}}
                  for field in ("edges", "visibility", "shallow")),
            ],
            "allOf": [{"if": {"required": ["dry_run"], "properties": {"dry_run": {"const": False}}},
                       "then": {"required": ["expected_revision"]}}],
        },
        _with_capture_receipt(bridge, "e3d_set_primitive_visual", _set_primitive_visual, settings),
        destructive=False,
        capture_receipt=True,
    )
    server.add_tool(
        "e3d_remove_drawlist_member",
        "Preview or remove one explicitly named native top-level member from the current E3D Drawlist using the public SDK. "
        "This removes display membership, not database geometry; it does not execute PML or substitute an ancestor for a primitive. "
        "Requires complete display observations and rejects overlapping displayed ancestors/descendants, crossing primitive aliases, "
        "or graphical selections overlapping the target. Inspect the rejection; selection changes must be explicit via e3d_set_graphical_selection. "
        "Preview defaults to true and returns a revision bound to the request, target identity, bridge instance, project/MDB, "
        "Drawlist, camera, current element, full display maps and ordered graphical selection. Execute the same refno with that revision "
        "and command permission. An already absent eligible top-level member is a verified no-op, not proof of pixel invisibility. "
        "Success verifies the target database identity still exists, all remaining member/primitive observations, camera, CE and selection. "
        "A native removal is dispatched at most once. Failure reports actual residuals without automatically re-adding or replaying; "
        "inspect outcome_uncertain and do not resend uncertain writes. Persistence status is separate from verified removal. "
        "Use capture_after for a native image; pixel visibility is not inferred from membership alone.",
        {"type": "object", "properties": {
            "refno": {"type": "string", "description": "Required explicit /NAME or =database/element reference for the exact top-level display member."},
            "expected_revision": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$",
                                  "description": "Revision from a current removal preview with the same refno; required for execution."},
            "dry_run": {"type": "boolean", "default": True},
        }, "required": ["refno"], "additionalProperties": False,
         "allOf": [{"if": {"required": ["dry_run"], "properties": {"dry_run": {"const": False}}},
                    "then": {"required": ["expected_revision"]}}]},
        _with_capture_receipt(bridge, "e3d_remove_drawlist_member", _remove_drawlist_member, settings),
        destructive=False,
        capture_receipt=True,
    )
    server.add_tool(
        "e3d_set_view",
        "Explicitly set a native E3D camera preset and optionally fit the existing Drawlist or one target's geometry bounds. Axis presets specify the native looking direction; isometric looks along normalized [1,1,-1]. A non-keep preset selects orthographic projection with zero twist. Preview defaults to true; execution requires command permission. Never adds elements, restores a saved view or selects a current element. A target must already be visible through the Drawlist for a useful image receipt.",
        {"type": "object", "properties": {
            "preset": {"type": "string", "anyOf": [
                {"enum": ["keep", "isometric", "x_positive", "x_negative", "y_positive", "y_negative", "z_positive", "z_negative"]},
                {"pattern": r"^\s*(?i:keep|isometric|x_positive|x_negative|y_positive|y_negative|z_positive|z_negative)\s*$"},
            ], "default": "isometric"},
            "fit": {"type": "string", "anyOf": [
                {"enum": ["none", "drawlist", "target"]},
                {"pattern": r"^\s*(?i:none|drawlist|target)\s*$"},
            ], "default": "none"},
            "target_refno": {"type": "string", "default": "", "description": "Required only for fit=target; /NAME or =database/element resolved by the SDK."},
            "padding": {"type": "number", "minimum": 1, "maximum": 4, "default": 1.15, "description": "Fit radius multiplier, used when fit is drawlist or target."},
            "dry_run": {"type": "boolean", "default": True},
        }, "additionalProperties": False,
         "allOf": [
             {"if": {"required": ["preset"], "properties": {"preset": {"pattern": r"^\s*(?i:keep)\s*$"}}},
              "then": {"required": ["fit"], "properties": {"fit": {"anyOf": [
                  {"enum": ["drawlist", "target"]}, {"pattern": r"^\s*(?i:drawlist|target)\s*$"},
              ]}}}},
             {"if": {"required": ["fit"], "properties": {"fit": {"pattern": r"^\s*(?i:target)\s*$"}}},
              "then": {"required": ["target_refno"],
                       "properties": {"target_refno": {"type": "string", "minLength": 1, "pattern": r"\S"}}}},
             {"if": {"required": ["target_refno"], "properties": {"target_refno": {"pattern": r"\S"}}},
              "then": {"required": ["fit"], "properties": {"fit": {"pattern": r"^\s*(?i:target)\s*$"}}}},
         ]},
        _with_capture_receipt(bridge, "e3d_set_view", _set_view, settings),
        destructive=False,
        capture_receipt=True,
    )
    server.add_tool(
        "e3d_set_camera",
        "Preview or set five explicit native E3D camera fields: centre, looking direction, radius, perspective and extra twist. "
        "Use values from e3d_view_state to return to a previously observed pose, with native floating-point readback tolerance. "
        "All five fields are required; coordinates use the SDK native frame and direction is normalized by the adapter. "
        "This does not restore other optics, clipping, viewport size or shading settings. Preview defaults to true; execute the "
        "same request with the preview revision as expected_revision and command permission. The revision binds the requested "
        "values, current camera and viewport, bridge instance, project/MDB, view, Drawlist, full display maps, CE and ordered "
        "graphical selection. State changes require a fresh preview. A camera already matching within readback tolerance skips "
        "camera setters and MapView. A real write maps "
        "once, then verifies camera readback, shading, display, selection and CE; failures preserve observed residuals without "
        "automatic retry or rollback. Never replay an uncertain write. Does not add graphics, fit geometry, select CE or save "
        "Drawlist state. capture_after returns a native picture and requested/readback camera agreement at bitmap time.",
        {"type": "object", "properties": {
            "centre": {"type": "array", "items": {"type": "number", "minimum": -3.4028234663852886e38,
                                                  "maximum": 3.4028234663852886e38}, "minItems": 3, "maxItems": 3,
                       "description": "Three finite SDK native coordinates, within the readable single-precision range."},
            "direction": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3,
                          "description": "Finite nonzero native looking vector; the adapter normalizes its length."},
            "radius": {"type": "number", "exclusiveMinimum": 0, "description": "Finite positive native camera radius."},
            "perspective": {"type": "boolean"},
            "extra_twist_degrees": {"type": "number", "description": "Finite native additional camera twist in degrees."},
            "expected_revision": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$",
                                  "description": "Fresh revision from the same five-field preview; required for execution."},
            "dry_run": {"type": "boolean", "default": True},
        }, "required": ["centre", "direction", "radius", "perspective", "extra_twist_degrees"],
         "additionalProperties": False,
         "allOf": [{"if": {"required": ["dry_run"], "properties": {"dry_run": {"const": False}}},
                    "then": {"required": ["expected_revision"]}}]},
        _with_capture_receipt(bridge, "e3d_set_camera", _set_camera, settings),
        destructive=False,
        capture_receipt=True,
    )
    server.add_tool(
        "e3d_restore_view",
        "Explicitly reload the saved E3D Drawlist and available display properties after a restart when view restoration is requested. Changes view membership; startup does not restore it automatically. Primitive transparency 0 can require a public aggregate reset of its resolved member, compensation of all existing primitive values and full-map verification; inspect transparency_reset_members. Schema-2 restoration rejects before setters when primitive transparency must become 0 but the resolved member must finish nonzero, or when member transparency must become 100 and its individual setter would clamp to 99. Check actual rollback/residual diagnostics on failure and result.persistence separately from restore readiness: a successful restore can have a failed save. A save failure does not replay the restore or prevent an optional native image receipt.",
        {
            "type": "object",
            "properties": {
                "fit": {
                    "type": "boolean",
                    "default": True,
                    "description": "Fit the 3D view after the saved elements are loaded.",
                },
            },
            "additionalProperties": False,
        },
        _with_capture_receipt(bridge, "e3d_restore_view", _restore_view),
        destructive=False,
        capture_receipt=True,
    )
    server.add_tool(
        "e3d_set_drawlist_visual",
        "Set color, edges, visibility, transparency, or shallow rendering on an exact top-level E3D Drawlist member, optionally requesting Add first. Use e3d_display_state to inspect a primitive's actual display owner before writing. Add does not guarantee the requested primitive becomes an independent top-level member; an ancestor resolved by the SDK is not an exact target match. Preview and execution reject a change to member transparency 100 before writing because the individual setter clamps to 99; an already-matching value can remain unchanged. They also reject a nonzero final resolved-member transparency when preserving a primitive requires restoring its transparency to 0. Otherwise zero restoration may use a public aggregate member reset plus compensation and verification of all existing primitive values; inspect transparency_reset_members and actual rollback/residual diagnostics. Success requires other member and primitive values to remain unchanged. Empty colour preserves the current colour; a nonempty value must resolve to a native colour in preview and execution before any Add or setter. Native shallow means on-top transient rendering regenerated every frame; it does not limit descendant inclusion. Drawlist membership and display settings do not prove pixel visibility. Check result.persistence separately: a failed save preserves the successful display operation and remains eligible for an optional native image receipt. Do not replay the display operation to retry persistence or a rejected Add.",
        {
            "type": "object",
            "properties": {
                "refno": {"type": "string"},
                "colour": {"type": "string", "default": ""},
                "transparency": {"type": ["integer", "null"], "minimum": 0, "maximum": 100},
                "edges": {"type": ["boolean", "null"]},
                "visibility": {"type": ["boolean", "null"]},
                "shallow": {"type": ["boolean", "null"],
                            "description": "Native on-top transient rendering: regenerated every frame without cached graphics. Does not control whether descendants are included; null preserves the current setting."},
                "add": {"type": "boolean", "default": True},
                "dry_run": {"type": "boolean", "default": True},
            },
            "required": ["refno"],
            "additionalProperties": False,
        },
        _with_capture_receipt(bridge, "e3d_set_drawlist_visual", _set_drawlist_visual, settings),
        destructive=False,
        capture_receipt=True,
    )
    server.add_tool(
        "e3d_element_snapshot",
        "Read an E3D hierarchy. expected_type uses the SDK type ShortName and requires the resolved type to match; the only cross-type exception is implicit TUBI sharing a numeric DBREF with its BRAN/ELBO anchor. Other mismatches fail. limit caps the total nodes including the root. snapshot_info reports omitted descendants, depth/limit truncation, and read errors.",
        {
            "type": "object",
            "properties": {
                "refno": {"type": "string", "default": ""},
                "expected_type": {"type": "string", "maxLength": 64, "default": "", "description": "Optional required Design type ShortName, e.g. TUBI. Only implicit TUBI has cross-type resolution from a shared BRAN/ELBO numeric DBREF; other mismatches fail."},
                "depth": {"type": "integer", "minimum": 0, "maximum": 8, "default": 2},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
            },
            "additionalProperties": False,
        },
        lambda args: _element_snapshot(bridge, args),
        read_only=True,
    )
    server.add_tool(
        "e3d_list_members",
        "Read one page of direct children in database order through bounded SDK member indexes. Pass next_cursor unchanged to continue; omit refno when continuing or use the same parent. expected_type uses the SDK type ShortName and requires the resolved parent type to match; the only cross-type exception is implicit TUBI sharing a numeric DBREF with its BRAN/ELBO anchor. The cursor retains that type and sibling index. Members include sibling_index so same-refno objects remain distinct. Cursors expire on E3D restart or project/MDB change. This is a live listing: edits between pages can invalidate the cursor. Inspect has_more and read_errors; null has_more means the page failed.",
        {
            "type": "object",
            "properties": {
                "refno": {"type": "string", "default": "", "description": "Parent /NAME or =database/element. Empty starts at the current element, or continues the parent encoded in cursor."},
                "expected_type": {"type": "string", "maxLength": 64, "default": "", "description": "Optional required parent type ShortName. Only implicit TUBI has cross-type resolution from a shared BRAN/ELBO anchor; omitted continuation inherits the cursor type."},
                "cursor": {"type": "string", "default": "", "maxLength": 2048, "description": "Opaque next_cursor returned by the previous page. Start with an empty cursor."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
            "additionalProperties": False,
        },
        lambda args: _list_members(bridge, args),
        read_only=True,
    )
    server.add_tool(
        "e3d_find_descendants",
        "Search descendants under one explicit root, excluding the root itself. Filter by exact native element type and/or literal case-insensitive name text. Pages visit at most 1000 candidates and return at most limit matches; empty pages can still have has_more=true. Follow next_cursor until has_more=false, then inspect scope_complete, complete, depth_truncated and read_errors. Cursors bind the E3D session, project, root and filters; omitted filters/depth inherit the cursor. This is a live traversal, not an immutable snapshot: concurrent model edits can change results. Does not execute PML, select CE or alter the view.",
        {"type": "object", "properties": {
            "root_refno": {"type": "string", "description": "Explicit /NAME or =database/element required on the first page; may be omitted with cursor."},
            "element_type": {"type": "string", "maxLength": 64, "description": "Exact SDK base type such as NCYL or PANE. Empty matches all types; omitted inherits cursor or defaults to empty on the first page."},
            "name_contains": {"type": "string", "maxLength": 256, "description": "Literal case-insensitive substring, not a regular expression or PML. Omitted inherits cursor or defaults to empty on the first page."},
            "max_depth": {"type": "integer", "minimum": 1, "maximum": 64, "description": "Direct children are depth 1. Defaults to 64 on the first page; omitted inherits cursor. Depth omissions set complete=false even when scope_complete=true."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            "cursor": {"type": "string", "maxLength": 16384, "default": "", "description": "Opaque next_cursor, returned unchanged. Expired, altered or out-of-scope cursors fail explicitly."},
        }, "additionalProperties": False},
        lambda args: _find_descendants(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_validate_model",
        "Check required element types and publish dimension_check_rules for BOX/NBOX, CYLI/NCYL, CONE/NCON and DISH in one E3D UI dispatch. Lengths are converted from the SDK's dimensioned value to explicit millimetres; box/cylinder dimensions must be positive, a cone may have one zero end diameter but not both, and DISH permits RADI=0 under the bounded MCP policy. NOZZ.HEIG is checked while unsupported NOZZ.DIAM is listed in unverified_dimensions; ordinary read/conversion errors appear in check_read_errors and native faults stop immediately. Inspect complete, checks_complete, valid, issues and unverified fields separately; definite invalid dimensions remain false even when another read is unknown. Negative-primitive dimension validity does not prove a Boolean cut. This does not verify the complete native geometry domain, engineering design, surface topology or rendering. Set include_snapshot=false for a compact result. A single UI dispatch is not a database lock.",
        {
            "type": "object",
            "properties": {
                "refno": {"type": "string", "default": ""},
                "depth": {"type": "integer", "minimum": 0, "maximum": 8, "default": 4},
                "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 200},
                "required_types": {"type": "array", "items": {"type": "string"}, "default": []},
                "include_snapshot": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
        lambda args: _validate_model(bridge, args),
        read_only=True,
    )
    server.add_tool(
        "e3d_read_attributes",
        "Read requested SDK attributes (for example POS, ORI, HEIG, OWNER, or :CUSTOM) without executing PML. expected_type requires the resolved type to match; the only cross-type alias resolution supported is implicit TUBI from its shared BRAN/ELBO numeric DBREF. The result reports the resolved type. Returns native formatted values plus typed_values for scalar numbers, strings and booleans, and per-attribute errors. Arrays and geometric values retain their native formatting; use e3d_spatial_info for explicit coordinates. Partial values are retained on error.",
        {
            "type": "object",
            "properties": {
                "refno": {"type": "string", "default": ""},
                "expected_type": {"type": "string", "maxLength": 64, "description": "Optional required Design type ShortName, e.g. TUBI. Only implicit TUBI has cross-type resolution from a shared BRAN/ELBO numeric DBREF; other mismatches fail."},
                "attributes": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 50},
            },
            "required": ["attributes"],
            "additionalProperties": False,
        },
        lambda args: _read_attributes(bridge, args),
        read_only=True,
    )
    server.add_tool(
        "e3d_read_attributes_batch",
        "Read the same requested SDK attributes from up to 50 explicitly referenced elements in one E3D UI-thread dispatch. Returns ordered rows with each element's refno/type/name, native formatted values, typed scalar values and per-attribute errors. The combined batch is limited to 500 element/attribute cells; duplicate refs and attributes are removed after normalization. No PML command, CE change or view change is performed. Check each row's success/errors and the top-level complete/ce_unchanged flags; partial rows are retained on read failure. One dispatch reduces inter-call drift but the response is not a durable database snapshot.",
        {"type": "object", "properties": {
            "refnos": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 50,
                        "description": "Unique explicit /NAME or =database/element references; current CE is never implicit."},
            "attributes": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 50,
                             "description": "Requested attribute names, such as NAME, DESC, HEIG, or POS."},
        }, "required": ["refnos", "attributes"], "additionalProperties": False},
        lambda args: _read_attributes_batch(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_element_access",
        "Read current-session generic update_allowed/read_only, LOCK/DBWRIT/OKCLA/DACCLA, database identity/access flags, "
        "PRMOWN reference and target claim LCLM/LCLMH/USERC/CLMNUM/EXTRC for 1 to 32 explicit elements in one E3D UI dispatch. "
        "Each fields/database/target_claim leaf retains nullable value, status, source and source_refno. Unknown or unavailable values are not false. "
        "scan_complete means planned reads were attempted, including ordinary read errors; all_values_available requires every leaf status to be ok. "
        "success can coexist with unavailable/unset fields and does not prove an element is writable, claimable or safe to modify later. "
        "claims_freshness is current_session_unverified and claims_refresh_attempted is false: no claim refresh, claim/release, PML, CE/view change or capture occurs. "
        "PRMOWN is not recursively inspected; target_claim describes only the requested target. Distinct input aliases remain ordered rows with native duplicate_of_index when resolved to the same element. "
        "Duplicate input strings after trimming/case normalization are rejected. Inspect row errors, CE/context identity and native_database_fault; partial rows and not_attempted fields remain explicit. "
        "Requires no command permission; this observation is not a database lock or authorization for a future operation.",
        {"type": "object", "properties": {
            "refnos": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 32,
                       "uniqueItems": True, "description": "Explicit /NAME or =database/element references in result order; current CE is never implicit."},
        }, "required": ["refnos"], "additionalProperties": False},
        lambda args: _element_access(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_measure_elements_batch",
        "Read spatial coordinates, native bounds and/or native gross/net volume for 1 to 32 explicit elements in one E3D UI callback. This reduces inter-call drift without a database lock. Inspect each row and measurement's availability, completeness and read errors; failed rows retain partial values. Spatial PAVE reads can have success=true and complete=false because ORI is unsupported. Bounds retain the SDK native (unverified) coordinate frame and are not implicitly WORLD. Does not select CE, change the view or execute PML.",
        {"type": "object", "properties": {
            "refnos": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 32,
                       "uniqueItems": True, "description": "Unique explicit /NAME or =database/element references, in result order."},
            "measurements": {"type": "array", "items": {"type": "string", "enum": ["spatial", "bounds", "volume"]},
                             "minItems": 1, "maxItems": 3, "uniqueItems": True,
                             "default": ["spatial", "bounds", "volume"]},
            "wrt": {"type": "string", "default": "WORLD", "description": "Spatial frame: WORLD, OWNER, /NAME or =database/element. Does not relabel bounds."},
            "volume_scope": {"type": "string", "enum": ["element", "members"], "default": "element"},
        }, "required": ["refnos"], "additionalProperties": False},
        lambda args: _measure_elements_batch(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_element_schema",
        "Inspect the current E3D element's legal child/owner types and attribute metadata without executing PML. expected_type requires the resolved type to match; the only cross-type alias resolution supported is implicit TUBI from its shared BRAN/ELBO numeric DBREF. The result reports the resolved type. Reports supported attributes, SDK data types, unit categories, current settable state, and direct-editor permission. Empty attributes lists the SDK's attributes in pages; named attributes allow explicit probing, including pseudo-attributes. include_constraints=true additionally reads allowed values/descriptions, raw range boundaries and values-or-ranges optionality for 1 to 10 explicit attributes and an explicit refno. Range units and boundary encoding are unverified. Per-attribute output is capped at 100 values/boundaries and 1024 UTF-16 characters per text; SDK arrays are materialized before these output limits. Unsupported or unknown attributes are findings; read_errors indicate failed inspection. Readability/settable state and returned constraints do not prove that a particular value or create command will succeed.",
        {
            "type": "object",
            "properties": {
                "refno": {"type": "string", "default": "", "description": "Element /NAME or =database/element; empty selects the current element."},
                "expected_type": {"type": "string", "maxLength": 64, "description": "Optional required Design type ShortName, e.g. TUBI. Only implicit TUBI has cross-type resolution from a shared BRAN/ELBO numeric DBREF; other mismatches fail."},
                "attributes": {"type": "array", "items": {"type": "string"}, "maxItems": 50, "default": [],
                               "description": "Attribute names to inspect, e.g. POS, HEIG, OWNER. Empty lists native SDK attributes, sorted by name."},
                "offset": {"type": "integer", "minimum": 0, "maximum": 100000, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                "include_constraints": {"type": "boolean", "default": False,
                                        "description": "Read native allowed values/ranges and optionality. Requires explicit refno and 1 to 10 distinct named attributes; false preserves ordinary schema reads."},
            },
            "additionalProperties": False,
        },
        lambda args: _element_schema(bridge, settings, args),
        read_only=True,
    )
    server.add_tool(
        "e3d_nozzle_orientation_drawing",
        "Read explicitly supplied E3D nozzle references and native PPOS/PDIR connection-point data in WORLD coordinates, then build a bounded TOP plan and FRONT elevation orientation drawing. The structured receipt preserves every native response, point availability, position and direction, plus computed horizontal bearing and tilt when PDIR is finite. A PNG preview is always returned for complete geometry; include_svg and include_dxf optionally add vector exports with PDIR arrowheads drawn as feature lines rather than equipment outlines. Optional sheet creates paged A3/A4 landscape sheets with matching point IDs, leader labels and a WORLD coordinate/bearing/tilt schedule; each page uses one model-to-paper scale for both views. Sheets admit up to 128 points, with a total preview budget of 8000000 pixels. Incomplete or unavailable native geometry returns evidence without a partial image or vector drawing. It never infers DN, connection compatibility, catalogue data or engineering validity, and it never traverses the model automatically. The tool does not modify the model, CE, Drawlist, camera or view.",
        {"type": "object", "properties": {
            "targets": {"type": "array", "minItems": 1, "maxItems": 32, "items": {
                "type": "object", "properties": {
                    "refno": {"type": "string", "minLength": 2, "maxLength": 1024,
                              "description": "Explicit /NAME or =database/element nozzle reference."},
                    "point_numbers": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 10000},
                                      "maxItems": 64, "uniqueItems": True, "default": [],
                                      "description": "Optional native P-point numbers; empty discovers supported PPLS entries."},
                }, "required": ["refno"], "additionalProperties": False}},
            "preview_width": {"type": "integer", "minimum": 320, "maximum": 2048, "default": 1200},
            "preview_height": {"type": "integer", "minimum": 240, "maximum": 2048, "default": 700},
            "include_svg": {"type": "boolean", "default": False,
                            "description": "Include TOP/FRONT vector SVG in the structured drawing."},
            "include_dxf": {"type": "boolean", "default": False,
                            "description": "Include ASCII DXF R2000 vector output for complete native point data; dimension annotations are omitted because the drawing is a nozzle-point layout, not an equipment envelope."},
            "sheet": {"type": "object", "description": "Optional printable point-orientation sheets; omitting this preserves the existing bare-view output.",
                      "properties": {
                          "size": {"type": "string", "enum": ["A3", "A4"], "default": "A3"},
                          "title": {"type": "string", "maxLength": 48, "default": "NOZZLE ORIENTATION"},
                          "drawing_number": {"type": "string", "maxLength": 32, "default": "E3D-NOZZLE"},
                          "points_per_page": {"type": "integer", "enum": [8, 16], "default": 8},
                      }, "additionalProperties": False},
        }, "required": ["targets"], "additionalProperties": False},
        lambda args: _nozzle_orientation_drawing(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_equipment_layout_drawing",
        "Read up to 256 explicitly referenced equipment elements in WORLD coordinates in batches of 32 and generate a read-only FRONT/TOP/SIDE equipment pose layout. The drawing uses verified WORLD POS and ORI only: each item is shown by an origin marker and available axis lines labelled X/Y/Z, while native extents are deliberately not invented from an unverified SDK LimitsBox. Only unique, identity-verified rows associated with requested_refno are drawn; missing, duplicate, malformed, unexpected and mismatched rows remain in receipts and make complete=false. When some requested positions are available, the structured result and PNG/SVG are marked partial; DXF requires a complete batch. This is not a shape-outline or dimensioned equipment drawing. It does not traverse automatically, modify the model, select CE, add Drawlist members or change camera/view.",
        {"type": "object", "properties": {
            "refnos": {"type": "array", "minItems": 1, "maxItems": 256, "uniqueItems": True,
                       "items": {"type": "string", "minLength": 2, "maxLength": 1024},
                       "description": "Explicit /NAME or =database/element references in layout order."},
            "views": {"type": "array", "minItems": 1, "maxItems": 3, "uniqueItems": True,
                      "items": {"type": "string", "enum": ["FRONT", "TOP", "SIDE"]},
                      "default": ["FRONT", "TOP", "SIDE"]},
            "axis_length_mm": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000000,
                                "default": 1000, "description": "World millimetres used for local-axis glyphs."},
            "preview_width": {"type": "integer", "minimum": 320, "maximum": 2048, "default": 1200},
            "preview_height": {"type": "integer", "minimum": 240, "maximum": 2048, "default": 700},
            "include_svg": {"type": "boolean", "default": False},
            "include_dxf": {"type": "boolean", "default": False},
        }, "required": ["refnos"], "additionalProperties": False},
        lambda args: _equipment_layout_drawing(bridge, args), read_only=True,
    )
    location_datum_schema = {"type": "object", "properties": {
        "label": {"type": "string", "minLength": 1, "maxLength": 32,
                  "description": "ASCII datum label; does not certify an equipment centre or axis."},
        "origin_mm": {"type": "array", "minItems": 3, "maxItems": 3,
                      "items": {"type": "number", "minimum": -1000000000, "maximum": 1000000000},
                      "description": "Explicit WORLD X/Y/Z datum in millimetres."},
    }, "required": ["label", "origin_mm"], "additionalProperties": False}
    location_output_schema = {
        "preview_width": {"type": "integer", "minimum": 320, "maximum": 2048, "default": 1400},
        "preview_height": {"type": "integer", "minimum": 240, "maximum": 2048, "default": 990},
        "include_svg": {"type": "boolean", "default": False},
        "include_dxf": {"type": "boolean", "default": False},
    }
    server.add_tool(
        "e3d_equipment_layout_sheet",
        "Read 1 to 256 explicit WORLD element origins and local orientations in batches of 32 and generate complete A3/A4 TOP/FRONT equipment location sheets. Stable E001 IDs, source references, leaders and signed DX/DY/DEL schedules share one datum, global bounds and position scale across all pages. Default datum is WORLD origin; +Y is not assumed to be plant north. Pose symbols have fixed paper sizes and are not equipment outlines or footprints. Only complete identity-verified native sources and matching session/project/MDB boundary observations produce sheets; observations are sequential, not an atomic snapshot. Returns one PNG per page and optional paper-mm SVG/DXF. Limits: 8 or 16 items per page, 8M total preview pixels, 8 MiB structured output and 8 MiB combined PNG bytes; any page, export or budget failure suppresses all page images. No automatic traversal, native Draft creation, model, CE, Drawlist or camera changes.",
        {"type": "object", "properties": {
            "refnos": {"type": "array", "minItems": 1, "maxItems": 256, "uniqueItems": True,
                       "items": {"type": "string", "minLength": 2, "maxLength": 1024}},
            "datum": location_datum_schema,
            "sheet": {"type": "object", "properties": {
                "size": {"type": "string", "enum": ["A3", "A4"], "default": "A3"},
                "title": {"type": "string", "maxLength": 48, "default": "EQUIPMENT LOCATION"},
                "drawing_number": {"type": "string", "maxLength": 32, "default": "E3D-EQUIPMENT"},
                "equipment_per_page": {"type": "integer", "enum": [8, 16], "default": 8},
            }, "additionalProperties": False},
            **location_output_schema,
        }, "required": ["refnos"], "additionalProperties": False},
        lambda args: _equipment_layout_sheet(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_nozzle_datum_sheet",
        "Read 1 to 32 explicit nozzle references and native WORLD PPOS/PDIR, then generate A3/A4 TOP/FRONT orientation sheets relative to a required user-declared WORLD datum. Stable N001 IDs and signed DX/DY/DEL schedules preserve full original WORLD coordinates and directions. All pages share one position scale and global bounds. PDIR bearing remains WORLD +Y toward +X and tilt toward +Z; a datum is not a verified equipment centre, radial nozzle angle or equipment axis. Requires complete native point data, verified source identity and WORLD frame, and matching session/project/MDB boundary observations. Up to 128 points, 8 or 16 per page; 8M total preview pixels, 8 MiB structured output and 8 MiB combined PNG bytes. Returns one PNG per page and optional paper-mm SVG/DXF; any failure suppresses all page images. Does not infer shape, DN, connection validity, traverse automatically, create native Draft or change model, CE, Drawlist or camera.",
        {"type": "object", "properties": {
            "targets": {"type": "array", "minItems": 1, "maxItems": 32, "items": {
                "type": "object", "properties": {
                    "refno": {"type": "string", "minLength": 2, "maxLength": 1024},
                    "point_numbers": {"type": "array", "maxItems": 64, "uniqueItems": True,
                                      "items": {"type": "integer", "minimum": 1, "maximum": 10000}},
                }, "required": ["refno"], "additionalProperties": False}},
            "datum": location_datum_schema,
            "sheet": {"type": "object", "properties": {
                "size": {"type": "string", "enum": ["A3", "A4"], "default": "A3"},
                "title": {"type": "string", "maxLength": 48, "default": "NOZZLE ORIENTATION"},
                "drawing_number": {"type": "string", "maxLength": 32, "default": "E3D-NOZZLE"},
                "points_per_page": {"type": "integer", "enum": [8, 16], "default": 8},
            }, "additionalProperties": False},
            **location_output_schema,
        }, "required": ["targets", "datum"], "additionalProperties": False},
        lambda args: _nozzle_datum_sheet(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_nozzle_axis_sheet",
        "Read one explicit EQUI WORLD POS/ORI and 1 to 32 explicit native nozzle PPOS/PDIR sources. Generate A3/A4 radial U/V and axial U/H point sheets around explicitly selected equipment local axial_axis and zero_axis. u is the positive zero axis, n is the positive axial axis and v=n cross u. Position angle is atan2(delta dot v, delta dot u); PDIR azimuth and tilt are separate values. On-axis point angles and axial-direction azimuths are unavailable. Stable point IDs, radius, signed axial offset and two distinct angles preserve original WORLD data and the native equipment frame. All pages share bounds and position scale. The equipment coordinate origin is not certified as a physical centre, and target ownership is not inferred. Requires complete identity-verified EQUI/nozzle data, matching native WORLD frames and session/project/MDB boundary observations. Limits: 128 points, 8/16 per page, 8M total preview pixels, 8 MiB structured output and 8 MiB combined PNG. Any failure suppresses every sheet image. No model, CE, Drawlist, camera, restart or native Draft writes.",
        {"type": "object", "properties": {
            "equipment_refno": {"type": "string", "minLength": 2, "maxLength": 1024},
            "targets": {"type": "array", "minItems": 1, "maxItems": 32, "items": {
                "type": "object", "properties": {
                    "refno": {"type": "string", "minLength": 2, "maxLength": 1024},
                    "point_numbers": {"type": "array", "maxItems": 64, "uniqueItems": True,
                                      "items": {"type": "integer", "minimum": 1, "maximum": 10000}},
                }, "required": ["refno"], "additionalProperties": False}},
            "axial_axis": {"type": "string", "enum": ["X", "Y", "Z"]},
            "zero_axis": {"type": "string", "enum": ["X", "Y", "Z"],
                          "description": "Positive local zero-angle axis, distinct from axial_axis."},
            "sheet": {"type": "object", "properties": {
                "size": {"type": "string", "enum": ["A3", "A4"], "default": "A3"},
                "title": {"type": "string", "maxLength": 48, "default": "NOZZLE AXIS ORIENTATION"},
                "drawing_number": {"type": "string", "maxLength": 32, "default": "E3D-NOZZLE-AXIS"},
                "points_per_page": {"type": "integer", "enum": [8, 16], "default": 8},
            }, "additionalProperties": False},
            **location_output_schema,
        }, "required": ["equipment_refno", "targets", "axial_axis", "zero_axis"], "additionalProperties": False},
        lambda args: _nozzle_axis_sheet(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_equipment_outline_sheet",
        "Read 1 to 16 explicit EQUI groups and a total of 1 to 128 selected typed material sources. Verify each resolved source is a native descendant of its assigned EQUI using bounded paged hierarchy observations, then project all material once with global TOP visibility and render one A3/A4 equipment layout sheet. Shows selected parameter geometry, equipment-origin IDs/leaders and WORLD XYZ plus signed DX/DY/DEL location schedules at one shared scale. A3/A4 landscape; explicit scale optional, otherwise fits. Every equipment requires at least one nonempty admitted source. This does not certify whole-equipment geometry, a native footprint, inter-source Boolean union or native cut application scope. A4 can reject dense 16-equipment layouts instead of dropping labels. Hierarchy proof is capped at 8 pages of up to 200 returned nodes per equipment; source reads remain sequential observations, not an atomic snapshot. Limits: 10k vertices/20k triangles, 2M preview pixels, 8 MiB structured output and 8 MiB combined PNG. Any context/source/membership/projection/layout/export/budget failure returns no sheet images. No automatic geometry selection, native Draft, model, CE, Drawlist or camera writes.",
        {"type": "object", "properties": {
            "equipment": {"type": "array", "minItems": 1, "maxItems": 16, "items": {
                "type": "object", "properties": {
                    "refno": {"type": "string", "minLength": 2, "maxLength": 1024},
                    "sources": {"type": "array", "minItems": 1, "maxItems": 128, "items": {
                        "type": "object", "properties": {
                            "refno": {"type": "string", "minLength": 2, "maxLength": 1024},
                            "expected_type": {"type": "string", "enum": list(_ELEMENT_MESH_TYPES)},
                            "geometry_mode": {"type": "string", "enum": ["parameter_mesh", "owner_member_difference"]},
                        }, "required": ["refno", "expected_type", "geometry_mode"], "additionalProperties": False}},
                }, "required": ["refno", "sources"], "additionalProperties": False}},
            "datum": location_datum_schema,
            "sheet": {"type": "object", "properties": {
                "size": {"type": "string", "enum": ["A3", "A4"], "default": "A3"},
                "title": {"type": "string", "maxLength": 48, "default": "EQUIPMENT OUTLINE LAYOUT"},
                "drawing_number": {"type": "string", "maxLength": 32, "default": "E3D-EQUIPMENT-OUTLINE"},
                "scale_denominator": {"type": "number", "exclusiveMinimum": 0},
            }, "additionalProperties": False},
            "radial_segments": {"type": "integer", "minimum": 12, "maximum": 99, "default": 24},
            "feature_angle_degrees": {"type": "number", "minimum": 0, "maximum": 180, "default": 30},
            "hidden_line_removal": {"type": "boolean", "default": True},
            "show_hidden_lines": {"type": "boolean", "default": False},
            **location_output_schema,
        }, "required": ["equipment"], "additionalProperties": False},
        lambda args: _equipment_outline_sheet(bridge, args), read_only=True,
    )
    selected_material_source_schema = {"type": "object", "properties": {
        "refno": {"type": "string", "minLength": 2, "maxLength": 1024},
        "expected_type": {"type": "string", "enum": list(_ELEMENT_MESH_TYPES)},
        "geometry_mode": {"type": "string", "enum": ["parameter_mesh", "owner_member_difference"]},
    }, "required": ["refno", "expected_type", "geometry_mode"], "additionalProperties": False}
    selected_geometry_options = {
        "chord_tolerance_mm": _CHORD_TOLERANCE_SCHEMA,
        "use_generated_dishes": {"type": "boolean", "default": False,
            "description": "Opt into the audited E3D21 Design BasicDIS profile mapping for selected standalone DISH sources. Combines native database dimensions and branch semantics with matching generated-CSG markers and WORLD transforms. Generic catalogue CSG_DISH decoding is unsupported. Requires stable observations; unresolved shapes fail without a drawing. Other sources retain their declared mode."},
        "radial_segments": {"type": "integer", "minimum": 12, "maximum": 99, "default": 24},
        "feature_angle_degrees": {"type": "number", "minimum": 0, "maximum": 180, "default": 30},
        "hidden_line_removal": {"type": "boolean", "default": True},
        "show_hidden_lines": {"type": "boolean", "default": False},
    }
    server.add_tool(
        "e3d_equipment_layout_tiles",
        "Generate fixed-scale A3/A4 TOP equipment layout tiles from 1 to 64 explicit EQUI groups and at most 128 selected typed material sources. Requires sheet.scale_denominator. Reads and verifies complete WORLD EQUI poses, selected material and bounded native descendant ownership. Projects all sources once with global visibility, then clips one contiguous overlapping grid; never reprojects or reclassifies per page. Grid bounds include geometry and every equipment POS, with stable E001 IDs and per-page WORLD XYZ/DX/DY/DEL schedules. Devices whose geometry crosses a page but whose POS lies outside are explicitly marked without a false origin leader. At most 16 related devices per page; dense pages fail. Preserves source/edge/visibility and exact input-line interval coverage, page coordinates and neighbors. Selected parameter geometry only, not whole-equipment geometry, certified footprint, native cut scope, union or native Draft. Limits: 24 pages, 10k vertices/20k triangles, 20k global lines/40k fragments, 8M total preview pixels and 8 MiB each structured/PNG output. Any failure suppresses all page images. Read-only; no model, CE, Drawlist, camera or restart changes.",
        {"type": "object", "properties": {
            "equipment": {"type": "array", "minItems": 1, "maxItems": 64, "items": {
                "type": "object", "properties": {
                    "refno": {"type": "string", "minLength": 2, "maxLength": 1024},
                    "sources": {"type": "array", "minItems": 1, "maxItems": 128, "items": selected_material_source_schema},
                }, "required": ["refno", "sources"], "additionalProperties": False}},
            "datum": location_datum_schema,
            "sheet": {"type": "object", "properties": {
                "size": {"type": "string", "enum": ["A3", "A4"], "default": "A3"},
                "title": {"type": "string", "maxLength": 48, "default": "EQUIPMENT LAYOUT TILES"},
                "drawing_number": {"type": "string", "maxLength": 32, "default": "E3D-EQUIPMENT-TILES"},
                "scale_denominator": {"type": "number", "exclusiveMinimum": 0},
            }, "required": ["scale_denominator"], "additionalProperties": False},
            "tiling": {"type": "object", "properties": {
                "overlap_mm": {"type": "number", "minimum": 0, "default": 10},
                "max_pages": {"type": "integer", "minimum": 1, "maximum": 24, "default": 24},
            }, "additionalProperties": False},
            **selected_geometry_options, **location_output_schema,
        }, "required": ["equipment", "sheet"], "additionalProperties": False},
        lambda args: _equipment_layout_tiles(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_nozzle_outline_sheet",
        "Generate equipment-local radial U/V and axial U/H nozzle orientation sheets with selected parameter equipment outlines. Requires one explicit EQUI, 1 to 32 native nozzle targets, distinct positive local axial_axis/zero_axis and 1 to 128 typed material sources. Verifies source membership under the equipment, complete native WORLD poses/points and agreeing session/project/MDB boundaries. WORLD mesh coordinates/hash are preserved; projections use u=zero axis, n=axial axis, v=n cross u, radial (u,v,n) and axial (u,n,-v), with joint depth visibility across all selected sources. Uses one common scale/bounds including outlines, points and native POS; distinguishes point position angle from PDIR azimuth/tilt. Axial PDIR azimuth and zero-radius position angles are NA. Returns A3/A4 sheets with one PNG per page and optional paper-mm SVG/DXF. Selected geometry does not certify complete equipment, native tessellation/cut scope, physical centerline or nozzle ownership. 128 points, 8/16 per page, 10k vertices/20k triangles, 8M preview pixels, 8 MiB structured and PNG totals; any failure suppresses every image. Read-only, no native Draft, model, CE, Drawlist, camera or restart writes.",
        {"type": "object", "properties": {
            "equipment_refno": {"type": "string", "minLength": 2, "maxLength": 1024},
            "targets": {"type": "array", "minItems": 1, "maxItems": 32, "items": {
                "type": "object", "properties": {
                    "refno": {"type": "string", "minLength": 2, "maxLength": 1024},
                    "point_numbers": {"type": "array", "maxItems": 64, "uniqueItems": True,
                        "items": {"type": "integer", "minimum": 1, "maximum": 10000}},
                }, "required": ["refno"], "additionalProperties": False}},
            "sources": {"type": "array", "minItems": 1, "maxItems": 128, "items": selected_material_source_schema},
            "axial_axis": {"type": "string", "enum": ["X", "Y", "Z"]},
            "zero_axis": {"type": "string", "enum": ["X", "Y", "Z"]},
            "nozzle_geometry": {"type": "string", "enum": ["points", "catalogue"], "default": "points",
                "description": "catalogue reads and verifies a supported coaxial SCYL flange/neck GMSE and PTAX pair, drawing actual catalogue outside diameters and a single label per nozzle. Unsupported catalogues fail explicitly. Base material only; insulation and bore/wall geometry are not inferred. points retains the diagnostic P-point presentation."},
            "sheet": {"type": "object", "properties": {
                "size": {"type": "string", "enum": ["A3", "A4"], "default": "A3"},
                "title": {"type": "string", "maxLength": 48, "default": "NOZZLE ORIENTATION / OUTLINE"},
                "drawing_number": {"type": "string", "maxLength": 32, "default": "E3D-NOZZLE-OUTLINE"},
                "points_per_page": {"type": "integer", "enum": [8, 16], "default": 8},
                "scale_denominator": {"type": "number", "exclusiveMinimum": 0,
                    "description": "Optional fixed model-to-paper scale 1:N for catalogue geometry. Omit for a fitting engineering scale; explicit scales fail if they do not fit."},
            }, "additionalProperties": False},
            **selected_geometry_options, **location_output_schema,
        }, "required": ["equipment_refno", "targets", "sources", "axial_axis", "zero_axis"], "additionalProperties": False},
        lambda args: _nozzle_outline_sheet(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_connection_points",
        "Read actual E3D connection points for one explicit element in WORLD, OWNER or an explicit reference frame. "
        "An omitted/empty point_numbers discovers and reads up to 64 native PPLS point numbers; it never assumes contiguous numbering. "
        "Explicit point numbers require native existence evidence before indexed reads. Returns per-field availability for PPOS position, "
        "PDIR direction, PPBO bore in mm, PPCO connection type and raw PCREF encoding, plus catalogue/connection references. "
        "Inspect enumeration, complete and errors; unknown PPLS membership stays null and negative point numbers decode by absolute value. "
        "PCREF type 710633 preserves its anchor and resolves the fixed 'ILEAVE TUBE OF =database/element' PML1 reference expression through the Database SDK; only a TUBI result is marked resolved. "
        "A zero native bore is not DN0. Element POS, HEIG and ORI are not substituted for connection geometry. "
        "Read completeness does not establish suitability for connection, flange rating or catalogue compatibility. "
        "Does not dispatch PML commands, edit geometry, select CE, add display members or change the view.",
        {"type": "object", "properties": {
            "refno": {"type": "string", "description": "Required explicit /NAME or =database/element reference."},
            "wrt": {"type": "string", "default": "WORLD", "description": "WORLD, OWNER, /NAME or =database/element."},
            "point_numbers": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 10000},
                              "maxItems": 64, "uniqueItems": True, "default": []},
            "expected_type": {"type": "string", "maxLength": 64, "description": "Optional required Design type ShortName. Only implicit TUBI has cross-type resolution from a shared BRAN/ELBO anchor; other mismatches fail."},
        }, "required": ["refno"], "additionalProperties": False},
        lambda args: _connection_points(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_check_connection_pair",
        "Read and compare two explicitly selected E3D P-points in one native E3D21 UI-thread dispatch. Requires both explicit element references, both point numbers and a caller-supplied position tolerance in mm. Optional first_expected_type and second_expected_type constrain each resolved endpoint to a Design type ShortName; only implicit TUBI has cross-type resolution from a shared BRAN/ELBO anchor. Reports each point's existence and read completeness, position delta against that tolerance, native PDIR direction dot product and angle, and each PCREF's resolution and exact correspondence to the opposite selected endpoint. Direction labels use a descriptive plus/minus 0.999 dot threshold, not an engineering rule. Preserves native PPBO bore, PPCO connection type, raw PCREF data and SPRE/CATR/CREF catalogue/connection reference evidence. Unknown or partial reads remain unknown; native evidence is not a dimensional, pressure-class, catalogue or engineering compatibility verdict. Does not dispatch PML commands, edit geometry, select CE, add display members or change the view.",
        {"type": "object", "properties": {
            "first_refno": {"type": "string", "description": "Required explicit /NAME or =database/element reference for the first element."},
            "first_expected_type": {"type": "string", "maxLength": 64, "description": "Optional required Design type ShortName for the first element. Only implicit TUBI has cross-type resolution from a shared BRAN/ELBO anchor; other mismatches fail."},
            "first_point_number": {"type": "integer", "minimum": 1, "maximum": 10000},
            "second_refno": {"type": "string", "description": "Required explicit /NAME or =database/element reference for the second element."},
            "second_expected_type": {"type": "string", "maxLength": 64, "description": "Optional required Design type ShortName for the second element. Only implicit TUBI has cross-type resolution from a shared BRAN/ELBO anchor; other mismatches fail."},
            "second_point_number": {"type": "integer", "minimum": 1, "maximum": 10000},
            "tolerance_mm": {"type": "number", "minimum": 0, "maximum": 1000000,
                              "description": "Required finite position tolerance in millimetres; no default is applied."},
        }, "required": ["first_refno", "first_point_number", "second_refno", "second_point_number", "tolerance_mm"],
            "additionalProperties": False},
        lambda args: _check_connection_pair(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_trace_piping_connectivity",
        "Trace directed native PCREF links from 1 to 16 explicit P-point starts in one E3D21 UI dispatch. Reads P-point existence, PCREF and element identity only; it does not read geometry or catalogue fields. Follows fully resolved element, type and point references within max_hops and max_nodes. An optional expected_type uses the SDK type ShortName and requires the start type to match; the only cross-type exception is implicit TUBI sharing a numeric DBREF with its BRAN/ELBO anchor. Reports endpoint and reference evidence, open ends, unresolved references, reciprocal/one-way/unknown direction, observed cycles, truncation and context changes separately. Unknown PCREF reads are never treated as open ends or disconnected. This is a database-reference trace, not a geometric proximity or engineering compatibility verdict; one UI callback is not a database lock.",
        {"type": "object", "properties": {
            "starts": {"type": "array", "items": {"type": "object", "properties": {
                "refno": {"type": "string", "description": "Explicit /NAME or =database/element reference."},
                "point_number": {"type": "integer", "minimum": 1, "maximum": 10000},
                "expected_type": {"type": "string", "maxLength": 64, "description": "Optional required Design type ShortName. Only implicit TUBI has cross-type resolution from a shared BRAN/ELBO anchor; other mismatches fail."},
            }, "required": ["refno", "point_number"], "additionalProperties": False}, "minItems": 1, "maxItems": 16},
            "max_hops": {"type": "integer", "minimum": 0, "maximum": 16, "default": 8},
            "max_nodes": {"type": "integer", "minimum": 1, "maximum": 128, "default": 64,
                          "description": "Maximum number of unique endpoints to inspect; must be at least the number of starts."},
        }, "required": ["starts"], "additionalProperties": False},
        lambda args: _trace_piping_connectivity(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_spatial_info",
        "Read numeric POS and ORI through the E3D SDK with an explicit reference frame. Returns millimetres and local axis vectors in WORLD, OWNER, or a named/DBREF frame. Check position_complete and orientation_complete independently; complete requires both. A PAVE can have success=true, position_complete=true, orientation_complete=false and complete=false because ORI is unsupported. For position checks, verify position_complete, refno, frame and errors; do not require ORI. Unsupported attributes are not read errors; SDK failures retain other available fields and set success=false. Does not change the current element or view.",
        {
            "type": "object",
            "properties": {
                "refno": {"type": "string", "default": "", "description": "Element name /NAME or numeric =database/element; empty selects the current element."},
                "wrt": {"type": "string", "default": "WORLD", "description": "Reference frame: WORLD, OWNER, /NAME, or =database/element. Coordinates are relative to this frame, not implicitly the parent."},
            },
            "additionalProperties": False,
        },
        lambda args: _spatial_info(bridge, args),
        read_only=True,
    )
    server.add_tool(
        "e3d_transform_coordinates",
        "Convert up to 128 points and 128 direction vectors between explicit E3D reference frames in one native UI dispatch. Frames are WORLD, /NAME or =database/element; coordinates and point origins use millimetres. The SDK reads frame POS/ORI relative to WORLD; conversion fails closed for missing orientation, non-finite or non-orthonormal axes, unavailable frames, or changed CE/project/MDB context. Points include translation; direction vectors do not. Inspect success, complete, read_errors and context identity. A single UI dispatch is not a database lock. Mock mode and non-E3D21 versions report unavailable without fabricated conversions.",
        {"type": "object", "properties": {
            "from_wrt": {"type": "string", "description": "Source frame: WORLD, /NAME or =database/element."},
            "to_wrt": {"type": "string", "description": "Destination frame: WORLD, /NAME or =database/element."},
            "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}, "maxItems": 128, "default": [], "description": "Point coordinates in mm; each is [x,y,z]."},
            "vectors": {"type": "array", "items": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}, "maxItems": 128, "default": [], "description": "Direction vectors, each [x,y,z]; no translation is applied."},
        }, "required": ["from_wrt", "to_wrt"], "additionalProperties": False},
        lambda args: _transform_coordinates(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_graphical_selection",
        "Read the native graphical interaction selection with bounded element details. Its scope is the native interaction selection; membership is not guaranteed to belong to the current view and does not establish tree selection, every highlighted element or the last picked item. Inspect availability, completeness, truncation and read errors; unknown or failed reads do not establish an empty selection. Does not select CE, modify the selection or change the view.",
        {"type": "object", "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
        }, "additionalProperties": False},
        lambda args: _graphical_selection(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_set_graphical_selection",
        "Preview or replace the native graphical interaction selection with 0 to 32 explicit numeric references; an empty refnos list clears it. "
        "Targets must already exist as exact Drawlist members or primitive entries. Never Add graphics, promote owners, expand children, change CE or fit the camera. "
        "Preview defaults to true and requires a complete current selection of at most 200 entries and complete display maps. Execute with its 64-hex revision as expected_revision and command permission. "
        "The revision binds session/context, current CE, complete ordered original selection, requested references and display state; it is not a lock. "
        "A same-set no-op preserves native order and skips all setters, notification and update. Actual writes use addGraphics=false and are checked in a separate UI callback. "
        "Inspect readback_verified, verification_state, ce_unchanged, context_unchanged and display_unchanged; set equality and order agreement are separate observations. "
        "Void native returns or update completion alone do not prove success. Failures may leave partial selection; no automatic rollback, replay, cancellation or image capture. Mock cannot simulate a native result.",
        {"type": "object", "properties": {
            "refnos": {"type": "array", "minItems": 0, "maxItems": 32, "uniqueItems": True,
                       "items": {"type": "string", "pattern": r"^\s*=[0-9]+/[0-9]+\s*$"},
                       "description": "Explicit numeric =database/element identities; [] means clear. Normalized duplicate identities are rejected."},
            "dry_run": {"type": "boolean", "default": True},
            "expected_revision": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$", "minLength": 64, "maxLength": 64},
        }, "required": ["refnos"], "additionalProperties": False,
            "allOf": [{"if": {"properties": {"dry_run": {"const": False}}, "required": ["dry_run"]},
                       "then": {"required": ["expected_revision"]}}]},
        lambda args: _set_graphical_selection(bridge, settings, args), destructive=False,
    )
    server.add_tool(
        "e3d_delete_element",
        "Preview or delete an E3D element through the database API without invoking the interactive DELETE CE command. Execution is successful only when the native receipt confirms the exact numeric DBREF is deleted and the complete original owner's ordered membership matches that same member removed. Inspect native_delete_returned, verification_status, deletion_verified, readback_deleted_state, readback_absent_from_owner and readback_owner_membership_matches_expected. Incomplete or uncertain readback is an MCP error with retry_safe=false; do not replay it. Older add-in responses lacking this evidence fail closed and skip image capture.",
        {
            "type": "object",
            "properties": {"refno": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}},
            "required": ["refno"],
            "additionalProperties": False,
        },
        _with_capture_receipt(bridge, "e3d_delete_element", _delete_element, settings),
        capture_receipt=True,
    )
    server.add_tool(
        "e3d_set_attribute",
        "Preview or update a restricted E3D attribute directly through the SDK. Both paths check attribute support, current settable state and input type; numeric input must be finite, with dot decimal notation. Preview does not prove all value/database constraints. Use e3d_element_schema for permitted attributes and inspect returned write_attempted/write_applied after failures.",
        {
            "type": "object",
            "properties": {"refno": {"type": "string"}, "attribute": {"type": "string"}, "value": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}},
            "required": ["refno", "attribute", "value"],
            "additionalProperties": False,
        },
        _with_capture_receipt(bridge, "e3d_set_attribute", _set_attribute, settings),
        capture_receipt=True,
    )
    server.add_tool(
        "e3d_set_attributes",
        "Preview or set 1 to 20 restricted attributes on one explicit E3D element in a single UI dispatch. Keys use the same direct-edit whitelist as e3d_set_attribute; all values are strings and numeric values use finite invariant notation. Preview validates the full request before writing and returns a revision bound to the target, requested values, original values and session/project/MDB. Execute the same request with expected_revision from that preview. Fields are applied sequentially and read back individually; this is not an atomic transaction, and a failure can leave earlier fields changed. Inspect per-field status, write/readback and residual observations, then read the element before deciding what to do. No automatic rollback or replay.",
        {"type": "object", "properties": {
            "refno": {"type": "string", "description": "Explicit /NAME or =database/element reference."},
            "values": {"type": "object", "minProperties": 1, "maxProperties": 20,
                       "propertyNames": {"pattern": r"^\s*(?:[Nn][Aa][Mm][Ee]|[Dd][Ee][Ss][Cc]|[Dd][Uu][Tt][Yy]|[Dd][Ii][Aa][Mm]|[Hh][Ee][Ii][Gg]|[Xx][Ll][Ee][Nn]|[Yy][Ll][Ee][Nn]|[Zz][Ll][Ee][Nn]|[Dd][Tt][Oo][Pp]|[Dd][Bb][Oo][Tt])\s*$",
                                         "not": {"pattern": r"[\u0000-\u001f\u007f-\u009f]"}},
                       "additionalProperties": {"type": "string"},
                       "description": "One to 20 unique attributes from NAME, DESC, DUTY, DIAM, HEIG, XLEN, YLEN, ZLEN, DTOP and DBOT. Numeric values are strings in finite invariant notation. Names are trimmed and uppercased; case/space variants of the same attribute are duplicates."},
            "expected_revision": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$", "minLength": 64, "maxLength": 64,
                                  "description": "Revision returned by previewing the same target and values. Required for execution."},
            "dry_run": {"type": "boolean", "default": True},
        }, "required": ["refno", "values"], "additionalProperties": False,
            "allOf": [{"if": {"properties": {"dry_run": {"const": False}}, "required": ["dry_run"]},
                       "then": {"required": ["expected_revision"]}}]},
        _with_capture_receipt(bridge, "e3d_set_attributes", _set_attributes, settings),
        capture_receipt=True,
    )
    server.add_tool(
        "e3d_execute_pml",
        "Preview or execute a PML command. dry_run=true checks input without proving valid PML or executing it. Adapters advertising pml_nested_scope run ordinary requests in independent nested scopes; inspect execution_scope and errors. Use e3d_execute_pml_script for complete scripts with IF/DO blocks. Local ! variables are not shared across requests. Real execution requires dry_run=false and E3D_MCP_ALLOW_COMMANDS=true.",
        {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "PML command to validate or execute"},
                "dry_run": {"type": "boolean", "default": True},
                "expected_session_id": {"type": "string", "description": "Optional session_id from e3d_status. A supporting adapter rejects the command if the E3D session has changed (api_features includes command_session_guard)."},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        _with_capture_receipt(bridge, "e3d_execute_pml", _execute_pml, settings),
        open_world=True,
        capture_receipt=True,
    )
    server.add_tool(
        "e3d_execute_pml_script",
        "Preview or execute a complete PML script, including IF/DO blocks, via an adapter-owned temporary macro in an independent nested scope. "
        "source must be non-empty valid Unicode, contain no NUL and occupy at most 262144 strict UTF-8 bytes; source formatting is preserved. "
        "dry_run=true validates input and the running E3D code-page encoding only: it creates no file, performs no execution and does not check syntax. "
        "Real execution requires dry_run=false and E3D_MCP_ALLOW_COMMANDS=true. Inspect execution_attempted/completed, native_run_returned, outcome_uncertain, "
        "error_metadata and cleanup; failures can leave partially executed effects, with no rollback or automatic retry. "
        "The receipt has no submitted source/command field; native output and errors may contain printed values or source excerpts and are not redacted. "
        "Execution assigns a UUID request_id when omitted and reads /health for a session guard when expected_session_id is omitted; no script is submitted without a session. "
        "A transport timeout or disconnection retains outcome_uncertain=true, retry_safe=false and pml_script_request identifiers; it does not automatically inspect command state, cancel, read messages or resend the script. "
        "Use e3d_pml_script_status to look up an uncertain request. A request_id can be accepted only once per session; never replay an uncertain script with a new ID. "
        "byte_count/source_sha256 describe original UTF-8 source; encoding.byte_count describes the actual macro bytes and reports any terminal newline added. "
        "Local ! variables stay within this script; !! globals are session state. capture_after returns an image only after confirmed success.",
        {"type": "object", "properties": {
            "source": {"type": "string", "minLength": 1, "maxLength": PML_SCRIPT_MAX_BYTES,
                       "pattern": r"\S", "not": {"pattern": r"\x00"},
                       "x-maxUtf8Bytes": PML_SCRIPT_MAX_BYTES,
                       "description": "Complete source, preserved verbatim; non-whitespace, no NUL or invalid Unicode, at most 262144 UTF-8 bytes. The running E3D code page may reject characters that UTF-8 accepts."},
            "dry_run": {"type": "boolean", "default": True},
            "request_id": {"type": "string", "format": "uuid",
                           "description": "Optional canonical UUID (any version), normalized to lowercase. Generated for real execution if omitted. Preview only validates it and creates no receipt."},
            "expected_session_id": {"type": "string", "minLength": 1, "pattern": r"\S",
                                    "description": "Optional session_id from e3d_status; a mismatch rejects the script before execution. Real execution reads /health first when omitted."},
        }, "required": ["source"], "additionalProperties": False},
        _with_capture_receipt(bridge, "e3d_execute_pml_script", _execute_pml_script, settings),
        open_world=True,
        capture_receipt=True,
    )
    server.add_tool(
        "e3d_pml_script_status",
        "Read a complete PML script request's in-memory receipt without executing, cancelling or capturing anything. "
        "Requires the original request_id and expected_session_id; a different session rejects the lookup. "
        "Inspect available, status (pending/completed/unknown_or_expired), receipt_available and the receipt's original envelope/http_status or size-limit summary. "
        "A completed lookup can contain a failed script receipt; inspect that original result separately. pending does not prove the SDK began executing; "
        "unknown_or_expired does not prove the script never executed, even when no receipt remains. "
        "At most 64 receipts are retained, completed receipts expire after 30 minutes, and all records disappear when the E3D session ends. "
        "Accepted IDs remain reserved for the session even after receipt expiry/eviction; request_id_reserved reports this when available. "
        "Duplicate IDs are rejected without re-execution. Up to 65536 accepted IDs are reserved; a full store rejects new execution. Never automatically replay an uncertain script.",
        {"type": "object", "properties": {
            "request_id": {"type": "string", "format": "uuid",
                           "description": "Original script request UUID in 8-4-4-4-12 hexadecimal format; any version, normalized to lowercase."},
            "expected_session_id": {"type": "string", "minLength": 1, "pattern": r"\S",
                                    "description": "Original E3D session_id; never substitute a newer session after a restart."},
        }, "required": ["request_id", "expected_session_id"], "additionalProperties": False},
        lambda args: _pml_script_status(bridge, args),
        read_only=True,
    )
    server.add_tool(
        "e3d_reenter_design",
        "Debug restart: require successful SAVEWORK, send DESIGN once, then confirm a new session with the original project and MDB. Reuses the E3D login. On a readiness timeout inspect status/project; do not automatically resend DESIGN. Does not restore the view.",
        {"type": "object", "properties": {
            "wait_seconds": {"type": "integer", "minimum": 5, "maximum": 120, "default": 90,
                             "description": "Readiness polling budget after the single DESIGN request; individual HTTP requests may add their timeout."}
        }, "additionalProperties": False},
        lambda args: _reenter_design(bridge, settings, args),
    )
    server.add_tool(
        "e3d_execute_pml_batch",
        "Execute a sequence of PML commands through the E3D UI thread. Each command is reported separately and the batch stops on the first error by default.",
        {
            "type": "object",
            "properties": {
                "commands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 200,
                },
                "dry_run": {"type": "boolean", "default": True},
                "stop_on_error": {"type": "boolean", "default": True},
            },
            "required": ["commands"],
            "additionalProperties": False,
        },
        _with_capture_receipt(bridge, "e3d_execute_pml_batch", _execute_pml_batch, settings),
        open_world=True,
        capture_receipt=True,
    )
    vector_schema = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
    transform_properties = {
        "position": {**vector_schema, "description": "Position in millimetres in the wrt frame."},
        "orientation": {"type": "object", "properties": {"x_axis": vector_schema, "y_axis": vector_schema},
                        "required": ["x_axis", "y_axis"], "additionalProperties": False,
                        "description": "Local X and Y axes as unit, orthogonal vectors in the wrt frame; Z is their cross product."},
        "wrt": {"type": "string", "default": "OWNER", "description": "OWNER, WORLD, /NAME or =database/element."},
        "dry_run": {"type": "boolean", "default": True},
    }
    server.add_tool(
        "e3d_create_element",
        "Create a child through the E3D SDK under an explicit parent, with optional dimensions/text and transform. Preview validates type/owner compatibility and inputs without creating anything; it returns a revision when the connected E3D21 bridge supports guarded execution. Pass that revision as expected_revision when executing so a changed parent, frame or request is rejected before creation. Older preview calls remain compatible. Preview does not prove all database constraints. A failed initialization attempts to remove only the new element; inspect cleanup and residual state before retrying.",
        {"type": "object", "properties": {
            "parent_refno": {"type": "string"}, "element_type": {"type": "string"},
            "name": {"type": "string", "default": ""},
            "attributes": {"type": "object", "additionalProperties": {"type": "string"}, "maxProperties": 20, "default": {}},
            "expected_revision": {"type": "string", "pattern": r"^[0-9a-fA-F]{64}$", "minLength": 64, "maxLength": 64,
                                  "description": "Revision returned by a create preview. The E3D21 bridge requires it for guarded execution; omitted previews remain valid."},
            **transform_properties,
        }, "required": ["parent_refno", "element_type"],
         "allOf": [{"if": {"properties": {"dry_run": {"const": False}}, "required": ["dry_run"]},
                    "then": {"required": ["expected_revision"]}}],
         "additionalProperties": False},
        _with_capture_receipt(bridge, "e3d_create_element", _create_element, settings),
        capture_receipt=True,
    )
    server.add_tool(
        "e3d_copy_element",
        "Copy supported native geometry through one guarded SDK operation under an explicit destination parent and new /NAME. Preview first and execute with its revision as expected_revision; inspect the source-to-copy mapping and native readback. Supports CYLI, BOX and CONE with leaf NCYL, NBOX and NCON cuts, positive standard DISH without children, plus bounded SUBE trees containing these and simple PANE/PLOO/PAVE geometry, up to 2048 nodes and depth 32. DISH copying preserves DIAM, HEIG and stored RADI, and its origin remains the base circle centre. DISH with any children and NDIS are unsupported. Whole EQUI equipment, rules, links and UDET types are unsupported. Named descendants receive the new root name plus -C and their depth-first sequence number; unnamed descendants remain unnamed. Omitted position and orientation independently preserve the source WORLD components; supplied components use wrt, whose OWNER is the destination parent. Native inspection decides eligibility and name collisions. A failed copy attempts cleanup of only its new subtree; inspect cleanup and actual residual state before retrying. This is not an atomic database transaction. Does not select CE, add to the Drawlist, or fit the camera. Never automatically replay a copy to obtain an image.",
        {"type": "object", "properties": {
            "source_refno": {"type": "string", "description": "Explicit source /NAME or =database/element reference."},
            "parent_refno": {"type": "string", "description": "Explicit destination parent /NAME or =database/element reference."},
            "name": {"type": "string", "minLength": 2, "maxLength": 50, "pattern": r"^/[^\s\x00-\x1f\x7f-\x9f]+$",
                     "description": "Required new root /NAME, at most 50 characters including slash, without whitespace or control characters. Native preflight requires at most 44 when named descendants need the six-character -C0001 suffix; anonymous descendants need no suffix. Names are never truncated."},
            **transform_properties,
            "position": {**transform_properties["position"],
                         "description": "Position in millimetres in wrt; omit to preserve the source WORLD position."},
            "orientation": {**transform_properties["orientation"],
                            "description": "Unit orthogonal X/Y axes in wrt; omit to preserve the source WORLD orientation."},
            "wrt": {**transform_properties["wrt"],
                    "description": "OWNER means the destination parent; otherwise WORLD, /NAME or =database/element."},
            "expected_revision": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$", "minLength": 64, "maxLength": 64,
                                  "description": "Revision from copy preview. Required for execution; may be omitted for preview."},
        }, "required": ["source_refno", "parent_refno", "name"], "additionalProperties": False},
        _with_capture_receipt(bridge, "e3d_copy_element", _copy_element, settings),
        capture_receipt=True,
    )
    server.add_tool(
        "e3d_create_panel",
        "Create a native PANE with one PLOO and 3 to 256 straight PAVE vertices through one SDK operation under an explicit parent. Vertices are local XY millimetres, simple and counterclockwise, without a repeated closing vertex or holes; thickness is positive millimetres. Position and orientation default to the origin and identity in the wrt frame. Preview performs native validation without writing. A failed creation attempts to remove only its new subtree; inspect cleanup and residual state before retrying. This is not an atomic database transaction. The adapter does not explicitly add to the Drawlist or fit a view; E3D creation notifications may display new geometry.",
        {"type": "object", "properties": {
            "parent_refno": {"type": "string", "description": "Explicit /NAME or =database/element parent reference."},
            "vertices": {"type": "array", "items": {"type": "array", "items": {"type": "number"},
                         "minItems": 2, "maxItems": 2}, "minItems": 3, "maxItems": 256,
                         "description": "Local [x,y] vertices in millimetres, counterclockwise with no closing repeat."},
            "thickness": {"type": "number", "exclusiveMinimum": 0, "description": "Panel thickness in millimetres."},
            "justification": {"type": "string", "enum": ["positive", "centre", "negative"],
                              "x-normalize": "trim_casefold", "default": "positive",
                              "description": "Thickness extends along local +Z, symmetrically about XY, or along local -Z."},
            "name": {"type": "string", "default": ""},
            **transform_properties,
            "position": {**transform_properties["position"], "default": [0, 0, 0]},
            "orientation": {**transform_properties["orientation"],
                            "default": {"x_axis": [1, 0, 0], "y_axis": [0, 1, 0]}},
        }, "required": ["parent_refno", "vertices", "thickness"], "additionalProperties": False},
        _with_capture_receipt(bridge, "e3d_create_panel", _create_panel, settings),
        capture_receipt=True,
    )
    server.add_tool(
        "e3d_panel_geometry",
        "Read an explicitly referenced PANE's native PLOO/PAVE geometry, local XYZ vertices, thickness, justification, polygon findings and revision token for guarded updates. Does not use or select the current element. Native inspection is bounded at 32 loops and 2048 vertices; inspect complete, partial/read errors and validation availability before interpreting the result. Optional include_world reads each PAVE's native control-point POS in the current MDB Design WORLD and reports independent world_positions_complete/world_read_errors. Optional include_boundary reads raw native P0/P4/P6/P8 geometry points for nonzero-FRAD PAVEs, with P0 direction and explicit PANE-local coordinates; include_world also requests independent WORLD boundary-point reads. Inspect boundary_points_complete/errors separately. These optional reads do not change local completeness/topology and do not assemble arcs, resolve thickness or return a rendered outline.",
        {"type": "object", "properties": {"refno": {"type": "string",
            "description": "Explicit PANE /NAME or =database/element reference; current element is never implicit."},
            "include_world": {"type": "boolean", "default": False,
                "description": "Also read each PAVE native control-point position in the current MDB Design WORLD, with separate completeness and errors."},
            "include_boundary": {"type": "boolean", "default": False,
                "description": "Also inspect raw native P0/P4/P6/P8 geometry points for nonzero-FRAD PAVEs; optional WORLD evidence follows include_world. No curve or thickness inference."}},
         "required": ["refno"], "additionalProperties": False},
        lambda args: _panel_geometry(bridge, args),
        read_only=True,
    )
    server.add_tool(
        "e3d_panel_mesh",
        "Read one explicitly referenced PANE and derive a closed prism mesh from one complete, valid, straight loop and its native HEIG/SJUS parameters. Supports documented BOTT, CENT, TOP and BOTTOM justification tokens only. coordinate_space defaults to panel_local; WORLD uses one native read of all PAVE WORLD positions and accepts only a validated right-handed orthonormal panel frame with consistent rigid-transform residuals. Multiple loops, blind loops, fillets, incomplete geometry or invalid WORLD transforms are rejected. This is parameter-derived geometry, not E3D tessellation, CSG or rendered geometry; no model, current element, Drawlist or camera changes are made.",
        {"type": "object", "properties": {
            "refno": {"type": "string",
                "description": "Explicit PANE /NAME or =database/element reference; current element is never implicit."},
            "coordinate_space": {"type": "string", "enum": ["panel_local", "WORLD"], "default": "panel_local",
                "description": "Output coordinate frame; WORLD requires complete native PAVE WORLD positions."}},
         "required": ["refno"], "additionalProperties": False},
        lambda args: _panel_mesh(bridge, args),
        read_only=True,
    )
    server.add_tool(
        "e3d_project_panel_boundary_drawing",
        "Read one explicitly referenced native PANE and project the native PAVE control-point loops directly into FRONT/TOP/SIDE views. This route does not derive a prism mesh, does not require HEIG/SJUS, and does not infer outer/inner loops, holes, thickness, visibility or Boolean cuts. WORLD requires complete native PAVE WORLD positions; incomplete loops or point reads fail closed. Returns a PNG preview and optional SVG/DXF control-polygon linework with explicit limitations. It never changes the model, current element, Drawlist, camera or view.",
        {"type": "object", "properties": {
            "refno": {"type": "string", "description": "Explicit PANE /NAME or =database/element reference."},
            "coordinate_space": {"type": "string", "enum": ["WORLD", "panel_local"], "default": "WORLD",
                                  "description": "Use native PAVE WORLD control points, or PANE-local PAVE positions."},
            "views": {"type": "array", "minItems": 1, "maxItems": 3, "uniqueItems": True,
                      "items": {"type": "string", "enum": ["FRONT", "TOP", "SIDE"]},
                      "default": ["FRONT", "TOP", "SIDE"]},
            "include_svg": {"type": "boolean", "default": False},
            "include_dxf": {"type": "boolean", "default": False},
            "preview_width": {"type": "integer", "minimum": 64, "maximum": 2048, "default": 1200},
            "preview_height": {"type": "integer", "minimum": 64, "maximum": 2048, "default": 440},
        }, "required": ["refno"], "additionalProperties": False},
        lambda args: _project_panel_boundary_drawing(bridge, args),
        read_only=True,
    )
    server.add_tool(
        "e3d_panel_curve_boundary",
        "Read an explicit PANE, check native DESP import descriptors, and derive validated straight/positive-fillet curves in its definition plane from native P0/P4/P6/P8. Returns line/arc segments and bounded chord samples, preserving independent PPEXST unknowns and native completeness. Supports eligible ordinary convex/concave fillets; unsupported tags, curve types, ambiguous arcs, inconsistent frames, crossing boundaries or changed observations return no boundary. WORLD requires independently observed native WORLD points and a validated rigid frame. Separate reads are compared and bracketed by session/project observations, not an atomic snapshot. No model, CE, Drawlist, camera or view changes; no rendered outline, thickness surface, holes, cuts or visibility is inferred.",
        {"type": "object", "properties": {
            "refno": {"type": "string", "description": "Explicit PANE /NAME or numeric DBREF; never implicit CE."},
            "coordinate_space": {"type": "string", "enum": ["panel_local", "WORLD"], "default": "panel_local"},
            "chord_tolerance_mm": {"type": "number", "minimum": 0.000001, "maximum": 1000, "default": 0.1,
                                   "description": "Maximum circular-arc chord sagitta in millimetres; sampling is bounded."},
        }, "required": ["refno"], "additionalProperties": False},
        lambda args: _panel_curve_boundary(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_project_panel_curve_drawing",
        "Read one explicit PANE and generate FRONT/TOP/SIDE projections of its validated native definition-plane line/arc boundary, with a PNG and optional SVG/DXF. Curve linework is sampled to chord_tolerance_mm; DXF contains sampled LINE entities. This resolves eligible positive fillets instead of drawing their pole control polygon. Import tags, unsupported geometry, failed or changed native reads produce no image. Preserves unknown PPEXST existence separately from available point coordinates. Does not compute thickness surfaces, final rendered/Boolean outlines, holes, hidden lines or occlusion, and never changes the E3D model, CE, Drawlist or camera.",
        {"type": "object", "properties": {
            "refno": {"type": "string"},
            "coordinate_space": {"type": "string", "enum": ["panel_local", "WORLD"], "default": "WORLD"},
            "chord_tolerance_mm": {"type": "number", "minimum": 0.000001, "maximum": 1000, "default": 0.1},
            "views": {"type": "array", "minItems": 1, "maxItems": 3, "uniqueItems": True,
                      "items": {"type": "string", "enum": ["FRONT", "TOP", "SIDE"]},
                      "default": ["FRONT", "TOP", "SIDE"]},
            "include_svg": {"type": "boolean", "default": False},
            "include_dxf": {"type": "boolean", "default": False},
            "preview_width": {"type": "integer", "minimum": 64, "maximum": 2048, "default": 1200},
            "preview_height": {"type": "integer", "minimum": 64, "maximum": 2048, "default": 440},
        }, "required": ["refno"], "additionalProperties": False},
        lambda args: _project_panel_curve_drawing(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_project_panel_curve_assembly_drawing",
        "Read 1 to 128 explicit PANE references and compose paged WORLD FRONT/TOP/SIDE drawings of their validated definition-plane line/positive-fillet curves. All pages use identical global view bounds so spatial alignment and display scale remain comparable across pages. Each line retains its global source index and PANE reference; curves are sampled to chord_tolerance_mm, with optional SVG and 1:1 millimetre DXF LINE exports. Every source uses native boundary points and checked DESP descriptors, with whole-operation session/project observations in addition to per-source consistency checks. Unsupported or incomplete geometry, duplicate resolved identities, frame changes or resource limits return no image. Separate reads are not an atomic snapshot. This is independent definition-curve composition: no cross-panel Boolean union, thickness surfaces, hole roles, hidden lines or final rendered equipment outline. Does not change the model, CE, Drawlist, camera or view.",
        {"type": "object", "properties": {
            "targets": {"type": "array", "minItems": 1, "maxItems": 128, "uniqueItems": True,
                        "items": {"type": "string"}, "description": "Explicit PANE /NAME or numeric DBREF values, in source order."},
            "panels_per_page": {"type": "integer", "enum": [16, 32, 64, 128], "default": 16},
            "chord_tolerance_mm": {"type": "number", "minimum": 0.000001, "maximum": 1000, "default": 0.1},
            "views": {"type": "array", "minItems": 1, "maxItems": 3, "uniqueItems": True,
                      "items": {"type": "string", "enum": ["FRONT", "TOP", "SIDE"]},
                      "default": ["FRONT", "TOP", "SIDE"]},
            "include_svg": {"type": "boolean", "default": False},
            "include_dxf": {"type": "boolean", "default": False},
            "preview_width": {"type": "integer", "minimum": 64, "maximum": 2048, "default": 1200},
            "preview_height": {"type": "integer", "minimum": 64, "maximum": 2048, "default": 600},
        }, "required": ["targets"], "additionalProperties": False},
        lambda args: _project_panel_curve_assembly_drawing(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_project_panel_assembly_boundary_drawing",
        "Read an explicit list of up to 128 native PANE elements and create paged shared E3D WORLD FRONT/TOP/SIDE views of their PAVE control-point polygons. Defaults to 16 PANE sources per page; choose 32, 64 or 128 for denser grouped pages or a whole-equipment control-polygon overview. Each page returns its own PNG, with optional SVG/DXF, and every line retains global source index and PANE reference. This supports large assemblies even when PANE HEIG/SJUS prevents prism-mesh generation. Every PANE and WORLD PAVE position must be complete and identity-matched, with one identical native WORLD frame across all sources; each source retains its native revision when available. The complete request is bounded to 2048 PAVE points and 12000 projected line segments. This is control-polygon composition only: no shell union, thickness, outer/inner loop, holes, visibility, hidden-line removal, cuts or native tessellation are inferred. Separate native reads and before/after context observations are not an atomic snapshot. Read-only: no model, current element, Drawlist, camera or view changes.",
        {"type": "object", "properties": {
            "targets": {"type": "array", "minItems": 1, "maxItems": 128,
                        "items": {"type": "string", "minLength": 2, "maxLength": 1024},
                        "description": "Explicit PANE /NAME or =database/element references. Use e3d_find_descendants to collect and paginate the equipment's PANE members first."},
            "panels_per_page": {"type": "integer", "enum": [16, 32, 64, 128], "default": 16,
                                "description": "Number of explicit PANE sources overlaid per drawing page; controls image density and page count."},
            "views": {"type": "array", "minItems": 1, "maxItems": 3, "uniqueItems": True,
                      "items": {"type": "string", "enum": ["FRONT", "TOP", "SIDE"]},
                      "default": ["FRONT", "TOP", "SIDE"]},
            "include_svg": {"type": "boolean", "default": False},
            "include_dxf": {"type": "boolean", "default": False},
            "preview_width": {"type": "integer", "minimum": 64, "maximum": 2048, "default": 1200},
            "preview_height": {"type": "integer", "minimum": 64, "maximum": 2048, "default": 600},
        }, "required": ["targets"], "additionalProperties": False},
        lambda args: _project_panel_assembly_boundary_drawing(bridge, args),
        read_only=True,
    )
    server.add_tool(
        "e3d_panel_loop_relations",
        "Read one explicitly referenced PANE and report pairwise geometric relations between its simple, complete, coplanar PLOO polygons in PANE-local XY. Relations distinguish containment, separation, boundary crossings/contacts and unknown pairs. This is a geometry-only result: it does not infer outer/inner loops, holes, cut success or rendered visibility, and it does not upgrade the panel's overall topology_valid verdict. Native panel completeness and per-pair unknown reasons remain explicit. No model, current element, Drawlist or camera changes are made.",
        {"type": "object", "properties": {"refno": {"type": "string",
            "description": "Explicit PANE /NAME or =database/element reference; current element is never implicit."}},
         "required": ["refno"], "additionalProperties": False},
        lambda args: _panel_loop_relations(bridge, args),
        read_only=True,
    )
    server.add_tool(
        "e3d_update_panel",
        "Reshape one explicitly referenced native PANE from 3 to 256 simple counterclockwise local XY vertices, without holes or a repeated closing vertex. Inspect e3d_panel_geometry, preview this update, then execute with expected_revision from the preview and verify readback or an image receipt. Optional thickness and justification preserve their current values when omitted. Retains the PANE, PLOO and existing prefix PAVE references; growth appends vertices, while shrinking requires allow_vertex_deletion=true and preview reports the exact removed references. Deletions are not atomically reversible. Inspect partial, rollback and actual residual state after failure; never automatically replay a write. Does not accept transforms or rename the panel.",
        {"type": "object", "properties": {
            "refno": {"type": "string", "description": "Explicit PANE /NAME or =database/element reference."},
            "vertices": {"type": "array", "items": {"type": "array", "items": {"type": "number"},
                         "minItems": 2, "maxItems": 2}, "minItems": 3, "maxItems": 256,
                         "description": "Replacement local [x,y] vertices in millimetres, counterclockwise with no closing repeat."},
            "thickness": {"type": "number", "exclusiveMinimum": 0,
                          "description": "Positive finite millimetres; omit to preserve the current thickness."},
            "justification": {"type": "string", "enum": ["positive", "centre", "negative"],
                              "x-normalize": "trim_casefold",
                              "description": "Thickness direction along local Z; omit to preserve the current justification."},
            "expected_revision": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$", "minLength": 64, "maxLength": 64,
                                  "description": "Revision from native inspection/preview. Required for execution; may be omitted for preview."},
            "allow_vertex_deletion": {"type": "boolean", "default": False,
                                      "description": "Explicitly permit removing trailing PAVE vertices when shrinking the polygon."},
            "dry_run": {"type": "boolean", "default": True},
        }, "required": ["refno", "vertices"], "additionalProperties": False},
        _with_capture_receipt(bridge, "e3d_update_panel", _update_panel, settings),
        capture_receipt=True,
    )
    server.add_tool(
        "e3d_primitive_geometry",
        "Read one explicitly referenced native CYLI, NCYL, BOX, NBOX, CONE, NCON or positive DISH: inspect dimensions, placement, child references, eligibility findings and the revision token before a guarded update. DISH uses DIAM, HEIG and RADI; its base circle is at local z=0, and only a standard DISH without children is eligible for guarded editing. Does not select the current element, write geometry or change the view. Inspect native availability, completeness and read errors before interpreting the result.",
        {"type": "object", "properties": {"refno": {"type": "string",
            "description": "Explicit primitive /NAME or =database/element reference; current element is never implicit."}},
         "required": ["refno"], "additionalProperties": False},
        lambda args: _primitive_geometry(bridge, args),
        read_only=True,
    )
    server.add_tool(
        "e3d_generated_csg",
        "Read bounded native generated-CSG descriptors for one explicit BOX/CYLI/CONE/DISH/NOZZ/PANE. "
        "Returns native item types, raw real/integer parameters, 12-value transforms and untransformed local limits; unknown and negative item types remain explicit. "
        "This is an analytic descriptor observation, not native tessellation or a resolved Boolean mesh. Raw units and transform conventions remain unverified. "
        "Rejects hierarchy-wide requests. Output budgets do not bound SDK generation time or memory. A native generation failure quarantines further SDK work until a module restart. "
        "Does not change the model, current element, Drawlist or camera.",
        {"type": "object", "properties": {
            "refno": {"type": "string", "description": "One explicit supported element /NAME or =database/element reference."},
            "max_trees": {"type": "integer", "minimum": 1, "maximum": 16, "default": 8},
            "max_items": {"type": "integer", "minimum": 1, "maximum": 256, "default": 64},
        }, "required": ["refno"], "additionalProperties": False},
        lambda args: _generated_csg(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_primitive_mesh",
        "Read one explicitly referenced primitive and derive a bounded triangle mesh from its native parameters for positive BOX, CYLI, CONE, or a positive DISH with RADI=0. DISH RADI=0 uses a mathematical spherical-cap approximation with its base at local z=0; nonzero RADI is rejected because its native curved surface is not parameterized by the stored value. coordinate_space defaults to primitive_local; WORLD applies the validated native WORLD pose to vertices and normals. This is parameter-derived geometry, not E3D tessellation or rendered/Boolean geometry. Negative primitives, any child geometry, incomplete reads, unsupported shapes, or invalid WORLD frames are rejected. No model, current element, Drawlist or camera changes are made.",
        {"type": "object", "properties": {
            "chord_tolerance_mm": _CHORD_TOLERANCE_SCHEMA,
            "refno": {"type": "string", "description": "Explicit positive BOX, CYLI, CONE, or standalone DISH reference."},
            "radial_segments": {"type": "integer", "minimum": 12, "maximum": 128, "default": 48,
                                "description": "Circumferential segments for CYLI/CONE and both circumferential/latitude segments for DISH. BOX ignores this value."},
            "coordinate_space": {"type": "string", "enum": ["primitive_local", "WORLD"], "default": "primitive_local",
                                 "description": "Output coordinates; WORLD uses the primitive's validated WORLD pose."},
        }, "required": ["refno"], "additionalProperties": False},
        lambda args: _primitive_mesh(bridge, args),
        read_only=True,
    )
    server.add_tool(
        "e3d_primitive_csg_mesh",
        "Read a positive BOX/CYLI/CONE and its 1 to 32 flat leaf NCYL/NBOX/NCON children, then compute their owner-member parameter difference with manifold3d Mesh64 in a bounded 30-second worker. "
        "The current native bridge rejects DISH with child members. Output is WORLD millimetres, at most 10,000 vertices/20,000 triangles. Preserves inward cavity shells; fully removed material is successful and empty. "
        "Requires the optional geometry dependencies. This is parameter CSG, not native tessellation; native negative-application scope remains unverified. "
        "Two matching complete native parameter reads and session/project/MDB boundary observations are required, without an atomic database snapshot. Optional native volume comparison preserves representation-dependent discrepancies and does not verify shape or cut scope. "
        "No model, current element, Drawlist, camera or view changes.",
        {"type": "object", "properties": {
            "chord_tolerance_mm": _CHORD_TOLERANCE_SCHEMA,
            "refno": {"type": "string", "description": "Explicit positive primitive /NAME or =database/element reference with supported negative children."},
            "radial_segments": {"type": "integer", "minimum": 12, "maximum": 99, "default": 48,
                                "description": "Radial facet count for curved parent and cutter operands."},
            "include_native_volume": {"type": "boolean", "default": False,
                                      "description": "Read native GVOL/NVOL ONLY between the two parameter reads; requested unavailable volume fails the operation. Native volume does not prove the parameter Boolean matches native shape."},
            "mesh_detail": {"type": "string", "enum": ["full", "summary"], "default": "full",
                            "description": "full includes indexed geometry; summary retains topology, hash, volume and source evidence without geometry arrays."},
        }, "required": ["refno"], "additionalProperties": False},
        lambda args: _primitive_csg_operation(bridge, args, drawing_requested=False), read_only=True,
    )
    server.add_tool(
        "e3d_project_primitive_csg_drawing",
        "Read a supported positive primitive with 1 to 32 flat negative children and project its manifold3d Mesh64 owner-member parameter difference in WORLD millimetres into FRONT/TOP/SIDE linework. "
        "Supports BOX/CYLI/CONE parents, with NCYL/NBOX/NCON cutters, bounded hidden-line removal and optional hidden edges. The current native bridge rejects DISH with child members. "
        "Returns a PNG preview plus optional SVG/DXF carrying PARAMETER CSG - NATIVE CUT SCOPE UNVERIFIED. Fully removed material succeeds with empty views and no image. "
        "Requires optional geometry dependencies, two matching native parameter reads and matching session/project/MDB boundaries; these separate observations are not atomic. "
        "Native tessellation and negative-application scope remain unverified. Optional native volume comparison reports differences without establishing shape equivalence. "
        "No model, current element, Drawlist, camera or view changes.",
        {"type": "object", "properties": {
            "chord_tolerance_mm": _CHORD_TOLERANCE_SCHEMA,
            "refno": {"type": "string", "description": "Explicit positive primitive /NAME or =database/element reference with supported negative children."},
            **{key: value for key, value in _drawing_option_schema(projection_sheet=False).items()
               if key not in {"sheet", "radial_segments"}},
            "radial_segments": {"type": "integer", "minimum": 12, "maximum": 99, "default": 48,
                                "description": "Radial facet count for curved parent and cutter operands."},
            "include_native_volume": {"type": "boolean", "default": False,
                                      "description": "Also read native GVOL/NVOL ONLY between parameter reads. Requested unavailable volume fails; discrepancies are retained without verifying native cut scope."},
        }, "required": ["refno"], "additionalProperties": False},
        lambda args: _primitive_csg_operation(bridge, args, drawing_requested=True), read_only=True,
    )
    server.add_tool(
        "e3d_element_mesh",
        "Read a parameter-derived triangle mesh for one explicit reference and required SDK base type. Supports simple PANE, positive BOX/CYLI/CONE and childless positive DISH with RADI=0. Dispatches one native geometry read and checks the returned element identity before deriving a mesh. No type fallback, Boolean cuts, native tessellation, or model/view changes.",
        {"type": "object", "properties": {
            "chord_tolerance_mm": _CHORD_TOLERANCE_SCHEMA,
            "refno": {"type": "string", "description": "Explicit /NAME or =database/element reference."},
            "expected_type": {"type": "string", "enum": list(_ELEMENT_MESH_TYPES),
                              "description": "Required SDK base type short name. Use PANE for panels."},
            "coordinate_space": {"type": "string", "enum": ["local", "WORLD"], "default": "local",
                                 "description": "Local to the requested element, or verified WORLD millimetres."},
            "mesh_detail": {"type": "string", "enum": ["full", "summary"], "default": "full",
                            "description": "full returns indexed geometry; summary returns counts, bounds, area and topology without vertex/face arrays."},
            "radial_segments": {"type": "integer", "minimum": 12, "maximum": 99, "default": 48,
                                "description": "Optional curve resolution for CYLI, CONE and DISH only. Unified mesh output is capped at 99 segments to remain within bounded measurement limits."},
        }, "required": ["refno", "expected_type"], "additionalProperties": False},
        lambda args: _element_mesh(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_project_element_drawing",
        "Read one explicitly referenced PANE or supported positive primitive, derive its bounded parameter mesh, and project topology-derived FRONT/TOP/SIDE orthographic linework. Returns a deterministic PNG preview image and structured linework with millimetre projection bounds. Bounded hidden-line removal defaults on and tests projected mesh depth; it does not evaluate E3D CSG, Boolean cuts, PANE hole semantics, native tessellation or engineering tolerances. Work-budget exhaustion fails closed without a partial drawing. Does not modify the model, current element, Drawlist, camera or view.",
        {"type": "object", "properties": {
            "refno": {"type": "string", "description": "Explicit /NAME or =database/element reference."},
            "expected_type": {"type": "string", "enum": list(_ELEMENT_MESH_TYPES),
                              "description": "Required SDK base type short name; use PANE for panels."},
            "coordinate_space": {"type": "string", "enum": ["local", "WORLD"], "default": "WORLD",
                                 "description": "Projection coordinates; WORLD uses the validated E3D WORLD pose."},
            **_drawing_option_schema(),
        }, "required": ["refno", "expected_type"], "additionalProperties": False},
        lambda args: _project_element_drawing(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_project_material_assembly_drawing",
        "Read 1 to 128 explicit WORLD/mm sources and jointly project mixed parameter meshes and owner-member parameter differences, with cross-source hidden-line removal. "
        "Every target must specify expected_type and geometry_mode: parameter_mesh reads supported PANE/BOX/CYLI/CONE or standalone RADI=0 DISH; owner_member_difference reads BOX/CYLI/CONE with 1 to 32 flat NCYL/NBOX/NCON cutters using the optional Mesh64 geometry engine. No automatic fallback between modes. "
        "Keeps inward cavity shells and successful empty sources, source index ranges and per-line source attribution. Returns a PNG and optional SVG/DXF; an entirely empty assembly succeeds with no image. "
        "Total geometry is limited to 10,000 vertices/20,000 triangles, callback receipts to 16 MiB, structured output and PNG to 8 MiB each, with bounded visibility work. "
        "Requires two matching complete parameter observations per source and matching session/project/MDB boundaries; these independent reads are not an atomic snapshot. "
        "Cut differences apply only to the explicitly chosen sources. No inter-source union, welding, intersection curves, equipment traversal, native tessellation or verified native negative-application scope. "
        "Does not modify the model, current element, Drawlist, camera or view.",
        {"type": "object", "properties": {
            "targets": {"type": "array", "minItems": 1, "maxItems": 128,
                        "items": {"type": "object", "properties": {
                            "refno": {"type": "string", "minLength": 2, "maxLength": 1024,
                                      "description": "Explicit /NAME or =database/element source reference."},
                            "expected_type": {"type": "string", "enum": list(_ELEMENT_MESH_TYPES)},
                            "geometry_mode": {"type": "string", "enum": ["parameter_mesh", "owner_member_difference"],
                                              "description": "Required explicit source interpretation; no mode fallback or native cut-scope inference."},
                        }, "required": ["refno", "expected_type", "geometry_mode"], "additionalProperties": False,
                            "allOf": [{"if": {"properties": {"geometry_mode": {"const": "owner_member_difference"}}},
                                       "then": {"properties": {"expected_type": {"enum": ["BOX", "CYLI", "CONE"]}}}}]}},
            **{key: value for key, value in _drawing_option_schema(projection_sheet=False).items()
               if key != "sheet"},
            "source_detail": {"type": "string", "enum": ["summary", "full"], "default": "summary",
                              "description": "summary retains source identity, hashes, read sequence and context evidence; full also returns decoded callback receipts with native parameters and arrays. All source verification runs in both modes."},
            "radial_segments": {"type": "integer", "minimum": 12, "maximum": 99, "default": 48,
                                "description": "Curve resolution for positive primitives and negative cutter operands."},
            "include_svg": {"type": "boolean", "default": False,
                            "description": "Include SVG for a nonempty successful assembly; fully empty material has no image or vectors."},
        }, "required": ["targets"], "additionalProperties": False},
        lambda args: _project_material_assembly_drawing(bridge, args), read_only=True,
    )
    material_sheet_options = _drawing_option_schema()
    material_sheet_options.pop("views")
    material_sheet_options["sheet"]["properties"]["projection_method"]["default"] = "third_angle"
    material_sheet_options["sheet"]["properties"]["projection_method"]["description"] = (
        "Aligned physical three-view layout; defaults to third_angle. FRONT is recomputed from -Y, TOP from +Z and right SIDE from +X. "
        "Third-angle places TOP above FRONT and SIDE right; first-angle places TOP below and SIDE left. All views share one scale; no full drawing-standard certification.")
    material_sheet_options["sheet"]["description"] = (
        "Required printable sheet and title block shared by PNG/SVG/DXF. Paper strokes and DXF/SVG use paper millimetres at 1:N; "
        "structured model coordinates and dimension values remain model millimetres. A non-fitting explicit scale fails without cropping.")
    material_sheet_options["radial_segments"]["description"] = "Curve resolution for positive primitives and negative cutter operands."
    server.add_tool(
        "e3d_project_material_assembly_sheet",
        "Read 1 to 128 explicitly typed WORLD/mm material sources and compose aligned FRONT/TOP/right-SIDE views on one printable A3/A4 sheet. "
        "Each target requires geometry_mode: parameter_mesh for admitted PANE/BOX/CYLI/CONE/standalone RADI=0 DISH, or owner_member_difference for admitted BOX/CYLI/CONE with direct NCYL/NBOX/NCON cutters. No mode fallback. "
        "Reuses one jointly occluded material projection and one source workflow, preserving per-source hashes, cavity proof, empty ranges and structured line/stroke attribution. "
        "Requires sheet; defaults to third-angle with first-angle available. FRONT looks from -Y, TOP from +Z and right SIDE from +X. One shared scale applies to paper linework; dimension labels remain model millimetres. "
        "Returns PNG and optional paper-mm SVG/DXF with borders, title block and an explicit unverified-native-cut-scope footer. Vector coordinates use 15 decimal places for serialization, not a claim of engineering accuracy. "
        "An explicit scale that cannot fit, failed source, context change, budget or export error returns no partial image; all-empty material succeeds without an image. "
        "Limits: 10,000 vertices/20,000 triangles, 16 MiB decoded source receipts and 8 MiB each structured output/PNG. Independent repeated observations are not an atomic snapshot. "
        "No native Draft sheet, equipment traversal, inter-source union, tessellation or verified native cut-application scope. Does not modify model, CE, Drawlist or camera.",
        {"type": "object", "properties": {
            "targets": {"type": "array", "minItems": 1, "maxItems": 128,
                        "items": {"type": "object", "properties": {
                            "refno": {"type": "string", "minLength": 2, "maxLength": 1024,
                                      "description": "Explicit /NAME or =database/element source reference."},
                            "expected_type": {"type": "string", "enum": list(_ELEMENT_MESH_TYPES)},
                            "geometry_mode": {"type": "string", "enum": ["parameter_mesh", "owner_member_difference"]},
                        }, "required": ["refno", "expected_type", "geometry_mode"], "additionalProperties": False,
                            "allOf": [{"if": {"properties": {"geometry_mode": {"const": "owner_member_difference"}}},
                                       "then": {"properties": {"expected_type": {"enum": ["BOX", "CYLI", "CONE"]}}}}]}},
            **material_sheet_options,
            "source_detail": {"type": "string", "enum": ["summary", "full"], "default": "summary",
                              "description": "Both modes fully verify sources; full additionally returns raw decoded callback receipts."},
        }, "required": ["targets", "sheet"], "additionalProperties": False},
        lambda args: _project_material_assembly_sheet(bridge, args), read_only=True,
    )
    material_tile_options = _drawing_option_schema(projection_sheet=False)
    material_tile_options.pop("views")
    material_tile_options["preview_width"]["default"] = 1400
    material_tile_options["preview_height"]["default"] = 990
    material_tile_options["sheet"]["required"] = ["scale_denominator"]
    material_tile_options["sheet"]["description"] = (
        "Required page paper/title settings and explicit shared model-to-paper scale 1:N. "
        "Each page is one crop of the same physical projection; no first/third-angle arrangement. Vector files use paper millimetres.")
    material_tile_options["radial_segments"]["description"] = "Curve resolution for positive primitives and negative cutter operands."
    server.add_tool(
        "e3d_project_material_assembly_tiles",
        "Read 1 to 128 explicit WORLD/mm material sources once through the repeated-observation workflow, project one physical FRONT/TOP/SIDE view once with global cross-source visibility, then clip that same linework into contiguous printable pages at one required explicit scale. "
        "Each target declares expected_type and geometry_mode: admitted parameter_mesh or BOX/CYLI/CONE owner_member_difference, with no mode fallback. FRONT looks from -Y, TOP from +Z, SIDE from +X. "
        "Pages share one anchored projected-coordinate grid; optional overlap is paper mm. Preserves global line index, clipped source interval, source identity and inherited visibility. Boundary-collinear lines may appear on adjacent closed rectangles; overlap intentionally repeats geometry. "
        "Returns one PNG per page, structured grid/coverage/source evidence, optional SVG/DXF paper-mm files and visible row/column, neighbors and projected-coordinate window labels. Blank tiles are retained for contiguous assembly; window labels are not full-model dimensions. "
        "Limits: 24 pages, 8 million preview pixels, 40,000 clipped segments, 10,000 vertices/20,000 triangles, 16 MiB source receipts, 8 MiB structured output and 8 MiB combined PNG bytes. Any source/projection/coverage/page/export/context/budget failure suppresses all page images. All-empty material succeeds with zero pages. "
        "Source reads are separate observations, not an atomic snapshot. No native Draft drawing, tessellation, native-cut-scope verification, inter-source union or automatic equipment traversal. Does not modify model, CE, Drawlist or camera.",
        {"type": "object", "properties": {
            "targets": {"type": "array", "minItems": 1, "maxItems": 128,
                "items": {"type": "object", "properties": {
                    "refno": {"type": "string", "minLength": 2, "maxLength": 1024},
                    "expected_type": {"type": "string", "enum": list(_ELEMENT_MESH_TYPES)},
                    "geometry_mode": {"type": "string", "enum": ["parameter_mesh", "owner_member_difference"]},
                }, "required": ["refno", "expected_type", "geometry_mode"], "additionalProperties": False,
                "allOf": [{"if": {"properties": {"geometry_mode": {"const": "owner_member_difference"}}},
                           "then": {"properties": {"expected_type": {"enum": ["BOX", "CYLI", "CONE"]}}}}]}},
            "view": {"type": "string", "enum": ["FRONT", "TOP", "SIDE"], "default": "TOP"},
            **material_tile_options,
            "tiling": {"type": "object", "properties": {
                "overlap_mm": {"type": "number", "minimum": 0, "default": 10,
                               "description": "Paper overlap; must be less than half the smaller page viewport extent."},
                "max_pages": {"type": "integer", "minimum": 1, "maximum": 24, "default": 24},
            }, "additionalProperties": False},
            "source_detail": {"type": "string", "enum": ["summary", "full"], "default": "summary"},
        }, "required": ["targets", "sheet"], "additionalProperties": False},
        lambda args: _project_material_assembly_tiles(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_project_assembly_drawing",
        "Read 1 to 16 explicit PANE or supported positive BOX/CYLI/CONE/DISH targets once each in WORLD millimetres, combine their independent parameter meshes, and generate joint FRONT/TOP/SIDE linework with cross-element occlusion and a PNG preview. Optional SVG/DXF share the same classified geometry. Requires complete, correctly resolved sources; duplicate target identities and failed or changed session/project/MDB boundary observations produce no partial drawing. Reads are separate live observations without a database lock or common snapshot. Total mesh is limited to 10,000 vertices and 20,000 triangles, with bounded visibility work. Preserves source index ranges; does not weld vertices, compute a Boolean union, intersection curves, cuts or hole semantics, or traverse equipment automatically. DXF layers are per-view/kind, not per-component. Does not modify the model, CE, Drawlist, camera or view.",
        {"type": "object", "properties": {
            "targets": {"type": "array", "minItems": 1, "maxItems": MAX_DRAWING_SOURCES,
                        "items": {"type": "object", "properties": {
                            "refno": {"type": "string", "minLength": 2, "maxLength": 1024,
                                      "description": "Explicit /NAME or =database/element reference."},
                            "expected_type": {"type": "string", "enum": list(_ELEMENT_MESH_TYPES)},
                        }, "required": ["refno", "expected_type"], "additionalProperties": False}},
            "coordinate_space": {"type": "string", "enum": ["WORLD"], "default": "WORLD",
                                 "description": "All components must share the verified WORLD millimetre frame."},
            **_drawing_option_schema(),
        }, "required": ["targets"], "additionalProperties": False},
        lambda args: _project_assembly_drawing(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_section_element_drawing",
        "Read one explicitly referenced PANE or supported positive BOX/CYLI/CONE/DISH once, and intersect its closed parameter-derived triangle mesh with an arbitrary plane. Returns indexed section vertices, source-triangle segments, closed contours, a SECTION PNG preview and optional SVG/DXF or printable sheet. Optional hatch generates bounded parallel linework using nonzero winding of verified simple non-touching section contours; spacing is in section model millimetres. Plane origin/normal/up use the selected local or WORLD frame; section coordinates are millimetres relative to that origin along the returned orthonormal right/up axes. A miss returns a successful empty section without an image. Coplanar patches, tangency/topology ambiguity, incomplete geometry or requested hatch failure produce no partial drawing. Contour fill does not establish native hole roles or E3D Boolean material; no clipping of the retained solid, native tessellation, CSG cuts, or manufacturing certification. Does not modify the model, current element, Drawlist, camera or view.",
        {"type": "object", "properties": {
            "refno": {"type": "string", "description": "Explicit /NAME or =database/element reference."},
            "expected_type": {"type": "string", "enum": list(_ELEMENT_MESH_TYPES)},
            "coordinate_space": {"type": "string", "enum": ["local", "WORLD"], "default": "WORLD",
                                 "description": "The plane and source mesh use this same coordinate frame."},
            **_section_option_schema(),
        }, "required": ["refno", "expected_type", "plane"], "additionalProperties": False},
        lambda args: _section_element_drawing(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_section_assembly_drawing",
        "Read 1 to 16 explicit PANE or supported positive BOX/CYLI/CONE/DISH targets once each in WORLD millimetres and section their independent parameter meshes with one arbitrary WORLD plane. Returns source-indexed closed contours, a SECTION PNG preview, optional SVG/DXF or printable sheet, and optional bounded nonzero contour hatching. Every source is checked before sectioning; failed sources, duplicate resolved identities, changed session/project/MDB boundary observations, tangency or coplanar ambiguity produce no partial drawing. All-miss is a successful empty section with no image. Global source-triangle indices, local source-triangle indices and source-to-section membership remain explicit. Source shells are not welded or Boolean-unioned: without hatch, independent crossing/touching/coincident contours are overlaid; hatch rejects crossing/touching contours rather than inferring material or holes. Nested outward shells remain filled under nonzero winding. No native CSG, retained-solid projection, automatic traversal or manufacturing certification. Separate reads and boundary observations do not establish a common database snapshot. Does not modify the model, CE, Drawlist, camera or view.",
        {"type": "object", "properties": {
            "targets": {"type": "array", "minItems": 1, "maxItems": MAX_DRAWING_SOURCES,
                        "items": {"type": "object", "properties": {
                            "refno": {"type": "string", "minLength": 2, "maxLength": 1024,
                                      "description": "Explicit /NAME or =database/element reference."},
                            "expected_type": {"type": "string", "enum": list(_ELEMENT_MESH_TYPES)},
                        }, "required": ["refno", "expected_type"], "additionalProperties": False}},
            "coordinate_space": {"type": "string", "enum": ["WORLD"], "default": "WORLD",
                                 "description": "Both the section plane and every source mesh use verified WORLD millimetres."},
            **_section_option_schema(),
        }, "required": ["targets", "plane"], "additionalProperties": False},
        lambda args: _section_assembly_drawing(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_compose_drawing_sheet",
        "Compose aligned FRONT/TOP/right-SIDE whole-model projections and an arbitrary-plane SECTION on one printable sheet from 1–16 explicit supported elements in WORLD millimetres. All four views reuse the same combined parameter mesh and one paper scale. Returns a PNG image, structured source/projection/section evidence, optional SVG/DXF, optional bounded contour hatch and revision-bound SECTION dimensions. Requires sheet; defaults to third-angle arrangement, with first-angle available. A plane miss, incomplete source, context change, ambiguous section, stale dimensions, visibility/hatch budget or export failure returns evidence without a partial image. Source reads are separate live observations, not an atomic database snapshot. No native Draft sheet, Boolean cuts, automatic traversal or certified engineering drawing. Does not modify the model, CE, Drawlist or camera.",
        {"type": "object", "properties": {
            "targets": {"type": "array", "minItems": 1, "maxItems": MAX_DRAWING_SOURCES,
                        "items": {"type": "object", "properties": {
                            "refno": {"type": "string", "minLength": 2, "maxLength": 1024,
                                      "description": "Explicit /NAME or =database/element reference."},
                            "expected_type": {"type": "string", "enum": list(_ELEMENT_MESH_TYPES)},
                        }, "required": ["refno", "expected_type"], "additionalProperties": False}},
            "coordinate_space": {"type": "string", "enum": ["WORLD"], "default": "WORLD"},
            **_composite_option_schema(),
        }, "required": ["targets", "plane", "sheet"], "additionalProperties": False},
        lambda args: _compose_drawing_sheet(bridge, args), read_only=True,
    )
    atlas_options = _composite_option_schema()
    atlas_options.pop("dimensions")
    atlas_options.pop("plane")
    atlas_options["sheet"]["required"] = ["scale_denominator"]
    server.add_tool(
        "e3d_compose_drawing_atlas",
        "Compose 2–24 explicitly scoped pages, each with 1–16 supported elements, a WORLD section plane and its own independent source mesh. Optional section dimensions are bound separately per page. Pages share one required explicit sheet.scale_denominator and produce separate PNG image blocks plus per-page evidence and optional SVG/DXF. manifest_sha256 covers the page manifest and shared drawing options. Total source count is capped at 384 and total preview area at 8 million pixels. Each page fits at the shared scale or fails without cropping; one failed page suppresses all page images. Session/project/MDB boundary observations must agree across pages, but the reads are live and not atomic. Targets must be unique within each page; reusing an element across different pages is allowed. This creates separately scoped sheets, not seamless tiles or a globally occluded projection of one oversized model. No native Draft sheet, Boolean cuts, automatic traversal or certified engineering drawing. Read-only: no model, CE, Drawlist or camera changes.",
        {"type": "object", "properties": {
            "pages": {"type": "array", "minItems": 2, "maxItems": MAX_DRAWING_ATLAS_PAGES, "items": {
                "type": "object", "properties": {
                    "title": {"type": "string", "minLength": 1, "maxLength": 120,
                              "pattern": "^[A-Za-z0-9 .+/:_-]*$"},
                    "drawing_number": {"type": "string", "maxLength": 40,
                                       "pattern": "^[A-Za-z0-9 .+/:_-]*$"},
                    "plane": _section_option_schema()["plane"],
                    "dimensions": _section_option_schema()["dimensions"],
                    "targets": {"type": "array", "minItems": 1, "maxItems": MAX_DRAWING_SOURCES,
                        "items": {"type": "object", "properties": {
                            "refno": {"type": "string", "minLength": 2, "maxLength": 1024,
                                      "description": "Explicit /NAME or =database/element reference."},
                            "expected_type": {"type": "string", "enum": list(_ELEMENT_MESH_TYPES)},
                        }, "required": ["refno", "expected_type"], "additionalProperties": False}},
                }, "required": ["targets", "plane"], "additionalProperties": False},
                "description": "Explicit page scopes and section planes in order. Each page accepts up to 16 unique explicit elements."},
            "coordinate_space": {"type": "string", "enum": ["WORLD"], "default": "WORLD"},
            **atlas_options,
        }, "required": ["pages", "sheet"], "additionalProperties": False},
        lambda args: _compose_drawing_atlas(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_compare_element_meshes",
        "Read two explicitly referenced elements once each and compare their parameter-derived meshes. WORLD (default) allows spatial bounds and matching-index vertex displacement comparisons; local mode compares per-element local coordinates and does not claim a common position frame. Reports measured surface-area deltas; volume is included only when each mesh validates as one outward closed component. Indexed vertex displacement is reported only for WORLD coordinates when vertex counts and triangle index lists match exactly. Meshes are approximations, not E3D tessellation or Boolean results. The two reads are separate observations without a database lock or common snapshot. Failed, incomplete or identity-mismatched reads produce per-target errors and no comparison conclusion. Read-only: no model, current element, Drawlist, camera or view changes.",
        {"type": "object", "properties": {
            "first_refno": {"type": "string", "description": "Explicit /NAME or =database/element reference for the first element."},
            "first_expected_type": {"type": "string", "enum": list(_ELEMENT_MESH_TYPES),
                                    "description": "Required SDK base type for the first element."},
            "second_refno": {"type": "string", "description": "Explicit /NAME or =database/element reference for the second element."},
            "second_expected_type": {"type": "string", "enum": list(_ELEMENT_MESH_TYPES),
                                     "description": "Required SDK base type for the second element."},
            "coordinate_space": {"type": "string", "enum": ["local", "WORLD"], "default": "WORLD",
                                 "description": "WORLD provides a common spatial frame in millimetres; local compares each element about its own origin and axes."},
            "radial_segments": {"type": "integer", "minimum": 12, "maximum": 99, "default": 48,
                                "description": "Curve resolution for CYLI, CONE or DISH targets; ignored for PANE and BOX."},
        }, "required": ["first_refno", "first_expected_type", "second_refno", "second_expected_type"],
           "additionalProperties": False},
        lambda args: _compare_element_meshes(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_update_primitive",
        "Resize and/or move one explicitly referenced native CYLI, NCYL, BOX, NBOX, CONE, NCON or positive DISH in one guarded SDK operation. Read e3d_primitive_geometry, preview this update, pass its revision as expected_revision for execution, then verify native volume/readback and an image receipt. Omitted dimensions and transforms keep their current values. Preserves the primitive and existing child references; supported negative children move with a positive parent while their local cut placements remain unchanged. DISH editing supports a standard positive DISH without children, DIAM > 0, HEIG > 0 and RADI >= 0; these are MCP admission limits, not proof of all native geometric constraints. Its position is the base circle centre, not the bounding-box centre. NDIS is outside guarded editing. Native inspection decides shape-specific dimensions, zero-value validity and child eligibility. This is not an atomic database transaction: inspect actual rollback and residual state after failure, and never automatically replay a write. Does not rename, add to the Drawlist, or fit the camera.",
        {"type": "object", "properties": {
            "refno": {"type": "string", "description": "Explicit primitive /NAME or =database/element reference."},
            "dimensions": {"type": "object", "minProperties": 1, "maxProperties": 3,
                           "propertyNames": {"pattern": r"^\s*(?:[Dd][Ii][Aa][Mm]|[Hh][Ee][Ii][Gg]|[Xx][Ll][Ee][Nn]|[Yy][Ll][Ee][Nn]|[Zz][Ll][Ee][Nn]|[Dd][Tt][Oo][Pp]|[Dd][Bb][Oo][Tt]|[Rr][Aa][Dd][Ii])\s*$"},
                           "additionalProperties": {"type": "number", "minimum": 0},
                           "description": "One to three finite, nonnegative millimetre values keyed by DIAM, HEIG, XLEN, YLEN, ZLEN, DTOP, DBOT or RADI. DISH uses DIAM/HEIG/RADI; stored RADI is not DIAM/2 or a verified physical knuckle radius. Inspect the native shape and volume. Names normalize to uppercase; duplicates are rejected. Native shape validation decides applicable keys and whether zero is permitted."},
            **transform_properties,
            "expected_revision": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$", "minLength": 64, "maxLength": 64,
                                  "description": "Revision from native inspection/preview. Required for execution; may be omitted for preview."},
        }, "required": ["refno"], "anyOf": [{"required": ["dimensions"]}, {"required": ["position"]}, {"required": ["orientation"]}],
         "additionalProperties": False},
        _with_capture_receipt(bridge, "e3d_update_primitive", _update_primitive, settings),
        capture_receipt=True,
    )
    server.add_tool(
        "e3d_set_transform",
        "Preview a position and/or orientation change in an explicit SDK reference frame, converting to verified native storage coordinates. A successful native preview returns a revision bound to the target state and submitted transform; pass it as expected_revision for execution. Real writes require dry_run=false, that revision and command permission. The revisioned endpoint rejects stale target/frame/request state before setters; older bridges return 404 and are never retried through the unguarded legacy route. Captures original values and attempts restoration on partial setter failure. After attempted writes and any rollback, sends one Command.Update notification and verifies the requested fields, frame identity and CE again. Inspect notification_update: callbacks may change database/UI state; completion does not prove spatial-map repair. write_applied and rollback_succeeded are pre-notification observations. Notification failure never repeats writes or rollback. Does not select the element as CE.",
        {"type": "object", "properties": {"refno": {"type": "string"}, **transform_properties,
            "expected_revision": {"type": "string", "pattern": r"^[0-9a-fA-F]{64}$", "minLength": 64, "maxLength": 64,
                                  "description": "64-hex revision from a preview of the same target and transform; required for execution."}},
         "required": ["refno"],
         "anyOf": [{"required": ["position"]}, {"required": ["orientation"]}],
         "allOf": [{"if": {"properties": {"dry_run": {"const": False}}, "required": ["dry_run"]},
                    "then": {"required": ["expected_revision"]}}],
         "additionalProperties": False},
        _with_capture_receipt(bridge, "e3d_set_transform", _set_transform, settings),
        capture_receipt=True,
    )
    server.add_tool(
        "e3d_capture_view",
        "Return the current E3D 3D view as an MCP PNG image using View3D.GetBitmap, with actual camera/view/Drawlist observations around the native bitmap call. "
        "The observation status is consistent, changed or unverified; successful PNG capture alone does not prove view consistency. "
        "Does not set the camera or change Drawlist membership. Graphics refresh may process pending changes. "
        "A changed view may reflect user navigation; no camera write or edit is replayed. Inspect membership and fit explicitly when needed.",
        {"type": "object", "properties": {
            "width": {"type": "integer", "minimum": 64, "maximum": 2048, "default": 1280},
            "height": {"type": "integer", "minimum": 64, "maximum": 2048, "default": 960},
        }, "additionalProperties": False},
        lambda args: _capture_view(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_geometry_bounds",
        "Read one element's generated geometry bounds through SDK Spatial.LimitsBox without adding it to a view. Returns finite min/max in millimetres and native box attributes for comparison. coordinate_frame is explicitly unverified; compare known geometry before interpreting it as WORLD. Unavailable bounds are reported as a read failure, never as zero size.",
        {"type": "object", "properties": {"refno": {"type": "string"}}, "required": ["refno"], "additionalProperties": False},
        lambda args: _geometry_bounds(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_validate_triangle_mesh",
        "Validate a caller-supplied indexed triangle mesh without contacting E3D. Checks finite 3D coordinates, integer indices, relative degeneracy, duplicate faces, edge and vertex manifoldness, shared-edge winding, and signed volume per connected component. A valid result does not detect triangle self-intersections and does not certify E3D native CSG, tessellation or rendering.",
        {"type": "object", "properties": {
            "vertices": {"type": "array", "maxItems": MAX_VERTICES,
                         "items": {"type": "array", "minItems": 3, "maxItems": 3,
                                   "items": {"type": "number"}},
                         "description": "Indexed [x,y,z] coordinate list; coordinates are caller-supplied and their unit is not inferred."},
            "triangles": {"type": "array", "maxItems": MAX_TRIANGLES,
                          "items": {"type": "array", "minItems": 3, "maxItems": 3,
                                    "items": {"type": "integer"}},
                          "description": "Indexed [i,j,k] triangle list."},
        }, "required": ["vertices", "triangles"], "additionalProperties": False},
        lambda args: _validate_triangle_mesh(args), read_only=True,
    )
    server.add_tool(
        "e3d_measure_triangle_mesh",
        "Measure a caller-supplied indexed triangle mesh without contacting E3D. Returns an axis-aligned bounding box and the sum of supplied triangle areas when all coordinates and indices are finite and measurable. Also includes e3d_validate_triangle_mesh findings; open meshes can be measured but remain valid=false. Coordinates and area units are caller-supplied and never inferred. This does not detect self-intersections or certify E3D CSG, tessellation, engineering validity, or screen visibility.",
        {"type": "object", "properties": {
            "vertices": {"type": "array", "maxItems": MAX_VERTICES,
                         "items": {"type": "array", "minItems": 3, "maxItems": 3,
                                   "items": {"type": "number"}},
                         "description": "Indexed [x,y,z] coordinate list; units are caller-supplied."},
            "triangles": {"type": "array", "maxItems": MAX_TRIANGLES,
                          "items": {"type": "array", "minItems": 3, "maxItems": 3,
                                    "items": {"type": "integer"}},
                          "description": "Indexed [i,j,k] triangle list."},
        }, "required": ["vertices", "triangles"], "additionalProperties": False},
        lambda args: _measure_triangle_mesh(args), read_only=True,
    )
    server.add_tool(
        "e3d_geometry_overlap_candidates",
        "Compare two explicit groups of generated geometry AABBs using SDK Spatial.LimitsBox and return broad-phase overlap or near-contact candidates. This is not an exact clash, clearance, or engineering verdict: concavity, holes, negative geometry, and frame differences can change the real result. Bounds are compared as returned; coordinate_frame is SDK native (unverified) and coordinate_comparability is unverified, so validate the frame for the elements being compared before relying on exclusions. A gap equal to tolerance_mm is included; default zero includes touching bounds. Missing or invalid bounds leave affected pairs unknown and make the result incomplete. Reads at most 32 references per group and 1024 pairs, without editing the model or changing CE, Drawlist, or camera.",
        {"type": "object", "properties": {
            "first_refnos": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 32,
                             "description": "First group of explicit /NAME or =database/element references."},
            "second_refnos": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 32,
                              "description": "Second group of explicit /NAME or =database/element references."},
            "tolerance_mm": {"type": "number", "minimum": 0, "maximum": 1000000, "default": 0,
                              "description": "Maximum AABB gap on each axis to retain as a candidate, in mm."},
        }, "required": ["first_refnos", "second_refnos"], "additionalProperties": False},
        lambda args: _geometry_overlap_candidates(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_geometry_relation",
        "Read one ordered pair of explicit Design elements using E3D Spatial.ElementInElementExact(test, against, false). Optionally include Spatial.ElementInElementBox as a separate native box-volume predicate. Returns raw bool values with field-level unknown/errors and CE before/after checks. A false value is only the SDK result for the requested predicate; this does not issue a clash/clear verdict, compute minimum clearance, imply model-wide non-interference, or establish engineering suitability. E3D21 only; no PML, database writes, Drawlist, camera, or view operations.",
        {"type": "object", "properties": {
            "test_refno": {"type": "string", "description": "Explicit /NAME or =database/element Design element tested against the second reference."},
            "against_refno": {"type": "string", "description": "Explicit /NAME or =database/element Design element used as the comparison target."},
            "include_box": {"type": "boolean", "default": False,
                            "description": "Also read the separate native ElementInElementBox predicate; does not replace the exact result."},
        }, "required": ["test_refno", "against_refno"], "additionalProperties": False},
        lambda args: _geometry_relation(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_geometry_relation_batch",
        "Read 1 to 32 ordered pairs of explicit Design elements in one E3D21 UI callback using Spatial.ElementInElementExact(test, against, false), optionally followed per pair by Spatial.ElementInElementBox(test, against). Results retain pair order, raw booleans, field-level unknown/errors, pair completion and CE before/after consistency. A false value is only the requested native predicate result; this does not issue a clash/clear verdict, compute minimum clearance, imply model-wide non-interference, or establish engineering suitability. The operation is bounded and read-only: no PML, database writes, Drawlist, camera, or view operations.",
        {"type": "object", "properties": {
            "pairs": {"type": "array", "minItems": 1, "maxItems": 32, "items": {
                "type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "string"},
                "description": "One ordered [test_refno, against_refno] pair of explicit /NAME or =database/element Design references."
            }},
            "include_box": {"type": "boolean", "default": False,
                            "description": "Also read the separate native ElementInElementBox predicate for each pair."},
        }, "required": ["pairs"], "additionalProperties": False},
        lambda args: _geometry_relation_batch(bridge, args), read_only=True,
    )
    server.add_tool(
        "e3d_native_clash_check",
        "Native checking is temporarily suspended after an E3D21 CheckSpatialMap stack-overflow crash on 2026-11-16: native_spatial_map_check_suspended blocks both map checks and actual Clasher execution before any factory. Bounded input previews remain available. "
        "Experimental native E3D clash diagnostics for two explicit, disjoint sets of EQUI/SUBE groups containing BOX/CYLI/CONE positive geometry and NCYL/NBOX/NCON negative geometry. "
        "The native adapter bounds the entire inspected input to 256 nodes and depth 8; unsupported types, canonical aliases and overlapping subtrees are rejected. "
        "Direct BOX/CYLI/CONE roots remain eligible for preview only; the pre-suspension implementation rejected actual execution as unsupported_native_check_granularity pending their own geometry validation. The current suspension reason takes precedence. "
        "dry_run=true reads native references, hierarchy and Design DB identity without querying spatial-map status, creating Clasher objects or running the engine. "
        "The following actual-check rules describe the suspended implementation: dry_run=false, E3D_MCP_ALLOW_COMMANDS=true and UPTODATE or NOWARNINGS spatial maps were required for all input Design DBs before any Clasher factory; other statuses reject spatial_map_not_accepted. No MAP BUILD is performed automatically. "
        "Inspect input_spatial_maps_accepted, per-database accepted and spatial_map_admission_policy; input_spatial_maps_up_to_date and per-database up_to_date remain exact UPTODATE checks. "
        "The experimental NOWARNINGS policy follows a local E3D21 single-DB MAP CHECK with zero discrepancies while SDK status remained NOWARNINGS; neither acceptance nor that observation alone proves correct geometry classification. "
        "After input-database admission and explicit bounded scope validation, actual execution sets and verifies options.override=true. Read override_policy: this may bypass unrelated current-MDB map warnings, which remain in the receipt, without building maps. "
        "A local PML comparison returned error (69,85) with OVERRIDE OFF and one clash for the same EQUI pair with OVERRIDE ON. A subsequent isolated EQUI/BOX SDK positive fixture returned one HH clash with verified BOX endpoints; broader geometry_classification_verified remains false, including unvalidated CYLI/CONE, negative cuts and general units/frames. Prior Check=true with four empty arrays does not prove geometry was computed or establish a valid negative result. "
        "CheckSpatialMap resolves databases by Number only, without verified ExtractNumber targeting. Inputs with Db.IsExtract=true remain preview-only and actual checks reject unsupported_spatial_map_extract_scope even when their map status is accepted. "
        "Use the native is_extract flag, not extract_number!=0: a primary database may have extract_number=1. Inspect spatial_map_database_resolution and spatial_map_extract_targeting_verified. "
        "Execution changes native global diagnostic settings: obstruction and touch-overlap changes have been observed. Full diagnostic state is not automatically restored; this is not a read-only operation. "
        "Inspect input preflight/map findings, native_check_attempted, native_check_returned, native_check_result, complete/truncated/scope_complete and each category's native_count/returned_count/records/complete/read_errors. "
        "Returned endpoints may be verified descendants of the explicit groups; unknown endpoint attribution leaves scope incomplete. No whole-MDB geometry scope is implicitly selected. "
        "Preserve the native clashes, touches, clearances and not_proven classifications; not_proven remains unknown. "
        "no_clear_verdict=true: an empty, complete or successful result does not establish exact non-interference, minimum distance, overlap volume or engineering compatibility. "
        "clearance and result positions use unverified native units; position coordinate frames are also unverified. "
        "Each group accepts 1..8 unique explicit references; result_limit bounds serialized records across categories, not native computation or allocations. "
        "No automatic cancellation, replay, model edits, view changes or image capture are performed by this tool; after a transport failure native completion may be unknown.",
        {"type": "object", "properties": {
            "first_refnos": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8, "uniqueItems": True,
                             "description": "First explicit /NAME or =database/element set. Actual execution accepts bounded EQUI/SUBE groups; direct BOX/CYLI/CONE roots support preview only."},
            "second_refnos": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8, "uniqueItems": True,
                              "description": "Second explicit EQUI/SUBE group set; must be disjoint from first_refnos, including resolved aliases and validated descendants. Direct primitives are preview-only."},
            "clearance": {"type": "number", "minimum": 0, "maximum": 1000000, "default": 0,
                          "description": "Native clearance option; units remain unverified. This numerical input cap does not bound engine work."},
            "result_limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50,
                             "description": "Maximum serialized native records across all four categories; does not limit native computation."},
            "dry_run": {"type": "boolean", "default": True},
        }, "required": ["first_refnos", "second_refnos"], "additionalProperties": False},
        lambda args: _native_clash_check(bridge, settings, args),
    )
    server.add_tool(
        "e3d_geometry_volume",
        "Read actual native gross and net geometry volumes for one explicitly referenced element. scope=element reads this element (ONLY); scope=members includes this element and its members (ALL). Inspect read_complete, per-value availability, units and partial errors; unavailable geometry is never replaced with zero. For SDK base types NCYLINDER, NBOX and NCONE, volume_pair_applicable=false and volume_pair_coherent/removed_volume_mm3 remain null even when both native values were read successfully. Standalone negative NVOL is not verified owner removal; never sum part volumes into an equipment result. Other types retain the existing numerical pair check. Compare positive-target volumes before and after negative geometry, then inspect the native view: a volume reduction alone does not prove a through hole. Does not create geometry, select the current element, or change the view.",
        {"type": "object", "properties": {
            "refno": {"type": "string", "description": "Explicit /NAME or =database/element reference."},
            "scope": {"type": "string", "anyOf": [
                {"enum": ["element", "members"]}, {"pattern": r"^\s*(?i:element|members)\s*$"},
            ], "default": "element",
                      "description": "Read this element only, or include it and its members."},
        }, "required": ["refno"], "additionalProperties": False},
        lambda args: _geometry_volume(bridge, args), read_only=True,
    )
    return server


def _spatial_map_status(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    if set(args) - {"database_number", "extract_number", "expected_context"}:
        raise ValueError("Unknown e3d_spatial_map_status argument")
    parameters = spatial_map_status_parameters(args.get("database_number"), args.get("extract_number"),
                                               args.get("expected_context", ""))
    return bridge.spatial_map_status(**parameters)


def _geometry_bounds(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    refno = _string(args, "refno").strip()
    if not refno:
        raise ValueError("refno must not be empty")
    return bridge.geometry_bounds(refno)


def _validate_triangle_mesh(args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"vertices", "triangles"}
    if unknown:
        raise ValueError(f"Unknown e3d_validate_triangle_mesh arguments: {', '.join(sorted(unknown))}")
    return {"ok": True, "version": "offline", "result": validate_triangle_mesh(args.get("vertices"), args.get("triangles"))}


def _measure_triangle_mesh(args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"vertices", "triangles"}
    if unknown:
        raise ValueError(f"Unknown e3d_measure_triangle_mesh arguments: {', '.join(sorted(unknown))}")
    return {"ok": True, "version": "offline", "result": measure_triangle_mesh(args.get("vertices"), args.get("triangles"))}


def _geometry_relation(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"test_refno", "against_refno", "include_box"}
    if unknown:
        raise ValueError(f"Unknown e3d_geometry_relation arguments: {', '.join(sorted(unknown))}")
    test_refno = _panel_reference(args, "test_refno")
    against_refno = _panel_reference(args, "against_refno")
    include_box = args.get("include_box", False)
    if not isinstance(include_box, bool):
        raise ValueError("include_box must be a boolean")
    return bridge.geometry_relation(test_refno, against_refno, include_box)


def _geometry_relation_batch(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"pairs", "include_box"}
    if unknown:
        raise ValueError(f"Unknown e3d_geometry_relation_batch arguments: {', '.join(sorted(unknown))}")
    pairs = args.get("pairs")
    if not isinstance(pairs, list) or not 1 <= len(pairs) <= 32:
        raise ValueError("pairs must contain between 1 and 32 ordered pairs")
    normalized: list[tuple[str, str]] = []
    for index, pair in enumerate(pairs):
        if not isinstance(pair, list) or len(pair) != 2 or any(not isinstance(value, str) for value in pair):
            raise ValueError(f"pairs[{index}] must contain exactly two strings")
        normalized.append((_panel_reference_value(pair[0], f"pairs[{index}][0]"),
                           _panel_reference_value(pair[1], f"pairs[{index}][1]")))
    include_box = args.get("include_box", False)
    if not isinstance(include_box, bool):
        raise ValueError("include_box must be a boolean")
    return bridge.geometry_relation_batch(normalized, include_box)


def _native_clash_check(bridge: BridgeClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    if set(args) - {"first_refnos", "second_refnos", "clearance", "result_limit", "dry_run"}:
        raise ValueError("Unknown e3d_native_clash_check argument")
    parameters = native_clash_parameters(args.get("first_refnos"), args.get("second_refnos"),
        args.get("clearance", 0), args.get("result_limit", 50), args.get("dry_run", True))
    if not parameters["dry_run"] and not settings.allow_commands:
        raise PermissionError("Native clash execution is disabled; set E3D_MCP_ALLOW_COMMANDS=true explicitly")
    return bridge.native_clash_check(**parameters)


def _geometry_overlap_candidates(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    allowed = {"first_refnos", "second_refnos", "tolerance_mm"}
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"Unknown e3d_geometry_overlap_candidates arguments: {', '.join(sorted(unknown))}")

    def references(name: str) -> list[str]:
        values = args.get(name)
        if not isinstance(values, list) or not 1 <= len(values) <= 32:
            raise ValueError(f"{name} must contain 1 to 32 explicit element references")
        normalized = [_panel_reference({name: value}, name) for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError(f"{name} must not contain duplicate references")
        return normalized

    first_refnos = references("first_refnos")
    second_refnos = references("second_refnos")
    tolerance = args.get("tolerance_mm", 0)
    if (isinstance(tolerance, bool) or not isinstance(tolerance, (int, float))
            or (isinstance(tolerance, float) and not math.isfinite(tolerance))
            or tolerance < 0 or tolerance > 1000000):
        raise ValueError("tolerance_mm must be a finite number between 0 and 1000000 mm")
    tolerance = float(tolerance)

    bounds_by_ref: dict[str, dict[str, Any]] = {}
    errors: dict[str, Any] = {}
    unique_refnos = list(dict.fromkeys(first_refnos + second_refnos))
    bounds_read_count = 0

    def finite_coordinate(value: Any) -> bool:
        if type(value) not in (int, float):
            return False
        try:
            return math.isfinite(float(value))
        except (OverflowError, ValueError):
            return False

    for index, refno in enumerate(unique_refnos):
        try:
            bounds_read_count += 1
            response = bridge.geometry_bounds(refno)
        except BridgeError as exc:
            errors[refno] = {"error": str(exc), **exc.diagnostics}
            for remaining in unique_refnos[index + 1:]:
                errors[remaining] = {"error": "Bounds read was not attempted after a bridge read failure."}
            break
        result = response.get("result") if isinstance(response, dict) else None
        if not isinstance(result, dict):
            errors[refno] = {"error": "Geometry bounds response did not contain a result object."}
            continue
        min_value = result.get("min")
        max_value = result.get("max")
        valid_vectors = all(
            isinstance(vector, list) and len(vector) == 3 and
            all(finite_coordinate(component) for component in vector)
            for vector in (min_value, max_value)
        )
        valid = (result.get("success") is True and result.get("available") is True and
                 result.get("unit") == "mm" and
                 result.get("coordinate_frame") == "SDK native (unverified)" and valid_vectors)
        if valid:
            minimum = [float(component) for component in min_value]
            maximum = [float(component) for component in max_value]
            valid = all(minimum[axis] <= maximum[axis] for axis in range(3))
        if not valid:
            errors[refno] = {
                "success": result.get("success"),
                "available": result.get("available"),
                "coordinate_frame": result.get("coordinate_frame"),
                "errors": result.get("errors", {}),
                "error": "Bounds are unavailable or do not satisfy the finite SDK-native millimetre contract.",
            }
            continue
        bounds_by_ref[refno] = {
            "refno": result.get("refno", refno),
            "type": result.get("type"),
            "min": minimum,
            "max": maximum,
        }

    candidates: list[dict[str, Any]] = []
    unknown_pair_count = 0
    same_element_pair_count = 0
    screened_clear_pair_count = 0
    for first_refno in first_refnos:
        first = bounds_by_ref.get(first_refno)
        for second_refno in second_refnos:
            second = bounds_by_ref.get(second_refno)
            if first is None or second is None:
                unknown_pair_count += 1
                continue
            if first["refno"] == second["refno"]:
                same_element_pair_count += 1
                continue
            gaps = [max(first["min"][axis] - second["max"][axis],
                        second["min"][axis] - first["max"][axis], 0.0) for axis in range(3)]
            if all(gap <= tolerance for gap in gaps):
                candidates.append({
                    "first": first,
                    "second": second,
                    "axis_gaps_mm": gaps,
                    "relation": "overlap" if all(gap == 0 for gap in gaps) else "within_tolerance",
                })
            else:
                screened_clear_pair_count += 1

    complete = not errors
    return {
        "ok": True,
        "success": complete,
        "available": bool(bounds_by_ref),
        "complete": complete,
        "coordinate_frame": "SDK native (unverified)",
        "coordinate_comparability": "unverified",
        "consistency": "sequential live reads; concurrent model edits are not excluded",
        "unit": "mm",
        "method": "inclusive AABB gap screening using E3D SDK Spatial.LimitsBox",
        "tolerance_mm": tolerance,
        "first_count": len(first_refnos),
        "second_count": len(second_refnos),
        "bounds_read_count": bounds_read_count,
        "bounds_available_count": len(bounds_by_ref),
        "bounds_errors": errors,
        "comparison_pair_count": len(first_refnos) * len(second_refnos),
        "unknown_pair_count": unknown_pair_count,
        "same_element_pair_count": same_element_pair_count,
        "screened_clear_pair_count": screened_clear_pair_count,
        "candidate_count": len(candidates),
        "candidate_pairs": candidates,
        "limitations": [
            "Bounds are compared as returned; coordinate comparability is unverified.",
            "A candidate is not proof of exact geometric interference or engineering incompatibility.",
            "A pair without complete bounds is unknown and is not classified as clear.",
        ],
    }


def _geometry_volume(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"refno", "scope"}
    if unknown:
        raise ValueError(f"Unknown e3d_geometry_volume arguments: {', '.join(sorted(unknown))}")
    refno = _panel_reference(args, "refno")
    scope = _string(args, "scope", "element").strip().lower()
    if scope not in {"element", "members"}:
        raise ValueError("scope must be element or members")
    return bridge.geometry_volume(refno, scope)


def _edit_preview(settings: Settings, args: dict[str, Any]) -> bool:
    dry_run = args.get("dry_run", True)
    if not isinstance(dry_run, bool):
        raise ValueError("dry_run must be a boolean")
    if not dry_run and not settings.allow_commands:
        raise PermissionError("Model editing is disabled; set E3D_MCP_ALLOW_COMMANDS=true explicitly")
    return dry_run


def _vector(value: Any, name: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 3 or any(
        isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item)
        for item in value
    ):
        raise ValueError(f"{name} must contain three finite numbers")
    return [float(item) for item in value]


def _transform_args(args: dict[str, Any]) -> tuple:
    position = _vector(args["position"], "position") if "position" in args else None
    x_axis = y_axis = None
    if "orientation" in args:
        orientation = args["orientation"]
        if not isinstance(orientation, dict) or set(orientation) != {"x_axis", "y_axis"}:
            raise ValueError("orientation requires x_axis and y_axis")
        x_axis = _vector(orientation["x_axis"], "x_axis")
        y_axis = _vector(orientation["y_axis"], "y_axis")
        if any(abs(math.hypot(*axis) - 1.0) > 1e-6 for axis in (x_axis, y_axis)) or abs(sum(x*y for x,y in zip(x_axis, y_axis))) > 1e-6:
            raise ValueError("orientation axes must be unit length and orthogonal")
    wrt = _string(args, "wrt", "OWNER").strip()
    if not wrt:
        raise ValueError("wrt must not be empty")
    return position, x_axis, y_axis, wrt


def _create_element(bridge: BridgeClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    dry_run = _edit_preview(settings, args)
    parent = _string(args, "parent_refno").strip()
    element_type = _string(args, "element_type").strip().upper()
    if not parent or not element_type:
        raise ValueError("parent_refno and element_type must not be empty")
    attributes = args.get("attributes", {})
    if not isinstance(attributes, dict) or len(attributes) > 20 or any(
        not isinstance(key, str) or not key.strip() or not isinstance(value, str) for key, value in attributes.items()
    ):
        raise ValueError("attributes must be an object with at most 20 string values")
    normalized = {key.strip().upper(): value for key, value in attributes.items()}
    if len(normalized) != len(attributes):
        raise ValueError("Duplicate attribute names")
    options: dict[str, Any] = {}
    if "expected_revision" in args:
        revision = _string(args, "expected_revision")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", revision):
            raise ValueError("expected_revision must contain exactly 64 hexadecimal characters")
        options["expected_revision"] = revision.lower()
    elif not dry_run:
        raise ValueError("expected_revision from create preview is required for execution")
    return bridge.create_element(parent, element_type, _string(args, "name"), normalized,
                                 *_transform_args(args), dry_run, **options)


def _panel_reference(args: dict[str, Any], name: str) -> str:
    return _panel_reference_value(_string(args, name), name)


def _panel_reference_value(value: str, name: str) -> str:
    value = value.strip()
    if (not value or any(char.isspace() or ord(char) < 32 or 127 <= ord(char) < 160 for char in value)
            or not (value.startswith("/") and len(value) > 1 or re.fullmatch(r"=[0-9]+/[0-9]+", value))):
        raise ValueError(f"{name} must be an explicit /NAME or =database/element reference")
    return value


def _copy_element(bridge: BridgeClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    allowed = {"source_refno", "parent_refno", "name", "position", "orientation", "wrt", "expected_revision", "dry_run",
               "capture_after", "capture_width", "capture_height"}
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"Unknown e3d_copy_element arguments: {', '.join(sorted(unknown))}")
    dry_run = _edit_preview(settings, args)
    source = _panel_reference(args, "source_refno")
    parent = _panel_reference(args, "parent_refno")
    name = _string(args, "name")
    if len(name) > 50 or not re.fullmatch(r"/[^\s\x00-\x1f\x7f-\x9f]+", name):
        raise ValueError("name must be an explicit /NAME of at most 50 characters including slash without whitespace or control characters")
    position, x_axis, y_axis, wrt = _transform_args(args)
    if wrt.upper() in {"OWNER", "WORLD"}:
        wrt = wrt.upper()
    else:
        wrt = _panel_reference({"wrt": wrt}, "wrt")
    options: dict[str, Any] = {}
    if position is not None:
        options["position"] = position
    if x_axis is not None:
        options.update(x_axis=x_axis, y_axis=y_axis)
    if "expected_revision" in args:
        revision = _string(args, "expected_revision")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", revision):
            raise ValueError("expected_revision must contain exactly 64 hexadecimal characters")
        options["expected_revision"] = revision.lower()
    elif not dry_run:
        raise ValueError("expected_revision from copy preview is required for execution")
    return bridge.copy_element(source, parent, name, wrt=wrt, dry_run=dry_run, **options)


def _panel_vertices(vertices: Any) -> list[list[float]]:
    if not isinstance(vertices, list) or not 3 <= len(vertices) <= 256:
        raise ValueError("vertices must contain 3 to 256 local [x,y] pairs")
    normalized = []
    for vertex in vertices:
        if not isinstance(vertex, list) or len(vertex) != 2 or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) for value in vertex
        ):
            raise ValueError("vertices must contain local [x,y] pairs of finite numbers")
        try:
            pair = [float(value) for value in vertex]
        except (OverflowError, ValueError):
            raise ValueError("vertices must contain local [x,y] pairs of finite numbers") from None
        if not all(math.isfinite(value) for value in pair):
            raise ValueError("vertices must contain local [x,y] pairs of finite numbers")
        normalized.append(pair)
    return normalized


def _panel_thickness(thickness: Any) -> float:
    if isinstance(thickness, bool) or not isinstance(thickness, (int, float)):
        raise ValueError("thickness must be a positive finite number in millimetres")
    try:
        thickness = float(thickness)
    except (OverflowError, ValueError):
        raise ValueError("thickness must be a positive finite number in millimetres") from None
    if not math.isfinite(thickness) or thickness <= 0:
        raise ValueError("thickness must be a positive finite number in millimetres")
    return thickness


def _create_panel(bridge: BridgeClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    allowed = {"parent_refno", "vertices", "thickness", "justification", "name", "position", "orientation", "wrt", "dry_run",
               "capture_after", "capture_width", "capture_height"}
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"Unknown e3d_create_panel arguments: {', '.join(sorted(unknown))}")
    dry_run = _edit_preview(settings, args)
    parent = _panel_reference(args, "parent_refno")
    normalized = _panel_vertices(args.get("vertices"))
    thickness = _panel_thickness(args.get("thickness"))
    justification = _string(args, "justification", "positive").strip().lower()
    if justification not in {"positive", "centre", "negative"}:
        raise ValueError("justification must be positive, centre, or negative")
    defaults = {"position": [0, 0, 0], "orientation": {"x_axis": [1, 0, 0], "y_axis": [0, 1, 0]}, **args}
    position, x_axis, y_axis, wrt = _transform_args(defaults)
    if wrt.upper() in {"OWNER", "WORLD"}:
        wrt = wrt.upper()
    else:
        wrt = _panel_reference({"wrt": wrt}, "wrt")
    return bridge.create_panel(parent, normalized, thickness, _string(args, "name"), position, x_axis, y_axis,
                               wrt, justification, dry_run)


def _panel_geometry(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"refno", "include_world", "include_boundary"}
    if unknown:
        raise ValueError(f"Unknown e3d_panel_geometry arguments: {', '.join(sorted(unknown))}")
    include_world = args.get("include_world", False)
    if not isinstance(include_world, bool):
        raise ValueError("include_world must be a boolean")
    include_boundary = args.get("include_boundary", False)
    if not isinstance(include_boundary, bool):
        raise ValueError("include_boundary must be a boolean")
    refno = _panel_reference(args, "refno")
    if include_boundary:
        options = {"include_boundary": True}
        if include_world:
            options["include_world"] = True
        return bridge.panel_geometry(refno, **options)
    if include_world:
        return bridge.panel_geometry(refno, include_world=True)
    return bridge.panel_geometry(refno)


def _panel_loop_relations(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"refno"}
    if unknown:
        raise ValueError(f"Unknown e3d_panel_loop_relations arguments: {', '.join(sorted(unknown))}")
    refno = _panel_reference(args, "refno")
    response = bridge.panel_geometry(refno)
    if not isinstance(response, dict) or not isinstance(response.get("result"), dict):
        return response
    geometry = response["result"]
    return {
        **response,
        "result": {
            **geometry,
            "loop_relationships": analyze_panel_loop_relations(geometry),
        },
    }


def _panel_mesh(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"refno", "coordinate_space"}
    if unknown:
        raise ValueError(f"Unknown e3d_panel_mesh arguments: {', '.join(sorted(unknown))}")
    refno = _panel_reference(args, "refno")
    coordinate_space = args.get("coordinate_space", "panel_local")
    if not isinstance(coordinate_space, str) or coordinate_space not in {"panel_local", "WORLD"}:
        raise ValueError("coordinate_space must be panel_local or WORLD")
    response = bridge.panel_geometry(refno, include_world=coordinate_space == "WORLD")
    if not isinstance(response, dict) or response.get("ok") is not True or not isinstance(response.get("result"), dict):
        return response
    geometry = response["result"]
    mesh = derive_panel_mesh(geometry, coordinate_space=coordinate_space)
    return {**response, "result": _mesh_result(geometry, mesh)}


def _project_panel_boundary_drawing(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any] | ImageToolResult:
    allowed = {"refno", "coordinate_space", "views", "include_svg", "include_dxf", "preview_width", "preview_height"}
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"Unknown e3d_project_panel_boundary_drawing arguments: {', '.join(sorted(unknown))}")
    refno = _panel_reference(args, "refno")
    coordinate_space = args.get("coordinate_space", "WORLD")
    if not isinstance(coordinate_space, str) or coordinate_space not in {"WORLD", "panel_local"}:
        raise ValueError("coordinate_space must be WORLD or panel_local")
    views = args.get("views", ["FRONT", "TOP", "SIDE"])
    if not isinstance(views, list) or not 1 <= len(views) <= 3 or len(set(views)) != len(views) or any(view not in {"FRONT", "TOP", "SIDE"} for view in views):
        raise ValueError("views must contain one to three unique FRONT, TOP or SIDE values")
    include_svg = args.get("include_svg", False)
    include_dxf = args.get("include_dxf", False)
    if not isinstance(include_svg, bool) or not isinstance(include_dxf, bool):
        raise ValueError("include_svg and include_dxf must be booleans")
    width = _integer(args, "preview_width", 1200, 64, 2048)
    height = _integer(args, "preview_height", 440, 64, 2048)
    if width * height > 2_000_000:
        raise ValueError("preview_width * preview_height must not exceed 2000000 pixels")
    response = bridge.panel_geometry(refno, include_world=coordinate_space == "WORLD")
    if not isinstance(response, dict) or response.get("ok") is not True:
        return response
    native = response.get("result")
    if not isinstance(native, dict):
        return {**response, "ok": False, "result": {"success": False, "available": False, "complete": False,
                                                        "error_code": "invalid_panel_geometry_result", "drawing": None}}
    identity = native.get("element") if isinstance(native.get("element"), dict) else {}
    resolved_type = identity.get("type")
    if resolved_type not in {"PANE", "PANEL"}:
        return {**response, "ok": False, "result": {"success": False, "available": False, "complete": False,
                                                        "error_code": "element_type_mismatch", "resolved_type": resolved_type,
                                                        "drawing": None}}
    resolved_refno = identity.get("refno")
    if not isinstance(resolved_refno, str):
        return {**response, "ok": False, "result": {"success": False, "available": False, "complete": False,
                                                        "error_code": "resolved_reference_unavailable",
                                                        "requested_refno": refno, "resolved_refno": resolved_refno,
                                                        "drawing": None}}
    if refno.startswith("=") and _drawing_reference_key(resolved_refno) != _drawing_reference_key(refno):
        return {**response, "ok": False, "result": {"success": False, "available": False, "complete": False,
                                                        "error_code": "element_reference_mismatch",
                                                        "requested_refno": refno, "resolved_refno": resolved_refno,
                                                        "drawing": None}}
    resolved_name = identity.get("name")
    if refno.startswith("/") and (
            not isinstance(resolved_name, str) or
            ("/" + resolved_name.lstrip("/")).casefold() != refno.casefold()):
        return {**response, "ok": False, "result": {"success": False, "available": False, "complete": False,
                                                        "error_code": "element_name_mismatch",
                                                        "requested_refno": refno, "resolved_refno": resolved_refno,
                                                        "resolved_name": resolved_name, "drawing": None}}
    base = {"requested_refno": refno, "resolved_refno": identity.get("refno", refno),
            "resolved_type": "PANE", "coordinate_space": coordinate_space,
            "success": False, "available": False, "complete": False, "drawing": None}
    try:
        drawing = build_panel_boundary_drawing(native, coordinate_space=coordinate_space, views=views,
                                               include_svg=include_svg, include_dxf=include_dxf)
        png = render_panel_boundary_png(drawing, width=width, height=height)
    except ValueError as exc:
        return {**response, "ok": False, "result": {**base, "error_code": str(exc),
                                                        "native_geometry": {"complete": native.get("complete"),
                                                                             "world_positions_complete": native.get("world_positions_complete"),
                                                                             "world_read_errors": native.get("world_read_errors", {})}}}
    output = {**base, "success": True, "available": True, "complete": True,
              "drawing": drawing, "native_geometry": {
                  "complete": native.get("complete"), "world_positions_complete": native.get("world_positions_complete"),
                  "world_position_count": native.get("world_position_count"),
                  "world_position_vertex_count": native.get("world_position_vertex_count"),
                  "world_position_frame": native.get("world_position_frame"),
                  "world_read_errors": native.get("world_read_errors", {}),
                  "native_geometry_read_errors": native.get("read_errors", {}),
                  "loop_topology": [{"loop_index": loop.get("loop_index"),
                                     "polygon_valid": loop.get("polygon_valid"),
                                     "role": loop.get("role"),
                                     "native_features": loop.get("unsupported_features", [])}
                                    for loop in native.get("loops", []) if isinstance(loop, dict)],
              }}
    return ImageToolResult(data=base64.b64encode(png).decode("ascii"), mime_type="image/png",
                           metadata={**response, "result": output})


def _panel_curve_boundary(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    return _panel_curve_operation(bridge, args, drawing_requested=False)


def _project_panel_curve_drawing(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any] | ImageToolResult:
    return _panel_curve_operation(bridge, args, drawing_requested=True)


def _panel_curve_operation(bridge: BridgeClient, args: dict[str, Any], *,
                           drawing_requested: bool) -> dict[str, Any] | ImageToolResult:
    allowed = {"refno", "coordinate_space", "chord_tolerance_mm"}
    if drawing_requested:
        allowed |= {"views", "include_svg", "include_dxf", "preview_width", "preview_height"}
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"Unknown panel-curve arguments: {', '.join(sorted(unknown))}")
    refno = _panel_reference(args, "refno")
    coordinate_space = args.get("coordinate_space", "WORLD" if drawing_requested else "panel_local")
    if not isinstance(coordinate_space, str) or coordinate_space not in {"panel_local", "WORLD"}:
        raise ValueError("coordinate_space must be panel_local or WORLD")
    tolerance = args.get("chord_tolerance_mm", 0.1)
    if (isinstance(tolerance, bool) or not isinstance(tolerance, (int, float))
            or not 0.000001 <= tolerance <= 1000 or not math.isfinite(tolerance)):
        raise ValueError("chord_tolerance_mm must be finite and between 0.000001 and 1000 mm")
    views = args.get("views", ["FRONT", "TOP", "SIDE"])
    include_svg = args.get("include_svg", False)
    include_dxf = args.get("include_dxf", False)
    width = height = 0
    if drawing_requested:
        if (not isinstance(views, list) or not 1 <= len(views) <= 3
                or any(not isinstance(view, str) or view not in {"FRONT", "TOP", "SIDE"} for view in views)
                or len(set(views)) != len(views)):
            raise ValueError("views must contain one to three unique FRONT, TOP or SIDE values")
        if not isinstance(include_svg, bool) or not isinstance(include_dxf, bool):
            raise ValueError("include_svg and include_dxf must be booleans")
        width = _integer(args, "preview_width", 1200, 64, 2048)
        height = _integer(args, "preview_height", 440, 64, 2048)
        if width * height > 2_000_000:
            raise ValueError("preview_width * preview_height must not exceed 2000000 pixels")
    base: dict[str, Any] = {"success": False, "available": False, "complete": False,
                            "requested_refno": refno, "coordinate_space": coordinate_space,
                            "coordinate_unit": "mm", "curve_boundary": None,
                            "atomic_snapshot": False}
    if drawing_requested:
        base["drawing"] = None
    try:
        native, evidence = read_panel_curve_source(
            bridge, refno, coordinate_space=coordinate_space,
            context_reader=lambda: _assembly_boundary_context(bridge),
        )
    except PanelCurveReadError as exc:
        return {"ok": False, "result": {**base, "error_code": exc.code, "source_evidence": exc.evidence}}
    from .panel_curve_boundary import derive_panel_curve_boundary
    boundary = derive_panel_curve_boundary(native, coordinate_space=coordinate_space,
                                           chord_tolerance_mm=float(tolerance))
    base.update(resolved_refno=evidence["resolved_refno"], source_evidence=evidence,
                curve_boundary=boundary)
    version = evidence["context"]["before"].get("version")
    if boundary.get("success") is not True or boundary.get("available") is not True or boundary.get("complete") is not True:
        return {"ok": False, "version": version, "result": {**base, "error_code": "panel_curve_derivation_failed"}}
    output = {**base, "success": True, "available": True, "complete": True,
              "success_scope": "validated projected native definition-plane curves; native existence, rendered surfaces and engineering validity remain independent"}
    if not drawing_requested:
        return {"ok": True, "version": version, "result": output}
    from .panel_curve_drawing import build_panel_curve_drawing, render_panel_curve_png
    try:
        drawing = build_panel_curve_drawing(boundary, views=views, include_svg=include_svg, include_dxf=include_dxf)
        png = render_panel_curve_png(drawing, width=width, height=height)
    except ValueError as exc:
        return {"ok": False, "version": version,
                "result": {**base, "error_code": str(exc)}}
    output["drawing"] = drawing
    return ImageToolResult(data=base64.b64encode(png).decode("ascii"), mime_type="image/png",
                           metadata={"ok": True, "version": version, "result": output})


def _project_panel_curve_assembly_drawing(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any] | ImageToolResult | MultiImageToolResult:
    allowed = {"targets", "panels_per_page", "chord_tolerance_mm", "views", "include_svg", "include_dxf",
               "preview_width", "preview_height"}
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"Unknown e3d_project_panel_curve_assembly_drawing arguments: {', '.join(sorted(unknown))}")
    raw_targets = args.get("targets")
    if not isinstance(raw_targets, list) or not 1 <= len(raw_targets) <= 128:
        raise ValueError("targets must contain one to 128 explicit PANE references")
    targets = [_panel_reference_value(target, f"targets[{index}]") for index, target in enumerate(raw_targets)]
    keys = [_drawing_reference_key(target) for target in targets]
    if len(set(keys)) != len(keys):
        raise ValueError("targets must not contain duplicate references")
    per_page = args.get("panels_per_page", 16)
    if type(per_page) is not int or per_page not in {16, 32, 64, 128}:
        raise ValueError("panels_per_page must be 16, 32, 64 or 128")
    tolerance = args.get("chord_tolerance_mm", 0.1)
    try:
        valid_tolerance = (not isinstance(tolerance, bool) and isinstance(tolerance, (int, float))
                           and math.isfinite(tolerance) and 0.000001 <= tolerance <= 1000)
    except (OverflowError, ValueError):
        valid_tolerance = False
    if not valid_tolerance:
        raise ValueError("chord_tolerance_mm must be finite and between 0.000001 and 1000")
    views = args.get("views", ["FRONT", "TOP", "SIDE"])
    if (not isinstance(views, list) or not 1 <= len(views) <= 3
            or any(not isinstance(view, str) or view not in {"FRONT", "TOP", "SIDE"} for view in views)
            or len(set(views)) != len(views)):
        raise ValueError("views must contain one to three unique FRONT, TOP or SIDE values")
    include_svg, include_dxf = args.get("include_svg", False), args.get("include_dxf", False)
    if type(include_svg) is not bool or type(include_dxf) is not bool:
        raise ValueError("include_svg and include_dxf must be booleans")
    width = _integer(args, "preview_width", 1200, 64, 2048)
    height = _integer(args, "preview_height", 600, 64, 2048)
    page_count = (len(targets) + per_page - 1) // per_page
    if width * height > 2_000_000 or width * height * page_count > 8_000_000:
        raise ValueError("each preview may use at most 2000000 pixels; all pages together at most 8000000")

    from .panel_curve_assembly_workflow import read_panel_curve_assembly
    observed = read_panel_curve_assembly(
        bridge, targets, chord_tolerance_mm=float(tolerance),
        context_reader=lambda: _assembly_boundary_context(bridge))
    version = observed.get("context", {}).get("before", {}).get("version")
    result = {key: value for key, value in observed.items() if key != "boundaries"}
    result["pages"] = []
    if any(observed.get(flag) is not True for flag in ("success", "available", "complete")):
        return {"ok": False, "version": version, "result": result}
    from .panel_curve_assembly import compose_panel_curve_pages
    from .panel_curve_drawing import render_panel_curve_png
    try:
        composed = compose_panel_curve_pages(
            observed["boundaries"], panels_per_page=per_page, views=views,
            include_svg=include_svg, include_dxf=include_dxf)
        if any(composed.get(flag) is not True for flag in ("success", "available", "complete")):
            raise ValueError("curve_assembly_composition_incomplete")
        pages = composed["pages"]
        payloads = []
        for page in pages:
            png = render_panel_curve_png(page["drawing"], width=width, height=height)
            payloads.append((f"Page {page['page_number']}: PANE definition curves",
                             base64.b64encode(png).decode("ascii"), "image/png"))
    except ValueError as exc:
        result.update(success=False, available=False, complete=False,
                      error_code="panel_curve_assembly_drawing_failed", drawing_error=str(exc))
        return {"ok": False, "version": version, "result": result}
    result.update({key: value for key, value in composed.items() if key not in {"sources", "requested_targets"}})
    result["panels_per_page"] = per_page
    result["limits"] = {"read": observed.get("limits", {}), "drawing": composed.get("limits", {})}
    if len(pages) == 1:
        result["drawing"] = pages[0]["drawing"]
    metadata = {"ok": True, "version": version, "result": result}
    json_bytes = len(json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    image_bytes = sum(len(payload[1]) for payload in payloads)
    if json_bytes > 8 * 1024 * 1024 or image_bytes > 16 * 1024 * 1024:
        compact = [{key: source[key] for key in (
            "source_index", "requested_refno", "resolved_refno", "read_status", "success", "available", "complete",
            "error_code", "curve_summary") if key in source} for source in result["sources"]]
        return {"ok": False, "version": version, "result": {
            "success": False, "available": False, "complete": False,
            "error_code": "panel_curve_assembly_output_limit_exceeded", "sources": compact,
            "requested_targets": targets, "context": result["context"], "atomic_snapshot": False,
            "observed_output_bytes": {"json": json_bytes, "base64_images": image_bytes},
            "pages": [], "output_omitted": "Drawing pages, raw source evidence and images omitted; source summaries retained."}}
    if len(payloads) == 1:
        return ImageToolResult(data=payloads[0][1], mime_type="image/png", metadata=metadata)
    return MultiImageToolResult(payloads, metadata)


def _project_panel_assembly_boundary_drawing(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any] | ImageToolResult:
    allowed = {"targets", "panels_per_page", "views", "include_svg", "include_dxf", "preview_width", "preview_height"}
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"Unknown e3d_project_panel_assembly_boundary_drawing arguments: {', '.join(sorted(unknown))}")
    raw_targets = args.get("targets")
    if not isinstance(raw_targets, list) or not 1 <= len(raw_targets) <= 128:
        raise ValueError("targets must contain one to 128 explicit PANE references")
    targets = [_panel_reference_value(value, f"targets[{index}]") for index, value in enumerate(raw_targets)]
    requested_keys = [_drawing_reference_key(target) for target in targets]
    if len(set(requested_keys)) != len(requested_keys):
        raise ValueError("targets must not contain duplicate references")
    panels_per_page = args.get("panels_per_page", 16)
    if type(panels_per_page) is not int or panels_per_page not in {16, 32, 64, 128}:
        raise ValueError("panels_per_page must be 16, 32, 64 or 128")
    page_count = (len(targets) + panels_per_page - 1) // panels_per_page
    views = args.get("views", ["FRONT", "TOP", "SIDE"])
    if not isinstance(views, list) or not 1 <= len(views) <= 3 or any(
            not isinstance(view, str) or view not in {"FRONT", "TOP", "SIDE"} for view in views) or len(set(views)) != len(views):
        raise ValueError("views must contain one to three unique FRONT, TOP or SIDE values")
    include_svg = args.get("include_svg", False)
    include_dxf = args.get("include_dxf", False)
    if not isinstance(include_svg, bool) or not isinstance(include_dxf, bool):
        raise ValueError("include_svg and include_dxf must be booleans")
    width = _integer(args, "preview_width", 1200, 64, 2048)
    height = _integer(args, "preview_height", 600, 64, 2048)
    if width * height > 2_000_000:
        raise ValueError("preview_width * preview_height must not exceed 2000000 pixels")
    if width * height * page_count > 8_000_000:
        raise ValueError("all panel-boundary page previews together may not exceed 8000000 pixels")

    before = _assembly_boundary_context(bridge)
    sources: list[dict[str, Any]] = [
        {"source_index": index, "requested_refno": target, "read_status": "not_read",
         "success": False, "complete": False} for index, target in enumerate(targets)]
    drawings: list[dict[str, Any]] = []
    total_vertices = total_segments = 0
    world_position_frame: str | None = None
    resolved_keys: set[str] = set()
    if before.get("available") is not True:
        return {"ok": False, "version": before.get("version"), "result": {
            "success": False, "available": False, "complete": False, "coordinate_space": "WORLD",
            "sources": sources, "requested_targets": targets,
            "context": {"before": before, "after": None, "boundary_agreement": None},
            "atomic_snapshot": False, "error_code": "assembly_panel_context_unavailable"}}
    for index, target in enumerate(targets):
        try:
            response = bridge.panel_geometry(target, include_world=True)
        except Exception as exc:
            sources[index] = {"source_index": index, "requested_refno": target, "read_status": "failed",
                              "success": False, "complete": False,
                              "error_code": "native_panel_read_failed", "error_type": type(exc).__name__,
                              "read_error": str(exc)[:1024],
                              **({"native_diagnostics": exc.diagnostics} if isinstance(exc, BridgeError) else {})}
            output = {"success": False, "available": False, "complete": False,
                      "coordinate_space": "WORLD", "coordinate_unit": "mm",
                      "sources": sources, "requested_targets": targets,
                      "context": {"before": before, "after": None, "boundary_agreement": None},
                      "atomic_snapshot": False, "error_code": "assembly_panel_read_failed"}
            return {"ok": False, "version": before.get("version"), "result": output}
        if not isinstance(response, dict) or response.get("ok") is not True or not isinstance(response.get("result"), dict):
            sources[index] = {"source_index": index, "requested_refno": target, "read_status": "failed",
                              "success": False, "complete": False, "error_code": "invalid_panel_response"}
            return {"ok": False, "version": before.get("version"), "result": {
                "success": False, "available": False, "complete": False, "coordinate_space": "WORLD",
                "sources": sources, "requested_targets": targets,
                "context": {"before": before, "after": None, "boundary_agreement": None},
                "atomic_snapshot": False, "error_code": "assembly_panel_read_failed"}}
        native = response["result"]
        identity = native.get("element") if isinstance(native.get("element"), dict) else {}
        resolved_refno = identity.get("refno")
        row = {"source_index": index, "requested_refno": target, "resolved_refno": resolved_refno,
               "resolved_type": "PANE" if identity.get("type") in {"PANE", "PANEL"} else identity.get("type"),
               "native_success": native.get("success"), "complete": native.get("complete"),
               "world_positions_complete": native.get("world_positions_complete"),
               "world_position_count": native.get("world_position_count"),
               "world_position_vertex_count": native.get("world_position_vertex_count"),
               "world_position_frame": native.get("world_position_frame"),
               "revision": native.get("revision"), "topology_valid": native.get("topology_valid"),
               "read_errors": native.get("read_errors", {}), "world_read_errors": native.get("world_read_errors", {}),
               "loop_count": native.get("loop_count"), "vertex_count": native.get("vertex_count"),
               "loops": [{"loop_index": loop.get("loop_index"), "vertex_count": loop.get("vertex_count"),
                          "polygon_valid": loop.get("polygon_valid"), "role": loop.get("role"),
                          "native_features": loop.get("unsupported_features", [])}
                         for loop in native.get("loops", []) if isinstance(loop, dict)]}
        row["read_status"] = "returned"
        sources[index] = row
        if (response.get("version") != before.get("version") or identity.get("type") not in {"PANE", "PANEL"} or
                not isinstance(resolved_refno, str) or
                (target.startswith("=") and _drawing_reference_key(resolved_refno) != _drawing_reference_key(target)) or
                (target.startswith("/") and (not isinstance(identity.get("name"), str) or
                 ("/" + identity["name"].lstrip("/")).casefold() != target.casefold()))):
            row.update(success=False, error_code="element_identity_mismatch")
            return {"ok": False, "version": before.get("version"), "result": {
                "success": False, "available": False, "complete": False, "coordinate_space": "WORLD",
                "sources": sources, "requested_targets": targets,
                "context": {"before": before, "after": None, "boundary_agreement": None},
                "atomic_snapshot": False, "error_code": "assembly_panel_identity_mismatch"}}
        resolved_key = _drawing_reference_key(resolved_refno)
        if resolved_key in resolved_keys:
            row.update(success=False, error_code="duplicate_resolved_identity")
            return {"ok": False, "version": before.get("version"), "result": {
                "success": False, "available": False, "complete": False, "coordinate_space": "WORLD",
                "sources": sources, "requested_targets": targets,
                "context": {"before": before, "after": None, "boundary_agreement": None},
                "atomic_snapshot": False, "error_code": "duplicate_resolved_identity"}}
        resolved_keys.add(resolved_key)
        try:
            drawing = build_panel_boundary_drawing(native, coordinate_space="WORLD", views=views)
        except ValueError as exc:
            row.update(success=False, error_code=str(exc))
            return {"ok": False, "version": before.get("version"), "result": {
                "success": False, "available": False, "complete": False, "coordinate_space": "WORLD",
                "sources": sources, "requested_targets": targets,
                "context": {"before": before, "after": None, "boundary_agreement": None},
                "atomic_snapshot": False, "error_code": "assembly_panel_geometry_incomplete"}}
        native_frame = native["world_position_frame"]
        if world_position_frame is not None and native_frame != world_position_frame:
            row.update(success=False, error_code="world_position_frame_mismatch")
            return {"ok": False, "version": before.get("version"), "result": {
                "success": False, "available": False, "complete": False, "coordinate_space": "WORLD",
                "world_position_frame": world_position_frame,
                "sources": sources, "requested_targets": targets,
                "context": {"before": before, "after": None, "boundary_agreement": None},
                "atomic_snapshot": False, "error_code": "assembly_panel_world_frame_mismatch"}}
        world_position_frame = native_frame
        source = drawing.get("source", {})
        total_vertices += source.get("vertex_count", 0)
        total_segments += drawing.get("segment_count", 0)
        if total_vertices > 2048 or total_segments > 12000:
            row.update(success=False, error_code="panel_boundary_assembly_limit_exceeded")
            return {"ok": False, "version": before.get("version"), "result": {
                "success": False, "available": False, "complete": False, "coordinate_space": "WORLD",
                "sources": sources, "requested_targets": targets,
                "observed_counts": {"panels": index + 1, "vertices": total_vertices, "projected_segments": total_segments},
                "context": {"before": before, "after": None, "boundary_agreement": None},
                "atomic_snapshot": False, "error_code": "panel_boundary_assembly_limit_exceeded"}}
        row.update(success=True, available=True, complete=True)
        drawings.append(drawing)

    after = _assembly_boundary_context(bridge)
    agreement = (before.get("available") is True and after.get("available") is True and
                 all(before.get(key) == after.get(key) for key in ("session_id", "version", "project")))
    context = {"before": before, "after": after, "boundary_agreement": agreement}
    if not agreement:
        return {"ok": False, "version": before.get("version"), "result": {
            "success": False, "available": False, "complete": False, "coordinate_space": "WORLD",
            "sources": sources, "requested_targets": targets, "context": context, "atomic_snapshot": False,
            "error_code": "assembly_panel_context_changed"}}
    pages: list[dict[str, Any]] = []
    image_payloads: list[tuple[str, str, str]] = []
    for offset in range(0, len(drawings), panels_per_page):
        end = min(offset + panels_per_page, len(drawings))
        page_number = len(pages) + 1
        try:
            drawing = compose_panel_boundary_page(
                drawings[offset:end], coordinate_space="WORLD", include_svg=include_svg,
                include_dxf=include_dxf, source_indices=range(offset, end))
            png = render_panel_boundary_png(drawing, width=width, height=height)
        except ValueError as exc:
            return {"ok": False, "version": before.get("version"), "result": {
                "success": False, "available": False, "complete": False, "coordinate_space": "WORLD",
                "sources": sources, "requested_targets": targets, "pages": pages, "context": context,
                "atomic_snapshot": False, "error_code": "panel_boundary_drawing_failed",
                "failed_page": page_number, "drawing_error": str(exc)}}
        page_sources = sources[offset:end]
        page = {"page_number": page_number, "source_range": [offset, end],
                "requested_targets": targets[offset:end], "sources": page_sources,
                "success": True, "complete": True, "drawing": drawing}
        pages.append(page)
        label = f"Page {page_number}: PANE boundaries {offset + 1}-{end}"
        image_payloads.append((label, base64.b64encode(png).decode("ascii"), "image/png"))
    result = {"success": True, "available": True, "complete": True,
              "coordinate_space": "WORLD", "coordinate_unit": "mm",
              "world_position_frame": world_position_frame,
              "requested_targets": targets, "sources": sources,
              "page_count": len(pages), "completed_page_count": len(pages),
              "panels_per_page": panels_per_page,
              "observed_counts": {"panels": len(sources), "vertices": total_vertices,
                                  "projected_segments": total_segments},
              "pages": pages, "context": context, "atomic_snapshot": False,
              "read_consistency": "Sequential native PANE reads bracketed by session/project/MDB observations; no atomic database snapshot."}
    if len(pages) == 1:
        result["drawing"] = pages[0]["drawing"]
        return ImageToolResult(data=image_payloads[0][1], mime_type="image/png",
                               metadata={"ok": True, "version": before.get("version"), "result": result})
    metadata = {"ok": True, "version": before.get("version"), "result": result}
    encoded_total = sum(len(image[1]) for image in image_payloads)
    if len(json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 8 * 1024 * 1024 or encoded_total > 16 * 1024 * 1024:
        return {"ok": False, "version": before.get("version"), "result": {
            "success": False, "available": False, "complete": False, "coordinate_space": "WORLD",
            "requested_targets": targets,
            "sources": [{key: item[key] for key in ("source_index", "requested_refno", "resolved_refno",
                         "resolved_type", "native_success", "complete", "world_positions_complete",
                         "world_position_count", "world_position_vertex_count", "world_position_frame",
                         "revision", "topology_valid",
                         "read_errors", "world_read_errors", "loop_count", "vertex_count",
                         "success", "error_code") if key in item} for item in sources],
            "page_count": len(pages), "completed_page_count": len(pages),
            "context": context, "atomic_snapshot": False,
            "error_code": "panel_boundary_output_limit_exceeded",
            "output_omitted": "Drawing pages and image blocks omitted; per-source read summaries retained."}}
    return MultiImageToolResult(image_payloads, metadata)


def _update_panel(bridge: BridgeClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    allowed = {"refno", "vertices", "thickness", "justification", "expected_revision", "allow_vertex_deletion", "dry_run",
               "capture_after", "capture_width", "capture_height"}
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"Unknown e3d_update_panel arguments: {', '.join(sorted(unknown))}")
    dry_run = _edit_preview(settings, args)
    refno = _panel_reference(args, "refno")
    vertices = _panel_vertices(args.get("vertices"))
    options: dict[str, Any] = {}
    if "thickness" in args:
        options["thickness"] = _panel_thickness(args["thickness"])
    if "justification" in args:
        justification = _string(args, "justification").strip().lower()
        if justification not in {"positive", "centre", "negative"}:
            raise ValueError("justification must be positive, centre, or negative")
        options["justification"] = justification
    if "expected_revision" in args:
        revision = _string(args, "expected_revision")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", revision):
            raise ValueError("expected_revision must contain exactly 64 hexadecimal characters")
        options["expected_revision"] = revision.lower()
    elif not dry_run:
        raise ValueError("expected_revision from native inspection or preview is required for execution")
    allow_vertex_deletion = args.get("allow_vertex_deletion", False)
    if not isinstance(allow_vertex_deletion, bool):
        raise ValueError("allow_vertex_deletion must be a boolean")
    return bridge.update_panel(refno, vertices, allow_vertex_deletion=allow_vertex_deletion, dry_run=dry_run, **options)


_PRIMITIVE_CSG_MAX_RESULT_BYTES = 8 * 1024 * 1024


def _primitive_csg_safe_evidence(value: Any, depth: int = 0) -> Any:
    """Retain failed native observations with explicit non-JSON value markers."""
    if depth > 32:
        return {"diagnostic_omitted": "nesting limit"}
    if value is None or type(value) is bool:
        return value
    if type(value) in {int, float}:
        try:
            if math.isfinite(value):
                return value
        except OverflowError:
            return {"non_json_number": "outside floating-point range"}
        return {"non_json_number": "NaN" if math.isnan(value) else "+Infinity" if value > 0 else "-Infinity"}
    if isinstance(value, str):
        try:
            value.encode("utf-8", errors="strict")
            return value
        except UnicodeError:
            return {"non_json_string": value.encode("unicode_escape").decode("ascii")}
    if isinstance(value, dict):
        return {str(key): _primitive_csg_safe_evidence(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_primitive_csg_safe_evidence(item, depth + 1) for item in value]
    return {"non_json_type": type(value).__name__}


def _primitive_csg_operation(bridge: BridgeClient, args: dict[str, Any], *,
                             drawing_requested: bool) -> dict[str, Any] | ImageToolResult:
    allowed = {"refno", "radial_segments", "include_native_volume", "chord_tolerance_mm"}
    if drawing_requested:
        allowed |= {"views", "feature_angle_degrees", "hidden_line_removal", "show_hidden_lines",
                    "include_svg", "include_dxf", "preview_width", "preview_height"}
    else:
        allowed.add("mesh_detail")
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"Unknown primitive CSG arguments: {', '.join(sorted(unknown))}")
    refno = _panel_reference(args, "refno")
    radial_segments = _integer(args, "radial_segments", 48, 12, 99)
    chord_tolerance = _chord_tolerance(args)
    if drawing_requested and chord_tolerance is None:
        chord_tolerance = _CSG_DRAWING_DEFAULT_CHORD_TOLERANCE_MM
    include_native_volume = args.get("include_native_volume", False)
    if type(include_native_volume) is not bool:
        raise ValueError("include_native_volume must be a boolean")
    detail = args.get("mesh_detail", "full")
    if not isinstance(detail, str) or detail not in {"full", "summary"}:
        raise ValueError("mesh_detail must be full or summary")
    options = _drawing_options(args) if drawing_requested else None
    from .primitive_csg_workflow import PrimitiveCsgReadError, compare_primitive_csg_volumes, read_primitive_csg_source
    from .primitive_csg_mesh import derive_primitive_csg_mesh
    base = {"success": False, "available": False, "complete": False,
            "requested_refno": refno, "coordinate_space": "WORLD", "coordinate_unit": "mm",
            "atomic_snapshot": False, "native_tessellation": False, "native_cut_application_verified": False,
            "derived_mesh": None, "drawing": None}
    version = None

    def finish(result: dict[str, Any], png: bytes | None = None):
        metadata = {"ok": result.get("success") is True, "version": version, "result": result}
        try:
            json_bytes = len(json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError, OverflowError, UnicodeError) as exc:
            failure = {**base, "error_code": result.get("error_code") or "primitive_csg_output_not_serializable",
                       "serialization_error_type": type(exc).__name__,
                       "diagnostic_values_marked": True}
            for name in ("source_evidence", "source_geometry"):
                if name in result:
                    failure[name] = _primitive_csg_safe_evidence(result[name])
            metadata = {"ok": False, "version": version, "result": failure}
            png = None
            json_bytes = len(json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
        if json_bytes > _PRIMITIVE_CSG_MAX_RESULT_BYTES or png is not None and len(png) > _PRIMITIVE_CSG_MAX_RESULT_BYTES:
            return {"ok": False, "version": version, "result": {
                **base, "error_code": "primitive_csg_output_limit_exceeded", "observed_json_bytes": json_bytes,
                "output_omitted": "Mesh, drawing and raw source evidence omitted because the output budget was exceeded."}}
        if png is not None:
            return ImageToolResult(data=base64.b64encode(png).decode("ascii"), mime_type="image/png", metadata=metadata)
        return metadata

    try:
        native, evidence = read_primitive_csg_source(
            bridge, refno, include_native_volume=include_native_volume,
            context_reader=lambda: _assembly_boundary_context(bridge))
    except PrimitiveCsgReadError as exc:
        before = exc.evidence.get("context", {}).get("before")
        version = before.get("version") if isinstance(before, dict) else None
        return finish({**base, "error_code": exc.code, "source_evidence": exc.evidence})
    version = evidence["context"]["before"]["version"]
    output = {**base, "resolved_refno": evidence["resolved_refno"], "resolved_type": evidence["resolved_type"],
              "source_geometry": native, "source_evidence": evidence}
    try:
        mesh = derive_primitive_csg_mesh(native, radial_segments=radial_segments,
            **({"chord_tolerance_mm": chord_tolerance} if chord_tolerance is not None else {}))
    except Exception as exc:
        output.update(error_code="primitive_csg_derivation_failed", error_type=type(exc).__name__,
                      derivation_error=str(exc)[:1024])
        return finish(output)
    output["native_volume_comparison"] = compare_primitive_csg_volumes(mesh, evidence)
    if any(mesh.get(flag) is not True for flag in ("success", "available", "complete")):
        output.update(error_code="primitive_csg_derivation_failed", derived_mesh=mesh)
        return finish(output)
    output.update(success=True, available=True, complete=True,
                  cuts_applied=mesh.get("cuts_applied"), csg_evaluated=mesh.get("csg_evaluated"),
                  material_boundary_validated=mesh.get("material_boundary_validated"), empty=mesh.get("empty"))
    if drawing_requested or detail == "summary":
        output["derived_mesh"] = {key: value for key, value in mesh.items()
                                  if key not in {"vertices", "triangles", "normals", "triangle_normals"}}
        output["derived_mesh"].update(vertices=None, triangles=None, normals=None, geometry_arrays_included=False)
    else:
        output["derived_mesh"] = mesh
    output["mesh_detail"] = "summary" if drawing_requested else detail
    png = None
    if drawing_requested:
        from .primitive_csg_drawing import generate_primitive_csg_drawing
        try:
            drawing = generate_primitive_csg_drawing(mesh, **_drawing_projection_options(options))
        except Exception as exc:
            output.update(success=False, available=False, complete=False,
                          error_code="primitive_csg_drawing_failed", error_type=type(exc).__name__,
                          projection_error=str(exc)[:1024])
            return finish(output)
        if any(drawing.get(flag) is not True for flag in ("success", "available", "complete")):
            output.update(success=False, available=False, complete=False,
                          error_code="primitive_csg_drawing_failed", drawing_diagnostics=drawing)
            return finish(output)
        if drawing.get("empty") is True:
            output.update(drawing=drawing, image_omitted="The parameter difference is empty; there is no linework to render.")
        else:
            try:
                exported = {key: value for key, value in drawing.items() if key not in {"svg", "dxf"}}
                if options["include_svg"]:
                    exported["svg"] = drawing["svg"]
                if options["include_dxf"]:
                    exported["dxf"] = export_orthographic_dxf(drawing, footer=drawing["footer"])
                png = rasterize_orthographic_png(drawing["views"], width=options["preview_width"],
                                                 height=options["preview_height"], footer=drawing["footer"])
                output["drawing"] = exported
            except Exception as exc:
                output.update(success=False, available=False, complete=False,
                              error_code="primitive_csg_drawing_export_failed", export_error=str(exc), drawing=None)
                return finish(output)
    return finish(output, png)


def _primitive_geometry(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"refno"}
    if unknown:
        raise ValueError(f"Unknown e3d_primitive_geometry arguments: {', '.join(sorted(unknown))}")
    return bridge.primitive_geometry(_panel_reference(args, "refno"))


def _generated_csg(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"refno", "max_trees", "max_items"}
    if unknown:
        raise ValueError(f"Unknown e3d_generated_csg arguments: {', '.join(sorted(unknown))}")
    refno = _panel_reference(args, "refno")
    max_trees = _integer(args, "max_trees", 8, 1, 16)
    max_items = _integer(args, "max_items", 64, 1, 256)
    response = bridge.generated_csg(refno, max_trees=max_trees, max_items=max_items)
    return validate_generated_csg_response(response, requested_refno=refno,
                                           max_trees=max_trees, max_items=max_items)


def _primitive_mesh(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"refno", "radial_segments", "coordinate_space", "chord_tolerance_mm"}
    if unknown:
        raise ValueError(f"Unknown e3d_primitive_mesh arguments: {', '.join(sorted(unknown))}")
    refno = _panel_reference(args, "refno")
    radial_segments = _integer(args, "radial_segments", 48, 12, 128)
    chord_tolerance = _chord_tolerance(args)
    coordinate_space = args.get("coordinate_space", "primitive_local")
    if not isinstance(coordinate_space, str) or coordinate_space not in {"primitive_local", "WORLD"}:
        raise ValueError("coordinate_space must be primitive_local or WORLD")
    response = bridge.primitive_geometry(refno)
    if not isinstance(response, dict) or response.get("ok") is not True or not isinstance(response.get("result"), dict):
        return response
    geometry = response["result"]
    mesh = derive_primitive_mesh(geometry, radial_segments=radial_segments, coordinate_space=coordinate_space,
        **({"chord_tolerance_mm": chord_tolerance} if chord_tolerance is not None else {}))
    return {**response, "result": _mesh_result(geometry, mesh)}


def _mesh_result(geometry: dict[str, Any], mesh: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": mesh.get("success") is True,
        "available": mesh.get("available") is True,
        "complete": mesh.get("complete") is True,
        "source_geometry": geometry,
        "derived_mesh": mesh,
    }


def _mesh_source_summary(geometry: dict[str, Any]) -> dict[str, Any]:
    summary = dict(geometry)
    source_loops = geometry.get("loops")
    if isinstance(source_loops, (list, tuple)):
        compact_loops = []
        for loop in source_loops:
            if not isinstance(loop, dict):
                compact_loops.append(loop)
                continue
            compact_loop = dict(loop)
            raw_vertices = compact_loop.get("vertices")
            compact_loop["vertex_count"] = len(raw_vertices) if isinstance(raw_vertices, (list, tuple)) else None
            compact_loop["vertices"] = None
            compact_loop["vertex_array_included"] = False
            compact_loops.append(compact_loop)
        summary["loops"] = compact_loops
    summary["geometry_arrays_included"] = False
    return summary


def _element_mesh(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"refno", "expected_type", "coordinate_space", "mesh_detail", "radial_segments", "chord_tolerance_mm"}
    if unknown:
        raise ValueError(f"Unknown e3d_element_mesh arguments: {', '.join(sorted(unknown))}")
    refno = _panel_reference(args, "refno")
    expected_type = _string(args, "expected_type")
    if expected_type not in _ELEMENT_MESH_TYPES:
        raise ValueError("expected_type must be one of: " + ", ".join(_ELEMENT_MESH_TYPES))
    space = _string(args, "coordinate_space", "local")
    if space not in ("local", "WORLD"):
        raise ValueError("coordinate_space must be local or WORLD")
    mesh_detail = _string(args, "mesh_detail", "full")
    if mesh_detail not in ("full", "summary"):
        raise ValueError("mesh_detail must be full or summary")
    if "radial_segments" in args and expected_type in ("PANE", "BOX"):
        raise ValueError("radial_segments applies only to CYLI, CONE and DISH")
    radial_segments = _integer(args, "radial_segments", 48, 12, 99)
    chord_tolerance = _chord_tolerance(args)
    if chord_tolerance is not None and expected_type == "PANE":
        raise ValueError("chord_tolerance_mm does not apply to PANE")

    panel = expected_type == "PANE"
    response = (bridge.panel_geometry(refno, include_world=space == "WORLD")
                if panel else bridge.primitive_geometry(refno))
    if isinstance(response, dict) and response.get("ok") is False:
        return response
    if not isinstance(response, dict) or response.get("ok") is not True or not isinstance(response.get("result"), dict):
        return {"ok": False, "error": "Native mesh reader did not return a geometry result", "error_code": "invalid_geometry_response"}
    geometry = response["result"]
    target = geometry.get("element")
    target = target if isinstance(target, dict) else {}
    actual_refno = target.get("refno")
    actual_type = target.get("type")
    # The PANE reader checks ShortName and returns the SDK long type name.
    resolved_type = "PANE" if panel and actual_type == "PANEL" else actual_type
    evidence = {"requested_refno": refno, "expected_type": expected_type,
                "resolved_refno": actual_refno, "resolved_type": resolved_type, "coordinate_space": space}

    if geometry.get("success") is not True or geometry.get("complete") is not True:
        error = "native_geometry_incomplete"
    elif not isinstance(actual_refno, str) or not re.fullmatch(r"=[0-9]+/[0-9]+", actual_refno):
        error = "native_element_identity_missing"
    elif refno.startswith("=") and [part.lstrip("0") for part in refno[1:].split("/")] != [
            part.lstrip("0") for part in actual_refno[1:].split("/")]:
        error = "element_reference_mismatch"
    elif refno.startswith("/") and (
            not isinstance(target.get("name"), str) or
            ("/" + target["name"].lstrip("/")).casefold() != refno.casefold()):
        error = "element_name_mismatch"
    elif resolved_type != expected_type:
        error = "element_type_mismatch"
    else:
        error = None
    if error:
        source_geometry = _mesh_source_summary(geometry) if mesh_detail == "summary" else geometry
        return {**response, "result": {**evidence, "success": False, "available": False, "complete": False,
                                       "error_code": error, "mesh_detail": mesh_detail,
                                       "source_geometry": source_geometry, "derived_mesh": None}}

    if panel:
        mesh = derive_panel_mesh(geometry, coordinate_space="panel_local" if space == "local" else space)
    else:
        mesh = derive_primitive_mesh(geometry, radial_segments=radial_segments,
                                     coordinate_space="primitive_local" if space == "local" else space,
                                     **({"chord_tolerance_mm": chord_tolerance} if chord_tolerance is not None else {}))
    output = {**evidence, **_mesh_result(geometry, mesh)}
    if mesh_detail == "summary":
        output["source_geometry"] = _mesh_source_summary(geometry)
        mesh_summary = {key: value for key, value in mesh.items()
                        if key not in {"vertices", "triangles", "triangle_normals"}}
        mesh_summary.update({"vertices": None, "triangles": None, "triangle_normals": None,
                             "geometry_arrays_included": False})
        output["derived_mesh"] = mesh_summary
        if mesh.get("available") is True:
            metrics = measure_triangle_mesh(mesh.get("vertices"), mesh.get("triangles"))
            validation = metrics.get("validation", {})
            output["mesh_metrics"] = {
                "available": metrics.get("available") is True,
                "complete": metrics.get("complete") is True,
                "bounds": metrics.get("bounds"),
                "surface_area": metrics.get("surface_area"),
                "coordinate_unit": "mm",
                "area_unit": "mm2",
                "volume_unit": "mm3",
                "valid": validation.get("valid"),
                "checks": validation.get("checks"),
                "components": validation.get("components"),
                "findings": validation.get("findings"),
            }
        else:
            output["mesh_metrics"] = {"available": False, "complete": False,
                                      "errors": mesh.get("errors", ["derived_mesh_unavailable"])}
    output["mesh_detail"] = mesh_detail
    return {**response, "result": output}


def _drawing_options(args: dict[str, Any], *, allow_projection_method: bool = True) -> dict[str, Any]:
    """Validate projection/export options before either drawing reader runs."""
    views = args.get("views", ["FRONT", "TOP", "SIDE"])
    if not isinstance(views, list) or not 1 <= len(views) <= 3 or any(
            not isinstance(view, str) or view not in {"FRONT", "TOP", "SIDE"} for view in views):
        raise ValueError("views must contain one to three of FRONT, TOP or SIDE")
    if len(set(views)) != len(views):
        raise ValueError("views must not contain duplicates")
    feature_angle = args.get("feature_angle_degrees", 30.0)
    try:
        feature_angle = float(feature_angle) if not isinstance(feature_angle, bool) else math.nan
    except (OverflowError, TypeError, ValueError):
        feature_angle = math.nan
    if not math.isfinite(feature_angle) or not 0.0 <= feature_angle <= 180.0:
        raise ValueError("feature_angle_degrees must be a finite number between 0 and 180")
    hidden_line_removal = args.get("hidden_line_removal", True)
    if not isinstance(hidden_line_removal, bool):
        raise ValueError("hidden_line_removal must be a boolean")
    show_hidden_lines = args.get("show_hidden_lines", False)
    if not isinstance(show_hidden_lines, bool):
        raise ValueError("show_hidden_lines must be a boolean")
    if show_hidden_lines and not hidden_line_removal:
        raise ValueError("show_hidden_lines requires hidden_line_removal=true")
    include_svg = args.get("include_svg", False)
    if not isinstance(include_svg, bool):
        raise ValueError("include_svg must be a boolean")
    include_dxf = args.get("include_dxf", False)
    if not isinstance(include_dxf, bool):
        raise ValueError("include_dxf must be a boolean")
    preview_width = _integer(args, "preview_width", 1200, 64, 2048)
    preview_height = _integer(args, "preview_height", 440, 64, 2048)
    if preview_width * preview_height > 2_000_000:
        raise ValueError("preview_width * preview_height must not exceed 2000000 pixels")
    radial_segments = _integer(args, "radial_segments", 48, 12, 99)
    sheet_options = normalize_sheet_options(args["sheet"]) if "sheet" in args else None
    if sheet_options is not None and "projection_method" in sheet_options:
        if not allow_projection_method:
            raise ValueError("projection_method_requires_projected_three_views")
        if set(views) != {"FRONT", "TOP", "SIDE"}:
            raise ValueError("projection_method_requires_all_three_views")
    return {
        "views": views, "feature_angle_degrees": feature_angle,
        "hidden_line_removal": hidden_line_removal, "show_hidden_lines": show_hidden_lines,
        "include_svg": include_svg, "include_dxf": include_dxf,
        "preview_width": preview_width, "preview_height": preview_height, "radial_segments": radial_segments,
        "sheet": sheet_options,
    }


def _drawing_projection_options(options: dict[str, Any]) -> dict[str, Any]:
    result = {name: options[name] for name in (
        "views", "feature_angle_degrees", "hidden_line_removal", "show_hidden_lines")}
    if options.get("sheet") is not None and "projection_method" in options["sheet"]:
        result["projection_convention"] = "right_handed"
    return result


def _render_drawing_outputs(drawing: dict[str, Any], options: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    output = {key: value for key, value in drawing.items()
              if key not in {"svg", "dxf"}}
    if options["sheet"] is not None:
        sheet = build_drawing_sheet(drawing, options["sheet"])
        output["sheet"] = sheet
        if options["include_svg"]:
            output["svg"] = render_drawing_sheet_svg(sheet)
        if options["include_dxf"]:
            output["dxf"] = export_drawing_sheet_dxf(sheet)
        png = rasterize_drawing_sheet_png(sheet, width=options["preview_width"],
                                          height=options["preview_height"])
    else:
        if options["include_svg"]:
            output["svg"] = drawing["svg"]
        if options["include_dxf"]:
            output["dxf"] = export_orthographic_dxf(drawing)
        png = rasterize_orthographic_png(drawing["views"], width=options["preview_width"],
                                         height=options["preview_height"])
    return output, png


def _project_element_drawing(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any] | ImageToolResult:
    allowed = {"refno", "expected_type", "coordinate_space", "views", "feature_angle_degrees",
               "hidden_line_removal", "show_hidden_lines", "include_svg", "include_dxf", "preview_width", "preview_height",
               "radial_segments", "sheet"}
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"Unknown e3d_project_element_drawing arguments: {', '.join(sorted(unknown))}")
    refno = _panel_reference(args, "refno")
    expected_type = _string(args, "expected_type")
    if expected_type not in _ELEMENT_MESH_TYPES:
        raise ValueError("expected_type must be one of: " + ", ".join(_ELEMENT_MESH_TYPES))
    coordinate_space = _string(args, "coordinate_space", "WORLD")
    if coordinate_space not in ("local", "WORLD"):
        raise ValueError("coordinate_space must be local or WORLD")
    options = _drawing_options(args)
    if "radial_segments" in args and expected_type in {"PANE", "BOX"}:
        raise ValueError("radial_segments applies only to CYLI, CONE and DISH")
    mesh_args: dict[str, Any] = {
        "refno": refno, "expected_type": expected_type,
        "coordinate_space": coordinate_space, "mesh_detail": "full",
    }
    if expected_type in {"CYLI", "CONE", "DISH"}:
        mesh_args["radial_segments"] = options["radial_segments"]
    response = _element_mesh(bridge, mesh_args)
    if not isinstance(response, dict) or response.get("ok") is not True:
        return response if isinstance(response, dict) else {
            "ok": False, "error": "Native mesh reader did not return a response",
            "error_code": "invalid_mesh_response",
        }
    mesh_result = response.get("result")
    if not isinstance(mesh_result, dict):
        return {**response, "result": {"success": False, "available": False, "complete": False,
                                        "error_code": "invalid_mesh_result"}}
    derived = mesh_result.get("derived_mesh")
    if mesh_result.get("success") is not True or mesh_result.get("available") is not True or \
            mesh_result.get("complete") is not True or not isinstance(derived, dict):
        # Preserve the guarded mesh reader's explicit failure, without copying
        # any partial geometry into a drawing response.
        derived_errors = derived.get("errors") if isinstance(derived, dict) else None
        error_code = mesh_result.get("error_code")
        if not isinstance(error_code, str):
            error_code = derived_errors[0] if isinstance(derived_errors, list) and derived_errors and \
                isinstance(derived_errors[0], str) else "derived_mesh_unavailable"
        return {**response, "result": {
            "requested_refno": mesh_result.get("requested_refno", refno),
            "expected_type": expected_type,
            "resolved_refno": mesh_result.get("resolved_refno"),
            "resolved_type": mesh_result.get("resolved_type"),
            "coordinate_space": coordinate_space,
            "success": False, "available": False, "complete": mesh_result.get("complete") is True,
            "error_code": error_code,
            "source_mesh": {"available": False, "complete": derived.get("complete") is True
                            if isinstance(derived, dict) else False,
                            "errors": derived_errors if isinstance(derived_errors, list) else []},
            "drawing": None,
        }}
    drawing = generate_orthographic_drawing(
        derived.get("vertices"), derived.get("triangles"),
        coordinate_frame=str(derived.get("coordinate_frame", "E3D coordinates; millimetres")),
        coordinate_unit="mm",
        **_drawing_projection_options(options),
    )
    output = {
        "requested_refno": mesh_result.get("requested_refno", refno),
        "expected_type": expected_type,
        "resolved_refno": mesh_result.get("resolved_refno"),
        "resolved_type": mesh_result.get("resolved_type"),
        "coordinate_space": coordinate_space,
        "success": drawing.get("success") is True,
        "available": drawing.get("available") is True,
        "complete": drawing.get("complete") is True,
        "source_mesh": {
            "coordinate_frame": derived.get("coordinate_frame"),
            "coordinate_space": derived.get("coordinate_space"),
            "native_tessellation": derived.get("native_tessellation") is True,
            "cuts_applied": derived.get("cuts_applied") is True,
            "counts": derived.get("counts"),
        },
        "drawing": ({key: value for key, value in drawing.items()
                     if (options["include_svg"] or key != "svg") and key != "dxf"}
                    if drawing.get("success") is True else None),
    }
    if drawing.get("success") is not True:
        output["error_code"] = drawing.get("error_code", "drawing_unavailable")
        output["drawing_diagnostics"] = {key: value for key, value in drawing.items()
                                          if key not in {"views", "svg"}}
    metadata = {**response, "result": output}
    if drawing.get("success") is True:
        try:
            output["drawing"], png = _render_drawing_outputs(drawing, options)
        except ValueError as exc:
            output.update(success=False, available=False, complete=False, drawing=None,
                          error_code="drawing_export_failed", export_error=str(exc))
            return {**metadata, "ok": False}
        image_data = base64.b64encode(png).decode("ascii")
        return ImageToolResult(data=image_data, mime_type="image/png", metadata=metadata)
    return metadata


def _drawing_reference_key(refno: str) -> str:
    if refno.startswith("="):
        return "=" + "/".join(part.lstrip("0") or "0" for part in refno[1:].split("/"))
    return refno.casefold()


def _section_element_drawing(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any] | ImageToolResult:
    allowed = {"refno", "expected_type", "coordinate_space", "plane", "include_svg", "include_dxf",
               "preview_width", "preview_height", "radial_segments", "sheet", "hatch", "dimensions"}
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"Unknown e3d_section_element_drawing arguments: {', '.join(sorted(unknown))}")
    refno = _panel_reference(args, "refno")
    expected_type = _string(args, "expected_type")
    if expected_type not in _ELEMENT_MESH_TYPES:
        raise ValueError("expected_type must be one of: " + ", ".join(_ELEMENT_MESH_TYPES))
    coordinate_space = _string(args, "coordinate_space", "WORLD")
    if coordinate_space not in {"local", "WORLD"}:
        raise ValueError("coordinate_space must be local or WORLD")
    # Validate every option, including the entire plane, before the native read.
    normalize_section_plane(args.get("plane"))
    options = _drawing_options(args, allow_projection_method=False)
    hatch_options = normalize_section_hatch(args["hatch"]) if "hatch" in args else None
    if "dimensions" in args:
        normalize_section_dimensions(args["dimensions"])
        if options["sheet"] is None:
            raise ValueError("section_dimensions_require_sheet")
    if "radial_segments" in args and expected_type in {"PANE", "BOX"}:
        raise ValueError("radial_segments applies only to CYLI, CONE and DISH")
    mesh_args: dict[str, Any] = {"refno": refno, "expected_type": expected_type,
                                "coordinate_space": coordinate_space, "mesh_detail": "full"}
    if expected_type in {"CYLI", "CONE", "DISH"}:
        mesh_args["radial_segments"] = options["radial_segments"]
    response = _element_mesh(bridge, mesh_args)
    if not isinstance(response, dict) or response.get("ok") is not True:
        return response if isinstance(response, dict) else {
            "ok": False, "error_code": "invalid_mesh_response", "error": "Native mesh reader did not return a response"}
    source = _assembly_source_evidence({"refno": refno, "expected_type": expected_type}, response, 0)
    output: dict[str, Any] = {
        "requested_refno": refno, "expected_type": expected_type,
        "resolved_refno": source.get("resolved_refno"), "resolved_type": source.get("resolved_type"),
        "coordinate_space": coordinate_space, "coordinate_unit": "mm",
        "success": False, "available": False, "complete": False,
        "source": source, "source_mesh": source.get("source_mesh"), "section": None, "drawing": None,
    }
    if source.get("success") is not True:
        output["error_code"] = source["error_code"]
        return {**response, "ok": False, "result": output}
    mesh = response["result"]["derived_mesh"]
    return _finish_section_drawing({**response, "result": output}, mesh, args, options, hatch_options, coordinate_space)


def _prepare_section_drawing(response: dict[str, Any], mesh: dict[str, Any], args: dict[str, Any],
                             hatch_options: dict[str, Any] | None, coordinate_space: str, *,
                             assembly: bool = False) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]] | None] | None:
    """Prepare section evidence and annotations without rendering or rereading."""
    output = response["result"]
    section = slice_triangle_mesh(mesh.get("vertices"), mesh.get("triangles"), plane=args["plane"])
    section.update(coordinate_unit="mm", area_unit="mm2", length_unit="mm",
                   source_coordinate_frame=mesh.get("coordinate_frame"),
                   source_coordinate_space=coordinate_space)
    if assembly and section.get("success") is not True:
        section = annotate_section_diagnostics(section, mesh["source_ranges"])
    output.update(section=section, success=section.get("success") is True,
                  available=section.get("available") is True, complete=section.get("complete") is True)
    if section.get("success") is not True or section.get("complete") is not True:
        output.update(success=False, available=False, error_code=section.get("error_code", "section_unavailable"))
        return None
    if assembly:
        try:
            section = annotate_section_sources(section, mesh["source_ranges"])
        except ValueError as exc:
            output.update(success=False, available=False, complete=False,
                          error_code="section_source_mapping_failed", source_mapping_error=str(exc))
            return None
        output["section"] = section
    identities = output.get("sources", [output.get("source", {})])
    source_identity = [{"refno": item.get("resolved_refno"), "type": item.get("resolved_type"),
                        "revision": item.get("source_mesh", {}).get("revision")}
                       for item in identities]
    try:
        section["dimension_revision"] = section_dimension_revision(section, source_identity)
    except ValueError as exc:
        # Annotation eligibility must not narrow the existing unannotated
        # section contract, e.g. when its coordinates exceed annotation bounds.
        section["dimension_revision"] = None
        section["dimension_revision_error"] = str(exc)
    linear_dimensions = None
    if "dimensions" in args:
        try:
            linear_dimensions = resolve_section_dimensions(section, args["dimensions"])
        except ValueError as exc:
            output.update(success=False, available=False, complete=False, error_code="section_dimensions_failed")
            output["dimensions"] = {"success": False, "available": False, "complete": True,
                                    "error_code": str(exc), "items": [],
                                    "current_section_revision": section["dimension_revision"]}
            return None
        output["dimensions"] = {"success": True, "available": True, "complete": True,
                                "section_revision": section["dimension_revision"], "items": linear_dimensions,
                                "coordinate_unit": "mm", "measurement_scope": "absolute section-axis separation; not 3D length or certified engineering dimensions"}
    hatching = None
    if hatch_options is not None:
        try:
            hatching = build_section_hatching(section, hatch_options)
        except Exception as exc:
            # Preserve the native and section observations if generation
            # encounters an unexpected local error; do not return a fallback.
            hatching = {"success": False, "available": False, "complete": False, "lines": [],
                        "error_code": "section_hatch_generation_failed", "error_type": type(exc).__name__,
                        "error": str(exc)[:1024]}
        output["hatching"] = hatching
        if any(hatching.get(flag) is not True for flag in ("success", "available", "complete")):
            output.update(success=False, available=False, complete=False, error_code="section_hatching_failed")
            return None
    return section, hatching, linear_dimensions


def _finish_section_drawing(response: dict[str, Any], mesh: dict[str, Any], args: dict[str, Any],
                            options: dict[str, Any], hatch_options: dict[str, Any] | None,
                            coordinate_space: str, *, assembly: bool = False) -> dict[str, Any] | ImageToolResult:
    """Slice and render an already verified mesh, preserving source evidence."""
    prepared = _prepare_section_drawing(response, mesh, args, hatch_options, coordinate_space, assembly=assembly)
    if prepared is None:
        return {**response, "ok": False}
    section, hatching, linear_dimensions = prepared
    output = response["result"]
    if section.get("intersection") is False:
        return {**response, "result": output}
    try:
        drawing = _section_drawing(section, mesh, hatching=hatching, linear_dimensions=linear_dimensions,
                                   include_plain_svg=options["sheet"] is None and options["include_svg"])
        output["drawing"], png = _render_drawing_outputs(drawing, options)
    except ValueError as exc:
        output.update(success=False, available=False, complete=False, drawing=None,
                      error_code="drawing_export_failed", export_error=str(exc))
        return {**response, "ok": False, "result": output}
    return ImageToolResult(data=base64.b64encode(png).decode("ascii"), mime_type="image/png",
                           metadata={**response, "result": output})


def _compose_drawing_atlas(bridge: BridgeClient, args: dict[str, Any]) -> MultiImageToolResult:
    """Compose a bounded set of explicit assembly sheets at one shared scale."""
    if "dimensions" in args:
        raise ValueError("Section dimensions must be supplied per page; shared dimensions are not supported")
    raw_pages = args.get("pages")
    if not isinstance(raw_pages, list) or not 2 <= len(raw_pages) <= MAX_DRAWING_ATLAS_PAGES:
        raise ValueError(f"pages must contain two to {MAX_DRAWING_ATLAS_PAGES} explicit drawing pages")
    shared = {key: value for key, value in args.items() if key != "pages"}
    shared["views"] = ["FRONT", "TOP", "SIDE"]
    options = _drawing_options(shared)
    sheet_options = options.get("sheet")
    if not isinstance(sheet_options, dict) or "scale_denominator" not in sheet_options:
        raise ValueError("Multi-page mode requires sheet.scale_denominator to keep all pages at one explicit scale")
    scale_denominator = sheet_options["scale_denominator"]
    if _string(args, "coordinate_space", "WORLD") != "WORLD":
        raise ValueError("Composition requires coordinate_space=WORLD")
    preview_width, preview_height = options["preview_width"], options["preview_height"]
    if preview_width * preview_height * len(raw_pages) > 8_000_000:
        raise ValueError("Multi-page previews may not exceed 8000000 pixels in total")

    pages: list[dict[str, Any]] = []
    total_sources = 0
    for page_index, page in enumerate(raw_pages, 1):
        if not isinstance(page, dict) or set(page) - {"targets", "plane", "title", "drawing_number", "dimensions"} or \
                not {"targets", "plane"} <= set(page):
            raise ValueError(f"pages[{page_index - 1}] must contain targets and plane, with optional title/drawing_number/dimensions")
        targets = _assembly_drawing_targets({"targets": page["targets"]})
        total_sources += len(targets)
        if total_sources > MAX_DRAWING_ATLAS_SOURCES:
            raise ValueError(f"Multi-page mode supports at most {MAX_DRAWING_ATLAS_SOURCES} explicit element references")
        normalize_section_plane(page["plane"])
        plane = dict(page["plane"])
        if "dimensions" in page:
            normalize_section_dimensions(page["dimensions"])
        title = page.get("title")
        if title is not None and (not isinstance(title, str) or not title or len(title) > 120 or
                                  not re.fullmatch(r"[A-Za-z0-9 .+/:_-]*", title)):
            raise ValueError(f"pages[{page_index - 1}].title contains unsupported characters or length")
        drawing_number = page.get("drawing_number", "")
        if not isinstance(drawing_number, str) or len(drawing_number) > 40 or \
                not re.fullmatch(r"[A-Za-z0-9 .+/:_-]*", drawing_number):
            raise ValueError(f"pages[{page_index - 1}].drawing_number contains unsupported characters or length")
        pages.append({"page_number": page_index, "targets": targets, "plane": plane,
                      "title": title, "drawing_number": drawing_number,
                      "dimensions": page.get("dimensions")})

    manifest = [{"page_number": page["page_number"],
                 "targets": [{"refno": target["refno"], "expected_type": target["expected_type"]}
                             for target in page["targets"]],
                 "plane": page["plane"], "dimensions": page["dimensions"], "title": page["title"],
                 "drawing_number": page["drawing_number"]} for page in pages]
    shared_options = {key: value for key, value in args.items() if key != "pages"}
    manifest_hash = hashlib.sha256(json.dumps({"pages": manifest, "options": shared_options},
                                              sort_keys=True, separators=(",", ":"),
                                              ensure_ascii=True).encode("ascii")).hexdigest()
    result_pages: list[dict[str, Any]] = []
    image_payloads: list[tuple[str, str, str]] = []
    prior_context: tuple[str, str, str, str] | None = None
    result_version: str | None = None
    for page in pages:
        index = page["page_number"]
        per_page_sheet = dict(sheet_options)
        if page["title"] is not None:
            per_page_sheet["title"] = page["title"]
        else:
            suffix = f" PAGE {index:02d}"
            per_page_sheet["title"] = per_page_sheet.get("title", "E3D ORTHOGRAPHIC DRAWING")[:120 - len(suffix)] + suffix
        if page["drawing_number"]:
            per_page_sheet["drawing_number"] = page["drawing_number"]
        else:
            base_number = per_page_sheet.get("drawing_number", "")
            suffix = f"-P{index:02d}"
            per_page_sheet["drawing_number"] = f"{base_number[:40 - len(suffix)]}{suffix}" if base_number else f"P{index:02d}"
        compose_args = {key: value for key, value in args.items()
                        if key not in {"pages", "sheet", "targets", "plane", "dimensions"}}
        compose_args.update(targets=page["targets"], plane=page["plane"], sheet=per_page_sheet)
        if page["dimensions"] is not None:
            compose_args["dimensions"] = page["dimensions"]
        try:
            composed = _compose_drawing_sheet(bridge, compose_args)
        except Exception as exc:
            record = {"page_number": index, "title": per_page_sheet["title"],
                      "drawing_number": per_page_sheet["drawing_number"],
                      "requested_targets": page["targets"], "complete": False,
                      "drawing_complete": False, "error_code": "page_composition_exception",
                      "error_type": type(exc).__name__}
            result_pages.append(record)
            result_pages.extend({"page_number": remaining["page_number"], "title": remaining.get("title"),
                                 "drawing_number": remaining.get("drawing_number"),
                                 "requested_targets": remaining["targets"], "complete": False,
                                 "drawing_complete": False, "read_status": "not_attempted",
                                 "error_code": "earlier_page_failed"}
                                for remaining in pages[index:])
            return MultiImageToolResult([], {"ok": False, "result": {
                "success": False, "available": False, "complete": False, "coordinate_space": "WORLD",
                "manifest_sha256": manifest_hash, "scale_denominator": scale_denominator,
                "page_count": len(pages), "completed_page_count": len(image_payloads),
                "pages": result_pages, "error_code": record["error_code"]}})
        metadata = composed.metadata if isinstance(composed, ImageToolResult) else composed
        if isinstance(metadata, dict) and isinstance(metadata.get("version"), str):
            result_version = metadata["version"]
        page_result = metadata.get("result") if isinstance(metadata, dict) else None
        record: dict[str, Any] = {"page_number": index, "title": per_page_sheet["title"],
                                  "drawing_number": per_page_sheet["drawing_number"],
                                  "requested_targets": page["targets"], "complete": False,
                                  "drawing_complete": isinstance(page_result, dict) and
                                  page_result.get("success") is True and page_result.get("complete") is True,
                                  "result": page_result}
        if not isinstance(composed, ImageToolResult) or not isinstance(metadata, dict) or \
                metadata.get("ok") is not True or not isinstance(page_result, dict) or \
                page_result.get("success") is not True or page_result.get("complete") is not True:
            record["error_code"] = (page_result.get("error_code") if isinstance(page_result, dict) else None) or \
                (metadata.get("error_code") if isinstance(metadata, dict) else None) or "page_composition_failed"
            result_pages.append(record)
            result_pages.extend({"page_number": remaining["page_number"], "title": remaining.get("title"),
                                 "drawing_number": remaining.get("drawing_number"),
                                 "requested_targets": remaining["targets"], "complete": False,
                                 "drawing_complete": False,
                                 "read_status": "not_attempted", "error_code": "earlier_page_failed"}
                                for remaining in pages[index:])
            return MultiImageToolResult([], {"ok": False, "result": {
                "success": False, "available": False, "complete": False, "coordinate_space": "WORLD",
                "manifest_sha256": manifest_hash, "scale_denominator": scale_denominator,
                "page_count": len(pages), "completed_page_count": len(image_payloads), "pages": result_pages,
                "error_code": record["error_code"],
                "completeness_scope": "No PNG image blocks are emitted unless every requested page completes."}})
        drawing = page_result.get("drawing")
        actual_scale = drawing.get("sheet", {}).get("scale_denominator") if isinstance(drawing, dict) else None
        if not isinstance(actual_scale, (int, float)) or isinstance(actual_scale, bool) or actual_scale != scale_denominator:
            record["error_code"] = "shared_scale_verification_failed"
            result_pages.append(record)
            result_pages.extend({"page_number": remaining["page_number"], "title": remaining.get("title"),
                                 "drawing_number": remaining.get("drawing_number"),
                                 "requested_targets": remaining["targets"], "complete": False,
                                 "drawing_complete": False,
                                 "read_status": "not_attempted", "error_code": "earlier_page_failed"}
                                for remaining in pages[index:])
            return MultiImageToolResult([], {"ok": False, "result": {
                "success": False, "available": False, "complete": False, "coordinate_space": "WORLD",
                "manifest_sha256": manifest_hash, "scale_denominator": scale_denominator,
                "page_count": len(pages), "completed_page_count": len(image_payloads), "pages": result_pages,
                "error_code": record["error_code"]}})
        context = page_result.get("context")
        before = context.get("before") if isinstance(context, dict) else None
        after = context.get("after") if isinstance(context, dict) else None
        def identity(observed: Any) -> tuple[str, str, str, str] | None:
            if not isinstance(observed, dict) or observed.get("available") is not True:
                return None
            project = observed.get("project")
            if not isinstance(project, dict):
                return None
            values = (observed.get("session_id"), observed.get("version"), project.get("name"), project.get("mdb"))
            return tuple(values) if all(isinstance(value, str) and value for value in values) else None  # type: ignore[return-value]
        before_identity, after_identity = identity(before), identity(after)
        if (not isinstance(context, dict) or context.get("boundary_agreement") is not True or
                before_identity is None or after_identity is None or before_identity != after_identity or
                (prior_context is not None and prior_context != before_identity)):
            record["error_code"] = "multi_page_context_changed"
            result_pages.append(record)
            result_pages.extend({"page_number": remaining["page_number"], "title": remaining.get("title"),
                                 "drawing_number": remaining.get("drawing_number"),
                                 "requested_targets": remaining["targets"], "complete": False,
                                 "drawing_complete": False,
                                 "read_status": "not_attempted", "error_code": "earlier_page_failed"}
                                for remaining in pages[index:])
            return MultiImageToolResult([], {"ok": False, "result": {
                "success": False, "available": False, "complete": False, "coordinate_space": "WORLD",
                "manifest_sha256": manifest_hash, "scale_denominator": scale_denominator,
                "page_count": len(pages), "completed_page_count": len(image_payloads), "pages": result_pages,
                "error_code": record["error_code"],
                "context_consistency": "Page boundary observations differed; separate reads are not atomic."}})
        prior_context = after_identity
        record["complete"] = True
        result_pages.append(record)
        image_payloads.append((f"Page {index}: {per_page_sheet['title']} [{per_page_sheet['drawing_number']}]",
                               composed.data, composed.mime_type))

    metadata = {"ok": True, "version": result_version, "result": {
        "success": True, "available": True, "complete": True, "coordinate_space": "WORLD",
        "coordinate_unit": "mm", "manifest_sha256": manifest_hash, "scale_denominator": scale_denominator,
        "page_count": len(result_pages), "completed_page_count": len(result_pages),
        "total_requested_sources": total_sources, "pages": result_pages,
        "context_consistency": "All pages returned matching before/after session, project and MDB observations; this is not an atomic database snapshot."}}
    serialized = json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    image_bytes = sum(len(data.encode("ascii")) for _label, data, _mime in image_payloads)
    if len(serialized) > 8 * 1024 * 1024 or image_bytes > 16 * 1024 * 1024:
        compact_pages = []
        for page_record in result_pages:
            body = page_record.get("result") if isinstance(page_record, dict) else None
            if not isinstance(body, dict):
                compact_pages.append(page_record)
                continue
            drawing = body.get("drawing")
            sheet = drawing.get("sheet") if isinstance(drawing, dict) else None
            compact_body = {key: body[key] for key in (
                "success", "available", "complete", "coordinate_space", "coordinate_unit", "error_code",
                "sources", "source_mesh", "context", "observed_counts") if key in body}
            if isinstance(sheet, dict):
                compact_body["sheet"] = {key: sheet[key] for key in (
                    "scale_denominator", "scale_mode", "paper", "counts") if key in sheet}
            compact_pages.append({key: page_record[key] for key in (
                "page_number", "title", "drawing_number", "requested_targets", "complete", "drawing_complete", "error_code")
                if key in page_record} | {"result": compact_body})
        metadata["ok"] = False
        metadata["result"].update(success=False, available=False, complete=False,
                                   error_code="multi_page_output_limit_exceeded", pages=compact_pages,
                                   output_omitted="Page images and vector fields omitted; source and context evidence retained.")
        return MultiImageToolResult([], metadata)
    return MultiImageToolResult(image_payloads, metadata)


def _section_drawing(section: dict[str, Any], mesh: dict[str, Any], *, hatching: dict[str, Any] | None = None,
                     include_plain_svg: bool = True,
                     linear_dimensions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Adapt section contours to the shared linework/rendering contract."""
    points = section["vertices"]
    lines = [{"kind": "silhouette", "visibility": "visible",
              "start": list(points[segment["vertices"][0]]), "end": list(points[segment["vertices"][1]]),
              "section_segment_index": index, "source_triangles": list(segment["source_triangles"])}
             for index, segment in enumerate(section["segments"])]
    for line, segment in zip(lines, section["segments"]):
        line.update({key: segment[key] for key in ("source_index", "refno", "source_triangle_indices")
                     if key in segment})
    if not lines:
        raise ValueError("nonempty_section_geometry_required")
    if hatching is not None:
        lines.extend({"kind": "hatch", "visibility": "visible", "start": list(line["start"]),
                      "end": list(line["end"]), "hatch_segment_index": index}
                     for index, line in enumerate(hatching["lines"]))
    plane = section["plane"]
    bounds = section["bounds"]
    view = {
        "name": "SECTION", "bounds": bounds,
        "dimensions": {"width": bounds["max"][0] - bounds["min"][0],
                       "height": bounds["max"][1] - bounds["min"][1], "unit": "mm"},
        "coordinate_unit": "mm", "coordinate_frame": "section plane coordinates; millimetres",
        "horizontal_axis": plane["right"], "vertical_axis": plane["up"], "toward_viewer": plane["normal"],
        "origin": plane["origin"], "lines": lines, "line_count": len(lines),
    }
    if linear_dimensions is not None:
        view["linear_dimensions"] = linear_dimensions
    footer = ("Parameter-mesh section contours; no CSG cuts, hatching or hole semantics." if hatching is None else
              "Parameter-mesh section with nonzero contour fill; no native CSG or hole semantics.")
    # A sheet replaces the ordinary SVG with its paper-space rendering. Do not
    # reject a returned format because an unused intermediate has less precision.
    svg = _make_svg([view], footer=footer) if include_plain_svg else None
    vertices = mesh["vertices"]
    return {
        "success": True, "available": True, "complete": True,
        "drawing_kind": "parameter-derived plane section contours", "plane": plane,
        "coordinate_unit": "mm", "coordinate_frame": view["coordinate_frame"],
        "source_coordinate_frame": mesh.get("coordinate_frame"),
        "source_bounds": {"min": [min(point[axis] for point in vertices) for axis in range(3)],
                          "max": [max(point[axis] for point in vertices) for axis in range(3)],
                          "dimensions": [max(point[axis] for point in vertices) - min(point[axis] for point in vertices)
                                         for axis in range(3)]},
        "native_tessellation": False, "cuts_applied": False, "hole_semantics_inferred": False,
        "hidden_line_removal": False, "occlusion_checked": False, "show_hidden_lines": False,
        "hatching_applied": hatching is not None, "retained_solid_projection": False,
        **({"hatching_fill_rule": "nonzero", "hatch_line_count": len(hatching["lines"])} if hatching is not None else {}),
        "views": [view], "segment_count": len(lines), "svg": svg,
    }


def _assembly_boundary_context(bridge: BridgeClient) -> dict[str, Any]:
    """Observe readiness and project identity, without claiming an atomic snapshot."""
    observed: dict[str, Any] = {"available": False}
    try:
        health = bridge.health()
        if not isinstance(health, dict):
            return {**observed, "error_code": "invalid_health_response"}
        state = health.get("state")
        state = state if isinstance(state, dict) else {}
        fault = state.get("native_database_fault")
        fault = fault if isinstance(fault, dict) else {}
        observed.update({
            "session_id": health.get("session_id"), "version": health.get("version"),
            "connected": health.get("connected"), "database_ready": state.get("database_ready"),
            "busy": state.get("busy"), "recovery_required": state.get("recovery_required"),
            "native_database_fault": fault,
        })
        if health.get("ok") is not True:
            observed["read_error"] = health
        if (health.get("ok") is not True or observed["connected"] is not True or
                observed["database_ready"] is not True or observed["busy"] is not False or
                observed["recovery_required"] is not False or fault.get("has_fault") is not False or
                any(not isinstance(observed[key], str) or not observed[key].strip()
                    for key in ("session_id", "version"))):
            return {**observed, "error_code": "native_session_not_ready"}
        response = bridge.project()
        project = response.get("project") if isinstance(response, dict) else None
        if not isinstance(response, dict) or response.get("ok") is not True:
            observed["read_error"] = response
        if not isinstance(project, dict):
            return {**observed, "error_code": "project_identity_unavailable"}
        observed["project"] = {key: project.get(key) for key in (
            "name", "mdb", "connected", "recovery_only")}
        if (response.get("ok") is not True or project.get("connected") is not True or
                project.get("recovery_only") is not False or
                response.get("version") != observed["version"] or
                any(not isinstance(project.get(key), str) or not project[key].strip() for key in ("name", "mdb"))):
            return {**observed, "error_code": "project_identity_unavailable"}
        return {**observed, "available": True}
    except Exception as exc:
        return {**observed, "error_code": "context_read_failed", "error_type": type(exc).__name__,
                "read_error": str(exc)[:1024],
                **({"native_diagnostics": exc.diagnostics} if isinstance(exc, BridgeError) else {})}


def _assembly_source_evidence(target: dict[str, str], response: Any, index: int) -> dict[str, Any]:
    row: dict[str, Any] = {
        "source_index": index, "requested_refno": target["refno"], "expected_type": target["expected_type"],
        "read_status": "returned", "success": False, "available": False, "complete": False,
    }
    if not isinstance(response, dict):
        return {**row, "error_code": "invalid_mesh_response"}
    if response.get("ok") is not True:
        return {**row, "error_code": response.get("error_code") or "native_mesh_read_failed",
                "read_error": response}
    result = response.get("result")
    if not isinstance(result, dict):
        return {**row, "error_code": response.get("error_code", "native_mesh_read_failed"),
                "read_error": response}
    mesh = result.get("derived_mesh")
    for key in ("resolved_refno", "resolved_type", "coordinate_space"):
        row[key] = result.get(key)
    if isinstance(result.get("source_geometry"), dict):
        row["source_geometry"] = _mesh_source_summary(result["source_geometry"])
    if isinstance(mesh, dict):
        row["source_mesh"] = {key: value for key, value in mesh.items()
                              if key not in {"vertices", "triangles", "triangle_normals"}}
    if (response.get("ok") is True and all(result.get(key) is True for key in (
            "success", "available", "complete")) and isinstance(mesh, dict) and
            all(mesh.get(key) is True for key in ("success", "available", "complete"))):
        return {**row, "success": True, "available": True, "complete": True}
    code = result.get("error_code")
    if not isinstance(code, str):
        errors = mesh.get("errors") if isinstance(mesh, dict) else None
        code = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], str) else "source_mesh_unavailable"
    return {**row, "error_code": code}


def _assembly_drawing_targets(args: dict[str, Any]) -> list[dict[str, str]]:
    raw_targets = args.get("targets")
    if not isinstance(raw_targets, list) or not 1 <= len(raw_targets) <= MAX_DRAWING_SOURCES:
        raise ValueError("targets must contain one to sixteen explicit elements")
    targets: list[dict[str, str]] = []
    seen_requests: set[str] = set()
    for index, raw in enumerate(raw_targets):
        if not isinstance(raw, dict) or set(raw) != {"refno", "expected_type"}:
            raise ValueError(f"targets[{index}] must contain only refno and expected_type")
        refno = _panel_reference(raw, "refno")
        if len(refno) > 1024:
            raise ValueError("Target references must not exceed 1024 characters")
        expected_type = _string(raw, "expected_type")
        if expected_type not in _ELEMENT_MESH_TYPES:
            raise ValueError("expected_type must be one of: " + ", ".join(_ELEMENT_MESH_TYPES))
        key = _drawing_reference_key(refno)
        if key in seen_requests:
            raise ValueError("targets must not contain duplicate references")
        seen_requests.add(key)
        targets.append({"refno": refno, "expected_type": expected_type})

    return targets


def _read_assembly_drawing_sources(bridge: BridgeClient, targets: list[dict[str, str]],
                                   options: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    sources: list[dict[str, Any]] = [
        {"source_index": index, "requested_refno": target["refno"], "expected_type": target["expected_type"],
         "read_status": "not_read", "success": False, "available": False, "complete": False}
        for index, target in enumerate(targets)]
    output: dict[str, Any] = {
        "success": False, "available": False, "complete": False, "coordinate_space": "WORLD",
        "coordinate_unit": "mm", "sources": sources, "source_mesh": None, "drawing": None,
        "read_consistency": "separate live geometry reads; boundary context observations only; no database lock or common snapshot",
        "mesh_source": "independent parameter-derived shells; not native tessellation or Boolean geometry",
        "context": {"before": None, "after": None, "boundary_agreement": None},
    }
    before = _assembly_boundary_context(bridge)
    output["context"]["before"] = before

    def failure(code: str) -> dict[str, Any]:
        output["error_code"] = code
        return {"ok": False, "version": before.get("version"), "result": output}

    if before.get("available") is not True:
        return None, failure("assembly_context_unavailable")

    resolved: set[str] = set()
    mesh_results: list[dict[str, Any]] = []
    vertex_count = triangle_count = 0
    for index, target in enumerate(targets):
        read_args: dict[str, Any] = {**target, "coordinate_space": "WORLD", "mesh_detail": "full"}
        if target["expected_type"] in {"CYLI", "CONE", "DISH"}:
            read_args["radial_segments"] = options["radial_segments"]
        try:
            response = _element_mesh(bridge, read_args)
        except Exception as exc:
            sources[index] = {**sources[index], "read_status": "failed", "error_code": "native_mesh_read_failed",
                              "error_type": type(exc).__name__, "read_error": str(exc)[:1024],
                              **({"native_diagnostics": exc.diagnostics} if isinstance(exc, BridgeError) else {})}
            return None, failure("assembly_source_unavailable")
        row = _assembly_source_evidence(target, response, index)
        sources[index] = row
        if row.get("success") is not True:
            return None, failure("assembly_source_unavailable")
        if response.get("version") != before["version"]:
            row.update(success=False, available=False, complete=False, error_code="source_version_mismatch")
            return None, failure("assembly_source_unavailable")
        key = _drawing_reference_key(row["resolved_refno"])
        if key in resolved:
            row.update(success=False, available=False, complete=False, error_code="duplicate_drawing_source")
            return None, failure("duplicate_drawing_source")
        resolved.add(key)
        result = response["result"]
        mesh = result["derived_mesh"]
        vertex_count += len(mesh["vertices"])
        triangle_count += len(mesh["triangles"])
        if vertex_count > MAX_VERTICES or triangle_count > MAX_TRIANGLES:
            output["observed_counts"] = {"sources": index + 1, "vertices": vertex_count, "triangles": triangle_count}
            return None, failure("drawing_assembly_mesh_limit_exceeded")
        mesh_results.append(result)

    after = _assembly_boundary_context(bridge)
    output["context"]["after"] = after
    if after.get("available") is not True:
        return None, failure("assembly_context_unavailable")
    agreement = all(before[key] == after[key] for key in ("session_id", "version", "project"))
    output["context"]["boundary_agreement"] = agreement
    if not agreement:
        return None, failure("assembly_context_changed")

    combined = combine_drawing_meshes(mesh_results)
    output["source_mesh"] = {key: value for key, value in combined.items() if key not in {"vertices", "triangles"}}
    if combined.get("success") is not True:
        output["complete"] = combined.get("complete") is True
        return None, failure(combined.get("error_code", "assembly_mesh_unavailable"))
    for row, ranges in zip(sources, combined["source_ranges"]):
        row["mesh_range"] = ranges
    return combined, {"ok": True, "version": before["version"], "result": output}


_MATERIAL_ASSEMBLY_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
_MATERIAL_TILES_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
_MATERIAL_TILES_MAX_PNG_BYTES = 8 * 1024 * 1024
_MATERIAL_TILES_MAX_PREVIEW_PIXELS = 8_000_000


def _project_material_assembly_tiles(bridge: BridgeClient, args: dict[str, Any]) -> MultiImageToolResult:
    allowed = {"targets", "view", "sheet", "tiling", "source_detail", "radial_segments", "feature_angle_degrees",
               "hidden_line_removal", "show_hidden_lines", "include_svg", "include_dxf", "preview_width", "preview_height"}
    if set(args) - allowed:
        raise ValueError("Unknown material assembly tiles arguments")
    from .material_assembly_workflow import normalize_material_targets
    from .material_assembly_drawing import combine_material_drawing_meshes, generate_material_assembly_drawing
    from .material_drawing_tiles import normalize_material_tiling_options, plan_and_clip_material_tiles
    from .material_tile_sheet import build_material_tile_sheet

    targets = normalize_material_targets(args.get("targets"))
    view = args.get("view", "TOP")
    if type(view) is not str or view not in {"FRONT", "TOP", "SIDE"}:
        raise ValueError("view must be FRONT, TOP or SIDE")
    sheet_options = normalize_sheet_options(args.get("sheet"))
    if "scale_denominator" not in sheet_options:
        raise ValueError("material_tiles_require_explicit_scale")
    if "projection_method" in sheet_options:
        raise ValueError("material_tiles_single_view_has_no_projection_method")
    tiling = normalize_material_tiling_options(args.get("tiling"))
    short_side, long_side = (297.0, 420.0) if sheet_options["paper_size"] == "A3" else (210.0, 297.0)
    paper_width, paper_height = ((long_side, short_side) if sheet_options["orientation"] == "landscape"
                                 else (short_side, long_side))
    if tiling["overlap_mm"] * 2 >= min(paper_width - 50, paper_height - 94):
        raise ValueError("material_tiles_overlap_exceeds_viewport_limit")
    options = _drawing_options({"preview_width": 1400, "preview_height": 990, **args, "views": [view]})
    source_detail = args.get("source_detail", "summary")
    if type(source_detail) is not str or source_detail not in {"summary", "full"}:
        raise ValueError("source_detail must be summary or full")
    base = {"success": False, "available": False, "complete": False, "atomic_snapshot": False,
            "coordinate_space": "WORLD", "coordinate_unit": "mm", "native_tessellation": False,
            "native_cut_application_verified": False, "boolean_union_applied": False,
            "drawing": None, "combined_mesh": None, "projection": None, "manifest": None,
            "pages": [], "page_count": 0, "pages_generated": False, "view": view}
    version = None

    def finish(output, images=None, png_bytes=0):
        images = images or []
        metadata = {"ok": output.get("success") is True, "version": version, "result": output}
        try:
            size = len(json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError, OverflowError, UnicodeError) as exc:
            output = {**_primitive_csg_safe_evidence(output), **base,
                      "error_code": output.get("error_code") or "material_assembly_tiles_output_not_serializable",
                      "serialization_error_type": type(exc).__name__, "diagnostic_values_marked": True}
            metadata = {"ok": False, "version": version, "result": output}
            images = []
            size = len(json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
        if size > _MATERIAL_TILES_MAX_OUTPUT_BYTES or png_bytes > _MATERIAL_TILES_MAX_PNG_BYTES:
            metadata = {"ok": False, "version": version, "result": {
                **base, "error_code": "material_assembly_tiles_output_limit_exceeded", "observed_json_bytes": size,
                "observed_png_bytes": png_bytes, "original_error_code": output.get("error_code"),
                "output_omitted": "Source receipts, projections and pages omitted because the output budget was exceeded."}}
            images = []
        return MultiImageToolResult(images, metadata)

    read = _read_material_sources(bridge, targets, options["radial_segments"])
    output = {**base, **{key: value for key, value in read.items() if key != "mesh_sources"}, "source_detail": source_detail}
    output["sources"] = [{**{key: value for key, value in row.items() if key != "raw_receipts"},
        "raw_receipt_count": len(row.get("raw_receipts", [])), "raw_receipts_included": source_detail == "full",
        **({"raw_receipts": row.get("raw_receipts", [])} if source_detail == "full" else {})} for row in read.get("sources", [])]
    before = read.get("context", {}).get("before")
    version = before.get("version") if isinstance(before, dict) else None
    if any(read.get(flag) is not True for flag in ("success", "available", "complete")):
        return finish(output)
    failed_page_number = None
    try:
        combined = combine_material_drawing_meshes(read["mesh_sources"])
        if any(combined.get(flag) is not True for flag in ("success", "available", "complete")):
            output.update(success=False, available=False, complete=False,
                error_code=combined.get("error_code", "material_assembly_combination_failed"), combination_diagnostics=combined)
            return finish(output)
        output["combined_mesh"] = {key: value for key, value in combined.items() if key not in {"vertices", "triangles"}}
        output["combined_mesh"]["geometry_arrays_included"] = False
        projection = generate_material_assembly_drawing(combined, views=[view], projection_convention="right_handed",
            feature_angle_degrees=options["feature_angle_degrees"], hidden_line_removal=options["hidden_line_removal"],
            show_hidden_lines=options["show_hidden_lines"])
        if any(projection.get(flag) is not True for flag in ("success", "available", "complete")):
            output.update(success=False, available=False, complete=False,
                error_code=projection.get("error_code", "material_assembly_projection_failed"), projection_diagnostics=projection)
            return finish(output)
        output.update(projection={key: value for key, value in projection.items() if key not in {"svg", "dxf"}},
                      empty=projection.get("empty"), cuts_applied=combined.get("cuts_applied"),
                      source_parameter_boolean_evaluated=combined.get("source_parameter_boolean_evaluated"))
        if projection.get("empty") is True:
            output["image_omitted"] = "All requested source material boundaries are empty."
            return finish(output)
        manifest = plan_and_clip_material_tiles(projection, sheet_options, tiling)
        planned_pages = manifest["pages"]
        pixels = options["preview_width"] * options["preview_height"] * len(planned_pages)
        if pixels > _MATERIAL_TILES_MAX_PREVIEW_PIXELS:
            output.update(success=False, available=False, complete=False,
                          error_code="material_assembly_tiles_preview_limit_exceeded", planned_page_count=len(planned_pages),
                          observed_preview_pixels=pixels, preview_pixel_limit=_MATERIAL_TILES_MAX_PREVIEW_PIXELS)
            return finish(output)
        pages, images, png_bytes = [], [], 0
        for page in planned_pages:
            failed_page_number = page["page_number"]
            scene = build_material_tile_sheet(projection, manifest, page, sheet_options)
            rendered = {**{key: value for key, value in page.items() if key != "lines"}, "lines_in_manifest": True, "sheet": scene,
                        "vector_serialization": {"coordinate_decimal_places": 15, "unit": "paper mm", "engineering_accuracy_claimed": False}}
            if options["include_svg"]:
                rendered["svg"] = render_drawing_sheet_svg(scene, coordinate_decimal_places=15)
            if options["include_dxf"]:
                rendered["dxf"] = export_drawing_sheet_dxf(scene, coordinate_decimal_places=15)
            png = rasterize_drawing_sheet_png(scene, width=options["preview_width"], height=options["preview_height"])
            png_bytes += len(png)
            if png_bytes > _MATERIAL_TILES_MAX_PNG_BYTES:
                output.update(success=False, available=False, complete=False,
                              error_code="material_assembly_tiles_output_limit_exceeded", observed_png_bytes=png_bytes)
                return finish(output, png_bytes=png_bytes)
            pages.append(rendered)
            images.append((f"Page {page['page_number']} — {view}", base64.b64encode(png).decode("ascii"), "image/png"))
        output.update(manifest=manifest, pages=pages, page_count=len(pages), pages_generated=True)
    except Exception as exc:
        output.update(success=False, available=False, complete=False, pages=[], manifest=None,
                      page_count=0, pages_generated=False, error_code="material_assembly_tiles_failed",
                      tile_error=str(exc)[:1024], error_type=type(exc).__name__, failed_page_number=failed_page_number)
        return finish(output)
    return finish(output, images, png_bytes)


def _project_material_assembly_sheet(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any] | ImageToolResult:
    return _project_material_assembly_drawing(bridge, args, sheet_requested=True)


def _read_material_sources(bridge: BridgeClient, targets: list[dict[str, Any]], radial_segments: int,
                            *, chord_tolerance_mm=None, use_generated_dishes=False) -> dict[str, Any]:
    """Share the verified source workflow without rendering or rereading per page."""
    from .material_assembly_workflow import read_material_assembly_sources

    def parameter_reader(target, segments):
        if use_generated_dishes and target["expected_type"] == "DISH":
            from .generated_dish_workflow import read_generated_dish_mesh
            generated_tolerance = (chord_tolerance_mm
                if chord_tolerance_mm is not None
                else _GENERATED_DISH_DEFAULT_CHORD_TOLERANCE_MM)
            return read_generated_dish_mesh(bridge, target["refno"], radial_segments=segments,
                chord_tolerance_mm=generated_tolerance)
        read_args = {"refno": target["refno"], "expected_type": target["expected_type"],
                     "coordinate_space": "WORLD", "mesh_detail": "full"}
        if target["expected_type"] in {"CYLI", "CONE", "DISH"}:
            read_args["radial_segments"] = segments
        if chord_tolerance_mm is not None and target["expected_type"] != "PANE":
            read_args["chord_tolerance_mm"] = chord_tolerance_mm
        return _element_mesh(bridge, read_args)

    def difference_reader(target, segments):
        return _primitive_csg_operation(bridge, {"refno": target["refno"], "radial_segments": segments,
            "include_native_volume": False, "mesh_detail": "full",
            **({"chord_tolerance_mm": chord_tolerance_mm} if chord_tolerance_mm is not None else {})}, drawing_requested=False)

    return read_material_assembly_sources(targets, radial_segments=radial_segments,
        context_reader=lambda: _assembly_boundary_context(bridge),
        parameter_mesh_reader=parameter_reader, csg_mesh_reader=difference_reader)


def _project_material_assembly_drawing(bridge: BridgeClient, args: dict[str, Any], *,
                                       sheet_requested: bool = False) -> dict[str, Any] | ImageToolResult:
    allowed = {"targets", "views", "feature_angle_degrees", "hidden_line_removal", "show_hidden_lines", "source_detail",
               "include_svg", "include_dxf", "preview_width", "preview_height", "radial_segments"}
    if sheet_requested:
        allowed = (allowed - {"views"}) | {"sheet"}
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"Unknown material assembly arguments: {', '.join(sorted(unknown))}")
    from .material_assembly_workflow import normalize_material_targets
    from .material_assembly_drawing import combine_material_drawing_meshes, generate_material_assembly_drawing
    targets = normalize_material_targets(args.get("targets"))
    if sheet_requested:
        sheet_options = normalize_sheet_options(args.get("sheet"))
        sheet_options.setdefault("projection_method", "third_angle")
        options = _drawing_options({**args, "sheet": sheet_options, "views": ["FRONT", "TOP", "SIDE"]})
    else:
        options = _drawing_options(args)
    source_detail = args.get("source_detail", "summary")
    if type(source_detail) is not str or source_detail not in {"summary", "full"}:
        raise ValueError("source_detail must be summary or full")
    base = {"success": False, "available": False, "complete": False, "atomic_snapshot": False,
            "coordinate_space": "WORLD", "coordinate_unit": "mm", "native_tessellation": False,
            "native_cut_application_verified": False, "boolean_union_applied": False,
            "drawing": None, "combined_mesh": None}
    if sheet_requested:
        base.update(sheet_requested=True, sheet_generated=False)
    version = None

    def finish(output: dict[str, Any], png: bytes | None = None):
        metadata = {"ok": output.get("success") is True, "version": version, "result": output}
        try:
            size = len(json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError, OverflowError, UnicodeError) as exc:
            output = {**_primitive_csg_safe_evidence(output), **base,
                      "error_code": output.get("error_code") or "material_assembly_output_not_serializable",
                      "diagnostic_values_marked": True, "serialization_error_type": type(exc).__name__}
            metadata = {"ok": False, "version": version, "result": output}
            png = None
            size = len(json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
        if size > _MATERIAL_ASSEMBLY_MAX_OUTPUT_BYTES or png is not None and len(png) > _MATERIAL_ASSEMBLY_MAX_OUTPUT_BYTES:
            return {"ok": False, "version": version, "result": {
                **base, "error_code": "material_assembly_output_limit_exceeded", "observed_json_bytes": size,
                "original_error_code": output.get("error_code"),
                "output_omitted": "Source receipts, geometry and drawing omitted because the output budget was exceeded."}}
        if png is not None:
            return ImageToolResult(data=base64.b64encode(png).decode("ascii"), mime_type="image/png", metadata=metadata)
        return metadata

    read = _read_material_sources(bridge, targets, options["radial_segments"])
    mesh_sources = read.get("mesh_sources", [])
    output = {**base, **{key: value for key, value in read.items() if key != "mesh_sources"}}
    output["source_detail"] = source_detail
    output["sources"] = [{**{key: value for key, value in row.items() if key != "raw_receipts"},
                           "raw_receipt_count": len(row.get("raw_receipts", [])),
                           "raw_receipts_included": source_detail == "full",
                           **({"raw_receipts": row.get("raw_receipts", [])} if source_detail == "full" else {})}
                          for row in read.get("sources", [])]
    before = read.get("context", {}).get("before")
    version = before.get("version") if isinstance(before, dict) else None
    if any(read.get(flag) is not True for flag in ("success", "available", "complete")):
        return finish(output)
    try:
        combined = combine_material_drawing_meshes(mesh_sources)
        if any(combined.get(flag) is not True for flag in ("success", "available", "complete")):
            output.update(success=False, available=False, complete=False,
                          error_code=combined.get("error_code", "material_assembly_combination_failed"),
                          combination_diagnostics=combined)
            return finish(output)
        output["combined_mesh"] = {key: value for key, value in combined.items() if key not in {"vertices", "triangles"}}
        output["combined_mesh"].update(geometry_arrays_included=False)
        drawing = generate_material_assembly_drawing(combined, **_drawing_projection_options(options))
        if any(drawing.get(flag) is not True for flag in ("success", "available", "complete")):
            output.update(success=False, available=False, complete=False,
                          error_code=drawing.get("error_code", "material_assembly_projection_failed"),
                          drawing_diagnostics=drawing)
            return finish(output)
        output.update(empty=drawing.get("empty"), cuts_applied=combined.get("cuts_applied"),
                      source_parameter_boolean_evaluated=combined.get("source_parameter_boolean_evaluated"))
        if drawing.get("empty") is True:
            output.update(drawing=drawing, image_omitted="All requested source material boundaries are empty.")
            return finish(output)
        exported = {key: value for key, value in drawing.items() if key not in {"svg", "dxf"}}
        if sheet_requested:
            from .material_assembly_sheet import build_material_assembly_sheet
            scene = build_material_assembly_sheet(drawing, options["sheet"])
            exported["sheet"] = scene
            if options["include_svg"]:
                exported["svg"] = render_drawing_sheet_svg(scene, coordinate_decimal_places=15)
            if options["include_dxf"]:
                exported["dxf"] = export_drawing_sheet_dxf(scene, coordinate_decimal_places=15)
                exported["dxf_serialization"] = {
                    "coordinate_decimal_places": 15, "unit": "paper mm", "engineering_accuracy_claimed": False}
            png = rasterize_drawing_sheet_png(scene, width=options["preview_width"], height=options["preview_height"])
        else:
            if options["include_svg"]:
                exported["svg"] = drawing["svg"]
            if options["include_dxf"]:
                exported["dxf"] = export_orthographic_dxf(
                    drawing, footer=drawing["footer"], coordinate_decimal_places=12)
                exported["dxf_serialization"] = {
                    "coordinate_decimal_places": 12,
                    "unit": "mm",
                    "engineering_accuracy_claimed": False,
                }
            png = rasterize_orthographic_png(drawing["views"], width=options["preview_width"],
                                             height=options["preview_height"], footer=drawing["footer"])
        output["drawing"] = exported
        if sheet_requested:
            output["sheet_generated"] = True
    except Exception as exc:
        output.update(success=False, available=False, complete=False, drawing=None,
                      error_code="material_assembly_sheet_failed" if sheet_requested else "material_assembly_drawing_failed", error_type=type(exc).__name__,
                      drawing_error=str(exc)[:1024])
        return finish(output)
    return finish(output, png)


def _project_assembly_drawing(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any] | ImageToolResult:
    allowed = {"targets", "coordinate_space", "views", "feature_angle_degrees", "hidden_line_removal",
               "show_hidden_lines", "include_svg", "include_dxf", "preview_width", "preview_height", "radial_segments",
               "sheet"}
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"Unknown e3d_project_assembly_drawing arguments: {', '.join(sorted(unknown))}")
    options = _drawing_options(args)
    if _string(args, "coordinate_space", "WORLD") != "WORLD":
        raise ValueError("Assembly drawing requires coordinate_space=WORLD")
    targets = _assembly_drawing_targets(args)

    combined, response = _read_assembly_drawing_sources(bridge, targets, options)
    if combined is None:
        return response
    output = response["result"]
    drawing = generate_orthographic_drawing(
        combined["vertices"], combined["triangles"], coordinate_frame=WORLD_FRAME, coordinate_unit="mm",
        **_drawing_projection_options(options))
    output.update(success=drawing.get("success") is True, available=drawing.get("available") is True,
                  complete=drawing.get("complete") is True)
    if drawing.get("success") is not True:
        output["drawing_diagnostics"] = {key: value for key, value in drawing.items() if key not in {"views", "svg"}}
        return {**response, "ok": False, "result": {**output, "error_code": drawing.get("error_code", "drawing_unavailable")}}
    try:
        output["drawing"], png = _render_drawing_outputs(drawing, options)
    except ValueError as exc:
        output.update(success=False, available=False, complete=False, drawing=None,
                      export_error=str(exc))
        return {**response, "ok": False, "result": {**output, "error_code": "drawing_export_failed"}}
    return ImageToolResult(data=base64.b64encode(png).decode("ascii"), mime_type="image/png",
                           metadata={**response, "result": output})


def _section_assembly_drawing(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any] | ImageToolResult:
    allowed = {"targets", "coordinate_space", "plane", "hatch", "dimensions", "sheet", "include_svg", "include_dxf",
               "preview_width", "preview_height", "radial_segments"}
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"Unknown e3d_section_assembly_drawing arguments: {', '.join(sorted(unknown))}")
    normalize_section_plane(args.get("plane"))
    options = _drawing_options(args, allow_projection_method=False)
    hatch_options = normalize_section_hatch(args["hatch"]) if "hatch" in args else None
    if "dimensions" in args:
        normalize_section_dimensions(args["dimensions"])
        if options["sheet"] is None:
            raise ValueError("section_dimensions_require_sheet")
    if _string(args, "coordinate_space", "WORLD") != "WORLD":
        raise ValueError("Assembly drawing requires coordinate_space=WORLD")
    targets = _assembly_drawing_targets(args)
    combined, response = _read_assembly_drawing_sources(bridge, targets, options)
    response["result"]["section"] = None
    response["result"].update(boolean_union_applied=False, vertices_welded=False,
                              geometric_self_intersection_checked=False,
                              section_scope="Independent source contours overlaid in a shared WORLD plane; no Boolean material boundary")
    if combined is None:
        return response
    return _finish_section_drawing(response, combined, args, options, hatch_options, "WORLD", assembly=True)


def _compose_drawing_sheet(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any] | ImageToolResult:
    allowed = {"targets", "coordinate_space", "plane", "hatch", "dimensions", "sheet", "include_svg",
               "include_dxf", "preview_width", "preview_height", "radial_segments", "feature_angle_degrees",
               "hidden_line_removal", "show_hidden_lines"}
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"Unknown e3d_compose_drawing_sheet arguments: {', '.join(sorted(unknown))}")
    normalize_section_plane(args.get("plane"))
    options = _drawing_options(args)
    if options["sheet"] is None:
        raise ValueError("composition_requires_sheet")
    options["sheet"].setdefault("projection_method", "third_angle")
    hatch_options = normalize_section_hatch(args["hatch"]) if "hatch" in args else None
    if "dimensions" in args:
        normalize_section_dimensions(args["dimensions"])
    if _string(args, "coordinate_space", "WORLD") != "WORLD":
        raise ValueError("Composition requires coordinate_space=WORLD")
    targets = _assembly_drawing_targets(args)
    combined, response = _read_assembly_drawing_sources(bridge, targets, options)
    output = response["result"]
    output.update(projection=None, section=None, boolean_union_applied=False, vertices_welded=False,
                  geometric_self_intersection_checked=False,
                  composition_scope="Whole-model orthographic projections plus an independent plane SECTION; not a retained-solid cut-away projection",
                  section_scope="Independent source contours overlaid in a shared WORLD plane; no Boolean material boundary")
    if combined is None:
        return response

    def failure(code: str, **diagnostics: Any) -> dict[str, Any]:
        output.update(success=False, available=False, complete=False, drawing=None,
                      error_code=code, **diagnostics)
        return {**response, "ok": False}

    try:
        projection = generate_orthographic_drawing(
            combined["vertices"], combined["triangles"], coordinate_frame=WORLD_FRAME, coordinate_unit="mm",
            **_drawing_projection_options(options))
    except Exception as exc:
        return failure("composition_projection_failed", projection_error=str(exc)[:1024],
                       error_type=type(exc).__name__)
    output["projection"] = {key: value for key, value in projection.items() if key not in {"svg", "dxf"}}
    if any(projection.get(flag) is not True for flag in ("success", "available", "complete")):
        return failure(projection.get("error_code", "composition_projection_failed"))
    try:
        prepared = _prepare_section_drawing(response, combined, args, hatch_options, "WORLD", assembly=True)
    except Exception as exc:
        return failure("composition_section_failed", section_error=str(exc)[:1024], error_type=type(exc).__name__)
    if prepared is None:
        return {**response, "ok": False}
    section, hatching, linear_dimensions = prepared
    if section.get("intersection") is not True:
        return failure("composition_section_no_intersection")
    try:
        section_drawing = _section_drawing(section, combined, hatching=hatching,
                                           linear_dimensions=linear_dimensions, include_plain_svg=False)
        drawing = {**output["projection"],
                   "drawing_kind": "parameter-derived orthographic and independent plane section sheet",
                   "sheet_composition": "orthographic_section", "section_plane": section["plane"],
                   "retained_solid_projection": False,
                   "hatching_applied": hatching is not None,
                   "views": [*projection["views"], *section_drawing["views"]],
                   "segment_count": projection["segment_count"] + section_drawing["segment_count"]}
        if hatching is not None:
            drawing.update(hatching_fill_rule="nonzero", hatch_line_count=len(hatching["lines"]))
        exported, png = _render_drawing_outputs(drawing, options)
    except Exception as exc:
        return failure("drawing_export_failed", export_error=str(exc)[:1024], error_type=type(exc).__name__)
    output.update(success=True, available=True, complete=True, drawing=exported)
    return ImageToolResult(data=base64.b64encode(png).decode("ascii"), mime_type="image/png",
                           metadata={**response, "result": output})


def _compare_element_meshes(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    allowed = {"first_refno", "first_expected_type", "second_refno", "second_expected_type",
               "coordinate_space", "radial_segments"}
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"Unknown e3d_compare_element_meshes arguments: {', '.join(sorted(unknown))}")

    coordinate_space = _string(args, "coordinate_space", "WORLD")
    if coordinate_space not in ("local", "WORLD"):
        raise ValueError("coordinate_space must be local or WORLD")
    radial_segments = _integer(args, "radial_segments", 48, 12, 99)
    targets = []
    for prefix in ("first", "second"):
        refno = _panel_reference(args, f"{prefix}_refno")
        expected_type = _string(args, f"{prefix}_expected_type")
        if expected_type not in _ELEMENT_MESH_TYPES:
            raise ValueError(f"{prefix}_expected_type must be one of: " + ", ".join(_ELEMENT_MESH_TYPES))
        targets.append((prefix, refno, expected_type))

    # Validate all inputs before the first native read. Each explicitly requested
    # target then gets exactly one call through the existing guarded mesh reader.
    observed = []
    for prefix, refno, expected_type in targets:
        read_args: dict[str, Any] = {
            "refno": refno,
            "expected_type": expected_type,
            "coordinate_space": coordinate_space,
            "mesh_detail": "full",
        }
        if expected_type in {"CYLI", "CONE", "DISH"}:
            read_args["radial_segments"] = radial_segments
        try:
            response = _element_mesh(bridge, read_args)
            observed.append(_compare_mesh_target(prefix, refno, expected_type, coordinate_space, response))
        except Exception as exc:
            observed.append({
                "target": prefix, "requested_refno": refno, "expected_type": expected_type,
                "coordinate_space": coordinate_space, "success": False, "complete": False,
                "errors": [{"code": "native_mesh_read_failed", "error_type": type(exc).__name__}],
                "_vertices": None, "_triangles": None, "_volume_mm3": None,
            })

    summaries = [{key: value for key, value in item.items() if not key.startswith("_")}
                 for item in observed]
    both_successful = len(observed) == 2 and all(item.get("success") is True and item.get("complete") is True
                                                for item in observed)
    comparison = None
    if both_successful:
        first, second = observed
        first_bounds, second_bounds = first["bounds_mm"], second["bounds_mm"]
        first_min, first_max = first_bounds["min"], first_bounds["max"]
        second_min, second_max = second_bounds["min"], second_bounds["max"]
        topology_comparable = (
            isinstance(first.get("_vertices"), list) and isinstance(second.get("_vertices"), list) and
            len(first["_vertices"]) == len(second["_vertices"]) and
            isinstance(first.get("_triangles"), list) and isinstance(second.get("_triangles"), list) and
            first["_triangles"] == second["_triangles"]
        )
        comparison = {
            "coordinate_space": coordinate_space,
            "bounds_mm": {
                "first": first_bounds,
                "second": second_bounds,
                "delta_second_minus_first": _mesh_bounds_delta(first_min, first_max, second_min, second_max),
                "delta_frame_scope": "common_WORLD_frame" if coordinate_space == "WORLD" else
                "numeric_delta_between_element_local_frames; not a common spatial displacement",
            },
            "surface_area_mm2": {
                "first": first["surface_area_mm2"], "second": second["surface_area_mm2"],
                "delta_second_minus_first": _finite_mesh_delta(second["surface_area_mm2"],
                                                                 first["surface_area_mm2"]),
            },
            "volume_mm3": _compare_mesh_volumes(first, second),
            "index_topology_comparable": topology_comparable,
        }
        if topology_comparable and coordinate_space == "WORLD":
            displacement = _indexed_vertex_displacement(first["_vertices"], second["_vertices"])
            comparison["indexed_vertex_displacement_mm"] = displacement
        elif topology_comparable:
            comparison["indexed_vertex_displacement_unavailable_reason"] = "element_local_frames_are_not_common"

    errors = []
    for summary in summaries:
        if summary.get("success") is not True:
            errors.append({"target": summary["target"], "errors": summary["errors"]})
    try:
        version = bridge.settings.versions()[0]
    except Exception:
        version = None
    return {
        "ok": both_successful,
        "version": version,
        "result": {
            "success": both_successful,
            "available": both_successful,
            "complete": both_successful,
            "coordinate_space": coordinate_space,
            "read_consistency": "two separate native geometry observations; no database lock or common snapshot",
            "mesh_source": "parameter-derived; not native tessellation or Boolean geometry",
            "targets": summaries,
            "comparison": comparison,
            "errors": errors,
        },
    }


def _compare_mesh_target(prefix: str, refno: str, expected_type: str, coordinate_space: str,
                         response: Any) -> dict[str, Any]:
    base = {"target": prefix, "requested_refno": refno, "expected_type": expected_type,
            "coordinate_space": coordinate_space, "success": False, "complete": False, "errors": []}
    if not isinstance(response, dict) or response.get("ok") is not True:
        error_code = response.get("error_code") if isinstance(response, dict) else None
        error_message = response.get("error") if isinstance(response, dict) else None
        return {**base, "errors": [{"code": error_code or "native_mesh_read_failed",
                                    **({"message": str(error_message)[:256]} if error_message else {})}],
                "_vertices": None, "_triangles": None, "_volume_mm3": None}

    result = response.get("result")
    if not isinstance(result, dict):
        return {**base, "errors": [{"code": "invalid_mesh_result"}],
                "_vertices": None, "_triangles": None, "_volume_mm3": None}
    if result.get("success") is not True or result.get("available") is not True or result.get("complete") is not True:
        code = result.get("error_code")
        if not isinstance(code, str):
            mesh = result.get("derived_mesh")
            mesh_errors = mesh.get("errors") if isinstance(mesh, dict) else None
            code = mesh_errors[0] if isinstance(mesh_errors, list) and mesh_errors and isinstance(mesh_errors[0], str) \
                else "native_mesh_unavailable"
        return {**base, "resolved_refno": result.get("resolved_refno"),
                "resolved_type": result.get("resolved_type"), "errors": [{"code": code}],
                "_vertices": None, "_triangles": None, "_volume_mm3": None}

    mesh = result.get("derived_mesh")
    if not isinstance(mesh, dict) or mesh.get("success") is not True or mesh.get("available") is not True or \
            mesh.get("complete") is not True:
        mesh_errors = mesh.get("errors") if isinstance(mesh, dict) else None
        code = mesh_errors[0] if isinstance(mesh_errors, list) and mesh_errors and isinstance(mesh_errors[0], str) \
            else "derived_mesh_unavailable"
        return {**base, "resolved_refno": result.get("resolved_refno"),
                "resolved_type": result.get("resolved_type"), "errors": [{"code": code}],
                "_vertices": None, "_triangles": None, "_volume_mm3": None}

    metrics = measure_triangle_mesh(mesh.get("vertices"), mesh.get("triangles"))
    if metrics.get("available") is not True or metrics.get("complete") is not True or \
            not isinstance(metrics.get("bounds"), dict) or not isinstance(metrics.get("surface_area"), (int, float)):
        return {**base, "resolved_refno": result.get("resolved_refno"),
                "resolved_type": result.get("resolved_type"), "errors": [{"code": "mesh_metrics_unavailable"}],
                "_vertices": None, "_triangles": None, "_volume_mm3": None}

    validation = metrics.get("validation")
    components = validation.get("components") if isinstance(validation, dict) else None
    checks = validation.get("checks") if isinstance(validation, dict) else None
    volume = None
    if isinstance(components, list) and len(components) == 1 and isinstance(components[0], dict) and \
            components[0].get("closed") is True and components[0].get("winding_consistent") is True and \
            components[0].get("orientation") == "outward" and components[0].get("volume_available") is True and \
            isinstance(components[0].get("signed_volume"), (int, float)) and \
            isinstance(checks, dict) and checks.get("closed_manifold") is True and \
            checks.get("orientation_outward") is True and checks.get("vertex_manifold") is True and \
            checks.get("winding_consistent") is True:
        volume = components[0]["signed_volume"]

    return {
        **base, "success": True, "complete": True,
        "resolved_refno": result.get("resolved_refno"), "resolved_type": result.get("resolved_type"),
        "vertex_count": len(mesh["vertices"]) if isinstance(mesh.get("vertices"), list) else None,
        "triangle_count": len(mesh["triangles"]) if isinstance(mesh.get("triangles"), list) else None,
        "bounds_mm": metrics["bounds"], "surface_area_mm2": metrics["surface_area"],
        "volume_mm3": volume, "volume_available": volume is not None,
        "_vertices": mesh.get("vertices"), "_triangles": mesh.get("triangles"), "_volume_mm3": volume,
    }


def _compare_mesh_volumes(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    first_volume, second_volume = first.get("_volume_mm3"), second.get("_volume_mm3")
    if not isinstance(first_volume, (int, float)) or not isinstance(second_volume, (int, float)):
        return {"available": False, "reason": "each mesh must validate as one outward closed component"}
    return {"available": True, "first": first_volume, "second": second_volume,
            "delta_second_minus_first": _finite_mesh_delta(second_volume, first_volume)}


def _finite_mesh_delta(second: float, first: float) -> float | None:
    delta = second - first
    return delta if math.isfinite(delta) else None


def _mesh_bounds_delta(first_min: list[float], first_max: list[float],
                       second_min: list[float], second_max: list[float]) -> dict[str, Any]:
    minimum = [_finite_mesh_delta(second_min[index], first_min[index]) for index in range(3)]
    maximum = [_finite_mesh_delta(second_max[index], first_max[index]) for index in range(3)]
    return {"min": minimum, "max": maximum,
            "complete": all(value is not None for value in (*minimum, *maximum))}


def _indexed_vertex_displacement(first: list[Any], second: list[Any]) -> dict[str, Any]:
    distances: list[float] = []
    for first_vertex, second_vertex in zip(first, second):
        if not isinstance(first_vertex, list) or not isinstance(second_vertex, list) or \
                len(first_vertex) != 3 or len(second_vertex) != 3:
            return {"available": False, "complete": False, "error": "invalid_vertex"}
        try:
            distance = math.hypot(*(float(second_vertex[axis]) - float(first_vertex[axis]) for axis in range(3)))
        except (ArithmeticError, TypeError, ValueError, OverflowError):
            return {"available": False, "complete": False, "error": "numeric_range"}
        if not math.isfinite(distance):
            return {"available": False, "complete": False, "error": "numeric_range"}
        distances.append(distance)
    if not distances:
        return {"available": False, "complete": False, "error": "empty_vertices"}
    maximum = max(distances)
    scale = maximum
    rms = 0.0 if scale == 0.0 else scale * math.sqrt(math.fsum((distance / scale) ** 2 for distance in distances)
                                                         / len(distances))
    if not math.isfinite(rms):
        return {"available": False, "complete": False, "error": "numeric_range"}
    return {"available": True, "complete": True, "max": maximum, "rms": rms}


def _primitive_dimensions(dimensions: Any) -> dict[str, float]:
    if not isinstance(dimensions, dict) or not 1 <= len(dimensions) <= 3:
        raise ValueError("dimensions must contain one to three numeric millimetre values")
    normalized: dict[str, float] = {}
    for key, value in dimensions.items():
        if not isinstance(key, str) or key.strip().upper() not in {"DIAM", "HEIG", "XLEN", "YLEN", "ZLEN", "DTOP", "DBOT", "RADI"}:
            raise ValueError("dimensions names must be DIAM, HEIG, XLEN, YLEN, ZLEN, DTOP, DBOT or RADI")
        name = key.strip().upper()
        if name in normalized:
            raise ValueError("Duplicate dimension names")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("dimensions must contain finite nonnegative numbers in millimetres")
        try:
            number = float(value)
        except (OverflowError, ValueError):
            raise ValueError("dimensions must contain finite nonnegative numbers in millimetres") from None
        if not math.isfinite(number) or number < 0:
            raise ValueError("dimensions must contain finite nonnegative numbers in millimetres")
        normalized[name] = number
    return normalized


def _update_primitive(bridge: BridgeClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    allowed = {"refno", "dimensions", "position", "orientation", "wrt", "expected_revision", "dry_run",
               "capture_after", "capture_width", "capture_height"}
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"Unknown e3d_update_primitive arguments: {', '.join(sorted(unknown))}")
    dry_run = _edit_preview(settings, args)
    refno = _panel_reference(args, "refno")
    options: dict[str, Any] = {}
    if "dimensions" in args:
        options["dimensions"] = _primitive_dimensions(args["dimensions"])
    position, x_axis, y_axis, wrt = _transform_args(args)
    if position is not None:
        options["position"] = position
    if x_axis is not None:
        options.update(x_axis=x_axis, y_axis=y_axis)
    if not options:
        raise ValueError("dimensions, position or orientation is required")
    if wrt.upper() in {"OWNER", "WORLD"}:
        wrt = wrt.upper()
    else:
        wrt = _panel_reference({"wrt": wrt}, "wrt")
    if "expected_revision" in args:
        revision = _string(args, "expected_revision")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", revision):
            raise ValueError("expected_revision must contain exactly 64 hexadecimal characters")
        options["expected_revision"] = revision.lower()
    elif not dry_run:
        raise ValueError("expected_revision from native inspection or preview is required for execution")
    return bridge.update_primitive(refno, wrt=wrt, dry_run=dry_run, **options)


def _set_transform(bridge: BridgeClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    dry_run = _edit_preview(settings, args)
    refno = _string(args, "refno").strip()
    if not refno:
        raise ValueError("refno must not be empty")
    values = _transform_args(args)
    if values[0] is None and values[1] is None:
        raise ValueError("position or orientation is required")
    options: dict[str, Any] = {}
    if "expected_revision" in args:
        revision = _string(args, "expected_revision")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", revision):
            raise ValueError("expected_revision must contain exactly 64 hexadecimal characters")
        options["expected_revision"] = revision.lower()
    elif not dry_run:
        raise ValueError("expected_revision from a fresh transform preview is required for execution")
    return bridge.set_transform(refno, *values, dry_run, **options)


def _capture_view(bridge: BridgeClient, args: dict[str, Any]) -> ImageToolResult | dict[str, Any]:
    response = bridge.capture_view(_integer(args, "width", 1280, 64, 2048), _integer(args, "height", 960, 64, 2048))
    if McpServer._operation_failed(response):
        return response
    result = dict(response.get("result", {}))
    encoded = result.pop("image_base64", None)
    if result.get("mime_type") != "image/png" or not isinstance(encoded, str) or len(encoded) > 32000000:
        raise ValueError("E3D did not return a valid PNG image")
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR":
        raise ValueError("E3D returned invalid PNG content")
    width = int.from_bytes(raw[16:20], "big")
    height = int.from_bytes(raw[20:24], "big")
    if not (64 <= width <= 2048 and 64 <= height <= 2048) or result.get("width") != width or result.get("height") != height:
        raise ValueError("E3D PNG dimensions do not match the capture metadata")
    return ImageToolResult(encoded, "image/png", {**response, "result": result})


def _status(bridge: BridgeClient, settings: Settings) -> dict[str, Any]:
    result = bridge.health()
    result.setdefault("version", settings.version or settings.default_version)
    result["mcp"] = {"mode": settings.mode, "commands_enabled": settings.allow_commands}
    if settings.install_root:
        result["install_root"] = settings.install_root
    return result


def _query(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    if "expression" not in args:
        raise ValueError("expression is required")
    expression = _string(args, "expression").strip()
    if not expression:
        raise ValueError("expression must not be empty")
    return bridge.query(expression, _integer(args, "limit", 50, 1, 500))


def _context_snapshot(bridge: BridgeClient, settings: Settings, arguments: dict[str, Any]) -> dict[str, Any]:
    if arguments:
        raise ValueError("e3d_context_snapshot takes no arguments")
    return context_snapshot(bridge, settings)


def _e3d_diagnosis_prompt(args: dict[str, str], version: str, status_uri: str, project_uri: str) -> str:
    refno = element_access_refnos([args.get("refno")])[0]
    if len(refno) > 256:
        raise ValueError("refno must not exceed 256 characters")
    symptom = args.get("symptom")
    if not isinstance(symptom, str) or not symptom.strip() or len(symptom) > 2000 or "\x00" in symptom:
        raise ValueError("symptom must be non-empty text of at most 2000 characters without NUL")
    symptom = symptom.strip()
    try:
        refno.encode("utf-8", errors="strict")
        symptom.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise ValueError("Prompt reference and symptom must contain valid Unicode") from None
    return (
        f"Diagnose the reported E3D element in version {version} using read-only MCP evidence.\n"
        f"Target reference (data): {json.dumps(refno, ensure_ascii=False)}\n"
        f"Reported symptom (data): {json.dumps(symptom, ensure_ascii=False)}\n\n"
        f"1. First read only {status_uri}. Continue only when connected=true, state.database_ready=true, busy=false, "
        "recovery_required=false, native_database_fault.has_fault=false and complete dialog/alert observations show no "
        "dialogs or alerts. Missing or unknown readiness fields require stopping. "
        f"Only after these checks pass, read {project_uri} and retain the session/project/MDB identity. "
        "If any check fails, stop and report it; do not dismiss, cancel, restart, or continue native reads.\n"
        "2. Use the exact target reference with e3d_query and e3d_element_snapshot. Inspect e3d_element_schema when "
        "attribute support or legal children matter. Do not substitute current CE, an owner, or a similarly named element.\n"
        "3. Select geometry reads only after confirming the native type: e3d_geometry_bounds or "
        "e3d_measure_elements_batch for bounded measurements, e3d_panel_geometry and e3d_panel_loop_relations for PANE, and "
        "e3d_primitive_geometry and e3d_primitive_mesh for supported primitives. Preserve read errors, completeness and unsupported fields. "
        "Do not treat missing dimensions as zero or infer world coordinates from an unverified frame.\n"
        "4. Use e3d_display_state for this exact reference to inspect actual Drawlist coverage and primitive entries. "
        "For clipping symptoms, use e3d_diagnose_element with include_clipping=true or read e3d_view_clipping_state. "
        "Treat missing clipping data as unknown, and do not infer that the target is clipped from clip-box data in an unverified frame. "
        "Use e3d_capture_view only to observe the current native view; do not add members, change the camera, or alter visuals.\n"
        "Return a concise diagnosis with observed facts, supported inferences, and unresolved questions separated. "
        "Do not run PML, edit attributes or geometry, save work, or claim engineering validation from bounds or pixels alone."
    )


def _read_message_window(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    if set(args) - {"cursor", "limit", "checkpoint"}:
        raise ValueError("Unknown e3d_read_message_window argument")
    return bridge.message_window(cursor=_string(args, "cursor"),
                                 limit=_integer(args, "limit", 100, 1, 500),
                                 checkpoint=args.get("checkpoint", False))


def _read_logs(bridge: BridgeClient, settings: Settings, tail: int) -> dict[str, Any]:
    if settings.log_file:
        selected_version = settings.url_for()[1]
        path = Path(settings.log_file).expanduser()
        if not path.is_file():
            raise ValueError(f"E3D_LOG_FILE does not exist: {path}")
        lines = read_log_tail(path, tail)
        return {"ok": True, "source": str(path), "version": selected_version, "lines": lines}
    if settings.mode == "bridge":
        return bridge.logs(tail)
    return bridge.logs(tail)


def _execute_pml(bridge: BridgeClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    command = _string(args, "command").strip()
    if not command:
        raise ValueError("command must not be empty")
    dry_run = args.get("dry_run", True)
    if not isinstance(dry_run, bool):
        raise ValueError("dry_run must be a boolean")
    if not dry_run and not settings.allow_commands:
        raise PermissionError("Mutating PML commands are disabled; set E3D_MCP_ALLOW_COMMANDS=true explicitly")
    session_id = _string(args, "expected_session_id")
    if session_id:
        return bridge.command(command, dry_run, expected_session_id=session_id)
    return bridge.command(command, dry_run)


def _execute_pml_script(bridge: BridgeClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    allowed = {"source", "dry_run", "request_id", "expected_session_id", "capture_after", "capture_width", "capture_height"}
    if set(args) - allowed:
        # Unknown field names are also untrusted; do not echo submitted source
        # or credential-shaped data embedded in an unexpected JSON key.
        raise ValueError("Unknown e3d_execute_pml_script argument")
    source = args.get("source")
    pml_script_utf8(source)
    dry_run = args.get("dry_run", True)
    if not isinstance(dry_run, bool):
        raise ValueError("dry_run must be a boolean")
    if not dry_run and not settings.allow_commands:
        raise PermissionError("Mutating PML scripts are disabled; set E3D_MCP_ALLOW_COMMANDS=true explicitly")
    options: dict[str, Any] = {}
    if "request_id" in args:
        options["request_id"] = pml_script_request_id(args["request_id"])
    if "expected_session_id" in args:
        session_id = _string(args, "expected_session_id")
        if not session_id.strip():
            raise ValueError("expected_session_id must be a non-empty string")
        options["expected_session_id"] = session_id
    return bridge.execute_pml_script(source, dry_run, **options)


def _pml_script_status(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    if set(args) - {"request_id", "expected_session_id"}:
        raise ValueError("Unknown e3d_pml_script_status argument")
    request_id = pml_script_request_id(args.get("request_id"))
    session_id = _string(args, "expected_session_id")
    if not session_id.strip():
        raise ValueError("expected_session_id must be a non-empty string")
    return bridge.pml_script_status(request_id, session_id)


def _reenter_design(bridge: BridgeClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    if set(args) - {"wait_seconds"}:
        raise ValueError("Unknown e3d_reenter_design argument")
    if not settings.allow_commands:
        raise PermissionError("E3D debug restart is disabled; set E3D_MCP_ALLOW_COMMANDS=true explicitly")
    return bridge.reenter_design(wait_seconds=_integer(args, "wait_seconds", 90, 5, 120))


def _execute_pml_batch(bridge: BridgeClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    commands = args.get("commands")
    if not isinstance(commands, list) or not commands or len(commands) > 200 or any(not isinstance(item, str) for item in commands):
        raise ValueError("commands must be a non-empty array of at most 200 strings")
    dry_run = args.get("dry_run", True)
    if not isinstance(dry_run, bool):
        raise ValueError("dry_run must be a boolean")
    stop_on_error = args.get("stop_on_error", True)
    if not isinstance(stop_on_error, bool):
        raise ValueError("stop_on_error must be a boolean")
    if not dry_run and not settings.allow_commands:
        raise PermissionError("Mutating PML commands are disabled; set E3D_MCP_ALLOW_COMMANDS=true explicitly")
    return bridge.command_batch(commands, dry_run, stop_on_error)


def _attribute_names(args: dict[str, Any], allow_empty: bool = False) -> list[str]:
    attributes = args.get("attributes", [] if allow_empty else None)
    minimum = 0 if allow_empty else 1
    if not isinstance(attributes, list) or not minimum <= len(attributes) <= 50 or any(
        not isinstance(name, str) or not name.strip() or len(name.strip()) > 64 or
        any(not (char.isalnum() or char in ":_") for char in name.strip())
        for name in attributes
    ):
        raise ValueError(f"attributes must contain {minimum} to 50 attribute names, such as POS, HEIG, or :CUSTOM")
    return list(dict.fromkeys(name.strip().upper() for name in attributes))


def _read_attributes(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"refno", "expected_type", "attributes"}
    if unknown:
        raise ValueError(f"Unknown e3d_read_attributes arguments: {', '.join(sorted(unknown))}")
    refno = _string(args, "refno")
    attributes = _attribute_names(args)
    expected_type = _expected_element_type(args)
    if expected_type:
        return bridge.read_attributes(refno, attributes, expected_type=expected_type)
    return bridge.read_attributes(refno, attributes)


def _read_attributes_batch(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    allowed = {"refnos", "attributes"}
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"Unknown e3d_read_attributes_batch arguments: {', '.join(sorted(unknown))}")
    values = args.get("refnos")
    if not isinstance(values, list) or not 1 <= len(values) <= 50:
        raise ValueError("refnos must contain between 1 and 50 explicit element references")
    refnos = [_panel_reference({"refno": value}, "refno") for value in values]
    if len({refno.casefold() for refno in refnos}) != len(refnos):
        raise ValueError("refnos must not contain duplicate references")
    attributes = _attribute_names(args)
    if len(refnos) * len(attributes) > 500:
        raise ValueError("A batch may read at most 500 element/attribute values")
    return bridge.read_attributes_batch(refnos, attributes)


def _element_access(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"refnos"}
    if unknown:
        raise ValueError(f"Unknown e3d_element_access arguments: {', '.join(sorted(unknown))}")
    return bridge.element_access(element_access_refnos(args.get("refnos")))


def _measure_elements_batch(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"refnos", "measurements", "wrt", "volume_scope"}
    if unknown:
        raise ValueError(f"Unknown e3d_measure_elements_batch arguments: {', '.join(sorted(unknown))}")
    values = args.get("refnos")
    if not isinstance(values, list) or not 1 <= len(values) <= 32:
        raise ValueError("refnos must contain between 1 and 32 explicit element references")
    refnos = [_panel_reference({"refno": value}, "refno") for value in values]
    if len({refno.casefold() for refno in refnos}) != len(refnos):
        raise ValueError("refnos must not contain duplicate references")
    measurements = args.get("measurements", ["spatial", "bounds", "volume"])
    if (not isinstance(measurements, list) or not 1 <= len(measurements) <= 3 or
            any(not isinstance(value, str) or value not in {"spatial", "bounds", "volume"} for value in measurements)):
        raise ValueError("measurements must contain 1 to 3 values from spatial, bounds and volume")
    if len(set(measurements)) != len(measurements):
        raise ValueError("measurements must not contain duplicate values")
    wrt = _string(args, "wrt", "WORLD").strip()
    if wrt.upper() in {"WORLD", "OWNER"}:
        wrt = wrt.upper()
    else:
        wrt = _panel_reference({"wrt": wrt}, "wrt")
    scope = _string(args, "volume_scope", "element")
    if scope not in {"element", "members"}:
        raise ValueError("volume_scope must be element or members")
    return bridge.measure_elements_batch(refnos, measurements, wrt, scope)


def _graphical_selection(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"limit"}
    if unknown:
        raise ValueError(f"Unknown e3d_graphical_selection arguments: {', '.join(sorted(unknown))}")
    return bridge.graphical_selection(_integer(args, "limit", 50, 1, 200))


def _set_graphical_selection(bridge: BridgeClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"refnos", "dry_run", "expected_revision"}
    if unknown:
        raise ValueError(f"Unknown e3d_set_graphical_selection arguments: {', '.join(sorted(unknown))}")
    if "expected_revision" in args and args["expected_revision"] is None:
        raise ValueError("expected_revision must contain exactly 64 hexadecimal characters")
    payload = graphical_selection_parameters(args.get("refnos"), args.get("dry_run", True), args.get("expected_revision"))
    _edit_preview(settings, payload)
    return bridge.set_graphical_selection(**payload)


def _element_schema(bridge: BridgeClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"refno", "expected_type", "attributes", "offset", "limit", "include_constraints"}
    if unknown:
        raise ValueError(f"Unknown e3d_element_schema arguments: {', '.join(sorted(unknown))}")
    include_constraints = args.get("include_constraints", False)
    if not isinstance(include_constraints, bool):
        raise ValueError("include_constraints must be a boolean")
    attributes = _attribute_names(args, allow_empty=True)
    refno = _string(args, "refno")
    expected_type = _expected_element_type(args)
    if include_constraints:
        if not 1 <= len(attributes) <= 10:
            raise ValueError("Constraint reads require 1 to 10 explicitly named attributes")
        refno = element_access_refnos([refno])[0]
    options = {"include_constraints": True} if include_constraints else {}
    if expected_type:
        options["expected_type"] = expected_type
    response = bridge.element_schema(refno, attributes,
                                    _integer(args, "offset", 0, 0, 100000), _integer(args, "limit", 50, 1, 200), **options)
    result = response.get("result")
    if isinstance(result, dict):
        result["mcp_commands_enabled"] = settings.allow_commands
        for attribute in result.get("attributes", []):
            if isinstance(attribute, dict):
                attribute["direct_edit_enabled"] = (settings.allow_commands
                    and attribute.get("supported") is True and attribute.get("sdk_settable_now") is True
                    and attribute.get("direct_edit_supported") is True and not attribute.get("read_error"))
    return response


def _list_members(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    cursor = _string(args, "cursor")
    if len(cursor) > 2048:
        raise ValueError("cursor must not exceed 2048 characters")
    expected_type = _expected_element_type(args)
    return bridge.list_members(_string(args, "refno"), cursor, _integer(args, "limit", 50, 1, 200),
                               expected_type=expected_type)


def _expected_element_type(args: dict[str, Any]) -> str:
    return normalize_expected_element_type(_string(args, "expected_type"))


def _element_snapshot(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    return bridge.element(_string(args, "refno"), _integer(args, "depth", 2, 0, 8),
                          _integer(args, "limit", 100, 1, 500), expected_type=_expected_element_type(args))


def _find_descendants(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"root_refno", "element_type", "name_contains", "max_depth", "limit", "cursor"}
    if unknown:
        raise ValueError(f"Unknown e3d_find_descendants arguments: {', '.join(sorted(unknown))}")
    cursor = _string(args, "cursor")
    if len(cursor) > 16384:
        raise ValueError("cursor must not exceed 16384 characters")
    root = _string(args, "root_refno").strip()
    if root:
        root = _panel_reference(args, "root_refno")
    elif not cursor:
        raise ValueError("An explicit root_refno is required on the first page")
    element_type = _string(args, "element_type").strip().upper() if "element_type" in args else None
    if element_type is not None and (len(element_type) > 64 or
            element_type and not re.fullmatch(r"[A-Z][A-Z0-9_]*", element_type)):
        raise ValueError("element_type must be an SDK type name of at most 64 characters")
    name_contains = _string(args, "name_contains") if "name_contains" in args else None
    if name_contains is not None and (len(name_contains) > 256 or any(ord(char) < 32 or ord(char) == 127 for char in name_contains)):
        raise ValueError("name_contains must be literal text of at most 256 characters without control characters")
    max_depth = _integer(args, "max_depth", 64, 1, 64) if "max_depth" in args else None
    return bridge.find_descendants(root, element_type, name_contains, max_depth,
                                   _integer(args, "limit", 50, 1, 200), cursor)


def _connection_points(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"refno", "wrt", "point_numbers", "expected_type"}
    if unknown:
        raise ValueError(f"Unknown e3d_connection_points arguments: {', '.join(sorted(unknown))}")
    refno = _panel_reference(args, "refno")
    wrt = _string(args, "wrt", "WORLD").strip()
    if wrt.upper() in {"WORLD", "OWNER"}:
        wrt = wrt.upper()
    else:
        wrt = _panel_reference({"wrt": wrt}, "wrt")
    points = args.get("point_numbers", [])
    if (not isinstance(points, list) or len(points) > 64 or
            any(isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 10000 for n in points)):
        raise ValueError("point_numbers must contain at most 64 unique integers between 1 and 10000")
    if len(points) != len(set(points)):
        raise ValueError("point_numbers must not contain duplicates")
    expected_type = _expected_element_type(args)
    if expected_type:
        return bridge.connection_points(refno, wrt, points, expected_type=expected_type)
    return bridge.connection_points(refno, wrt, points)


def _normalize_nozzle_targets(raw_targets: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_targets, list) or not 1 <= len(raw_targets) <= 32:
        raise ValueError("targets must contain between 1 and 32 explicit nozzle references")
    targets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, target in enumerate(raw_targets):
        if not isinstance(target, dict) or set(target) - {"refno", "point_numbers"} or "refno" not in target:
            raise ValueError(f"targets[{index}] must contain refno and optional point_numbers")
        refno = _panel_reference(target, "refno")
        key = refno.casefold()
        if key in seen:
            raise ValueError("targets must not contain duplicate references")
        seen.add(key)
        points = target.get("point_numbers", [])
        if (not isinstance(points, list) or len(points) > 64 or
                any(isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 10000
                    for number in points) or len(points) != len(set(points))):
            raise ValueError(f"targets[{index}].point_numbers must contain at most 64 unique integers between 1 and 10000")
        targets.append({"refno": refno, "point_numbers": points})
    return targets


def _read_nozzle_sources(bridge: BridgeClient, targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for target in targets:
        try:
            native = bridge.connection_points(target["refno"], "WORLD", target["point_numbers"])
        except BridgeError as exc:
            native = {"ok": False, "version": bridge._selected_version(None),
                      "error": str(exc), "error_code": "native_connection_points_failed",
                      **exc.diagnostics}
            if exc.http_status is not None:
                native["http_status"] = exc.http_status
            if exc.response_invalid:
                native["response_invalid"] = True
        records.append({"refno": target["refno"], "native": native})
    return records


def _nozzle_orientation_drawing(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any] | ImageToolResult | MultiImageToolResult:
    unknown = set(args) - {"targets", "preview_width", "preview_height", "include_svg", "include_dxf", "sheet"}
    if unknown:
        raise ValueError(f"Unknown e3d_nozzle_orientation_drawing arguments: {', '.join(sorted(unknown))}")
    include_svg = args.get("include_svg", False)
    include_dxf = args.get("include_dxf", False)
    if not isinstance(include_svg, bool) or not isinstance(include_dxf, bool):
        raise ValueError("include_svg and include_dxf must be booleans")
    sheet_options = normalize_nozzle_sheet_options(args["sheet"]) if "sheet" in args else None
    targets = _normalize_nozzle_targets(args.get("targets"))
    width = _integer(args, "preview_width", 1200, 320, 2048)
    height = _integer(args, "preview_height", 700, 240, 2048)
    if width * height > 2_000_000:
        raise ValueError("preview_width * preview_height must not exceed 2000000 pixels")
    records = _read_nozzle_sources(bridge, targets)
    drawing, png = build_nozzle_orientation_drawing(records, width=width, height=height)
    result = {"success": drawing.get("success") is True, "available": drawing.get("available") is True,
              "complete": drawing.get("complete") is True, "coordinate_space": "WORLD",
              "drawing": drawing if drawing.get("success") is True else None,
              "evidence": {"targets": [{"refno": row["refno"], "native": row["native"]} for row in records],
                           "partial_drawing": False}}
    metadata = {"ok": all(isinstance(row["native"], dict) and row["native"].get("ok") is True for row in records),
                "version": bridge._selected_version(None), "result": result}
    if png is None:
        result["drawing_diagnostics"] = {"nozzles": drawing.get("nozzles", []),
                                         "reason": "incomplete_or_unavailable_native_geometry"}
        return metadata
    if sheet_options is not None:
        return _nozzle_orientation_sheet_result(
            metadata, sheet_options, width=width, height=height,
            include_svg=include_svg, include_dxf=include_dxf)
    if include_svg:
        try:
            result["drawing"]["svg"] = _make_svg(
                result["drawing"]["views"],
                footer="WORLD PPOS nozzle locations and PDIR orientation glyphs; not an equipment outline.")
        except (ValueError, TypeError) as exc:
            result["export_error"] = f"svg: {str(exc)[:512]}"
    if include_dxf:
        try:
            result["drawing"]["dxf"] = export_orthographic_dxf(
                result["drawing"], include_dimensions=False)
        except (ValueError, TypeError) as exc:
            previous = result.get("export_error")
            current = f"dxf: {str(exc)[:512]}"
            result["export_error"] = f"{previous}; {current}" if isinstance(previous, str) else current
    return ImageToolResult(data=base64.b64encode(png).decode("ascii"), mime_type="image/png", metadata=metadata)


def _nozzle_orientation_sheet_result(metadata: dict[str, Any], options: dict[str, Any], *,
                                     width: int, height: int, include_svg: bool,
                                     include_dxf: bool) -> dict[str, Any] | ImageToolResult | MultiImageToolResult:
    result = metadata["result"]
    drawing = result["drawing"]
    try:
        sheets = build_nozzle_orientation_sheets(drawing, options)
        if width * height * len(sheets) > 8_000_000:
            raise ValueError("nozzle_sheet_pixel_limit_exceeded")
        images: list[tuple[str, str, str]] = []
        for sheet in sheets:
            if include_svg:
                sheet["svg"] = render_drawing_sheet_svg(sheet)
            if include_dxf:
                sheet["dxf"] = export_drawing_sheet_dxf(sheet)
            page_png = rasterize_drawing_sheet_png(sheet, width=width, height=height)
            images.append((f"Nozzle orientation sheet {sheet['page_number']} of {len(sheets)}",
                           base64.b64encode(page_png).decode("ascii"), "image/png"))
        drawing["sheets"] = sheets
        drawing["sheet_count"] = len(sheets)
        if len(sheets) == 1:
            drawing["sheet"] = sheets[0]
        if (len(json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 8 * 1024 * 1024 or
                sum(len(item[1]) for item in images) > 16 * 1024 * 1024):
            raise ValueError("nozzle_sheet_output_limit_exceeded")
    except (ValueError, TypeError) as exc:
        result.update(success=False, available=False, complete=False, drawing=None,
                      drawing_diagnostics={"reason": "nozzle_sheet_generation_failed", "error_code": str(exc)})
        return {**metadata, "ok": False}
    if len(images) == 1:
        return ImageToolResult(data=images[0][1], mime_type="image/png", metadata=metadata)
    return MultiImageToolResult(images, metadata)


def _equipment_layout_reference_key(refno: str) -> str:
    if refno.startswith("="):
        return "=" + "/".join(part.lstrip("0") or "0" for part in refno[1:].split("/"))
    return ("/" + refno.lstrip("/")).casefold()


def _equipment_layout_row_identity_matches(requested_refno: Any, row: dict[str, Any]) -> bool:
    if not isinstance(requested_refno, str):
        return False
    element = row.get("element")
    resolved = row.get("refno")
    if not isinstance(element, dict):
        # Older test doubles/bridge envelopes may omit the identity object;
        # an exact refno match is still safe for explicit DBREF requests.
        return isinstance(resolved, str) and requested_refno.startswith("=") and \
            _equipment_layout_reference_key(resolved) == _equipment_layout_reference_key(requested_refno)
    element_refno = element.get("refno")
    if (not isinstance(resolved, str) or not isinstance(element_refno, str) or
            _equipment_layout_reference_key(resolved) != _equipment_layout_reference_key(element_refno)):
        return False
    expected = _equipment_layout_reference_key(requested_refno)
    if requested_refno.startswith("="):
        return _equipment_layout_reference_key(resolved) == expected
    name = element.get("name")
    return isinstance(name, str) and _equipment_layout_reference_key(name) == expected


def _read_equipment_layout_sources(bridge: BridgeClient, refnos: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Share identity-verified batches without rendering or additional reads."""
    batch_receipts: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for start in range(0, len(refnos), 32):
        batch = refnos[start:start + 32]
        try:
            native = bridge.measure_elements_batch(batch, ["spatial"], "WORLD", "element")
        except BridgeError as exc:
            native = {"ok": False, "version": bridge._selected_version(None),
                      "error": str(exc), "error_code": "native_spatial_measurement_failed",
                      **exc.diagnostics}
            if exc.http_status is not None:
                native["http_status"] = exc.http_status
            if exc.response_invalid:
                native["response_invalid"] = True
        receipt: dict[str, Any] = {"requested_refnos": batch, "native": native}
        batch_receipts.append(receipt)
        result = native.get("result") if isinstance(native, dict) else None
        batch_rows = result.get("rows") if isinstance(result, dict) else None
        if isinstance(batch_rows, list):
            returned = [row for row in batch_rows if isinstance(row, dict)]
            malformed_row_count = len(batch_rows) - len(returned)
            if malformed_row_count:
                receipt["malformed_row_count"] = malformed_row_count
            # Only the adapter's explicit requested_refno can associate a
            # measurement with an input. `refno` is the resolved DB identity
            # and may legitimately differ when the caller used /NAME.
            returned_refs = [row.get("requested_refno") for row in returned]
            normalized_returned = [value.casefold() if isinstance(value, str) else None for value in returned_refs]
            expected = [refno.casefold() for refno in batch]
            returned_counts = {refno: normalized_returned.count(refno) for refno in set(expected)}
            missing = [refno for refno in batch if returned_counts.get(refno.casefold(), 0) == 0]
            unexpected = [value for value in returned_refs if not isinstance(value, str) or
                          value.casefold() not in expected]
            duplicate_rows = sorted({value for value in normalized_returned
                                     if value is not None and normalized_returned.count(value) > 1})
            identity_mismatches: list[str] = []
            accepted: list[dict[str, Any]] = []
            for row, requested_refno, normalized in zip(returned, returned_refs, normalized_returned):
                if normalized not in returned_counts or returned_counts[normalized] != 1:
                    continue
                if not _equipment_layout_row_identity_matches(requested_refno, row):
                    identity_mismatches.append(requested_refno)
                    continue
                accepted.append(row)
            rows.extend(accepted)
            rejected = [refno for refno in batch if returned_counts.get(refno.casefold(), 0) != 1 or
                        any(isinstance(value, str) and value.casefold() == refno.casefold()
                            for value in identity_mismatches)]
            for refno in rejected:
                count = returned_counts.get(refno.casefold(), 0)
                code = ("measurement_row_missing" if count == 0 else
                        "measurement_row_duplicated" if count > 1 else
                        "measurement_row_identity_mismatch")
                rows.append({"requested_refno": refno, "status": "not_returned",
                             "errors": [{"code": code}], "measurements": {}})
            if missing:
                receipt["missing_refnos"] = missing
            if identity_mismatches:
                receipt["identity_mismatch_refnos"] = identity_mismatches
            if unexpected:
                receipt["unexpected_refnos"] = unexpected
            if duplicate_rows:
                receipt["duplicate_refnos"] = duplicate_rows
            receipt["rows_match_request"] = (not missing and not unexpected and not duplicate_rows and
                                              not identity_mismatches and not malformed_row_count)
        else:
            receipt["error_code"] = "measurement_rows_unavailable"
            receipt["rows_match_request"] = False
            rows.extend({"requested_refno": refno, "status": "not_attempted",
                         "errors": [{"code": "measurement_rows_unavailable"}], "measurements": {}}
                        for refno in batch)
    return rows, batch_receipts


def _equipment_layout_drawing(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any] | ImageToolResult:
    allowed = {"refnos", "views", "axis_length_mm", "preview_width", "preview_height", "include_svg", "include_dxf"}
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"Unknown e3d_equipment_layout_drawing arguments: {', '.join(sorted(unknown))}")
    raw_refnos = args.get("refnos")
    if not isinstance(raw_refnos, list) or not 1 <= len(raw_refnos) <= 256:
        raise ValueError("refnos must contain between 1 and 256 explicit element references")
    refnos = [_panel_reference({"refno": value}, "refno") for value in raw_refnos]
    if len({refno.casefold() for refno in refnos}) != len(refnos):
        raise ValueError("refnos must not contain duplicate references")
    views = args.get("views", ["FRONT", "TOP", "SIDE"])
    if not isinstance(views, list) or not 1 <= len(views) <= 3 or len(set(views)) != len(views) or any(
            value not in {"FRONT", "TOP", "SIDE"} for value in views):
        raise ValueError("views must contain 1 to 3 unique values from FRONT, TOP and SIDE")
    axis_length = args.get("axis_length_mm", 1000)
    if isinstance(axis_length, bool) or not isinstance(axis_length, (int, float)) or not math.isfinite(float(axis_length)) or not 0 < float(axis_length) <= 1_000_000:
        raise ValueError("axis_length_mm must be a finite number between 0 and 1000000 mm")
    width = _integer(args, "preview_width", 1200, 320, 2048)
    height = _integer(args, "preview_height", 700, 240, 2048)
    if width * height > 2_000_000:
        raise ValueError("preview_width * preview_height must not exceed 2000000 pixels")
    rows, batch_receipts = _read_equipment_layout_sources(bridge, refnos)
    drawing = build_equipment_layout_drawing(rows, views=views, axis_length_mm=float(axis_length))
    # Renderers use the same linework contract under the name ``segments``.
    render_views = []
    for view in drawing.get("views", []):
        render_views.append({**view, "segments": list(view.get("lines", []))})
    drawing["views"] = render_views
    batch_complete = all(item.get("rows_match_request") is True and
                         isinstance(item.get("native"), dict) and item["native"].get("ok") is True and
                         isinstance(item["native"].get("result"), dict) and
                         item["native"]["result"].get("complete") is True for item in batch_receipts)
    result: dict[str, Any] = {
        "success": drawing.get("success") is True,
        "available": drawing.get("available") is True,
        "complete": drawing.get("complete") is True and batch_complete,
        "coordinate_space": "WORLD", "drawing": drawing if drawing.get("success") is True else None,
        "evidence": {"batch_receipts": batch_receipts, "row_count": len(rows), "requested_refnos": refnos,
                     "batch_complete": batch_complete,
                     "partial_drawing": drawing.get("success") is True and
                     not (drawing.get("complete") is True and batch_complete)},
    }
    if drawing.get("success") is not True:
        result["drawing_diagnostics"] = {"equipment": drawing.get("equipment", []),
                                          "reason": drawing.get("error_code", "world_positions_unavailable")}
        return {"ok": batch_complete, "version": bridge._selected_version(None), "result": result}
    try:
        image_footer = ("PARTIAL POSE ONLY - EQUIPMENT EXTENTS NOT INCLUDED" if
                        result["evidence"]["partial_drawing"] else
                        "POSE ONLY - EQUIPMENT EXTENTS NOT INCLUDED")
        png = rasterize_orthographic_png(render_views, width=width, height=height, footer=image_footer)
        if args.get("include_svg", False):
            result["drawing"]["svg"] = _make_svg(
                render_views,
                footer=f"WORLD {image_footer.lower()}.")
        if args.get("include_dxf", False):
            if result["complete"] is not True:
                result["export_error"] = "complete_native_layout_required_for_dxf"
            else:
                result["drawing"]["dxf"] = export_orthographic_dxf(
                    result["drawing"], include_dimensions=False)
    except (ValueError, TypeError) as exc:
        result.update(success=False, available=False, drawing=None, export_error=str(exc))
        return {"ok": False, "version": bridge._selected_version(None), "result": result,
                "error": "equipment_layout_export_failed"}
    metadata = {"ok": batch_complete, "version": bridge._selected_version(None), "result": result}
    return ImageToolResult(data=base64.b64encode(png).decode("ascii"), mime_type="image/png", metadata=metadata)


def _location_sheet_preview_options(args: dict[str, Any]) -> tuple[int, int, bool, bool]:
    width = _integer(args, "preview_width", 1400, 320, 2048)
    height = _integer(args, "preview_height", 990, 240, 2048)
    if width * height > 2_000_000:
        raise ValueError("preview_width * preview_height must not exceed 2000000 pixels")
    svg, dxf = args.get("include_svg", False), args.get("include_dxf", False)
    if type(svg) is not bool or type(dxf) is not bool:
        raise ValueError("include_svg and include_dxf must be booleans")
    return width, height, svg, dxf


def _location_sheet_finish(metadata: dict[str, Any], images=None, png_bytes=0):
    """Enforce one output budget after retaining native failure evidence."""
    images = images or []
    try:
        size = len(json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, OverflowError, UnicodeError):
        metadata = _primitive_csg_safe_evidence(metadata)
        metadata["ok"] = False
        metadata["result"].update(success=False, available=False, complete=False, drawing=None,
                                  error_code="location_sheet_output_not_serializable")
        images = []
        size = len(json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    if size > 8 * 1024 * 1024 or png_bytes > 8 * 1024 * 1024:
        metadata = {"ok": False, "version": metadata.get("version"), "result": {
            "success": False, "available": False, "complete": False, "drawing": None,
            "error_code": "location_sheet_output_limit_exceeded", "observed_json_bytes": size,
            "observed_png_bytes": png_bytes, "evidence_omitted": "Output exceeded the shared receipt budget."}}
        images = []
    if metadata.get("ok") is not True or metadata["result"].get("complete") is not True:
        images = []
    return MultiImageToolResult(images, metadata) if images else metadata


def _location_sheet_failure(metadata: dict[str, Any], code: str, **diagnostics):
    metadata["ok"] = False
    metadata["result"].update(success=False, available=False, complete=False, drawing=None,
                              error_code=code, **diagnostics)
    return _location_sheet_finish(metadata)


def _location_sheet_context_agrees(before: dict[str, Any], after: dict[str, Any]) -> bool:
    return before.get("available") is True and after.get("available") is True and all(
        before.get(key) == after.get(key) for key in ("session_id", "version", "project"))


def _render_location_sheet_pages(metadata, sheets, *, width, height, include_svg, include_dxf,
                                 coordinate_decimal_places=6):
    images, png_bytes, failed_page = [], 0, None
    try:
        if not sheets or width * height * len(sheets) > 8_000_000:
            raise ValueError("location_sheet_preview_limit_exceeded")
        for sheet in sheets:
            failed_page = sheet["page_number"]
            if include_svg:
                sheet["svg"] = render_drawing_sheet_svg(sheet, coordinate_decimal_places=coordinate_decimal_places)
            if include_dxf:
                sheet["dxf"] = export_drawing_sheet_dxf(sheet, coordinate_decimal_places=coordinate_decimal_places)
            png = rasterize_drawing_sheet_png(sheet, width=width, height=height)
            png_bytes += len(png)
            if png_bytes > 8 * 1024 * 1024:
                raise ValueError("location_sheet_png_limit_exceeded")
            images.append((f"Location sheet {failed_page} of {len(sheets)}", base64.b64encode(png).decode("ascii"), "image/png"))
        drawing = metadata["result"]["drawing"]
        drawing.update(sheets=sheets, sheet_count=len(sheets))
        if len(sheets) == 1:
            drawing["sheet"] = sheets[0]
    except Exception as exc:
        return _location_sheet_failure(metadata, "location_sheet_generation_failed",
            drawing_diagnostics={"error_code": str(exc)[:1024], "error_type": type(exc).__name__, "failed_page_number": failed_page})
    return _location_sheet_finish(metadata, images, png_bytes)


def _read_complete_equipment_location(bridge: BridgeClient, refnos: list[str]) -> dict[str, Any]:
    """Read one identity-verified complete WORLD pose set without exporting."""
    before = _assembly_boundary_context(bridge)
    result = {"success": False, "available": False, "complete": False, "drawing": None,
              "coordinate_space": "WORLD", "coordinate_unit": "mm", "atomic_snapshot": False,
              "context": {"before": before, "after": None, "boundary_agreement": None},
              "evidence": {"requested_refnos": refnos, "batch_receipts": []}}
    metadata = {"ok": False, "version": before.get("version"), "result": result}
    if before.get("available") is not True:
        return _location_sheet_failure(metadata, "layout_sheet_context_unavailable")
    rows, receipts = _read_equipment_layout_sources(bridge, refnos)
    result["evidence"].update(batch_receipts=receipts, row_count=len(rows))
    after = _assembly_boundary_context(bridge)
    agreement = _location_sheet_context_agrees(before, after)
    result["context"].update(after=after, boundary_agreement=agreement)
    if not agreement:
        return _location_sheet_failure(metadata, "layout_sheet_context_changed")
    complete = all(item.get("rows_match_request") is True and isinstance(item.get("native"), dict)
                   and item["native"].get("ok") is True and item["native"].get("version") == before["version"]
                   and isinstance(item["native"].get("result"), dict)
                   and item["native"]["result"].get("wrt") == "WORLD"
                   and all(item["native"]["result"].get(flag) is True for flag in ("success", "complete")) for item in receipts)
    result["evidence"]["batch_complete"] = complete
    if not complete or len(rows) != len(refnos) or any(row.get("success") is not True or row.get("complete") is not True for row in rows):
        return _location_sheet_failure(metadata, "layout_sheet_complete_sources_required")
    resolved = [row.get("refno") for row in rows]
    if (any(not isinstance(value, str) or re.fullmatch(r"=\d+/\d+", value) is None for value in resolved)
            or len({_equipment_layout_reference_key(value) for value in resolved}) != len(resolved)):
        return _location_sheet_failure(metadata, "layout_sheet_duplicate_or_invalid_resolved_source")
    world_frames = set()
    for row in rows:
        measurements = row.get("measurements")
        field = measurements.get("spatial") if isinstance(measurements, dict) else None
        spatial = field.get("result") if isinstance(field, dict) else None
        if not isinstance(spatial, dict):
            return _location_sheet_failure(metadata, "layout_sheet_verified_world_frame_required")
        frame, position = spatial.get("frame"), spatial.get("position")
        if (not isinstance(frame, dict) or frame.get("requested") != "WORLD" or frame.get("type") != "WORL"
                or not isinstance(frame.get("refno"), str) or not frame["refno"].strip()
                or not isinstance(position, dict) or position.get("unit") != "mm"
                or spatial.get("refno") != row["refno"]):
            return _location_sheet_failure(metadata, "layout_sheet_verified_world_frame_required")
        world_frames.add(frame["refno"])
    if len(world_frames) != 1:
        return _location_sheet_failure(metadata, "layout_sheet_world_frame_mismatch")
    order = {value.casefold(): index for index, value in enumerate(refnos)}
    rows.sort(key=lambda row: order[row["requested_refno"].casefold()])
    try:
        drawing = build_equipment_layout_drawing(rows, views=["TOP", "FRONT"])
        if any(drawing.get(flag) is not True for flag in ("success", "available", "complete")):
            result["evidence"]["equipment"] = drawing.get("equipment")
            return _location_sheet_failure(metadata, "layout_sheet_complete_world_poses_required")
    except Exception as exc:
        return _location_sheet_failure(metadata, "layout_sheet_preparation_failed", drawing_diagnostics={"error_code": str(exc)[:1024]})
    result.update(success=True, available=True, complete=True, drawing=drawing)
    metadata["ok"] = True
    return metadata


def _equipment_layout_sheet(bridge: BridgeClient, args: dict[str, Any]):
    from .equipment_layout_sheet import (build_equipment_layout_sheets, normalize_equipment_layout_datum,
                                         normalize_equipment_layout_sheet_options)
    if set(args) - {"refnos", "datum", "sheet", "preview_width", "preview_height", "include_svg", "include_dxf"}:
        raise ValueError("Unknown equipment layout sheet arguments")
    raw = args.get("refnos")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 256:
        raise ValueError("refnos must contain between 1 and 256 explicit element references")
    refnos = [_panel_reference({"refno": value}, "refno") for value in raw]
    if len({_equipment_layout_reference_key(value) for value in refnos}) != len(refnos):
        raise ValueError("refnos must not contain duplicate references")
    options = normalize_equipment_layout_sheet_options(args.get("sheet", {}))
    datum = normalize_equipment_layout_datum(args.get("datum"))
    width, height, svg, dxf = _location_sheet_preview_options(args)
    metadata = _read_complete_equipment_location(bridge, refnos)
    if metadata.get("ok") is not True:
        return metadata
    result = metadata["result"]
    drawing = result["drawing"]
    try:
        sheets = build_equipment_layout_sheets(drawing, options, datum=datum)
    except Exception as exc:
        return _location_sheet_failure(metadata, "layout_sheet_preparation_failed", drawing_diagnostics={"error_code": str(exc)[:1024]})
    result["datum"] = datum
    return _render_location_sheet_pages(metadata, sheets, width=width, height=height, include_svg=svg, include_dxf=dxf)


def _read_complete_nozzle_location(bridge: BridgeClient, targets: list[dict[str, Any]]) -> dict[str, Any]:
    """Read a complete identity-verified WORLD PPOS/PDIR set without sheet export."""
    before = _assembly_boundary_context(bridge)
    result = {"success": False, "available": False, "complete": False, "drawing": None,
              "coordinate_space": "WORLD", "coordinate_unit": "mm", "atomic_snapshot": False,
              "context": {"before": before, "after": None, "boundary_agreement": None},
              "evidence": {"targets": [], "partial_drawing": False}}
    metadata = {"ok": False, "version": before.get("version"), "result": result}
    if before.get("available") is not True:
        return _location_sheet_failure(metadata, "nozzle_datum_context_unavailable")
    records = _read_nozzle_sources(bridge, targets)
    result["evidence"]["targets"] = records
    after = _assembly_boundary_context(bridge)
    agreement = _location_sheet_context_agrees(before, after)
    result["context"].update(after=after, boundary_agreement=agreement)
    if not agreement:
        return _location_sheet_failure(metadata, "nozzle_datum_context_changed")
    resolved = set()
    for target, record in zip(targets, records):
        native = record.get("native")
        data = native.get("result") if isinstance(native, dict) else None
        if (not isinstance(native, dict) or native.get("ok") is not True or native.get("version") != before["version"]
                or not isinstance(data, dict) or any(data.get(flag) is not True for flag in ("success", "complete"))):
            return _location_sheet_failure(metadata, "nozzle_datum_complete_sources_required")
        element, frame = data.get("element"), data.get("frame")
        ref = element.get("refno") if isinstance(element, dict) else None
        if (not isinstance(ref, str) or re.fullmatch(r"=\d+/\d+", ref) is None
                or not _equipment_layout_row_identity_matches(target["refno"], {"refno": ref, "element": element})):
            return _location_sheet_failure(metadata, "nozzle_datum_source_identity_mismatch")
        key = _equipment_layout_reference_key(ref)
        if key in resolved:
            return _location_sheet_failure(metadata, "nozzle_datum_duplicate_resolved_source")
        resolved.add(key)
        if (not isinstance(frame, dict) or frame.get("requested") != "WORLD" or frame.get("type") != "WORL"
                or not isinstance(frame.get("refno"), str) or not frame["refno"].strip()):
            return _location_sheet_failure(metadata, "nozzle_datum_world_frame_required")
        points = data.get("points")
        numbers = [point.get("number") if isinstance(point, dict) else None for point in points] if isinstance(points, list) else []
        if (not numbers or any(type(value) is not int or not 1 <= value <= 10000 for value in numbers)
                or len(numbers) != len(set(numbers)) or target["point_numbers"] and set(numbers) != set(target["point_numbers"])):
            return _location_sheet_failure(metadata, "nozzle_datum_point_identity_mismatch")
    try:
        drawing, _ = build_nozzle_orientation_drawing(records, width=320, height=240)
        if any(drawing.get(flag) is not True for flag in ("success", "available", "complete")):
            result["evidence"]["nozzles"] = drawing.get("nozzles")
            return _location_sheet_failure(metadata, "nozzle_datum_complete_geometry_required")
    except Exception as exc:
        return _location_sheet_failure(metadata, "nozzle_datum_preparation_failed", drawing_diagnostics={"error_code": str(exc)[:1024]})
    result.update(success=True, available=True, complete=True, drawing=drawing)
    metadata["ok"] = True
    return metadata


def _nozzle_datum_sheet(bridge: BridgeClient, args: dict[str, Any]):
    from .nozzle_orientation_sheet import normalize_nozzle_datum
    if set(args) - {"targets", "datum", "sheet", "preview_width", "preview_height", "include_svg", "include_dxf"}:
        raise ValueError("Unknown nozzle datum sheet arguments")
    targets = _normalize_nozzle_targets(args.get("targets"))
    datum = normalize_nozzle_datum(args.get("datum"))
    sheet = args.get("sheet", {})
    if isinstance(sheet, dict) and "datum" in sheet:
        raise ValueError("datum must be declared at the top level")
    options = normalize_nozzle_sheet_options(sheet)
    options["datum"] = datum
    width, height, svg, dxf = _location_sheet_preview_options(args)
    metadata = _read_complete_nozzle_location(bridge, targets)
    metadata["result"]["datum"] = datum
    if metadata.get("ok") is not True:
        return metadata
    try:
        sheets = build_nozzle_orientation_sheets(metadata["result"]["drawing"], options)
    except Exception as exc:
        return _location_sheet_failure(metadata, "nozzle_datum_preparation_failed", drawing_diagnostics={"error_code": str(exc)[:1024]})
    return _render_location_sheet_pages(metadata, sheets, width=width, height=height, include_svg=svg, include_dxf=dxf)


def _location_equipment_pose(metadata: dict[str, Any], index: int) -> dict[str, Any]:
    """Retain the native EQUI frame alongside the already validated pose."""
    result = metadata["result"]
    item = result["drawing"]["equipment"][index]
    requested = item["requested_refno"]
    rows = [row for receipt in result["evidence"]["batch_receipts"]
            for row in receipt["native"]["result"]["rows"] if row.get("requested_refno") == requested]
    if len(rows) != 1:
        raise ValueError("equipment_coordinate_frame_requires_native_EQUI")
    row = rows[0]
    spatial = row["measurements"]["spatial"]["result"]
    # SDK batch summaries expose EQUIPMENT; the spatial reader exposes EQUI.
    if row.get("element", {}).get("type") not in {"EQUI", "EQUIPMENT"} or spatial.get("type") != "EQUI":
        raise ValueError("equipment_coordinate_frame_requires_native_EQUI")
    frame_refno = spatial["frame"].get("refno")
    world = re.fullmatch(r"=([0-9]+)/([0-9]+)", frame_refno) if isinstance(frame_refno, str) else None
    if world is None or int(world[1]) <= 0 or int(world[2]) != 0:
        raise ValueError("equipment_coordinate_frame_requires_native_WORLD_root")
    return {"success": True, "available": True, "complete": True,
            "requested_refno": requested, "resolved_refno": row["refno"],
            "element_name": row["element"].get("name"), "coordinate_frame": "WORLD", "coordinate_unit": "mm",
            "position": item["position"], "orientation": item["orientation"], "native_frame": spatial["frame"]}


def _nozzle_axis_sheet(bridge: BridgeClient, args: dict[str, Any]):
    from .nozzle_axis_sheet import build_nozzle_axis_sheets
    if set(args) - {"equipment_refno", "targets", "axial_axis", "zero_axis", "sheet", "preview_width", "preview_height", "include_svg", "include_dxf"}:
        raise ValueError("Unknown nozzle axis sheet arguments")
    refno = _panel_reference(args, "equipment_refno")
    targets = _normalize_nozzle_targets(args.get("targets"))
    axial, zero = args.get("axial_axis"), args.get("zero_axis")
    if type(axial) is not str or type(zero) is not str or axial not in {"X", "Y", "Z"} or zero not in {"X", "Y", "Z"} or axial == zero:
        raise ValueError("axial_axis and zero_axis must be distinct positive local X, Y or Z axes")
    sheet = args.get("sheet", {})
    if not isinstance(sheet, dict) or "datum" in sheet:
        raise ValueError("nozzle axis sheet takes its datum from the native equipment frame")
    options = normalize_nozzle_sheet_options({"title": "NOZZLE AXIS ORIENTATION", "drawing_number": "E3D-NOZZLE-AXIS", **sheet})
    width, height, svg, dxf = _location_sheet_preview_options(args)
    equipment = _read_complete_equipment_location(bridge, [refno])
    if equipment.get("ok") is not True:
        return equipment
    try:
        pose = _location_equipment_pose(equipment, 0)
    except Exception as exc:
        return _location_sheet_failure(equipment, "nozzle_axis_equipment_frame_unavailable", drawing_diagnostics={"error_code": str(exc)[:1024]})
    metadata = _read_complete_nozzle_location(bridge, targets)
    result = metadata["result"]
    result.setdefault("evidence", {})["equipment"] = equipment["result"]["evidence"]
    result["equipment_pose"] = pose
    result["axial_axis"], result["zero_axis"] = axial, zero
    if metadata.get("ok") is not True:
        return _location_sheet_finish(metadata)
    initial = equipment["result"]["context"]["before"]
    nozzle_context = result["context"]
    result["context"] = {"before": initial, "equipment": equipment["result"]["context"],
                         "nozzles": nozzle_context, "after": nozzle_context["after"], "boundary_agreement": False}
    agreement = all(_location_sheet_context_agrees(initial, value) for value in (
        nozzle_context["before"], nozzle_context["after"], equipment["result"]["context"]["after"]))
    result["context"]["boundary_agreement"] = agreement
    if not agreement:
        return _location_sheet_failure(metadata, "nozzle_axis_context_changed")
    try:
        sheets = build_nozzle_axis_sheets(result["drawing"], pose, options, axial_axis=axial, zero_axis=zero)
    except Exception as exc:
        return _location_sheet_failure(metadata, "nozzle_axis_preparation_failed", drawing_diagnostics={"error_code": str(exc)[:1024]})
    return _render_location_sheet_pages(metadata, sheets, width=width, height=height, include_svg=svg, include_dxf=dxf)


def _verify_outline_source_membership(bridge: BridgeClient, groups: list[dict[str, Any]], poses: list[dict[str, Any]],
                                      source_rows: list[dict[str, Any]], expected_version: str) -> dict[str, Any]:
    """Prove positive membership of selected sources without claiming a whole tree."""
    proof = {"success": False, "complete": False, "groups": [], "max_pages_per_equipment": 8,
             "scope": "Positive native descendant observations for explicit sources; not complete equipment geometry."}
    offset = 0
    try:
        if (len(groups) != len(poses) or sum(len(group["sources"]) for group in groups) != len(source_rows)
                or not groups or any(not group["sources"] for group in groups)):
            raise ValueError("outline_membership_source_partition_invalid")
        for group, pose in zip(groups, poses):
            requested = source_rows[offset:offset + len(group["sources"])]
            offset += len(group["sources"])
            wanted = {_equipment_layout_reference_key(row["resolved_refno"]): row for row in requested}
            current = {"equipment_refno": pose["resolved_refno"], "requested_sources": list(wanted), "members": [], "pages": [], "complete": False}
            proof["groups"].append(current)
            ancestry = [_equipment_layout_reference_key(pose["resolved_refno"])]
            cursor, seen_cursors, seen_nodes = "", set(), {ancestry[0]}
            for page_index in range(8):
                native = bridge.find_descendants(pose["resolved_refno"] if page_index == 0 else "",
                    "" if page_index == 0 else None, "" if page_index == 0 else None, 64 if page_index == 0 else None, 200, cursor)
                current["pages"].append(native)
                data = native.get("result") if isinstance(native, dict) else None
                root = data.get("root") if isinstance(data, dict) else None
                if (not isinstance(native, dict) or native.get("ok") is not True or native.get("version") != expected_version
                        or not isinstance(data, dict) or data.get("success") is not True or data.get("read_errors") != []
                        or not isinstance(root, dict) or root.get("type") != "EQUI"
                        or not isinstance(root.get("refno"), str)
                        or _equipment_layout_reference_key(root["refno"]) != _equipment_layout_reference_key(pose["resolved_refno"])
                        or not isinstance(data.get("elements"), list) or len(data["elements"]) > 200):
                    raise ValueError("outline_membership_native_page_invalid")
                for member in data["elements"]:
                    if (not isinstance(member, dict) or not isinstance(member.get("refno"), str)
                            or re.fullmatch(r"=\d+/\d+", member["refno"]) is None):
                        raise ValueError("outline_membership_invalid_node")
                    key = _equipment_layout_reference_key(member["refno"])
                    depth, owner = member.get("depth"), member.get("owner_refno")
                    if (type(depth) is not int or not 1 <= depth <= 64 or depth > len(ancestry)
                            or not isinstance(owner, str) or re.fullmatch(r"=\d+/\d+", owner) is None
                            or _equipment_layout_reference_key(owner) != ancestry[depth - 1]):
                        raise ValueError("outline_membership_native_ancestry_mismatch")
                    if key in seen_nodes:
                        raise ValueError("outline_membership_duplicate_node")
                    seen_nodes.add(key)
                    ancestry = ancestry[:depth] + [key]
                    if key in wanted:
                        expected = wanted.pop(key)
                        if member.get("type") != expected["expected_type"]:
                            raise ValueError("outline_membership_source_type_mismatch")
                        current["members"].append({**member, "source_index": expected["source_index"]})
                if not wanted:
                    current["complete"] = True
                    break
                if data.get("has_more") is False:
                    raise ValueError("outline_source_not_descendant_of_equipment")
                cursor = data.get("next_cursor")
                if data.get("has_more") is not True or not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                    raise ValueError("outline_membership_invalid_continuation")
                seen_cursors.add(cursor)
            if not current["complete"]:
                raise ValueError("outline_membership_page_budget_exceeded")
    except Exception as exc:
        proof.update(error_code=str(exc)[:1024], error_type=type(exc).__name__)
        return proof
    proof.update(success=True, complete=True)
    return proof


def _equipment_outline_sheet(bridge: BridgeClient, args: dict[str, Any]):
    from .equipment_outline_sheet import (build_equipment_outline_sheet, normalize_equipment_outline_targets,
                                         normalize_equipment_outline_sheet_options)
    from .equipment_layout_sheet import normalize_equipment_layout_datum
    from .material_assembly_drawing import combine_material_drawing_meshes, generate_material_assembly_drawing
    if set(args) - {"equipment", "datum", "sheet", "radial_segments", "feature_angle_degrees", "hidden_line_removal", "show_hidden_lines",
                     "preview_width", "preview_height", "include_svg", "include_dxf"}:
        raise ValueError("Unknown equipment outline sheet arguments")
    groups = normalize_equipment_outline_targets(args.get("equipment"))
    sheet = normalize_equipment_outline_sheet_options(args.get("sheet", {}))
    datum = normalize_equipment_layout_datum(args.get("datum"))
    width, height, svg, dxf = _location_sheet_preview_options(args)
    options = _drawing_options({"radial_segments": 24, **{key: args[key] for key in ("radial_segments", "feature_angle_degrees", "hidden_line_removal", "show_hidden_lines") if key in args}, "views": ["TOP"]})
    metadata = _read_complete_equipment_location(bridge, [group["refno"] for group in groups])
    if metadata.get("ok") is not True:
        return metadata
    result = metadata["result"]
    equipment_drawing = result["drawing"]
    initial = result["context"]["before"]
    result["drawing"] = None
    try:
        poses = [_location_equipment_pose({**metadata, "result": {**result, "drawing": equipment_drawing}}, index) for index in range(len(groups))]
        targets = [source for group in groups for source in group["sources"]]
        read = _read_material_sources(bridge, targets, options["radial_segments"])
        result["material_sources"] = {key: value for key, value in read.items() if key not in {"mesh_sources", "sources"}}
        result["material_sources"]["sources"] = [{key: value for key, value in row.items() if key != "raw_receipts"} for row in read.get("sources", [])]
        if any(read.get(flag) is not True for flag in ("success", "available", "complete")):
            return _location_sheet_failure(metadata, "outline_material_sources_unavailable")
        if not all(_location_sheet_context_agrees(initial, read["context"][side]) for side in ("before", "after")):
            return _location_sheet_failure(metadata, "outline_source_context_changed")
        membership = _verify_outline_source_membership(bridge, groups, poses, read["sources"], metadata["version"])
        result["source_membership"] = membership
        final = _assembly_boundary_context(bridge)
        result["context"].update(after=final, boundary_agreement=_location_sheet_context_agrees(initial, final))
        if result["context"]["boundary_agreement"] is not True:
            return _location_sheet_failure(metadata, "outline_membership_context_changed")
        if membership.get("success") is not True:
            return _location_sheet_failure(metadata, "outline_source_membership_unverified")
        combined = combine_material_drawing_meshes(read["mesh_sources"])
        result["combined_mesh"] = {key: value for key, value in combined.items() if key not in {"vertices", "triangles"}}
        if any(combined.get(flag) is not True for flag in ("success", "available", "complete")):
            return _location_sheet_failure(metadata, "outline_material_combination_failed")
        projection = generate_material_assembly_drawing(combined, views=["TOP"], projection_convention="right_handed",
            feature_angle_degrees=options["feature_angle_degrees"], hidden_line_removal=options["hidden_line_removal"], show_hidden_lines=options["show_hidden_lines"])
        result["material_projection"] = {key: value for key, value in projection.items() if key not in {"svg", "dxf"}}
        if any(projection.get(flag) is not True for flag in ("success", "available", "complete")):
            return _location_sheet_failure(metadata, "outline_material_projection_failed")
        mapping, offset = [], 0
        for group in groups:
            mapping.append(list(range(offset, offset + len(group["sources"]))))
            offset += len(group["sources"])
        scene = build_equipment_outline_sheet(projection, equipment_drawing, mapping, sheet, datum=datum)
        scene["explicit_source_membership_verified"] = True
        result.update(success=True, available=True, complete=True, drawing={"equipment": equipment_drawing["equipment"],
                      "explicit_source_membership_verified": True, "whole_equipment_geometry_verified": False}, datum=datum)
    except Exception as exc:
        return _location_sheet_failure(metadata, "outline_sheet_preparation_failed", drawing_diagnostics={"error_code": str(exc)[:1024], "error_type": type(exc).__name__})
    return _render_location_sheet_pages(metadata, [scene], width=width, height=height, include_svg=svg, include_dxf=dxf,
                                         coordinate_decimal_places=15)


def _read_location_material(bridge, groups, metadata, poses, radial_segments, *, chord_tolerance_mm=None,
                            use_generated_dishes=False):
    """Observe selected material once and prove native membership in one context.

    Internal mesh arrays are returned separately and are never serialized as
    duplicate raw evidence. Failure retains the existing structured no-image
    contract. This helper performs no projection or rendering.
    """
    from .material_assembly_drawing import combine_material_drawing_meshes
    result = metadata["result"]
    initial = result["context"]["before"]
    targets = [source for group in groups for source in group["sources"]]
    read = _read_material_sources(bridge, targets, radial_segments,
        **({"chord_tolerance_mm": chord_tolerance_mm} if chord_tolerance_mm is not None else {}),
        **({"use_generated_dishes": True} if use_generated_dishes else {}))
    result["material_sources"] = {key: value for key, value in read.items() if key not in {"mesh_sources", "sources"}}
    result["material_sources"]["sources"] = [{key: value for key, value in row.items() if key != "raw_receipts"}
        for row in read.get("sources", [])]
    if any(read.get(flag) is not True for flag in ("success", "available", "complete")):
        return _location_sheet_failure(metadata, "location_material_sources_unavailable"), None
    if not all(_location_sheet_context_agrees(initial, read["context"][side]) for side in ("before", "after")):
        return _location_sheet_failure(metadata, "location_material_context_changed"), None
    membership = _verify_outline_source_membership(bridge, groups, poses, read["sources"], metadata["version"])
    result["source_membership"] = membership
    final = _assembly_boundary_context(bridge)
    agreement = _location_sheet_context_agrees(initial, final)
    result["context"].update(after=final, boundary_agreement=agreement)
    if not agreement:
        return _location_sheet_failure(metadata, "location_material_membership_context_changed"), None
    if membership.get("success") is not True:
        return _location_sheet_failure(metadata, "location_material_membership_unverified"), None
    combined = combine_material_drawing_meshes(read["mesh_sources"])
    result["combined_mesh"] = {key: value for key, value in combined.items() if key not in {"vertices", "triangles"}}
    if any(combined.get(flag) is not True for flag in ("success", "available", "complete")):
        return _location_sheet_failure(metadata, "location_material_combination_failed"), None
    return metadata, combined


def _equipment_layout_tiles(bridge: BridgeClient, args: dict[str, Any]):
    from .equipment_layout_tiles import (build_equipment_layout_tiles, normalize_equipment_layout_tile_targets,
                                         normalize_equipment_layout_tile_sheet_options, normalize_equipment_layout_tile_tiling_options)
    from .equipment_layout_sheet import normalize_equipment_layout_datum
    from .material_assembly_drawing import generate_material_assembly_drawing
    allowed = {"equipment", "datum", "sheet", "tiling", "radial_segments", "feature_angle_degrees", "chord_tolerance_mm", "use_generated_dishes",
               "hidden_line_removal", "show_hidden_lines", "preview_width", "preview_height", "include_svg", "include_dxf"}
    if set(args) - allowed:
        raise ValueError("Unknown equipment layout tiles arguments")
    groups = normalize_equipment_layout_tile_targets(args.get("equipment"))
    generated_dishes = args.get("use_generated_dishes", False)
    if type(generated_dishes) is not bool:
        raise ValueError("use_generated_dishes must be a boolean")
    chord_tolerance = _chord_tolerance(args)
    sheet = normalize_equipment_layout_tile_sheet_options(args.get("sheet"))
    tiling = normalize_equipment_layout_tile_tiling_options(sheet, args.get("tiling"), len(groups))
    datum = normalize_equipment_layout_datum(args.get("datum"))
    width, height, svg, dxf = _location_sheet_preview_options(args)
    options = _drawing_options({"radial_segments": 24, **{key: args[key] for key in (
        "radial_segments", "feature_angle_degrees", "hidden_line_removal", "show_hidden_lines") if key in args}, "views": ["TOP"]})
    metadata = _read_complete_equipment_location(bridge, [group["refno"] for group in groups])
    if metadata.get("ok") is not True:
        return metadata
    result = metadata["result"]
    equipment_drawing = result["drawing"]
    try:
        poses = [_location_equipment_pose(metadata, index) for index in range(len(groups))]
        result["drawing"] = None
        metadata, combined = _read_location_material(bridge, groups, metadata, poses, options["radial_segments"],
            **({"chord_tolerance_mm": chord_tolerance} if chord_tolerance is not None else {}),
            **({"use_generated_dishes": True} if generated_dishes else {}))
        if combined is None:
            return metadata
        projection = generate_material_assembly_drawing(combined, views=["TOP"], projection_convention="right_handed",
            feature_angle_degrees=options["feature_angle_degrees"], hidden_line_removal=options["hidden_line_removal"],
            show_hidden_lines=options["show_hidden_lines"])
        result["material_projection"] = {key: value for key, value in projection.items() if key not in {"svg", "dxf"}}
        if any(projection.get(flag) is not True for flag in ("success", "available", "complete")):
            return _location_sheet_failure(metadata, "equipment_layout_tiles_projection_failed")
        mapping, offset = [], 0
        for group in groups:
            mapping.append(list(range(offset, offset + len(group["sources"]))))
            offset += len(group["sources"])
        tiled = build_equipment_layout_tiles(projection, equipment_drawing, mapping, sheet, tiling, datum=datum)
        for scene in tiled["sheets"]:
            scene["explicit_source_membership_verified"] = True
        result.update(success=True, available=True, complete=True, datum=datum,
            drawing={"equipment": equipment_drawing["equipment"], "manifest": tiled["manifest"],
                     "explicit_source_membership_verified": True, "whole_equipment_geometry_verified": False})
    except Exception as exc:
        return _location_sheet_failure(metadata, "equipment_layout_tiles_preparation_failed",
            drawing_diagnostics={"error_code": str(exc)[:1024], "error_type": type(exc).__name__})
    return _render_location_sheet_pages(metadata, tiled["sheets"], width=width, height=height, include_svg=svg,
                                         include_dxf=dxf, coordinate_decimal_places=15)


def _nozzle_outline_sheet(bridge: BridgeClient, args: dict[str, Any]):
    from .material_assembly_workflow import normalize_material_targets
    from .nozzle_outline_sheet import NozzleOutlineScaleDoesNotFit, build_nozzle_outline_sheets
    allowed = {"equipment_refno", "targets", "sources", "axial_axis", "zero_axis", "sheet", "radial_segments", "chord_tolerance_mm", "nozzle_geometry", "use_generated_dishes",
               "feature_angle_degrees", "hidden_line_removal", "show_hidden_lines", "preview_width", "preview_height", "include_svg", "include_dxf"}
    if set(args) - allowed:
        raise ValueError("Unknown nozzle outline sheet arguments")
    refno = _panel_reference(args, "equipment_refno")
    targets = _normalize_nozzle_targets(args.get("targets"))
    sources = normalize_material_targets(args.get("sources"))
    generated_dishes = args.get("use_generated_dishes", False)
    if type(generated_dishes) is not bool:
        raise ValueError("use_generated_dishes must be a boolean")
    chord_tolerance = _chord_tolerance(args)
    nozzle_geometry = args.get("nozzle_geometry", "points")
    if nozzle_geometry not in {"points", "catalogue"}:
        raise ValueError("nozzle_geometry must be points or catalogue")
    axial, zero = args.get("axial_axis"), args.get("zero_axis")
    if type(axial) is not str or type(zero) is not str or axial not in {"X", "Y", "Z"} or zero not in {"X", "Y", "Z"} or axial == zero:
        raise ValueError("axial_axis and zero_axis must be distinct positive local X, Y or Z axes")
    sheet = args.get("sheet", {})
    if not isinstance(sheet, dict) or "datum" in sheet:
        raise ValueError("nozzle outline sheet uses the native equipment coordinate frame")
    sheet = dict(sheet)
    fixed_scale = sheet.pop("scale_denominator", None)
    if fixed_scale is not None and (type(fixed_scale) not in {int, float} or not math.isfinite(fixed_scale) or fixed_scale <= 0):
        raise ValueError("sheet.scale_denominator must be finite and positive")
    if fixed_scale is not None and nozzle_geometry != "catalogue":
        raise ValueError("sheet.scale_denominator requires nozzle_geometry=catalogue")
    sheet = normalize_nozzle_sheet_options({"title": "NOZZLE ORIENTATION / OUTLINE", "drawing_number": "E3D-NOZZLE-OUTLINE", **sheet})
    if fixed_scale is not None:
        sheet["scale_denominator"] = float(fixed_scale)
    width, height, svg, dxf = _location_sheet_preview_options(args)
    options = _drawing_options({"radial_segments": 24, **{key: args[key] for key in (
        "radial_segments", "feature_angle_degrees", "hidden_line_removal", "show_hidden_lines") if key in args}, "views": ["TOP"]})
    metadata = _read_complete_equipment_location(bridge, [refno])
    if metadata.get("ok") is not True:
        return metadata
    result = metadata["result"]
    try:
        pose = _location_equipment_pose(metadata, 0)
        equipment_context = result["context"]
        result["equipment_pose"] = pose
        result["drawing"] = None
        groups = [{"refno": refno, "sources": sources}]
        metadata, combined = _read_location_material(bridge, groups, metadata, [pose], options["radial_segments"],
            **({"chord_tolerance_mm": chord_tolerance} if chord_tolerance is not None else {}),
            **({"use_generated_dishes": True} if generated_dishes else {}))
        if combined is None:
            return metadata
        nozzle = _read_complete_nozzle_location(bridge, targets)
        result["evidence"]["nozzles"] = nozzle["result"]
        if nozzle.get("ok") is not True:
            return _location_sheet_failure(metadata, "nozzle_outline_complete_points_required")
        nozzle_result = nozzle["result"]
        initial = equipment_context["before"]
        contexts = nozzle_result["context"]
        agreement = all(_location_sheet_context_agrees(initial, contexts[side]) for side in ("before", "after"))
        result["context"].update(nozzles=contexts, after=contexts["after"], boundary_agreement=agreement)
        if not agreement:
            return _location_sheet_failure(metadata, "nozzle_outline_context_changed")
        profiles = None
        if nozzle_geometry == "catalogue":
            from .nozzle_catalogue import read_nozzle_catalogue_profiles
            catalog = read_nozzle_catalogue_profiles(bridge, nozzle_result["drawing"], pose, version=metadata["version"])
            result["evidence"]["nozzle_catalogue"] = catalog["evidence"]
            if catalog.get("success") is not True:
                return _location_sheet_failure(metadata, "nozzle_outline_catalogue_geometry_unavailable")
            final_context = _assembly_boundary_context(bridge)
            if not _location_sheet_context_agrees(initial, final_context):
                return _location_sheet_failure(metadata, "nozzle_outline_catalogue_context_changed")
            result["context"].update(after=final_context, boundary_agreement=True)
            profiles = catalog["profiles"]
        built = build_nozzle_outline_sheets(nozzle_result["drawing"], pose, combined, sheet,
            axial_axis=axial, zero_axis=zero, feature_angle_degrees=options["feature_angle_degrees"],
            hidden_line_removal=options["hidden_line_removal"], show_hidden_lines=options["show_hidden_lines"],
            **({"nozzle_profiles":profiles, "chord_tolerance_mm":chord_tolerance} if profiles is not None else {}))
        projection = built["projection"]
        if any(projection.get(flag) is not True for flag in ("success", "available", "complete")):
            return _location_sheet_failure(metadata, "nozzle_outline_projection_failed")
        # The verified WORLD arrays are internal; hashes, ranges and native
        # source observations remain in the public receipt without duplication.
        result["outline_projection"] = {key: value for key, value in projection.items() if key != "source_material"}
        result["outline_projection"].update(explicit_source_membership_verified=True,
            membership_proof_scope="Selected material native descendant proof in result.source_membership; nozzle ownership is unverified.")
        for scene in built["sheets"]:
            scene["explicit_source_membership_verified"] = True
        result.update(success=True, available=True, complete=True, axial_axis=axial, zero_axis=zero,
            drawing={"equipment_pose": pose, "explicit_source_membership_verified": True,
                     "whole_equipment_geometry_verified": False})
    except NozzleOutlineScaleDoesNotFit as exc:
        return _location_sheet_failure(metadata, "nozzle_outline_preparation_failed",
            drawing_diagnostics={"error_code": str(exc), "error_type": "ValueError", **exc.diagnostics})
    except Exception as exc:
        return _location_sheet_failure(metadata, "nozzle_outline_preparation_failed",
            drawing_diagnostics={"error_code": str(exc)[:1024], "error_type": type(exc).__name__})
    return _render_location_sheet_pages(metadata, built["sheets"], width=width, height=height, include_svg=svg,
                                         include_dxf=dxf, coordinate_decimal_places=15)


def _check_connection_pair(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    allowed = {"first_refno", "first_expected_type", "first_point_number", "second_refno",
               "second_expected_type", "second_point_number", "tolerance_mm"}
    required = {"first_refno", "first_point_number", "second_refno", "second_point_number", "tolerance_mm"}
    unknown = set(args) - allowed
    missing = required - set(args)
    if unknown:
        raise ValueError(f"Unknown e3d_check_connection_pair arguments: {', '.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"Missing e3d_check_connection_pair arguments: {', '.join(sorted(missing))}")
    first_refno = _panel_reference(args, "first_refno")
    second_refno = _panel_reference(args, "second_refno")
    first_expected_type = normalize_expected_element_type(args.get("first_expected_type", ""))
    second_expected_type = normalize_expected_element_type(args.get("second_expected_type", ""))
    first_point_number = _integer(args, "first_point_number", 0, 1, 10000)
    second_point_number = _integer(args, "second_point_number", 0, 1, 10000)
    tolerance = args["tolerance_mm"]
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)):
        raise ValueError("tolerance_mm must be a finite number between 0 and 1000000 mm")
    if isinstance(tolerance, float) and not math.isfinite(tolerance):
        raise ValueError("tolerance_mm must be a finite number between 0 and 1000000 mm")
    if tolerance < 0 or tolerance > 1000000:
        raise ValueError("tolerance_mm must be a finite number between 0 and 1000000 mm")
    kwargs = {}
    if first_expected_type:
        kwargs["first_expected_type"] = first_expected_type
    if second_expected_type:
        kwargs["second_expected_type"] = second_expected_type
    return bridge.connection_pair_check(first_refno, first_point_number, second_refno,
                                        second_point_number, float(tolerance), **kwargs)


def _trace_piping_connectivity(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"starts", "max_hops", "max_nodes"}
    if unknown:
        raise ValueError(f"Unknown e3d_trace_piping_connectivity arguments: {', '.join(sorted(unknown))}")
    raw_starts = args.get("starts")
    if not isinstance(raw_starts, list) or not 1 <= len(raw_starts) <= 16:
        raise ValueError("starts must contain between 1 and 16 explicit element/P-point pairs")
    starts = []
    seen = set()
    for index, item in enumerate(raw_starts):
        if not isinstance(item, dict) or not {"refno", "point_number"}.issubset(item) or set(item) - {"refno", "point_number", "expected_type"}:
            raise ValueError(f"starts[{index}] must contain refno and point_number with optional expected_type")
        refno = element_access_refnos([item["refno"]])[0]
        expected_type = _expected_element_type(item)
        point = item["point_number"]
        if isinstance(point, bool) or not isinstance(point, int) or not 1 <= point <= 10000:
            raise ValueError(f"starts[{index}].point_number must be an integer between 1 and 10000")
        key = (refno.casefold(), expected_type.casefold(), point)
        if key in seen:
            raise ValueError("starts must not contain duplicate element/P-point pairs")
        seen.add(key)
        start = {"refno": refno, "point_number": point}
        if expected_type:
            start["expected_type"] = expected_type
        starts.append(start)
    max_hops = _integer(args, "max_hops", 8, 0, 16)
    max_nodes = _integer(args, "max_nodes", 64, len(starts), 128)
    return bridge.trace_connection_network(starts, max_hops, max_nodes)


def _spatial_info(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    wrt = _string(args, "wrt", "WORLD").strip()
    if wrt.upper() in {"WORLD", "OWNER"}:
        wrt = wrt.upper()
    elif not wrt.startswith(("/", "=")):
        raise ValueError("wrt must be WORLD, OWNER, an element name /NAME, or a numeric =database/element reference")
    return bridge.spatial_info(_string(args, "refno").strip(), wrt)


def _transform_coordinates(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"from_wrt", "to_wrt", "points", "vectors"}
    if unknown:
        raise ValueError(f"Unknown e3d_transform_coordinates arguments: {', '.join(sorted(unknown))}")

    def frame(name: str) -> str:
        value = _string(args, name).strip()
        if value.upper() == "WORLD":
            return "WORLD"
        return element_access_refnos([value])[0]

    def triples(name: str) -> list[list[float]]:
        values = args.get(name, [])
        if not isinstance(values, list) or len(values) > 128:
            raise ValueError(f"{name} must be an array of at most 128 three-number arrays")
        return [_vector(value, f"{name}[{index}]") for index, value in enumerate(values)]

    source = frame("from_wrt")
    target = frame("to_wrt")
    points = triples("points")
    vectors = triples("vectors")
    if not 1 <= len(points) + len(vectors) <= 256:
        raise ValueError("Provide between 1 and 256 total points and vectors")
    return bridge.transform_coordinates(source, target, points, vectors)


def _validate_model(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"refno", "depth", "limit", "required_types", "include_snapshot"}
    if unknown:
        raise ValueError(f"Unknown e3d_validate_model arguments: {', '.join(sorted(unknown))}")
    required_types = args.get("required_types", [])
    if not isinstance(required_types, list) or any(not isinstance(item, str) for item in required_types):
        raise ValueError("required_types must be an array of strings")
    include_snapshot = args.get("include_snapshot", True)
    if not isinstance(include_snapshot, bool):
        raise ValueError("include_snapshot must be a boolean")
    return bridge.validate(_string(args, "refno"), _integer(args, "depth", 4, 0, 8),
                           _integer(args, "limit", 200, 1, 2000), required_types,
                           include_snapshot=include_snapshot)


def _delete_element(bridge: BridgeClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    refno = _string(args, "refno").strip()
    if not refno:
        raise ValueError("refno must not be empty")
    dry_run = args.get("dry_run", True)
    if not isinstance(dry_run, bool):
        raise ValueError("dry_run must be a boolean")
    if not dry_run and not settings.allow_commands:
        raise PermissionError("Mutating element operations are disabled; set E3D_MCP_ALLOW_COMMANDS=true explicitly")
    response = bridge.delete_element(refno, dry_run)
    if dry_run:
        return response
    if not isinstance(response, dict):
        return _unknown_delete_response({}, response)
    native = response.get("result")
    if not isinstance(native, dict):
        return _unknown_delete_response(response, native)
    attempted = native.get("delete_attempted") is True
    success_claimed = native.get("success") is True
    if native.get("delete_attempted") is False and not success_claimed:
        return response
    verified = (
        attempted
        and "native_delete_returned" in native
        and success_claimed
        and native.get("verification_status") == "confirmed_deleted"
        and native.get("deletion_verified") is True
        and native.get("readback_refno") == native.get("refno")
        and native.get("readback_reference_is_valid") is False
        and native.get("readback_deleted_state") is True
        and native.get("readback_absent_from_owner") is True
        and native.get("readback_owner_membership_matches_expected") is True
        and isinstance(native.get("owner_refno"), str)
        and bool(native["owner_refno"])
    )
    if verified:
        return response

    still_present = (
        attempted
        and native.get("success") is False
        and native.get("verification_status") == "still_present"
        and native.get("deletion_verified") is False
        and native.get("readback_refno") == native.get("refno")
        and native.get("readback_reference_is_valid") is True
        and native.get("readback_deleted_state") is False
        and native.get("readback_absent_from_owner") is False
        and isinstance(native.get("owner_refno"), str)
        and bool(native["owner_refno"])
    )
    if still_present:
        guarded = dict(response)
        guarded["outcome_uncertain"] = True
        guarded["retry_safe"] = False
        guarded_result = dict(native)
        guarded_result.update({
            "success": False,
            "outcome_uncertain": True,
            "retry_safe": False,
            "error": "The exact target remains valid in its original owner, but this does not prove the delete had no partial effect on its hierarchy. Do not automatically resend the request.",
        })
        guarded["result"] = guarded_result
        return guarded

    # Older add-ins reported Delete()'s Boolean as success. Fail closed until
    # this exact numeric DBREF and its complete original owner membership have
    # an explicit native readback receipt; never capture a false success image.
    return _unknown_delete_response(response, native)


def _unknown_delete_response(response: dict[str, Any], native: Any) -> dict[str, Any]:
    guarded = dict(response)
    if not isinstance(native, dict) and native is not None:
        guarded["unverified_native_result"] = native
    guarded["outcome_uncertain"] = True
    guarded["retry_safe"] = False
    guarded_result = dict(native) if isinstance(native, dict) else {}
    guarded_result.update({
        "success": False,
        "deletion_verified": False,
        "verification_status": "unknown",
        "outcome_uncertain": True,
        "retry_safe": False,
        "error": "The native delete response lacks the complete exact-reference and original-owner verification required to confirm deletion. Do not replay the request.",
    })
    guarded["result"] = guarded_result
    return guarded


def _set_attribute(bridge: BridgeClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    if "value" not in args:
        raise ValueError("value is required")
    refno = _string(args, "refno").strip()
    attribute = _string(args, "attribute").strip()
    value = _string(args, "value")
    if not refno or not attribute:
        raise ValueError("refno and attribute must not be empty")
    dry_run = args.get("dry_run", True)
    if not isinstance(dry_run, bool):
        raise ValueError("dry_run must be a boolean")
    if not dry_run and not settings.allow_commands:
        raise PermissionError("Mutating element operations are disabled; set E3D_MCP_ALLOW_COMMANDS=true explicitly")
    return bridge.set_attribute(refno, attribute, value, dry_run)


def _set_attributes(bridge: BridgeClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    allowed = {"refno", "values", "expected_revision", "dry_run", "capture_after", "capture_width", "capture_height"}
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"Unknown e3d_set_attributes arguments: {', '.join(sorted(unknown))}")
    dry_run = _edit_preview(settings, args)
    refno = _panel_reference(args, "refno")
    values = args.get("values")
    if not isinstance(values, dict) or not 1 <= len(values) <= 20:
        raise ValueError("values must contain 1 to 20 direct-edit attributes")
    string_fields = {"NAME", "DESC", "DUTY"}
    number_fields = {"DIAM", "HEIG", "XLEN", "YLEN", "ZLEN", "DTOP", "DBOT"}
    normalized: dict[str, str] = {}
    number_pattern = re.compile(r"[+-]?(?:(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)\Z")
    for raw_name, value in values.items():
        if not isinstance(raw_name, str) or not isinstance(value, str):
            raise ValueError("values must map attribute names to string values")
        if any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in raw_name):
            raise ValueError("Attribute names must not contain control characters")
        name = raw_name.strip().upper()
        if name not in string_fields | number_fields:
            raise ValueError(f"Attribute is not enabled for direct MCP editing: {raw_name}")
        if name in normalized:
            raise ValueError(f"Duplicate attribute name after normalization: {name}")
        if name in number_fields:
            text = value.strip()
            if not number_pattern.fullmatch(text):
                raise ValueError(f"Numeric attribute {name} requires a finite number in invariant notation")
            try:
                if not math.isfinite(float(text)):
                    raise ValueError(f"Numeric attribute {name} requires a finite number in invariant notation")
            except (OverflowError, ValueError):
                raise ValueError(f"Numeric attribute {name} requires a finite number in invariant notation") from None
        normalized[name] = value
    options: dict[str, Any] = {}
    if "expected_revision" in args:
        revision = _string(args, "expected_revision")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", revision):
            raise ValueError("expected_revision must contain exactly 64 hexadecimal characters")
        options["expected_revision"] = revision.lower()
    elif not dry_run:
        raise ValueError("expected_revision from a preview of the same target and values is required for execution")
    return bridge.set_attributes(refno, normalized, dry_run=dry_run, **options)


def _set_drawlist_visual(bridge: BridgeClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    refno = _string(args, "refno").strip()
    if not refno:
        raise ValueError("refno must not be empty")
    colour = _string(args, "colour")
    transparency = args.get("transparency")
    if transparency is not None and (isinstance(transparency, bool) or not isinstance(transparency, int) or not 0 <= transparency <= 100):
        raise ValueError("transparency must be an integer between 0 and 100 or null")
    for name in ("edges", "visibility", "shallow", "add", "dry_run"):
        if name in args and not isinstance(args[name], bool):
            raise ValueError(f"{name} must be a boolean")
    edges = args.get("edges")
    visibility = args.get("visibility")
    shallow = args.get("shallow")
    add = args.get("add", True)
    dry_run = args.get("dry_run", True)
    if not dry_run and not settings.allow_commands:
        raise PermissionError("Mutating Drawlist operations are disabled; set E3D_MCP_ALLOW_COMMANDS=true explicitly")
    return bridge.set_drawlist_visual(refno, colour, transparency, edges, visibility, shallow, add, dry_run)


def _fit_view(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    shaded = args.get("shaded", True)
    if not isinstance(shaded, bool):
        raise ValueError("shaded must be a boolean")
    isometric = args.get("isometric", True)
    if not isinstance(isometric, bool):
        raise ValueError("isometric must be a boolean")
    auto_add_current = args.get("auto_add_current", True)
    if not isinstance(auto_add_current, bool):
        raise ValueError("auto_add_current must be a boolean")
    return bridge.fit_view(shaded, isometric, auto_add_current)


def _view_state(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    if args:
        raise ValueError("e3d_view_state does not accept arguments")
    return bridge.view_state()


def _view_clipping_state(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    if args:
        raise ValueError("e3d_view_clipping_state does not accept arguments")
    return bridge.view_clipping_state()


def _observe_view(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"samples", "interval_ms", "include_clipping"}
    if unknown:
        raise ValueError(f"Unknown e3d_observe_view arguments: {', '.join(sorted(unknown))}")
    include_clipping = args.get("include_clipping", False)
    if not isinstance(include_clipping, bool):
        raise ValueError("include_clipping must be a boolean")
    if include_clipping:
        return bridge.observe_view(_integer(args, "samples", 3, 2, 10),
                                   _integer(args, "interval_ms", 100, 50, 500), include_clipping=True)
    return bridge.observe_view(_integer(args, "samples", 3, 2, 10),
                               _integer(args, "interval_ms", 100, 50, 500))


def _display_state(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"refno"}
    if unknown:
        raise ValueError(f"Unknown e3d_display_state arguments: {', '.join(sorted(unknown))}")
    return bridge.display_state(_panel_reference(args, "refno"))


def _display_snapshot(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    if args:
        raise ValueError("e3d_display_snapshot does not accept arguments")
    return bridge.display_snapshot()


def _diagnose_element(bridge: BridgeClient, args: dict[str, Any],
                      receipt_store: DiagnosticReceiptStore | None = None) -> ImageToolResult | dict[str, Any]:
    unknown = set(args) - {"refno", "include_image", "include_clipping", "capture_width", "capture_height", "evidence_detail", "retain_receipt"}
    if unknown:
        raise ValueError(f"Unknown e3d_diagnose_element arguments: {', '.join(sorted(unknown))}")
    detail = args.get("evidence_detail", "full")
    if not isinstance(detail, str) or detail not in {"full", "compact"}:
        raise ValueError("evidence_detail must be 'full' or 'compact'")
    retain = args.get("retain_receipt", False)
    if not isinstance(retain, bool):
        raise ValueError("retain_receipt must be a boolean")
    if retain and receipt_store is None:
        raise ValueError("retain_receipt requires a server receipt store")
    include_clipping = args.get("include_clipping", False)
    if not isinstance(include_clipping, bool):
        raise ValueError("include_clipping must be a boolean")
    value = diagnose_element(bridge, _string(args, "refno"),
        include_image=args.get("include_image", False),
        include_clipping=include_clipping,
        capture_width=args.get("capture_width", 1280), capture_height=args.get("capture_height", 960))
    info = None
    if retain:
        try:
            info = receipt_store.put(value)
        except Exception:
            info = {"status": "failed", "reason": "storage_error", "storage": "mcp_process_memory"}
    retention_failed = retain and (not isinstance(info, dict) or info.get("status") != "stored")
    projected = compact_diagnosis(value) if detail == "compact" and not retention_failed else value
    if not retain:
        return projected
    metadata = dict(projected.metadata if isinstance(projected, ImageToolResult) else projected)
    result = dict(metadata.get("result", {}))
    if detail == "compact" and retention_failed:
        result["evidence_compaction"] = {
            "requested": "compact", "applied": False, "reason": "receipt_retention_failed",
        }
    result["diagnostic_receipt"] = info
    metadata["result"] = result
    if isinstance(projected, ImageToolResult):
        return ImageToolResult(data=projected.data, mime_type=projected.mime_type, metadata=metadata)
    return metadata


def _diagnostic_receipt(receipt_store: DiagnosticReceiptStore, args: dict[str, Any]) -> ImageToolResult | dict[str, Any]:
    unknown = set(args) - {"receipt_id", "include_image"}
    if unknown:
        raise ValueError(f"Unknown e3d_diagnostic_receipt arguments: {', '.join(sorted(unknown))}")
    receipt_id = args.get("receipt_id")
    if not isinstance(receipt_id, str) or re.fullmatch(r"diag_[0-9a-f]{32}", receipt_id) is None:
        raise ValueError("receipt_id must be diag_ followed by 32 lowercase hexadecimal characters")
    include_image = args.get("include_image", False)
    if not isinstance(include_image, bool):
        raise ValueError("include_image must be a boolean")
    return receipt_store.get(receipt_id, include_image=include_image)


def _compare_diagnostic_receipts(receipt_store: DiagnosticReceiptStore, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"before_receipt_id", "after_receipt_id", "difference_limit"}
    if unknown:
        raise ValueError(f"Unknown e3d_compare_diagnostic_receipts arguments: {', '.join(sorted(unknown))}")
    return compare_diagnostic_receipts(receipt_store, args.get("before_receipt_id"), args.get("after_receipt_id"),
                                       difference_limit=args.get("difference_limit", 100))


def _remove_drawlist_member(bridge: BridgeClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"refno", "expected_revision", "dry_run", "capture_after", "capture_width", "capture_height"}
    if unknown:
        raise ValueError(f"Unknown e3d_remove_drawlist_member arguments: {', '.join(sorted(unknown))}")
    refno = _panel_reference(args, "refno")
    dry_run = args.get("dry_run", True)
    if not isinstance(dry_run, bool):
        raise ValueError("dry_run must be a boolean")
    options: dict[str, Any] = {}
    if "expected_revision" in args:
        revision = _string(args, "expected_revision")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", revision):
            raise ValueError("expected_revision must contain exactly 64 hexadecimal characters")
        options["expected_revision"] = revision.lower()
    elif not dry_run:
        raise ValueError("expected_revision from a current removal preview with the same refno is required for execution")
    if not dry_run and not settings.allow_commands:
        raise PermissionError("Drawlist removal is disabled; set E3D_MCP_ALLOW_COMMANDS=true explicitly")
    return bridge.remove_drawlist_member(refno, dry_run=dry_run, **options)


def _set_primitive_visual(bridge: BridgeClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    allowed = {"refno", "expected_revision", "colour", "transparency", "edges", "visibility", "shallow",
               "dry_run", "capture_after", "capture_width", "capture_height"}
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"Unknown e3d_set_primitive_visual arguments: {', '.join(sorted(unknown))}")
    refno = _panel_reference(args, "refno")
    dry_run = args.get("dry_run", True)
    if not isinstance(dry_run, bool):
        raise ValueError("dry_run must be a boolean")
    options: dict[str, Any] = {}
    if "colour" in args:
        options["colour"] = _string(args, "colour")
    if "transparency" in args:
        options["transparency"] = _integer(args, "transparency", 0, 0, 100)
    for field in ("edges", "visibility", "shallow"):
        value = args.get(field)
        if value is not None:
            if not isinstance(value, bool):
                raise ValueError(f"{field} must be a boolean or null")
            options[field] = value
    if not options.get("colour", "").strip() and not any(
        field in options for field in ("transparency", "edges", "visibility", "shallow")
    ):
        raise ValueError("At least one nonempty colour, transparency or non-null edges, visibility or shallow value is required")
    if "expected_revision" in args:
        revision = _string(args, "expected_revision")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", revision):
            raise ValueError("expected_revision must contain exactly 64 hexadecimal characters")
        options["expected_revision"] = revision.lower()
    elif not dry_run:
        raise ValueError("expected_revision from the preview of the same visual changes is required for execution")
    if not dry_run and not settings.allow_commands:
        raise PermissionError("Primitive visual changes are disabled; set E3D_MCP_ALLOW_COMMANDS=true explicitly")
    return bridge.set_primitive_visual(refno, dry_run=dry_run, **options)


def _set_view(bridge: BridgeClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    allowed = {"preset", "fit", "target_refno", "padding", "dry_run",
               "capture_after", "capture_width", "capture_height"}
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"Unknown e3d_set_view arguments: {', '.join(sorted(unknown))}")
    preset = _string(args, "preset", "isometric").strip().lower()
    if preset not in {"keep", "isometric", "x_positive", "x_negative", "y_positive", "y_negative", "z_positive", "z_negative"}:
        raise ValueError("preset must be keep, isometric, x_positive, x_negative, y_positive, y_negative, z_positive, or z_negative")
    fit = _string(args, "fit", "none").strip().lower()
    if fit not in {"none", "drawlist", "target"}:
        raise ValueError("fit must be none, drawlist, or target")
    target_refno = _string(args, "target_refno").strip()
    if fit == "target" and not target_refno:
        raise ValueError("target_refno is required when fit=target")
    if fit != "target" and target_refno:
        raise ValueError("target_refno is only allowed when fit=target")
    if preset == "keep" and fit == "none":
        raise ValueError("Specify a camera preset or fit; keep with fit=none does not change the view")
    padding = args.get("padding", 1.15)
    if isinstance(padding, bool) or not isinstance(padding, (int, float)) or not math.isfinite(padding) or not 1 <= padding <= 4:
        raise ValueError("padding must be a finite number between 1 and 4")
    dry_run = args.get("dry_run", True)
    if not isinstance(dry_run, bool):
        raise ValueError("dry_run must be a boolean")
    if not dry_run and not settings.allow_commands:
        raise PermissionError("View changes are disabled; set E3D_MCP_ALLOW_COMMANDS=true explicitly")
    return bridge.set_view(preset, fit, target_refno, float(padding), dry_run)


def _set_camera(bridge: BridgeClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    required = {"centre", "direction", "radius", "perspective", "extra_twist_degrees"}
    allowed = required | {"expected_revision", "dry_run", "capture_after", "capture_width", "capture_height"}
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"Unknown e3d_set_camera arguments: {', '.join(sorted(unknown))}")
    if required - set(args):
        raise ValueError(f"Missing camera fields: {', '.join(sorted(required - set(args)))}")

    def finite(value: Any, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a finite number")
        try:
            number = float(value)
        except (OverflowError, ValueError) as error:
            raise ValueError(f"{name} must be a finite number") from error
        if not math.isfinite(number):
            raise ValueError(f"{name} must be a finite number")
        return number

    def vector(name: str) -> list[float]:
        value = args[name]
        if not isinstance(value, list) or len(value) != 3:
            raise ValueError(f"{name} must contain exactly three finite numbers")
        return [finite(item, name) for item in value]

    centre, direction = vector("centre"), vector("direction")
    if any(abs(value) > 3.4028234663852886e38 for value in centre):
        raise ValueError("centre must fit the SDK's readable single-precision range")
    if not any(value != 0 for value in direction):
        raise ValueError("direction must be nonzero")
    radius = finite(args["radius"], "radius")
    if radius <= 0:
        raise ValueError("radius must be positive")
    twist = finite(args["extra_twist_degrees"], "extra_twist_degrees")
    perspective, dry_run = args["perspective"], args.get("dry_run", True)
    if not isinstance(perspective, bool) or not isinstance(dry_run, bool):
        raise ValueError("perspective and dry_run must be booleans")
    options = {}
    if "expected_revision" in args:
        revision = _string(args, "expected_revision")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", revision):
            raise ValueError("expected_revision must contain exactly 64 hexadecimal characters")
        options["expected_revision"] = revision.lower()
    elif not dry_run:
        raise ValueError("expected_revision from the same camera preview is required for execution")
    if not dry_run and not settings.allow_commands:
        raise PermissionError("Camera changes are disabled; set E3D_MCP_ALLOW_COMMANDS=true explicitly")
    return bridge.set_camera(centre, direction, radius, perspective, twist, dry_run=dry_run, **options)


def _restore_view(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    fit = args.get("fit", True)
    if not isinstance(fit, bool):
        raise ValueError("fit must be a boolean")
    return bridge.restore_view(fit)

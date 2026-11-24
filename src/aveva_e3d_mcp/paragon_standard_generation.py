"""Generate a Catalogue SCOM from a project specification or external mapping."""
from __future__ import annotations

from decimal import Decimal, localcontext
import hashlib
import json
import math
from typing import Any

from . import paragon_component_copy as copy
from . import paragon_component_parameters as param
from .paragon_reference_trace import (
    MAX_TRACE_LIMIT, PATH as TRACE_PATH, validate_paragon_reference_trace_receipt,
)
from .bridge import BridgeClient, BridgeError, _contains_native_fault
from .protocol import McpServer


TOOL = "e3d_paragon_generate_from_standard"
OPERATION = "paragon_generate_from_standard"
VERSION = "E3D21"
_EXECUTION = {"request_id", "expected_session_id", "expected_bridge_build", "expected_revision"}
_UNITS = {"mm": Decimal(1), "in": Decimal("25.4"), "1": Decimal(1)}
_SLOTS = {"parameter_index", "quantity", "source_value", "source_unit", "param_unit"}
_RECEIPTS = ("health", "reference_trace", "copy_preview", "copy_execute", "parameters_preview", "parameters_execute")


def _closed(value: Any, required: set[str], optional: set[str], field: str) -> None:
    if type(value) is not dict or not required <= value.keys() or value.keys() - required - optional:
        raise ValueError(f"Invalid fields for {field}.")


def _reference(value: Any, field: str) -> str:
    if not copy._refno(value):
        raise ValueError(f"{field} must be a canonical numeric DBREF.")
    return value


def _text(value: Any, maximum: int, field: str) -> None:
    if not copy._text(value, maximum, printable=True) or value.strip() != value:
        raise ValueError(f"{field} must be printable, nonblank text without surrounding whitespace.")


def _external_parameters(basis: dict[str, Any]) -> tuple[dict[str, Any], list[float]]:
    _closed(basis, {"kind", "standard", "edition", "locator", "source_refno", "mapping"},
            {"document_sha256"}, "external_standard basis")
    _reference(basis["source_refno"], "source_refno")
    for key, maximum in (("standard", 128), ("edition", 80), ("locator", 256)):
        _text(basis[key], maximum, key)
    if "document_sha256" in basis and not copy._sha(basis["document_sha256"]):
        raise ValueError("document_sha256 must be a lowercase SHA-256 digest.")
    rows = basis["mapping"]
    if type(rows) is not list or not 1 <= len(rows) <= 128:
        raise ValueError("mapping must cover 1 to 128 PARAM positions.")
    values: dict[int, float] = {}
    mapping: dict[int, dict[str, Any]] = {}
    for row in rows:
        _closed(row, _SLOTS, set(), "PARAM mapping row")
        index = row["parameter_index"]
        if type(index) is not int or not 1 <= index <= len(rows) or index in mapping:
            raise ValueError("Mapping indices must cover each 1-based PARAM position once.")
        _text(row["quantity"], 128, "quantity")
        number = row["source_value"]
        if type(number) not in (int, float):
            raise ValueError("source_value must be a finite number.")
        try:
            finite = math.isfinite(number)
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError("source_value must be representable as a finite DOUBLE.")
        source_unit, target_unit = row["source_unit"], row["param_unit"]
        if (type(source_unit) is not str or type(target_unit) is not str
                or source_unit not in _UNITS or target_unit not in _UNITS
                or (source_unit == "1") != (target_unit == "1")):
            raise ValueError("Use compatible units mm/in or dimensionless 1 on both sides.")
        if source_unit == target_unit:
            value = float(number)
        else:
            with localcontext() as context:
                context.prec = 400
                value = float(Decimal(str(number)) * _UNITS[source_unit] / _UNITS[target_unit])
        if not math.isfinite(value) or (value == 0.0 and number != 0):
            raise ValueError("Converted PARAM value exceeds the finite DOUBLE range.")
        mapping[index] = dict(row)
        values[index] = value
    order = range(1, len(rows) + 1)
    return {**basis, "mapping": [mapping[index] for index in order]}, [values[index] for index in order]


def _input(args: Any) -> tuple[dict[str, Any], list[float] | None]:
    _closed(args, {"basis", "parent_refno", "name"}, _EXECUTION | {"dry_run", "parameter_request_id"}, "request")
    basis = args["basis"]
    if type(basis) is not dict:
        raise ValueError("basis must be an object.")
    kind = basis.get("kind")
    if kind == "e3d_specification":
        _closed(basis, {"kind", "spec_refno", "spco_refno"}, set(), "e3d_specification basis")
        spec = _reference(basis["spec_refno"], "spec_refno")
        spco = _reference(basis["spco_refno"], "spco_refno")
        if spec == spco:
            raise ValueError("SPEC and SPCO must be distinct.")
        basis, parameters = dict(basis), None
    elif kind == "external_standard":
        basis, parameters = _external_parameters(basis)
    else:
        raise ValueError("Choose an e3d_specification or external_standard basis.")

    parent = _reference(args["parent_refno"], "parent_refno")
    dry_run = args.get("dry_run", True)
    if type(dry_run) is not bool:
        raise ValueError("dry_run must be a boolean.")
    # Use the writer's public validator before any reads, including its strict
    # execution token and NAME rules. Native SPEC resolution supplies the source later.
    candidate = basis["source_refno"] if parameters is not None else basis["spco_refno"]
    tokens = {key: args[key] for key in _EXECUTION if key in args}
    copy._parameters({"source_refno": candidate, "parent_refno": parent, "name": args["name"],
                      "dry_run": dry_run, **tokens})
    if "parameter_request_id" in args and (dry_run or parameters is None):
        raise ValueError("parameter_request_id is only used for external standard execution.")
    if not dry_run and parameters is not None:
        parameter_id = args.get("parameter_request_id")
        if not copy._uuid(parameter_id) or parameter_id == tokens["request_id"]:
            raise ValueError("External standard execution needs a distinct, canonical parameter_request_id.")
    return {**args, "basis": basis, "dry_run": dry_run}, parameters


def _observed_basis(trace_receipt: dict[str, Any], basis: dict[str, Any], parent: str) -> tuple[str, list[str]]:
    result = trace_receipt["result"]
    trace, codes = result["trace"], result["db_type_codes"]

    def matches(path: str, noun: str, refno: str | None = None) -> bool:
        row = trace.get(path)
        return (row is not None and row["status"] == "resolved" and row["type"] == noun
                and codes.get(path) == 2 and (refno is None or row["ref"] == refno))

    if not matches("root", "SPCO", basis["spco_refno"]):
        raise ValueError("The explicit SPCO could not be verified in Catalogue.")
    path = "root.OWNER"
    ownership = []
    while matches(path, "SELE"):
        ownership.append(trace[path]["ref"])
        path += ".OWNER"
    if not matches(path, "SPEC", basis["spec_refno"]):
        raise ValueError("The SPCO ownership chain did not reach the requested SPEC within the observed paths.")
    if not matches("root.CATREF", "SCOM") or not matches("root.CATREF.OWNER", "CATE", parent):
        raise ValueError("SPCO.CATREF must resolve to an SCOM owned by the requested Catalogue CATE.")
    return trace["root.CATREF"]["ref"], [*ownership, trace[path]["ref"]]


def _check_features(health: dict[str, Any]) -> None:
    if _contains_native_fault(health):
        raise BridgeError("The native database fault is latched.", diagnostics=health)
    features = health.get("api_features")
    if (health.get("version") != VERSION or health.get("connected") is not True
            or not copy._text(health.get("session_id"), 256, printable=True)
            or not copy._uuid(health.get("bridge_build"))
            or type(features) is not list
            or not all(feature in features for feature in ("paragon_component_copy", "paragon_component_parameters"))):
        raise ValueError("External standard generation requires a connected E3D21 Addin with copy and PARAM features.")


def _same_context(trace_receipt: dict[str, Any], copy_preview: dict[str, Any]) -> bool:
    observed = trace_receipt["result"]["context"]["after"]
    prepared = copy_preview["result"]["before"]["context"]
    if observed is None:
        return False
    keys = ("session_id", "bridge_build", "project", "mdb")
    ce = prepared["current_element"]
    return (all(observed[key] == prepared[key] for key in keys)
            and observed["current_element"] == {key: ce[key] for key in ("refno", "type", "db_type_code")})


def _revision(request: dict[str, Any], copy_preview: dict[str, Any], source_refno: str,
              ownership: list[str] | None, parameters: list[float] | None) -> str:
    evidence = {
        "operation": OPERATION,
        "basis": request["basis"],
        "source_refno": source_refno,
        "spec_ownership": ownership,
        "parameter_bits": [param._bit_string(value) for value in parameters] if parameters is not None else None,
        "parent_refno": request["parent_refno"],
        "name": request["name"],
        "copy_revision": copy_preview["result"]["scoped_revision"],
        "session": copy_preview["session_id"],
        "build": copy_preview["bridge_build"],
    }
    encoded = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _verified_failed_copy_ref(error: BridgeError, execution: dict[str, Any]) -> str | None:
    """Recover a verified identity from the append evidence of an interrupted copy."""
    diagnostics = error.diagnostics
    native = diagnostics.get("result")
    if type(native) is not dict:
        return None
    session, build = execution["expected_session_id"], execution["expected_bridge_build"]
    if (diagnostics.get("version"), diagnostics.get("session_id"), diagnostics.get("bridge_build")) != (VERSION, session, build):
        return None
    try:
        expected = copy._parameters(execution)
        if (native["status"] != "failed" or native["operation"] != copy.OPERATION or native["dry_run"] is not False
                or native["request_id"] != expected["request_id"] or native["scoped_revision"] != expected["expected_revision"]
                or native["session_id"] != session or native["bridge_build"] != build
                or native["request"] != {key: expected[key] for key in ("source_refno", "parent_refno", "name", "recipe")}):
            return None
        progress = copy._progress(native["progress"])
        refno = progress["known_new_refno"]
        if (progress["known_new_refno_verified"] is not True or progress["native_run_attempted"] is not True
                or not copy._refno(refno) or refno in (expected["source_refno"], expected["parent_refno"])):
            return None
        before, after = native["before"], native["after"]
        if (not copy._before(before, expected, session, build) or not copy._module(after["module"])
                or not copy._context(after["context"], session, build, "failed copy context")
                or not all(before["context"][key] == after["context"][key] for key in ("project", "mdb", "instance_binding"))
                or not copy._members(after["members"], after["members_complete"], 101)):
            return None
        owner = after["new_owner"]
        copy._full_identity(owner, "verified new owner")
        first, last = before["members"], after["members"]
        if (not copy._kind(owner, "CATE") or owner["refno"] != expected["parent_refno"]
                or len(last) != len(first) + 1 or first != last[:-1]
                or after["verification"]["new_identity"] is not True
                or after["verification"]["parent_append"] is not True):
            return None
        child = last[-1]
        if (child["refno"] != refno or child["type"] != "SCOM" or child["db_type_code"] != 2
                or not child["name"].isascii() or child["name"].casefold() != expected["name"].casefold()):
            return None
        template = after["new_template"]
        if template is not None:
            if not copy._template(template, refno, session, build, after["context"]):
                return None
            target = template["target"]
            if (not copy._kind(target, "SCOM") or copy._identity_key(target) != copy._identity_key(child)
                    or template["leaf"]["is_leaf"] is not True or target["name_error"] is not None
                    or not target["name"].isascii() or target["name"].casefold() != child["name"].casefold()):
                return None
        return refno
    except (KeyError, TypeError, AttributeError, ValueError, OverflowError):
        return None


def generate_from_standard(bridge: BridgeClient, args: Any) -> dict[str, Any]:
    request, parameters = _input(args)
    dry_run = request["dry_run"]
    if not dry_run and bridge.settings.allow_commands is not True:
        raise ValueError("Generation requires E3D_MCP_ALLOW_COMMANDS=true.")
    copy._runtime(bridge)

    receipts = dict.fromkeys(_RECEIPTS)
    outcome = {"operation": OPERATION, "dry_run": dry_run, "basis": request["basis"],
               "parent_refno": request["parent_refno"], "name": request["name"], "source_refno": None,
               "status": "blocked", "success": False, "complete": False, "scoped_revision": None,
               "known_new_refno": None, "parameters": parameters, "receipts": receipts, "error": None,
               "copy_status_args": None, "parameters_status_args": None,
               "specification_usage_updated": False, "engineering_validated": None}
    response = {"ok": True, "version": VERSION, "session_id": None, "bridge_build": None, "result": outcome}
    if not dry_run:
        outcome["copy_status_args"] = {"request_id": request["request_id"],
                                       "expected_session_id": request["expected_session_id"]}
        if parameters is not None:
            outcome["parameters_status_args"] = {"request_id": request["parameter_request_id"],
                                                 "expected_session_id": request["expected_session_id"]}
    stage = "reference_trace" if parameters is None else "health"
    ownership = None
    try:
        if parameters is None:
            trace_args = {"refno": request["basis"]["spco_refno"], "trace_limit": MAX_TRACE_LIMIT}
            raw = bridge._request("POST", TRACE_PATH, trace_args, version=VERSION)
            trace = validate_paragon_reference_trace_receipt(raw, trace_args)
            receipts["reference_trace"] = trace
            response.update(session_id=trace["session_id"], bridge_build=trace["bridge_build"])
            source, ownership = _observed_basis(trace, request["basis"], request["parent_refno"])
        else:
            health = bridge._request("GET", "/health", version=VERSION)
            receipts["health"] = health
            _check_features(health)
            source = request["basis"]["source_refno"]
        outcome["source_refno"] = source
        stage = "copy_preview"
        copy_args = {"source_refno": source, "parent_refno": request["parent_refno"], "name": request["name"]}
        preview = copy.paragon_component_copy(bridge, copy_args)
        receipts["copy_preview"] = preview
        if preview["result"]["status"] != "prepared":
            raise ValueError("Native Catalogue copy preview is blocked. Inspect its receipt.")
        if receipts["reference_trace"] is not None and not _same_context(receipts["reference_trace"], preview):
            raise ValueError("SPEC trace and native copy preview observed different bridge or Catalogue contexts.")
        session, build = preview["session_id"], preview["bridge_build"]
        response.update(session_id=session, bridge_build=build)
        if parameters is not None:
            if (session, build) != (health["session_id"], health["bridge_build"]):
                raise ValueError("The Addin changed between the feature check and copy preview.")
            numbers = copy._numbers(preview["result"]["before"]["source_template"])
            if numbers is None or len(numbers) != len(parameters):
                raise ValueError("The mapping must cover the exact PARAM length of the verified source template.")
        revision = _revision(request, preview, source, ownership, parameters)
        outcome["scoped_revision"] = revision
        if dry_run:
            outcome.update(status="prepared", success=True, complete=True)
            return response
        if (session != request["expected_session_id"] or build != request["expected_bridge_build"]
                or revision != request["expected_revision"]):
            raise ValueError("Generation preview changed. Review current evidence before executing.")

        stage = "copy_execute"
        execution = {
            **copy_args, "dry_run": False, "request_id": request["request_id"],
            "expected_session_id": session, "expected_bridge_build": build,
            "expected_revision": preview["result"]["scoped_revision"],
        }
        try:
            copied = copy.paragon_component_copy(bridge, execution)
        except BridgeError as error:
            outcome["known_new_refno"] = _verified_failed_copy_ref(error, execution)
            raise
        receipts["copy_execute"] = copied
        new_refno = copied["result"]["progress"]["known_new_refno"]
        outcome["known_new_refno"] = new_refno

        if parameters is not None:
            stage = "parameters_preview"
            param_args = {"refno": new_refno, "parameters": parameters}
            param_preview = param.paragon_component_parameters_set(bridge, param_args)
            receipts["parameters_preview"] = param_preview
            if param_preview["result"]["status"] != "prepared":
                raise ValueError("Native PARAM preview is blocked. The copy receipt still identifies the new SCOM.")
            after_copy = copied["result"]["after"]
            before_param = param_preview["result"]["before"]
            copy_numbers = after_copy["new_template"]["parameters"]["values"]
            param_numbers = before_param["template"]["parameters"]["values"]
            if (before_param["context"] != after_copy["context"]
                    or copy._identity_key(before_param["owner"]) != copy._identity_key(after_copy["new_owner"])
                    or copy._identity_key(before_param["template"]["target"])
                    != copy._identity_key(after_copy["new_template"]["target"])
                    or [row["value"] for row in copy_numbers] != [row["value"] for row in param_numbers]):
                raise ValueError("New SCOM or Catalogue context changed between copy and parameter preparation.")
            stage = "parameters_execute"
            receipts["parameters_execute"] = param.paragon_component_parameters_set(bridge, {
                **param_args, "dry_run": False, "request_id": request["parameter_request_id"],
                "expected_session_id": session, "expected_bridge_build": build,
                "expected_revision": param_preview["result"]["scoped_revision"],
            })
        outcome.update(status="executed", success=True, complete=True)
    except (BridgeError, ValueError) as error:
        if stage in {"copy_execute", "parameters_preview", "parameters_execute"}:
            outcome["status"] = "partial"
        outcome["error"] = {"stage": stage, "message": str(error), "http_status": getattr(error, "http_status", None),
                            "diagnostics": getattr(error, "diagnostics", {})}
    return response


def register_paragon_standard_generation(server: McpServer, bridge: BridgeClient) -> None:
    ref = {"type": "string", "minLength": 4, "maxLength": 22, "pattern": copy._REF_PATTERN}
    sha = {"type": "string", "pattern": r"^[a-f0-9]{64}(?![\s\S])"}
    uuid = {"type": "string", "minLength": 36, "maxLength": 36, "format": "uuid"}
    unit = {"type": "string", "enum": list(_UNITS)}
    basis = {"type": "object", "additionalProperties": False, "required": ["kind"], "properties": {
        "kind": {"enum": ["e3d_specification", "external_standard"]}, "spec_refno": ref, "spco_refno": ref,
        "standard": {"type": "string", "minLength": 1, "maxLength": 128},
        "edition": {"type": "string", "minLength": 1, "maxLength": 80},
        "locator": {"type": "string", "minLength": 1, "maxLength": 256},
        "document_sha256": sha, "source_refno": ref,
        "mapping": {"type": "array", "minItems": 1, "maxItems": 128, "items": {
            "type": "object", "additionalProperties": False, "required": sorted(_SLOTS), "properties": {
                "parameter_index": {"type": "integer", "minimum": 1, "maximum": 128},
                "quantity": {"type": "string", "minLength": 1, "maxLength": 128},
                "source_value": {"type": "number"}, "source_unit": unit, "param_unit": unit,
            }}},
    }}
    fields_by_kind = {
        "e3d_specification": {"spec_refno", "spco_refno"},
        "external_standard": {"standard", "edition", "locator", "source_refno", "mapping"},
    }
    basis["allOf"] = [
        {"if": {"properties": {"kind": {"const": kind}}},
         "then": {"required": sorted(fields),
                  "not": {"anyOf": [{"required": [field]} for field in sorted(forbidden)]}}}
        for kind, fields in fields_by_kind.items()
        for forbidden in [set(basis["properties"]) - fields - {"kind"} -
                          ({"document_sha256"} if kind == "external_standard" else set())]
    ]
    schema = {"type": "object", "additionalProperties": False, "required": ["basis", "parent_refno", "name"],
              "properties": {"basis": basis, "parent_refno": ref,
                             "name": {"type": "string", "minLength": 2, "maxLength": 50, "pattern": "^" + copy._NAME_PATTERN},
                             "dry_run": {"type": "boolean", "default": True}, "request_id": uuid,
                             "parameter_request_id": uuid, "expected_bridge_build": uuid, "expected_revision": sha,
                             "expected_session_id": {"type": "string", "minLength": 1, "maxLength": 256}},
              "allOf": [{"if": {"required": ["dry_run"], "properties": {"dry_run": {"const": False}}},
                         "then": {"required": sorted(_EXECUTION)},
                         "else": {"not": {"anyOf": [{"required": [field]}
                                                    for field in sorted(_EXECUTION | {"parameter_request_id"})]}}}]}
    server.add_tool(
        TOOL, "Preview or generate one Catalogue SCOM in E3D21 PARAGON. Choose an explicit E3D SPEC/SPCO "
        "and its traced CATREF template, or supply an external standard edition, source locator, template, "
        "and complete dimension-to-PARAM mapping. Execution requires the reviewed generation revision and "
        "caller-owned request IDs. External generation creates the SCOM first, then writes and verifies PARAM. "
        "The result retains each native receipt and any verified new REF. Engineering review remains required.",
        schema, lambda args: generate_from_standard(bridge, args), read_only=False,
    )

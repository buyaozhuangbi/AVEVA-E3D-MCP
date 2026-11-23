"""Write one complete bounded PARAM array to an explicit Catalogue SCOM.

The public request contains numbers. Binary64 bit strings derived by Python
bind the wire intent and the native observations across net48 JSON zero-sign
loss. This operation has its own revision and managed journal; it neither
creates an element nor automatically retries a write.
"""
from __future__ import annotations

from datetime import datetime
import math
import re
import struct
from typing import Any

from .bridge import BridgeClient, BridgeError, _contains_native_fault
from .paragon_component_copy import (
    _IDENTITY, _JOURNAL_FIELDS, _JOURNAL_SCOPE, _REF_PATTERN, _UUID_PATTERN,
    _context, _core_identity, _full_identity, _identity_key, _kind, _module, _numbers, _refno, _sha,
    _status_parameters, _template, _text, _uuid,
)
from .protocol import McpServer


VERSION = "E3D21"
TOOL = "e3d_paragon_component_parameters_set"
STATUS_TOOL = "e3d_paragon_component_parameters_status"
PATH = "/api/v1/paragon/component-parameters"
STATUS_PATH = PATH + "/status"
OPERATION = "paragon_component_parameters_set"
MAXIMUM_PARAMETERS = 128

_BITS = re.compile(r"[a-f0-9]{16}")
_ERROR_CODE = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_EXECUTION_FIELDS = {"request_id", "expected_session_id", "expected_bridge_build", "expected_revision"}
_BASE_FIELDS = {"refno", "parameters", "dry_run"}
_REQUEST_FIELDS = {"refno", "parameters", "parameter_bits"}
_RESULT_FIELDS = {
    "operation", "dry_run", "request", "session_id", "bridge_build", "request_id", "status", "success", "complete",
    "scoped_revision", "before", "after", "progress", "errors", "journal", "scope", "this_submission_executed", "request_replayed",
}
_EVIDENCE_FIELDS = {"module", "context", "template", "owner", "setable", "parameter_bits", "target_binding"}
_PROGRESS_FIELDS = {
    "known_target_refno", "known_target_refno_verified", "parameter_write_attempted", "parameter_write_returned",
    "parameters_equal_request", "readback_complete", "context_unchanged", "no_change", "outcome_uncertain",
    "native_fault_latched", "retry_safe",
}
_SUMMARY_FIELDS = _PROGRESS_FIELDS | {"error_code"}
_SCOPE = {
    "kind": "fixed_catalogue_scom_numeric_parameters_update", "backend": "native_sdk_double_array_setter",
    "parameter_comparison": "complete_finite_binary64_bits", "whole_array_replacement": True,
    "array_length_change_allowed": False, "units_converted": False, "units_verified": False,
    "geometry_observed": False, "geometry_equivalence_verified": False, "word_parameters_observed": False,
    "hidden_attributes_observed": False, "all_references_covered": False, "whole_clone_fingerprint": False,
    "target_creation_verified": False, "engineering_validated": None, "write_permission_verified": None,
    "savework_performed": False, "automatic_ce_restore": False, "atomic_snapshot": False,
    "scoped_revision_is_lock": False, "whole_project_coverage": False,
}


def _need(condition: bool, field: str) -> None:
    if not condition:
        raise ValueError("Invalid PARAGON component-PARAM receipt: " + field + ".")


def _fields(value: Any, names: set[str], field: str) -> dict[str, Any]:
    _need(type(value) is dict and value.keys() == names, field + " fields")
    return value


def _float(value: Any) -> float:
    if type(value) not in (int, float):
        raise ValueError("parameters must contain only finite DOUBLE numbers, with booleans excluded.")
    try:
        result = float(value)
    except OverflowError:
        raise ValueError("parameters must contain only finite DOUBLE numbers.") from None
    if not math.isfinite(result):
        raise ValueError("parameters must contain only finite DOUBLE numbers.")
    return result


def _bit_string(value: float) -> str:
    return struct.pack(">d", value).hex()


def _parameters(args: Any) -> dict[str, Any]:
    return _request_parameters(args, wire=False)


def _request_parameters(args: Any, *, wire: bool) -> dict[str, Any]:
    allowed = _BASE_FIELDS | _EXECUTION_FIELDS | ({"parameter_bits"} if wire else set())
    if type(args) is not dict or args.keys() - allowed:
        raise ValueError("Only refno, parameters, dry_run and explicit execution tokens are accepted.")
    if not _refno(args.get("refno")):
        raise ValueError("refno must be a canonical numeric Int32 =database/element reference.")
    values = args.get("parameters")
    if type(values) is not list or not 1 <= len(values) <= MAXIMUM_PARAMETERS:
        raise ValueError("parameters must contain 1 to 128 finite DOUBLE numbers.")
    values = [_float(value) for value in values]
    bits = [_bit_string(value) for value in values]
    if wire and "parameter_bits" in args:
        decoded = _numeric_bits(args["parameter_bits"], values, "wire request", minimum=1)
        values, bits = decoded, list(args["parameter_bits"])
    dry_run = args.get("dry_run", True)
    if type(dry_run) is not bool:
        raise ValueError("dry_run must be a boolean.")
    result = {"refno": args["refno"], "parameters": values, "parameter_bits": bits, "dry_run": dry_run}
    if dry_run:
        if args.keys() & _EXECUTION_FIELDS:
            raise ValueError("Execution tokens must be absent when dry_run is true, including null values.")
        return result
    if not _EXECUTION_FIELDS <= args.keys():
        raise ValueError("Execution requires request_id, expected_session_id, expected_bridge_build and expected_revision.")
    if not _uuid(args["request_id"]) or not _uuid(args["expected_bridge_build"]):
        raise ValueError("request_id and expected_bridge_build must be canonical lowercase UUIDs.")
    if not _text(args["expected_session_id"], 256, printable=True):
        raise ValueError("expected_session_id must contain 1 to 256 printable UTF16 units.")
    if not _sha(args["expected_revision"]):
        raise ValueError("expected_revision must be a 64-character lowercase SHA-256 digest.")
    result.update({key: args[key] for key in _EXECUTION_FIELDS})
    return result


def _numeric_bits(bits: Any, values: Any, field: str, *, minimum: int = 0) -> list[float]:
    _need(type(bits) is list and type(values) is list and minimum <= len(bits) == len(values) <= 128,
          field + " ordered numeric bit count")
    decoded = []
    for index, (encoded, observed) in enumerate(zip(bits, values)):
        _need(type(encoded) is str and bool(_BITS.fullmatch(encoded)), field + " binary64 hex")
        number = struct.unpack(">d", bytes.fromhex(encoded))[0]
        _need(math.isfinite(number), field + " finite binary64 bits")
        value = _float(observed)
        _need((value == 0.0 and number == 0.0) or _bit_string(value) == encoded,
              field + f" numeric value versus bits at {index}")
        decoded.append(number)
    return decoded


def _envelope(reply: Any) -> tuple[dict[str, Any], str, str]:
    row = _fields(reply, {"ok", "version", "session_id", "bridge_build", "result"}, "native envelope")
    _need(row["ok"] is True and row["version"] == VERSION, "native E3D21 envelope")
    _need(_text(row["session_id"], 256, printable=True) and _uuid(row["bridge_build"]), "native session/build")
    return row["result"], row["session_id"], row["bridge_build"]


def _errors(value: Any) -> None:
    _need(type(value) is list and len(value) <= 100, "bounded errors")
    for error in value:
        row = _fields(error, {"stage", "code", "message"}, "error")
        _need(type(row["stage"]) is str and bool(_ERROR_CODE.fullmatch(row["stage"]))
              and type(row["code"]) is str and bool(_ERROR_CODE.fullmatch(row["code"]))
              and _text(row["message"], 4096), "error text")


def _progress(value: Any) -> dict[str, Any]:
    row = _fields(value, _PROGRESS_FIELDS, "progress")
    for key in ("parameter_write_attempted", "parameter_write_returned", "readback_complete", "outcome_uncertain", "native_fault_latched"):
        _need(type(row[key]) is bool, "progress " + key)
    for key in ("known_target_refno_verified", "parameters_equal_request", "context_unchanged", "no_change"):
        _need(row[key] is None or type(row[key]) is bool, "progress " + key)
    _need(row["known_target_refno"] is None or _refno(row["known_target_refno"]), "captured target REF")
    _need(row["known_target_refno_verified"] is not True or row["known_target_refno"] is not None, "verified REF present")
    _need(not row["parameter_write_returned"] or row["parameter_write_attempted"], "setter return requires attempt")
    _need(row["retry_safe"] is False, "no automatic retry")
    return row


def _binding(template: Any, reference: str) -> bool:
    if template is None or template["target"] is None:
        return False
    target = template["target"]
    return (target["refno"] == reference and target["type"] == "SCOM" and target["db_type_code"] == 2
            and target["request_binding"]["verified"] is True and "request_binding" not in target["errors"])


def _evidence(value: Any, payload: dict[str, Any], session: str, build: str, *, after: bool) -> tuple[bool, bool]:
    row = _fields(value, _EVIDENCE_FIELDS, "after" if after else "before")
    ready = _module(row["module"]) if row["module"] is not None else False
    ready &= _context(row["context"], session, build, "PARAM context") if row["context"] is not None else False
    _need(row["setable"] is None or type(row["setable"]) is bool, "native setability observation")
    if after:
        _need(row["setable"] is None, "post setability was not observed")
    else:
        ready &= row["setable"] is True
    template, owner, early = row["template"], row["owner"], row["target_binding"]
    bound, numeric = False, False
    if early is not None:
        _fields(early, _IDENTITY, "first target binding")
        _core_identity(early, "first target binding")
        bound = early["refno"] == payload["refno"] and early["type"] == "SCOM" and early["db_type_code"] == 2
    if template is None:
        ready = False
    else:
        ready &= _template(template, payload["refno"], session, build, row["context"])
        target = template["target"]
        ready &= (bound and _binding(template, payload["refno"]) and _kind(target, "SCOM")
                  and _identity_key(early) == _identity_key(target) and template["leaf"]["is_leaf"] is True)
        numeric = _numbers(template) is not None
    if owner is None:
        ready = False
    else:
        _full_identity(owner, "PARAM target owner")
        ready &= _kind(owner, "CATE")
    bits = row["parameter_bits"]
    if numeric:
        values = [item["value"] for item in template["parameters"]["values"]]
        _numeric_bits(bits, values, "native PARAM")
        ready &= 1 <= len(bits) == len(payload["parameter_bits"])
    else:
        _need(bits is None, "unknown numeric PARAM bits")
        ready = False
    return bool(ready), bound


def _required_identity(row: dict[str, Any]) -> tuple[Any, ...]:
    target = row["template"]["target"]
    return (*_identity_key(target), target["base_type"]["short_name"], target["actual_type"]["short_name"],
            *_identity_key(row["owner"]), row["owner"]["base_type"]["short_name"], row["owner"]["actual_type"]["short_name"])


def _validate(reply: Any, payload: dict[str, Any]) -> None:
    payload = _request_parameters(payload, wire=True)
    result, session, build = _envelope(reply)
    result = _fields(result, _RESULT_FIELDS, "result")
    _need(result["operation"] == OPERATION and result["dry_run"] is payload["dry_run"], "operation and request mode")
    request = _fields(result["request"], _REQUEST_FIELDS, "request")
    _need(request["refno"] == payload["refno"] and request["parameter_bits"] == payload["parameter_bits"], "requested REF and bit intent")
    _numeric_bits(request["parameter_bits"], request["parameters"], "response request", minimum=1)
    _need(result["session_id"] == session and result["bridge_build"] == build, "result session/build")
    dry_run = payload["dry_run"]
    _need(result["request_id"] is None if dry_run else result["request_id"] == payload["request_id"], "request ID")
    if not dry_run:
        _need(session == payload["expected_session_id"] and build == payload["expected_bridge_build"], "execute session/build pin")
    _need(result["request_replayed"] is False and type(result["this_submission_executed"]) is bool, "submission replay flags")
    _need(type(result["success"]) is bool and type(result["complete"]) is bool, "outcome types")
    _need(result["scoped_revision"] is None or _sha(result["scoped_revision"]), "scoped revision")
    scope = _fields(result["scope"], set(_SCOPE), "scope")
    _need(all(type(scope[key]) is type(item) and scope[key] == item for key, item in _SCOPE.items()), "numeric-only scope")
    _errors(result["errors"])
    progress = _progress(result["progress"])
    _need(result["this_submission_executed"] is progress["parameter_write_attempted"], "this submission setter attempt")
    before, after = result["before"], result["after"]
    before_ready, bound = _evidence(before, payload, session, build, after=False) if before is not None else (False, False)
    if progress["known_target_refno_verified"] is True:
        _need(bound and progress["known_target_refno"] == payload["refno"], "captured target numeric binding")
    if before_ready:
        _need(progress["no_change"] is (before["parameter_bits"] == payload["parameter_bits"]), "independent unchanged observation")
    else:
        _need(progress["no_change"] is None, "unobserved unchanged state")
    status = result["status"]
    _need(type(status) is str and status in ({"prepared", "blocked"} if dry_run else {"updated", "unchanged"}), "normal status")
    if result["journal"] is not None:
        _need(not dry_run, "preview has no execution journal")
        _journal(result["journal"], payload["request_id"], session, build)
    if dry_run:
        _need(after is None and result["journal"] is None and progress["parameter_write_attempted"] is False
              and progress["parameter_write_returned"] is False and progress["readback_complete"] is False
              and progress["parameters_equal_request"] is None and progress["context_unchanged"] is None
              and progress["outcome_uncertain"] is False and progress["native_fault_latched"] is False, "preview has no write or readback")
        if status == "prepared":
            _need(before_ready and progress["known_target_refno_verified"] is True and not result["errors"]
                  and _sha(result["scoped_revision"]) and result["success"] is True and result["complete"] is True, "prepared required slice")
        else:
            _need(result["success"] is False and result["complete"] is False and bool(result["errors"]), "blocked preparation")
        return
    after_ready, after_bound = _evidence(after, payload, session, build, after=True) if after is not None else (False, False)
    _need(before_ready and after_ready and after_bound, "complete required before/after evidence")
    unchanged = before["context"] == after["context"] and _required_identity(before) == _required_identity(after)
    equal = after["parameter_bits"] == payload["parameter_bits"]
    _need(progress["context_unchanged"] is unchanged and unchanged and progress["parameters_equal_request"] is equal and equal,
          "independent context/identity and requested bits readback")
    _need(result["scoped_revision"] == payload["expected_revision"] and not result["errors"]
          and result["success"] is True and result["complete"] is True
          and progress["known_target_refno_verified"] is True and progress["readback_complete"] is True
          and progress["outcome_uncertain"] is False and progress["native_fault_latched"] is False, "verified numeric update")
    no_change = progress["no_change"]
    _need(status == ("unchanged" if no_change else "updated") and progress["parameter_write_attempted"] is (not no_change)
          and progress["parameter_write_returned"] is (not no_change), "updated versus observed no-change")


def _validate_status(reply: Any, payload: dict[str, str]) -> None:
    payload = _status_parameters(payload)
    snapshot, session, build = _envelope(reply)
    _need(session == payload["expected_session_id"], "status session pin")
    _journal(snapshot, payload["request_id"], session, build)


def _journal(snapshot: Any, request_id: str, session: str, build: str) -> None:
    row = _fields(snapshot, _JOURNAL_FIELDS, "journal")
    _need(row["operation"] == OPERATION and row["request_id"] == request_id and row["retry_safe"] is False, "journal operation/request")
    for key in ("available", "request_id_reserved", "completion_pending", "receipt_available"):
        _need(type(row[key]) is bool, "journal " + key)
    status = row["status"]
    _need(type(status) is str and status in {"accepted", "running", "completed", "unknown_or_expired"}, "journal state")
    _need(row["scope"] == _JOURNAL_SCOPE, "journal scope")
    policy = _fields(row["store_policy"], {"receipt_record_limit", "max_receipt_utf8_bytes", "session_request_id_limit",
        "completed_retention_seconds", "maximum_json_depth", "receipt_budget_kind", "accepted_or_running_evicted",
        "request_ids_released_before_session_end", "durable", "cross_session_exactly_once"}, "journal policy")
    for key, maximum in (("receipt_record_limit", 64), ("max_receipt_utf8_bytes", 262144), ("session_request_id_limit", 65536)):
        _need(type(policy[key]) is int and 1 <= policy[key] <= maximum, "journal " + key)
    _need(type(policy["completed_retention_seconds"]) in (int, float) and 0 < policy["completed_retention_seconds"] <= 1800
          and type(policy["maximum_json_depth"]) is int and policy["maximum_json_depth"] == 64
          and policy["receipt_budget_kind"] == "serialized_utf8_logical_bytes", "journal retention/budget")
    _need(all(policy[key] is False for key in ("accepted_or_running_evicted", "request_ids_released_before_session_end", "durable",
                                              "cross_session_exactly_once")), "journal persistence policy")
    summary = _fields(row["critical_summary"], _SUMMARY_FIELDS, "critical summary")
    for key in _SUMMARY_FIELDS - {"known_target_refno", "error_code", "retry_safe"}:
        _need(summary[key] is None or type(summary[key]) is bool, "nullable native fact " + key)
    _need(summary["known_target_refno"] is None or _refno(summary["known_target_refno"]), "summary target REF")
    _need(summary["known_target_refno_verified"] is not True or summary["known_target_refno"] is not None,
          "summary verified REF present")
    _need(summary["parameter_write_returned"] is not True or summary["parameter_write_attempted"] is True,
          "summary setter return requires observed attempt")
    _need(summary["error_code"] is None or _text(summary["error_code"], 128), "summary error code")
    _need(summary["retry_safe"] is False, "summary no replay")
    times = {}
    for key in ("accepted_utc", "started_utc", "completed_utc"):
        value = row[key]
        if value is None:
            times[key] = None
        else:
            _need(type(value) is str and len(value) <= 40, "journal timestamp")
            times[key] = datetime.fromisoformat(value.replace("Z", "+00:00"))
            _need(times[key].utcoffset() is not None and times[key].utcoffset().total_seconds() == 0, "journal UTC timestamp")
    if status == "unknown_or_expired":
        _need(row["available"] is False and row["request_fingerprint"] is None and row["http_status"] is None
              and all(item is None for item in times.values()) and row["completion_pending"] is False
              and row["receipt_available"] is False and row["receipt"] is None
              and row["receipt_omitted_reason"] == "unknown_or_expired", "unknown/expired state")
    else:
        _need(row["available"] is True and row["request_id_reserved"] is True and _sha(row["request_fingerprint"])
              and times["accepted_utc"] is not None, "reserved journal record")
        if status != "completed":
            _need(times["completed_utc"] is None and row["http_status"] is None and row["receipt_available"] is False
                  and row["receipt"] is None and row["receipt_omitted_reason"] == "not_completed"
                  and (times["started_utc"] is not None) is (status == "running"), "active callback state")
    if status != "completed":
        _need(all(summary[key] is None for key in _SUMMARY_FIELDS - {"retry_safe"}), "unobserved native facts")
        return
    _need(times["completed_utc"] is not None and row["completion_pending"] is False and type(row["http_status"]) is int
          and 100 <= row["http_status"] <= 599, "completed journal state")
    stored = row["receipt"]
    if not row["receipt_available"]:
        _need(stored is None and row["receipt_omitted_reason"] in {"receipt_too_large", "receipt_not_pure_json", "serialization_failed", "receipt_missing"},
              "omitted completed receipt")
        return
    _need(type(stored) is dict and row["receipt_omitted_reason"] is None
          and {"ok", "version", "session_id", "bridge_build", "result"} <= stored.keys()
          and stored.keys() <= {"ok", "version", "session_id", "bridge_build", "result", "error", "error_code", "native_fault_latched", "retry_safe"},
          "stored HTTP envelope")
    _need(type(stored["ok"]) is bool and stored["version"] == VERSION and stored["session_id"] == session
          and stored["bridge_build"] == build, "stored session/build")
    result = _fields(stored["result"], _RESULT_FIELDS, "stored result")
    _need(result["request_id"] == request_id and result["session_id"] == session and result["bridge_build"] == build
          and result["operation"] == OPERATION and result["journal"] is None and result["request_replayed"] is False, "stored binding")
    progress = _progress(result["progress"])
    for key in _PROGRESS_FIELDS - {"retry_safe"}:
        _need(summary[key] is None or summary[key] == progress[key], "stored critical fact " + key)
    if stored["ok"]:
        request = _fields(result["request"], _REQUEST_FIELDS, "stored request")
        payload = {**request, "dry_run": False, "request_id": request_id, "expected_session_id": session,
                   "expected_bridge_build": build, "expected_revision": result["scoped_revision"]}
        _validate({key: stored[key] for key in ("ok", "version", "session_id", "bridge_build", "result")}, payload)
    else:
        _need(type(result["status"]) is str and result["status"] in {"failed", "rejected", "duplicate"}
              and result["success"] is False and result["complete"] is False and _text(stored.get("error"), 4096), "stored failed outcome")


def _runtime(bridge: BridgeClient) -> None:
    if bridge.settings.mode != "bridge" or bridge._selected_version(None) != VERSION:
        raise BridgeError("PARAGON numeric PARAM updates require E3D21 bridge mode and a supporting Addin.")


def paragon_component_parameters_set(bridge: BridgeClient, args: Any) -> dict[str, Any]:
    payload = _parameters(args)
    if not payload["dry_run"] and bridge.settings.allow_commands is not True:
        raise ValueError("PARAM execution requires E3D_MCP_ALLOW_COMMANDS=true.")
    _runtime(bridge)
    try:
        reply = bridge._request("POST", PATH, payload, version=VERSION)
    except BridgeError as error:
        if not payload["dry_run"] and (error.transport_error or error.response_invalid or error.outcome_uncertain):
            error.outcome_uncertain = True
            error.diagnostics.setdefault("outcome_uncertain", True)
            error.diagnostics.setdefault("retry_safe", False)
        raise
    if _contains_native_fault(reply):
        raise BridgeError("A native database fault stopped the PARAM update. Do not retry.", diagnostics=reply,
                          outcome_uncertain=not payload["dry_run"])
    try:
        _validate(reply, payload)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
        raise BridgeError(str(error), response_invalid=True, outcome_uncertain=not payload["dry_run"],
                          diagnostics={"outcome_uncertain": not payload["dry_run"], "retry_safe": False}) from error
    return reply


def paragon_component_parameters_status(bridge: BridgeClient, args: Any) -> dict[str, Any]:
    payload = _status_parameters(args)
    _runtime(bridge)
    reply = bridge._request("GET", STATUS_PATH, query=payload, version=VERSION)
    # Stored historical fault flags are managed history, never a new SDK fault.
    try:
        _validate_status(reply, payload)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
        raise BridgeError(str(error), response_invalid=True) from error
    return reply


def register_paragon_component_parameters(server: McpServer, bridge: BridgeClient) -> None:
    uuid_schema = {"type": "string", "minLength": 36, "maxLength": 36, "pattern": "^" + _UUID_PATTERN + r"(?![\s\S])"}
    session_schema = {"type": "string", "minLength": 1, "maxLength": 256,
                      "description": "Explicit original bridge session; printable and at most 256 UTF16 units."}
    server.add_tool(
        TOOL,
        "Prepare or replace one explicit Catalogue SCOM leaf's complete finite DOUBLE PARAM array in PARAGON/81. "
        "Supply 1 to 128 values with the existing array length. Preparation defaults to dry_run=true. Execution "
        "requires command permission, a caller-owned request ID and the exact session, build and scoped revision. "
        "Native readback verifies typed target/owner/context and ordered binary64 values, preserving negative zero. "
        "Metadata NAME, reference or unit-kind partials do not replace required numeric evidence. "
        "This updates an existing REF and does not verify its creation, units, geometry, engineering suitability or "
        "persistence. Copy and PARAM update are separate non-transactional operations. No automatic retry, rollback, "
        "CE restore, SAVEWORK or PML forwarding.",
        {"type": "object", "properties": {
            "refno": {"type": "string", "minLength": 4, "maxLength": 22, "pattern": _REF_PATTERN},
            "parameters": {"type": "array", "minItems": 1, "maxItems": 128, "items": {"type": "number"}},
            "dry_run": {"type": "boolean", "default": True}, "request_id": dict(uuid_schema),
            "expected_session_id": dict(session_schema), "expected_bridge_build": dict(uuid_schema),
            "expected_revision": {"type": "string", "minLength": 64, "maxLength": 64, "pattern": r"^[a-f0-9]{64}(?![\s\S])"},
        }, "required": ["refno", "parameters"], "additionalProperties": False,
         "allOf": [{"if": {"required": ["dry_run"], "properties": {"dry_run": {"const": False}}},
                    "then": {"required": sorted(_EXECUTION_FIELDS)},
                    "else": {"not": {"anyOf": [{"required": [key]} for key in sorted(_EXECUTION_FIELDS)]}}}]},
        lambda args: paragon_component_parameters_set(bridge, args), read_only=False,
    )
    server.add_tool(
        STATUS_TOOL,
        "Read the original session's managed PARAM-update journal for one explicit request ID. No SDK reads, "
        "busy/fault probes or replay; historical native faults remain inspectable. Accepted/running/completed are "
        "journal states, not proof of setter success. Unknown or expired does not prove no write; records are not durable.",
        {"type": "object", "properties": {"request_id": dict(uuid_schema), "expected_session_id": dict(session_schema)},
         "required": ["request_id", "expected_session_id"], "additionalProperties": False},
        lambda args: paragon_component_parameters_status(bridge, args), read_only=True,
    )

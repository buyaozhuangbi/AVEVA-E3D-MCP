"""Create one Catalogue SCOM from explicit, observed point and geometry sets.

The native creator owns the preview, revision, request journal and SDK writes.
Python accepts literal identities and finite numeric PARAM values only. It
never forwards PML, infers set definitions, or retries an uncertain creation.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from . import paragon_component_copy as copy
from . import paragon_component_parameters as param
from . import paragon_component_template as template
from .bridge import BridgeClient, BridgeError, _contains_native_fault
from .protocol import McpServer


VERSION = "E3D21"
TOOL = "e3d_paragon_component_create"
STATUS_TOOL = "e3d_paragon_component_create_status"
PATH = "/api/v1/paragon/component-create"
STATUS_PATH = PATH + "/status"
OPERATION = "paragon_component_create"
MAXIMUM_PARAMETERS = 128
MAXIMUM_SET_MEMBERS = 64
_EXECUTION_FIELDS = {"request_id", "expected_session_id", "expected_bridge_build", "expected_revision"}
_REFERENCES = ("parent_refno", "point_refno", "geometry_refno")
_PUBLIC_FIELDS = set(_REFERENCES) | {"name", "parameters", "dry_run"} | _EXECUTION_FIELDS
_REQUEST_FIELDS = set(_REFERENCES) | {"name", "parameters", "parameter_bits"}
_RESULT_FIELDS = {"operation", "dry_run", "request", "session_id", "bridge_build", "request_id", "status",
                  "success", "complete", "scoped_revision", "before", "after", "progress", "errors", "journal",
                  "scope", "this_submission_executed", "request_replayed"}
_BEFORE_FIELDS = {"module", "context", "parent", "point_set", "geometry_set", "members", "members_complete",
                  "name_lookup", "creatable"}
_AFTER_FIELDS = {"module", "context", "parent", "point_set", "geometry_set", "members", "members_complete",
                 "new_identity", "new_owner", "new_leaf", "new_template", "parameter_bits"}
_ATTEMPT_FIELDS = tuple(item for name in ("creation", "name", "point_ref", "geometry_ref", "parameters")
                        for item in (name + "_attempted", name + "_returned"))
_FACT_FIELDS = {"known_new_refno_verified", "parent_append", "context_unchanged", "parameters_equal_request",
                "references_equal_request", "sets_unchanged_scoped"}
_PROGRESS_FIELDS = set(_ATTEMPT_FIELDS) | _FACT_FIELDS | {
    "known_new_refno", "readback_complete", "outcome_uncertain", "native_fault_latched", "retry_safe",
}
_SUMMARY_FIELDS = set(_ATTEMPT_FIELDS) | {"known_new_refno", "known_new_refno_verified", "readback_complete",
    "outcome_uncertain", "native_fault_latched", "error_code", "retry_safe"}
_SCOPE = {"kind": "catalogue_scom_create", "backend": "native_sdk", "set_membership_only": True,
          "shared_sets": True, "geometry_evaluated": False, "engineering_validated": None,
          "savework_performed": False, "atomic_snapshot": False}


def _need(condition: bool, field: str) -> None:
    if not condition:
        raise ValueError("Invalid PARAGON component creation evidence: " + field)


def _fields(value: Any, fields: set[str], field: str) -> dict[str, Any]:
    _need(type(value) is dict and value.keys() == fields, field + " fields")
    return value


def _envelope(reply: Any) -> tuple[dict[str, Any], str, str]:
    row = _fields(reply, {"ok", "version", "session_id", "bridge_build", "result"}, "native envelope")
    _need(row["ok"] is True and row["version"] == VERSION, "native E3D21 success envelope")
    _need(copy._text(row["session_id"], 256, printable=True) and copy._uuid(row["bridge_build"]), "native session/build")
    return row["result"], row["session_id"], row["bridge_build"]


def _parameters(args: Any) -> dict[str, Any]:
    if type(args) is not dict or args.keys() - _PUBLIC_FIELDS:
        raise ValueError("Only explicit parent, point and geometry references, NAME, PARAM and execution tokens are accepted.")
    for key in _REFERENCES:
        if not copy._refno(args.get(key)):
            raise ValueError(key + " must be a canonical numeric Int32 =database/element reference.")
    if len({args[key] for key in _REFERENCES}) != len(_REFERENCES):
        raise ValueError("parent_refno, point_refno and geometry_refno must be distinct.")
    name = args.get("name")
    if type(name) is not str or not 2 <= len(name) <= 50 or not copy._NAME.fullmatch(name):
        raise ValueError("name must be an ASCII slash-separated NAME of at most 50 characters with letter-led segments.")
    parameters = param._parameters({"refno": args["parent_refno"], "parameters": args.get("parameters"),
                                    **{key: args[key] for key in _EXECUTION_FIELDS | {"dry_run"} if key in args}})
    return {key: args[key] for key in _REFERENCES} | {"name": name} | {
        key: value for key, value in parameters.items() if key != "refno"
    }


def _status_parameters(args: Any) -> dict[str, str]:
    return copy._status_parameters(args)


def _native_request(value: Any) -> dict[str, Any]:
    request = _fields(value, _REQUEST_FIELDS, "request")
    numbers = param._numeric_bits(request["parameter_bits"], request["parameters"], "creation request", minimum=1)
    return _parameters({**{key: request[key] for key in _REQUEST_FIELDS - {"parameters", "parameter_bits"}},
                        "parameters": numbers})


def _progress(value: Any) -> dict[str, Any]:
    row = _fields(value, _PROGRESS_FIELDS, "progress")
    for key in set(_ATTEMPT_FIELDS) | {"readback_complete", "outcome_uncertain", "native_fault_latched"}:
        _need(type(row[key]) is bool, "progress " + key)
    for key in _FACT_FIELDS:
        _need(row[key] is None or type(row[key]) is bool, "progress nullable " + key)
    _need(row["retry_safe"] is False, "no creation replay")
    _need(row["known_new_refno"] is None or copy._refno(row["known_new_refno"]), "captured new numeric REF")
    _need(row["known_new_refno_verified"] is not True or row["known_new_refno"] is not None,
          "verified new REF present")
    for index in range(0, len(_ATTEMPT_FIELDS), 2):
        attempted, returned = _ATTEMPT_FIELDS[index:index + 2]
        _need(not row[returned] or row[attempted], returned + " requires observed attempt")
        if index:
            _need(not row[attempted] or row[_ATTEMPT_FIELDS[index - 1]], attempted + " requires previous SDK return")
    return row


def _set(value: Any, reference: str, kind: str) -> bool:
    if value is None:
        return False
    row = _fields(value, {"target", "members", "members_complete"}, kind + " set")
    target = row["target"]
    if target is not None:
        copy._full_identity(target, kind + " target")
    ready = target is not None and target["refno"] == reference and copy._kind(target, kind)
    members = row["members"]
    _need(type(members) is list and len(members) <= MAXIMUM_SET_MEMBERS
          and type(row["members_complete"]) is bool, kind + " bounded direct members")
    seen = set()
    for item in members:
        member = _fields(item, {"identity", "owner"}, kind + " member")
        identity = _fields(member["identity"], template._IDENTITY, kind + " member identity")
        template._core_identity(identity, kind + " member identity")
        owner = member["owner"]
        if owner is not None:
            _fields(owner, template._IDENTITY, kind + " member owner")
            template._core_identity(owner, kind + " member owner")
        ready &= (identity["db_type_code"] == 2 and identity["refno"] not in seen
                  and identity["refno"] != reference and target is not None
                  and owner is not None and copy._identity_key(owner) == copy._identity_key(target))
        seen.add(identity["refno"])
    return bool(ready and members and row["members_complete"])


def _sets_equal(before: Any, after: Any, payload: dict[str, Any]) -> bool | None:
    compared = []
    for key, reference, kind in (("point_set", payload["point_refno"], "PTSE"),
                                  ("geometry_set", payload["geometry_refno"], "GMSE")):
        first, last = before[key], after[key]
        if not (_set(first, reference, kind) and _set(last, reference, kind)):
            compared.append(None)
        else:
            compared.append(copy._identity_key(first["target"]) == copy._identity_key(last["target"])
                            and first["members"] == last["members"])
    return None if None in compared else all(compared)


def _parent(value: Any, reference: str, field: str) -> bool:
    if value is None:
        return False
    copy._full_identity(value, field)
    return value["refno"] == reference and copy._kind(value, "CATE")


def _before(value: Any, payload: dict[str, Any], session: str, build: str) -> bool:
    row = _fields(value, _BEFORE_FIELDS, "before")
    ready = copy._module(row["module"]) if row["module"] is not None else False
    ready &= copy._context(row["context"], session, build, "before context") if row["context"] is not None else False
    ready &= _parent(row["parent"], payload["parent_refno"], "before parent")
    ready &= _set(row["point_set"], payload["point_refno"], "PTSE")
    ready &= _set(row["geometry_set"], payload["geometry_refno"], "GMSE")
    ready &= copy._members(row["members"], row["members_complete"], 100)
    _need(row["creatable"] is None or type(row["creatable"]) is bool, "SCOM creatability")
    ready &= row["creatable"] is True
    lookup = _fields(row["name_lookup"], {"status", "target"}, "name lookup")
    _need(type(lookup["status"]) is str and lookup["status"] in {"absent", "present", "unknown"}, "name lookup status")
    if lookup["status"] == "present":
        copy._full_identity(lookup["target"], "existing NAME target")
    else:
        _need(lookup["target"] is None, "unobserved NAME lookup identity")
    ready &= lookup["status"] == "absent"
    ready &= not any(type(item["name"]) is str and item["name"].casefold() == payload["name"].casefold()
                     for item in row["members"])
    return bool(ready)


def _scalar_reference(value: dict[str, Any], reference: str, kind: str) -> bool | None:
    observed = copy._reference(value)
    if observed is None or observed == "unsupported_excluded":
        return None
    if value["shape"] != "element" or value["is_array"] is not False or len(value["values"]) != 1:
        return False
    item = value["values"][0]
    identity = item["identity"]
    return (item["index"] == 0 and item["reference_null"] is False and identity is not None
            and identity["refno"] == reference and copy._kind(identity, kind))


def _after(value: Any, before: Any, payload: dict[str, Any], session: str, build: str,
           progress: dict[str, Any]) -> bool:
    row = _fields(value, _AFTER_FIELDS, "after")
    ready = copy._module(row["module"]) if row["module"] is not None else False
    ready &= copy._context(row["context"], session, build, "after context") if row["context"] is not None else False
    ready &= _parent(row["parent"], payload["parent_refno"], "after parent")
    ready &= _parent(row["new_owner"], payload["parent_refno"], "new owner")
    ready &= _set(row["point_set"], payload["point_refno"], "PTSE")
    ready &= _set(row["geometry_set"], payload["geometry_refno"], "GMSE")
    ready &= copy._members(row["members"], row["members_complete"], 101)
    identity, leaf, raw = row["new_identity"], row["new_leaf"], row["new_template"]
    if identity is not None:
        copy._full_identity(identity, "independent new identity")
    leaf_ready = template._leaf(leaf, read=True) if leaf is not None else False
    if raw is not None:
        _need(copy._refno(progress["known_new_refno"]), "new template numeric binding")
        ready &= copy._template(raw, progress["known_new_refno"], session, build, row["context"])
    else:
        ready = False
    bits = row["parameter_bits"]
    _need(bits is None or (type(bits) is list and len(bits) <= MAXIMUM_PARAMETERS
          and all(type(item) is str and param._BITS.fullmatch(item) for item in bits)), "readback parameter bits")
    if before is None:
        return False
    contexts = (before["context"], row["context"])
    context_equal = None if any(context is None for context in contexts) else contexts[0] == contexts[1]
    _need(progress["context_unchanged"] is context_equal, "independent context comparison")
    ready &= context_equal is True
    first, last = before["members"], row["members"]
    append = (before["members_complete"] and row["members_complete"] and len(last) == len(first) + 1
              and all(copy._identity_key(old) == copy._identity_key(observed) and old["name"] == observed["name"]
                      for old, observed in zip(first, last))
              and last[-1]["refno"] == progress["known_new_refno"] and last[-1]["type"] == "SCOM"
              and type(last[-1]["name"]) is str and last[-1]["name"].isascii()
              and last[-1]["name"].casefold() == payload["name"].casefold())
    _need(progress["parent_append"] is bool(append), "ordered parent append")
    new_identity = (append and identity is not None and copy._kind(identity, "SCOM")
                    and identity["refno"] == progress["known_new_refno"] == last[-1]["refno"]
                    and identity["refno"] not in {payload[key] for key in _REFERENCES}
                    and copy._identity_key(identity) == copy._identity_key(last[-1])
                    and identity["name_error"] is None and type(identity["name"]) is str
                    and identity["name"].isascii() and last[-1]["name"] is not None and last[-1]["name"].isascii()
                    and identity["name"].casefold() == last[-1]["name"].casefold() == payload["name"].casefold()
                    and leaf_ready and leaf["is_leaf"] is True)
    ready &= bool(new_identity)
    if raw is not None:
        target = raw["target"]
        ready &= (target is not None and identity is not None and copy._kind(target, "SCOM")
                  and copy._identity_key(target) == copy._identity_key(identity)
                  and target["name"] == identity["name"] and target["name_error"] is None
                  and raw["leaf"]["is_leaf"] is True)
        if copy._numbers(raw) is not None:
            values = [item["value"] for item in raw["parameters"]["values"]]
            # net48 JSON may serialize native -0.0 as 0.0. The separate SDK
            # bits retain its sign; all other values must match those bits.
            param._numeric_bits(bits, values, "native creation PARAM")
            parameters_equal = bits == payload["parameter_bits"]
        else:
            _need(bits is None, "unknown native creation PARAM bits")
            parameters_equal = None
        observed = [_scalar_reference(raw["references"][name], payload[field], kind)
                    for name, field, kind in (("PTREF", "point_refno", "PTSE"), ("GMREF", "geometry_refno", "GMSE"))]
        references_equal = False if False in observed else None if None in observed else True
    else:
        parameters_equal = references_equal = None
    sets_equal = _sets_equal(before, row, payload)
    for key, expected in (("parameters_equal_request", parameters_equal),
                          ("references_equal_request", references_equal), ("sets_unchanged_scoped", sets_equal)):
        _need(progress[key] is expected, "independent " + key)
        ready &= expected is True
    return bool(ready)


def _validate(reply: Any, payload: dict[str, Any]) -> None:
    result, session, build = _envelope(reply)
    row = _fields(result, _RESULT_FIELDS, "creation result")
    _need(row["operation"] == OPERATION and row["dry_run"] is payload["dry_run"], "operation and dry_run")
    request = _native_request(row["request"])
    _need(all(request[key] == payload[key] for key in _REQUEST_FIELDS), "explicit request binding")
    _need(row["session_id"] == session and row["bridge_build"] == build, "result session/build")
    _need(row["request_id"] is None if payload["dry_run"] else row["request_id"] == payload["request_id"], "request ID")
    if not payload["dry_run"]:
        _need(session == payload["expected_session_id"] and build == payload["expected_bridge_build"], "execution session/build")
    _need(row["request_replayed"] is False and type(row["this_submission_executed"]) is bool, "submission replay flags")
    _need(type(row["success"]) is bool and type(row["complete"]) is bool, "outcome flags")
    _need(row["scoped_revision"] is None or copy._sha(row["scoped_revision"]), "scoped revision")
    template._fixed(row["scope"], _SCOPE, "creation scope")
    copy._errors(row["errors"])
    progress = _progress(row["progress"])
    _need(row["this_submission_executed"] is progress["creation_attempted"], "this submission creation attempt")
    status = row["status"]
    _need(type(status) is str and status in ({"prepared", "blocked"} if payload["dry_run"] else {"created"}), "normal creation status")
    before_ready = _before(row["before"], payload, session, build) if row["before"] is not None else False
    after_ready = _after(row["after"], row["before"], payload, session, build, progress) if row["after"] is not None else False
    if row["journal"] is not None:
        _need(not payload["dry_run"], "preview has no request journal")
        _journal(row["journal"], payload["request_id"], session, build)
    if payload["dry_run"]:
        _need(row["after"] is None and row["journal"] is None and row["this_submission_executed"] is False,
              "preview has no creation or readback")
        _need(all(progress[key] is False for key in set(_ATTEMPT_FIELDS) | {
            "readback_complete", "outcome_uncertain", "native_fault_latched", "retry_safe"})
            and all(progress[key] is None for key in _FACT_FIELDS | {"known_new_refno"}), "preview no mutating stage")
        if status == "prepared":
            _need(before_ready and not row["errors"] and copy._sha(row["scoped_revision"])
                  and row["success"] is True and row["complete"] is True, "prepared scoped observations")
        else:
            _need(row["success"] is False and row["complete"] is False and bool(row["errors"]), "blocked preparation")
        return
    _need(before_ready and after_ready and not row["errors"] and row["scoped_revision"] == payload["expected_revision"],
          "created scoped evidence")
    _need(row["success"] is True and row["complete"] is True and row["this_submission_executed"] is True,
          "created verified outcome")
    _need(all(progress[key] is True for key in set(_ATTEMPT_FIELDS) | _FACT_FIELDS | {"readback_complete"})
          and progress["outcome_uncertain"] is False and progress["native_fault_latched"] is False,
          "created verified progress")


def _validate_status(reply: Any, payload: dict[str, str]) -> None:
    payload = _status_parameters(payload)
    snapshot, session, build = _envelope(reply)
    _need(session == payload["expected_session_id"], "status original session pin")
    _journal(snapshot, payload["request_id"], session, build)


def _journal(snapshot: Any, request_id: str, session: str, build: str) -> None:
    row = _fields(snapshot, copy._JOURNAL_FIELDS, "journal")
    _need(row["operation"] == OPERATION and row["request_id"] == request_id and row["retry_safe"] is False,
          "journal operation/request/no replay")
    for key in ("available", "request_id_reserved", "completion_pending", "receipt_available"):
        _need(type(row[key]) is bool, "journal " + key)
    status = row["status"]
    _need(type(status) is str and status in {"accepted", "running", "completed", "unknown_or_expired"}, "journal state")
    _need(row["scope"] == copy._JOURNAL_SCOPE, "journal ownership scope")
    policy = _fields(row["store_policy"], {"receipt_record_limit", "max_receipt_utf8_bytes", "session_request_id_limit",
        "completed_retention_seconds", "maximum_json_depth", "receipt_budget_kind", "accepted_or_running_evicted",
        "request_ids_released_before_session_end", "durable", "cross_session_exactly_once"}, "journal store policy")
    for key, maximum in (("receipt_record_limit", 64), ("max_receipt_utf8_bytes", 262144), ("session_request_id_limit", 65536)):
        _need(type(policy[key]) is int and 1 <= policy[key] <= maximum, "journal " + key)
    _need(type(policy["completed_retention_seconds"]) in (int, float) and 0 < policy["completed_retention_seconds"] <= 1800
          and type(policy["maximum_json_depth"]) is int and policy["maximum_json_depth"] == 64
          and policy["receipt_budget_kind"] == "serialized_utf8_logical_bytes", "journal retention/depth/budget")
    _need(all(policy[key] is False for key in ("accepted_or_running_evicted", "request_ids_released_before_session_end",
                                              "durable", "cross_session_exactly_once")), "journal session-local bounds")
    summary = _fields(row["critical_summary"], _SUMMARY_FIELDS, "critical summary")
    for key in _SUMMARY_FIELDS - {"known_new_refno", "error_code", "retry_safe"}:
        _need(summary[key] is None or type(summary[key]) is bool, "summary nullable native fact " + key)
    _need(summary["known_new_refno"] is None or copy._refno(summary["known_new_refno"]), "summary new numeric REF")
    _need(summary["known_new_refno_verified"] is not True or summary["known_new_refno"] is not None,
          "summary verified new REF present")
    _need(summary["error_code"] is None or copy._text(summary["error_code"], 128), "summary error code")
    _need(summary["retry_safe"] is False, "summary no replay")
    for attempted, returned in zip(_ATTEMPT_FIELDS[::2], _ATTEMPT_FIELDS[1::2]):
        _need(summary[returned] is not True or summary[attempted] is True, "summary SDK return requires attempt")
    times = {}
    for key in ("accepted_utc", "started_utc", "completed_utc"):
        value = row[key]
        if value is None:
            times[key] = None
        else:
            _need(type(value) is str and len(value) <= 40, "journal UTC timestamp")
            times[key] = datetime.fromisoformat(value.replace("Z", "+00:00"))
            _need(times[key].utcoffset() is not None and times[key].utcoffset().total_seconds() == 0, "journal UTC offset")
    if status == "unknown_or_expired":
        _need(row["available"] is False and row["request_fingerprint"] is None and row["http_status"] is None
              and all(value is None for value in times.values()) and row["completion_pending"] is False
              and row["receipt_available"] is False and row["receipt"] is None
              and row["receipt_omitted_reason"] == "unknown_or_expired", "unknown/expired journal")
    else:
        _need(row["available"] is True and row["request_id_reserved"] is True and copy._sha(row["request_fingerprint"])
              and times["accepted_utc"] is not None, "reserved journal record")
        if status != "completed":
            _need(times["completed_utc"] is None and row["http_status"] is None and row["receipt_available"] is False
                  and row["receipt"] is None and row["receipt_omitted_reason"] == "not_completed"
                  and (times["started_utc"] is not None) is (status == "running"), "active callback ownership")
    if status != "completed":
        _need(all(summary[key] is None for key in _SUMMARY_FIELDS - {"retry_safe"}), "unobserved native facts")
        return
    _need(times["completed_utc"] is not None and row["completion_pending"] is False and type(row["http_status"]) is int
          and 100 <= row["http_status"] <= 599, "completed journal")
    stored = row["receipt"]
    if not row["receipt_available"]:
        _need(stored is None and row["receipt_omitted_reason"] in {"receipt_too_large", "receipt_not_pure_json",
              "serialization_failed", "receipt_missing"}, "omitted completed receipt")
        return
    _need(type(stored) is dict and row["receipt_omitted_reason"] is None
          and {"ok", "version", "session_id", "bridge_build", "result"} <= stored.keys()
          and stored.keys() <= {"ok", "version", "session_id", "bridge_build", "result", "error", "error_code",
                               "native_fault_latched", "retry_safe"}, "stored HTTP envelope")
    _need(type(stored["ok"]) is bool and stored["version"] == VERSION and stored["session_id"] == session
          and stored["bridge_build"] == build, "stored session/build")
    result = _fields(stored["result"], _RESULT_FIELDS, "stored creation result")
    _need(result["operation"] == OPERATION and result["dry_run"] is False
          and result["request_id"] == request_id and result["session_id"] == session
          and result["bridge_build"] == build and result["journal"] is None and result["request_replayed"] is False,
          "stored submission binding")
    progress = _progress(result["progress"])
    _need(result["this_submission_executed"] is progress["creation_attempted"], "stored this-submission creation attempt")
    template._fixed(result["scope"], _SCOPE, "stored creation scope")
    copy._errors(result["errors"])
    _native_request(result["request"])
    for key in _SUMMARY_FIELDS - {"error_code", "retry_safe"}:
        _need(type(summary[key]) is type(progress[key]) and summary[key] == progress[key],
              "stored critical fact " + key)
    _need(summary["error_code"] == (result["errors"][-1]["code"] if result["errors"] else None), "stored critical error code")
    if stored["ok"]:
        request = _fields(result["request"], _REQUEST_FIELDS, "stored request")
        payload = {**request, "dry_run": False, "request_id": request_id, "expected_session_id": session,
                   "expected_bridge_build": build, "expected_revision": result["scoped_revision"]}
        _validate({key: stored[key] for key in ("ok", "version", "session_id", "bridge_build", "result")}, payload)
    else:
        _need(type(result["status"]) is str and result["status"] in {"failed", "rejected", "duplicate"}
              and result["success"] is False and result["complete"] is False and copy._text(stored.get("error"), 4096),
              "stored failed outcome")


def _runtime(bridge: BridgeClient) -> None:
    if bridge.settings.mode != "bridge" or bridge._selected_version(None) != VERSION:
        raise BridgeError("PARAGON component creation requires E3D21 bridge mode and a supporting Addin.")


def paragon_component_create(bridge: BridgeClient, args: Any) -> dict[str, Any]:
    payload = _parameters(args)
    if not payload["dry_run"] and bridge.settings.allow_commands is not True:
        raise ValueError("Component creation requires E3D_MCP_ALLOW_COMMANDS=true.")
    _runtime(bridge)
    try:
        reply = bridge._request("POST", PATH, payload, version=VERSION)
    except BridgeError as error:
        copy._uncertain(error, payload)
        raise
    if _contains_native_fault(reply):
        raise BridgeError("A native database fault stopped component creation. Do not retry.", diagnostics=reply,
                          outcome_uncertain=not payload["dry_run"])
    try:
        _validate(reply, payload)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
        raise BridgeError(str(error), response_invalid=True, outcome_uncertain=not payload["dry_run"],
                          diagnostics={"outcome_uncertain": not payload["dry_run"], "retry_safe": False}) from error
    return reply


def paragon_component_create_status(bridge: BridgeClient, args: Any) -> dict[str, Any]:
    payload = _status_parameters(args)
    _runtime(bridge)
    reply = bridge._request("GET", STATUS_PATH, query=payload, version=VERSION)
    # Stored fault evidence is historical data. This managed lookup cannot
    # execute the creation again or probe an SDK object after a native fault.
    try:
        _validate_status(reply, payload)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
        raise BridgeError(str(error), response_invalid=True) from error
    return reply


def register_paragon_component_create(server: McpServer, bridge: BridgeClient) -> None:
    reference = {"type": "string", "minLength": 4, "maxLength": 22, "pattern": copy._REF_PATTERN}
    uuid = {"type": "string", "minLength": 36, "maxLength": 36,
            "pattern": "^" + copy._UUID_PATTERN + r"(?![\s\S])"}
    session = {"type": "string", "minLength": 1, "maxLength": 256,
               "description": "Exact printable server-owned session identity, at most 256 UTF16 units."}
    server.add_tool(
        TOOL,
        "Prepare or create one Catalogue SCOM under an explicit existing CATE in E3D21 PARAGON. Select explicit "
        "existing PTSE and GMSE sets and 1 to 128 finite numeric PARAM values. Preparation defaults to dry_run=true. "
        "The native creator observes each set and its bounded direct members, then binds a scoped revision to "
        "those observations. Execution requires command permission and the exact reviewed session, build and "
        "revision with a caller-owned request ID. Readback checks the new identity, owner, NAME, ordered parent "
        "append, PTREF, GMREF and binary64 PARAM values. Set contents are observed, while point evaluation, "
        "geometry generation, units, engineering suitability and persistence remain unverified. No PML input, "
        "automatic retry, rollback, CE restore or SAVEWORK.",
        {"type": "object", "properties": {
            "parent_refno": {**reference, "description": "Explicit existing Catalogue CATE."},
            "point_refno": {**reference, "description": "Explicit existing Catalogue PTSE, with 1 to 64 observed direct members."},
            "geometry_refno": {**reference, "description": "Explicit existing Catalogue GMSE, with 1 to 64 observed direct members."},
            "name": {"type": "string", "minLength": 2, "maxLength": 50, "pattern": "^" + copy._NAME_PATTERN},
            "parameters": {"type": "array", "minItems": 1, "maxItems": MAXIMUM_PARAMETERS, "items": {"type": "number"}},
            "dry_run": {"type": "boolean", "default": True}, "request_id": dict(uuid),
            "expected_session_id": dict(session), "expected_bridge_build": dict(uuid),
            "expected_revision": {"type": "string", "minLength": 64, "maxLength": 64,
                                  "pattern": r"^[a-f0-9]{64}(?![\s\S])"},
        }, "required": [*_REFERENCES, "name", "parameters"], "additionalProperties": False,
         "allOf": [{"if": {"required": ["dry_run"], "properties": {"dry_run": {"const": False}}},
                    "then": {"required": sorted(_EXECUTION_FIELDS)},
                    "else": {"not": {"anyOf": [{"required": [key]} for key in sorted(_EXECUTION_FIELDS)]}}}]},
        lambda args: paragon_component_create(bridge, args), read_only=False,
    )
    server.add_tool(
        STATUS_TOOL,
        "Read the session-local managed creation journal for an explicit request ID and its original session. "
        "The lookup performs no SDK read or creation replay. Accepted, running and completed describe callback "
        "ownership, and completed does not prove native success. Unknown or expired does not prove no creation. "
        "Historical faults remain inspectable. The journal is not durable across sessions.",
        {"type": "object", "properties": {"request_id": dict(uuid), "expected_session_id": dict(session)},
         "required": ["request_id", "expected_session_id"], "additionalProperties": False},
        lambda args: paragon_component_create_status(bridge, args), read_only=True,
    )

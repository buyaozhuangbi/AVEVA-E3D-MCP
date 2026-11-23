"""Read and validate bounded E3D21 native reference-path observations.

The standard HTTP envelope binds the result's before/after samples to one
native session and assembly build. Each current-element sample includes its
REF, base type and raw SDK database code, or a verified native null. Trace
rows and DbType evidence are suppressed if the boundary cannot be verified;
the attempted-path budget remains available. No alternative reads are made.
"""

from __future__ import annotations

from collections.abc import Callable
import re
from typing import Any

from .bridge import BridgeClient, BridgeError
from .protocol import McpServer

TOOL = "e3d_paragon_reference_trace"
PATH = "/api/v1/paragon/reference-trace"
VERSION = "E3D21"
OPERATION = "paragon_reference_trace"
DEFAULT_TRACE_LIMIT = 24
MAX_TRACE_LIMIT = 32

_INT32_TEN_DIGITS = (
    r"1[0-9]{9}|20[0-9]{8}|21[0-3][0-9]{7}|214[0-6][0-9]{6}|2147[0-3][0-9]{5}|"
    r"21474[0-7][0-9]{4}|214748[0-2][0-9]{3}|2147483[0-5][0-9]{2}|"
    r"21474836[0-3][0-9]|214748364[0-7]"
)
_POSITIVE_INT32 = rf"(?:[1-9][0-9]{{0,8}}|{_INT32_TEN_DIGITS})"
_DBREF_PATTERN = rf"={_POSITIVE_INT32}/(?:0|{_POSITIVE_INT32})"
_DBREF = re.compile(_DBREF_PATTERN)
_NOUN = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_BUILD = re.compile(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}")
_DESIGN, _CATALOGUE, _PROPERTY = 1, 2, 4
_INT32_MAX = 2**31 - 1
_COMPONENTS = frozenset({"SCOM", "SPRF", "SFIT", "JOIN"})
_PROPERTY_COMPONENTS = frozenset({"CMPD", "TUBD", "CABD"})
_ROOT_NOUNS = {
    _CATALOGUE: _COMPONENTS | {"SPEC", "SPCO", "GPART", "CATA", "SECT", "STSE", "CATE", "STCA"},
    _PROPERTY: _PROPERTY_COMPONENTS | {"SOLI", "FLUI"},
}

# Keep DFS priority in sync with E3d21ParagonReferenceTrace.cs. The order of
# keys in a JSON object is irrelevant, including when a budget cuts a branch.
_SPEC_OWNER = ("OWNER", _CATALOGUE, {"SELE", "SPEC"})
_FOLLOW = {
    "SPCO": (
        ("CATREF", _CATALOGUE, {"SCOM"}),
        ("PRTREF", _CATALOGUE, {"GPART"}),
        ("CMPREF", _PROPERTY, {"CMPD", "TUBD"}),
        _SPEC_OWNER,
    ),
    "SELE": (_SPEC_OWNER,),
    "GPART": (("CATREF", _CATALOGUE, _COMPONENTS), ("CMPREF", _PROPERTY, _PROPERTY_COMPONENTS)),
    "SPEC": (("MATREF", _PROPERTY, {"SOLI"}), ("FLUREF", _PROPERTY, {"FLUI"})),
    "SCOM": (("OWNER", _CATALOGUE, {"CATE", "SECT"}),),
    "SPRF": (("OWNER", _CATALOGUE, {"STCA", "STSE"}),),
    "SFIT": (("OWNER", _CATALOGUE, {"STCA", "STSE"}),),
    "JOIN": (("OWNER", _CATALOGUE, {"STCA", "STSE"}),),
    "CATE": (("OWNER", _CATALOGUE, {"SECT"}),),
    "STCA": (("OWNER", _CATALOGUE, {"STSE"}),),
    "SECT": (("OWNER", _CATALOGUE, {"CATA"}),),
    "STSE": (("OWNER", _CATALOGUE, {"CATA"}),),
}
_EXPANDABLE = {"resolved", "owner_read_error"}
_DBTYPE_OBSERVED = _EXPANDABLE | {"out_of_scope"}
_ATTEMPTED = _DBTYPE_OBSERVED | {"unresolved", "read_error"}
_ENVELOPE_FIELDS = {"ok", "version", "session_id", "bridge_build", "result"}
_RESULT_FIELDS = {"status", "trace_limit", "trace", "context", "db_type_codes"}
_CONTEXT_FIELDS = {"before", "after", "same_project_instance", "same_mdb_instance", "boundary_agreement"}
_SAMPLE_FIELDS = {"session_id", "bridge_build", "project", "mdb", "current_element"}
_FAULT_FLAGS = {"hasfault", "nativefault", "nativedatabasefault", "nativefaultlatched"}
_FAULT_CODES = {"nativefault", "nativedatabasefault", "nativedatabasefaultexception"}


def _dbref(value: Any) -> bool:
    return type(value) is str and 4 <= len(value) <= 22 and bool(_DBREF.fullmatch(value))


def _parameters(args: dict[str, Any]) -> dict[str, Any]:
    if type(args) is not dict or args.keys() - {"refno", "trace_limit"}:
        raise ValueError("PARAGON reference trace accepts only refno and trace_limit.")
    refno = args.get("refno")
    if not _dbref(refno):
        raise ValueError("refno must be a canonical numeric =database/element reference with Int32 components.")
    limit = args.get("trace_limit", DEFAULT_TRACE_LIMIT)
    if type(limit) is not int or not 1 <= limit <= MAX_TRACE_LIMIT:
        raise ValueError(f"trace_limit must be an integer between 1 and {MAX_TRACE_LIMIT}.")
    return {"refno": refno, "trace_limit": limit}


def _need(condition: bool, field: str) -> None:
    if not condition:
        raise BridgeError(f"Invalid PARAGON reference trace receipt: {field}.", response_invalid=True)


def _fields(value: Any, fields: set[str]) -> bool:
    return type(value) is dict and value.keys() == fields


def _identity_text(value: Any) -> bool:
    if type(value) is not str or not value.strip() or not value.isprintable():
        return False
    try:
        return len(value.encode("utf-16-le")) <= 512
    except UnicodeEncodeError:
        return False


def _has_embedded_fault(reply: Any) -> bool:
    # Inspect even malformed envelopes before classifying optional features.
    pending = [reply]
    seen = set()
    while pending:
        value = pending.pop()
        if not isinstance(value, (dict, list)) or id(value) in seen:
            continue
        seen.add(id(value))
        if isinstance(value, list):
            pending.extend(value)
            continue
        for key, item in value.items():
            marker = key.replace("_", "").casefold() if isinstance(key, str) else ""
            flag = marker in _FAULT_FLAGS and item is True
            code = (marker in {"errorcode", "code", "status"} and isinstance(item, str)
                    and item.replace("_", "").casefold() in _FAULT_CODES)
            if flag or code:
                return True
            pending.append(item)
    return False


def _check_embedded_fault(reply: Any) -> None:
    if _has_embedded_fault(reply):
        raise BridgeError(
            "A native database fault stopped the PARAGON reference trace. Do not retry the read.",
            diagnostics=reply,
        )


def _validate_boundary(reply: dict, status: str, used: int) -> None:
    context = reply["result"]["context"]
    _need(_fields(context, _CONTEXT_FIELDS), "context fields")
    before, after = context["before"], context["after"]
    for sample in (before, after):
        if sample is None:
            continue
        _need(_fields(sample, _SAMPLE_FIELDS), "context sample fields")
        _need(sample["session_id"] == reply["session_id"] and sample["bridge_build"] == reply["bridge_build"],
              "context session and build binding")
        _need(all(_identity_text(sample[key]) for key in ("project", "mdb")), "context project and MDB")
        ce = sample["current_element"]
        if ce is not None:
            _need(_fields(ce, {"refno", "type", "db_type_code"}), "context typed current element fields")
            _need(_dbref(ce["refno"]) and type(ce["type"]) is str and bool(_NOUN.fullmatch(ce["type"]))
                  and type(ce["db_type_code"]) is int and -2**31 <= ce["db_type_code"] <= _INT32_MAX,
                  "context typed current element")

    same_project, same_mdb = context["same_project_instance"], context["same_mdb_instance"]
    if before is None or after is None:
        _need(same_project is None and same_mdb is None, "unread context instance comparisons")
        agreement = None
    else:
        _need(type(same_project) is bool and type(same_mdb) is bool, "context instance comparisons")
        agreement = same_project and same_mdb and before == after
    _need(context["boundary_agreement"] is agreement, "context boundary agreement")
    _need((before is not None) == (used > 0) and (before is not None or after is None),
          "context sampling versus trace attempts")
    if status == "busy":
        _need(used == 0, "busy receipt cannot have trace attempts")
    elif status == "context_unverified":
        _need(agreement is not True, "unverified context cannot claim agreement")
    else:
        _need(agreement is True, "verified before and after context required")


def _validate_row(row: Any, binding: str | None, parent: dict | None, edge: str, db_code: int | None,
                  ancestors: tuple[str, ...], in_scope: Callable[[int, str], bool]) -> None:
    _need(_fields(row, {"ref", "type", "owner", "status"}), "trace row fields")
    ref, noun, owner, status = (row[key] for key in ("ref", "type", "owner", "status"))
    _need(isinstance(status, str), "trace row status")
    _need(ref is None or _dbref(ref), "trace row reference")
    _need(noun is None or (ref is not None and isinstance(noun, str) and bool(_NOUN.fullmatch(noun))),
          "trace row native base type")
    _need(owner is None or (status == "resolved" and _dbref(owner)), "trace row direct owner")
    if parent is None:
        allowed = _ATTEMPTED | {"reference_mismatch"}
    elif edge == "OWNER":
        if binding is None:
            # A verified null owner and a failed owner read are distinct.
            allowed = {"owner_read_error" if parent["status"] == "owner_read_error" else "owner_missing"}
        else:
            allowed = _ATTEMPTED | {"cycle", "reference_mismatch"}
    else:
        allowed = _ATTEMPTED | {"cycle", "unset", "attribute_unavailable"}
    _need(status in allowed, "trace row status for path")
    if parent is None or edge == "OWNER":
        _need(ref == binding, "root or OWNER reference binding")

    db_observed = status in _DBTYPE_OBSERVED and not (edge == "OWNER" and binding is None)
    if db_observed:
        _need(noun is not None and db_code is not None, "observed SDK DbType and base noun")
        _need(in_scope(db_code, noun) == (status != "out_of_scope"), "trace path DbType and base noun scope")
    else:
        _need(db_code is None, "unread SDK DbType")
        if status in {"unset", "attribute_unavailable"}:
            _need(ref is None, "no identity on an unset or unavailable attribute")
        if status == "cycle":
            _need(noun is not None and ref in ancestors, "cycle must repeat an ancestor DBREF")
        elif status != "read_error":
            _need(noun is None, "unread native base noun")
    if noun is not None and status != "cycle":
        _need(ref not in ancestors, "typed ancestor repetition must be reported as a cycle")


def _root_allowed(db: int, noun: str) -> bool:
    return db == _DESIGN or noun in _ROOT_NOUNS.get(db, ())


def _validate_paths(trace: dict, db_types: dict, refno: str, limit: int, reached: bool) -> str:
    visited = set()
    truncated = False

    def visit(path: str, edge: str, parent: dict | None, binding: str | None,
              ancestors: tuple[str, ...], expected_db: int | None = None,
              expected_nouns: set[str] | frozenset[str] = frozenset()) -> None:
        nonlocal truncated
        if len(visited) == limit:
            truncated = True
            return
        row = trace.get(path)
        db_code = db_types.get(path)
        in_scope = _root_allowed if parent is None else lambda db, noun: db == expected_db and noun in expected_nouns
        _validate_row(row, binding, parent, edge, db_code, ancestors, in_scope)
        visited.add(path)
        if row["status"] not in _EXPANDABLE or db_code is None:
            return

        noun = row["type"]
        if db_code == _DESIGN:
            nouns = {"SPCO", "SCOM"} if noun in {"NOZZ", "ELCONN"} else {"SPCO"}
            follow = (("SPREF", _CATALOGUE, nouns),)
        else:
            follow = _FOLLOW.get(noun, ()) if db_code == _CATALOGUE else ()
        for child_edge, child_db, child_nouns in follow:
            child_binding = row["owner"] if child_edge == "OWNER" else None
            visit(f"{path}.{child_edge}", child_edge, row, child_binding, ancestors + (row["ref"],),
                  child_db, child_nouns)

    visit("root", "root", None, refno, ())
    _need(trace.keys() == visited, "unplanned or over-budget trace paths")
    _need(reached is truncated, "trace budget truncation")
    if trace["root"]["status"] not in _EXPANDABLE:
        return "unavailable"
    if truncated or any(row["status"] not in {"resolved", "unset"} for row in trace.values()):
        return "partial"
    return "observed"


def validate_paragon_reference_trace_receipt(reply: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Return a validated native receipt, or raise BridgeError with no trace evidence."""
    payload = _parameters(args)
    _check_embedded_fault(reply)
    _need(_fields(reply, _ENVELOPE_FIELDS) and reply["ok"] is True and reply["version"] == VERSION,
          "native E3D21 HTTP envelope")
    _need(_identity_text(reply["session_id"]) and type(reply["bridge_build"]) is str
          and bool(_BUILD.fullmatch(reply["bridge_build"])), "session and canonical build identities")
    result = reply["result"]
    _need(_fields(result, _RESULT_FIELDS), "trace result fields")
    status = result["status"]
    _need(isinstance(status, str) and status in {"observed", "partial", "unavailable", "busy", "context_unverified"},
          "trace result status")
    budget = result["trace_limit"]
    _need(_fields(budget, {"limit", "used", "reached"}), "trace budget fields")
    limit, used, reached = (budget[key] for key in ("limit", "used", "reached"))
    _need(type(limit) is int and limit == payload["trace_limit"] and type(used) is int and 0 <= used <= limit
          and type(reached) is bool and (not reached or used == limit), "trace budget versus request")
    _validate_boundary(reply, status, used)
    trace, db_types = result["trace"], result["db_type_codes"]
    _need(isinstance(trace, dict) and isinstance(db_types, dict) and db_types.keys() <= trace.keys()
          and all(type(code) is int and -2**31 <= code <= _INT32_MAX for code in db_types.values()),
          "trace rows and observed DbType codes")
    if status in {"busy", "context_unverified"}:
        _need(not trace, "no trace identities on a busy or unverified boundary")
    else:
        _need(len(trace) == used and used > 0, "trace row count")
        _need(_validate_paths(trace, db_types, payload["refno"], limit, reached) == status, "trace result coverage")
    return reply


def _unavailable(version: str, payload: dict[str, Any], code: str, message: str) -> dict[str, Any]:
    return {
        "ok": True,
        "version": version,
        "result": {
            "operation": OPERATION,
            "requested_refno": payload["refno"],
            "available": False,
            "status": "unknown",
            "success": False,
            "complete": False,
            "read_only": True,
            "single_dispatch": None,
            "atomic_snapshot": False,
            "read_errors": [{"code": code, "message": message}],
        },
    }


def paragon_reference_trace(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    payload = _parameters(args)
    version = bridge._selected_version(None)
    if bridge.settings.mode != "bridge" or version != VERSION:
        return _unavailable(version, payload, "native_unavailable",
                            "PARAGON reference trace requires E3D21 bridge mode and a supporting Addin.")
    try:
        reply = bridge._request("POST", PATH, payload, version=VERSION)
    except BridgeError as error:
        if _has_embedded_fault(error.diagnostics) or error.transport_error:
            raise
        if error.response_invalid:
            return _unavailable(version, payload, "invalid_native_response", str(error))
        if error.http_status == 404:
            return _unavailable(version, payload, "native_feature_unavailable",
                                "The running Addin does not expose the PARAGON reference trace reader.")
        raise
    _check_embedded_fault(reply)
    try:
        return validate_paragon_reference_trace_receipt(reply, payload)
    except BridgeError as error:
        if not error.response_invalid:
            raise
        return _unavailable(version, payload, "invalid_native_response", str(error))


def register_paragon_reference_trace(server: McpServer, bridge: BridgeClient) -> None:
    server.add_tool(
        TOOL,
        "Read fixed native DBREF paths from one explicit element in E3D21. Inspect SDK database type, base element "
        "type and direct owner evidence, together with the before and after session, build, project, MDB and current "
        "element boundary. Each attempted path uses one budget entry, including the root and repeated DBREFs on "
        "distinct paths. Unread paths remain unknown. The receipt provides reference evidence only and establishes "
        "neither engineering connectivity nor material compatibility. Requires a supporting E3D21 Addin.",
        {"type": "object", "properties": {
            "refno": {"type": "string", "pattern": "^" + _DBREF_PATTERN + r"(?![\s\S])",
                      "minLength": 4, "maxLength": 22,
                      "description": "Explicit =database/element DBREF with canonical ASCII Int32 components."},
            "trace_limit": {"type": "integer", "minimum": 1, "maximum": MAX_TRACE_LIMIT,
                            "default": DEFAULT_TRACE_LIMIT},
        }, "required": ["refno"], "additionalProperties": False},
        lambda args: paragon_reference_trace(bridge, args), read_only=True,
    )

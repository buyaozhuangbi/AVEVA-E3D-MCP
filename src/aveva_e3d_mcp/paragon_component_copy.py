"""Prepare or submit one session-bound native PARAGON SCOM copy.

The server owns the fixed two-statement PML implementation. Python forwards
only explicit recipe arguments and execution tokens, makes no supplemental
reads, and never replays a submission. Status reads inspect managed history.
"""
from __future__ import annotations

from datetime import datetime
import math
import re
import struct
from typing import Any

from .bridge import BridgeClient, BridgeError, _contains_native_fault
from .paragon_component_template import (
    _IDENTITY, _REF_PATTERN, _REFERENCES, _core_identity,
    _identity as _template_identity, _validate as _validate_template,
)
from .protocol import McpServer


VERSION = "E3D21"
TOOL = "e3d_paragon_component_copy"
STATUS_TOOL = "e3d_paragon_component_copy_status"
PATH = "/api/v1/paragon/component-copy"
STATUS_PATH = PATH + "/status"
OPERATION = "paragon_component_copy"
RECIPE = "same_category_scom_copy_v1"

_REF = re.compile(_REF_PATTERN)
_UUID_PATTERN = r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}"
_UUID = re.compile(_UUID_PATTERN)
_SHA = re.compile(r"[a-f0-9]{64}")
_NAME_PATTERN = r"/(?:[A-Za-z][A-Za-z0-9_-]*)(?:/[A-Za-z][A-Za-z0-9_-]*)*(?![\s\S])"
_NAME = re.compile(_NAME_PATTERN)
_CODE = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_BASE_ARGUMENTS = {"source_refno", "parent_refno", "name", "recipe", "dry_run"}
_EXECUTION_ARGUMENTS = {"request_id", "expected_session_id", "expected_bridge_build", "expected_revision"}
_REQUEST_FIELDS = {"source_refno", "parent_refno", "name", "recipe"}
_PROGRESS_FIELDS = {
    "creation_attempted", "native_run_attempted", "native_run_returned", "native_run_success",
    "known_new_refno", "known_new_refno_verified", "readback_complete", "source_unchanged_scoped",
    "copied_parameters_equal", "copied_references_equal", "outcome_uncertain", "native_fault_latched", "retry_safe",
}
_RESULT_FIELDS = {
    "operation", "recipe", "dry_run", "request", "session_id", "bridge_build", "request_id", "status",
    "success", "complete", "scoped_revision", "before", "after", "progress", "errors", "journal", "scope",
    "this_submission_executed", "request_replayed",
}
_BEFORE_FIELDS = {"module", "context", "source_template", "parent", "source_owner", "members",
                  "members_complete", "name_lookup", "creatable"}
_AFTER_FIELDS = {"module", "context", "source_template", "new_template", "parent", "members",
                 "members_complete", "verification", "new_owner"}
_VERIFICATION_FIELDS = {"new_identity", "parent_append", "source_unchanged_scoped",
                        "copied_parameters_equal", "copied_references_equal"}
_SCOPE = {
    "kind": "same_category_scom_template_copy",
    "backend": "server_generated_fixed_pml",
    "parameter_comparison": "complete_finite_numeric_PARAM_only",
    "reference_comparison": "observed_supported_fixed_seven_only",
    "all_references_covered": False, "whole_clone_fingerprint": False,
    "geometry_observed": False, "geometry_equivalence_verified": False,
    "word_parameters_observed": False, "hidden_attributes_observed": False,
    "engineering_validated": None, "write_permission_verified": None,
    "savework_performed": False, "automatic_ce_restore": False, "atomic_snapshot": False,
    "scoped_revision_is_lock": False, "whole_project_coverage": False,
}
_JOURNAL_FIELDS = {
    "operation", "available", "status", "request_id", "request_fingerprint", "request_id_reserved",
    "accepted_utc", "started_utc", "completed_utc", "completion_pending", "http_status", "receipt_available",
    "receipt_omitted_reason", "receipt", "critical_summary", "retry_safe", "store_policy", "scope",
}
_SUMMARY_FIELDS = {
    "creation_attempted", "native_run_attempted", "native_run_returned", "native_run_success",
    "known_new_refno", "known_new_refno_verified", "readback_complete", "source_unchanged_scoped",
    "outcome_uncertain", "native_fault_latched", "error_code", "retry_safe",
}
_JOURNAL_SCOPE = (
    "Session-local managed callback ownership only; accepted is not running, running does not prove native execution, "
    "and completed records a final receipt rather than native success. Unknown/expired does not prove no execution. "
    "No replay, durability, cross-session exactly-once, or SDK/UI ownership guarantee."
)


def _refno(value: Any) -> bool:
    return type(value) is str and 4 <= len(value) <= 22 and bool(_REF.fullmatch(value))


def _uuid(value: Any) -> bool:
    return type(value) is str and bool(_UUID.fullmatch(value))


def _sha(value: Any) -> bool:
    return type(value) is str and bool(_SHA.fullmatch(value))


def _text(value: Any, maximum: int, *, printable: bool = False) -> bool:
    if type(value) is not str or not value.strip() or (printable and not value.isprintable()):
        return False
    try:
        return len(value.encode("utf-16-le")) <= 2 * maximum
    except UnicodeEncodeError:
        return False


def _parameters(args: Any) -> dict[str, Any]:
    if type(args) is not dict or args.keys() - (_BASE_ARGUMENTS | _EXECUTION_ARGUMENTS):
        raise ValueError("Only explicit component-copy recipe arguments and execution tokens are accepted.")
    payload = {}
    for key in ("source_refno", "parent_refno"):
        if not _refno(args.get(key)):
            raise ValueError(key + " must be a canonical numeric Int32 =database/element reference.")
        payload[key] = args[key]
    if payload["source_refno"] == payload["parent_refno"]:
        raise ValueError("source_refno and parent_refno must be different.")
    name = args.get("name")
    if type(name) is not str or not 2 <= len(name) <= 50 or not _NAME.fullmatch(name):
        raise ValueError("name must be an ASCII slash-separated NAME of at most 50 characters; each segment starts with a letter.")
    if args.get("recipe", RECIPE) != RECIPE:
        raise ValueError("recipe must be " + RECIPE + ".")
    dry_run = args.get("dry_run", True)
    if type(dry_run) is not bool:
        raise ValueError("dry_run must be a boolean.")
    payload.update(name=name, recipe=RECIPE, dry_run=dry_run)
    if dry_run:
        if args.keys() & _EXECUTION_ARGUMENTS:
            raise ValueError("Execution tokens must be absent when dry_run is true.")
        return payload
    if not _EXECUTION_ARGUMENTS <= args.keys():
        raise ValueError("Execution requires request_id, expected_session_id, expected_bridge_build and expected_revision.")
    for key in ("request_id", "expected_bridge_build"):
        if not _uuid(args[key]):
            raise ValueError(key + " must be a canonical lowercase UUID with hyphens.")
    if not _text(args["expected_session_id"], 256, printable=True):
        raise ValueError("expected_session_id must be nonempty printable text of at most 256 UTF16 units.")
    if not _sha(args["expected_revision"]):
        raise ValueError("expected_revision must be a 64-character lowercase SHA-256 digest.")
    payload.update({key: args[key] for key in _EXECUTION_ARGUMENTS})
    return payload


def _status_parameters(args: Any) -> dict[str, str]:
    if type(args) is not dict or args.keys() != {"request_id", "expected_session_id"}:
        raise ValueError("Status requires only request_id and expected_session_id.")
    if not _uuid(args["request_id"]):
        raise ValueError("request_id must be a canonical lowercase UUID with hyphens.")
    if not _text(args["expected_session_id"], 256, printable=True):
        raise ValueError("expected_session_id must be nonempty printable text of at most 256 UTF16 units.")
    return dict(args)


def _need(condition: bool, field: str) -> None:
    if not condition:
        raise ValueError("Invalid PARAGON component-copy receipt: " + field + ".")


def _fields(value: Any, expected: set[str], field: str) -> dict[str, Any]:
    _need(type(value) is dict and value.keys() == expected, field + " fields")
    return value


def _nullable_bool(value: Any) -> bool:
    return value is None or type(value) is bool


def _scope(value: Any) -> None:
    _need(type(value) is dict and _SCOPE.keys() <= value.keys(), "scope")
    _need(all(type(value[key]) is type(expected) and value[key] == expected
              for key, expected in _SCOPE.items()), "bounded copy scope")


def _envelope(reply: Any) -> tuple[dict[str, Any], str, str]:
    envelope = _fields(reply, {"ok", "version", "session_id", "bridge_build", "result"}, "native envelope")
    _need(envelope["ok"] is True and envelope["version"] == VERSION, "native E3D21 success envelope")
    session, build = envelope["session_id"], envelope["bridge_build"]
    _need(_text(session, 256, printable=True) and _uuid(build), "native session/build")
    return envelope["result"], session, build


def _errors(value: Any) -> None:
    _need(type(value) is list and len(value) <= 100, "bounded errors")
    for item in value:
        error = _fields(item, {"stage", "code", "message"}, "error")
        _need(type(error["stage"]) is str and bool(_CODE.fullmatch(error["stage"]))
              and type(error["code"]) is str and bool(_CODE.fullmatch(error["code"]))
              and _text(error["message"], 4096), "error text")


def _progress(value: Any) -> dict[str, Any]:
    progress = _fields(value, _PROGRESS_FIELDS, "progress")
    for key in ("creation_attempted", "native_run_attempted", "native_run_returned", "readback_complete",
                "outcome_uncertain", "native_fault_latched"):
        _need(type(progress[key]) is bool, "progress " + key)
    for key in ("native_run_success", "known_new_refno_verified", "source_unchanged_scoped",
                "copied_parameters_equal", "copied_references_equal"):
        _need(_nullable_bool(progress[key]), "progress " + key)
    _need(progress["retry_safe"] is False, "no automatic replay")
    ref = progress["known_new_refno"]
    _need(ref is None or _refno(ref), "known new numeric REF")
    _need(progress["known_new_refno_verified"] is not True or ref is not None, "verified new REF present")
    _need(not progress["native_run_returned"] or progress["native_run_attempted"], "Run return requires attempt")
    _need(progress["native_run_success"] is None or progress["native_run_returned"], "Run result requires return")
    _need(not progress["native_run_attempted"] or progress["creation_attempted"], "native creation attempt")
    return progress


def _validate(reply: Any, payload: dict[str, Any]) -> None:
    result, session, build = _envelope(reply)
    result = _fields(result, _RESULT_FIELDS, "copy result")
    _need(result["operation"] == OPERATION and result["recipe"] == RECIPE, "operation and recipe")
    _need(result["dry_run"] is payload["dry_run"] and result["request"] ==
          {key: payload[key] for key in _REQUEST_FIELDS}, "explicit request binding")
    _fields(result["request"], _REQUEST_FIELDS, "request")
    _need(result["session_id"] == session and result["bridge_build"] == build, "result session/build")
    _need(result["request_id"] is None if payload["dry_run"] else
          result["request_id"] == payload["request_id"], "request ID binding")
    if not payload["dry_run"]:
        _need(session == payload["expected_session_id"] and build == payload["expected_bridge_build"],
              "execution session/build pin")
    _need(result["request_replayed"] is False and type(result["this_submission_executed"]) is bool,
          "submission replay flags")
    _need(type(result["success"]) is bool and type(result["complete"]) is bool, "copy outcome types")
    _need(result["scoped_revision"] is None or _sha(result["scoped_revision"]), "scoped revision")
    _scope(result["scope"])
    _errors(result["errors"])
    progress = _progress(result["progress"])
    # Full evidence validation is kept in one place for both public calls and
    # the native receipt harness. No success is derived from Run alone.
    _validate_evidence(result, payload, session, build, progress)


def _validate_evidence(result: dict[str, Any], payload: dict[str, Any], session: str, build: str,
                       progress: dict[str, Any]) -> None:
    status, dry_run = result["status"], payload["dry_run"]
    _need(type(status) is str and status in ({"prepared", "blocked"} if dry_run else {"executed"}), "normal copy status")
    _need(result["this_submission_executed"] is progress["native_run_attempted"], "this submission native attempt")
    before, after = result["before"], result["after"]
    before_ready = _before(before, payload, session, build) if before is not None else False
    after_ready = _after(after, before, payload, session, build, progress) if after is not None else False
    if result["journal"] is not None:
        _need(not dry_run, "prepare has no execution journal")
        _journal(result["journal"], payload["request_id"], session, build)
    if dry_run:
        _need(after is None and result["journal"] is None and not result["this_submission_executed"],
              "prepare performs no submission or readback")
        _fixed_not_attempted(progress)
        if status == "prepared":
            _need(before_ready and not result["errors"] and _sha(result["scoped_revision"]), "prepared preconditions")
            _need(result["success"] is True and result["complete"] is True, "prepared scoped completeness")
        else:
            _need(result["success"] is False and result["complete"] is False and bool(result["errors"]),
                  "blocked observations cannot claim preparation")
        return
    _need(before_ready and after_ready and not result["errors"] and result["scoped_revision"] == payload["expected_revision"],
          "executed scoped evidence")
    _need(result["success"] is True and result["complete"] is True and result["this_submission_executed"] is True,
          "executed success")
    _need(all(progress[key] is True for key in (
        "creation_attempted", "native_run_attempted", "native_run_returned", "native_run_success",
        "known_new_refno_verified", "readback_complete", "source_unchanged_scoped", "copied_parameters_equal",
        "copied_references_equal")) and progress["outcome_uncertain"] is False
        and progress["native_fault_latched"] is False, "executed verified progress")


def _fixed_not_attempted(progress: dict[str, Any]) -> None:
    false_fields = {"creation_attempted", "native_run_attempted", "native_run_returned", "readback_complete",
                    "outcome_uncertain", "native_fault_latched", "retry_safe"}
    _need(all(progress[key] is False if key in false_fields else progress[key] is None
              for key in _PROGRESS_FIELDS), "no mutating stage was entered")


def _identity_key(value: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(value[key] for key in ("refno", "type", "db_type", "db_type_code", "identity_key"))


def _full_identity(value: Any, field: str) -> None:
    _template_identity(value, field)


def _kind(value: dict[str, Any] | None, kind: str) -> bool:
    if value is None or value["type"] != kind or value["db_type_code"] != 2 or value["db_type"] != "Catalogue":
        return False
    return all(value[key] is not None and value[key]["short_name"] == kind for key in ("base_type", "actual_type"))


def _context(value: Any, session: str, build: str, field: str) -> bool:
    row = _fields(value, {"session_id", "bridge_build", "project", "mdb", "current_element", "instance_binding"}, field)
    _need(row["session_id"] == session and row["bridge_build"] == build, field + " session/build")
    for key in ("project", "mdb"):
        _need(row[key] is None or _text(row[key], 256), field + " " + key)
    _need(row["instance_binding"] is None or _sha(row["instance_binding"]), field + " instance tag")
    ce = row["current_element"]
    if ce is not None:
        _fields(ce, _IDENTITY, field + " typed CE")
        _core_identity(ce, field + " typed CE")
    return all(row[key] is not None for key in ("project", "mdb", "current_element", "instance_binding"))


def _module(value: Any) -> bool:
    row = _fields(value, {"name", "number"}, "module")
    _need((row["name"] is None or _text(row["name"], 256)) and
          (row["number"] is None or type(row["number"]) is int), "module observations")
    return row["name"] == "PARAGON" and type(row["number"]) is int and row["number"] == 81


def _members(value: Any, complete: Any, maximum: int) -> bool:
    _need(type(complete) is bool and type(value) is list and len(value) <= maximum, "member observation bounds")
    seen, known = set(), True
    for row in value:
        _fields(row, _IDENTITY | {"name", "name_error"}, "direct member")
        _core_identity(row, "direct member")
        if row["name_error"] is None:
            _need(_text(row["name"], 1024), "member NAME")
        else:
            _need(row["name"] is None and _text(row["name_error"], 4096), "member unknown NAME")
            known = False
        if row["refno"] in seen or row["db_type_code"] != 2:
            known = False
        seen.add(row["refno"])
    return complete and known


def _template(value: Any, reference: str, session: str, build: str,
              context: dict[str, Any] | None) -> bool:
    _validate_template({"ok": True, "version": VERSION, "session_id": session, "bridge_build": build, "result": value},
                       {"refno": reference})
    if context is None or value["context"]["boundary_agreement"] is not True:
        return False
    for side in ("before", "after"):
        sample = value["context"][side]
        if sample["errors"] or any(sample[key] != context[key] for key in ("project", "mdb", "current_element")):
            return False
    return True


def _numbers(template: dict[str, Any]) -> tuple[bytes, ...] | None:
    value = template["parameters"]
    if not (value["supported"] is True and value["sdk_type"] == "DOUBLE" and value["is_array"] is True
            and value["shape"] == "double_array" and value["truncated"] is False
            and value["inspection_limited"] is False and value["native_array_materialized"] is True
            and value["errors"].keys() <= {"unit_kind"} and type(value["total_count"]) is int
            and value["total_count"] == value["returned_count"] == len(value["values"])):
        return None
    numbers = []
    for index, row in enumerate(value["values"]):
        number = row["value"]
        if row["index"] != index or row["status"] != "observed" or row["error"] is not None:
            return None
        if type(number) not in (int, float) or not math.isfinite(number):
            return None
        numbers.append(struct.pack(">d", number))
    return tuple(numbers)


def _reference(value: dict[str, Any]) -> tuple[Any, ...] | str | None:
    if value["supported"] is False:
        return "unsupported_excluded"
    if not (value["supported"] is True and value["sdk_type"] == "ELEMENT" and type(value["is_array"]) is bool
            and value["truncated"] is False and value["inspection_limited"] is False
            and value["errors"].keys() <= {"unit_kind", "items"} and type(value["total_count"]) is int
            and value["total_count"] == value["returned_count"] == len(value["values"])):
        return None
    if value["is_array"] and value["native_array_materialized"] is not True:
        return None
    identities = []
    for index, row in enumerate(value["values"]):
        identity = row["identity"]
        if row["index"] != index:
            return None
        if (row["reference_null"] is True and row["status"] == "unset" and identity is None
                and row["error"] is None):
            # Verified SDK native-null is an observed value. Preserve its
            # position; an unread CLR proxy or failed getter remains unknown.
            identities.append(None)
            continue
        if row["reference_null"] is not False or identity is None:
            return None
        basis, actual = identity["base_type"], identity["actual_type"]
        if basis is None or actual is None or basis["short_name"] is None or actual["short_name"] is None:
            return None
        if basis["short_name"] != identity["type"] or "request_binding" in identity["errors"]:
            return None
        identities.append((*_identity_key(identity), basis["short_name"], actual["short_name"]))
    return value["shape"], tuple(identities)


def _references_equal(source: dict[str, Any], target: dict[str, Any]) -> bool | None:
    unknown, compared = False, False
    for name in _REFERENCES:
        first, last = _reference(source["references"][name]), _reference(target["references"][name])
        if first is None or last is None:
            unknown = True
        elif first != last:
            return False
        elif first != "unsupported_excluded":
            compared = True
    return None if unknown or not compared else True


def _parameters_equal(source: dict[str, Any], target: dict[str, Any]) -> bool | None:
    first, last = _numbers(source), _numbers(target)
    return None if first is None or last is None else first == last


def _source_equal(source: dict[str, Any], target: dict[str, Any]) -> bool | None:
    first, last = source["target"], target["target"]
    if first is None or last is None:
        return None
    for key in ("refno", "type", "db_type_code", "name"):
        if first[key] is None or last[key] is None:
            return None
        if first[key] != last[key]:
            return False
    if not _kind(first, "SCOM") or not _kind(last, "SCOM"):
        return False
    leaf = target["leaf"]["is_leaf"]
    if leaf is not True:
        return None if leaf is None else False
    numeric, references = _parameters_equal(source, target), _references_equal(source, target)
    return False if numeric is False or references is False else True if numeric is True and references is True else None


def _before(value: Any, payload: dict[str, Any], session: str, build: str) -> bool:
    row = _fields(value, _BEFORE_FIELDS, "before")
    ready = _module(row["module"]) if row["module"] is not None else False
    ready &= _context(row["context"], session, build, "before context") if row["context"] is not None else False
    template = row["source_template"]
    if template is not None:
        ready &= _template(template, payload["source_refno"], session, build, row["context"])
    else:
        ready = False
    for key in ("parent", "source_owner"):
        identity = row[key]
        if identity is None:
            ready = False
        else:
            _full_identity(identity, "before " + key)
            ready &= identity["refno"] == payload["parent_refno"] and _kind(identity, "CATE")
    ready &= _members(row["members"], row["members_complete"], 100)
    _need(_nullable_bool(row["creatable"]), "creatability type")
    ready &= row["creatable"] is True
    lookup = _fields(row["name_lookup"], {"status", "target"}, "name lookup")
    _need(type(lookup["status"]) is str and lookup["status"] in {"absent", "present", "unknown"}, "name lookup status")
    if lookup["status"] == "present":
        _full_identity(lookup["target"], "name lookup target")
    else:
        _need(lookup["target"] is None, "unobserved name lookup target")
    ready &= lookup["status"] == "absent"
    if template is None:
        return False
    source = template["target"]
    ready &= (source is not None and source["refno"] == payload["source_refno"] and _kind(source, "SCOM")
              and source["name"] is not None and source["name_error"] is None
              and template["leaf"]["is_leaf"] is True and _numbers(template) is not None
              and _references_equal(template, template) is True)
    listed = [item for item in row["members"] if item["refno"] == payload["source_refno"] and item["type"] == "SCOM"]
    ready &= len(listed) == 1
    if source is not None and len(listed) == 1:
        ready &= _identity_key(listed[0]) == _identity_key(source) and listed[0]["name"] == source["name"]
    ready &= not any(type(item["name"]) is str and item["name"].casefold() == payload["name"].casefold()
                     for item in row["members"])
    return bool(ready)


def _after(value: Any, before: Any, payload: dict[str, Any], session: str, build: str,
           progress: dict[str, Any]) -> bool:
    row = _fields(value, _AFTER_FIELDS, "after")
    ready = _module(row["module"]) if row["module"] is not None else False
    ready &= _context(row["context"], session, build, "after context") if row["context"] is not None else False
    ready &= _members(row["members"], row["members_complete"], 101)
    for key in ("parent", "new_owner"):
        if row[key] is not None:
            _full_identity(row[key], "after " + key)
            ready &= row[key]["refno"] == payload["parent_refno"] and _kind(row[key], "CATE")
        else:
            ready = False
    checks = _fields(row["verification"], _VERIFICATION_FIELDS, "verification")
    _need(all(_nullable_bool(item) for item in checks.values()), "verification observation types")
    source, new = row["source_template"], row["new_template"]
    for raw, reference in ((source, payload["source_refno"]), (new, progress["known_new_refno"])):
        if raw is not None:
            _need(_refno(reference), "template numeric request binding")
            ready &= _template(raw, reference, session, build, row["context"])
        else:
            ready = False
    if before is None:
        return False
    if before["context"] is not None and row["context"] is not None:
        ready &= all(before["context"][key] == row["context"][key] for key in ("project", "mdb", "instance_binding"))
    else:
        ready = False
    if before["parent"] is not None and row["parent"] is not None:
        ready &= _identity_key(before["parent"]) == _identity_key(row["parent"])
    first, last = before["members"], row["members"]
    append = (before["members_complete"] and row["members_complete"] and len(last) == len(first) + 1
              and all(_identity_key(old) == _identity_key(observed) and old["name"] == observed["name"]
                      for old, observed in zip(first, last)))
    _need(checks["parent_append"] is bool(append), "ordered unique append evidence")
    identity = new["target"] if new is not None else None
    new_identity = (append and identity is not None and _kind(identity, "SCOM")
                    and identity["refno"] == progress["known_new_refno"] == last[-1]["refno"]
                    and _identity_key(identity) == _identity_key(last[-1])
                    and identity["refno"] not in {payload["source_refno"], payload["parent_refno"]}
                    and identity["name_error"] is None and type(identity["name"]) is str
                    and type(last[-1]["name"]) is str
                    and identity["name"].isascii() and last[-1]["name"].isascii()
                    and identity["name"].casefold() == last[-1]["name"].casefold() == payload["name"].casefold()
                    and new["leaf"]["is_leaf"] is True and row["new_owner"] is not None
                    and row["new_owner"]["refno"] == payload["parent_refno"] and _kind(row["new_owner"], "CATE"))
    if checks["new_identity"] is True:
        _need(new_identity, "new numeric identity, NAME, owner and leaf evidence")
    ready &= bool(new_identity) and checks["new_identity"] is True and append
    old_source = before["source_template"]
    expected_source = _source_equal(old_source, source) if old_source is not None and source is not None else None
    expected_parameters = _parameters_equal(source, new) if source is not None and new is not None else None
    expected_refs = _references_equal(source, new) if source is not None and new is not None else None
    for key, expected in (("source_unchanged_scoped", expected_source), ("copied_parameters_equal", expected_parameters),
                          ("copied_references_equal", expected_refs)):
        _need(checks[key] is expected and progress[key] is expected, "independent " + key + " comparison")
        ready &= expected is True
    return bool(ready)


def _validate_status(reply: Any, payload: dict[str, str]) -> None:
    snapshot, session, build = _envelope(reply)
    _need(session == payload["expected_session_id"], "status session pin")
    _journal(snapshot, payload["request_id"], session, build)


def _journal(snapshot: Any, request_id: str, session: str, build: str) -> None:
    row = _fields(snapshot, _JOURNAL_FIELDS, "journal")
    _need(row["operation"] == OPERATION and row["request_id"] == request_id and row["retry_safe"] is False,
          "journal operation/request/replay")
    _need(type(row["available"]) is bool and type(row["request_id_reserved"]) is bool
          and type(row["completion_pending"]) is bool and type(row["receipt_available"]) is bool,
          "journal flags")
    status = row["status"]
    _need(type(status) is str and status in {"accepted", "running", "completed", "unknown_or_expired"}, "journal state")
    _need(row["scope"] == _JOURNAL_SCOPE, "journal observation scope")
    policy = _fields(row["store_policy"], {
        "receipt_record_limit", "max_receipt_utf8_bytes", "session_request_id_limit", "completed_retention_seconds",
        "maximum_json_depth", "receipt_budget_kind", "accepted_or_running_evicted", "request_ids_released_before_session_end",
        "durable", "cross_session_exactly_once",
    }, "journal store policy")
    for key, maximum in (("receipt_record_limit", 64), ("max_receipt_utf8_bytes", 262144), ("session_request_id_limit", 65536)):
        _need(type(policy[key]) is int and 1 <= policy[key] <= maximum, "journal " + key)
    retention = policy["completed_retention_seconds"]
    _need(type(retention) in (int, float) and 0 < retention <= 1800 and policy["maximum_json_depth"] == 64
          and type(policy["maximum_json_depth"]) is int, "journal retention/depth")
    _need(policy["receipt_budget_kind"] == "serialized_utf8_logical_bytes" and all(policy[key] is False for key in
          ("accepted_or_running_evicted", "request_ids_released_before_session_end", "durable", "cross_session_exactly_once")),
          "journal bounded session policy")
    summary = _fields(row["critical_summary"], _SUMMARY_FIELDS, "journal critical summary")
    for key in _SUMMARY_FIELDS - {"known_new_refno", "error_code", "retry_safe"}:
        _need(_nullable_bool(summary[key]), "journal nullable native fact " + key)
    _need(summary["known_new_refno"] is None or _refno(summary["known_new_refno"]), "journal captured numeric REF")
    _need(summary["error_code"] is None or _text(summary["error_code"], 128), "journal native error code")
    _need(summary["retry_safe"] is False, "journal summary no replay")
    times = {}
    for key in ("accepted_utc", "started_utc", "completed_utc"):
        value = row[key]
        if value is None:
            times[key] = None
        else:
            _need(type(value) is str and len(value) <= 40, "journal UTC timestamp")
            try:
                times[key] = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                _need(False, "journal UTC timestamp")
            _need(times[key].utcoffset() is not None and times[key].utcoffset().total_seconds() == 0,
                  "journal UTC timestamp offset")
    if status == "unknown_or_expired":
        _need(row["available"] is False and row["request_fingerprint"] is None and not row["completion_pending"]
              and all(value is None for value in times.values()) and row["http_status"] is None
              and row["receipt_available"] is False and row["receipt"] is None
              and row["receipt_omitted_reason"] == "unknown_or_expired", "unknown journal record")
        _need(all(summary[key] is None for key in _SUMMARY_FIELDS - {"retry_safe"}), "unknown native outcome")
        return
    _need(row["available"] is True and row["request_id_reserved"] is True and _sha(row["request_fingerprint"])
          and times["accepted_utc"] is not None, "reserved journal record")
    if status != "completed":
        _need(times["completed_utc"] is None and row["http_status"] is None and row["receipt_available"] is False
              and row["receipt"] is None and row["receipt_omitted_reason"] == "not_completed", "active journal state")
        _need((times["started_utc"] is not None) is (status == "running"), "journal callback admission state")
        _need(all(summary[key] is None for key in _SUMMARY_FIELDS - {"retry_safe"}), "active native facts unknown")
        return
    _need(times["completed_utc"] is not None and row["completion_pending"] is False
          and type(row["http_status"]) is int and 100 <= row["http_status"] <= 599, "completed journal state")
    receipt = row["receipt"]
    if not row["receipt_available"]:
        _need(receipt is None and row["receipt_omitted_reason"] in {
            "receipt_too_large", "receipt_not_pure_json", "serialization_failed", "receipt_missing",
        }, "omitted completed receipt")
        return
    _need(row["receipt_omitted_reason"] is None and type(receipt) is dict, "stored completed receipt")
    _need({"ok", "version", "session_id", "bridge_build", "result"} <= receipt.keys()
          and receipt.keys() <= {"ok", "version", "session_id", "bridge_build", "result", "error", "error_code",
                                 "native_fault_latched", "retry_safe"}, "stored HTTP envelope")
    _need(type(receipt["ok"]) is bool and receipt["version"] == VERSION and receipt["session_id"] == session
          and receipt["bridge_build"] == build, "stored envelope session/build")
    stored = receipt["result"]
    _fields(stored, _RESULT_FIELDS, "stored copy result")
    _need(stored["request_id"] == request_id and stored["session_id"] == session and stored["bridge_build"] == build
          and stored["journal"] is None and stored["request_replayed"] is False, "stored submission binding")
    progress = _progress(stored["progress"])
    for key in _SUMMARY_FIELDS - {"error_code", "retry_safe"}:
        _need(summary[key] is None or summary[key] == progress[key], "stored critical fact " + key)
    if receipt["ok"]:
        request = _fields(stored["request"], _REQUEST_FIELDS, "stored request")
        payload = _parameters({**request, "dry_run": False, "request_id": request_id,
                               "expected_session_id": session, "expected_bridge_build": build,
                               "expected_revision": stored["scoped_revision"]})
        _validate({key: receipt[key] for key in ("ok", "version", "session_id", "bridge_build", "result")}, payload)
    else:
        _need(stored["status"] in {"failed", "rejected", "duplicate"} and stored["success"] is False
              and stored["complete"] is False and _text(receipt.get("error"), 4096), "stored failed outcome")


def _runtime(bridge: BridgeClient) -> None:
    if bridge.settings.mode != "bridge" or bridge._selected_version(None) != VERSION:
        raise BridgeError("PARAGON component copy requires E3D21 bridge mode and a supporting Addin.")


def _uncertain(error: BridgeError, payload: dict[str, Any]) -> None:
    if not payload["dry_run"] and (error.transport_error or error.response_invalid or error.outcome_uncertain):
        error.outcome_uncertain = True
        # Preserve the native result and any already observed native flags.
        error.diagnostics.setdefault("outcome_uncertain", True)
        error.diagnostics.setdefault("retry_safe", False)


def paragon_component_copy(bridge: BridgeClient, args: Any) -> dict[str, Any]:
    payload = _parameters(args)
    if not payload["dry_run"] and bridge.settings.allow_commands is not True:
        raise ValueError("Component-copy execution requires E3D_MCP_ALLOW_COMMANDS=true.")
    _runtime(bridge)
    try:
        reply = bridge._request("POST", PATH, payload, version=VERSION)
    except BridgeError as error:
        _uncertain(error, payload)
        raise
    if _contains_native_fault(reply):
        raise BridgeError("A native database fault stopped the component copy. Do not retry.", diagnostics=reply,
                          outcome_uncertain=not payload["dry_run"])
    try:
        _validate(reply, payload)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
        invalid = BridgeError(str(error), response_invalid=True, outcome_uncertain=not payload["dry_run"],
                              diagnostics={"outcome_uncertain": not payload["dry_run"], "retry_safe": False})
        raise invalid from error
    return reply


def paragon_component_copy_status(bridge: BridgeClient, args: Any) -> dict[str, Any]:
    payload = _status_parameters(args)
    _runtime(bridge)
    reply = bridge._request("GET", STATUS_PATH, query=payload, version=VERSION)
    # Fault evidence inside a stored historical receipt is data. This lookup
    # is managed and remains usable after the native latch has been set.
    try:
        _validate_status(reply, payload)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
        raise BridgeError(str(error), response_invalid=True) from error
    return reply


def register_paragon_component_copy(server: McpServer, bridge: BridgeClient) -> None:
    uuid_schema = {"type": "string", "minLength": 36, "maxLength": 36,
                   "pattern": "^" + _UUID_PATTERN + r"(?![\s\S])"}
    session_schema = {"type": "string", "minLength": 1, "maxLength": 256,
                      "description": "Explicit printable server-owned session identity, at most 256 UTF16 units."}
    reference_schema = {"type": "string", "minLength": 4, "maxLength": 22, "pattern": _REF_PATTERN}
    server.add_tool(
        TOOL,
        "Prepare or execute one same-category Catalogue SCOM copy in E3D21 PARAGON. Preparation defaults to "
        "dry_run=true and returns a scoped revision only from sufficient native observations. Execution requires "
        "command permission, a caller-owned request ID, and the exact prepared session, build and revision. "
        "The server generates one fixed PML recipe and verifies the new numeric REF, owner, NAME, ordered parent "
        "append, finite numeric PARAM values and observed supported fixed references. This is a scoped check; "
        "word parameters, complete clone identity, geometry, engineering suitability and persistence remain "
        "unverified. No automatic retry, rollback, CE restore, SAVEWORK or generic script forwarding.",
        {"type": "object", "properties": {
            "source_refno": dict(reference_schema), "parent_refno": dict(reference_schema),
            "name": {"type": "string", "minLength": 2, "maxLength": 50, "pattern": "^" + _NAME_PATTERN},
            "recipe": {"type": "string", "const": RECIPE, "default": RECIPE},
            "dry_run": {"type": "boolean", "default": True},
            "request_id": dict(uuid_schema), "expected_session_id": dict(session_schema),
            "expected_bridge_build": dict(uuid_schema),
            "expected_revision": {"type": "string", "minLength": 64, "maxLength": 64,
                                  "pattern": r"^[a-f0-9]{64}(?![\s\S])"},
        }, "required": ["source_refno", "parent_refno", "name"], "additionalProperties": False,
         "allOf": [{"if": {"required": ["dry_run"], "properties": {"dry_run": {"const": False}}},
                    "then": {"required": sorted(_EXECUTION_ARGUMENTS)},
                    "else": {"not": {"anyOf": [{"required": [key]} for key in sorted(_EXECUTION_ARGUMENTS)]}}}]},
        lambda args: paragon_component_copy(bridge, args), read_only=False,
    )
    server.add_tool(
        STATUS_TOOL,
        "Read the session-local managed journal for one explicit component-copy request ID. Requires the original "
        "session ID and performs no SDK reads, busy check or replay. Accepted/running/completed describe journal "
        "ownership; completion alone does not prove native success, and unknown or expired does not prove no write. "
        "Historical native faults remain available for inspection. The receipt is not durable across sessions.",
        {"type": "object", "properties": {"request_id": dict(uuid_schema), "expected_session_id": dict(session_schema)},
         "required": ["request_id", "expected_session_id"], "additionalProperties": False},
        lambda args: paragon_component_copy_status(bridge, args), read_only=True,
    )

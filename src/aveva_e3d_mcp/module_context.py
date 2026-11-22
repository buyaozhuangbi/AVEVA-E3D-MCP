"""Read-only E3D21 active-module identity and bounded native context."""
from __future__ import annotations

import re
from typing import Any

from .bridge import BridgeClient, BridgeError
from .protocol import McpServer

TOOL = "e3d_module_context"
PATH = "/api/v1/module/context"
VERSION = "E3D21"
MAX_ERRORS = 24
MAX_TEXT_LENGTH = 256
MAX_ERROR_LENGTH = 320
_GUID = re.compile(r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")
_REFNO = re.compile(r"\A=[0-9]+/[0-9]+\Z")
_ELEMENT_TYPE = re.compile(r"\A[A-Z][A-Z0-9_]{0,63}\Z")


def _unavailable(version: str, code: str, message: str) -> dict[str, Any]:
    return {
        "ok": True,
        "version": version,
        "result": {
            "operation": "module_context",
            "available": False,
            "success": False,
            "complete": False,
            "read_only": True,
            "single_dispatch": None,
            "atomic_snapshot": False,
            "module": {"status": "unknown", "name": None, "number": None, "description": None},
            "context": {"before": None, "after": None, "unchanged": None},
            "scope": {
                "kind": "active_module_identity",
                "authorization_status": "unknown",
                "admin_database_creation_permission_verified": None,
                "paragon_write_permission_verified": None,
                "database_lock_status": "not_assessed",
            },
            "read_errors": [{"code": code, "message": message}],
        },
    }


def _bounded_text(value: Any, *, required: bool = False) -> bool:
    return (
        type(value) is str
        and len(value) <= MAX_TEXT_LENGTH
        and (not required or bool(value.strip()))
        and not any(ord(character) < 32 for character in value)
    )


def _status_identity(
    value: Any,
    keys: tuple[str, ...],
    *,
    statuses: set[str],
    required_when_present: tuple[str, ...] = (),
) -> bool:
    if (type(value) is not dict or type(value.get("status")) is not str
            or value["status"] not in statuses or not {"status", *keys} <= value.keys()):
        return False
    status = value["status"]
    for key in keys:
        item = value.get(key)
        if status in {"absent", "not_open"}:
            if item is not None:
                return False
        elif item is None:
            if status == "present" and key in required_when_present:
                return False
            continue
        elif key == "number" and type(item) is int:
            if not -(2**31) <= item <= 2**31 - 1:
                return False
            continue
        elif key == "element_is_null" and type(item) is bool:
            continue
        elif item is not None and not _bounded_text(item, required=key not in {"description", "user"}):
            return False
    return True


def _mdb_element_identity(value: Any, *, status: str) -> bool:
    if value is None:
        return status in {"absent", "unknown"}
    if type(value) is not dict or not {"refno", "type", "db_type", "db_type_name", "db_type_code"} <= value.keys():
        return False
    for key in ("refno", "type", "db_type", "db_type_name"):
        item = value.get(key)
        if item is None:
            if status == "present":
                return False
            continue
        if not _bounded_text(item, required=True):
            return False
    reference, element_type = value.get("refno"), value.get("type")
    if reference is not None and not _REFNO.fullmatch(reference):
        return False
    if element_type is not None and not _ELEMENT_TYPE.fullmatch(element_type):
        return False
    number = value.get("db_type_code")
    if number is None:
        return status != "present"
    return type(number) is int and -(2**31) <= number <= 2**31 - 1


def _observation_agreement(before: dict[str, Any], after: dict[str, Any]) -> bool | None:
    if before.get("session_id") != after.get("session_id") or before.get("bridge_build") != after.get("bridge_build"):
        return False
    for key, fields in (
        ("module", ("name", "number", "description")),
        ("project", ("name", "user", "project_number", "aduuid")),
        ("mdb", ("element",)),
        ("current_element", ("is_null", "refno", "type")),
    ):
        first, last = before.get(key), after.get(key)
        if type(first) is not dict or type(last) is not dict:
            return None
        first_status, last_status = first.get("status"), last.get("status")
        if first_status == "unknown" or last_status == "unknown":
            return None
        if first_status != last_status:
            return False
        if first_status in {"absent", "not_open"}:
            continue
        for field in fields:
            if first.get(field) is None or last.get(field) is None:
                return None
            if first[field] != last[field]:
                return False
    return True


def _native_fault(value: Any) -> bool:
    if type(value) is dict:
        error_code = value.get("error_code")
        if (value.get("has_fault") is True or value.get("native_fault") is True
                or value.get("native_fault_latched") is True
                or type(error_code) is str and error_code in {"native_fault", "native_database_fault"}):
            return True
        return any(_native_fault(item) for item in value.values())
    return type(value) is list and any(_native_fault(item) for item in value)


def _validate(reply: Any) -> None:
    if type(reply) is not dict or reply.get("ok") is not True or reply.get("version") != VERSION:
        raise ValueError("The module context response has an invalid E3D21 envelope.")
    if not _bounded_text(reply.get("session_id"), required=True) or not _GUID.fullmatch(reply["session_id"]):
        raise ValueError("The module context response has an invalid bridge session ID.")
    if not _bounded_text(reply.get("bridge_build"), required=True) or not _GUID.fullmatch(reply["bridge_build"]):
        raise ValueError("The module context response has an invalid bridge build ID.")
    result = reply.get("result")
    if type(result) is not dict or result.get("operation") != "module_context":
        raise ValueError("The module context result is missing or malformed.")
    for field, expected in (("available", True), ("read_only", True), ("single_dispatch", True), ("atomic_snapshot", False)):
        if result.get(field) is not expected:
            raise ValueError("The module context result does not satisfy its read-only observation contract.")
    if type(result.get("success")) is not bool or type(result.get("complete")) is not bool:
        raise ValueError("The module context result has invalid observation flags.")

    module = result.get("module")
    if not _status_identity(module, ("name", "number", "description"),
                            statuses={"present", "absent", "unknown"},
                            required_when_present=("name", "number")):
        raise ValueError("The module context has a malformed module identity.")
    if module.get("number") is not None and (
        type(module["number"]) is not int or not -(2**31) <= module["number"] <= 2**31 - 1
    ):
        raise ValueError("The module context has an invalid module number.")

    context = result.get("context")
    if type(context) is not dict or type(context.get("unchanged")) not in (bool, type(None)):
        raise ValueError("The module context boundary observations are missing.")
    if result["success"] is not (context.get("unchanged") is True):
        raise ValueError("The module context success flag disagrees with its boundary comparison.")
    before, after = context.get("before"), context.get("after")
    if type(before) is not dict or type(after) is not dict:
        raise ValueError("The module context is missing a before or after boundary.")
    for sample in (before, after):
        if sample.get("session_id") != reply["session_id"] or sample.get("bridge_build") != reply["bridge_build"]:
            raise ValueError("The module context boundary does not match the bridge session and build.")
        if not _status_identity(sample.get("module"), ("name", "number", "description"),
                                statuses={"present", "absent", "unknown"},
                                required_when_present=("name", "number")):
            raise ValueError("A module boundary has malformed module identity evidence.")
        sample_module = sample["module"]
        if sample_module.get("number") is not None and (
            type(sample_module["number"]) is not int
            or not -(2**31) <= sample_module["number"] <= 2**31 - 1
        ):
            raise ValueError("A module boundary has an invalid module number.")
        if not _status_identity(sample.get("project"), ("name", "user", "project_number", "aduuid"),
                                statuses={"present", "unknown", "not_open"},
                                required_when_present=("name", "project_number", "aduuid")):
            raise ValueError("A project boundary has malformed identity evidence.")
        mdb = sample.get("mdb")
        if (type(mdb) is not dict or type(mdb.get("status")) is not str
                or mdb["status"] not in {"present", "absent", "unknown"}
                or not {"status", "name", "element_is_null", "element"} <= mdb.keys()
                or not _status_identity(mdb, ("name",), statuses={"present", "absent", "unknown"})):
            raise ValueError("An MDB boundary has malformed identity evidence.")
        is_null = mdb.get("element_is_null")
        if is_null is not None and type(is_null) is not bool:
            raise ValueError("An MDB boundary has an invalid explicit null-state observation.")
        if mdb["status"] == "absent":
            if is_null is not True or mdb.get("name") is not None or mdb.get("element") is not None:
                raise ValueError("An absent MDB requires an explicit null element and no identity.")
        elif mdb["status"] == "present":
            if is_null is not False or mdb.get("element") is None:
                raise ValueError("A present MDB requires a non-null typed element identity.")
        elif is_null is True:
            raise ValueError("A null MDB element must be reported as absent.")
        if not _mdb_element_identity(mdb.get("element"), status=mdb["status"]):
            raise ValueError("An MDB boundary has malformed typed MDB-element identity evidence.")
        element = sample.get("current_element")
        if (type(element) is not dict or type(element.get("status")) is not str
                or element["status"] not in {"present", "absent", "unknown"}
                or not {"status", "is_null", "refno", "type"} <= element.keys()):
            raise ValueError("A typed current element boundary has malformed identity evidence.")
        is_null = element.get("is_null")
        if is_null is not None and type(is_null) is not bool:
            raise ValueError("A current element boundary has an invalid explicit null-state observation.")
        if element["status"] == "absent":
            if is_null is not True or element.get("refno") is not None or element.get("type") is not None:
                raise ValueError("An absent current element cannot have an identity.")
        else:
            if element["status"] == "present" and (
                is_null is not False or element.get("refno") is None or element.get("type") is None
            ):
                raise ValueError("A present current element must include its complete typed identity and non-null state.")
            if element["status"] == "unknown" and is_null is True:
                raise ValueError("A null current element must be reported as absent.")
            if element["status"] == "unknown" and is_null is None and (
                element.get("refno") is not None or element.get("type") is not None
            ):
                raise ValueError("An unknown current element cannot carry identity without a null-state read.")
            if (element.get("refno") is not None
                    and (type(element["refno"]) is not str or not _REFNO.fullmatch(element["refno"]))):
                raise ValueError("A current element boundary has an invalid native reference.")
            if (element.get("type") is not None
                    and (type(element["type"]) is not str or not _ELEMENT_TYPE.fullmatch(element["type"]))):
                raise ValueError("A current element boundary has an invalid native type.")

    expected_agreement = _observation_agreement(before, after)
    if context["unchanged"] is not expected_agreement:
        raise ValueError("The reported module context boundary comparison is inconsistent.")
    if result["module"] != after["module"]:
        raise ValueError("The top-level module identity does not match the after observation.")

    scope = result.get("scope")
    if (type(scope) is not dict or scope.get("kind") != "active_module_identity"
            or scope.get("authorization_status") != "unknown"
            or scope.get("admin_database_creation_permission_verified") is not None
            or scope.get("paragon_write_permission_verified") is not None
            or scope.get("database_lock_status") != "not_assessed"):
        raise ValueError("The module context scope must leave permissions unknown and database locking unassessed.")

    errors = result.get("read_errors")
    if type(errors) is not list or len(errors) > MAX_ERRORS:
        raise ValueError("The module context has invalid bounded read errors.")
    for error in errors:
        if (type(error) is not dict or not _bounded_text(error.get("stage"), required=True)
                or not _bounded_text(error.get("message"), required=True)
                or len(error["message"]) > MAX_ERROR_LENGTH):
            raise ValueError("The module context has a malformed bounded read error.")
    expected_success = expected_agreement is True and not errors
    if result["success"] is not expected_success:
        raise ValueError("The module context success flag disagrees with complete native identity evidence.")
    if result["complete"] is not expected_success:
        raise ValueError("The module context complete flag requires stable, error-free identity observations.")


def module_context(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    if type(args) is not dict or args:
        raise ValueError("e3d_module_context takes no arguments")
    version = bridge._selected_version(None)
    if version != VERSION or bridge.settings.mode != "bridge":
        return _unavailable(version, "native_unavailable", "Module context requires E3D21 bridge mode.")
    try:
        reply = bridge._request("GET", PATH, version=VERSION)
    except BridgeError as error:
        if _native_fault(error.diagnostics) or (
            error.http_status == 500 and "native fault" in str(error).casefold()
        ):
            raise
        if error.http_status == 404:
            return _unavailable(version, "native_feature_unavailable", "The running Addin does not expose module context.")
        raise
    if _native_fault(reply):
        raise BridgeError("A native database fault stopped module-context observation; do not retry the read.",
                          diagnostics=reply)
    try:
        _validate(reply)
    except ValueError as error:
        return _unavailable(version, "invalid_native_response", str(error))
    return reply


def register_module_context(server: McpServer, bridge: BridgeClient) -> None:
    server.add_tool(
        TOOL,
        "Read the active E3D21 module name, number and description in one UI-thread observation with before/after "
        "session, bridge build, project/user, optional MDB and typed current element evidence. A missing module, "
        "project, MDB or CE is reported separately from an unknown getter result. Module identity does not grant "
        "Admin database creation or PARAGON write permission; both remain unknown. This does not assess a database "
        "lock, initialize Admin, switch modules, access the model database or change CE. Requires E3D21 bridge mode.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        lambda args: module_context(bridge, args),
        read_only=True,
    )

"""Validate a read-only PARAGON element-create preview observation.

This is deliberately separate from catalogue schema/manifests. It reports the
SDK's MemberTypes and IsCreatable observations for an explicit parent target;
it never builds or runs PML/NEW and cannot create a database or element.
"""
from __future__ import annotations

import re
from typing import Any

from .bridge import BridgeClient, BridgeError
from .paragon_catalogue import _parameters as _manifest_parameters, _text
from .protocol import McpServer


TOOL = "e3d_paragon_element_create_preview"
PATH = "/api/v1/paragon/element-create-preview"
VERSION = "E3D21"
CANDIDATE_TYPES = ("CATA", "SECT", "CATE")
_DBREF = re.compile(r"=[1-9][0-9]{0,9}/(?:0|[1-9][0-9]{0,9})\Z")
_TYPE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")


def _parameters(args: dict[str, Any]) -> dict[str, str]:
    if not isinstance(args, dict) or set(args) != {"refno", "element_type"}:
        raise ValueError("An explicit refno and one supported element_type are required")
    validated = _manifest_parameters({"refno": args["refno"], "max_depth": 1, "limit": 1, "attributes": []})
    refno = validated["refno"]
    if refno.startswith("=") and not _DBREF.fullmatch(refno):
        raise ValueError("refno must identify an explicit /NAME or =database/element target")
    if any(char in refno for char in "*?"):
        raise ValueError("refno must be a literal target without wildcards")
    candidate_type = args["element_type"]
    if not isinstance(candidate_type, str) or candidate_type.strip().upper() not in CANDIDATE_TYPES:
        raise ValueError("element_type must be one of CATA, SECT or CATE")
    return {"refno": refno, "element_type": candidate_type.strip().upper()}


def _need(condition: bool, field: str) -> None:
    if not condition:
        raise ValueError("Invalid PARAGON element-create preview evidence: " + field)


def _errors(value: Any, allowed: set[str], field: str) -> dict[str, str]:
    _need(isinstance(value, dict) and value.keys() <= allowed
          and all(_text(message, nonempty=True) for message in value.values()), field)
    return value


def _validate(reply: Any, payload: dict[str, str]) -> None:
    _need(isinstance(reply, dict) and reply.get("ok") is True and reply.get("version") == VERSION
          and all(_text(reply.get(name), 256, nonempty=True) for name in ("session_id", "bridge_build")), "native envelope")
    result = reply.get("result")
    _need(isinstance(result, dict) and result.get("operation") == "paragon_element_create_preview"
          and result.get("read_only") is True and result.get("preview_only") is True
          and result.get("native_side_effects") is False and result.get("execution_supported") is False,
          "read-only preview contract")
    _need(result.get("requested_refno") == payload["refno"]
          and result.get("requested_type") == payload["element_type"], "explicit target and requested type echo")

    target = result.get("target")
    _need(isinstance(target, dict) and target.get("requested_refno") == payload["refno"]
          and set(target) == {"requested_refno", "resolved_refno", "resolved_name", "element_type", "database_type",
                              "database_type_code", "target_matches_request", "catalogue_target", "errors", "complete"},
          "target shape")
    target_errors = _errors(target["errors"], {"resolve", "refno", "name", "element_type", "database_type"}, "target read errors")
    if "resolve" in target_errors:
        _need(all(target[name] is None for name in ("resolved_refno", "resolved_name", "element_type", "database_type",
                                                    "database_type_code", "target_matches_request", "catalogue_target")),
              "unresolved explicit target")
    else:
        resolved = target["resolved_refno"]
        _need(isinstance(resolved, str) and bool(_DBREF.fullmatch(resolved)), "resolved native DBREF")
        for name, maximum in (("resolved_name", 4096), ("element_type", 64), ("database_type", 64)):
            _need(target[name] is None if name in target_errors else _text(target[name], maximum, nonempty=True), name)
        code = target["database_type_code"]
        _need(code is None if "database_type" in target_errors else type(code) is int and -1 <= code <= 2147483647,
              "target database type code")
        _need(target["target_matches_request"] is None if "refno" in target_errors or "name" in target_errors
              else type(target["target_matches_request"]) is bool, "explicit target verification")
        _need(target["catalogue_target"] is None if "database_type" in target_errors
              else type(target["catalogue_target"]) is bool, "target database scope")
        if type(target["catalogue_target"]) is bool:
            _need(target["catalogue_target"] is (target["database_type"] == "Catalogue" and code == 2),
                  "Catalogue database identity")
        if "refno" not in target_errors and "name" not in target_errors:
            if payload["refno"].startswith("="):
                identity_matches = resolved == payload["refno"]
            else:
                requested_name = payload["refno"][1:]
                actual_name = target["resolved_name"]
                identity_matches = requested_name.casefold() == (
                    actual_name[1:] if actual_name.startswith("/") else actual_name
                ).casefold()
            _need(target["target_matches_request"] is identity_matches, "resolved identity matches explicit request")
    _need(target["complete"] is (not target_errors and target["target_matches_request"] is True), "target completeness")

    row = result.get("candidate")
    _need(isinstance(row, dict) and set(row) == {"type", "member_type", "sdk_is_creatable_now", "errors", "complete"}
          and row["type"] == payload["element_type"], "candidate shape")
    candidate_errors = _errors(row["errors"], {"member_types", "is_creatable", "element_type", "target"}, "candidate read errors")
    for field, read_error in (("member_type", "member_types"), ("sdk_is_creatable_now", "is_creatable")):
        value = row[field]
        _need(value is None if read_error in candidate_errors or "target" in candidate_errors or "element_type" in candidate_errors
              else type(value) is bool, "unknown-preserving " + field)
    if target["complete"] is not True or target["catalogue_target"] is not True:
        _need(row["member_type"] is None and row["sdk_is_creatable_now"] is None and "target" in candidate_errors,
              "do not probe an unverified or non-Catalogue target")
    _need(row["complete"] is (row["member_type"] is not None
                               and row["sdk_is_creatable_now"] is not None and not candidate_errors), "candidate completeness")

    scope = result.get("scope")
    _need(scope == {"kind": "paragon_element_create_preview", "module": None, "permissions_verified": None,
                    "revision": None, "whole_project_coverage": False,
                    "pml_executed": False, "element_creation_attempted": False, "element_created": False},
          "unknown module/permission and non-execution scope")
    complete = target["complete"] and target["catalogue_target"] is True and row["complete"]
    _need(result.get("complete") is complete and result.get("success") is complete, "read completeness")
    _need(result.get("read_errors") == {
        "target": target_errors,
        "candidate": candidate_errors,
    }, "read error summary")


def paragon_element_create_preview(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    payload = _parameters(args)
    try:
        reply = bridge._request("POST", PATH, payload, version=VERSION)
        _validate(reply, payload)
        return reply
    except BridgeError:
        raise
    except ValueError as error:
        raise BridgeError("E3D bridge returned invalid PARAGON element-create preview evidence: " + str(error),
                          response_invalid=True) from error


def register_paragon_element_create_preview(server: McpServer, bridge: BridgeClient) -> None:
    server.add_tool(
        TOOL,
        "Read SDK MemberTypes and IsCreatable observations for CATA, SECT and CATE under one explicit Catalogue target. This is only a preview; it does not execute PML/NEW or verify module permissions.",
        {"type": "object", "properties": {
            "refno": {"type": "string", "description": "Explicit Catalogue target /NAME or =database/element; implicit CE and wildcards are not accepted."},
            "element_type": {"type": "string", "enum": list(CANDIDATE_TYPES),
                             "x-normalize": "trim_casefold",
                             "description": "One proposed child type to inspect; no type is created."},
        }, "required": ["refno", "element_type"], "additionalProperties": False},
        lambda args: paragon_element_create_preview(bridge, args), read_only=True,
    )

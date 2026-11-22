"""Compose one bounded PARAGON SCOM-copy draft from raw read-only evidence.

No preview or execution request is submitted. Native actual-type and bounded
numeric PARAM observations inform the draft; copy semantics and the full set of
parameters remain unverified. Existing Catalogue databases and user
configuration are prerequisites.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Callable

from .bridge import BridgeClient, BridgeError
from .module_context import PATH as MODULE_PATH, _validate as _validate_module
from .paragon_component_template import PATH as TEMPLATE_PATH, _validate as _validate_template
from .paragon_schema import PATH as SCHEMA_PATH, _validate as _validate_schema
from .piping_specification_read import PATH_CANDIDATE as SPEC_PATH, _validate as _validate_spec
from .protocol import McpServer


TOOL = "e3d_paragon_generation_plan"
VERSION = "E3D21"
RECIPE = "same_category_scom_copy_v1"
REFERENCE_ATTRIBUTES = ["PTREF", "GMREF", "DTREF", "BLRFA"]
REQUIRED_FEATURES = ("module_context", "piping_specification_read", "paragon_component_template",
                     "paragon_catalogue_schema", "pml_script_execution")
_REF = re.compile(r"=([1-9][0-9]{0,9})/(0|[1-9][0-9]{0,9})")
_NAME = re.compile(r"/[A-Za-z][A-Za-z0-9_/-]*")
_GUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
_IDENTITY = ("refno", "type", "db_type", "db_type_code", "identity_key")


def _parameters(args: Any) -> dict[str, str]:
    if type(args) is not dict or args.keys() - {"source_refno", "parent_refno", "name", "recipe"}:
        raise ValueError("Only source_refno, parent_refno, name and recipe are accepted")
    result = {}
    for field in ("source_refno", "parent_refno"):
        value = args.get(field)
        match = _REF.fullmatch(value) if type(value) is str else None
        if match is None or any(int(part) > 2147483647 for part in match.groups()):
            raise ValueError(field + " must be a canonical numeric Int32 =database/element reference")
        result[field] = value
    name = args.get("name")
    if (type(name) is not str or not 2 <= len(name) <= 128 or not _NAME.fullmatch(name)
            or "//" in name or name.endswith("/")):
        raise ValueError("name must be an ASCII /NAME of at most 128 characters, with a letter first and letters, digits, underscore, hyphen or single slashes only")
    if args.get("recipe", RECIPE) != RECIPE:
        raise ValueError("recipe must be " + RECIPE)
    return {**result, "name": name, "recipe": RECIPE}


def _fault(value: Any) -> bool:
    pending, seen = [value], set()
    while pending:
        item = pending.pop()
        if not isinstance(item, (dict, list, tuple)) or id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, dict):
            if (any(item.get(k) is True for k in ("has_fault", "native_fault", "native_database_fault", "native_fault_latched"))
                    or any(item.get(k) in ("native_fault", "native_database_fault") for k in ("code", "error_code"))):
                return True
            pending.extend(item.values())
        else:
            pending.extend(item)
    return False


def _require(condition: bool, field: str) -> None:
    if not condition:
        raise ValueError("Invalid PARAGON generation-plan evidence: " + field)


def _spec_request(refno: str, kind: str, *, source: bool) -> dict[str, Any]:
    return {"refno": refno, "expected_type": kind, "database_type": "Catalogue", "max_depth": 1 if source else 16,
            "limit": 1 if source else 100, "attributes": list(REFERENCE_ATTRIBUTES) if source else []}


def _draft(request: dict[str, str]) -> dict[str, Any]:
    source = f"{request['parent_refno']}\nNEW SCOM {request['name']} AFTER LAST MEMBER COPY {request['source_refno']}\n"
    encoded = source.encode("utf-8")
    return {"source": source, "source_sha256": hashlib.sha256(encoded).hexdigest(), "byte_count": len(encoded),
            "line_count": 2, "line_count_scope": "Two PML statements, with a terminal LF; native script line_count counts the final empty line separately.",
            "runtime_syntax_verified": False,
            "grammar_sources": ["paragon/objects/catstructuraltranslate.pmlobj:110,117",
                                "paragon/objects/catmodelviews.pmlobj:4550,4569"],
            "scope": "Review draft assembled from installed patterns; this exact complete script has not been run."}


def _readback_contract(request: dict[str, str]) -> dict[str, Any]:
    return {
        "performed": False, "automatic_readback": False, "created_by_request_verified": False,
        "parent_request": {"tool": "e3d_piping_specification_read", "arguments":
                           _spec_request(request["parent_refno"], "CATE", source=False)},
        "source_request": {"tool": "e3d_piping_specification_read", "arguments":
                           _spec_request(request["source_refno"], "SCOM", source=True)},
        "source_template_request": {"tool": "e3d_paragon_component_template", "arguments":
                                    {"refno": request["source_refno"]}},
        "new_element_template_request": {"tool": "e3d_paragon_component_template", "request_ready": False,
                                         "required_argument": "refno",
                                         "refno_source": "independently_captured_new_numeric_ref"},
        "new_element": {"refno": None, "name": request["name"], "base_type": "SCOM",
                        "database_type": "Catalogue", "database_type_code": 2,
                        "owner_refno": request["parent_refno"], "owner_base_type": "CATE"},
        "checks": ["Independently match session/build/project/MDB/typed CE before interpreting new reads.",
                   "Require a complete bounded parent traversal and exactly one direct child with the requested NAME; otherwise retain unknown.",
                   "Capture that child's new numeric REF and verify Catalogue/SCOM and its typed CATE owner; never infer the REF from NAME or the source.",
                   "Read the new REF explicitly with PTREF/GMREF/DTREF/BLRFA; compare available typed reference identities against the preserved source observations.",
                   "Read the verified new numeric REF with e3d_paragon_component_template and separately read the source again; compare observed base/actual type, finite numeric PARAM values and their indices/order, and applicable observed reference identities. Missing, truncated or unknown fields cannot prove equality.",
                   "Read the source again and compare only fields actually observed before; do not infer all-source immutability.",
                   "Review the original request journal and source_sha256 separately; native Run success is not a model postcondition."],
        "unverified_requirements": ["post-copy actual type identity and numeric PARAM equivalence", "word parameters and complete parameter-copy equivalence",
                                    "complete geometry/Ppoint definitions and reference semantics", "units and engineering suitability",
                                    "operation-specific authorization and database locking", "durable SAVEWORK/persistence", "causal attribution of a new element to this request"],
        "failure_policy": "A failed NAME/COPY can leave partial effects. Do not automatically delete, restore CE, replay or issue a new request ID.",
    }


def _new_result(request: dict[str, str]) -> dict[str, Any]:
    return {"operation": "paragon_generation_plan", "schema_version": 1, "recipe": RECIPE, "request": request,
            "status": "unknown", "available": False, "success": False, "complete": False,
            "plan_only": True, "read_only": True, "atomic_snapshot": False, "writes_attempted": False,
            "draft": _draft(request), "preview_request": None, "execution_request": None,
            "execution_supported": False, "execution_ready": False, "execution_binding_enforced": False,
            "revision": None, "read_coverage_complete": False, "required_checks": {}, "evidence": {}, "issues": [],
            "binding": None, "readback_contract": _readback_contract(request),
            "scope": {"kind": "one_same_category_scom_copy_draft", "maximum_copy_operations": 1,
                      "existing_catalogue_database_required": True, "admin_configuration_owned_by_user": True,
                      "admin_database_creation_attempted": False, "category_creation_attempted": False,
                      "module_identity_is_authorization": False, "permissions_verified": None,
                      "source_actual_type_observed": False, "source_actual_type_verified": False,
                      "source_numeric_param_values_observed": False, "source_numeric_param_coverage_complete": False,
                      "source_parameter_values_verified": False, "whole_clone_fingerprint": False,
                      "runtime_copy_semantics_verified": False, "runtime_syntax_verified": False,
                      "geometry_generated": False, "geometry_verified": False, "reference_semantics_verified": False,
                      "units_verified": False, "engineering_validated": False, "whole_project_coverage": False,
                      "name_conflict_scope": "observed_current_parent_children_only", "name_global_uniqueness_verified": False}}


class _Stop(Exception):
    pass


def _identity(value: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(value[k] for k in _IDENTITY)


def _module_anchor(reply: dict[str, Any]) -> dict[str, Any]:
    context = reply["result"]["context"]
    first, last = context["before"], context["after"]
    _require(context["unchanged"] is True and not reply["result"]["read_errors"], "stable module identity boundaries")
    for sample in (first, last):
        _require(sample["project"]["status"] == "present" and sample["mdb"]["status"] == "present"
                 and sample["current_element"]["status"] == "present", "present module project/MDB/typed CE")
    _require(first["mdb"]["name"] == last["mdb"]["name"], "module MDB name agreement")
    return {"project": first["project"]["name"], "mdb": first["mdb"]["name"],
            "ce_refno": first["current_element"]["refno"], "ce_type": first["current_element"]["type"]}


def _boundaries(reply: dict[str, Any], anchor: dict[str, Any], ce: tuple[Any, ...] | None) -> tuple[Any, ...]:
    context = reply["result"]["context"]
    _require(context["boundary_agreement"] is True and context["same_project_instance"] is True
             and context["same_mdb_instance"] is True, "endpoint native instance/boundary agreement")
    for sample in (context["before"], context["after"]):
        current = sample["current_element"]
        _require(not sample["errors"] and sample["project"] == anchor["project"] and sample["mdb"] == anchor["mdb"]
                 and current["refno"] == anchor["ce_refno"] and current["type"] == anchor["ce_type"],
                 "cross-endpoint project/MDB/typed CE")
        observed = _identity(current)
        _require(ce is None or observed == ce, "cross-reader full typed CE identity")
        ce = observed
    return ce


def _template_observations(reply: dict[str, Any], root: dict[str, Any], requested: str,
                           source_leaf: bool | None) -> tuple[dict[str, bool | None], dict[str, bool]]:
    """Project required slices only after the full raw template is validated.

    A NAME failure does not erase a separately observed numeric identity, type,
    leaf or PARAM prefix. A known NAME drift still prevents sufficient coverage.
    Optional one-hop references do not gate the recipe's required slices.
    """
    native = reply["result"]
    target, parameters, leaf = native["target"], native["parameters"], native["leaf"]
    identity_ok = name_agreement = actual_scom = None
    actual_observed = False
    if target is not None:
        binding = target["request_binding"]
        identity_ok = (_identity(target) == _identity(root)
                       and target["refno"] == requested and target["type"] == "SCOM"
                       and target["db_type"] == "Catalogue" and target["db_type_code"] == 2
                       and binding["verified"] is True and binding["requested_refno"] == requested
                       and binding["resolved_refno"] == requested and "request_binding" not in target["errors"])
        if target["name_error"] is None and root["name_error"] is None:
            name_agreement = target["name"] == root["name"]
        base, actual = target["base_type"], target["actual_type"]
        base_short = base["short_name"] if base is not None else None
        actual_short = actual["short_name"] if actual is not None else None
        actual_observed = actual_short is not None
        # A positively observed non-SCOM short name is a known mismatch even
        # when a different type getter failed. Never infer actual from base.
        if base_short not in (None, "SCOM") or actual_short not in (None, "SCOM"):
            actual_scom = False
        elif base_short == actual_short == "SCOM":
            actual_scom = True
    leaf_agreement = (leaf["is_leaf"] is source_leaf
                      if type(source_leaf) is bool and type(leaf["is_leaf"]) is bool else None)
    values = parameters["values"]
    numeric_shape = (parameters["supported"] is True and parameters["sdk_type"] == "DOUBLE"
                     and parameters["is_array"] is True and parameters["shape"] == "double_array")
    numeric_observed = numeric_shape and (parameters["total_count"] == 0
                        or any(item["status"] == "observed" for item in values))
    # The whole receipt was strictly validated before this projection. Units
    # are outside this recipe's numeric-value slice, so an isolated unit_kind
    # getter failure must not erase a fully observed DOUBLE array. Every
    # other attribute/metadata/value/budget error still prevents coverage.
    numeric_complete = (numeric_shape and parameters["errors"].keys() <= {"unit_kind"}
                        and parameters["truncated"] is False and parameters["inspection_limited"] is False
                        and parameters["native_array_materialized"] is True
                        and type(parameters["total_count"]) is int
                        and parameters["total_count"] == parameters["returned_count"] == len(values)
                        and all(item["index"] == index and item["status"] == "observed" and item["error"] is None
                                and type(item["value"]) in (int, float) and math.isfinite(item["value"])
                                for index, item in enumerate(values)))
    return ({"source_template_numeric_identity_verified": identity_ok,
             "source_template_name_agreement": name_agreement,
             "source_actual_type_scom": actual_scom,
             "source_template_leaf_agreement": leaf_agreement,
             "source_numeric_param_coverage_complete": True if numeric_complete else None},
            {"source_actual_type_observed": actual_observed and identity_ok is True,
             "source_actual_type_verified": actual_scom is True and identity_ok is True,
             "source_numeric_param_values_observed": numeric_observed and identity_ok is True,
             "source_numeric_param_coverage_complete": numeric_complete and identity_ok is True})


def paragon_generation_plan(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    request = _parameters(args)
    result = _new_result(request)
    version = bridge._selected_version(None)
    if bridge.settings.mode != "bridge" or version != VERSION:
        result["status"] = "unavailable"
        result["issues"].append({"stage": "mode", "code": "native_unavailable"})
        return {"ok": True, "version": version, "result": result}
    session = build = None

    def issue(stage: str, code: str, message: str | None = None) -> None:
        row = {"stage": stage, "code": code}
        if message is not None:
            row["message"] = message
        result["issues"].append(row)

    def read(stage: str, operation: Callable[[], Any], validate: Callable[[Any], None] | None = None) -> Any:
        try:
            value = operation()
        except BridgeError as error:
            if _fault(error.diagnostics):
                raise
            result["evidence"][stage] = {"bridge_error": {"message": str(error), "http_status": error.http_status,
                "transport_error": error.transport_error, "response_invalid": error.response_invalid,
                "diagnostics": error.diagnostics}}
            if error.http_status == 404:
                result["status"] = "unavailable"
            issue(stage, "native_feature_unavailable" if error.http_status == 404 else "bridge_read_error")
            raise _Stop from error
        if _fault(value):
            raise BridgeError("A native database fault stopped PARAGON planning; do not retry.", diagnostics=value)
        result["evidence"][stage] = value
        try:
            _require(type(value) is dict and value.get("ok") is True and value.get("version") == VERSION, "native version/envelope")
            if stage != "health":
                _require(value.get("session_id") == session and value.get("bridge_build") == build,
                         "independent health session/build")
            if validate is not None:
                validate(value)
        except (ValueError, KeyError, TypeError) as error:
            issue(stage, "invalid_or_unbound_native_evidence", str(error))
            raise _Stop from error
        return value

    def check(name: str, value: bool | None) -> None:
        result["required_checks"][name] = value

    def block(stage: str, code: str) -> None:
        result["status"] = "blocked"
        issue(stage, code)
        raise _Stop

    try:
        health = read("health", lambda: bridge.health(version=VERSION))
        session, build = health.get("session_id"), health.get("bridge_build")
        state = health.get("state")
        _require(type(session) is str and bool(_GUID.fullmatch(session)) and type(build) is str and bool(_GUID.fullmatch(build)),
                 "independent canonical health session/build")
        _require(type(state) is dict and health.get("connected") is True and state.get("database_ready") is True
                 and state.get("busy") is False and state.get("recovery_required") is False
                 and type(state.get("native_database_fault")) is dict and state["native_database_fault"].get("has_fault") is False
                 and type(state.get("generated_csg_quarantine")) is dict and state["generated_csg_quarantine"].get("active") is False
                 and health.get("diagnostic_error") is None, "explicit native readiness without fault/quarantine")
        features = health.get("api_features")
        _require(type(features) is list and all(type(item) is str for item in features), "health feature observations")
        if any(feature not in features for feature in REQUIRED_FEATURES):
            result["status"] = "unavailable"
            issue("health", "native_feature_unavailable")
            raise _Stop
        result["available"] = True
        module = read("module_context", lambda: bridge._request("GET", MODULE_PATH, version=VERSION), _validate_module)
        observed_module = module["result"]["module"]
        module_ok = (observed_module["status"] == "present" and observed_module["number"] == 81
                     and observed_module["name"] == "PARAGON")
        check("paragon_module_identity", module_ok if observed_module["status"] == "present" else None)
        if result["required_checks"]["paragon_module_identity"] is False:
            block("module_context", "not_paragon_module_81")
        anchor = _module_anchor(module)
        result["binding"] = {"version": VERSION, "session_id": session, "bridge_build": build,
                             **anchor, "module_number": 81, "module_name": "PARAGON",
                             "module_ce_comparison_fields": ["refno", "type"],
                             "reader_ce_comparison_fields": list(_IDENTITY),
                             "project_mdb_instances_across_requests_verified": False,
                             "scope": "Read-time observations only; no execution guard, lock or cross-request atomicity."}
        source_request = _spec_request(request["source_refno"], "SCOM", source=True)
        source = read("source", lambda: bridge._request("POST", SPEC_PATH, source_request, version=VERSION),
                      lambda value: _validate_spec(value, source_request))
        ce = _boundaries(source, anchor, None)
        source_result, root = source["result"], source["result"]["rows"][0]
        owner = root["owner"]
        same_parent = (None if root["owner_error"] is not None else owner["refno"] == request["parent_refno"]
                       and owner["type"] == "CATE" and owner["db_type"] == "Catalogue" and owner["db_type_code"] == 2)
        check("source_same_catalogue_cate_parent", same_parent)
        check("source_leaf", source_result["traversal_complete"] if not source_result["read_errors"] else None)
        if same_parent is False:
            block("source", "source_owner_is_not_explicit_cate_parent")
        if result["required_checks"]["source_leaf"] is False:
            block("source", "source_not_a_verified_leaf")
        template_request = {"refno": request["source_refno"]}
        template = read("source_template", lambda: bridge._request("POST", TEMPLATE_PATH, template_request, version=VERSION),
                        lambda value: _validate_template(value, template_request))
        ce = _boundaries(template, anchor, ce)
        template_checks, template_scope = _template_observations(template, root, request["source_refno"],
                                                               result["required_checks"]["source_leaf"])
        result["required_checks"].update(template_checks)
        result["scope"].update(template_scope)
        if template_checks["source_actual_type_scom"] is False:
            block("source_template", "source_actual_or_base_type_is_not_scom")
        target_request = _spec_request(request["parent_refno"], "CATE", source=False)
        target = read("target", lambda: bridge._request("POST", SPEC_PATH, target_request, version=VERSION),
                      lambda value: _validate_spec(value, target_request))
        ce = _boundaries(target, anchor, ce)
        target_result = target["result"]
        children = [row for row in target_result["rows"] if row["depth"] == 1]
        prefix_complete = target_result["traversal_complete"] is True
        check("target_prefix_complete", prefix_complete)
        names_known = all(row["name_error"] is None for row in children)
        collision = any(row["name"] is not None and row["name"].casefold() == request["name"].casefold() for row in children)
        check("name_clear_in_parent", False if collision else True if prefix_complete and names_known else None)
        source_rows = [row for row in children if _identity(row) == _identity(root)]
        check("source_listed_under_parent", len(source_rows) == 1 if prefix_complete else None)
        source_name_stable = source_leaf_in_parent = None
        if len(source_rows) == 1:
            source_row = source_rows[0]
            name_rows = (root, template["result"]["target"], source_row)
            known_names = [row["name"] for row in name_rows if row is not None and row["name_error"] is None]
            source_name_stable = (False if len(set(known_names)) > 1
                                  else True if len(known_names) == len(name_rows) else None)
            observed_source_child = any(row["parent_index"] == source_row["id"] for row in target_result["rows"])
            source_leaf_in_parent = False if observed_source_child else True if prefix_complete else None
        check("source_name_stable_in_parent", source_name_stable)
        check("source_leaf_in_parent", source_leaf_in_parent)
        if collision:
            block("target", "name_already_observed_under_parent")
        schema_request = {"refno": request["parent_refno"], "attributes": [], "include_constraints": False}
        schema = read("parent_schema", lambda: bridge._request("POST", SCHEMA_PATH, schema_request, version=VERSION),
                      lambda value: _validate_schema(value, schema_request))
        _boundaries(schema, anchor, ce)
        schema_result = schema["result"]
        check("schema_target_is_cate", schema_result["target"]["type"] == "CATE")
        if result["required_checks"]["schema_target_is_cate"] is False:
            block("parent_schema", "schema_target_is_not_cate")
        members = schema_result["child_types"]
        choices = [row for row in members if row["valid"] is True and row["short_name"] == "SCOM"]
        member_coverage = (schema_result["child_types_observation"]["truncated"] is False
                           and "member_types" not in schema_result["read_errors"]
                           and all(row["valid"] is True and row["short_name"] is not None for row in members))
        check("scom_member_type_observed", True if len(choices) == 1 else False if not choices and member_coverage else None)
        selected = choices[0] if len(choices) == 1 else {}
        check("scom_catalogue_supported", selected.get("catalogue_supported"))
        check("scom_sdk_creatable_now", selected.get("sdk_creatable_now"))
        if any(result["required_checks"][key] is False for key in (
                "scom_member_type_observed", "scom_catalogue_supported", "scom_sdk_creatable_now")):
            block("parent_schema", "scom_sdk_candidate_observation_false")
        check("cross_endpoint_context_verified", True)
        result["read_coverage_complete"] = all(value is True for value in result["required_checks"].values())
        if result["read_coverage_complete"]:
            result["status"] = "reviewable"
            result["preview_request"] = {"tool": "e3d_execute_pml_script", "arguments": {
                "source": result["draft"]["source"], "dry_run": True, "expected_session_id": session},
                "preview_scope": "Content/budget and native encoding only when separately submitted; not PML semantics, authorization or model validation."}
        else:
            issue("coverage", "required_observation_unknown")
    except (ValueError, KeyError, TypeError) as error:
        issue("context", "invalid_or_unbound_native_evidence", str(error))
    except _Stop:
        pass
    result["success"] = result["status"] == "reviewable"
    if result["status"] == "unavailable":
        result["available"] = False
    result["complete"] = result["read_coverage_complete"]
    result["plan_fingerprint"] = hashlib.sha256(json.dumps(
        {"request": request, "draft_sha256": result["draft"]["source_sha256"], "binding": result["binding"],
         "required_checks": result["required_checks"]}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    result["plan_fingerprint_scope"] = ("Advisory hash of request, draft hash, observed context and required-check values only; "
                                        "full raw receipts are not hashed. Never a revision, permission, journal receipt or execution token.")
    return {"ok": True, "version": VERSION, "result": result}


def register_paragon_generation_plan(server: McpServer, bridge: BridgeClient) -> None:
    server.add_tool(
        TOOL,
        "Compose one review draft for copying an existing SCOM leaf within the same explicit Catalogue CATE. "
        "The user configures the existing Catalogue database through ADMIN. Reads health, PARAGON module 81 identity, "
        "source specification, component-template actual type and bounded numeric PARAM evidence, parent specification "
        "and parent schema; retains all six raw receipts. Requires observed native base and actual SCOM identity and "
        "complete finite numeric PARAM coverage for a two-line PML "
        "draft and an unsubmitted dry_run=true script request only with sufficient observations. No PML preview or "
        "execution is submitted. Module 81 is identity, not permission. Word parameters, complete clone identity, "
        "runtime grammar/copy semantics, geometry/reference equivalence and units remain unverified; execution_ready "
        "is always false. Does not "
        "create a DB/CATE, select CE, deploy, save, claim, change parameters or automatically retry.",
        {"type": "object", "properties": {
            "source_refno": {"type": "string", "pattern": r"^=[1-9][0-9]{0,9}/(?:0|[1-9][0-9]{0,9})$",
                             "maxLength": 22, "description": "Canonical numeric source SCOM leaf; Int32 pairs only."},
            "parent_refno": {"type": "string", "pattern": r"^=[1-9][0-9]{0,9}/(?:0|[1-9][0-9]{0,9})$",
                             "maxLength": 22, "description": "Explicit numeric Catalogue CATE; must be the source's actual typed owner."},
            "name": {"type": "string", "minLength": 2, "maxLength": 128, "pattern": r"^/[A-Za-z][A-Za-z0-9_/-]*$",
                     "description": "ASCII /NAME; no double or terminal slash, whitespace, dot, quotes, commands or wildcards."},
            "recipe": {"type": "string", "const": RECIPE, "default": RECIPE},
        }, "required": ["source_refno", "parent_refno", "name"], "additionalProperties": False},
        lambda args: paragon_generation_plan(bridge, args), read_only=True,
    )

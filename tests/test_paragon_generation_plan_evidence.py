"""Independent wire-evidence tests for the public PARAGON draft-only plan.

Only urllib's wire transport is replaced. Public MCP registration, request
validation, BridgeClient decoding, and all native-evidence validators execute
production code. No native bridge, subprocess, preview or PML is permitted.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch
import urllib.error
import urllib.parse

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.module_context import _validate as validate_module
from aveva_e3d_mcp.paragon_component_template import _validate as validate_template
from aveva_e3d_mcp.paragon_schema import _validate as validate_schema
from aveva_e3d_mcp.piping_specification_read import _validate as validate_specification
from aveva_e3d_mcp.tools import create_server


TOOL = "e3d_paragon_generation_plan"
RECIPE = "same_category_scom_copy_v1"
SESSION = "11111111-1111-4111-8111-111111111111"
BUILD = "22222222-2222-4222-8222-222222222222"
PROJECT, MDB, USER = "PLANT", "CATALOGUE-MDB", "ADMIN"
PARENT, SOURCE, OTHER = "=7/100", "=7/101", "=7/102"
ATTRIBUTES = ["PTREF", "GMREF", "DTREF", "BLRFA"]
ARGS = {"source_refno": SOURCE, "parent_refno": PARENT, "name": "/NEW_SCOM"}
MODULE_PATH = "/api/v1/module/context"
SPEC_PATH = "/api/v1/piping/specification-read"
SCHEMA_PATH = "/api/v1/paragon/catalogue-schema"
TEMPLATE_PATH = "/api/v1/paragon/component-template"
SOURCE_PAYLOAD = {"refno": SOURCE, "expected_type": "SCOM", "database_type": "Catalogue", "max_depth": 1,
                  "limit": 1, "attributes": ATTRIBUTES}
PARENT_PAYLOAD = {"refno": PARENT, "expected_type": "CATE", "database_type": "Catalogue", "max_depth": 16,
                  "limit": 100, "attributes": []}
SCHEMA_PAYLOAD = {"refno": PARENT, "attributes": [], "include_constraints": False}
EXPECTED_WIRE = [("GET", "/health", None), ("GET", MODULE_PATH, None),
                 ("POST", SPEC_PATH, SOURCE_PAYLOAD), ("POST", TEMPLATE_PATH, {"refno": SOURCE}),
                 ("POST", SPEC_PATH, PARENT_PAYLOAD),
                 ("POST", SCHEMA_PATH, SCHEMA_PAYLOAD)]
MISSING = object()


def identity(reference=PARENT, kind="CATE", database="Catalogue", code=2):
    return {"refno": reference, "type": kind, "identity_key": f"{reference}|{kind}",
            "db_type": database, "db_type_code": code}


def envelope(result):
    return {"ok": True, "version": "E3D21", "session_id": SESSION, "bridge_build": BUILD, "result": result}


def health():
    return {"ok": True, "version": "E3D21", "session_id": SESSION, "bridge_build": BUILD, "connected": True,
            "diagnostic_error": None, "api_features": ["module_context", "piping_specification_read", "paragon_component_template", "paragon_catalogue_schema", "pml_script_execution"],
            "state": {"database_ready": True, "busy": False, "recovery_required": False,
                      "native_database_fault": {"has_fault": False}, "generated_csg_quarantine": {"active": False},
                      "execution": {"dispatcher_ready": True, "native_operation_active": False},
                      "native_dialogs": {"complete": True, "standard_dialog_present": False},
                      "pml_alerts": {"complete": True, "alert_present": False}}}


def module_observation():
    before = {"session_id": SESSION, "bridge_build": BUILD,
        "module": {"status": "present", "name": "PARAGON", "number": 81, "description": "Catalogue module"},
        "project": {"status": "present", "name": PROJECT, "user": USER, "project_number": "7", "aduuid": "project-ad-7"},
        "mdb": {"status": "present", "name": MDB, "element_is_null": False,
                "element": {"refno": "=5/1", "type": "MDB", "db_type": "System", "db_type_name": "System", "db_type_code": 10}},
        "current_element": {"status": "present", "is_null": False, "refno": PARENT, "type": "CATE"}}
    return envelope({"operation": "module_context", "available": True, "success": True, "complete": True,
        "read_only": True, "single_dispatch": True, "atomic_snapshot": False, "module": deepcopy(before["module"]),
        "context": {"before": before, "after": deepcopy(before), "unchanged": True}, "read_errors": [],
        "scope": {"kind": "active_module_identity", "authorization_status": "unknown",
                  "admin_database_creation_permission_verified": None, "paragon_write_permission_verified": None,
                  "database_lock_status": "not_assessed"}})


def boundary():
    return {"session_id": SESSION, "bridge_build": BUILD, "project": PROJECT, "mdb": MDB,
            "current_element": identity(), "errors": {}}


def observation(reference, kind, name):
    return {**identity(reference, kind), "name": name, "name_error": None,
            "database": {"status": "observed", "name": "CATADMIN/COMPONENTS", "number": 650,
                         "type": "Catalogue", "type_code": 2, "errors": {}}}


def attribute(name, index):
    kind = {"PTREF": "PTSE", "GMREF": "GMSE", "DTREF": "DTSE", "BLRFA": "BTSE"}[name]
    value = observation(f"=7/{200 + index}", kind, "/SHARED_" + kind)
    return {"requested_name": name, "status": "observed", "supported": True, "sdk_type": "ELEMENT", "is_array": False,
            "unit_kind": "NONE", "unit": None, "unit_status": "unknown", "unit_reason": "no_explicit_unit_conversion",
            "formatted_value": value["name"], "formatted_status": "observed", "typed_value": value,
            "typed_status": "observed", "reference_null": False, "errors": {}}


def specification(*, source=False):
    payload = SOURCE_PAYLOAD if source else PARENT_PAYLOAD
    if source:
        rows = [{**observation(SOURCE, "SCOM", "/TEMPLATE"), "id": 0, "depth": 0, "parent_index": None,
                 "sibling_index": None, "owner": identity(), "owner_error": None,
                 "attributes": {name: attribute(name, index) for index, name in enumerate(ATTRIBUTES)}, "complete": True}]
    else:
        rows = [{**observation(PARENT, "CATE", "/CATEGORY"), "id": 0, "depth": 0, "parent_index": None,
                 "sibling_index": None, "owner": identity("=7/90", "SECT"), "owner_error": None,
                 "attributes": {}, "complete": True}]
        for index, (refno, name) in enumerate(((SOURCE, "/TEMPLATE"), (OTHER, "/OTHER")), 1):
            rows.append({**observation(refno, "SCOM", name), "id": index, "depth": 1, "parent_index": 0,
                         "sibling_index": index, "owner": identity(), "owner_error": None, "attributes": {}, "complete": True})
    return envelope({"operation": "piping_specification_read", "available": True, "success": True, "complete": True,
        "read_only": True, "single_dispatch": True, "atomic_snapshot": False, "requested_refno": payload["refno"],
        **{key: deepcopy(payload[key]) for key in ("expected_type", "database_type", "max_depth", "limit", "attributes")},
        "database_type_code": 2, "rows": rows, "returned_count": len(rows), "order": "database_preorder",
        "traversal_complete": True, "truncated": False, "truncation_reasons": [], "read_errors": [],
        "context": {"before": boundary(), "after": boundary(), "same_project_instance": True,
                    "same_mdb_instance": True, "boundary_agreement": True},
        "scope": {"kind": "explicit_typed_subtree_and_reference_identities", "reference_hops": 1,
                  "reference_targets_expanded": False, "whole_project_coverage": False, "units_verified": False,
                  "engineering_compatible": None, "material_selected": None, "database_access_authorized": None},
        "completeness_scope": "Requested raw/typed observations and bounded ownership traversal only; units and engineering suitability remain unverified."})


def schema_observation():
    child = {"sdk_index": 0, "valid": True, "name": "SCOMPONENT", "short_name": "SCOM", "database_type_codes": [2],
             "database_types_total_count": 1, "database_types_truncated": False, "catalogue_supported": True,
             "sdk_creatable_now": True, "errors": {}, "complete": True}
    return envelope({"operation": "paragon_catalogue_schema", "available": True, "success": True, "complete": True,
        "read_only": True, "single_dispatch": True, "atomic_snapshot": False, "requested_refno": PARENT,
        "requested_attributes": [], "include_constraints": False, "target": {**identity(), "name": "/CATEGORY", "name_error": None},
        "child_types": [child], "child_types_observation": {"limit": 200, "returned_count": 1, "total_count": 1,
            "truncated": False, "complete": True, "order": "sdk_member_types", "native_arrays_materialized_before_output_limits": True},
        "attributes": [], "attributes_complete": True, "constraints_complete": None, "read_errors": {},
        "context": {"before": boundary(), "after": boundary(), "same_project_instance": True,
                    "same_mdb_instance": True, "boundary_agreement": True},
        "output_limits": {"child_types": 200, "database_types_per_child": 32, "attributes": 10,
            "values_or_boundaries_per_attribute": 100, "constraint_text_utf16_characters": 1024,
            "native_arrays_materialized_before_output_limits": True},
        "scope": {"kind": "catalogue_parent_schema", "attribute_target": "current_target", "member_type_basis": "native_base_type",
            "future_child_attributes_observed": False, "whole_project_coverage": False, "current_module": None,
            "permissions_verified": None, "proposed_values_validated": False, "engineering_validated": None,
            "range_unit_basis": "unknown", "range_boundary_encoding": "unknown", "optionality_is_requiredness": False}})


def template_identity(reference, kind, name, *, requested=None):
    def type_observation(source):
        return {"source": source, "name": kind, "short_name": kind, "status": "observed", "errors": {}}
    return {**identity(reference, kind), "name": name, "name_error": None,
            "base_type": type_observation("DbElement.GetElementType"), "actual_type": type_observation("DbElement.GetActualType"),
            "request_binding": {"source": "explicit_request_resolution", "requested_refno": requested,
                                "resolved_refno": requested, "observed_name": None, "verified": True} if requested else None,
            "errors": {}, "complete": True}


def template_attribute(name="PARAM", values=None):
    parameters = name == "PARAM"
    if values is None:
        values = [0.0, 80.0, 125.5] if parameters else []
    rows = [{"index": index, "value": value, "status": "observed", "error": None} for index, value in enumerate(values)] if parameters else values
    return {"requested_name": name, "expected_shape": "double_array" if parameters else "element_or_element_array",
            "status": "observed", "supported": True, "sdk_type": "DOUBLE" if parameters else "ELEMENT", "is_array": True,
            "unit_kind": "NONE", "shape": "double_array" if parameters else "element_array", "reference_null": None,
            "unit": None, "unit_status": "unknown", "total_count": len(rows), "returned_count": len(rows),
            "truncated": False, "inspection_limited": False, "native_array_materialized": True,
            "values": rows, "errors": {}, "complete": True}


def template_observation():
    refs = {name: template_attribute(name) for name in ("PTREF", "GMREF", "DTREF", "BLRFA", "NGMREF", "PSTREF", "GSTREF")}
    for index, (name, kind) in enumerate((("PTREF", "PTSE"), ("GMREF", "GMSE"), ("DTREF", "DTSE"))):
        refs[name] = template_attribute(name, [{"index": 0, "status": "observed", "reference_null": False, "error": None,
            "identity": template_identity(f"=7/{200 + index}", kind, "/SHARED_" + kind)}])
    return envelope({"operation": "paragon_component_template", "available": True, "success": True, "complete": True,
        "read_only": True, "single_dispatch": True, "atomic_snapshot": False, "session_id": SESSION, "bridge_build": BUILD,
        "requested_refno": SOURCE, "expected_base_type": "SCOM", "target": template_identity(SOURCE, "SCOM", "/TEMPLATE", requested=SOURCE),
        "leaf": {"source": "DbElement.Member(1)", "status": "observed", "is_leaf": True, "first_member": None, "complete": True, "errors": {}},
        "parameters": template_attribute(), "references": refs, "read_errors": [],
        "context": {"before": boundary(), "after": boundary(), "same_project_instance": True,
                    "same_mdb_instance": True, "boundary_agreement": True},
        "output_limits": {"maximum_parameters": 128, "maximum_reference_items": 16, "native_array_inspection_limit": 4096,
            "maximum_text_length": 1024, "native_arrays_materialized_before_output_limits": True, "native_array_allocation_bounded": False},
        "scope": {"kind": "fixed_catalogue_scom_template_observations", "database_type": "Catalogue", "database_type_code": 2,
            "actual_type_observed_separately": True, "reference_hops": 1, "reference_targets_expanded": False,
            "word_parameters_observed": False, "hidden_attributes_observed": False, "all_attributes_covered": False,
            "all_references_covered": False, "whole_clone_fingerprint": False, "geometry_observed": False, "geometry_created": False,
            "model_written": False, "permissions_verified": None, "write_permission_verified": None, "engineering_validated": None,
            "whole_project_coverage": False}})


class JsonResponse(io.BytesIO):
    def __init__(self, value):
        super().__init__(json.dumps(value, allow_nan=False).encode("utf-8"))


class WireEvidence:
    """Only the exact six read requests may leave the public tool."""

    def __init__(self, mutate=None, fault=None):
        self.replies = [health(), module_observation(), specification(source=True), template_observation(), specification(), schema_observation()]
        self.calls, self.originals, self.mutate, self.fault = [], [], mutate, fault

    def urlopen(self, request, timeout=None):
        index = len(self.calls)
        require_expected = EXPECTED_WIRE[index] if index < len(EXPECTED_WIRE) else None
        path = urllib.parse.urlsplit(request.full_url).path
        body = json.loads(request.data) if request.data is not None else None
        call = (request.method, path, body)
        self.calls.append(call)
        assert call == require_expected, f"Unexpected read/preview/execute/fallback request: {call}"
        assert not urllib.parse.urlsplit(request.full_url).query
        if self.fault:
            failure = self.fault(index, request)
            if failure is not None:
                if isinstance(failure, BaseException):
                    raise failure
                return JsonResponse(failure)
        reply = deepcopy(self.replies[index])
        if self.mutate:
            self.mutate(index, reply)
        self.originals.append(deepcopy(reply))
        return JsonResponse(reply)


@pytest.fixture(autouse=True)
def no_live_process_or_network():
    with patch("subprocess.Popen", side_effect=AssertionError("No process may be started")), \
         patch("urllib.request.urlopen", side_effect=AssertionError("All HTTP must use explicit local wire fixtures")):
        yield


def public_call(wire=None, arguments=None, *, settings=None):
    wire = wire or WireEvidence()
    server = create_server(settings or Settings(mode="bridge", version="E3D21", allow_commands=False))
    with patch("urllib.request.urlopen", side_effect=wire.urlopen) as transport:
        frame = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": TOOL, "arguments": ARGS if arguments is None else arguments}})
    assert "error" not in frame, frame
    return frame["result"], wire, transport


def test_independent_wire_fixtures_satisfy_real_evidence_validators():
    validate_module(module_observation())
    validate_specification(specification(source=True), SOURCE_PAYLOAD)
    validate_specification(specification(), PARENT_PAYLOAD)
    validate_schema(schema_observation(), SCHEMA_PAYLOAD)
    validate_template(template_observation(), {"refno": SOURCE})


@pytest.mark.parametrize("arguments", [
    {}, {"source_refno": SOURCE, "parent_refno": PARENT},
    *[{**ARGS, key: value} for key in ("source_refno", "parent_refno") for value in (
        None, True, 7, "", "CE", "WORLD", "/NAMED", "=0/1", "=-1/1", "=1/-1", "=1/+1",
        "=2147483648/1", "=1/2147483648", "=1/1/1", "=7/*", "=7/101;SAVEWORK", "=7/101\nSAVEWORK")],
    *[{**ARGS, "name": value} for value in (None, True, 7, "", "NEW", "/", "/" + "N" * 128,
        "/NEW SCOM", "/NEW\nSAVEWORK", "/NEW;SAVEWORK", "/NEW'", '/NEW"', "/NEW!X", "/NEW$X",
        "/NEW*", "/NEW?", "/元件", "/N\x00", "/N\x7f")],
    *[{**ARGS, key: value} for key, value in (("attributes", {}), ("params", [1]), ("geometry", {}),
        ("dry_run", True), ("execute", False), ("source", "SAVEWORK"), ("expected_session_id", SESSION),
        ("expected_revision", "a" * 64), ("include_references", True), ("recipe", "create_empty_gmse"), ("recipe", None))],
])
def test_public_invalid_or_injectable_inputs_never_read_or_submit_anything(arguments):
    response, wire, transport = public_call(arguments=arguments)
    assert response.get("isError") is True
    assert wire.calls == []
    transport.assert_not_called()


def result_of(response):
    content = response.get("structuredContent")
    assert isinstance(content, dict), response
    result = content.get("result")
    assert isinstance(result, dict), response
    return result


def assert_nonexecuting(result):
    assert result["plan_only"] is result["read_only"] is True
    assert result["writes_attempted"] is False
    assert all(result[field] is False for field in ("execution_supported", "execution_ready", "execution_binding_enforced"))
    assert result["execution_request"] is result["revision"] is None
    scope = result["scope"]
    assert all(scope[field] is False for field in ("admin_database_creation_attempted", "category_creation_attempted",
        "module_identity_is_authorization", "source_parameter_values_verified",
        "runtime_copy_semantics_verified", "runtime_syntax_verified", "geometry_generated", "geometry_verified",
        "reference_semantics_verified", "units_verified", "engineering_validated", "whole_project_coverage"))
    assert scope["permissions_verified"] is None
    assert scope["name_conflict_scope"] == "observed_current_parent_children_only"
    assert scope["name_global_uniqueness_verified"] is False
    assert result["readback_contract"]["performed"] is result["readback_contract"]["automatic_readback"] is False
    assert result["draft"]["runtime_syntax_verified"] is False
    for key in ("source_actual_type_verified", "source_actual_type_observed", "source_numeric_param_values_observed",
                "source_numeric_param_coverage_complete"):
        assert type(scope[key]) is bool


def assert_failed_plan(response, *, status=None):
    assert response.get("isError") is True
    result = result_of(response)
    assert_nonexecuting(result)
    assert result["success"] is result["complete"] is result["read_coverage_complete"] is False
    assert result["preview_request"] is None
    if status:
        assert result["status"] == status
    return result


def test_public_real_transport_chain_retains_original_evidence_and_only_emits_review_draft():
    wire = WireEvidence()
    before = deepcopy(wire.replies)
    response, wire, transport = public_call(wire)
    result = result_of(response)
    assert response.get("isError") is not True and result["status"] == "reviewable"
    assert result["success"] is result["complete"] is result["read_coverage_complete"] is True
    assert_nonexecuting(result)
    assert result["request"] == {**ARGS, "recipe": RECIPE}
    draft = f"{PARENT}\nNEW SCOM {ARGS['name']} AFTER LAST MEMBER COPY {SOURCE}\n"
    assert result["draft"]["source"] == draft
    assert result["draft"]["source_sha256"] == hashlib.sha256(draft.encode("utf-8")).hexdigest()
    assert result["draft"]["byte_count"] == len(draft.encode("utf-8")) and result["draft"]["line_count"] == 2
    assert result["preview_request"]["tool"] == "e3d_execute_pml_script"
    assert result["preview_request"]["arguments"] == {"source": draft, "dry_run": True, "expected_session_id": SESSION}
    assert "not PML semantics" in result["preview_request"]["preview_scope"]
    assert result["evidence"] == dict(zip(("health", "module_context", "source", "source_template", "target", "parent_schema"), wire.originals))
    assert result["scope"]["source_actual_type_verified"] is result["scope"]["source_numeric_param_coverage_complete"] is True
    descriptor = result["readback_contract"]["new_element_template_request"]
    assert descriptor["request_ready"] is False and "arguments" not in descriptor
    assert result["readback_contract"]["source_template_request"] == {"tool": "e3d_paragon_component_template", "arguments": {"refno": SOURCE}}
    assert wire.replies == before
    assert result["binding"]["session_id"] == SESSION and result["binding"]["bridge_build"] == BUILD
    assert result["binding"]["project"] == PROJECT and result["binding"]["mdb"] == MDB
    assert result["binding"]["ce_refno"] == PARENT and result["binding"]["ce_type"] == "CATE"
    assert result["binding"]["module_number"] == 81
    assert wire.calls == EXPECTED_WIRE and transport.call_count == 6


def test_public_discovery_exposes_only_a_closed_readonly_recipe():
    server = create_server(Settings(mode="mock", version="E3D21"))
    definitions = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})["result"]["tools"]
    tool, = [definition for definition in definitions if definition["name"] == TOOL]
    assert tool["annotations"] == {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
    schema = tool["inputSchema"]
    assert set(schema["properties"]) == {"source_refno", "parent_refno", "name", "recipe"}
    assert set(schema["required"]) == {"source_refno", "parent_refno", "name"} and schema["additionalProperties"] is False
    assert schema["properties"]["recipe"]["const"] == schema["properties"]["recipe"]["default"] == RECIPE


@pytest.mark.parametrize("name", ["/A", "/COMPONENT_1-A", "/CATEGORY/NEW_SCOM", "/" + "N" * 127])
def test_safe_ascii_names_are_preserved_in_exact_two_statement_script(name):
    response, wire, _ = public_call(arguments={**ARGS, "recipe": RECIPE, "name": name})
    result = result_of(response)
    assert result["status"] == "reviewable"
    assert result["draft"]["source"] == f"{PARENT}\nNEW SCOM {name} AFTER LAST MEMBER COPY {SOURCE}\n"
    assert wire.calls == EXPECTED_WIRE


@pytest.mark.parametrize("settings", [Settings(mode="mock", version="E3D21"), Settings(mode="bridge", version="E3D31"),
                                      Settings(mode="bridge", default_version="default")])
def test_unavailable_mode_never_invents_provenance_or_contacts_bridge(settings):
    response, wire, transport = public_call(settings=settings)
    result = assert_failed_plan(response, status="unavailable")
    assert result["binding"] is None and result["evidence"] == {} and result["required_checks"] == {}
    transport.assert_not_called()
    assert wire.calls == []


@pytest.mark.parametrize("feature", ["module_context", "piping_specification_read", "paragon_catalogue_schema", "pml_script_execution"])
def test_missing_required_capability_stops_after_independent_health(feature):
    def mutate(index, reply):
        if index == 0:
            reply["api_features"].remove(feature)
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    assert_failed_plan(response, status="unavailable")
    assert wire.calls == EXPECTED_WIRE[:1]


@pytest.mark.parametrize("field,value", [("version", None), ("version", "E3D31"), ("session_id", "not-uuid"),
    ("session_id", None), ("bridge_build", "configured-build"), ("bridge_build", None), ("connected", 1),
    ("diagnostic_error", "read failed")])
def test_health_uncertainty_or_false_boolean_cannot_be_filled_from_later_evidence(field, value):
    def mutate(index, reply):
        if index == 0:
            reply[field] = value
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    assert_failed_plan(response)
    assert wire.calls == EXPECTED_WIRE[:1]


@pytest.mark.parametrize("field,value", [("database_ready", 1), ("busy", None), ("busy", True), ("recovery_required", 0)])
def test_native_health_gate_booleans_must_be_explicit(field, value):
    def mutate(index, reply):
        if index == 0:
            reply["state"][field] = value
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    assert_failed_plan(response)
    assert len(wire.calls) == 1


@pytest.mark.parametrize("stage", range(6))
@pytest.mark.parametrize("status", [200, 404, 500])
def test_native_fault_at_any_stage_stops_all_later_reads_and_preserves_fault_metadata(stage, status):
    fault = {"ok": False, "version": "E3D21", "session_id": SESSION, "bridge_build": BUILD,
             "error": "Native database fault sentinel", "error_code": "native_database_fault",
             "native_fault_latched": True, "retry_safe": False}
    def fail(index, request):
        if index != stage:
            return None
        if status == 200:
            return fault
        return urllib.error.HTTPError(request.full_url, status, "fault", {}, JsonResponse(fault))
    response, wire, transport = public_call(WireEvidence(fault=fail))
    assert response.get("isError") is True and transport.call_count == stage + 1
    assert wire.calls == EXPECTED_WIRE[:stage + 1]
    observed = response["structuredContent"]
    assert observed["error_code"] == "native_database_fault" and observed["native_fault_latched"] is True
    assert observed["retry_safe"] is False and observed["session_id"] == SESSION and observed["bridge_build"] == BUILD
    assert observed.get("preview_request") is None and observed.get("execution_request") is None


@pytest.mark.parametrize("stage", range(6))
@pytest.mark.parametrize("status", [404, 500])
def test_nested_wire_error_fault_survives_each_read_route_without_unavailable_downgrade(stage, status):
    # Rotate the carrier across twelve cases instead of mirroring every marker /
    # container combination. Both specification calls explicitly cover 404.
    container = ("details", "diagnostics", "details" if status == 404 else "result",
                 "result", "diagnostics" if status == 404 else "result", "result")[stage]
    nested = {"outer": [{"sdk": {"native_fault_latched": True, "error_code": "native_database_fault",
                                   "retry_safe": False, "sentinel": f"read-{stage}-http-{status}"}}],
              "raw_context": {"preserve": "original fault-bearing container"}}
    fault = {"ok": False, "version": "E3D21", "session_id": SESSION, "bridge_build": BUILD,
             "error": "Nested SDK fault sentinel", container: nested,
             "ordinary_private_field": "must not be forwarded"}
    def fail(index, request):
        if index == stage:
            return urllib.error.HTTPError(request.full_url, status, "fault", {}, JsonResponse(fault))
    response, wire, transport = public_call(WireEvidence(fault=fail))
    assert response.get("isError") is True
    assert wire.calls == EXPECTED_WIRE[:stage + 1] and transport.call_count == stage + 1
    observed = response["structuredContent"]
    assert observed[container] == nested
    assert observed["session_id"] == SESSION and observed["bridge_build"] == BUILD
    assert observed["error"] == f"E3D bridge request failed: HTTP {status}: Nested SDK fault sentinel"
    assert observed.get("status") != "unavailable" and observed.get("operation") != "paragon_generation_plan"
    assert observed.get("preview_request") is observed.get("execution_request") is None
    assert "ordinary_private_field" not in observed


@pytest.mark.parametrize("stage,container", [(2, "details"), (4, "diagnostics")])
def test_source_and_target_nested404_rethrow_the_transport_bridge_error_with_original_cause(stage, container):
    from aveva_e3d_mcp.paragon_generation_plan import paragon_generation_plan

    nested = {"native": [{"has_fault": True, "retry_safe": False, "sentinel": "original diagnostic identity"}]}
    fault = {"ok": False, "version": "E3D21", "session_id": SESSION, "bridge_build": BUILD,
             "error": "Nested source/target fault", container: nested}
    sent_errors = []
    def fail(index, request):
        if index == stage:
            original = urllib.error.HTTPError(request.full_url, 404, "fault", {}, JsonResponse(fault))
            sent_errors.append(original)
            return original
    wire = WireEvidence(fault=fail)
    with patch("urllib.request.urlopen", side_effect=wire.urlopen) as transport, pytest.raises(BridgeError) as caught:
        paragon_generation_plan(BridgeClient(Settings(mode="bridge", version="E3D21", allow_commands=False)), ARGS)
    # BridgeClient's original BridgeError has the HTTPError as its direct cause.
    # A planner-created replacement would lose this original transport binding.
    assert caught.value.__cause__ is sent_errors[0]
    assert caught.value.http_status == 404
    assert caught.value.diagnostics == {"version": "E3D21", "session_id": SESSION, "bridge_build": BUILD, container: nested}
    assert wire.calls == EXPECTED_WIRE[:stage + 1] and transport.call_count == stage + 1


@pytest.mark.parametrize("stage", range(6))
def test_fault_in_successful_native_receipt_precedes_schema_validation_and_future_reads(stage):
    def mutate(index, reply):
        if index == stage:
            reply["diagnostics"] = {"nested": [{"has_fault": True, "sentinel": "preserve original raw diagnostic"}]}
    wire = WireEvidence(mutate=mutate)
    response, wire, _ = public_call(wire)
    assert response.get("isError") is True and wire.calls == EXPECTED_WIRE[:stage + 1]
    assert response["structuredContent"]["diagnostics"] == wire.originals[-1]["diagnostics"]
    assert response["structuredContent"].get("preview_request") is None


@pytest.mark.parametrize("stage", range(6))
@pytest.mark.parametrize("status", [404, 500])
def test_ordinary_wire_failure_is_checkpointed_without_retry_or_alternate_reader(stage, status):
    error = {"ok": False, "version": "E3D21", "session_id": SESSION, "bridge_build": BUILD, "error": "ordinary read unavailable"}
    def fail(index, request):
        if index == stage:
            return urllib.error.HTTPError(request.full_url, status, "read failed", {}, JsonResponse(error))
    response, wire, _ = public_call(WireEvidence(fault=fail))
    result = assert_failed_plan(response, status="unavailable" if status == 404 else None)
    assert wire.calls == EXPECTED_WIRE[:stage + 1]
    key = ("health", "module_context", "source", "source_template", "target", "parent_schema")[stage]
    assert result["evidence"][key]["bridge_error"]["http_status"] == status


@pytest.mark.parametrize("stage", range(6))
def test_missing_native_version_is_not_repaired_by_configured_e3d21(stage):
    def mutate(index, reply):
        if index == stage:
            reply.pop("version")
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = assert_failed_plan(response)
    assert wire.calls == EXPECTED_WIRE[:stage + 1]
    key = ("health", "module_context", "source", "source_template", "target", "parent_schema")[stage]
    assert "version" not in result["evidence"][key]


@pytest.mark.parametrize("stage", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("field", ["session_id", "bridge_build"])
@pytest.mark.parametrize("replacement", [None, "other", MISSING])
def test_matching_inner_native_identities_never_override_missing_or_conflicting_outer_identity(stage, field, replacement):
    def mutate(index, reply):
        if index == stage:
            if replacement is MISSING:
                reply.pop(field)
            else:
                reply[field] = replacement
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    assert_failed_plan(response)
    assert wire.calls == EXPECTED_WIRE[:stage + 1]


@pytest.mark.parametrize("stage", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("side", ["before", "after"])
@pytest.mark.parametrize("field", ["session_id", "bridge_build"])
def test_each_reader_native_boundary_has_to_match_independent_health(stage, side, field):
    def mutate(index, reply):
        if index == stage:
            reply["result"]["context"][side][field] = "other"
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    assert_failed_plan(response)
    assert wire.calls == EXPECTED_WIRE[:stage + 1]


@pytest.mark.parametrize("stage", [2, 3, 4, 5])
@pytest.mark.parametrize("field", ["project", "mdb", "current_element"])
def test_stable_but_different_endpoint_context_cannot_replace_module_anchor(stage, field):
    def mutate(index, reply):
        if index != stage:
            return
        for side in ("before", "after"):
            sample = reply["result"]["context"][side]
            sample[field] = identity("=7/999", "SCOM") if field == "current_element" else "OTHER_CONTEXT"
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = assert_failed_plan(response)
    assert result["binding"]["project"] == PROJECT and result["binding"]["mdb"] == MDB
    assert wire.calls == EXPECTED_WIRE[:stage + 1]


@pytest.mark.parametrize("stage", [3, 4, 5])
def test_full_typed_ce_database_identity_cannot_drift_behind_matching_ref_and_type(stage):
    def mutate(index, reply):
        if index == stage:
            for side in ("before", "after"):
                ce = reply["result"]["context"][side]["current_element"]
                ce.update(db_type="Design", db_type_code=1)
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    assert_failed_plan(response)
    assert wire.calls == EXPECTED_WIRE[:stage + 1]


@pytest.mark.parametrize("module_name,module_number", [("DESIGN", 4), ("PARAGON", 41), ("ADMIN", 81), ("PARAGON", True)])
def test_module_name_and_native_number_are_evidence_not_authorization(module_name, module_number):
    def mutate(index, reply):
        if index == 1:
            for item in (reply["result"]["module"], reply["result"]["context"]["before"]["module"], reply["result"]["context"]["after"]["module"]):
                item.update(name=module_name, number=module_number)
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    assert_failed_plan(response)
    assert wire.calls == EXPECTED_WIRE[:2]


@pytest.mark.parametrize("stage,flag", [(stage, flag) for stage in (2, 3, 4, 5)
    for flag in ("boundary_agreement", "same_project_instance", "same_mdb_instance")])
@pytest.mark.parametrize("value", [1, False, None])
def test_endpoint_context_flags_cannot_be_true_like_numbers_or_unknown(stage, flag, value):
    def mutate(index, reply):
        if index == stage:
            reply["result"]["context"][flag] = value
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    assert_failed_plan(response)
    assert wire.calls == EXPECTED_WIRE[:stage + 1]


@pytest.mark.parametrize("owner", [identity("=7/999", "CATE"), identity(PARENT, "SECT"), identity(PARENT, "CATE", "Design", 1)])
def test_source_owner_must_be_exact_typed_catalogue_parent_before_target_is_read(owner):
    def mutate(index, reply):
        if index == 2:
            reply["result"]["rows"][0]["owner"] = deepcopy(owner)
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = assert_failed_plan(response, status="blocked")
    assert result["required_checks"]["source_same_catalogue_cate_parent"] is False
    assert wire.calls == EXPECTED_WIRE[:3]


def test_source_leaf_is_required_and_single_row_limit_does_not_prove_it():
    def mutate(index, reply):
        if index == 2:
            reply["result"].update(success=False, complete=False, traversal_complete=False, truncated=True, truncation_reasons=["limit"])
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = assert_failed_plan(response, status="blocked")
    assert result["required_checks"]["source_leaf"] is False and wire.calls == EXPECTED_WIRE[:3]


def test_observed_case_insensitive_name_collision_blocks_before_schema():
    def mutate(index, reply):
        if index == 4:
            reply["result"]["rows"][2]["name"] = ARGS["name"].lower()
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = assert_failed_plan(response, status="blocked")
    assert result["required_checks"]["name_clear_in_parent"] is False and wire.calls == EXPECTED_WIRE[:5]


def test_full_parent_must_independently_contain_source_typed_identity():
    def mutate(index, reply):
        if index == 4:
            reply["result"]["rows"][1].update(refno="=7/999", identity_key="=7/999|SCOM")
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = assert_failed_plan(response)
    assert result["required_checks"]["source_listed_under_parent"] is False and wire.calls == EXPECTED_WIRE
    assert result["required_checks"]["source_name_stable_in_parent"] is result["required_checks"]["source_leaf_in_parent"] is None


def test_truncated_parent_prefix_never_proves_name_absence_or_source_membership():
    def mutate(index, reply):
        if index != 4:
            return
        result = reply["result"]
        template = result["rows"][2]
        for row_id in range(3, 100):
            refno = f"=7/{1000 + row_id}"
            result["rows"].append({**deepcopy(template), "id": row_id, "refno": refno, "identity_key": f"{refno}|SCOM",
                                   "sibling_index": row_id, "name": f"/PREFIX{row_id}"})
        result.update(returned_count=100, complete=False, success=False, traversal_complete=False,
                      truncated=True, truncation_reasons=["limit"])
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = assert_failed_plan(response)
    assert result["required_checks"]["name_clear_in_parent"] is None
    assert result["required_checks"]["source_listed_under_parent"] is None
    assert result["required_checks"]["source_leaf_in_parent"] is None
    assert result["evidence"]["target"]["result"]["returned_count"] == 100 and wire.calls == EXPECTED_WIRE


def test_late_complete_parent_source_name_drift_is_valid_raw_but_never_reviewable():
    def mutate(index, reply):
        if index == 4:
            reply["result"]["rows"][1]["name"] = "/SOURCE_RENAMED_AFTER_TEMPLATE"
            validate_specification(reply, PARENT_PAYLOAD)

    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = assert_failed_plan(response, status="unknown")
    checks = result["required_checks"]
    assert checks["source_template_name_agreement"] is True
    assert checks["source_name_stable_in_parent"] is False
    assert checks["source_leaf_in_parent"] is True
    assert result["scope"]["source_actual_type_verified"] is result["scope"]["source_numeric_param_coverage_complete"] is True
    raw = result["evidence"]["target"]
    assert raw == wire.originals[4] and raw["result"]["complete"] is raw["result"]["success"] is True
    assert raw["result"]["context"]["boundary_agreement"] is True
    assert raw["result"]["rows"][1]["name"] != result["evidence"]["source_template"]["result"]["target"]["name"]
    assert wire.calls == EXPECTED_WIRE


def test_late_source_child_in_valid_parent_preorder_overrides_earlier_leaf_observations():
    def mutate(index, reply):
        if index != 4:
            return
        raw = reply["result"]
        root, source, other = raw["rows"]
        child = {**observation("=7/130", "SCYL", "/LATE_SOURCE_CHILD"), "id": 2, "depth": 2, "parent_index": source["id"],
                 "sibling_index": 1, "owner": identity(SOURCE, "SCOM"), "owner_error": None, "attributes": {}, "complete": True}
        other["id"] = 3
        raw.update(rows=[root, source, child, other], returned_count=4)
        validate_specification(reply, PARENT_PAYLOAD)

    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = assert_failed_plan(response, status="unknown")
    checks = result["required_checks"]
    assert checks["source_leaf"] is checks["source_template_leaf_agreement"] is True
    assert checks["source_name_stable_in_parent"] is True
    assert checks["source_leaf_in_parent"] is False
    assert result["scope"]["source_actual_type_verified"] is result["scope"]["source_numeric_param_coverage_complete"] is True
    raw = result["evidence"]["target"]
    assert raw == wire.originals[4] and raw["result"]["complete"] is raw["result"]["traversal_complete"] is True
    assert raw["result"]["rows"][2]["owner"] == identity(SOURCE, "SCOM")
    assert raw["result"]["context"]["boundary_agreement"] is True
    assert wire.calls == EXPECTED_WIRE


def test_late_parent_source_name_getter_failure_stays_unknown_without_erasing_earlier_slices():
    def mutate(index, reply):
        if index == 4:
            reply["result"]["rows"][1].update(name=None, name_error="late source NAME getter failed", complete=False)
            reply["result"].update(success=False, complete=False)
            validate_specification(reply, PARENT_PAYLOAD)

    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = assert_failed_plan(response, status="unknown")
    assert result["required_checks"]["source_name_stable_in_parent"] is None
    assert result["required_checks"]["source_leaf_in_parent"] is True
    assert result["scope"]["source_actual_type_verified"] is result["scope"]["source_numeric_param_coverage_complete"] is True
    assert result["evidence"]["target"] == wire.originals[4]
    assert result["evidence"]["target"]["result"]["traversal_complete"] is True
    assert wire.calls == EXPECTED_WIRE


@pytest.mark.parametrize("mode", ["unsupported", "null_reference", "array_typed_unknown", "typed_read_error"])
def test_source_reference_partial_is_validated_and_retained_without_erasing_required_identity_leaf_evidence(mode):
    def mutate(index, reply):
        if index != 2:
            return
        raw = reply["result"]
        field = raw["rows"][0]["attributes"]["BLRFA"]
        field.update(status="unknown", typed_status="unknown", typed_value=None, reference_null=None, errors={"typed": "raw SDK value not observed"})
        if mode == "unsupported":
            field.update(supported=False, sdk_type=None, is_array=None, unit_kind=None, formatted_value=None,
                         formatted_status="unknown", errors={"value": "not supported on this source"})
        elif mode == "null_reference":
            field["reference_null"] = True
        elif mode == "array_typed_unknown":
            field.update(sdk_type="ELEMENTARRAY", is_array=True)
        raw["rows"][0]["complete"] = False
        raw.update(success=False, complete=False)
    wire = WireEvidence(mutate=mutate)
    response, wire, _ = public_call(wire)
    result = result_of(response)
    assert response.get("isError") is not True and result["status"] == "reviewable"
    assert_nonexecuting(result)
    assert result["read_coverage_complete"] is True
    assert result["evidence"]["source"] == wire.originals[2]
    assert result["evidence"]["source"]["result"]["complete"] is False
    assert wire.calls == EXPECTED_WIRE


def test_source_partial_receipt_is_never_accepted_without_existing_validator():
    def mutate(index, reply):
        if index == 2:
            # The stale row/top booleans contradict the actual attribute value.
            reply["result"]["rows"][0]["attributes"]["PTREF"]["typed_value"] = "not a typed reference"
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = assert_failed_plan(response)
    assert result["evidence"]["source"]["result"]["complete"] is True
    assert wire.calls == EXPECTED_WIRE[:3]


@pytest.mark.parametrize("value,status", [(False, "blocked"), (None, "unknown")])
def test_parent_creatability_is_an_independent_three_state_observation(value, status):
    def mutate(index, reply):
        if index != 5:
            return
        raw = reply["result"]
        child = raw["child_types"][0]
        child["sdk_creatable_now"] = value
        if value is None:
            child.update(complete=False, errors={"sdk_creatable_now": "unknown SDK state"})
            raw["child_types_observation"]["complete"] = False
            raw.update(success=False, complete=False)
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = assert_failed_plan(response, status=status)
    assert result["required_checks"]["scom_sdk_creatable_now"] is value and wire.calls == EXPECTED_WIRE


def test_parent_member_catalogue_support_is_separate_from_creatability():
    def mutate(index, reply):
        if index == 5:
            reply["result"]["child_types"][0].update(database_type_codes=[1], catalogue_supported=False)
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = assert_failed_plan(response, status="blocked")
    assert result["required_checks"]["scom_sdk_creatable_now"] is True
    assert result["required_checks"]["scom_catalogue_supported"] is False


def test_complete_category_with_geometry_descendants_and_sibling_source_is_read_to_depth16():
    def mutate(index, reply):
        if index != 4:
            return
        raw = reply["result"]
        root, source, other = raw["rows"]
        geometry = {**observation("=7/110", "GMSE", "/GEOMETRY"), "id": 1, "depth": 1, "parent_index": 0,
                    "sibling_index": 1, "owner": identity(), "owner_error": None, "attributes": {}, "complete": True}
        primitive = {**observation("=7/111", "SCYL", "/GEOMETRY/CYLINDER"), "id": 2, "depth": 2, "parent_index": 1,
                     "sibling_index": 1, "owner": identity("=7/110", "GMSE"), "owner_error": None, "attributes": {}, "complete": True}
        source.update(id=3, sibling_index=2)
        other.update(id=4, sibling_index=3)
        raw.update(rows=[root, geometry, primitive, source, other], returned_count=5)
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = result_of(response)
    assert response.get("isError") is not True and result["status"] == "reviewable"
    assert result["required_checks"]["target_prefix_complete"] is True
    assert result["required_checks"]["source_listed_under_parent"] is True
    assert result["readback_contract"]["parent_request"]["arguments"]["max_depth"] == 16
    assert wire.calls == EXPECTED_WIRE


@pytest.mark.parametrize("stage,field,value", [(2, "success", 1), (2, "complete", 1), (2, "traversal_complete", 1),
    (4, "returned_count", True), (4, "complete", 1), (5, "complete", 1), (5, "attributes_complete", 1)])
def test_real_validators_reject_true_like_or_stale_native_summary_fields(stage, field, value):
    def mutate(index, reply):
        if index == stage:
            reply["result"][field] = value
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    assert_failed_plan(response)
    assert wire.calls == EXPECTED_WIRE[:stage + 1]


@pytest.mark.parametrize("stage,field,value", [(2, "type", "SPCO"), (2, "db_type", "Design"),
                                             (4, "type", "STCA"), (4, "db_type", "Design")])
def test_source_and_parent_typed_root_identity_cannot_be_inferred_from_request(stage, field, value):
    def mutate(index, reply):
        if index != stage:
            return
        row = reply["result"]["rows"][0]
        row[field] = value
        if field == "type":
            row["identity_key"] = f"{row['refno']}|{value}"
        else:
            row["db_type_code"] = 1
            row["database"].update(type="Design", type_code=1)
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    assert_failed_plan(response)
    assert wire.calls == EXPECTED_WIRE[:stage + 1]


def test_unknown_catalogue_support_keeps_candidate_unknown_without_changing_creatability():
    def mutate(index, reply):
        if index != 5:
            return
        raw = reply["result"]
        raw["child_types"][0].update(database_type_codes=None, database_types_total_count=None, database_types_truncated=None,
                                     catalogue_supported=None, errors={"database_type_codes": "SDK db types unavailable"}, complete=False)
        raw["child_types_observation"]["complete"] = False
        raw.update(success=False, complete=False)
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = assert_failed_plan(response, status="unknown")
    assert result["required_checks"]["scom_member_type_observed"] is True
    assert result["required_checks"]["scom_catalogue_supported"] is None
    assert result["required_checks"]["scom_sdk_creatable_now"] is True
    assert wire.calls == EXPECTED_WIRE


def test_missing_scom_in_complete_schema_is_an_observed_blocker():
    def mutate(index, reply):
        if index == 5:
            reply["result"]["child_types"][0].update(name="POINTSET", short_name="PTSE")
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = assert_failed_plan(response, status="blocked")
    assert result["required_checks"]["scom_member_type_observed"] is False
    assert wire.calls == EXPECTED_WIRE


def test_unreadable_parent_child_name_keeps_absence_unknown_and_preserves_raw_error():
    def mutate(index, reply):
        if index == 4:
            raw = reply["result"]
            raw["rows"][2].update(name=None, name_error="SDK name getter failed", complete=False)
            raw.update(success=False, complete=False)
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = assert_failed_plan(response)
    assert result["required_checks"]["name_clear_in_parent"] is None
    assert result["evidence"]["target"] == wire.originals[4]
    assert wire.calls == EXPECTED_WIRE


def test_request_raw_fixtures_and_returned_evidence_are_independent_after_public_serialization():
    arguments, wire = deepcopy(ARGS), WireEvidence()
    original = deepcopy((arguments, wire.replies))
    response, wire, _ = public_call(wire, arguments=arguments)
    result = result_of(response)
    assert (arguments, wire.replies) == original
    # Editing this client's report cannot mutate the source observations used
    # by another call or manufacture a new returned execution request.
    result["evidence"]["source"]["result"]["rows"][0]["attributes"]["PTREF"]["formatted_value"] = "edited report"
    assert wire.replies == original[1]
    response2, wire2, _ = public_call()
    assert result_of(response2)["evidence"]["source"]["result"]["rows"][0]["attributes"]["PTREF"]["formatted_value"] != "edited report"
    assert_nonexecuting(result_of(response2))
    assert wire2.calls == EXPECTED_WIRE


def partial_template(reply):
    reply["result"].update(success=False, complete=False)


def unread_template_attribute(name, *, supported=None, errors=None):
    row = template_attribute(name)
    row.update(status="unknown", supported=supported, sdk_type=None, is_array=None, unit_kind=None, shape=None,
               reference_null=None, total_count=None, returned_count=0, truncated=None, inspection_limited=None,
               native_array_materialized=False, values=[], errors={} if errors is None else errors, complete=False)
    return row


def test_template_actual_udet_mismatch_blocks_before_parent_and_schema_reads():
    def mutate(index, reply):
        if index == 3:
            reply["result"]["target"]["actual_type"].update(name="CustomCatalogueComponent", short_name=":CUSTOM_SCOM")
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = assert_failed_plan(response, status="blocked")
    assert result["required_checks"]["source_actual_type_scom"] is False
    assert result["scope"]["source_actual_type_observed"] is True
    assert result["scope"]["source_actual_type_verified"] is False
    assert wire.calls == EXPECTED_WIRE[:4]


def test_unknown_actual_type_preserves_observed_param_without_inventing_base_as_actual():
    def mutate(index, reply):
        if index == 3:
            target = reply["result"]["target"]
            target.update(actual_type=None, errors={"actual_type": "native actual type getter failed"}, complete=False)
            partial_template(reply)
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = assert_failed_plan(response, status="unknown")
    assert result["required_checks"]["source_actual_type_scom"] is None
    assert result["scope"]["source_actual_type_observed"] is result["scope"]["source_actual_type_verified"] is False
    assert result["scope"]["source_numeric_param_values_observed"] is result["scope"]["source_numeric_param_coverage_complete"] is True
    assert result["evidence"]["source_template"] == wire.originals[3] and wire.calls == EXPECTED_WIRE


@pytest.mark.parametrize("count", [0, 128])
def test_complete_finite_numeric_param_can_be_empty_or_exactly_at_output_limit(count):
    values = [index * 0.125 - 5.0 for index in range(count)]
    def mutate(index, reply):
        if index == 3:
            reply["result"]["parameters"] = template_attribute(values=values)
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = result_of(response)
    assert response.get("isError") is not True and result["status"] == "reviewable"
    assert result["required_checks"]["source_numeric_param_coverage_complete"] is True
    assert result["scope"]["source_numeric_param_values_observed"] is result["scope"]["source_numeric_param_coverage_complete"] is True
    assert result["scope"]["source_parameter_values_verified"] is False
    assert [row["value"] for row in result["evidence"]["source_template"]["result"]["parameters"]["values"]] == values
    assert wire.calls == EXPECTED_WIRE


@pytest.mark.parametrize("count", [129, 4096, 4097])
def test_param_prefix_or_native_inspection_cutoff_never_becomes_complete_parameter_evidence(count):
    def mutate(index, reply):
        if index != 3:
            return
        parameter = template_attribute(values=[float(i) for i in range(min(count, 128))])
        parameter.update(total_count=count, truncated=True, complete=False)
        if count <= 4096:
            parameter.update(status="partial", errors={"output_limit": "native PARAM prefix only"})
        else:
            parameter.update(status="unknown", returned_count=0, inspection_limited=True, values=[],
                             errors={"inspection_limit": "native array materialized but not inspected"})
        reply["result"]["parameters"] = parameter
        partial_template(reply)
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = assert_failed_plan(response, status="unknown")
    raw = result["evidence"]["source_template"]["result"]["parameters"]
    assert raw["total_count"] == count and raw["truncated"] is True
    assert result["required_checks"]["source_numeric_param_coverage_complete"] is None
    assert result["scope"]["source_numeric_param_coverage_complete"] is False
    assert result["scope"]["source_numeric_param_values_observed"] is (count <= 4096)
    assert raw["native_array_materialized"] is True and wire.calls == EXPECTED_WIRE


def test_nonfinite_native_param_is_retained_as_unknown_item_and_stops_preview_readiness():
    def mutate(index, reply):
        if index == 3:
            parameter = reply["result"]["parameters"]
            parameter["values"][1].update(value=None, status="unknown", error="non_finite_native_value")
            parameter.update(status="partial", complete=False, errors={"non_finite": "non-finite native PARAM value"})
            partial_template(reply)
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = assert_failed_plan(response, status="unknown")
    assert result["scope"]["source_numeric_param_values_observed"] is True
    assert result["scope"]["source_numeric_param_coverage_complete"] is False
    assert result["evidence"]["source_template"] == wire.originals[3]


def test_wire_param_unit_kind_only_partial_keeps_numeric_coverage_without_rewriting_raw_receipt():
    def mutate(index, reply):
        if index == 3:
            reply["result"]["parameters"].update(unit_kind=None, status="partial", complete=False,
                errors={"unit_kind": "SDK unit category getter failed"})
            partial_template(reply)
            validate_template(reply, {"refno": SOURCE})

    wire = WireEvidence(mutate=mutate)
    unchanged = deepcopy(wire.replies)
    response, wire, transport = public_call(wire)
    result = result_of(response)
    assert response.get("isError") is not True and result["status"] == "reviewable"
    assert result["complete"] is result["read_coverage_complete"] is True
    assert result["required_checks"]["source_numeric_param_coverage_complete"] is True
    assert result["scope"]["source_numeric_param_values_observed"] is result["scope"]["source_numeric_param_coverage_complete"] is True
    assert result["scope"]["units_verified"] is result["scope"]["source_parameter_values_verified"] is False
    assert_nonexecuting(result)
    assert result["preview_request"]["arguments"]["dry_run"] is True
    raw = result["evidence"]["source_template"]
    assert raw == wire.originals[3] and raw["result"]["success"] is raw["result"]["complete"] is False
    parameter = raw["result"]["parameters"]
    assert parameter["unit_kind"] is None and parameter["status"] == "partial" and parameter["complete"] is False
    assert parameter["errors"] == {"unit_kind": "SDK unit category getter failed"}
    assert [row["value"] for row in parameter["values"]] == [0.0, 80.0, 125.5]
    assert wire.replies == unchanged and wire.calls == EXPECTED_WIRE and transport.call_count == 6


def test_wire_param_unit_kind_plus_value_failure_remains_unknown_despite_observed_type_and_array():
    def mutate(index, reply):
        if index == 3:
            reply["result"]["parameters"].update(unit_kind=None, status="unknown", complete=False,
                total_count=None, returned_count=0, truncated=None, inspection_limited=None,
                native_array_materialized=False, values=[],
                errors={"unit_kind": "SDK unit category getter failed", "value": "Native numeric PARAM value not observed"})
            partial_template(reply)
            validate_template(reply, {"refno": SOURCE})

    wire = WireEvidence(mutate=mutate)
    unchanged = deepcopy(wire.replies)
    response, wire, transport = public_call(wire)
    result = assert_failed_plan(response, status="unknown")
    assert result["required_checks"]["source_numeric_param_coverage_complete"] is None
    assert result["scope"]["source_numeric_param_values_observed"] is result["scope"]["source_numeric_param_coverage_complete"] is False
    assert result["scope"]["units_verified"] is result["scope"]["source_parameter_values_verified"] is False
    raw = result["evidence"]["source_template"]
    assert raw == wire.originals[3] and raw["result"]["complete"] is False
    parameter = raw["result"]["parameters"]
    assert parameter["sdk_type"] == "DOUBLE" and parameter["is_array"] is True and parameter["shape"] == "double_array"
    assert parameter["values"] == [] and parameter["complete"] is False and parameter["errors"].keys() == {"unit_kind", "value"}
    assert wire.replies == unchanged and wire.calls == EXPECTED_WIRE and transport.call_count == 6


@pytest.mark.parametrize("mode", ["unsupported", "wrong_sdk_shape", "value_getter_failed"])
def test_unobserved_param_shape_or_getter_never_invents_numeric_values(mode):
    def mutate(index, reply):
        if index != 3:
            return
        if mode == "unsupported":
            parameter = unread_template_attribute("PARAM", supported=False, errors={"support": "unsupported_attribute_value_unknown"})
        else:
            parameter = unread_template_attribute("PARAM", supported=True, errors={"value": "native PARAM array unavailable"})
            parameter.update(sdk_type="INTEGER" if mode == "wrong_sdk_shape" else "DOUBLE", is_array=True, unit_kind="NONE",
                             shape=None if mode == "wrong_sdk_shape" else "double_array")
        reply["result"]["parameters"] = parameter
        partial_template(reply)
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = assert_failed_plan(response, status="unknown")
    assert result["scope"]["source_numeric_param_values_observed"] is result["scope"]["source_numeric_param_coverage_complete"] is False
    assert result["evidence"]["source_template"] == wire.originals[3] and wire.calls == EXPECTED_WIRE


@pytest.mark.parametrize("mode", ["bool_value", "string_value", "wrong_index", "index_bool", "count_bool", "cutoff_claim", "stale_flag"])
def test_template_param_validator_is_never_skipped_to_accept_forged_required_slices(mode):
    def mutate(index, reply):
        if index != 3:
            return
        parameter = reply["result"]["parameters"]
        if mode == "bool_value":
            parameter["values"][0]["value"] = True
        elif mode == "string_value":
            parameter["values"][0]["value"] = "1.0"
        elif mode == "wrong_index":
            parameter["values"][1]["index"] = 0
        elif mode == "index_bool":
            parameter["values"][0]["index"] = False
        elif mode == "count_bool":
            parameter["returned_count"] = True
        elif mode == "cutoff_claim":
            parameter.update(total_count=4097, complete=True)
        else:
            parameter["complete"] = 1
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    assert_failed_plan(response)
    assert wire.calls == EXPECTED_WIRE[:4]


def test_partial_optional_template_reference_does_not_gate_actual_or_param_required_slices():
    def mutate(index, reply):
        if index == 3:
            reply["result"]["references"]["NGMREF"] = unread_template_attribute(
                "NGMREF", supported=False, errors={"support": "unsupported_attribute_value_unknown"})
            partial_template(reply)
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = result_of(response)
    assert response.get("isError") is not True and result["status"] == "reviewable"
    assert_nonexecuting(result)
    assert result["evidence"]["source_template"] == wire.originals[3]
    assert result["evidence"]["source_template"]["result"]["complete"] is False
    assert result["scope"]["source_actual_type_verified"] is result["scope"]["source_numeric_param_coverage_complete"] is True
    assert wire.calls == EXPECTED_WIRE


@pytest.mark.parametrize("stage", [2, 3])
def test_source_name_getter_failure_retains_independent_actual_leaf_and_numeric_evidence(stage):
    def mutate(index, reply):
        if index != stage:
            return
        if stage == 2:
            reply["result"]["rows"][0].update(name=None, name_error="SDK NAME failed", complete=False)
            reply["result"].update(success=False, complete=False)
        else:
            target = reply["result"]["target"]
            target.update(name=None, name_error="SDK NAME failed", errors={"name": "SDK NAME failed"}, complete=False)
            partial_template(reply)
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = assert_failed_plan(response, status="unknown")
    assert result["required_checks"]["source_template_name_agreement"] is None
    assert result["required_checks"]["source_template_numeric_identity_verified"] is True
    assert result["scope"]["source_actual_type_verified"] is result["scope"]["source_numeric_param_coverage_complete"] is True
    assert wire.calls == EXPECTED_WIRE


def test_known_name_drift_between_specification_and_template_never_becomes_reviewable():
    def mutate(index, reply):
        if index == 3:
            reply["result"]["target"]["name"] = "/RENAMED_AFTER_SPEC_READ"
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = assert_failed_plan(response, status="unknown")
    assert result["required_checks"]["source_template_name_agreement"] is False
    assert result["required_checks"]["source_template_numeric_identity_verified"] is True
    assert wire.calls == EXPECTED_WIRE


def test_later_numeric_source_identity_drift_preserves_first_binding_but_cannot_reuse_other_values():
    def mutate(index, reply):
        if index != 3:
            return
        raw = reply["result"]
        raw["target"].update(refno="=7/999", identity_key="=7/999|SCOM", errors={"request_binding": "target identity changed"}, complete=False)
        raw["leaf"].update(status="unknown", is_leaf=None, first_member=None, complete=False)
        raw["parameters"] = unread_template_attribute("PARAM")
        raw["references"] = {name: unread_template_attribute(name) for name in raw["references"]}
        partial_template(reply)
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    result = assert_failed_plan(response, status="unknown")
    assert result["required_checks"]["source_template_numeric_identity_verified"] is False
    assert result["scope"]["source_actual_type_verified"] is result["scope"]["source_numeric_param_values_observed"] is False
    assert result["evidence"]["source_template"]["result"]["target"]["request_binding"]["resolved_refno"] == SOURCE
    assert wire.calls == EXPECTED_WIRE


@pytest.mark.parametrize("field", ["session_id", "bridge_build"])
@pytest.mark.parametrize("value", [None, "other", MISSING])
def test_template_result_identity_is_required_in_addition_to_matching_outer_and_boundaries(field, value):
    def mutate(index, reply):
        if index == 3:
            if value is MISSING:
                reply["result"].pop(field)
            else:
                reply["result"][field] = value
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    assert_failed_plan(response)
    assert wire.calls == EXPECTED_WIRE[:4]


@pytest.mark.parametrize("mode", ["requested_ref", "resolved_ref", "verified_bool", "inferred_name", "missing_context_side", "typed_ce_unknown"])
def test_template_source_binding_and_context_are_revalidated_before_summary_projection(mode):
    def mutate(index, reply):
        if index != 3:
            return
        raw = reply["result"]
        binding = raw["target"]["request_binding"]
        if mode == "requested_ref":
            binding["requested_refno"] = OTHER
        elif mode == "resolved_ref":
            binding["resolved_refno"] = OTHER
        elif mode == "verified_bool":
            binding["verified"] = 1
        elif mode == "inferred_name":
            binding["observed_name"] = "/TEMPLATE"
        elif mode == "missing_context_side":
            raw["context"].pop("after")
        else:
            raw["context"]["after"]["current_element"] = None
            raw["context"]["after"]["errors"] = {"current_element": "typed CE unavailable"}
            raw["context"]["boundary_agreement"] = None
            partial_template(reply)
    response, wire, _ = public_call(WireEvidence(mutate=mutate))
    assert_failed_plan(response)
    assert wire.calls == EXPECTED_WIRE[:4]

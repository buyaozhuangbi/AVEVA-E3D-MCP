"""Read-only generation-plan contracts; every native/network read is mocked."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.paragon_generation_plan import (
    MODULE_PATH, RECIPE, REFERENCE_ATTRIBUTES, REQUIRED_FEATURES, SCHEMA_PATH, SPEC_PATH, TEMPLATE_PATH, TOOL,
    _parameters, paragon_generation_plan, register_paragon_generation_plan,
)
from aveva_e3d_mcp.protocol import McpServer
from aveva_e3d_mcp.paragon_component_template import (
    _OUTPUT_LIMITS as TEMPLATE_LIMITS, _SCOPE as TEMPLATE_SCOPE, _validate as validate_template,
)
from aveva_e3d_mcp.piping_specification_read import _validate as validate_spec
from test_module_context_tools import observed as module_sample
from test_paragon_component_template_native_contract import component_template_receipts
from test_paragon_catalogue_schema import sample as schema_sample
from test_piping_specification_read import BUILD, SESSION, attribute, identity, observation, sample as spec_sample


SETTINGS = Settings(mode="bridge", version="E3D21", allow_commands=False)
ARGS = {"source_refno": "=1/10", "parent_refno": "=1/50", "name": "/MCP/NEW_SCOM"}
STAGES = ("health", "module_context", "source", "source_template", "target", "parent_schema")
CE = identity("=1/900", "CATA", "Catalogue")


def healthy():
    return {"ok": True, "version": "E3D21", "session_id": SESSION, "bridge_build": BUILD,
            "connected": True, "diagnostic_error": None, "api_features": list(REQUIRED_FEATURES), "state": {
                "database_ready": True, "busy": False, "recovery_required": False,
                "native_database_fault": {"has_fault": False}, "generated_csg_quarantine": {"active": False}}}


def boundary():
    return {"session_id": SESSION, "bridge_build": BUILD, "project": "PRJ", "mdb": "CAT-MDB",
            "current_element": deepcopy(CE), "errors": {}}


def bind(reply):
    reply.update(session_id=SESSION, bridge_build=BUILD)
    reply["result"]["context"].update(before=boundary(), after=boundary())
    return reply


def native_module():
    reply = module_sample()
    reply.update(session_id=SESSION, bridge_build=BUILD)
    module = {"status": "present", "name": "PARAGON", "number": 81, "description": "Catalogue"}
    reply["result"]["module"] = deepcopy(module)
    for sample in reply["result"]["context"].values():
        if not isinstance(sample, dict):
            continue
        sample.update(session_id=SESSION, bridge_build=BUILD, module=deepcopy(module),
                      project={"status": "present", "name": "PRJ", "user": "SYSTEM", "project_number": "7", "aduuid": "project-ad-7"},
                      mdb={"status": "present", "name": "CAT-MDB", "element_is_null": False,
                           "element": {"refno": "=9/1", "type": "MDB", "db_type": "System", "db_type_name": "System", "db_type_code": 10}},
                      current_element={"status": "present", "is_null": False, "refno": CE["refno"], "type": CE["type"]})
    return reply


def native_spec(*, source):
    attributes = {name: attribute(name, value=observation(identity("=1/" + str(100 + index), kind, "Catalogue")))
                  for index, (name, kind) in enumerate(zip(REFERENCE_ATTRIBUTES, ("PTSE", "GMSE", "DTSE", "BTSE")))} if source else {}
    reply = bind(spec_sample(attributes))
    result = reply["result"]
    reference, kind, name = ("=1/10", "SCOM", "/OLD_SCOM") if source else ("=1/50", "CATE", "/CATEGORY")
    root = {**observation(identity(reference, kind, "Catalogue")), "name": name,
            "id": 0, "depth": 0, "parent_index": None, "sibling_index": None,
            "owner": identity("=1/50", "CATE", "Catalogue") if source else identity("=1/40", "SECT", "Catalogue"),
            "owner_error": None, "attributes": deepcopy(attributes), "complete": True}
    rows = [root]
    if not source:
        rows.append({**observation(identity("=1/10", "SCOM", "Catalogue")), "name": "/OLD_SCOM",
                     "id": 1, "depth": 1, "parent_index": 0, "sibling_index": 1,
                     "owner": identity("=1/50", "CATE", "Catalogue"), "owner_error": None, "attributes": {}, "complete": True})
    result.update(requested_refno=reference, expected_type=kind, database_type="Catalogue", database_type_code=2,
                  attributes=list(attributes), max_depth=1 if source else 16, limit=1 if source else 100, rows=rows, returned_count=len(rows))
    return reply


def native_schema():
    reply = bind(schema_sample(attributes=(), include=False))
    result = reply["result"]
    result.update(requested_refno=ARGS["parent_refno"], target={**identity("=1/50", "CATE", "Catalogue"),
                                                            "name": "/CATEGORY", "name_error": None})
    child = result["child_types"][0]
    child.update(name="SCOM", short_name="SCOM")
    result["child_types"] = [child]
    result["child_types_observation"].update(returned_count=1, total_count=1)
    return reply


def native_template():
    def type_observation(getter):
        return {"source": getter, "name": "SCOM", "short_name": "SCOM", "status": "observed", "errors": {}}

    def reference(name):
        return {"requested_name": name, "expected_shape": "element_or_element_array", "status": "unknown",
                "supported": False, "shape": None, "reference_null": None, "sdk_type": None, "is_array": None,
                "unit_kind": None, "unit": None, "unit_status": "unknown", "total_count": None,
                "returned_count": 0, "truncated": None, "inspection_limited": None,
                "native_array_materialized": False, "values": [],
                "errors": {"support": "unsupported_attribute_value_unknown"}, "complete": False}

    target = {**identity(ARGS["source_refno"], "SCOM", "Catalogue"), "name": "/OLD_SCOM", "name_error": None,
              "request_binding": {"source": "explicit_request_resolution", "requested_refno": ARGS["source_refno"],
                                  "resolved_refno": ARGS["source_refno"], "observed_name": None, "verified": True},
              "base_type": type_observation("DbElement.GetElementType"),
              "actual_type": type_observation("DbElement.GetActualType"), "errors": {}, "complete": True}
    parameters = {"requested_name": "PARAM", "expected_shape": "double_array", "status": "observed",
                  "supported": True, "sdk_type": "DOUBLE", "is_array": True, "unit_kind": "NONE",
                  "shape": "double_array", "reference_null": None, "unit": None, "unit_status": "unknown",
                  "total_count": 2, "returned_count": 2, "truncated": False, "inspection_limited": False,
                  "native_array_materialized": True,
                  "values": [{"index": index, "value": value, "status": "observed", "error": None}
                             for index, value in enumerate((1.5, -4.0))], "errors": {}, "complete": True}
    reply = bind({"ok": True, "version": "E3D21", "result": {
        "operation": "paragon_component_template", "available": True, "success": False, "complete": False,
        "read_only": True, "single_dispatch": True, "atomic_snapshot": False, "session_id": SESSION,
        "bridge_build": BUILD, "requested_refno": ARGS["source_refno"], "expected_base_type": "SCOM",
        "target": target, "leaf": {"source": "DbElement.Member(1)", "status": "observed", "is_leaf": True,
                                  "first_member": None, "complete": True, "errors": {}},
        "parameters": parameters, "references": {name: reference(name) for name in
                      ("PTREF", "GMREF", "DTREF", "BLRFA", "NGMREF", "PSTREF", "GSTREF")},
        "read_errors": [], "context": {"same_project_instance": True, "same_mdb_instance": True,
                                       "boundary_agreement": True},
        "output_limits": deepcopy(TEMPLATE_LIMITS), "scope": deepcopy(TEMPLATE_SCOPE)}})
    validate_template(reply, {"refno": ARGS["source_refno"]})
    return reply


@pytest.fixture
def receipts():
    return [healthy(), native_module(), native_spec(source=True), native_template(), native_spec(source=False), native_schema()]


class RecordingBridge:
    settings = SETTINGS

    def __init__(self, values):
        self.values = values
        self.events = []

    def _selected_version(self, version):
        assert version is None
        return self.settings.version or self.settings.default_version

    def _next(self, event):
        index = len(self.events)
        self.events.append(event)
        if index >= len(self.values):
            raise AssertionError("Unexpected preview, after-snapshot, execution or retry")
        value = self.values[index]
        if isinstance(value, BaseException):
            raise value
        return value

    def health(self, *, version):
        return self._next(("GET", "/health", None, version))

    def _request(self, method, path, payload=None, *, version):
        return self._next((method, path, deepcopy(payload), version))

    def __getattr__(self, name):
        raise AssertionError("Forbidden generation action/fallback: " + name)


def plan(receipts, args=None):
    bridge = RecordingBridge(receipts)
    response = paragon_generation_plan(bridge, ARGS if args is None else args)
    return response["result"], bridge


def partial_refs(source):
    row = source["result"]["rows"][0]
    row["attributes"]["GMREF"].update(status="unknown", typed_status="unknown", typed_value=None,
                                     reference_null=None, errors={"typed": "native getter failed"})
    row["complete"] = False
    source["result"].update(complete=False, success=False)


def test_exact_two_line_review_draft_and_six_read_workflow_never_submit_pml(receipts):
    originals = deepcopy(receipts)
    result, bridge = plan(receipts)
    assert result["status"] == "reviewable" and result["read_coverage_complete"] is True
    assert result["success"] is True and result["complete"] is True
    expected = "=1/50\nNEW SCOM /MCP/NEW_SCOM AFTER LAST MEMBER COPY =1/10\n"
    assert result["draft"]["source"] == expected
    assert result["draft"]["source_sha256"] == hashlib.sha256(expected.encode()).hexdigest()
    assert result["draft"]["byte_count"] == len(expected.encode()) and result["draft"]["line_count"] == 2
    assert [event[1] for event in bridge.events] == ["/health", MODULE_PATH, SPEC_PATH, TEMPLATE_PATH, SPEC_PATH, SCHEMA_PATH]
    assert bridge.events[2][2] == {"refno": "=1/10", "expected_type": "SCOM", "database_type": "Catalogue",
                                  "max_depth": 1, "limit": 1, "attributes": REFERENCE_ATTRIBUTES}
    assert bridge.events[3][2] == {"refno": "=1/10"}
    assert bridge.events[4][2] == {"refno": "=1/50", "expected_type": "CATE", "database_type": "Catalogue",
                                  "max_depth": 16, "limit": 100, "attributes": []}
    assert bridge.events[5][2] == {"refno": "=1/50", "attributes": [], "include_constraints": False}
    assert result["preview_request"]["arguments"] == {"source": expected, "dry_run": True, "expected_session_id": SESSION}
    assert result["execution_request"] is None and result["revision"] is None
    assert all(result[name] is False for name in ("execution_ready", "execution_supported", "execution_binding_enforced", "writes_attempted"))
    assert result["scope"]["permissions_verified"] is None
    assert result["scope"]["name_conflict_scope"] == "observed_current_parent_children_only"
    assert result["scope"]["name_global_uniqueness_verified"] is False
    for name in ("source_actual_type_observed", "source_actual_type_verified", "source_numeric_param_values_observed",
                 "source_numeric_param_coverage_complete"):
        assert result["scope"][name] is True
    for name in ("source_parameter_values_verified", "whole_clone_fingerprint", "runtime_copy_semantics_verified",
                 "runtime_syntax_verified", "geometry_generated", "geometry_verified", "reference_semantics_verified", "units_verified"):
        assert result["scope"][name] is False
    assert result["readback_contract"]["performed"] is False
    assert result["readback_contract"]["new_element"]["refno"] is None
    assert result["readback_contract"]["new_element"]["owner_refno"] == ARGS["parent_refno"]
    assert result["readback_contract"]["new_element"]["base_type"] == "SCOM"
    assert result["readback_contract"]["source_template_request"] == {
        "tool": "e3d_paragon_component_template", "arguments": {"refno": "=1/10"}}
    assert result["readback_contract"]["new_element_template_request"] == {
        "tool": "e3d_paragon_component_template", "request_ready": False, "required_argument": "refno",
        "refno_source": "independently_captured_new_numeric_ref"}
    assert receipts == originals
    assert all(result["evidence"][stage] is original for stage, original in zip(STAGES, receipts))


@pytest.mark.parametrize("change", [
    {"source_refno": "/SOURCE"}, {"source_refno": "CE"}, {"source_refno": "=01/10"}, {"source_refno": "=0/1"},
    {"source_refno": "=2147483648/1"}, {"source_refno": "=1/2147483648"}, {"source_refno": "=１/10"},
    {"parent_refno": "=1/050"}, {"parent_refno": " =1/50"}, {"parent_refno": "=1/50\n"},
    {"name": "/A.B"}, {"name": "/A B"}, {"name": "/A$B"}, {"name": "/A|B"}, {"name": "/A'B"},
    {"name": '/A"B'}, {"name": "/A;DELETE"}, {"name": "/A*"}, {"name": "/A?"}, {"name": "/A//B"},
    {"name": "/A/"}, {"name": "/1A"}, {"name": "/中文"}, {"name": "/A\n"}, {"name": "/" + "A" * 128},
    {"recipe": "pipe_component_seed_v1"}, {"recipe": None}, {"attributes": {}}, {"execute": True},
    {"expected_session_id": SESSION}, {"bridge_build": BUILD}, {"limit": 1},
])
def test_invalid_inputs_have_zero_bridge_io(receipts, change):
    bridge = RecordingBridge(receipts)
    with pytest.raises(ValueError):
        paragon_generation_plan(bridge, {**ARGS, **change})
    assert bridge.events == []


@pytest.mark.parametrize("args", [None, [], {}, {"source_refno": "=1/10"}])
def test_missing_or_nonobject_inputs_have_zero_io(receipts, args):
    bridge = RecordingBridge(receipts)
    with pytest.raises(ValueError):
        paragon_generation_plan(bridge, args)
    assert bridge.events == []


@pytest.mark.parametrize("name", ["/A", "/A/B-2_C", "/" + "A" * 127])
def test_names_and_int32_boundaries_are_literal_and_never_normalized(name):
    args = {"source_refno": "=2147483647/2147483647", "parent_refno": "=1/0", "name": name}
    assert _parameters(args) == {**args, "recipe": RECIPE}


@pytest.mark.parametrize("settings", [Settings(mode="mock", version="E3D21"), Settings(mode="bridge", version="E3D31")])
def test_mock_or_other_versions_are_unverified_zero_io_drafts(receipts, settings):
    bridge = RecordingBridge(receipts)
    bridge.settings = settings
    result = paragon_generation_plan(bridge, ARGS)["result"]
    assert result["status"] == "unavailable" and result["binding"] is None
    assert result["draft"]["source"] and result["preview_request"] is None
    assert result["execution_ready"] is False and result["read_coverage_complete"] is False
    assert bridge.events == [] and result["evidence"] == {}


def test_registration_only_exposes_fixed_read_only_recipe_and_dispatches_collector(receipts):
    bridge = RecordingBridge(receipts)
    server = McpServer("offline-generation-plan", "0")
    register_paragon_generation_plan(server, bridge)
    tool = server._tools[TOOL][0]
    assert set(tool["inputSchema"]["properties"]) == {"source_refno", "parent_refno", "name", "recipe"}
    assert tool["inputSchema"]["properties"]["recipe"]["const"] == RECIPE
    assert tool["annotations"] == {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
    output = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": TOOL, "arguments": ARGS}})["result"]
    assert output.get("isError") is not True and output["structuredContent"]["result"]["status"] == "reviewable"
    assert len(bridge.events) == 6


def test_source_reference_getter_partial_preserves_independent_required_slices(receipts):
    partial_refs(receipts[2])
    result, bridge = plan(receipts)
    assert len(bridge.events) == 6 and result["status"] == "reviewable"
    assert result["read_coverage_complete"] is True and result["evidence"]["source"]["result"]["complete"] is False
    assert result["evidence"]["source"]["result"]["rows"][0]["attributes"]["GMREF"]["typed_value"] is None
    assert result["scope"]["reference_semantics_verified"] is False


@pytest.mark.parametrize("number,name", [(41, "PARAGON"), (81, "DESIGN"), (11, "ADMIN")])
def test_wrong_module_is_known_block_and_stops_before_source_reads(receipts, number, name):
    receipts[1]["result"]["module"].update(number=number, name=name)
    for sample in ("before", "after"):
        receipts[1]["result"]["context"][sample]["module"].update(number=number, name=name)
    result, bridge = plan(receipts)
    assert result["status"] == "blocked" and result["preview_request"] is None and len(bridge.events) == 2
    assert result["scope"]["permissions_verified"] is None


@pytest.mark.parametrize("field", ["refno", "type", "db_type"])
def test_source_owner_requires_explicit_same_cate_and_catalogue_evidence(receipts, field):
    owner = receipts[2]["result"]["rows"][0]["owner"]
    if field == "refno":
        owner.update(refno="=1/51", identity_key="=1/51|CATE")
    elif field == "type":
        owner.update(type="SECT", identity_key="=1/50|SECT")
    else:
        owner.update(db_type="Design", db_type_code=1)
    result, bridge = plan(receipts)
    assert result["status"] == "blocked" and result["preview_request"] is None and len(bridge.events) == 3


def test_source_nonleaf_blocks_without_attempting_copy_or_further_sdk_reads(receipts):
    receipts[2]["result"].update(complete=False, success=False, traversal_complete=False, truncated=True,
                                 truncation_reasons=["limit"])
    result, bridge = plan(receipts)
    assert result["status"] == "blocked" and result["required_checks"]["source_leaf"] is False
    assert result["preview_request"] is None and len(bridge.events) == 3


def test_unknown_source_owner_does_not_clear_evidence_or_become_false(receipts):
    root = receipts[2]["result"]["rows"][0]
    root.update(owner=None, owner_error="SDK owner getter failed", complete=False)
    receipts[2]["result"].update(complete=False, success=False)
    result, _ = plan(receipts)
    assert result["status"] == "unknown" and result["preview_request"] is None
    assert result["required_checks"]["source_same_catalogue_cate_parent"] is None
    assert result["evidence"]["source"] is receipts[2]


def test_observed_name_collision_is_known_block_even_without_projectwide_uniqueness(receipts):
    receipts[4]["result"]["rows"][1]["name"] = ARGS["name"].lower()
    result, bridge = plan(receipts)
    assert result["status"] == "blocked" and result["required_checks"]["name_clear_in_parent"] is False
    assert result["preview_request"] is None and len(bridge.events) == 5
    assert result["scope"]["whole_project_coverage"] is False


def test_unknown_member_name_is_not_a_negative_collision_check(receipts):
    receipts[4]["result"]["rows"][1].update(name=None, name_error="SDK NAME failed", complete=False)
    receipts[4]["result"].update(success=False, complete=False)
    result, _ = plan(receipts)
    assert result["status"] == "unknown" and result["required_checks"]["name_clear_in_parent"] is None
    assert result["required_checks"]["source_name_stable_in_parent"] is None
    assert result["required_checks"]["source_leaf_in_parent"] is True
    assert result["scope"]["source_actual_type_verified"] is True
    assert result["scope"]["source_numeric_param_coverage_complete"] is True
    assert result["preview_request"] is None


def validate_parent_read(receipts):
    validate_spec(receipts[4], {"refno": ARGS["parent_refno"], "expected_type": "CATE",
                              "database_type": "Catalogue", "max_depth": 16, "limit": 100, "attributes": []})


def test_later_parent_source_name_drift_cannot_be_overridden_by_early_agreement(receipts):
    receipts[4]["result"]["rows"][1]["name"] = "/RENAMED_AFTER_TEMPLATE"
    validate_parent_read(receipts)
    original = deepcopy(receipts)
    result, bridge = plan(receipts)
    assert result["status"] == "unknown" and result["complete"] is False and len(bridge.events) == 6
    assert result["required_checks"]["source_template_name_agreement"] is True
    assert result["required_checks"]["source_listed_under_parent"] is True
    assert result["required_checks"]["source_name_stable_in_parent"] is False
    assert result["required_checks"]["source_leaf_in_parent"] is True
    assert result["scope"]["source_actual_type_verified"] is True
    assert result["scope"]["source_numeric_param_coverage_complete"] is True
    assert result["preview_request"] is None and result["evidence"]["target"] is receipts[4]
    assert receipts == original


def test_later_parent_source_child_cannot_be_overridden_by_early_leaf_observations(receipts):
    target = receipts[4]["result"]
    member = deepcopy(target["rows"][1])
    member.update(observation(identity("=1/71", "SCYL", "Catalogue")))
    member.update(name="/LATE_CHILD", id=2, depth=2, parent_index=1, sibling_index=1,
                  owner=identity(ARGS["source_refno"], "SCOM", "Catalogue"))
    target.update(rows=[*target["rows"], member], returned_count=3)
    validate_parent_read(receipts)
    original = deepcopy(receipts)
    result, bridge = plan(receipts)
    assert result["status"] == "unknown" and result["complete"] is False and len(bridge.events) == 6
    assert result["required_checks"]["source_leaf"] is True
    assert result["required_checks"]["source_template_leaf_agreement"] is True
    assert result["required_checks"]["source_listed_under_parent"] is True
    assert result["required_checks"]["source_leaf_in_parent"] is False
    assert result["required_checks"]["source_name_stable_in_parent"] is True
    assert result["scope"]["source_actual_type_verified"] is True
    assert result["scope"]["source_numeric_param_coverage_complete"] is True
    assert result["preview_request"] is None and result["evidence"]["target"] is receipts[4]
    assert receipts == original


@pytest.mark.parametrize("stage", [2, 3, 4])
def test_any_unknown_source_name_stays_unknown_without_filling_from_other_reads(receipts, stage):
    native = receipts[stage]["result"]
    row = native["target"] if stage == 3 else native["rows"][0 if stage == 2 else 1]
    row.update(name=None, name_error="SDK NAME failed", complete=False)
    if stage == 3:
        row["errors"]["name"] = row["name_error"]
    native.update(complete=False, success=False)
    validate_parent_read(receipts)
    original = deepcopy(receipts)
    result, _ = plan(receipts)
    assert result["status"] == "unknown" and result["required_checks"]["source_name_stable_in_parent"] is None
    assert result["required_checks"]["source_leaf_in_parent"] is True
    assert result["scope"]["source_actual_type_verified"] is True
    assert result["scope"]["source_numeric_param_coverage_complete"] is True
    assert result["preview_request"] is None and receipts == original


@pytest.mark.parametrize("under_source", [False, True])
def test_depth16_cutoff_cannot_prove_later_source_leaf_absence_but_preserves_observed_child(receipts, under_source):
    native = receipts[4]["result"]
    rows = native["rows"]
    if not under_source:
        branch = deepcopy(rows[1])
        branch.update(observation(identity("=1/70", "GMSE", "Catalogue")))
        branch.update(id=2, depth=1, parent_index=0, sibling_index=2, name="/GEOMETRY")
        rows.append(branch)
    for depth in range(2, 17):
        row = deepcopy(rows[-1])
        row.update(observation(identity(f"=1/{100 + depth}", "SCYL", "Catalogue")))
        row.update(id=len(rows), depth=depth, parent_index=rows[-1]["id"], sibling_index=1,
                   name=f"/CHILD_{depth}", owner={key: rows[-1][key] for key in
                                                 ("refno", "type", "db_type", "db_type_code", "identity_key")})
        rows.append(row)
    native.update(returned_count=len(rows), complete=False, success=False, traversal_complete=False,
                  truncated=True, truncation_reasons=["depth"])
    validate_parent_read(receipts)
    original = deepcopy(receipts)
    result, _ = plan(receipts)
    assert result["status"] == "unknown" and result["preview_request"] is None
    assert result["required_checks"]["source_listed_under_parent"] is None
    assert result["required_checks"]["source_name_stable_in_parent"] is True
    assert result["required_checks"]["source_leaf_in_parent"] is (False if under_source else None)
    assert result["scope"]["source_actual_type_verified"] is True
    assert result["scope"]["source_numeric_param_coverage_complete"] is True
    assert receipts == original


def test_complete_parent_without_source_cannot_claim_name_stability_or_leaf_evidence(receipts):
    native = receipts[4]["result"]
    native.update(rows=native["rows"][:1], returned_count=1)
    validate_parent_read(receipts)
    result, _ = plan(receipts)
    assert result["status"] == "unknown" and result["preview_request"] is None
    assert result["required_checks"]["source_listed_under_parent"] is False
    assert result["required_checks"]["source_name_stable_in_parent"] is None
    assert result["required_checks"]["source_leaf_in_parent"] is None


def test_truncated_target_prefix_cannot_prove_name_absence_or_source_membership(receipts):
    target = receipts[4]["result"]
    base = target["rows"][1]
    for index in range(2, 100):
        row = deepcopy(base)
        row.update(identity(f"=1/{1000 + index}", "SCOM", "Catalogue"))
        row.update(id=index, sibling_index=index, name=f"/CHILD_{index}")
        target["rows"].append(row)
    target.update(returned_count=100, complete=False, success=False, traversal_complete=False,
                  truncated=True, truncation_reasons=["limit"])
    result, bridge = plan(receipts)
    assert result["status"] == "unknown" and len(bridge.events) == 6
    assert result["required_checks"]["name_clear_in_parent"] is None
    assert result["required_checks"]["source_listed_under_parent"] is None
    assert result["required_checks"]["source_leaf_in_parent"] is None
    assert result["preview_request"] is None


def nested_target(receipts):
    target = receipts[4]["result"]
    source = deepcopy(target["rows"][1])
    gmse = deepcopy(source)
    gmse.update(observation(identity("=1/70", "GMSE", "Catalogue")))
    gmse.update(id=1, sibling_index=1, name="/GEOMETRY")
    cylinder = deepcopy(gmse)
    cylinder.update(observation(identity("=1/71", "SCYL", "Catalogue")))
    cylinder.update(id=2, depth=2, parent_index=1, sibling_index=1, name="/CYLINDER",
                    owner=identity("=1/70", "GMSE", "Catalogue"))
    source.update(id=3, sibling_index=2)
    target.update(rows=[target["rows"][0], gmse, cylinder, source], returned_count=4)
    return target


def test_complete_cate_geometry_descendants_do_not_hide_later_direct_scom_sibling(receipts):
    nested_target(receipts)
    result, bridge = plan(receipts)
    assert result["status"] == "reviewable" and result["read_coverage_complete"] is True
    assert result["required_checks"]["source_listed_under_parent"] is True
    assert result["required_checks"]["source_name_stable_in_parent"] is True
    assert result["required_checks"]["source_leaf_in_parent"] is True
    assert result["required_checks"]["name_clear_in_parent"] is True
    assert bridge.events[4][2]["max_depth"] == 16
    assert result["readback_contract"]["parent_request"]["arguments"]["max_depth"] == 16


def test_same_geometry_subtree_cutoff_cannot_prove_later_direct_child_names(receipts):
    target = nested_target(receipts)
    # A depth-16 chain may end before the source sibling. Retain the observed
    # prefix; neither name absence nor complete direct membership follows.
    target["rows"] = target["rows"][:3]
    for depth in range(3, 17):
        row = deepcopy(target["rows"][-1])
        row.update(observation(identity(f"=1/{100 + depth}", "SCYL", "Catalogue")))
        row.update(id=depth, depth=depth, parent_index=depth - 1, sibling_index=1,
                   owner={key: target["rows"][-1][key] for key in ("refno", "type", "db_type", "db_type_code", "identity_key")})
        target["rows"].append(row)
    target.update(returned_count=len(target["rows"]), traversal_complete=False, complete=False, success=False,
                  truncated=True, truncation_reasons=["depth"])
    result, _ = plan(receipts)
    assert result["status"] == "unknown" and result["preview_request"] is None
    assert result["required_checks"]["target_prefix_complete"] is False
    assert result["required_checks"]["name_clear_in_parent"] is None
    assert result["required_checks"]["source_name_stable_in_parent"] is None
    assert result["required_checks"]["source_leaf_in_parent"] is None


@pytest.mark.parametrize("field", ["sdk_creatable_now", "catalogue_supported", "member_type"])
def test_false_schema_observations_block_without_becoming_permission(field, receipts):
    native = receipts[5]["result"]
    child = native["child_types"][0]
    if field == "member_type":
        native["child_types"] = []
        native["child_types_observation"].update(total_count=0, returned_count=0)
    elif field == "catalogue_supported":
        child.update(database_type_codes=[1], catalogue_supported=False)
    else:
        child["sdk_creatable_now"] = False
    result, _ = plan(receipts)
    assert result["status"] == "blocked" and result["preview_request"] is None
    assert result["execution_ready"] is False and result["scope"]["permissions_verified"] is None


def test_unknown_creatability_remains_unknown_and_raw_partial_is_retained(receipts):
    native = receipts[5]["result"]
    native["child_types"][0].update(sdk_creatable_now=None, errors={"sdk_creatable_now": "SDK failure"}, complete=False)
    native["child_types_observation"]["complete"] = False
    native.update(complete=False, success=False)
    result, _ = plan(receipts)
    assert result["status"] == "unknown" and result["required_checks"]["scom_sdk_creatable_now"] is None
    assert result["evidence"]["parent_schema"] is receipts[5] and result["preview_request"] is None


@pytest.mark.parametrize("stage", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("field", ["version", "session_id", "bridge_build"])
def test_independent_health_provenance_cannot_be_filled_from_the_pending_receipt(receipts, stage, field):
    if field == "version":
        del receipts[stage][field]
    else:
        receipts[stage][field] = "33333333-3333-4333-8333-333333333333"
        for sample in ("before", "after"):
            receipts[stage]["result"]["context"][sample][field] = receipts[stage][field]
    result, bridge = plan(receipts)
    assert result["status"] == "unknown" and result["preview_request"] is None
    assert result["evidence"][STAGES[stage]] is receipts[stage] and len(bridge.events) == stage + 1


@pytest.mark.parametrize("stage", [2, 3, 4, 5])
@pytest.mark.parametrize("field", ["project", "mdb", "ce_refno", "ce_type", "ce_storage"])
def test_cross_endpoint_context_uses_common_module_shape_and_full_reader_typed_ce(receipts, stage, field):
    for sample in ("before", "after"):
        boundary_value = receipts[stage]["result"]["context"][sample]
        if field in ("project", "mdb"):
            boundary_value[field] = "CHANGED"
        else:
            current = boundary_value["current_element"]
            if field == "ce_refno":
                current.update(refno="=1/901", identity_key="=1/901|CATA")
            elif field == "ce_type":
                current.update(type="SECT", identity_key="=1/900|SECT")
            else:
                current.update(db_type="Design", db_type_code=1)
    result, bridge = plan(receipts)
    # The first reader establishes storage absent from module-context's CE shape;
    # subsequent readers must exactly match it, including storage type/code.
    if stage == 2 and field == "ce_storage":
        assert len(bridge.events) == 4
    else:
        assert len(bridge.events) == stage + 1
    assert result["status"] == "unknown" and result["preview_request"] is None


@pytest.mark.parametrize("feature", REQUIRED_FEATURES)
def test_missing_health_feature_is_unavailable_and_stops_before_module(receipts, feature):
    receipts[0]["api_features"].remove(feature)
    result, bridge = plan(receipts)
    assert result["status"] == "unavailable" and result["preview_request"] is None
    assert len(bridge.events) == 1


@pytest.mark.parametrize("path,value", [(("connected",), False), (("bridge_build",), "build"),
    (("session_id",), "session"), (("state", "database_ready"), None), (("state", "busy"), True),
    (("state", "recovery_required"), True), (("state", "native_database_fault"), None),
    (("state", "generated_csg_quarantine", "active"), True)])
def test_unready_health_never_becomes_an_authorized_generation_plan(receipts, path, value):
    target = receipts[0]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    result, bridge = plan(receipts)
    assert result["status"] == "unknown" and result["preview_request"] is None and len(bridge.events) == 1


MARKERS = [{"has_fault": True}, {"native_fault": True}, {"native_database_fault": True},
           {"native_fault_latched": True}, {"error_code": "native_fault"}, {"code": "native_database_fault"}]


@pytest.mark.parametrize("stage", range(6))
@pytest.mark.parametrize("marker", MARKERS)
@pytest.mark.parametrize("status", [None, 404, 500])
def test_fault_bridge_error_is_unchanged_before_404_and_no_later_sdk_read(receipts, stage, marker, status):
    failure = BridgeError("Native fault stop", http_status=status,
                          diagnostics={"nested": [deepcopy(marker)], "retry_safe": False})
    receipts[stage] = failure
    bridge = RecordingBridge(receipts)
    with pytest.raises(BridgeError) as caught:
        paragon_generation_plan(bridge, ARGS)
    assert caught.value is failure and len(bridge.events) == stage + 1


@pytest.mark.parametrize("stage", range(6))
@pytest.mark.parametrize("marker", MARKERS)
def test_raw_native_fault_precedes_all_shape_or_completeness_checks(receipts, stage, marker):
    raw = {"ok": False, "read_errors": ["invalid"] * 2000 + [deepcopy(marker)]}
    receipts[stage] = raw
    bridge = RecordingBridge(receipts)
    with pytest.raises(BridgeError) as caught:
        paragon_generation_plan(bridge, ARGS)
    assert caught.value.diagnostics is raw and len(bridge.events) == stage + 1


@pytest.mark.parametrize("stage", range(6))
def test_ordinary_404_keeps_original_diagnostics_and_does_not_fallback(receipts, stage):
    diagnostics = {"version": VERSION, "session_id": SESSION, "bridge_build": BUILD}
    receipts[stage] = BridgeError("Optional native feature missing", http_status=404, diagnostics=diagnostics)
    result, bridge = plan(receipts)
    assert result["status"] == "unavailable" and len(bridge.events) == stage + 1
    assert result["preview_request"] is None
    assert result["evidence"][STAGES[stage]]["bridge_error"]["diagnostics"] is diagnostics


VERSION = "E3D21"


def test_real_urlopen_wire_only_sends_six_read_requests_and_retains_decoded_native_evidence(receipts):
    responses = [io.BytesIO(json.dumps(value).encode()) for value in receipts]
    with patch("urllib.request.urlopen", side_effect=responses) as urlopen:
        result = paragon_generation_plan(BridgeClient(SETTINGS), ARGS)["result"]
    assert result["status"] == "reviewable" and urlopen.call_count == 6
    requests = [call.args[0] for call in urlopen.call_args_list]
    assert [item.get_method() for item in requests] == ["GET", "GET", "POST", "POST", "POST", "POST"]
    assert [item.full_url for item in requests] == [SETTINGS.bridge_url + path for path in (
        "/health", MODULE_PATH, SPEC_PATH, TEMPLATE_PATH, SPEC_PATH, SCHEMA_PATH)]
    assert all("pml-script" not in item.full_url for item in requests)
    assert result["evidence"]["source"] == receipts[2]


@pytest.mark.parametrize("short_name", [":CUSTOM_SCOM", "SCOU", "SCYL"])
def test_known_actual_type_mismatch_blocks_before_target_or_schema(receipts, short_name):
    target = receipts[3]["result"]["target"]
    target["actual_type"].update(name=short_name, short_name=short_name)
    original = deepcopy(receipts)
    validate_template(receipts[3], {"refno": ARGS["source_refno"]})
    result, bridge = plan(receipts)
    assert result["status"] == "blocked" and len(bridge.events) == 4
    assert result["required_checks"]["source_actual_type_scom"] is False
    assert result["scope"]["source_actual_type_observed"] is True
    assert result["scope"]["source_actual_type_verified"] is False
    assert result["preview_request"] is None and receipts == original


@pytest.mark.parametrize("getter", ["actual_type", "base_type"])
def test_unknown_type_does_not_infer_actual_from_base_or_enable_preview(receipts, getter):
    target = receipts[3]["result"]["target"]
    target[getter] = None
    target["errors"][getter] = "SDK type getter failed"
    target["complete"] = False
    validate_template(receipts[3], {"refno": ARGS["source_refno"]})
    result, bridge = plan(receipts)
    assert result["status"] == "unknown" and len(bridge.events) == 6
    assert result["required_checks"]["source_actual_type_scom"] is None
    assert result["scope"]["source_actual_type_verified"] is False
    assert result["scope"]["source_numeric_param_coverage_complete"] is True
    assert result["preview_request"] is None


def test_actual_short_name_can_be_observed_when_optional_long_type_name_failed(receipts):
    target = receipts[3]["result"]["target"]
    target["actual_type"].update(name=None, status="unknown", errors={"name": "SDK type NAME failed"})
    target["complete"] = False
    validate_template(receipts[3], {"refno": ARGS["source_refno"]})
    result, _ = plan(receipts)
    assert result["status"] == "reviewable" and result["scope"]["source_actual_type_verified"] is True
    assert result["evidence"]["source_template"]["result"]["complete"] is False


@pytest.mark.parametrize("stage", [2, 3])
def test_name_getter_failure_keeps_independent_numeric_actual_and_leaf_evidence(receipts, stage):
    native = receipts[stage]["result"]
    row = native["target"] if stage == 3 else native["rows"][0]
    row.update(name=None, name_error="SDK NAME failed", complete=False)
    if stage == 3:
        row["errors"]["name"] = row["name_error"]
        validate_template(receipts[stage], {"refno": ARGS["source_refno"]})
    native.update(complete=False, success=False)
    original = deepcopy(receipts)
    result, _ = plan(receipts)
    assert result["status"] == "unknown" and result["preview_request"] is None
    assert result["required_checks"]["source_template_name_agreement"] is None
    assert result["required_checks"]["source_template_numeric_identity_verified"] is True
    assert result["required_checks"]["source_template_leaf_agreement"] is True
    assert result["scope"]["source_actual_type_verified"] is True
    assert result["scope"]["source_numeric_param_coverage_complete"] is True
    assert result["evidence"][STAGES[stage]] is receipts[stage] and receipts == original


def test_known_cross_reader_name_drift_cannot_become_complete(receipts):
    receipts[3]["result"]["target"]["name"] = "/RENAMED_BETWEEN_READS"
    result, _ = plan(receipts)
    assert result["status"] == "unknown" and result["complete"] is False
    assert result["required_checks"]["source_template_name_agreement"] is False
    assert result["scope"]["source_actual_type_verified"] is True
    assert result["preview_request"] is None


@pytest.mark.parametrize("change", ["truncated", "inspection_limited", "nonfinite", "null", "unknown_metadata", "wrong_shape"])
def test_numeric_param_partial_or_unknown_never_becomes_reviewable(receipts, change):
    row = receipts[3]["result"]["parameters"]
    row.update(complete=False, status="partial")
    if change == "truncated":
        row.update(total_count=129, returned_count=128, truncated=True,
                   values=[{"index": index, "value": float(index), "status": "observed", "error": None}
                           for index in range(128)], errors={"output_limit": "Native array exceeds output bound"})
    elif change == "inspection_limited":
        row.update(status="unknown", total_count=4097, returned_count=0, truncated=True, inspection_limited=True,
                   values=[], errors={"inspection_limit": "Native array exceeds inspection bound"})
    elif change == "nonfinite":
        row["values"][1].update(value=None, status="unknown", error="non_finite_native_value")
        row["errors"] = {"non_finite": "Native DOUBLE is non-finite"}
    else:
        row.update(status="unknown", total_count=None, returned_count=0, truncated=None, inspection_limited=None,
                   native_array_materialized=False, values=[], errors={"value": "Native array unavailable"})
        if change == "unknown_metadata":
            row.update(sdk_type=None, shape=None)
            row["errors"]["sdk_type"] = "SDK metadata getter failed"
        elif change == "wrong_shape":
            row.update(sdk_type="INTEGER", shape=None)
    validate_template(receipts[3], {"refno": ARGS["source_refno"]})
    original = deepcopy(receipts)
    result, bridge = plan(receipts)
    assert result["status"] == "unknown" and len(bridge.events) == 6
    assert result["scope"]["source_actual_type_verified"] is True
    assert result["scope"]["source_numeric_param_coverage_complete"] is False
    assert result["required_checks"]["source_numeric_param_coverage_complete"] is None
    assert result["scope"]["source_parameter_values_verified"] is False
    assert result["preview_request"] is None and result["evidence"]["source_template"] is receipts[3]
    assert receipts == original


def test_observed_empty_numeric_param_array_is_complete_within_numeric_scope(receipts):
    receipts[3]["result"]["parameters"].update(total_count=0, returned_count=0, values=[])
    validate_template(receipts[3], {"refno": ARGS["source_refno"]})
    result, _ = plan(receipts)
    assert result["status"] == "reviewable"
    assert result["scope"]["source_numeric_param_values_observed"] is True
    assert result["scope"]["source_numeric_param_coverage_complete"] is True
    assert result["scope"]["source_parameter_values_verified"] is False


def test_unit_kind_getter_partial_preserves_complete_numeric_param_slice(receipts):
    raw = receipts[3]
    row = raw["result"]["parameters"]
    row.update(unit_kind=None, status="partial", complete=False, errors={"unit_kind": "SDK Units getter failed"})
    validate_template(raw, {"refno": ARGS["source_refno"]})
    original = deepcopy(raw)
    result, bridge = plan(receipts)
    assert result["status"] == "reviewable" and len(bridge.events) == 6
    assert result["required_checks"]["source_numeric_param_coverage_complete"] is True
    assert result["scope"]["source_numeric_param_values_observed"] is True
    assert result["scope"]["source_numeric_param_coverage_complete"] is True
    assert result["scope"]["units_verified"] is False and row["unit_status"] == "unknown"
    assert result["scope"]["source_parameter_values_verified"] is False
    assert result["evidence"]["source_template"] is raw and raw["result"]["complete"] is False
    assert raw == original


@pytest.mark.parametrize("error", ["attribute", "support", "sdk_type", "is_array", "value", "non_finite",
                                  "inspection_limit", "output_limit"])
def test_unit_kind_failure_does_not_let_forged_value_or_shape_errors_pass_whole_raw_validation(receipts, error):
    raw = receipts[3]
    row = raw["result"]["parameters"]
    row.update(unit_kind=None, status="partial", complete=False, errors={"unit_kind": "SDK Units getter failed",
                                                                      error: "Numeric evidence not verified"})
    original = deepcopy(raw)
    with pytest.raises(ValueError, match="Invalid PARAGON component template evidence"):
        validate_template(raw, {"refno": ARGS["source_refno"]})
    result, bridge = plan(receipts)
    assert result["status"] == "unknown" and len(bridge.events) == 4 and result["preview_request"] is None
    assert result["scope"]["source_numeric_param_coverage_complete"] is False
    assert result["evidence"]["source_template"] is raw and raw == original


@pytest.mark.parametrize("error", ["value", "sdk_type", "is_array"])
def test_unit_kind_and_valid_numeric_read_failure_cannot_claim_coverage(receipts, error):
    raw = receipts[3]
    row = raw["result"]["parameters"]
    row.update(unit_kind=None, status="unknown", complete=False, total_count=None, returned_count=0,
               truncated=None, inspection_limited=None, native_array_materialized=False, values=[],
               errors={"unit_kind": "SDK Units getter failed", "value": "Numeric value not observed"})
    if error != "value":
        row[error] = None
        row["shape"] = None
        row["errors"][error] = "Numeric metadata not observed"
    validate_template(raw, {"refno": ARGS["source_refno"]})
    result, bridge = plan(receipts)
    assert result["status"] == "unknown" and len(bridge.events) == 6 and result["preview_request"] is None
    assert result["scope"]["source_numeric_param_coverage_complete"] is False
    assert result["scope"]["units_verified"] is False


@pytest.mark.parametrize("name", ["PTREF", "GMREF", "DTREF", "BLRFA", "NGMREF", "PSTREF", "GSTREF"])
def test_optional_reference_null_or_unsupported_does_not_gate_numeric_actual_leaf_slices(receipts, name):
    row = receipts[3]["result"]["references"][name]
    row.update(supported=True, sdk_type="ELEMENT", is_array=False, unit_kind="NONE", shape="element",
               reference_null=True, total_count=1, returned_count=1, truncated=False, inspection_limited=False,
               status="partial", errors={"items": "Reference identity unavailable"},
               values=[{"index": 0, "status": "unknown", "identity": None, "reference_null": True,
                        "error": "native_null_reference_value_unknown"}])
    validate_template(receipts[3], {"refno": ARGS["source_refno"]})
    original = deepcopy(receipts)
    result, _ = plan(receipts)
    assert result["status"] == "reviewable" and result["read_coverage_complete"] is True
    assert result["evidence"]["source_template"]["result"]["complete"] is False
    assert result["scope"]["reference_semantics_verified"] is False and receipts == original


@pytest.mark.parametrize("layer", ["envelope", "result", "before", "after"])
@pytest.mark.parametrize("field", ["session_id", "bridge_build"])
def test_template_each_provenance_layer_is_independently_bound_to_health(receipts, layer, field):
    raw = receipts[3]
    row = raw if layer == "envelope" else raw["result"] if layer == "result" else raw["result"]["context"][layer]
    row[field] = "33333333-3333-4333-8333-333333333333"
    original = deepcopy(raw)
    result, bridge = plan(receipts)
    assert result["status"] == "unknown" and result["preview_request"] is None and len(bridge.events) == 4
    assert result["evidence"]["source_template"] is raw and raw == original


@pytest.mark.parametrize("field,value", [("requested_refno", "=1/11"), ("resolved_refno", "=1/11"),
    ("observed_name", "/INFERRED"), ("source", "inferred"), ("verified", False)])
def test_template_first_numeric_binding_cannot_be_forged_or_replaced(receipts, field, value):
    receipts[3]["result"]["target"]["request_binding"][field] = value
    result, bridge = plan(receipts)
    assert result["status"] == "unknown" and len(bridge.events) == 4 and result["preview_request"] is None


@pytest.mark.parametrize("field,value", [("value", float("inf")), ("value", float("nan")),
    ("value", True), ("index", 1), ("status", "unknown")])
def test_invalid_numeric_param_item_is_not_repaired_from_other_evidence(receipts, field, value):
    receipts[3]["result"]["parameters"]["values"][0][field] = value
    result, bridge = plan(receipts)
    assert result["status"] == "unknown" and len(bridge.events) == 4 and result["preview_request"] is None


def test_production_template_receipts_preserve_partial_observations_through_plan(component_template_receipts):
    """Production readers run against managed fakes only; no AVEVA assembly executes."""
    statuses = set()
    positive_partial = drift = units_partial = 0
    for case in component_template_receipts:
        raw = deepcopy(case["reply"])
        raw.update(session_id=SESSION, bridge_build=BUILD)
        native = raw["result"]
        native.update(session_id=SESSION, bridge_build=BUILD)
        for side in ("before", "after"):
            native["context"][side].update(session_id=SESSION, bridge_build=BUILD)
        validate_template(raw, case["payload"])
        request = {**ARGS, "source_refno": case["payload"]["refno"]}
        values = [healthy(), native_module(), native_spec(source=True), raw, native_spec(source=False), native_schema()]
        source = values[2]["result"]
        source.update(requested_refno=request["source_refno"])
        source["rows"][0].update(identity(request["source_refno"], "SCOM", "Catalogue"))
        source_name = native["target"]["name"] if native["target"] is not None else None
        if source_name is not None:
            source["rows"][0]["name"] = source_name
        values[4]["result"]["rows"][1].update(identity(request["source_refno"], "SCOM", "Catalogue"))
        values[4]["result"]["rows"][1]["name"] = source["rows"][0]["name"]
        for index in (2, 4, 5):
            for side in ("before", "after"):
                values[index]["result"]["context"][side].update(
                    project="PROJECT", mdb="MDB", current_element=identity("=1/9", "EQUI", "Design"))
        for side in ("before", "after"):
            sample = values[1]["result"]["context"][side]
            sample["project"]["name"] = "PROJECT"
            sample["mdb"]["name"] = "MDB"
            sample["current_element"].update(refno="=1/9", type="EQUI")
        original = deepcopy(raw)
        result, bridge = plan(values, request)
        statuses.add(result["status"])
        assert result["evidence"]["source_template"] is raw and raw == original
        target = native["target"]
        if target and target["actual_type"] and target["actual_type"]["short_name"] == ":CUSTOM_SCOM":
            assert result["status"] == "blocked" and len(bridge.events) == 4
        if target and "request_binding" in target["errors"]:
            drift += 1
            assert result["complete"] is False and result["preview_request"] is None
            assert result["scope"]["source_actual_type_verified"] is False
            assert result["scope"]["source_numeric_param_values_observed"] is False
        if result["status"] == "reviewable":
            assert target["actual_type"]["short_name"] == target["base_type"]["short_name"] == "SCOM"
            assert native["parameters"]["errors"].keys() <= {"unit_kind"} and native["leaf"]["is_leaf"] is True
            if native["parameters"]["errors"].keys() == {"unit_kind"}:
                units_partial += 1
                assert native["parameters"]["complete"] is False and native["parameters"]["status"] == "partial"
                assert result["scope"]["source_numeric_param_coverage_complete"] is True
                assert result["scope"]["units_verified"] is False
            if native["complete"] is False:
                positive_partial += 1
    assert statuses == {"reviewable", "blocked", "unknown"}
    assert positive_partial >= 4 and drift >= 1 and units_partial >= 1

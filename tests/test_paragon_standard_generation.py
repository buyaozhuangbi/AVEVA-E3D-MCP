"""Public standards generation through real registration, decoding and validators.

Only urllib's transport is replaced. No SDK, PML, model write, health probe or
status recovery is allowed beyond the exact per-basis request sequence below.
"""
from __future__ import annotations

from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import urllib.error
import urllib.parse
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.paragon_component_copy import PATH as COPY_PATH, RECIPE, _validate as validate_copy
from aveva_e3d_mcp.paragon_component_parameters import PATH as PARAM_PATH, _validate as validate_parameters
from aveva_e3d_mcp.paragon_reference_trace import PATH as TRACE_PATH, validate_paragon_reference_trace_receipt
from aveva_e3d_mcp.tools import create_server
from test_paragon_component_copy import (
    ARGS as COPY_ARGS, BUILD, NEW, PARENT, REQUEST_ID, REVISION, SESSION, SOURCE,
    executed as copied, prepared as copy_prepared,
)
from test_paragon_component_parameters import (
    bits, prepared as parameter_prepared, updated as parameters_updated, wire as parameter_wire,
)


TOOL = "e3d_paragon_generate_from_standard"
SETTINGS = Settings(mode="bridge", version="E3D21", allow_commands=True)
SPEC, SPCO, SECTION, CATALOGUE = "=7/201", "=7/202", "=7/90", "=7/80"
PARAMETER_ID = "b4444444-4444-4444-8444-444444444444"
PARAMETER_REVISION = "c" * 64
NAME = COPY_ARGS["name"]
E3D_BASIS = {"kind": "e3d_specification", "spec_refno": SPEC, "spco_refno": SPCO}
EXTERNAL_BASIS = {"kind": "external_standard", "standard": "ASME B16.5", "edition": "2020",
    "locator": "Table 7, explicit selected row", "document_sha256": "d" * 64, "source_refno": SOURCE,
    "mapping": [
        {"parameter_index": 1, "quantity": "diameter", "source_value": 1.0, "source_unit": "mm", "param_unit": "mm"},
        {"parameter_index": 2, "quantity": "ratio", "source_value": -0.0, "source_unit": "1", "param_unit": "1"},
        {"parameter_index": 3, "quantity": "length", "source_value": 2.0, "source_unit": "in", "param_unit": "mm"},
    ]}
CONVERTED = [1.0, -0.0, 50.8]
RECEIPT_KEYS = {"health", "reference_trace", "copy_preview", "copy_execute", "parameters_preview", "parameters_execute"}


def arguments(kind="e3d_specification"):
    return {"basis": deepcopy(E3D_BASIS if kind == "e3d_specification" else EXTERNAL_BASIS),
            "parent_refno": PARENT, "name": NAME}


def trace():
    rows = {
        "root": {"ref": SPCO, "type": "SPCO", "owner": SPEC, "status": "resolved"},
        "root.CATREF": {"ref": SOURCE, "type": "SCOM", "owner": PARENT, "status": "resolved"},
        "root.CATREF.OWNER": {"ref": PARENT, "type": "CATE", "owner": SECTION, "status": "resolved"},
        "root.CATREF.OWNER.OWNER": {"ref": SECTION, "type": "SECT", "owner": CATALOGUE, "status": "resolved"},
        "root.CATREF.OWNER.OWNER.OWNER": {"ref": CATALOGUE, "type": "CATA", "owner": None, "status": "resolved"},
        "root.PRTREF": {"ref": None, "type": None, "owner": None, "status": "unset"},
        "root.CMPREF": {"ref": None, "type": None, "owner": None, "status": "unset"},
        "root.OWNER": {"ref": SPEC, "type": "SPEC", "owner": None, "status": "resolved"},
        "root.OWNER.MATREF": {"ref": None, "type": None, "owner": None, "status": "unset"},
        "root.OWNER.FLUREF": {"ref": None, "type": None, "owner": None, "status": "unset"},
    }
    first = {"session_id": SESSION, "bridge_build": BUILD, "project": "PLANT", "mdb": "CATALOGUE-MDB",
             "current_element": {"refno": PARENT, "type": "CATE", "db_type_code": 2}}
    return {"ok": True, "version": "E3D21", "session_id": SESSION, "bridge_build": BUILD, "result": {
        "status": "observed", "trace_limit": {"limit": 32, "used": len(rows), "reached": False}, "trace": rows,
        "db_type_codes": {path: 2 for path, row in rows.items() if row["status"] == "resolved"},
        "context": {"before": first, "after": deepcopy(first), "same_project_instance": True,
                    "same_mdb_instance": True, "boundary_agreement": True}}}


def health():
    return {"ok": True, "version": "E3D21", "session_id": SESSION, "bridge_build": BUILD, "connected": True,
            "api_features": ["paragon_component_copy", "paragon_component_parameters"]}


def copy_preview():
    return copy_prepared()


def copy_execution():
    return copied(journal=False)


def parameter_preview(values=CONVERTED):
    reply = parameter_prepared(values)
    reply["result"]["scoped_revision"] = PARAMETER_REVISION
    return reply


def parameter_execution(values=CONVERTED):
    reply = parameters_updated(values, journal=False)
    reply["result"].update(request_id=PARAMETER_ID, scoped_revision=PARAMETER_REVISION)
    return reply


def copy_payload(*, execute=False):
    payload = {**COPY_ARGS, "recipe": RECIPE, "dry_run": not execute}
    if execute:
        payload.update(request_id=REQUEST_ID, expected_session_id=SESSION, expected_bridge_build=BUILD,
                       expected_revision=REVISION)
    return payload


def parameter_payload(*, execute=False, values=CONVERTED):
    payload = {"refno": NEW, "parameters": list(values), "dry_run": not execute}
    if execute:
        payload.update(request_id=PARAMETER_ID, expected_session_id=SESSION, expected_bridge_build=BUILD,
                       expected_revision=PARAMETER_REVISION)
    return parameter_wire(payload)


def sequence(kind, *, execute=False, values=CONVERTED):
    steps = []
    if kind == "e3d_specification":
        steps.append((TRACE_PATH, {"refno": SPCO, "trace_limit": 32}, trace()))
    else:
        steps.append(("/health", None, health()))
    steps.append((COPY_PATH, copy_payload(), copy_preview()))
    if execute:
        steps.append((COPY_PATH, copy_payload(execute=True), copy_execution()))
        if kind == "external_standard":
            steps.append((PARAM_PATH, parameter_payload(values=values), parameter_preview(values)))
            steps.append((PARAM_PATH, parameter_payload(execute=True, values=values), parameter_execution(values)))
    return steps


def call(server, args):
    frame = server._dispatch({"jsonrpc": "2.0", "id": 91, "method": "tools/call",
                             "params": {"name": TOOL, "arguments": args}})
    assert frame["jsonrpc"] == "2.0" and frame["id"] == 91
    return frame["result"]


class Wire:
    def __init__(self, steps, *, failure=None):
        self.steps, self.failure, self.calls, self.received = steps, failure, [], []

    def __call__(self, request, **kwargs):
        index = len(self.calls)
        assert index < len(self.steps), "No additional write, status, health, diagnosis, fallback or retry is permitted"
        path, payload, reply = self.steps[index]
        url = urllib.parse.urlsplit(request.full_url)
        assert request.method == ("GET" if path == "/health" else "POST") and request.full_url.startswith(SETTINGS.bridge_url)
        assert url.path == path and not url.query
        if path == "/health":
            assert request.data is None
        else:
            assert request.get_header("Content-type") == "application/json"
            assert json.loads(request.data) == payload
        self.calls.append((request.method, url.path, deepcopy(payload)))
        if self.failure is not None:
            failed = self.failure(index, path, payload)
            if failed is not None:
                if isinstance(failed, BaseException):
                    raise failed
                return io.BytesIO(failed)
        self.received.append(deepcopy(reply))
        return io.BytesIO(json.dumps(reply, allow_nan=False).encode("utf-8"))


def run(args, steps, *, failure=None, settings=SETTINGS):
    snapshots = deepcopy(steps)
    outgoing = Wire(steps, failure=failure)
    server = create_server(settings)
    with patch("urllib.request.urlopen", side_effect=outgoing) as transport:
        result = call(server, args)
    assert transport.call_count == len(outgoing.calls)
    assert steps == snapshots
    return result, outgoing


def prepare(kind="e3d_specification", *, steps=None, args=None):
    actual_args = arguments(kind) if args is None else args
    response, outgoing = run(actual_args, sequence(kind) if steps is None else steps)
    result = response.get("structuredContent", {}).get("result", {})
    assert response.get("isError") is not True, response
    assert result["status"] == "prepared" and result["success"] is True and result["complete"] is True
    assert len(result["scoped_revision"]) == 64
    return response, outgoing


def execution_arguments(kind, prepared_response):
    args = {**arguments(kind), "dry_run": False, "request_id": REQUEST_ID,
            "expected_session_id": SESSION, "expected_bridge_build": BUILD,
            "expected_revision": prepared_response["structuredContent"]["result"]["scoped_revision"]}
    if kind == "external_standard":
        args["parameter_request_id"] = PARAMETER_ID
    return args


def result_of(response):
    return response.get("structuredContent", {}).get("result", {})


def assert_raw_receipts(result, expected):
    assert set(result["receipts"]) == RECEIPT_KEYS
    assert result["receipts"] == {key: expected.get(key) for key in RECEIPT_KEYS}


def test_independent_wire_fixtures_satisfy_all_three_production_validators():
    raw_trace, preview, copied_reply = trace(), copy_preview(), copy_execution()
    validate_paragon_reference_trace_receipt(raw_trace, {"refno": SPCO, "trace_limit": 32})
    validate_copy(preview, copy_payload())
    validate_copy(copied_reply, copy_payload(execute=True))
    validate_parameters(parameter_preview(), parameter_payload())
    validate_parameters(parameter_execution(), parameter_payload(execute=True))


def test_public_registration_is_closed_and_does_not_probe_bridge_during_catalog_creation():
    with patch("urllib.request.urlopen", side_effect=AssertionError("No catalog I/O")) as transport:
        server = create_server(SETTINGS)
    transport.assert_not_called()
    definition = server._tools[TOOL][0]
    assert definition["inputSchema"]["additionalProperties"] is False
    assert definition["annotations"]["readOnlyHint"] is False
    assert definition["annotations"]["idempotentHint"] is False


@pytest.mark.parametrize("kind", ["e3d_specification", "external_standard"])
def test_public_prepare_preserves_raw_basis_and_copy_receipts_without_submitting_writes(kind):
    response, outgoing = prepare(kind)
    result = result_of(response)
    assert response["structuredContent"]["version"] == "E3D21"
    assert response["structuredContent"]["session_id"] == SESSION
    assert response["structuredContent"]["bridge_build"] == BUILD
    assert result["operation"] == "paragon_generate_from_standard"
    assert result["known_new_refno"] is None
    assert result["parameters"] == (CONVERTED if kind == "external_standard" else None)
    expected = {"copy_preview": copy_preview()}
    if kind == "e3d_specification":
        expected["reference_trace"] = trace()
    else:
        expected["health"] = health()
    assert_raw_receipts(result, expected)
    assert len(outgoing.calls) == 2


@pytest.mark.parametrize("kind", ["e3d_specification", "external_standard"])
def test_public_execute_rechecks_preflight_and_binds_only_verified_copy_ref_to_parameters(kind):
    prepared_response, _ = prepare(kind)
    response, outgoing = run(execution_arguments(kind, prepared_response), sequence(kind, execute=True))
    result = result_of(response)
    assert response.get("isError") is not True, response
    assert result["status"] == "executed" and result["success"] is True and result["complete"] is True
    assert result["known_new_refno"] == NEW
    expected = {"copy_preview": copy_preview(), "copy_execute": copy_execution()}
    if kind == "e3d_specification":
        expected["reference_trace"] = trace()
        assert len(outgoing.calls) == 3
    else:
        expected.update(health=health(), parameters_preview=parameter_preview(), parameters_execute=parameter_execution())
        assert len(outgoing.calls) == 5
        assert outgoing.calls[-1][2]["refno"] == copy_execution()["result"]["progress"]["known_new_refno"]
        assert outgoing.calls[-1][2]["request_id"] == PARAMETER_ID != REQUEST_ID
        assert outgoing.calls[-1][2]["parameter_bits"] == bits(CONVERTED)
    assert_raw_receipts(result, expected)


@pytest.mark.parametrize("bad", [
    {}, {**arguments(), "unexpected": "caller"}, {**arguments(), "basis": None},
    {**arguments(), "parent_refno": "CE"}, {**arguments(), "parent_refno": "=01/1"},
    {**arguments(), "name": "/A;NEW"}, {**arguments(), "name": "/A/1B"},
    {**arguments(), "dry_run": False}, {**arguments(), "dry_run": "false"},
    *[{**arguments(), key: None} for key in ("request_id", "expected_session_id", "expected_bridge_build", "expected_revision")],
    {**arguments(), "basis": {**E3D_BASIS, "kind": "SPEC"}},
    {**arguments(), "basis": {**E3D_BASIS, "spco_refno": "/NAME"}},
    {**arguments(), "basis": {**E3D_BASIS, "spec_refno": "=2147483648/1"}},
    {**arguments(), "basis": {**E3D_BASIS, "source_refno": SOURCE}},
    {**arguments("external_standard"), "basis": {**EXTERNAL_BASIS, "mapping": []}},
    {**arguments("external_standard"), "basis": {**EXTERNAL_BASIS, "mapping": [{**EXTERNAL_BASIS["mapping"][0], "parameter_index": 0}]}},
    {**arguments("external_standard"), "basis": {**EXTERNAL_BASIS, "mapping": [{**EXTERNAL_BASIS["mapping"][0], "parameter_index": True}]}},
    {**arguments("external_standard"), "basis": {**EXTERNAL_BASIS, "mapping": [{**EXTERNAL_BASIS["mapping"][0], "source_value": True}]}},
    {**arguments("external_standard"), "basis": {**EXTERNAL_BASIS, "mapping": [{**EXTERNAL_BASIS["mapping"][0], "source_value": float("inf")}]}},
    {**arguments("external_standard"), "basis": {**EXTERNAL_BASIS, "mapping": [{**EXTERNAL_BASIS["mapping"][0], "source_unit": "cm"}]}},
    {**arguments("external_standard"), "basis": {**EXTERNAL_BASIS, "mapping": [{**EXTERNAL_BASIS["mapping"][0], "param_unit": "kg"}]}},
    {**arguments("external_standard"), "basis": {**EXTERNAL_BASIS, "document_sha256": "A" * 64}},
])
def test_public_invalid_arguments_are_rejected_before_io(bad):
    server = create_server(SETTINGS)
    with patch("urllib.request.urlopen", side_effect=AssertionError("No I/O for invalid input")) as transport:
        response = call(server, bad)
    transport.assert_not_called()
    assert response.get("isError") is True


@pytest.mark.parametrize("index_order", [[1, 1, 2], [1, 3, 4], [2, 3, 4]])
def test_external_mapping_must_cover_complete_contiguous_positions(index_order):
    args = arguments("external_standard")
    for row, index in zip(args["basis"]["mapping"], index_order):
        row["parameter_index"] = index
    server = create_server(SETTINGS)
    with patch("urllib.request.urlopen", side_effect=AssertionError("No I/O for invalid mapping")) as transport:
        response = call(server, args)
    transport.assert_not_called()
    assert response.get("isError") is True


@pytest.mark.parametrize("kind", ["e3d_specification", "external_standard"])
def test_execute_requires_command_permission_before_any_preflight_read(kind):
    preview, _ = prepare(kind)
    server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=False))
    with patch("urllib.request.urlopen", side_effect=AssertionError("No I/O when execution is disabled")) as transport:
        response = call(server, execution_arguments(kind, preview))
    transport.assert_not_called()
    assert response.get("isError") is True


def test_external_execute_requires_a_distinct_explicit_parameter_request_id():
    preview, _ = prepare("external_standard")
    for replacement in (None, REQUEST_ID):
        args = execution_arguments("external_standard", preview)
        if replacement is None:
            del args["parameter_request_id"]
        else:
            args["parameter_request_id"] = replacement
        server = create_server(SETTINGS)
        with patch("urllib.request.urlopen", side_effect=AssertionError("No I/O without a distinct parameter ID")) as transport:
            response = call(server, args)
        transport.assert_not_called()
        assert response.get("isError") is True


@pytest.mark.parametrize("field,value", [("expected_revision", "f" * 64), ("expected_session_id", "another-session"),
                                      ("expected_bridge_build", "f5555555-5555-4555-8555-555555555555")])
@pytest.mark.parametrize("kind", ["e3d_specification", "external_standard"])
def test_execute_detects_replayed_revision_or_context_pin_before_any_write(kind, field, value):
    preview, _ = prepare(kind)
    args = execution_arguments(kind, preview)
    args[field] = value
    steps = sequence(kind, execute=True)
    response, outgoing = run(args, steps)
    result = result_of(response)
    assert response.get("isError") is True and result["status"] == "blocked"
    assert len(outgoing.calls) == 2
    assert result["known_new_refno"] is None
    assert result["receipts"]["copy_execute"] is None
    assert result["receipts"]["parameters_execute"] is None


@pytest.mark.parametrize("field,value", [("ref", "=7/999"), ("owner", "=7/999")])
def test_trace_spec_or_catalogue_parent_mismatch_is_valid_raw_but_blocks_generation(field, value):
    steps = sequence("e3d_specification")
    raw = steps[0][2]
    if field == "ref":
        raw["result"]["trace"]["root"]["owner"] = value
        raw["result"]["trace"]["root.OWNER"]["ref"] = value
    else:
        raw["result"]["trace"]["root.CATREF"]["owner"] = value
        raw["result"]["trace"]["root.CATREF.OWNER"]["ref"] = value
    validate_paragon_reference_trace_receipt(raw, {"refno": SPCO, "trace_limit": 32})
    response, outgoing = run(arguments(), steps)
    result = result_of(response)
    assert response.get("isError") is True and result["status"] == "blocked"
    assert result["known_new_refno"] is None
    assert result["receipts"]["reference_trace"] == raw
    assert len(outgoing.calls) <= 2


@pytest.mark.parametrize("drift", ["project", "mdb", "typed_ce", "build", "session"])
def test_independently_valid_copy_preview_must_match_the_trace_context(drift):
    steps = sequence("e3d_specification")
    reply = steps[1][2]
    if drift in {"build", "session"}:
        key = "bridge_build" if drift == "build" else "session_id"
        value = "f5555555-5555-4555-8555-555555555555" if drift == "build" else "another-session"
        reply[key] = value
        reply["result"][key] = value
        before = reply["result"]["before"]
        before["context"][key] = value
        before["source_template"][key] = value
        for sample in ("before", "after"):
            before["source_template"]["context"][sample][key] = value
    else:
        key = "current_element" if drift == "typed_ce" else drift
        value = {"refno": "=7/90", "type": "SECT", "db_type": "Catalogue", "db_type_code": 2,
                 "identity_key": "=7/90|SECT"} if drift == "typed_ce" else "ANOTHER"
        reply["result"]["before"]["context"][key] = deepcopy(value)
        for sample in ("before", "after"):
            reply["result"]["before"]["source_template"]["context"][sample][key] = deepcopy(value)
    validate_copy(reply, copy_payload())
    response, outgoing = run(arguments(), steps)
    result = result_of(response)
    assert response.get("isError") is True and result["status"] == "blocked"
    assert len(outgoing.calls) == 2 and result["receipts"]["copy_execute"] is None


def test_unknown_spco_catref_is_not_replaced_by_prtref_or_source_guess():
    steps = sequence("e3d_specification")
    raw = steps[0][2]
    rows = raw["result"]["trace"]
    for path in list(rows):
        if path.startswith("root.CATREF."):
            del rows[path]
            del raw["result"]["db_type_codes"][path]
    rows["root.CATREF"] = {"ref": None, "type": None, "owner": None, "status": "unset"}
    del raw["result"]["db_type_codes"]["root.CATREF"]
    raw["result"]["trace_limit"]["used"] = len(rows)
    validate_paragon_reference_trace_receipt(raw, {"refno": SPCO, "trace_limit": 32})
    response, outgoing = run(arguments(), steps)
    assert response.get("isError") is True and result_of(response)["status"] == "blocked"
    assert len(outgoing.calls) == 1


@pytest.mark.parametrize("field", ["request_id", "scoped_revision"])
def test_copy_execute_binding_is_enforced_by_production_consumer_before_following_param_write(field):
    preview, _ = prepare("external_standard")
    steps = sequence("external_standard", execute=True)
    steps[2][2]["result"][field] = "f" * 64 if field == "scoped_revision" else PARAMETER_ID
    response, outgoing = run(execution_arguments("external_standard", preview), steps)
    result = result_of(response)
    assert response.get("isError") is True and result["status"] == "partial"
    assert len(outgoing.calls) == 3
    assert result["receipts"]["parameters_preview"] is None and result["receipts"]["parameters_execute"] is None


@pytest.mark.parametrize("failure_stage", [3, 4])
@pytest.mark.parametrize("failure_kind", ["ordinary", "native_fault", "timeout"])
def test_external_failure_after_copy_keeps_known_new_ref_and_never_repeats_copy(failure_stage, failure_kind):
    preview, _ = prepare("external_standard")
    steps = sequence("external_standard", execute=True)
    original_copy = deepcopy(steps[2][2])

    def fail(index, path, payload):
        if index != failure_stage:
            return None
        if failure_kind == "timeout":
            return TimeoutError("Original PARAM timeout")
        body = {"ok": False, "version": "E3D21", "session_id": SESSION, "bridge_build": BUILD,
                "error": "Original PARAM failure", "result": {"progress": {"known_target_refno": NEW}}}
        if failure_kind == "native_fault":
            body.update(error_code="native_database_fault", native_fault_latched=True, retry_safe=False)
        return urllib.error.HTTPError(SETTINGS.bridge_url + path, 500, "Original failure", {}, io.BytesIO(json.dumps(body).encode()))

    response, outgoing = run(execution_arguments("external_standard", preview), steps, failure=fail)
    result = result_of(response)
    assert response.get("isError") is True and result["status"] == "partial"
    assert result["known_new_refno"] == NEW
    assert result["receipts"]["copy_execute"] == original_copy
    assert result["copy_status_args"] == {"request_id": REQUEST_ID, "expected_session_id": SESSION}
    assert len(outgoing.calls) == failure_stage + 1
    assert sum(payload["dry_run"] is False for _, path, payload in outgoing.calls if path == COPY_PATH) == 1
    assert result["error"]["stage"]
    if failure_kind != "timeout":
        assert result["error"]["http_status"] == 500
        assert "Original PARAM failure" in result["error"]["message"]


@pytest.mark.parametrize("drift", ["owner", "instance_binding", "typed_ce"])
def test_parameter_preview_is_bound_to_verified_copy_after_before_setter_is_submitted(drift):
    preview, _ = prepare("external_standard")
    steps = sequence("external_standard", execute=True)
    raw = steps[3][2]
    before = raw["result"]["before"]
    if drift == "owner":
        before["owner"]["refno"], before["owner"]["identity_key"] = "=7/999", "=7/999|CATE"
    elif drift == "instance_binding":
        before["context"]["instance_binding"] = "f" * 64
    else:
        ce = {"refno": NEW, "type": "SCOM", "db_type": "Catalogue", "db_type_code": 2, "identity_key": NEW + "|SCOM"}
        before["context"]["current_element"] = deepcopy(ce)
        for side in ("before", "after"):
            before["template"]["context"][side]["current_element"] = deepcopy(ce)
    validate_parameters(raw, parameter_payload())
    response, outgoing = run(execution_arguments("external_standard", preview), steps)
    result = result_of(response)
    assert response.get("isError") is True and result["status"] == "partial"
    assert result["known_new_refno"] == NEW and result["receipts"]["copy_execute"] == steps[2][2]
    assert len(outgoing.calls) == 4 and result["receipts"]["parameters_execute"] is None


def test_external_mapping_count_is_checked_against_source_before_any_copy_execute():
    args = arguments("external_standard")
    args["basis"]["mapping"] = args["basis"]["mapping"][:2]
    response, outgoing = run(args, sequence("external_standard"))
    result = result_of(response)
    assert response.get("isError") is True and result["status"] == "blocked"
    assert len(outgoing.calls) == 2
    assert result["receipts"]["copy_execute"] is None and result["known_new_refno"] is None


@pytest.mark.parametrize("features", [[], ["paragon_component_copy"], ["paragon_component_parameters"], None, "paragon_component_copy"])
def test_external_feature_preflight_stops_before_copy_when_parameter_writer_is_unavailable(features):
    steps = sequence("external_standard", execute=True)
    steps[0][2]["api_features"] = features
    response, outgoing = run(arguments("external_standard"), steps)
    result = result_of(response)
    assert response.get("isError") is True and result["status"] == "blocked"
    assert len(outgoing.calls) == 1 and outgoing.calls[0][:2] == ("GET", "/health")
    assert result["receipts"]["health"] == steps[0][2]
    assert result["receipts"]["copy_preview"] is None and result["receipts"]["copy_execute"] is None
    assert result["known_new_refno"] is None


@pytest.mark.parametrize("field,value", [("version", "E3D31"), ("session_id", ""), ("bridge_build", "old-build"), ("connected", False)])
def test_external_feature_receipt_requires_native_version_and_complete_bridge_provenance(field, value):
    steps = sequence("external_standard")
    steps[0][2][field] = value
    response, outgoing = run(arguments("external_standard"), steps)
    assert response.get("isError") is True and result_of(response)["status"] == "blocked"
    assert len(outgoing.calls) == 1


@pytest.mark.parametrize("field,value", [("session_id", "another-session"), ("bridge_build", "f5555555-5555-4555-8555-555555555555")])
def test_external_health_and_copy_preview_identity_must_agree_before_any_write(field, value):
    steps = sequence("external_standard")
    steps[0][2][field] = value
    response, outgoing = run(arguments("external_standard"), steps)
    result = result_of(response)
    assert response.get("isError") is True and result["status"] == "blocked"
    assert len(outgoing.calls) == 2 and result["receipts"]["copy_execute"] is None


def with_selection_owner(raw, sele="=7/203"):
    rows, codes = raw["result"]["trace"], raw["result"]["db_type_codes"]
    spec = rows.pop("root.OWNER")
    material, fluid = rows.pop("root.OWNER.MATREF"), rows.pop("root.OWNER.FLUREF")
    rows["root"]["owner"] = sele
    rows["root.OWNER"] = {"ref": sele, "type": "SELE", "owner": SPEC, "status": "resolved"}
    rows["root.OWNER.OWNER"] = spec
    rows["root.OWNER.OWNER.MATREF"], rows["root.OWNER.OWNER.FLUREF"] = material, fluid
    codes["root.OWNER.OWNER"] = 2
    raw["result"]["trace_limit"]["used"] = len(rows)


def test_e3d_basis_accepts_observed_selection_owner_chain_and_retains_the_exact_trace():
    steps = sequence("e3d_specification")
    with_selection_owner(steps[0][2])
    validate_paragon_reference_trace_receipt(steps[0][2], {"refno": SPCO, "trace_limit": 32})
    response, outgoing = prepare(steps=steps)
    result = result_of(response)
    assert len(outgoing.calls) == 2
    assert result["receipts"]["reference_trace"] == steps[0][2]
    assert result["source_refno"] == SOURCE and result["specification_usage_updated"] is False


def test_changed_spec_selection_ownership_is_bound_in_outer_revision_even_if_template_is_unchanged():
    first = sequence("e3d_specification")
    with_selection_owner(first[0][2])
    preview, _ = prepare(steps=first)
    later = sequence("e3d_specification", execute=True)
    with_selection_owner(later[0][2], "=7/204")
    validate_paragon_reference_trace_receipt(later[0][2], {"refno": SPCO, "trace_limit": 32})
    response, outgoing = run(execution_arguments("e3d_specification", preview), later)
    result = result_of(response)
    assert response.get("isError") is True and result["status"] == "blocked"
    assert len(outgoing.calls) == 2 and result["receipts"]["copy_execute"] is None


@pytest.mark.parametrize("field,new", [("edition", "2022"), ("locator", "Table 8, different row"),
                                     ("document_sha256", "f" * 64)])
def test_external_document_and_locator_changes_require_a_fresh_review_revision(field, new):
    preview, _ = prepare("external_standard")
    args = execution_arguments("external_standard", preview)
    args["basis"][field] = new
    response, outgoing = run(args, sequence("external_standard", execute=True))
    result = result_of(response)
    assert response.get("isError") is True and result["status"] == "blocked"
    assert len(outgoing.calls) == 2 and result["receipts"]["copy_execute"] is None


def test_external_mapping_changes_require_a_fresh_revision_before_copy_is_attempted():
    preview, _ = prepare("external_standard")
    args = execution_arguments("external_standard", preview)
    args["basis"]["mapping"][0]["source_value"] = 2.0
    response, outgoing = run(args, sequence("external_standard", execute=True))
    result = result_of(response)
    assert response.get("isError") is True and result["status"] == "blocked"
    assert result["parameters"] == [2.0, -0.0, 50.8]
    assert len(outgoing.calls) == 2 and result["known_new_refno"] is None


def test_external_mapping_is_sorted_by_explicit_one_based_position():
    args = arguments("external_standard")
    args["basis"]["mapping"].reverse()
    response, _ = prepare("external_standard", args=args)
    result = result_of(response)
    assert result["parameters"] == CONVERTED
    assert [row["parameter_index"] for row in result["basis"]["mapping"]] == [1, 2, 3]


@pytest.mark.parametrize("source,target,value,converted", [
    ("in", "mm", 2.0, 50.8), ("mm", "in", 25.4, 1.0), ("1", "1", -0.0, -0.0),
])
def test_external_explicit_compatible_units_convert_only_values_and_do_not_claim_engineering_conformance(source, target, value, converted):
    args = arguments("external_standard")
    row = args["basis"]["mapping"][0]
    row.update(source_value=value, source_unit=source, param_unit=target)
    response, _ = prepare("external_standard", args=args)
    result = result_of(response)
    assert result["parameters"][0] == converted
    assert bits(result["parameters"])[0] == bits([converted])[0]
    assert result["engineering_validated"] is None
    assert result["receipts"]["parameters_execute"] is None


def test_external_document_digest_is_optional_without_fabricating_a_document_verification():
    args = arguments("external_standard")
    del args["basis"]["document_sha256"]
    response, _ = prepare("external_standard", args=args)
    result = result_of(response)
    assert "document_sha256" not in result["basis"]
    assert result["engineering_validated"] is None


@pytest.mark.parametrize("source,target", [("1", "mm"), ("mm", "1"), ("in", "1"), ("1", "in")])
def test_dimensionless_and_length_units_cannot_be_cross_converted(source, target):
    args = arguments("external_standard")
    args["basis"]["mapping"][0].update(source_unit=source, param_unit=target)
    server = create_server(SETTINGS)
    with patch("urllib.request.urlopen", side_effect=AssertionError("No I/O for incompatible units")) as transport:
        response = call(server, args)
    transport.assert_not_called()
    assert response.get("isError") is True


@pytest.mark.parametrize("value,source,target", [(1.7976931348623157e308, "in", "mm"), (5e-324, "mm", "in")])
def test_conversion_overflow_or_nonzero_underflow_is_rejected_before_any_model_io(value, source, target):
    args = arguments("external_standard")
    args["basis"]["mapping"][0].update(source_value=value, source_unit=source, param_unit=target)
    server = create_server(SETTINGS)
    with patch("urllib.request.urlopen", side_effect=AssertionError("No I/O for nonfinite converted values")) as transport:
        response = call(server, args)
    transport.assert_not_called()
    assert response.get("isError") is True


@pytest.mark.parametrize("stage", [0, 1, 2, 3, 4])
@pytest.mark.parametrize("status", [404, 500])
def test_external_stage_fault_container_is_retained_and_all_later_reads_or_writes_stop(stage, status):
    preview, _ = prepare("external_standard")
    steps = sequence("external_standard", execute=True)
    diagnostics = {"details": [{"native_fault_latched": True, "native_code": 538}]}

    def fail(index, path, payload):
        if index != stage:
            return None
        body = {"ok": False, "version": "E3D21", "session_id": SESSION, "bridge_build": BUILD,
                "error": "Original stage native fault", **diagnostics}
        return urllib.error.HTTPError(SETTINGS.bridge_url + path, status, "Original error", {}, io.BytesIO(json.dumps(body).encode()))

    response, outgoing = run(execution_arguments("external_standard", preview), steps, failure=fail)
    result = result_of(response)
    assert response.get("isError") is True and result["status"] == ("blocked" if stage < 2 else "partial")
    assert len(outgoing.calls) == stage + 1
    assert result["error"]["http_status"] == status
    assert result["error"]["diagnostics"]["details"] == diagnostics["details"]
    if stage > 2:
        assert result["known_new_refno"] == NEW and result["receipts"]["copy_execute"] == steps[2][2]


def test_e3d_generation_does_not_repoint_existing_spco_or_invoke_parameter_writer():
    preview, _ = prepare()
    response, outgoing = run(execution_arguments("e3d_specification", preview), sequence("e3d_specification", execute=True))
    result = result_of(response)
    assert result["status"] == "executed" and result["specification_usage_updated"] is False
    assert result["engineering_validated"] is None
    assert all(path != PARAM_PATH for _, path, _ in outgoing.calls)
    assert result["receipts"]["reference_trace"]["result"]["trace"]["root.CATREF"]["ref"] == SOURCE
    assert result["known_new_refno"] == NEW != SOURCE


def test_valid_partial_reference_tail_does_not_erase_complete_required_spec_and_template_paths():
    steps = sequence("e3d_specification")
    raw = steps[0][2]
    raw["result"]["trace"]["root.OWNER.MATREF"]["status"] = "read_error"
    raw["result"]["status"] = "partial"
    validate_paragon_reference_trace_receipt(raw, {"refno": SPCO, "trace_limit": 32})
    response, outgoing = prepare(steps=steps)
    result = result_of(response)
    assert len(outgoing.calls) == 2 and result["receipts"]["reference_trace"] == raw
    assert result["source_refno"] == SOURCE and result["engineering_validated"] is None


@pytest.mark.parametrize("native_fault", [False, True])
def test_partial_copy_failure_retains_its_independently_verified_new_ref_without_parameter_submission(native_fault):
    preview, _ = prepare("external_standard")
    steps = sequence("external_standard", execute=True)
    partial = copy_execution()
    partial.update(ok=False, error="Original copy readback fault")
    partial["result"].update(status="failed", success=False, complete=False)
    partial["result"]["progress"].update(readback_complete=False, outcome_uncertain=True, native_fault_latched=native_fault)
    if native_fault:
        partial.update(error_code="native_database_fault", native_fault_latched=True, retry_safe=False)

    def fail(index, path, payload):
        if index != 2:
            return None
        return urllib.error.HTTPError(SETTINGS.bridge_url + path, 500, "Original copy failure", {},
                                       io.BytesIO(json.dumps(partial).encode()))

    response, outgoing = run(execution_arguments("external_standard", preview), steps, failure=fail)
    result = result_of(response)
    assert response.get("isError") is True and result["status"] == "partial"
    assert len(outgoing.calls) == 3 and result["receipts"]["parameters_preview"] is None
    assert result["error"]["diagnostics"]["result"] == partial["result"]
    assert result["known_new_refno"] == NEW


@pytest.mark.parametrize("tamper", ["request_id", "session", "request_parent", "unverified", "owner", "identity"])
def test_failed_copy_summary_alone_cannot_project_an_unbound_new_ref(tamper):
    preview, _ = prepare("external_standard")
    steps = sequence("external_standard", execute=True)
    partial = copy_execution()
    partial.update(ok=False, error="Original unverified partial copy")
    native = partial["result"]
    native.update(status="failed", success=False, complete=False)
    native["progress"].update(readback_complete=False, outcome_uncertain=True)
    if tamper == "request_id":
        native["request_id"] = PARAMETER_ID
    elif tamper == "session":
        partial["session_id"] = native["session_id"] = "another-session"
    elif tamper == "request_parent":
        native["request"]["parent_refno"] = "=7/999"
    elif tamper == "unverified":
        native["progress"]["known_new_refno_verified"] = False
    elif tamper == "owner":
        native["after"]["new_owner"]["refno"] = "=7/999"
    else:
        native["after"]["new_template"]["target"]["refno"] = "=7/999"

    def fail(index, path, payload):
        if index == 2:
            return urllib.error.HTTPError(SETTINGS.bridge_url + path, 500, "Original copy failure", {},
                                           io.BytesIO(json.dumps(partial).encode()))
        return None

    response, outgoing = run(execution_arguments("external_standard", preview), steps, failure=fail)
    result = result_of(response)
    assert response.get("isError") is True and result["status"] == "partial"
    assert len(outgoing.calls) == 3
    assert result["known_new_refno"] is None
    assert result["receipts"]["parameters_preview"] is None
    assert result["error"]["diagnostics"]["result"] == native


def test_parameter_preview_known_no_write_failure_keeps_copy_receipt_and_stops_execution():
    preview, _ = prepare("external_standard")
    steps = sequence("external_standard", execute=True)
    raw = steps[3][2]
    result = raw["result"]
    result.update(status="blocked", success=False, complete=False, scoped_revision=None,
                  errors=[{"stage": "preflight", "code": "not_setable", "message": "SDK setability is false"}])
    result["before"]["setable"] = False
    result["progress"]["no_change"] = None
    validate_parameters(raw, parameter_payload())
    response, outgoing = run(execution_arguments("external_standard", preview), steps)
    composite = result_of(response)
    assert response.get("isError") is True and composite["status"] == "partial"
    assert composite["known_new_refno"] == NEW
    assert composite["receipts"]["copy_execute"] == steps[2][2]
    assert composite["receipts"]["parameters_preview"] == raw
    assert composite["receipts"]["parameters_execute"] is None and len(outgoing.calls) == 4

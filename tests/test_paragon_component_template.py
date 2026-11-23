"""Request and production receipt checks for the bounded SCOM reader."""
from __future__ import annotations

from copy import deepcopy
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.paragon_component_template import (
    PATH, TOOL, VERSION, _parameters, _validate, paragon_component_template, register_paragon_component_template,
)
from aveva_e3d_mcp.protocol import McpServer
from test_paragon_component_template_native_contract import component_template_receipts


ARGS = {"refno": "=22/148"}


def call(server, arguments):
    return server._dispatch({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": TOOL, "arguments": arguments},
    })["result"]


@pytest.mark.parametrize("refno", ["=1/0", "=1/1", "=22/148", "=2147483647/2147483647"])
def test_only_canonical_int32_ref_is_forwarded_unchanged(refno):
    args = {"refno": refno}
    result = _parameters(args)
    assert result == args
    assert result is not args


@pytest.mark.parametrize("bad", [
    {}, {"refno": None}, {"refno": 15}, {"refno": True}, {"refno": ["=1/2"]},
    {"refno": "/SCOM"}, {"refno": "CE"}, {"refno": "=1/*"},
    {"refno": "=0/3"}, {"refno": "=-1/3"}, {"refno": "=1/-3"}, {"refno": "=1/+3"},
    {"refno": "=01/3"}, {"refno": "=1/00"}, {"refno": " =1/3"}, {"refno": "=1/3 "},
    {"refno": "=1/3\n"}, {"refno": "=1/3\x00"}, {"refno": "=1/３"},
    {"refno": "=2147483648/1"}, {"refno": "=1/2147483648"}, {"refno": "=99999999999/1"},
    *[{**ARGS, name: "caller value"} for name in (
        "session_id", "bridge_build", "version", "database_type", "expected_type", "attributes", "limit",
    )],
])
def test_invalid_or_additional_arguments_stop_at_the_schema_and_direct_entrypoint(bad):
    class NoReads:
        def __getattr__(self, name):
            raise AssertionError(f"Unexpected bridge access: {name}")

    bridge = NoReads()
    with pytest.raises(ValueError, match="refno"):
        paragon_component_template(bridge, bad)
    server = McpServer("offline-component-template", "0")
    register_paragon_component_template(server, bridge)
    assert call(server, bad)["isError"] is True


@pytest.mark.parametrize("mode, version", [("mock", "E3D21"), ("bridge", "E3D31")])
def test_registration_read_only_and_unavailable_modes_never_use_transport(mode, version, monkeypatch):
    bridge = BridgeClient(Settings(mode=mode, version=version))

    def unexpected(*args, **kwargs):
        pytest.fail("Unexpected native I/O")

    monkeypatch.setattr(bridge, "_request", unexpected)
    monkeypatch.setattr(bridge, "health", unexpected)
    server = McpServer("offline-component-template", "0")
    register_paragon_component_template(server, bridge)
    definition = server._tools[TOOL][0]
    schema = definition["inputSchema"]
    assert list(schema["properties"]) == schema["required"] == ["refno"]
    assert schema["additionalProperties"] is False
    assert definition["annotations"]["readOnlyHint"] is True
    response = call(server, ARGS)
    assert response["isError"] is True
    result = response["structuredContent"]["result"]
    assert result["available"] is False and result["complete"] is False
    assert result["single_dispatch"] is None and result["status"] == "unknown"
    assert result["requested_refno"] == ARGS["refno"]
    assert result["read_errors"][0]["code"] == "native_unavailable"


def test_every_production_native_receipt_passes_through_the_python_consumer(component_template_receipts):
    receipts = component_template_receipts
    assert any(case["reply"]["result"]["complete"] is True for case in receipts)
    assert any(case["reply"]["result"]["complete"] is False for case in receipts)
    bridge = BridgeClient(Settings(mode="bridge", version=VERSION, allow_commands=False))
    for index, case in enumerate(receipts, 1):
        payload, reply = case["payload"], case["reply"]
        original = deepcopy(reply)
        try:
            _validate(reply, _parameters(payload))
        except ValueError as error:
            pytest.fail(f"Production component template receipt {index}: {error}")
        with patch.object(bridge, "_request", return_value=reply) as request:
            assert paragon_component_template(bridge, payload) is reply, f"Receipt {index}"
        request.assert_called_once_with("POST", PATH, payload, version=VERSION)
        assert reply == original
    print(f"Validated and preserved all {len(receipts)} production component template receipts in Python.")


@pytest.fixture
def complete_case(component_template_receipts):
    return deepcopy(next(case for case in component_template_receipts if case["reply"]["result"]["complete"] is True
                         and case["reply"]["result"]["parameters"]["returned_count"] >= 2))


def test_registered_consumer_receives_the_production_envelope_over_its_exact_post(complete_case):
    payload, reply = complete_case["payload"], complete_case["reply"]
    bridge = BridgeClient(Settings(mode="bridge", version=VERSION, allow_commands=False))
    server = McpServer("offline-component-template", "0")
    register_paragon_component_template(server, bridge)
    with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(reply).encode("utf-8"))) as urlopen:
        observed = call(server, payload)
    urlopen.assert_called_once()
    request = urlopen.call_args.args[0]
    assert request.method == "POST" and request.full_url == bridge.settings.bridge_url + PATH
    assert json.loads(request.data) == payload
    assert observed.get("isError") is not True and observed["structuredContent"] == reply


_REMOVE = object()


@pytest.mark.parametrize("path, changed", [
    (("ok",), 1),
    (("version",), _REMOVE),
    (("version",), "E3D31"),
    (("session_id",), _REMOVE),
    (("session_id",), "😀" * 129),
    (("bridge_build",), "configured-build"),
    (("result", "session_id"), "different-native-session"),
    (("result", "context", "before", "session_id"), "different-native-session"),
    (("result", "context", "after", "bridge_build"), "b3333333-3333-4333-b333-333333333333"),
    (("result", "context", "same_mdb_instance"), None),
    (("result", "context", "same_project_instance"), 1),
    (("result", "context", "after", "project"), "different-project"),
    (("result", "context", "after", "current_element", "db_type_code"), True),
    (("result", "requested_refno"), "=2/2"),
    (("result", "target", "db_type"), "Catalog"),
    (("result", "target", "identity_key"), "=2/9|SCOM"),
    (("result", "target", "request_binding", "resolved_refno"), "=2/2"),
    (("result", "target", "request_binding", "observed_name"), "/INFERRED"),
    (("result", "target", "request_binding", "verified"), 1),
    (("result", "target", "actual_type", "source"), "DbElement.GetElementType"),
    (("result", "target", "actual_type", "short_name"), ":UDET*"),
    (("result", "target", "actual_type", "complete"), True),
    (("result", "target", "name"), "😀" * 513),
    (("result", "target", "name_error"), "spurious"),
    (("result", "leaf", "is_leaf"), False),
    (("result", "leaf", "source"), "DbElement.GetMembers"),
    (("result", "parameters", "total_count"), 4097),
    (("result", "parameters", "returned_count"), 2.0),
    (("result", "parameters", "unit"), "mm"),
    (("result", "parameters", "values", 0, "index"), False),
    (("result", "parameters", "values", 0, "value"), True),
    (("result", "parameters", "values", 0, "value"), float("inf")),
    (("result", "parameters", "values", 0, "value"), float("nan")),
    (("result", "parameters", "values", 0, "value"), 10**350),
    (("result", "parameters", "values", 0, "value"), "2.5"),
    (("result", "parameters", "values", 1, "index"), 0),
    (("result", "references", "PTREF"), _REMOVE),
    (("result", "references", "OTHER"), {}),
    (("result", "references", "BLRFA", "shape"), "element"),
    (("result", "references", "PTREF", "reference_null"), True),
    (("result", "references", "PTREF", "values", 0, "identity", "reference_targets"), []),
    (("result", "references", "PTREF", "values", 0, "identity", "db_type_code"), 99),
    (("result", "scope", "reference_hops"), True),
    (("result", "scope", "word_parameters_observed"), True),
    (("result", "scope", "whole_clone_fingerprint"), True),
    (("result", "scope", "write_permission_verified"), False),
    (("result", "output_limits", "native_array_allocation_bounded"), True),
    (("result", "output_limits", "maximum_reference_items"), 17),
    (("result", "complete"), 1),
    (("result", "success"), False),
], ids=lambda item: ".".join(map(str, item)) if isinstance(item, tuple) else None)
def test_inconsistent_or_overclaiming_native_receipt_is_rejected(complete_case, path, changed):
    reply = complete_case["reply"]
    row = reply
    for part in path[:-1]:
        row = row[part]
    if changed is _REMOVE:
        del row[path[-1]]
    else:
        row[path[-1]] = changed
    with pytest.raises(ValueError, match="Invalid PARAGON component template evidence"):
        _validate(reply, complete_case["payload"])


def test_drifted_target_preserves_first_binding_but_rejects_forged_value_reads(
    complete_case, component_template_receipts,
):
    drift = deepcopy(next(case for case in component_template_receipts
                          if case["reply"]["result"]["target"]
                          and "request_binding" in case["reply"]["result"]["target"]["errors"]))
    result = drift["reply"]["result"]
    assert result["target"]["request_binding"]["verified"] is True
    assert result["parameters"]["status"] == "unknown"
    result["parameters"] = complete_case["reply"]["result"]["parameters"]
    with pytest.raises(ValueError, match="unattempted attribute"):
        _validate(drift["reply"], drift["payload"])


def test_target_requires_before_project_and_mdb_instances(complete_case):
    result = complete_case["reply"]["result"]
    context = result["context"]
    context["before"]["project"] = None
    context["before"]["errors"]["project"] = "No current project."
    context["same_project_instance"] = context["boundary_agreement"] = None
    result["complete"] = result["success"] = False
    with pytest.raises(ValueError, match="project instance required to resolve target"):
        _validate(complete_case["reply"], complete_case["payload"])


def test_inspection_cutoff_cannot_be_claimed_as_an_observed_empty_prefix(component_template_receipts):
    limited = deepcopy(next(case for case in component_template_receipts
                            if case["reply"]["result"]["parameters"]["inspection_limited"] is True))
    parameter = limited["reply"]["result"]["parameters"]
    assert parameter["returned_count"] == 0 and parameter["native_array_materialized"] is True
    parameter["status"] = "observed"
    with pytest.raises(ValueError, match="materialized array not inspected"):
        _validate(limited["reply"], limited["payload"])


def unset_row(index=0):
    return {"index": index, "status": "unset", "identity": None, "reference_null": True, "error": None}


@pytest.mark.parametrize("shape", ["scalar", "mixed_array", "all_unset_array", "all_attributes_unset"])
def test_observed_unset_values_preserve_exact_nullness_without_an_identity(complete_case, shape):
    reply, payload = complete_case["reply"], complete_case["payload"]
    references = reply["result"]["references"]
    if shape in {"scalar", "all_attributes_unset"}:
        references["PTREF"]["values"] = [unset_row()]
        references["PTREF"]["reference_null"] = True
    if shape in {"mixed_array", "all_unset_array", "all_attributes_unset"}:
        value = references["BLRFA"]
        first = deepcopy(value["values"][0]) if shape == "mixed_array" else unset_row()
        value["values"] = [first, unset_row(1)]
        value["total_count"] = value["returned_count"] = 2
    if shape == "all_attributes_unset":
        for value in references.values():
            value["values"] = [unset_row(index) for index in range(value["returned_count"])]
            value["reference_null"] = True if value["shape"] == "element" else None
    original = deepcopy(reply)
    _validate(reply, payload)
    assert reply["result"]["complete"] is True
    assert all(value["complete"] is True for value in references.values())
    bridge = BridgeClient(Settings(mode="bridge", version=VERSION, allow_commands=False))
    with patch.object(bridge, "_request", return_value=reply) as request:
        assert paragon_component_template(bridge, payload) is reply
    request.assert_called_once_with("POST", PATH, payload, version=VERSION)
    assert reply == original


@pytest.mark.parametrize("nullness", [None, False, True])
@pytest.mark.parametrize("error", ["native_null_reference_value_unknown", "native_reference_handle_unknown",
                                  "unset native Ref getter failed", "x" * 4096, "😀" * 2048])
def test_legacy_and_new_unknown_reference_errors_never_become_complete(complete_case, nullness, error):
    reply, payload = complete_case["reply"], complete_case["payload"]
    result = reply["result"]
    value = result["references"]["PTREF"]
    value["values"] = [{"index": 0, "status": "unknown", "identity": None, "reference_null": nullness, "error": error}]
    value.update(reference_null=nullness, status="partial", complete=False, errors={"items": "Reference value remains unknown."})
    result["complete"] = result["success"] = False
    original = deepcopy(reply)
    _validate(reply, payload)
    assert value["values"][0]["status"] == "unknown"
    assert value["complete"] is False and result["complete"] is False
    assert reply == original
    value.update(status="observed", complete=True, errors={})
    result["complete"] = result["success"] = True
    with pytest.raises(ValueError, match="typed value errors"):
        _validate(reply, payload)


@pytest.mark.parametrize("field, changed", [
    ("reference_null", None), ("reference_null", False), ("reference_null", 1), ("reference_null", "true"),
    ("identity", "native-null"), ("identity", {}), ("error", ""), ("error", "read failed"),
    ("error", False), ("status", "observed"), ("status", "unknown"), ("status", "partial"),
    ("index", False), ("index", 0.0), ("index", 1), ("extra_evidence", True),
])
def test_unset_reference_requires_exact_true_null_identity_and_null_error(complete_case, field, changed):
    value = complete_case["reply"]["result"]["references"]["PTREF"]
    value["reference_null"] = True
    value["values"] = [unset_row()]
    value["values"][0][field] = changed
    with pytest.raises(ValueError, match="Invalid PARAGON component template evidence"):
        _validate(complete_case["reply"], complete_case["payload"])


@pytest.mark.parametrize("error", [None, "", " ", True, 1, {}, "x" * 4097, "😀" * 2049, "\ud800"])
def test_unknown_native_null_reference_requires_a_bounded_error(complete_case, error):
    result = complete_case["reply"]["result"]
    value = result["references"]["PTREF"]
    value["values"] = [{"index": 0, "status": "unknown", "identity": None, "reference_null": True, "error": error}]
    value.update(reference_null=True, status="partial", complete=False, errors={"items": "Unknown null reference value."})
    result["complete"] = result["success"] = False
    with pytest.raises(ValueError, match="unknown reference identity"):
        _validate(complete_case["reply"], complete_case["payload"])

"""Read-only registration and fail-closed Equipment child evidence contracts."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.equipment_child_schema import (
    PATH, TOOL, _SCOPE, _parameters, _validate, equipment_child_schema, register_equipment_child_schema,
)
from aveva_e3d_mcp.protocol import McpServer, _schema_matches


@pytest.fixture(autouse=True)
def no_real_http_transport():
    with patch("urllib.request.urlopen", side_effect=AssertionError("Equipment child tests must not use real HTTP")):
        yield


def sdk_type(name="CYLI", source="DbElementType.GetElementType"):
    return {"source": source, "valid": True, "name": name, "short_name": name, "errors": {}, "complete": True}


def identity(refno, name, base, actual=None, requested=None):
    requested = refno if requested is None else requested
    return {"refno": refno, "name": name, "base_type": sdk_type(base, "DbElement.ElementType"),
            "actual_type": sdk_type(actual or base, "DbElement.GetActualType"),
            "request_binding": {"source": "explicit_request_resolution", "requested_refno": requested,
                                "resolved_refno": refno, "observed_name": name if requested.startswith("/") else None,
                                "verified": True}, "errors": {}, "complete": True}


def metadata(name="DIAM"):
    return {"known": True, "name": name, "short_name": name, "data_type": "DOUBLE", "is_array": False,
            "is_pseudo": False, "is_uda": name.startswith(":"), "unit_kind": "DISTANCE", "maximum_size": 1,
            "errors": {}, "complete": True}


def response(*, kind="CYLI", target="=1/2", actual=None, creatable=True):
    actual = actual or kind
    matches = None if target is None else kind == actual
    instance = {"source": "explicit_existing_read_target", "scope": "read_target_only", "refno": target,
                "status": "unknown", "reason": "read_target_required" if target is None else "actual_type_mismatch",
                "attributes": [], "complete": False}
    if matches:
        instance.update(status="observed", reason=None, complete=True,
                        attributes=[{**metadata(), "requested_name": "DIAM", "supported": True, "sdk_settable_now": False}])
    boundary = {"session_id": "session", "bridge_build": "build", "project": "PROJECT", "mdb": "MDB",
                "ce_refno": "=1/9", "ce_base_type": "EQUI", "ce_actual_type": "EQUI", "errors": {}}
    result = {
        "operation": "equipment_child_schema", "available": True, "read_only": True,
        "single_dispatch": True, "atomic_snapshot": False, "success": matches is True, "complete": matches is True,
        "session_id": "session", "bridge_build": "build", "requested_parent_refno": "=1/1", "requested_element_type": kind,
        "requested_read_target_refno": target, "requested_attributes": ["DIAM"],
        "parent": identity("=1/1", "/PARENT", "EQUI"), "requested_type": sdk_type(kind),
        "parent_creatability": {"source": "DbElement.IsCreatable", "scope": "current_parent_requested_type",
                                "parent_refno": "=1/1", "requested_element_type": kind,
                                "sdk_creatable_now": creatable, "error": None, "complete": True},
        "type_system_attributes": {"source": "DbElementType.SystemAttributes", "scope": "requested_type_only",
                                   "offset": 0, "limit": 50, "total_count": 1, "returned_count": 1,
                                   "has_more": False, "next_offset": None, "attributes": [{**metadata(), "sdk_index": 0}],
                                   "errors": {}, "complete": True, "whole_type_coverage": True,
                                   "native_arrays_materialized_before_output_limits": True,
                                   "native_array_inspection_limit": 4096, "inspection_limited": False},
        "read_target": None if target is None else identity(target, "/EXEMPLAR", "CYLI", actual),
        "read_target_actual_type_matches": matches, "instance_attributes": instance, "read_errors": {},
        "context": {"before": boundary, "after": deepcopy(boundary), "identity_unchanged": True, "ce_unchanged": True},
        "scope": dict(_SCOPE),
    }
    return {"ok": True, "version": "E3D21", "session_id": "session", "bridge_build": "build", "result": result}


def arguments(**updates):
    return {"parent_refno": "=1/1", "element_type": "CYLI", "read_target_refno": "=1/2", "attributes": ["DIAM"], **updates}


def bridge(**updates):
    return BridgeClient(Settings(mode="bridge", version="E3D21", allow_commands=False, **updates))


def registered():
    server = McpServer("equipment-child-test", "0")
    register_equipment_child_schema(server, bridge())
    return server


def call(args):
    return registered()._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                   "params": {"name": TOOL, "arguments": args}})["result"]


def test_read_only_registration_exposes_exact_fields_defaults_and_evidence_limits():
    tool, = registered()._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})["result"]["tools"]
    assert tool["name"] == TOOL and tool["annotations"]["readOnlyHint"] is True
    schema = tool["inputSchema"]
    assert schema["required"] == ["parent_refno", "element_type"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"parent_refno", "element_type", "read_target_refno", "attributes", "offset", "limit"}
    fields = schema["properties"]
    assert fields["read_target_refno"]["type"] == ["string", "null"] and fields["read_target_refno"]["default"] is None
    assert fields["attributes"]["default"] == [] and fields["attributes"]["maxItems"] == 20
    assert fields["offset"]["default"] == 0 and fields["offset"]["minimum"] == 0 and fields["offset"]["maximum"] == 100000
    assert fields["limit"]["default"] == 50 and fields["limit"]["minimum"] == 1 and fields["limit"]["maximum"] == 200
    for phrase in ("three independent", "need not be a child", "actual SDK type", "UDETs", "4096", "memory", "creation preview", "permission/revision", "read-only"):
        assert phrase in tool["description"]
    native = response(kind=":CUSTOM")
    with patch.object(BridgeClient, "_request", return_value=native) as request:
        reply = call(arguments(parent_refno=" =01/001 ", element_type=" :custom ", attributes=[" diam "]))
    assert not reply.get("isError"), reply
    assert reply["structuredContent"] == native and json.loads(reply["content"][0]["text"]) == native
    request.assert_called_once_with("POST", PATH, _parameters(arguments(element_type=":CUSTOM")), version="E3D21")


@pytest.mark.parametrize("explicit_null", [False, True])
def test_registered_optional_exemplar_nullable_or_omitted_and_empty_attributes(explicit_null):
    args = {"parent_refno": "=1/1", "element_type": "CYLI"}
    if explicit_null:
        args["read_target_refno"] = None
    native = response(target=None)
    native["result"]["requested_attributes"] = []
    with patch.object(BridgeClient, "_request", return_value=native) as request:
        reply = call(args)
    assert reply["structuredContent"] == native
    request.assert_called_once_with("POST", PATH, {"parent_refno": "=1/1", "element_type": "CYLI", "read_target_refno": None,
                                                  "attributes": [], "offset": 0, "limit": 50}, version="E3D21")
    assert native["result"]["parent_creatability"]["complete"] is True
    assert native["result"]["type_system_attributes"]["complete"] is True
    assert native["result"]["instance_attributes"]["status"] == "unknown"


def test_registered_twenty_attributes_and_maximum_page_inputs_remain_read_only():
    names = [f":ATTR{index}" for index in range(20)]
    native = response(target=None)
    native["result"]["requested_attributes"] = names
    native["result"]["type_system_attributes"].update(offset=100000, limit=200, returned_count=0,
        has_more=False, next_offset=None, attributes=[], whole_type_coverage=False)
    with patch.object(BridgeClient, "_request", return_value=native) as request:
        reply = call({"parent_refno": "=1/1", "element_type": "CYLI", "attributes": names, "offset": 100000, "limit": 200})
    assert reply["structuredContent"] == native
    assert request.call_args.args[0:2] == ("POST", PATH)
    request.assert_called_once()


@pytest.mark.parametrize("kind,target,actual,creatable", [
    ("CYLI", "=1/2", "CYLI", True), ("CYLI", None, "CYLI", True),
    (":CUSTOM", "=1/2", ":CUSTOM", True), ("CYLI", "=1/2", ":CUSTOM", True),
    (":CUSTOM", "=1/2", "CYLI", True), (":CUSTOM", "=1/2", ":OTHER", True),
    ("CYLI", "=1/2", "CYLI", False),
])
def test_reader_preserves_distinct_native_type_parent_and_existing_target_evidence(kind, target, actual, creatable):
    reply = response(kind=kind, target=target, actual=actual, creatable=creatable)
    expected = deepcopy(reply)
    with patch.object(BridgeClient, "_request", return_value=reply) as request:
        result = equipment_child_schema(bridge(), arguments(element_type=kind, read_target_refno=target))
    assert result == expected and reply == expected
    request.assert_called_once_with("POST", PATH, _parameters(arguments(element_type=kind, read_target_refno=target)), version="E3D21")
    assert result["result"]["scope"]["future_child_attributes_observed"] is False
    if target is None or kind != actual:
        assert result["result"]["instance_attributes"]["status"] == "unknown"
        assert result["result"]["instance_attributes"]["attributes"] == []


def test_input_normalization_is_explicit_and_has_no_implicit_exemplar():
    assert _parameters({"parent_refno": " =01/002 ", "element_type": " :custom ", "attributes": [" diam "]}) == {
        "parent_refno": "=1/2", "element_type": ":CUSTOM", "read_target_refno": None,
        "attributes": ["DIAM"], "offset": 0, "limit": 50}


@pytest.mark.parametrize("changes", [
    {"parent_refno": None}, {"parent_refno": "CE"}, {"parent_refno": "/"}, {"parent_refno": "/FUTURE*"},
    {"parent_refno": "/FUTURE?"}, {"parent_refno": "=0/1"}, {"parent_refno": "=2147483648/1"},
    {"parent_refno": "/A\n/B"}, {"parent_refno": "/" + "X" * 256}, {"parent_refno": True},
    {"element_type": None}, {"element_type": ""}, {"element_type": "CYLI;"}, {"element_type": "CÝLI"},
    {"read_target_refno": ""}, {"read_target_refno": "CE"}, {"read_target_refno": "/EXEMPLAR?"},
    {"attributes": None}, {"attributes": "DIAM"}, {"attributes": ["DIAM", " diam "]},
    {"attributes": ["DÍAM"]}, {"attributes": ["A"] * 21}, {"offset": True}, {"offset": -1},
    {"offset": 100001}, {"limit": True}, {"limit": 0}, {"limit": 201}, {"limit": "50"},
    {"proposed_values": {"DIAM": "120"}}, {"refno": "/PARENT"},
])
def test_invalid_inputs_never_reach_the_bridge(changes):
    with patch.object(BridgeClient, "_request") as request, pytest.raises(ValueError):
        equipment_child_schema(bridge(), arguments(**changes))
    request.assert_not_called()
    with patch.object(BridgeClient, "_request") as request:
        reply = call(arguments(**changes))
    assert reply.get("isError") is True
    request.assert_not_called()


@pytest.mark.parametrize("args", [
    {"parent_refno": " /PARENT ", "element_type": " :custom "},
    {"parent_refno": "=0002147483647/0002147483647", "element_type": "CYLI", "read_target_refno": None},
    {"parent_refno": "/" + "N" * 255, "element_type": "A" * 64,
     "read_target_refno": "=1/0", "attributes": [" :attr ", "DIAM"], "offset": 100000, "limit": 200},
])
def test_public_schema_accepts_the_same_normalizable_input_boundaries(args):
    definition, = (definition for definition, _ in registered()._tools.values())
    assert _schema_matches(args, definition["inputSchema"])
    _parameters(args)


@pytest.mark.parametrize("field", ["parent_refno", "read_target_refno"])
@pytest.mark.parametrize("refno", ["CE", "/", "/A*", "/A?", "/A B", "/A\n/B", "/A\x7f", "=0/1", "=2147483648/0", "=1/2147483648", "/" + "N" * 256])
def test_public_reference_schema_excludes_nonexplicit_or_out_of_range_targets(field, refno):
    definition, = (definition for definition, _ in registered()._tools.values())
    assert not _schema_matches(arguments(**{field: refno}), definition["inputSchema"])


@pytest.mark.parametrize("modify", [
    lambda r: r.update(version="E3D31"),
    lambda r: r.update(session_id="wrong"),
    lambda r: r["result"].update(requested_read_target_refno="=1/404"),
    lambda r: r["result"].update(attributes=[{"supported": True}]),
    lambda r: r["result"].update(complete=False),
    lambda r: r["result"].update(atomic_snapshot=True),
    lambda r: r["result"]["scope"].update(future_child_attributes_observed=True),
    lambda r: r["result"]["scope"].update(future_child_attributes_observed=0),
    lambda r: r["result"]["scope"].update(permissions_verified=True),
    lambda r: r["result"]["scope"].update(type_system_attributes_are_instance_attributes=True),
    lambda r: r["result"]["scope"].update(constraints_observed=True),
    lambda r: r["result"]["parent"].update(refno="=1/999"),
    lambda r: r["result"]["read_target"].update(refno="=1/999"),
    lambda r: r["result"]["read_target"]["actual_type"].update(source="DbElement.ElementType"),
    lambda r: r["result"]["read_target"]["actual_type"].update(name=":OTHER", short_name=":OTHER"),
    lambda r: r["result"]["parent_creatability"].update(parent_refno="=1/2"),
    lambda r: r["result"]["parent_creatability"].update(scope="future_child"),
    lambda r: r["result"]["parent_creatability"].update(sdk_creatable_now=1),
    lambda r: r["result"]["type_system_attributes"].update(scope="future_child"),
    lambda r: r["result"]["type_system_attributes"].update(source="DbElement.GetAttributes"),
    lambda r: r["result"]["type_system_attributes"].update(native_arrays_materialized_before_output_limits=False),
    lambda r: r["result"]["type_system_attributes"].update(returned_count=0),
    lambda r: r["result"]["type_system_attributes"]["attributes"][0].update(supported=True),
    lambda r: r["result"]["instance_attributes"].update(source="parent_schema"),
    lambda r: r["result"]["instance_attributes"].update(scope="future_child"),
    lambda r: r["result"]["instance_attributes"].update(refno="=1/1"),
    lambda r: r["result"]["instance_attributes"]["attributes"][0].update(requested_name="HEIG"),
    lambda r: r["result"]["instance_attributes"]["attributes"][0].update(sdk_settable_now=None),
    lambda r: r["result"]["context"]["after"].update(mdb="OTHER"),
    lambda r: r["result"]["context"]["after"].update(ce_actual_type=":OTHER"),
])
def test_forged_or_cross_scope_native_claims_fail_closed(modify):
    reply = response()
    modify(reply)
    with patch.object(BridgeClient, "_request", return_value=reply) as request:
        result = equipment_child_schema(bridge(), arguments())
    assert result["result"]["available"] is False
    assert result["result"]["read_errors"][0]["code"] == "invalid_native_response"
    assert result["result"]["instance_attributes"]["status"] == "unknown"
    request.assert_called_once()


def test_no_target_cannot_smuggle_parent_or_type_metadata_into_instance_attributes():
    reply = response(target=None)
    reply["result"]["instance_attributes"].update(status="observed", attributes=[metadata()], complete=True)
    with pytest.raises(ValueError, match="unobserved instance"):
        _validate(reply, _parameters(arguments(read_target_refno=None)))


def test_real_partial_read_preserves_independent_type_evidence_and_nullable_support():
    reply = response()
    instance = reply["result"]["instance_attributes"]
    instance.update(status="partial", complete=False)
    instance["attributes"][0].update(supported=None, sdk_settable_now=None,
                                     errors={"supported": "ordinary read failure"}, complete=False)
    reply["result"].update(success=False, complete=False)
    with patch.object(BridgeClient, "_request", return_value=reply):
        assert equipment_child_schema(bridge(), arguments()) == reply


def test_numeric_target_identity_read_error_preserves_observed_metadata_as_partial():
    native = response()
    native["result"]["read_target"].update(name=None, errors={"name": "NAME unavailable after explicit resolution"}, complete=False)
    native["result"].update(success=False, complete=False)
    with patch.object(BridgeClient, "_request", return_value=native):
        reply = equipment_child_schema(bridge(), arguments())
    assert reply == native and reply["result"]["instance_attributes"]["status"] == "observed"
    assert reply["result"]["read_target"]["name"] is None


@pytest.mark.parametrize("section,field,requested", [("parent", "parent_refno", "/PARENT"), ("read_target", "read_target_refno", "/EXEMPLAR")])
def test_captured_named_resolution_retains_a_later_name_read_failure_as_partial(section, field, requested):
    native = response()
    row = native["result"][section]
    row["request_binding"].update(requested_refno=requested, observed_name=row["name"])
    row.update(name=None, errors={"name": "NAME unavailable after explicit resolution"}, complete=False)
    native["result"].update({"requested_" + field: requested, "success": False, "complete": False})
    expected = deepcopy(native)
    with patch.object(BridgeClient, "_request", return_value=native):
        reply = equipment_child_schema(bridge(), arguments(**{field: requested}))
    assert reply == expected and native == expected
    assert reply["result"]["instance_attributes"]["status"] == "observed"
    assert row["name"] is None and row["request_binding"]["observed_name"] == requested


@pytest.mark.parametrize("section,field,requested", [("parent", "parent_refno", "/PARENT"), ("read_target", "read_target_refno", "/EXEMPLAR")])
@pytest.mark.parametrize("change", [
    {"source": "unverified_lookup"}, {"requested_refno": "/OTHER"}, {"resolved_refno": "=1/999"},
    {"observed_name": "/OTHER"}, {"observed_name": None}, {"observed_name": ""},
    {"verified": False}, {"verified": 1}, {"verified": None},
])
def test_captured_named_binding_cannot_claim_another_request_or_an_unverified_identity(section, field, requested, change):
    native = response()
    row = native["result"][section]
    row["request_binding"].update({"requested_refno": requested, "observed_name": row["name"], **change})
    native["result"]["requested_" + field] = requested
    with pytest.raises(ValueError):
        _validate(native, _parameters(arguments(**{field: requested})))


@pytest.mark.parametrize("section", ["parent", "read_target"])
@pytest.mark.parametrize("field", ["source", "requested_refno", "resolved_refno", "observed_name", "verified", "whole_binding"])
def test_missing_captured_resolution_evidence_is_not_reconstructed(section, field):
    native = response()
    row = native["result"][section]
    if field == "whole_binding":
        del row["request_binding"]
    else:
        del row["request_binding"][field]
    with pytest.raises(ValueError):
        _validate(native, _parameters(arguments()))


@pytest.mark.parametrize("section,field,requested", [("parent", "parent_refno", "/PARENT"), ("read_target", "read_target_refno", "/EXEMPLAR")])
def test_readable_second_name_cannot_disagree_with_captured_resolution(section, field, requested):
    native = response()
    row = native["result"][section]
    row["request_binding"].update(requested_refno=requested, observed_name=row["name"])
    row["name"] = "/OTHER"
    native["result"]["requested_" + field] = requested
    with pytest.raises(ValueError, match="second NAME binding"):
        _validate(native, _parameters(arguments(**{field: requested})))


@pytest.mark.parametrize("section,field,requested", [("parent", "parent_refno", "/PARENT"), ("read_target", "read_target_refno", "/EXEMPLAR")])
def test_explicit_native_second_name_drift_preserves_both_readings_only_as_partial(section, field, requested):
    native = response()
    row = native["result"][section]
    row["request_binding"].update(requested_refno=requested, observed_name=row["name"])
    row.update(name="/RENAMED_AFTER_RESOLUTION", errors={"request_binding": "Current NAME differs from the first verified explicit request resolution."}, complete=False)
    native["result"].update({"requested_" + field: requested, "success": False, "complete": False})
    expected = deepcopy(native)
    with patch.object(BridgeClient, "_request", return_value=native) as request:
        reply = equipment_child_schema(bridge(), arguments(**{field: requested}))
    assert reply == expected and native == expected
    assert reply["result"]["instance_attributes"]["status"] == "observed"
    assert row["request_binding"]["observed_name"] == requested
    assert row["name"] == "/RENAMED_AFTER_RESOLUTION"
    request.assert_called_once()


@pytest.mark.parametrize("section,field,requested", [("parent", "parent_refno", "/PARENT"), ("read_target", "read_target_refno", "/EXEMPLAR")])
@pytest.mark.parametrize("invalid", ["first_capture", "identity_complete", "global_complete", "blank_error", "unknown_name", "no_drift"])
def test_second_name_drift_error_cannot_bypass_first_capture_or_claim_completeness(section, field, requested, invalid):
    native = response()
    row = native["result"][section]
    row["request_binding"].update(requested_refno=requested, observed_name=row["name"])
    row.update(name="/RENAMED_AFTER_RESOLUTION", errors={"request_binding": "Native current NAME drift."}, complete=False)
    native["result"].update({"requested_" + field: requested, "success": False, "complete": False})
    if invalid == "first_capture":
        row["request_binding"]["observed_name"] = "/WRONG_FIRST_CAPTURE"
    elif invalid == "identity_complete":
        row["complete"] = True
    elif invalid == "global_complete":
        native["result"].update(success=True, complete=True)
    elif invalid == "blank_error":
        row["errors"]["request_binding"] = " "
    elif invalid == "unknown_name":
        row["name"] = None
        row["errors"]["name"] = "Second NAME was unavailable, not a known drift."
    else:
        row["name"] = requested
    with pytest.raises(ValueError):
        _validate(native, _parameters(arguments(**{field: requested})))


@pytest.mark.parametrize("section", ["parent", "read_target"])
@pytest.mark.parametrize("change", [{"requested_refno": "=1/999"}, {"resolved_refno": "=1/999"}, {"observed_name": "/EXTRA"}, {"verified": 1}])
def test_captured_numeric_binding_requires_exact_dbref_and_no_invented_name_observation(section, change):
    native = response()
    native["result"][section]["request_binding"].update(**change)
    with pytest.raises(ValueError):
        _validate(native, _parameters(arguments()))


@pytest.mark.parametrize("known", [False, None])
def test_unknown_existing_attribute_metadata_retains_nullable_fields_without_future_inference(known):
    native = response()
    item = native["result"]["instance_attributes"]["attributes"][0]
    for name in ("name", "short_name", "data_type", "is_array", "is_pseudo", "is_uda", "unit_kind", "maximum_size", "supported", "sdk_settable_now"):
        item[name] = None
    item.update(known=known, errors={} if known is False else {"known": "ordinary SDK validity failure"}, complete=False)
    native["result"]["instance_attributes"].update(status="partial", complete=False)
    native["result"].update(success=False, complete=False)
    with patch.object(BridgeClient, "_request", return_value=native):
        assert equipment_child_schema(bridge(), arguments()) == native


def test_native_type_attribute_page_is_complete_only_for_its_returned_page():
    native = response()
    system = native["result"]["type_system_attributes"]
    system.update(offset=50, limit=1, total_count=100, has_more=True, next_offset=51, whole_type_coverage=False)
    system["attributes"][0]["sdk_index"] = 50
    with patch.object(BridgeClient, "_request", return_value=native):
        reply = equipment_child_schema(bridge(), arguments(offset=50, limit=1))
    assert reply == native
    assert system["complete"] is True and system["whole_type_coverage"] is False


def test_existing_exemplar_is_not_required_to_belong_to_the_requested_parent():
    reply = response(target="=2/700")
    with patch.object(BridgeClient, "_request", return_value=reply) as request:
        result = call(arguments(read_target_refno="=2/700"))
    assert result["structuredContent"] == reply
    assert result["structuredContent"]["result"]["instance_attributes"]["status"] == "observed"
    request.assert_called_once()


@pytest.mark.parametrize("location", ["envelope", "result", "before", "after"])
@pytest.mark.parametrize("field", ["session_id", "bridge_build"])
@pytest.mark.parametrize("invalid", [None, "", " ", False, 1, "different", "M" * 257])
def test_every_identity_layer_is_independently_required_and_cannot_be_overwritten(location, field, invalid):
    native = response()
    layer = native if location == "envelope" else native["result"] if location == "result" else native["result"]["context"][location]
    layer[field] = invalid
    expected = deepcopy(native)
    with patch.object(BridgeClient, "_request", return_value=native) as request:
        reply = equipment_child_schema(bridge(), arguments())
    assert reply["result"]["available"] is False
    assert reply["result"]["read_errors"][0]["code"] == "invalid_native_response"
    assert native == expected
    request.assert_called_once()


@pytest.mark.parametrize("location", ["envelope", "result", "before", "after"])
@pytest.mark.parametrize("field", ["session_id", "bridge_build"])
def test_missing_identity_at_any_layer_fails_without_adding_it(location, field):
    native = response()
    layer = native if location == "envelope" else native["result"] if location == "result" else native["result"]["context"][location]
    del layer[field]
    with patch.object(BridgeClient, "_request", return_value=native):
        reply = equipment_child_schema(bridge(), arguments())
    assert reply["result"]["read_errors"][0]["code"] == "invalid_native_response"
    assert field not in layer


@pytest.mark.parametrize("field", ["session_id", "bridge_build"])
def test_two_matching_outer_layers_cannot_hide_a_conflicting_native_boundary(field):
    native = response()
    native[field] = native["result"][field] = "alternate"
    native["result"]["context"]["before"][field] = "alternate"
    with pytest.raises(ValueError, match="binding"):
        _validate(native, _parameters(arguments()))


def test_native_array_inspection_cutoff_retains_total_and_exemplar_evidence_without_claiming_empty_type():
    native = response()
    system = native["result"]["type_system_attributes"]
    system.update(total_count=4097, returned_count=0, has_more=None, next_offset=None, attributes=[],
                  errors={"inspection_limit": "SDK array materialized 4097 entries; page metadata remains unknown."},
                  complete=False, whole_type_coverage=False, inspection_limited=True)
    native["result"].update(success=False, complete=False)
    with patch.object(BridgeClient, "_request", return_value=native) as request:
        reply = equipment_child_schema(bridge(), arguments())
    assert reply == native
    assert reply["result"]["parent_creatability"]["complete"] is True
    assert reply["result"]["instance_attributes"]["status"] == "observed"
    assert reply["result"]["type_system_attributes"]["total_count"] == 4097
    request.assert_called_once()


@pytest.mark.parametrize("changes", [
    {"inspection_limited": True}, {"inspection_limited": None}, {"native_array_inspection_limit": 4095},
    {"native_array_inspection_limit": True}, {"total_count": 4097},
    {"errors": {"inspection_limit": "not actually inspected"}},
])
def test_false_inspection_limit_claims_do_not_turn_into_type_metadata(changes):
    native = response()
    native["result"]["type_system_attributes"].update(**changes)
    with pytest.raises(ValueError):
        _validate(native, _parameters(arguments()))


@pytest.mark.parametrize("reason", ["type", "system_attributes"])
def test_unknown_native_type_arrays_preserve_parent_and_existing_target_evidence(reason):
    native = response()
    native["result"]["type_system_attributes"].update(total_count=None, returned_count=0, has_more=None,
        next_offset=None, attributes=[], errors={reason: "ordinary SDK array failure"}, complete=False,
        whole_type_coverage=False, inspection_limited=None)
    native["result"].update(success=False, complete=False)
    with patch.object(BridgeClient, "_request", return_value=native):
        assert equipment_child_schema(bridge(), arguments()) == native


def test_partial_context_read_retains_unknown_and_independent_metadata():
    native = response()
    native["result"]["context"]["after"].update(mdb=None, errors={"mdb": "ordinary MDB read failed"})
    native["result"]["context"]["identity_unchanged"] = None
    native["result"].update(success=False, complete=False)
    with patch.object(BridgeClient, "_request", return_value=native):
        assert equipment_child_schema(bridge(), arguments()) == native


def test_unavailable_explicit_target_never_uses_the_parent_or_system_attribute_scope_as_an_exemplar():
    native = response(target=None)
    native["result"].update(requested_read_target_refno="=1/2", read_errors={"read_target": "explicit target lookup failed"})
    native["result"]["instance_attributes"]["reason"] = "read_target_unavailable"
    with patch.object(BridgeClient, "_request", return_value=native):
        assert equipment_child_schema(bridge(), arguments()) == native


def test_context_object_drift_with_identical_names_is_preserved_without_claiming_complete():
    reply = response()
    reply["result"]["context"]["identity_unchanged"] = False
    reply["result"].update(success=False, complete=False)
    _validate(reply, _parameters(arguments()))


@pytest.mark.parametrize("settings", [Settings(mode="mock", version="E3D21"), Settings(mode="bridge", version="E3D31")])
def test_mock_and_unsupported_versions_never_claim_native_type_evidence(settings):
    with patch.object(BridgeClient, "_request") as request:
        reply = equipment_child_schema(BridgeClient(settings), arguments())
    request.assert_not_called()
    assert reply["result"]["available"] is False
    assert reply["result"]["instance_attributes"]["status"] == "unknown"


def test_endpoint_404_has_no_parent_schema_fallback_or_retry():
    with patch.object(BridgeClient, "_request", side_effect=BridgeError("missing", http_status=404)) as request:
        reply = equipment_child_schema(bridge(), arguments())
    assert reply["result"]["read_errors"][0]["code"] == "native_feature_unavailable"
    request.assert_called_once()


@pytest.mark.parametrize("error", [BridgeError("transport", transport_error=True), BridgeError("native_database_fault", http_status=500)])
def test_transport_and_native_faults_propagate_once_without_replay(error):
    with patch.object(BridgeClient, "_request", side_effect=error) as request, pytest.raises(BridgeError):
        equipment_child_schema(bridge(), arguments())
    request.assert_called_once()


@pytest.mark.parametrize("marker", [
    {"has_fault": True}, {"native_fault": True}, {"native_fault_latched": True},
    {"error_code": "native_fault"}, {"error_code": "native_database_fault"},
    {"error_code": "native_database_fault", "native_fault_latched": True, "retry_safe": False},
    {"native_fault": {"has_fault": True, "code": 538}},
])
@pytest.mark.parametrize("route", ["raw", "bridge_404", "bridge_500"])
def test_recursive_native_fault_markers_take_precedence_over_validation_or_404_and_preserve_diagnostics(marker, route):
    diagnostic = {"ok": False, "nested": [{"deeper": marker}], "error": "original SDK diagnostic"}
    error = BridgeError("original transport diagnostic", http_status=404 if route == "bridge_404" else 500, diagnostics=diagnostic)
    mock = {"return_value": diagnostic} if route == "raw" else {"side_effect": error}
    expected = deepcopy(diagnostic)
    with patch.object(BridgeClient, "_request", **mock) as request, pytest.raises(BridgeError) as caught:
        equipment_child_schema(bridge(), arguments())
    request.assert_called_once_with("POST", PATH, _parameters(arguments()), version="E3D21")
    assert caught.value.diagnostics is diagnostic and diagnostic == expected
    if route != "raw":
        assert caught.value is error


def test_native_fault_marker_on_an_otherwise_valid_receipt_still_stops_once():
    native = response()
    native["diagnostics"] = [{"native_fault_latched": True}]
    with patch.object(BridgeClient, "_request", return_value=native) as request, pytest.raises(BridgeError) as caught:
        equipment_child_schema(bridge(), arguments())
    assert caught.value.diagnostics is native
    request.assert_called_once()

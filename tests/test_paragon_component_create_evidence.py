"""Public create_server calls use real HTTP decoding and creation validation.

Only urlopen is replaced. The strict wire accepts one native creation route
or one managed status lookup and forbids every recovery read and write.
"""
from __future__ import annotations

from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import urllib.parse
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.paragon_component_create import PATH, STATUS_PATH, STATUS_TOOL, TOOL
from aveva_e3d_mcp.tools import create_server
from test_paragon_component_create import (
    ARGS, BUILD, EXECUTE, GEOMETRY, NEW, PARENT, POINT, SESSION, STATUS_ARGS,
    at, blocked, core, created, envelope, prepared, snapshot, wire,
)


SETTINGS = Settings(mode="bridge", version="E3D21", allow_commands=True)


class Wire:
    def __init__(self, reply, args=ARGS, tool=TOOL):
        self.reply, self.args, self.tool, self.calls = reply, args, tool, []

    def __call__(self, request, **kwargs):
        assert not self.calls, "No retry, fallback, health, diagnosis, PML, copy or parameter write is permitted"
        url = urllib.parse.urlsplit(request.full_url)
        assert request.full_url.startswith(SETTINGS.bridge_url)
        if self.tool == STATUS_TOOL:
            assert request.method == "GET" and url.path == STATUS_PATH and request.data is None
            assert urllib.parse.parse_qs(url.query) == {key: [value] for key, value in self.args.items()}
            payload = None
        else:
            assert request.method == "POST" and url.path == PATH and not url.query
            assert request.get_header("Content-type") == "application/json"
            payload = json.loads(request.data)
            assert payload == wire(self.args)
        self.calls.append((request.method, url.path, deepcopy(payload)))
        return io.BytesIO(json.dumps(self.reply, allow_nan=False).encode("utf-8"))


def public(args=ARGS, reply=None, *, tool=TOOL, settings=SETTINGS):
    reply = prepared() if reply is None else reply
    original = deepcopy(reply)
    outgoing = Wire(reply, args, tool)
    server = create_server(settings)
    with patch("urllib.request.urlopen", side_effect=outgoing) as transport:
        frame = server._dispatch({"jsonrpc": "2.0", "id": 11, "method": "tools/call",
                                 "params": {"name": tool, "arguments": args}})
    assert frame["id"] == 11 and frame["jsonrpc"] == "2.0" and "error" not in frame
    assert reply == original
    return frame["result"], outgoing, transport


def test_public_catalog_registers_creation_and_managed_status_without_io():
    with patch("urllib.request.urlopen", side_effect=AssertionError("Catalog must be local")) as transport:
        server = create_server(SETTINGS)
    transport.assert_not_called()
    assert {TOOL, STATUS_TOOL} <= server._tools.keys()
    create, status = server._tools[TOOL][0], server._tools[STATUS_TOOL][0]
    assert create["annotations"]["readOnlyHint"] is False and create["annotations"]["idempotentHint"] is False
    assert status["annotations"]["readOnlyHint"] is True
    assert create["inputSchema"]["additionalProperties"] is status["inputSchema"]["additionalProperties"] is False
    assert "parameter_bits" not in create["inputSchema"]["properties"]


@pytest.mark.parametrize("args,factory", [(ARGS, prepared), (ARGS, blocked), (EXECUTE, created)])
def test_public_exact_post_preserves_raw_receipt_and_does_not_orchestrate_other_tools(args, factory):
    raw = factory()
    response, outgoing, transport = public(args, raw)
    assert response["structuredContent"] == raw
    assert response.get("isError", False) is (raw["result"]["success"] is False)
    transport.assert_called_once()
    assert outgoing.calls == [("POST", PATH, wire(args))]


def test_public_prepare_is_available_without_command_permission():
    response, outgoing, _ = public(settings=Settings(mode="bridge", version="E3D21", allow_commands=False))
    assert response.get("isError") is not True
    assert response["structuredContent"]["result"]["progress"]["creation_attempted"] is False
    assert len(outgoing.calls) == 1


def test_public_execution_permission_is_required_before_post():
    response, outgoing, transport = public(EXECUTE, created(), settings=Settings(mode="bridge", version="E3D21", allow_commands=False))
    assert response["isError"] is True and "ALLOW_COMMANDS" in response["content"][0]["text"]
    assert outgoing.calls == []
    transport.assert_not_called()


@pytest.mark.parametrize("status", ["unknown_or_expired", "accepted", "running", "completed"])
def test_public_status_only_reads_managed_history_and_preserves_native_unknown(status):
    raw = envelope(snapshot(status, receipt=created() if status == "completed" else None))
    response, outgoing, transport = public(STATUS_ARGS, raw, tool=STATUS_TOOL)
    assert response.get("isError") is not True and response["structuredContent"] == raw
    assert outgoing.calls == [("GET", STATUS_PATH, None)]
    transport.assert_called_once()


@pytest.mark.parametrize("path,field,value", [
    ((), "version", "E3D31"), ((), "version", None), ((), "bridge_build", BUILD.upper()),
    (("result",), "session_id", "another"), (("result",), "this_submission_executed", 1),
    (("result", "before", "context"), "project", None),
    (("result", "before", "context", "current_element"), "db_type_code", 1),
    (("result", "before", "point_set", "target", "actual_type"), "short_name", "UDET:PTSE"),
    (("result", "before", "geometry_set", "target", "actual_type"), "short_name", "UDET:GMSE"),
    (("result", "before", "point_set"), "members", []),
    (("result", "before", "geometry_set", "members", 0), "owner", None),
    (("result", "before"), "members_complete", False),
    (("result", "before"), "creatable", None),
])
def test_public_positive_preparation_cannot_promote_unbound_or_unknown_evidence(path, field, value):
    raw = prepared()
    at(raw, path)[field] = value
    response, outgoing, transport = public(reply=raw)
    assert response["isError"] is True and "result" not in response.get("structuredContent", {})
    assert len(outgoing.calls) == 1
    transport.assert_called_once()


@pytest.mark.parametrize("path,field,value", [
    (("result", "after", "context"), "current_element", core(NEW, "SCOM")),
    (("result", "after", "context"), "instance_binding", "c" * 64),
    (("result", "after", "new_identity"), "name", "/OTHER"),
    (("result", "after", "new_identity", "actual_type"), "short_name", "UDET:SCOM"),
    (("result", "after", "point_set", "members", 0, "owner"), "refno", GEOMETRY),
    (("result", "after", "geometry_set", "members", 0, "identity"), "refno", NEW),
    (("result", "after", "new_template", "references", "PTREF", "values", 0, "identity"), "refno", GEOMETRY),
    (("result", "after", "new_template", "references", "GMREF", "values", 0, "identity"), "refno", POINT),
    (("result", "after", "new_template", "parameters", "values", 1), "value", 1.0),
    (("result", "after"), "parameter_bits", ["0" * 16] * 3),
    (("result", "progress"), "readback_complete", False),
    (("result", "progress"), "sets_unchanged_scoped", False),
    (("result", "progress"), "known_new_refno_verified", None),
])
def test_public_created_cannot_rely_on_success_flags_when_raw_postconditions_disagree(path, field, value):
    raw = created()
    at(raw, path)[field] = value
    response, outgoing, _ = public(EXECUTE, raw)
    assert response["isError"] is True and "result" not in response.get("structuredContent", {})
    assert outgoing.calls == [("POST", PATH, wire(EXECUTE))]


def test_public_net48_negative_zero_readback_retains_separate_native_bits():
    raw = created()
    raw["result"]["request"]["parameters"][1] = 0.0
    raw["result"]["after"]["new_template"]["parameters"]["values"][1]["value"] = 0.0
    response, outgoing, _ = public(EXECUTE, raw)
    assert response.get("isError") is not True and response["structuredContent"] == raw
    assert response["structuredContent"]["result"]["after"]["parameter_bits"][1] == "8000000000000000"
    assert len(outgoing.calls) == 1


def test_public_required_slice_accepts_optional_reference_failure_without_claiming_whole_template():
    raw = created()
    template = raw["result"]["after"]["new_template"]
    row = template["references"]["DTREF"]
    row.update(total_count=None, returned_count=0, values=[], reference_null=None, native_array_materialized=False,
               truncated=None, inspection_limited=None, status="unknown", errors={"value": "Getter failed"}, complete=False)
    template.update(success=False, complete=False)
    response, outgoing, _ = public(EXECUTE, raw)
    assert response.get("isError") is not True and response["structuredContent"] == raw
    result = response["structuredContent"]["result"]
    assert result["complete"] is True and result["after"]["new_template"]["complete"] is False
    assert result["scope"]["set_membership_only"] is True and result["scope"]["geometry_evaluated"] is False
    assert result["scope"]["engineering_validated"] is None and len(outgoing.calls) == 1


def test_public_numeric_param_values_remain_usable_when_only_unit_metadata_failed():
    raw = created()
    template = raw["result"]["after"]["new_template"]
    row = template["parameters"]
    row.update(unit_kind=None, errors={"unit_kind": "Unit metadata getter failed"}, status="partial", complete=False)
    template.update(success=False, complete=False)
    response, outgoing, _ = public(EXECUTE, raw)
    assert response.get("isError") is not True and response["structuredContent"] == raw
    result = response["structuredContent"]["result"]
    assert result["progress"]["parameters_equal_request"] is True
    assert result["after"]["new_template"]["parameters"]["unit_status"] == "unknown"
    assert result["after"]["parameter_bits"] == wire(EXECUTE)["parameter_bits"] and len(outgoing.calls) == 1


def test_public_set_and_reference_name_partial_preserves_required_numeric_typed_set_binding():
    raw = created()
    result = raw["result"]
    for side in ("before", "after"):
        for key in ("point_set", "geometry_set"):
            target = result[side][key]["target"]
            target.update(name=None, name_error="Set NAME failed", errors={"name": "Set NAME failed"}, complete=False)
    template = result["after"]["new_template"]
    for name in ("PTREF", "GMREF"):
        row = template["references"][name]
        item = row["values"][0]
        item["identity"].update(name=None, name_error="Reference NAME failed", errors={"name": "Reference NAME failed"}, complete=False)
        item.update(status="partial", error="Reference identity is partial")
        row.update(status="partial", errors={"items": "Reference identity is partial"}, complete=False)
    template.update(success=False, complete=False)
    response, outgoing, _ = public(EXECUTE, raw)
    assert response.get("isError") is not True and response["structuredContent"] == raw
    assert response["structuredContent"]["result"]["progress"]["sets_unchanged_scoped"] is True
    assert response["structuredContent"]["result"]["progress"]["references_equal_request"] is True
    assert len(outgoing.calls) == 1


@pytest.mark.parametrize("field,value", [("parameter_bits", []), ("source_refno", "=7/1"), ("template_refno", "=7/1"),
    ("pml", "NEW SCOM"), ("points", []), ("geometry", {}), ("ptref", POINT), ("gmref", GEOMETRY),
    ("parameters", [True]), ("parameters", []), ("parameters", [0] * 129),
    ("point_refno", "/POINTS"), ("geometry_refno", "=1/01"), ("name", "/A;NEW"),
    ("dry_run", "false"), ("request_id", None)])
def test_public_input_rejection_never_reaches_http(field, value):
    response, outgoing, transport = public({**ARGS, field: value})
    assert response["isError"] is True and outgoing.calls == []
    transport.assert_not_called()


@pytest.mark.parametrize("field", ["version", "session_id", "bridge_build", "result"])
def test_missing_native_envelope_identity_is_never_filled_from_configuration(field):
    raw = prepared()
    del raw[field]
    response, outgoing, _ = public(reply=raw)
    assert response["isError"] is True and "result" not in response.get("structuredContent", {}) and len(outgoing.calls) == 1

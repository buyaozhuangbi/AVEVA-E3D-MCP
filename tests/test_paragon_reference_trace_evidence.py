"""Public MCP reference-trace tests replacing only urllib's wire transport.

The real create_server registration, closed schema, BridgeClient decoder and
reference-trace receipt validator all run. The allowlist admits one POST and
rejects health, diagnostics, command, PML and model-write requests.
"""
from __future__ import annotations

from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import urllib.error
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.paragon_reference_trace import PATH, TOOL, validate_paragon_reference_trace_receipt
from aveva_e3d_mcp.tools import create_server
from test_paragon_reference_trace import BUILD, SESSION, _receipt, _row, design_receipt


DEFAULT_ARGS = {"refno": "=1/21"}
PAYLOAD = {**DEFAULT_ARGS, "trace_limit": 24}
SETTINGS = Settings(mode="bridge", version="E3D21", allow_commands=False)
PROVENANCE = {"version": "E3D21", "session_id": SESSION, "bridge_build": BUILD}
MISSING = object()


def call(server, args):
    frame = server._dispatch({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                              "params": {"name": TOOL, "arguments": args}})
    assert frame["jsonrpc"] == "2.0" and frame["id"] == 7
    return frame["result"]


class Wire:
    def __init__(self, value, *, payload=None, http_status=None, raw=None):
        self.value = value
        self.payload = PAYLOAD if payload is None else payload
        self.http_status, self.raw, self.calls = http_status, raw, []
        self.original = deepcopy(value)

    def __call__(self, request, **kwargs):
        assert self.calls == [], "Only the initial reference-trace read is authorized"
        assert request.method == "POST"
        assert request.full_url == SETTINGS.bridge_url + PATH
        assert request.get_header("Content-type") == "application/json"
        assert json.loads(request.data) == self.payload
        self.calls.append((request.method, request.full_url, json.loads(request.data)))
        data = self.raw if self.raw is not None else json.dumps(self.value, allow_nan=False).encode("utf-8")
        body = io.BytesIO(data)
        if self.http_status is not None:
            raise urllib.error.HTTPError(request.full_url, self.http_status, "failure", {}, body)
        return body


def run(value, args=None, **options):
    wire = Wire(value, **options)
    server = create_server(SETTINGS)
    with patch("urllib.request.urlopen", side_effect=wire) as transport:
        result = call(server, DEFAULT_ARGS if args is None else args)
    assert transport.call_count == len(wire.calls) == 1
    assert value == wire.original
    return result


def unknown(response, code):
    assert response.get("isError") is True
    envelope = response["structuredContent"]
    assert envelope["ok"] is True
    result = envelope["result"]
    assert result["operation"] == "paragon_reference_trace"
    assert result["requested_refno"] == "=1/21"
    assert result["status"] == "unknown" and result["available"] is False
    assert result["complete"] is False and result["success"] is False
    assert result["read_only"] is True and result["atomic_snapshot"] is False
    assert result["single_dispatch"] is None
    assert result["read_errors"][0]["code"] == code
    assert not {"trace", "context", "db_type_codes"} & result.keys()


def test_public_registration_is_closed_read_only_and_needs_no_health_probe():
    with patch("urllib.request.urlopen", side_effect=AssertionError("No network expected")) as transport:
        server = create_server(SETTINGS)
        tools = server._dispatch({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})["result"]["tools"]
    transport.assert_not_called()
    tool = next(item for item in tools if item["name"] == TOOL)
    assert tool["annotations"] == {"readOnlyHint": True, "destructiveHint": False,
                                    "idempotentHint": True, "openWorldHint": False}
    schema = tool["inputSchema"]
    assert schema["additionalProperties"] is False and schema["required"] == ["refno"]
    assert set(schema["properties"]) == {"refno", "trace_limit"}
    assert schema["properties"]["trace_limit"] == {"type": "integer", "minimum": 1, "maximum": 32, "default": 24}


def test_public_consumer_preserves_complete_native_paths_and_raw_evidence(design_receipt):
    validate_paragon_reference_trace_receipt(design_receipt, PAYLOAD)
    response = run(design_receipt)
    assert response.get("isError") is not True
    assert response["structuredContent"] == design_receipt
    trace = response["structuredContent"]["result"]["trace"]
    assert trace["root.SPREF.CATREF"] == trace["root.SPREF.PRTREF.CATREF"]
    assert "root.SPREF.CATREF" in trace and "root.SPREF.PRTREF.CATREF" in trace
    assert json.loads(response["content"][0]["text"]) == design_receipt


@pytest.mark.parametrize("refno", ["=1/0", "=2147483647/0", "=2147483647/2147483647"])
@pytest.mark.parametrize("limit", [1, 24, 32])
def test_public_schema_forwards_exact_canonical_int32_arguments(refno, limit):
    payload = {"refno": refno, "trace_limit": limit}
    reply = _receipt({"root": _row(refno, "CATA")}, {"root": 2}, limit=limit)
    response = run(reply, payload, payload=payload)
    assert response.get("isError") is not True and response["structuredContent"] == reply


@pytest.mark.parametrize("args", [
    {}, {"refno": None}, {"refno": True}, {"refno": 7}, {"refno": ["=1/21"]},
    {"refno": "CE"}, {"refno": "/NAME"}, {"refno": "=0/0"}, {"refno": "=01/21"},
    {"refno": "=1/021"}, {"refno": "=1/-1"}, {"refno": "=1/+1"}, {"refno": "=1/１"},
    {"refno": " =1/21"}, {"refno": "=1/21 "}, {"refno": "=1/21\n"}, {"refno": "=1/21\x00"},
    {"refno": "=1/2147483648"}, {"refno": "=2147483648/21"}, {"refno": "=1/*"},
    *[{**DEFAULT_ARGS, "trace_limit": value} for value in (None, True, False, "24", 1.0, 0, -1, 33)],
    *[{**DEFAULT_ARGS, key: "caller-owned"} for key in (
        "session_id", "bridge_build", "version", "attributes", "database_type", "expected_type", "dry_run",
    )],
])
def test_public_schema_rejects_invalid_or_caller_provenance_before_io(args):
    server = create_server(SETTINGS)
    with patch("urllib.request.urlopen", side_effect=AssertionError("No reads permitted")) as transport:
        response = call(server, args)
    transport.assert_not_called()
    assert response.get("isError") is True
    assert "structuredContent" not in response


@pytest.mark.parametrize("mode,version", [("mock", "E3D21"), ("bridge", "E3D31")])
def test_public_unsupported_runtime_is_unknown_with_zero_io(mode, version):
    server = create_server(Settings(mode=mode, version=version, allow_commands=False))
    with patch("urllib.request.urlopen", side_effect=AssertionError("No reads permitted")) as transport:
        response = call(server, DEFAULT_ARGS)
    transport.assert_not_called()
    unknown(response, "native_unavailable")
    assert response["structuredContent"]["version"] == version


@pytest.mark.parametrize("status", ["busy", "context_unverified"])
def test_public_pre_read_unknown_preserves_zero_attempt_budget_without_fabricating_rows(status):
    reply = _receipt({}, {}, status=status, used=0)
    reply["result"]["context"].update(before=None, after=None, same_project_instance=None,
                                       same_mdb_instance=None, boundary_agreement=None)
    validate_paragon_reference_trace_receipt(reply, PAYLOAD)
    response = run(reply)
    assert response["structuredContent"] == reply
    assert response["structuredContent"]["result"]["trace_limit"] == {"limit": 24, "used": 0, "reached": False}


@pytest.mark.parametrize("field,value", [("refno", "=1/23"), ("type", "NOZZ"), ("db_type_code", 2)])
def test_public_context_loss_retains_used_but_no_trace_identities(design_receipt, field, value):
    result = design_receipt["result"]
    result["context"]["after"]["current_element"][field] = value
    result["context"]["boundary_agreement"] = False
    result.update(status="context_unverified", trace={}, db_type_codes={})
    validate_paragon_reference_trace_receipt(design_receipt, PAYLOAD)
    response = run(design_receipt)
    assert response["structuredContent"] == design_receipt
    assert result["trace_limit"]["used"] == 18


def test_public_missing_after_boundary_keeps_actual_attempt_count(design_receipt):
    result = design_receipt["result"]
    result["context"].update(after=None, same_project_instance=None, same_mdb_instance=None, boundary_agreement=None)
    result.update(status="context_unverified", trace={}, db_type_codes={})
    validate_paragon_reference_trace_receipt(design_receipt, PAYLOAD)
    assert run(design_receipt)["structuredContent"] == design_receipt


@pytest.mark.parametrize("status", ["unset", "attribute_unavailable", "unresolved", "read_error", "out_of_scope"])
def test_public_partial_reference_statuses_keep_their_independent_meaning(status):
    observed = status == "out_of_scope"
    reply = _receipt({"root": _row("=1/21", "ELBO"),
                      "root.SPREF": _row("=7/1" if observed else None, "SPCO" if observed else None, status=status)},
                     {"root": 1, **({"root.SPREF": 7} if observed else {})},
                     status="observed" if status == "unset" else "partial")
    validate_paragon_reference_trace_receipt(reply, PAYLOAD)
    assert run(reply)["structuredContent"] == reply


def test_public_path_budget_is_a_dfs_prefix_and_not_unique_identity_count():
    payload = {"refno": "=1/21", "trace_limit": 1}
    reply = _receipt({"root": _row("=1/21", "ELBO")}, {"root": 1}, status="partial", limit=1, reached=True)
    validate_paragon_reference_trace_receipt(reply, payload)
    response = run(reply, payload, payload=payload)
    assert response["structuredContent"] == reply
    assert reply["result"]["trace_limit"] == {"limit": 1, "used": 1, "reached": True}
    assert "root.SPREF" not in response["structuredContent"]["result"]["trace"]


@pytest.mark.parametrize("layer,key,bad", [
    ("envelope", "version", MISSING), ("envelope", "version", "E3D31"),
    ("envelope", "session_id", MISSING), ("envelope", "bridge_build", MISSING),
    ("envelope", "bridge_build", "old-build"), ("result", "context", MISSING),
    ("result", "db_type_codes", MISSING), ("before", "session_id", "another-session"),
    ("after", "session_id", "another-session"), ("before", "bridge_build", "another-build"),
    ("after", "bridge_build", "another-build"), ("before", "current_element", "=1/20"),
    ("after", "current_element", "=1/20"), ("before", "current_element", {"refno": "=1/20"}),
    ("context", "same_mdb_instance", False), ("context", "same_project_instance", False),
    ("context", "boundary_agreement", 1), ("result", "status", "complete"),
])
def test_public_invalid_evidence_is_unknown_and_has_no_trace_projection(design_receipt, layer, key, bad):
    result = design_receipt["result"]
    context = result["context"]
    target = {"envelope": design_receipt, "result": result, "context": context,
              "before": context["before"], "after": context["after"]}[layer]
    if bad is MISSING:
        del target[key]
    else:
        target[key] = deepcopy(bad)
    unknown(run(design_receipt), "invalid_native_response")


@pytest.mark.parametrize("container", ["details", "diagnostics", "result"])
@pytest.mark.parametrize("status", [200, 404, 500])
def test_public_nested_native_fault_stops_with_original_diagnostics_and_no_fallback(container, status):
    raw = {"ok": False, "error": "Original trace SDK failure", **PROVENANCE,
           container: [{"nested": {"native_fault_latched": True}, "sdk_code": 538}],
           "private": "must remain private"}
    expected = {key: value for key, value in raw.items() if key not in {"private", "ok", "error"}}
    response = run(raw, http_status=None if status == 200 else status)
    assert response.get("isError") is True
    evidence = response["structuredContent"]
    assert evidence["ok"] is False
    assert "Original trace SDK failure" in evidence["error"]
    assert {key: value for key, value in evidence.items() if key not in {"ok", "error"}} == expected
    assert "private" not in evidence and "trace" not in evidence
    assert "native_feature_unavailable" not in json.dumps(evidence)


def test_public_404_missing_reader_is_unknown_after_exactly_one_post():
    raw = {"ok": False, "error": "No such route", **PROVENANCE}
    unknown(run(raw, http_status=404), "native_feature_unavailable")


@pytest.mark.parametrize("raw", [b"missing", b"[]", b'{"ok":true}', b'{"ok":false,"error":null}',
                                 b'{"ok":false,"error":"missing","number":NaN}'])
def test_public_malformed_error_never_claims_missing_feature(raw):
    unknown(run({}, http_status=404, raw=raw), "invalid_native_response")


def test_public_strict_success_json_does_not_accept_nonfinite_native_numbers(design_receipt):
    raw = json.dumps(design_receipt).replace('"root": 1', '"root": NaN').encode("utf-8")
    assert b"NaN" in raw
    unknown(run(design_receipt, raw=raw), "invalid_native_response")

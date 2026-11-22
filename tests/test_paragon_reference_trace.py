"""Offline receipt and transport tests for bounded PARAGON reference evidence."""

from __future__ import annotations

import io
import json
from pathlib import Path
import sys
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.paragon_reference_trace import (
    PATH,
    TOOL,
    paragon_reference_trace,
    register_paragon_reference_trace,
    validate_paragon_reference_trace_receipt,
)
from aveva_e3d_mcp.protocol import McpServer


def _row(ref=None, noun=None, owner=None, status="resolved"):
    return {"ref": ref, "type": noun, "owner": owner, "status": status}


def _receipt(trace, db_types, *, status="observed", limit=24, used=None, reached=False):
    before = {
        "session_id": "fixture-session", "bridge_build": "fixture-build", "project": "F01",
        "mdb": "CATALOGUES", "current_element": "=1/20",
    }
    return {
        "ok": True, "version": "E3D21", "session_id": before["session_id"], "bridge_build": before["bridge_build"],
        "context": {"before": before, "after": dict(before), "same_project_instance": True,
                    "same_mdb_instance": True, "boundary_agreement": True},
        "db_type_codes": db_types,
        "result": {"status": status, "trace_limit": {"limit": limit, "used": len(trace) if used is None else used,
                                                     "reached": reached},
                   "trace": trace},
    }


@pytest.fixture
def bridge():
    return BridgeClient(Settings(mode="bridge", version="E3D21"))


@pytest.fixture
def design_receipt():
    # The diamond and the two-level SELE chain exercised by the offline SDK harness.
    spco = "root.SPREF"
    spec = spco + ".OWNER.OWNER.OWNER"
    paths = {
        "root": (1, "=1/21", "ELBO", "=1/22"),
        spco: (2, "=2/10", "SPCO", "=2/11"),
        spco + ".CATREF": (2, "=2/4", "SCOM", "=2/3"),
        spco + ".CATREF.OWNER": (2, "=2/3", "CATE", "=2/2"),
        spco + ".CATREF.OWNER.OWNER": (2, "=2/2", "SECT", "=2/1"),
        spco + ".CATREF.OWNER.OWNER.OWNER": (2, "=2/1", "CATA", "=2/0"),
        spco + ".PRTREF": (2, "=2/5", "GPART", "=2/6"),
        spco + ".PRTREF.CATREF": (2, "=2/4", "SCOM", "=2/3"),
        spco + ".PRTREF.CATREF.OWNER": (2, "=2/3", "CATE", "=2/2"),
        spco + ".PRTREF.CATREF.OWNER.OWNER": (2, "=2/2", "SECT", "=2/1"),
        spco + ".PRTREF.CATREF.OWNER.OWNER.OWNER": (2, "=2/1", "CATA", "=2/0"),
        spco + ".PRTREF.CMPREF": (4, "=3/1", "CMPD", "=3/2"),
        spco + ".CMPREF": (4, "=3/1", "CMPD", "=3/2"),
        spco + ".OWNER": (2, "=2/11", "SELE", "=2/9"),
        spco + ".OWNER.OWNER": (2, "=2/9", "SELE", "=2/7"),
        spec: (2, "=2/7", "SPEC", "=2/8"),
        spec + ".MATREF": (4, "=3/4", "SOLI", "=3/3"),
        spec + ".FLUREF": (4, "=3/5", "FLUI", "=3/3"),
    }
    return _receipt({path: _row(*data[1:]) for path, data in reversed(paths.items())},
                    {path: data[0] for path, data in paths.items()})


def _validate(reply, refno="=1/21", **args):
    return validate_paragon_reference_trace_receipt(reply, {"refno": refno, **args})


def _invalid(reply, refno="=1/21", **args):
    with pytest.raises(BridgeError) as error:
        _validate(reply, refno, **args)
    assert error.value.response_invalid
    assert error.value.diagnostics == {}


def test_real_transport_posts_only_fixed_parameters_and_preserves_both_paths(bridge, design_receipt):
    with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(design_receipt).encode())) as send:
        result = paragon_reference_trace(bridge, {"refno": "=1/21"})
    send.assert_called_once()
    request, = send.call_args.args
    assert request.get_method() == "POST"
    assert request.full_url == bridge.settings.bridge_url + PATH
    assert json.loads(request.data) == {"refno": "=1/21", "trace_limit": 24}
    assert result == design_receipt
    trace = result["result"]["trace"]
    assert trace["root.SPREF.CATREF"] == trace["root.SPREF.PRTREF.CATREF"]
    assert trace["root.SPREF.CMPREF"] == trace["root.SPREF.PRTREF.CMPREF"]
    assert result["result"]["trace_limit"]["used"] == 18


@pytest.mark.parametrize("args", [
    {}, {"refno": "CE"}, {"refno": "/CAT"}, {"refno": "=1/1\n"}, {"refno": "=1/１"},
    {"refno": "=01/0"}, {"refno": "=1/01"}, {"refno": "=0/0"}, {"refno": "=1/-1"},
    {"refno": "=2147483648/1"}, {"refno": "=1/2147483648"}, {"refno": 12},
    {"refno": "=1/1", "trace_limit": True}, {"refno": "=1/1", "trace_limit": "2"},
    {"refno": "=1/1", "trace_limit": 2.0}, {"refno": "=1/1", "trace_limit": None},
    {"refno": "=1/1", "trace_limit": 0}, {"refno": "=1/1", "trace_limit": 33},
    {"refno": "=1/1", "attributes": ["SPRE"]},
])
def test_invalid_arguments_fail_before_request(bridge, args):
    with patch.object(bridge, "_request") as request, pytest.raises(ValueError):
        paragon_reference_trace(bridge, args)
    request.assert_not_called()


@pytest.mark.parametrize("settings", [Settings(mode="mock", version="E3D21"), Settings(mode="bridge", version="E3D31")])
def test_unsupported_runtime_has_no_mock_or_legacy_result(settings):
    bridge = BridgeClient(settings)
    with patch.object(bridge, "_request") as request, pytest.raises(BridgeError, match="E3D21 bridge mode"):
        paragon_reference_trace(bridge, {"refno": "=1/21"})
    request.assert_not_called()


def test_registration_declares_only_read_only_parameters(bridge):
    server = McpServer("test", "1")
    register_paragon_reference_trace(server, bridge)
    descriptor, _ = server._tools[TOOL]
    assert descriptor["annotations"] == {
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False,
    }
    schema = descriptor["inputSchema"]
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["refno"]
    assert set(schema["properties"]) == {"refno", "trace_limit"}


@pytest.mark.parametrize("field", ["ok", "version", "session_id", "bridge_build", "context", "db_type_codes"])
def test_missing_envelope_evidence_cannot_return_a_trace(design_receipt, field):
    del design_receipt[field]
    _invalid(design_receipt)


def test_bare_native_prototype_result_cannot_claim_public_evidence(design_receipt):
    _invalid(design_receipt["result"])


@pytest.mark.parametrize("field", ["session_id", "bridge_build"])
def test_shared_http_transport_does_not_supply_missing_native_identity(bridge, design_receipt, field):
    del design_receipt[field]
    with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(design_receipt).encode())) as send:
        with pytest.raises(BridgeError) as error:
            paragon_reference_trace(bridge, {"refno": "=1/21"})
    send.assert_called_once()
    assert error.value.response_invalid


@pytest.mark.parametrize("field,bad_value", [
    ("session_id", "another-session"), ("bridge_build", "another-build"), ("project", "OTHER"),
    ("mdb", ""), ("current_element", "=0/0"), ("current_element", "=1/23"),
])
def test_missing_or_conflicting_boundary_values_reject_affirmative_receipt(design_receipt, field, bad_value):
    design_receipt["context"]["after"][field] = bad_value
    _invalid(design_receipt)


def test_verified_null_current_element_and_native_dbref_bounds():
    refno = "=2147483647/0"
    reply = _receipt({"root": _row(refno, "CATA", "=2147483647/2147483647")},
                     {"root": 2}, limit=1)
    reply["context"]["before"]["current_element"] = None
    reply["context"]["after"]["current_element"] = None
    assert _validate(reply, refno, trace_limit=1) == reply
    reply["context"]["same_mdb_instance"] = False
    _invalid(reply, refno, trace_limit=1)


@pytest.mark.parametrize("status,used,after_state", [
    ("busy", 0, "unread"), ("context_unverified", 0, "unread"),
    ("context_unverified", 5, "unread"), ("context_unverified", 5, "changed_ce"),
    ("context_unverified", 5, "changed_instance"),
])
def test_busy_and_boundary_loss_preserve_only_attempt_budget(status, used, after_state):
    reply = _receipt({}, {}, status=status, limit=5, used=used, reached=used == 5)
    context = reply["context"]
    if not used:
        context["before"] = None
    if after_state == "unread":
        context.update(after=None, same_project_instance=None, same_mdb_instance=None, boundary_agreement=None)
    else:
        context["boundary_agreement"] = False
        if after_state == "changed_ce":
            context["after"]["current_element"] = "=1/25"
        else:
            context["same_mdb_instance"] = False
    assert _validate(reply, trace_limit=5) == reply
    reply["result"]["trace"]["root"] = _row("=1/21", "ELBO")
    _invalid(reply, trace_limit=5)


def test_design_dbtype_controls_expansion_even_if_the_noun_matches_a_catalogue_kind():
    reply = _receipt({"root": _row("=1/21", "SPCO"), "root.SPREF": _row(status="unset")}, {"root": 1})
    assert _validate(reply) == reply
    reply["db_type_codes"]["root"] = 2
    _invalid(reply)


def test_dbtype_and_noun_both_need_to_match_the_fixed_edge():
    reply = _receipt({"root": _row("=1/21", "ELBO"),
                      "root.SPREF": _row("=7/1", "SPCO", status="out_of_scope")},
                     {"root": 1, "root.SPREF": 7}, status="partial")
    assert _validate(reply) == reply
    reply["db_type_codes"]["root.SPREF"] = 2
    _invalid(reply)
    reply["result"]["trace"]["root.SPREF"]["type"] = "SCOM"
    assert _validate(reply) == reply  # only NOZZ and ELCONN may follow a direct SCOM
    reply["result"]["trace"]["root"]["type"] = "NOZZ"
    _invalid(reply)  # the same recorded target is now in scope and cannot be labeled out_of_scope


def test_direct_nozzle_scom_path_has_no_inferred_specification():
    trace = {
        "root": _row("=1/21", "NOZZ"),
        "root.SPREF": _row("=2/4", "SCOM", "=2/2"),
        "root.SPREF.OWNER": _row("=2/2", "SECT", "=2/1"),
        "root.SPREF.OWNER.OWNER": _row("=2/1", "CATA"),
    }
    reply = _receipt(trace, {path: 1 if path == "root" else 2 for path in trace})
    assert _validate(reply) == reply
    trace["root.SPREF.MATREF"] = _row(status="unset")
    reply["result"]["trace_limit"]["used"] += 1
    _invalid(reply)


@pytest.mark.parametrize("row,db_types,status", [
    (_row(status="unset"), {}, "observed"),
    (_row(status="attribute_unavailable"), {}, "partial"),
    (_row(status="unresolved"), {}, "partial"),
    (_row("=2/9", status="unresolved"), {}, "partial"),
    (_row("=2/9", "SPCO", status="read_error"), {}, "partial"),
    (_row("=2/9", "SPCO", status="out_of_scope"), {"root.SPREF": 1}, "partial"),
])
def test_native_unknown_statuses_preserve_only_the_fields_actually_read(row, db_types, status):
    reply = _receipt({"root": _row("=1/21", "ELBO"), "root.SPREF": row}, {"root": 1, **db_types}, status=status)
    assert _validate(reply)["result"]["trace"]["root.SPREF"] == row


@pytest.mark.parametrize("source,owner_status", [
    ("resolved", "owner_missing"), ("owner_read_error", "owner_read_error"),
])
def test_owner_absence_and_read_failure_are_distinct(source, owner_status):
    reply = _receipt({"root": _row("=2/3", "CATE", status=source), "root.OWNER": _row(status=owner_status)},
                     {"root": 2}, status="partial")
    assert _validate(reply, "=2/3") == reply
    reply["result"]["trace"]["root.OWNER"]["status"] = "unset"
    _invalid(reply, "=2/3")


def test_owner_path_must_bind_to_the_direct_owner_dbref(design_receipt):
    row = design_receipt["result"]["trace"]["root.SPREF.CATREF"]
    row["owner"] = "=2/123"
    _invalid(design_receipt)


def test_cycle_repeats_an_active_ancestor_and_does_not_claim_an_unread_dbtype():
    terminal = "root.OWNER.OWNER"
    trace = {
        "root": _row("=2/10", "SPCO", "=2/9"),
        "root.CATREF": _row(status="unset"),
        "root.PRTREF": _row(status="unset"),
        "root.CMPREF": _row(status="unset"),
        "root.OWNER": _row("=2/9", "SELE", "=2/10"),
        terminal: _row("=2/10", "SPCO", status="cycle"),
    }
    reply = _receipt(trace, {"root": 2, "root.OWNER": 2}, status="partial")
    assert _validate(reply, "=2/10") == reply
    trace["root.OWNER"]["owner"] = trace[terminal]["ref"] = "=2/15"
    _invalid(reply, "=2/10")


def test_root_mismatch_preserves_requested_identity_only():
    trace = {"root": _row("=1/21", status="reference_mismatch")}
    reply = _receipt(trace, {}, status="unavailable")
    assert _validate(reply) == reply
    trace["root"]["ref"] = "=1/99"
    _invalid(reply)


def test_budget_only_removes_the_unread_dfs_suffix():
    trace = {"root": _row("=2/10", "SPCO", "=2/9"), "root.CATREF": _row(status="unset")}
    reply = _receipt(trace, {"root": 2}, status="partial", limit=2, reached=True)
    assert _validate(reply, "=2/10", trace_limit=2) == reply
    assert "root.PRTREF" not in trace
    trace["root.PRTREF"] = trace.pop("root.CATREF")
    _invalid(reply, "=2/10", trace_limit=2)
    trace["root.CATREF"] = trace.pop("root.PRTREF")
    reply["result"]["trace_limit"]["reached"] = False
    _invalid(reply, "=2/10", trace_limit=2)
    reply["result"]["trace_limit"]["limit"] = 3
    _invalid(reply, "=2/10", trace_limit=3)


def test_status_cannot_overstate_coverage_at_exact_budget():
    reply = _receipt({"root": _row("=2/7", "SPEC"), "root.MATREF": _row(status="unset"),
                      "root.FLUREF": _row(status="unset")}, {"root": 2}, limit=3)
    assert _validate(reply, "=2/7", trace_limit=3) == reply
    reply["result"]["trace_limit"]["reached"] = True
    reply["result"]["status"] = "partial"
    _invalid(reply, "=2/7", trace_limit=3)


@pytest.mark.parametrize("part,field,value", [
    ("result", "material_compatible", True), ("result", "engineering_connectivity", "connected"),
    ("root", "status", []), ("root", "type", 1), ("root", "owner", "=0/0"),
    ("trace_limit", "used", 18.0), ("trace_limit", "reached", 0), ("db_type_codes", "root", True),
])
def test_extra_claims_and_malformed_fields_reject_entire_receipt(design_receipt, part, field, value):
    result = design_receipt["result"]
    containers = {"result": result, "root": result["trace"]["root"], "trace_limit": result["trace_limit"],
                  "db_type_codes": design_receipt["db_type_codes"]}
    containers[part][field] = value
    _invalid(design_receipt)


@pytest.mark.parametrize("fault", [
    {"NativeFault": True}, {"NativeFault": {"HasFault": True}},
    {"native_database_fault": {"has_fault": True}}, {"error_code": "native_database_fault"},
    {"native_fault_latched": True},
])
def test_embedded_native_fault_stops_before_projecting_any_rows(bridge, design_receipt, fault):
    design_receipt["result"]["extra"] = [{"details": fault, "private": "do not forward"}]
    with patch.object(bridge, "_request", return_value=design_receipt) as request:
        with pytest.raises(BridgeError, match="native database fault") as error:
            paragon_reference_trace(bridge, {"refno": "=1/21"})
    request.assert_called_once()
    assert error.value.diagnostics == {
        "error_code": "native_database_fault", "native_fault_latched": True, "retry_safe": False,
    }


@pytest.mark.parametrize("http_status", [404, 423, 500, None])
def test_http_failure_passes_through_without_retry_or_fallback(bridge, http_status):
    body = io.BytesIO(b'{"ok":false,"error":"native database fault","error_code":"native_database_fault"}')
    http = (patch("urllib.request.urlopen", side_effect=HTTPError("http://bridge" + PATH, http_status, "", {}, body))
            if http_status is not None else patch("urllib.request.urlopen", side_effect=TimeoutError("offline timeout")))
    with http as send, pytest.raises(BridgeError) as error:
        paragon_reference_trace(bridge, {"refno": "=1/21"})
    send.assert_called_once()
    assert send.call_args.args[0].full_url == bridge.settings.bridge_url + PATH
    assert error.value.http_status == http_status
    assert error.value.transport_error is (http_status is None)

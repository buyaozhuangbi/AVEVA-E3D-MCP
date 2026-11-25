"""Recovery comparisons must never replay or certify an unknown creation."""
from contextlib import ExitStack
from copy import deepcopy
from unittest.mock import patch

import pytest

from test_piping_native_route import (
    ATTRS, CONTEXT, MODULE, PARENT, REQUEST, SETTINGS, BridgeClient, BridgeError,
    NativePipingRouteCreator, VERIFY_TOOL, _plan_revision, _schema_matches,
    _verification_macro, encoding, execution_args, mock_preparation,
    no_live_requests, prepared_state, register_native_piping_route, verified_native_readback,
)
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.protocol import McpServer


def verification_args(prepared, *, bound=True):
    args = {**deepcopy(REQUEST), "pipe_refno": "=1/70", "expected_session_id": "session"}
    if bound:
        args["expected_plan_revision"] = prepared["plan_revision"]
    return args


def verified_endpoints(source, dry_run, **kwargs):
    result = encoding(source, dry_run, **kwargs)
    result["result"]["messages"] = [{"description": "MCP_NATIVE_PIPE_ROUTE_VERIFICATION_COMPLETE"}]
    return result


def test_plan_fingerprint_survives_ce_and_parent_changes_but_create_revision_does_not():
    state, prepared = prepared_state()
    altered = deepcopy(state)
    altered["context"]["current_element"] = "=1/80"
    altered["parent"]["members"].insert(0, {"refno": "=1/90"})
    altered["source"] = "a different membership guard"
    assert _plan_revision(REQUEST, altered) == prepared["plan_revision"]
    context = deepcopy(CONTEXT)
    context["identity"]["current_element"] = "=1/80"
    with ExitStack() as stack:
        mock_preparation(stack, context=context)
        _, other = NativePipingRouteCreator(BridgeClient(SETTINGS))._prepare(REQUEST)
    assert other["plan_revision"] == prepared["plan_revision"]
    assert other["revision"] != prepared["revision"]


@pytest.mark.parametrize("mutate", [
    lambda s: s["context"].update(session_id="different"),
    lambda s: s["context"].update(project="different"),
    lambda s: s["spec"].update(refno="=2/99"),
    lambda s: s["template"]["pose"]["position"].__setitem__(0, 1),
    lambda s: s["endpoint_points"][0]["position"].__setitem__(0, 1),
    lambda s: s["elbows"][0]["position_world_mm"].__setitem__(0, 10),
    lambda s: s["centerline"]["elements"][0].update(length_mm=899),
])
def test_plan_fingerprint_binds_session_sources_spec_and_geometry(mutate):
    state, prepared = prepared_state()
    mutate(state)
    assert _plan_revision(REQUEST, state) != prepared["plan_revision"]


def test_existing_pipe_preparation_requires_exact_parent_membership_without_create_admission():
    creator = NativePipingRouteCreator(BridgeClient(SETTINGS))
    parent = deepcopy(PARENT)
    parent["members"].append({"refno": "=1/70", "type": "PIPE", "name": "/AUTO-PIPE", "sibling_index": 2})
    with ExitStack() as stack:
        mock_preparation(stack)
        stack.enter_context(patch(MODULE + "_members", side_effect=lambda _b, ref, _kind:
                                 deepcopy(parent) if ref == "=1/1" else {"members": []}))
        schema = stack.enter_context(patch.object(BridgeClient, "element_schema"))
        state, prepared = creator._prepare(REQUEST, existing_pipe_refno="=1/70")
        with pytest.raises(ValueError, match="does not belong"):
            creator._prepare(REQUEST, existing_pipe_refno="=1/71")
    schema.assert_not_called()
    assert "source" not in state and "revision" not in prepared
    assert prepared["plan_revision"] == prepared_state()[1]["plan_revision"]


def test_full_destination_is_rejected_before_creating_an_unverifiable_201st_member():
    creator = NativePipingRouteCreator(BridgeClient(SETTINGS))
    parent = deepcopy(PARENT)
    parent["members"] *= 200
    with ExitStack() as stack:
        mock_preparation(stack)
        stack.enter_context(patch(MODULE + "_members", return_value=parent))
        script = stack.enter_context(patch.object(BridgeClient, "execute_pml_script"))
        result = creator(REQUEST)["result"]
    assert result["status"] == "preflight_rejected" and "member slot" in result["errors"][0]["message"]
    script.assert_not_called()


def test_duplicate_while_first_request_is_running_reports_pending_without_reentering_native_code():
    state, prepared = prepared_state()
    creator = NativePipingRouteCreator(BridgeClient(SETTINGS))
    args = execution_args(prepared)
    observed = []
    def prepare(_request):
        observed.append(creator(args)["result"])
        return state, prepared
    with patch.object(creator, "_prepare", side_effect=prepare) as prepare_call, \
         patch.object(BridgeClient, "execute_pml_script", side_effect=encoding) as script, \
         patch.object(creator, "_verify", return_value={"created": [{"refno": "=1/70"}]}):
        result = creator(args)["result"]
    assert result["status"] == "created_verified"
    assert observed[0]["status"] == "request_in_progress" and observed[0]["request_in_progress"] is True
    assert prepare_call.call_count == script.call_count == 1


def test_preflight_read_timeout_does_not_claim_creation_was_submitted_or_uncertain():
    creator = NativePipingRouteCreator(BridgeClient(SETTINGS))
    with patch.object(creator, "_prepare", side_effect=BridgeError("read timeout", transport_error=True)), \
         patch.object(BridgeClient, "execute_pml_script") as script:
        result = creator(REQUEST)["result"]
    assert result["status"] == "preflight_rejected"
    assert result["outcome_uncertain"] is result["execution_attempted"] is False
    script.assert_not_called()


def test_recovery_readback_accepts_changed_ce_and_target_position_in_current_parent_order():
    def alter(observed):
        observed["context"]["identity"]["current_element"] = "=1/999"
        observed["parent"]["members"].reverse()
        for index, row in enumerate(observed["parent"]["members"], 1):
            row["sibling_index"] = index
    readback = verified_native_readback(recovery=True, mutate=alter)
    assert readback["context"]["current_element"] == "=1/999"
    assert readback["external_connections_verified_unset"] is True


def test_generated_verification_script_only_asserts_explicit_native_world_endpoints():
    state, _ = prepared_state()
    source = _verification_macro(REQUEST, state, "=1/71")
    assert "!newbranch = !s.dbref()" in source
    for field in ("hpos", "tpos", "hdir", "tdir"):
        assert "!newbranch." + field in source
    assert ".wrt( /* )" in source
    assert "MCP_NATIVE_PIPE_ROUTE_VERIFICATION_COMPLETE" in source
    for forbidden in ("NEW ", "DELETE ", "SAVEWORK", "CONNECT ", "!newbranch = !!ce", "\n=1/71\n", "HPOS ", "TPOS ", "ADD "):
        assert forbidden not in source
    with pytest.raises(ValueError):
        _verification_macro(REQUEST, state, "=1/71\nDELETE CE")


@pytest.mark.parametrize("bound", [True, False])
def test_fresh_creator_verifies_without_creation_records_and_preserves_provenance_limits(bound):
    state, prepared = prepared_state()
    creator = NativePipingRouteCreator(BridgeClient(SETTINGS))
    with patch(MODULE + "_context", return_value=CONTEXT), \
         patch.object(creator, "_prepare", return_value=(state, prepared)), \
         patch(MODULE + "_query", return_value={"refno": "=1/71", "type": "BRAN"}), \
         patch.object(BridgeClient, "execute_pml_script", side_effect=verified_endpoints) as script, \
         patch.object(creator, "_verify", return_value={"created": [{"refno": "=1/70"}]}) as readback:
        result = creator.verify(verification_args(prepared, bound=bound))["result"]
    assert result["status"] == "model_matches_plan" and result["readback_verified"] is True
    assert result["plan_binding_verified"] is bound
    assert result["creation_attribution_verified"] is result["model_write_attempted"] is False
    assert result["readback"]["historical_parent_changes_verified"] is False
    assert result["readback"]["branch_endpoints_verified"] is True
    assert "created" not in result["readback"]
    assert creator._requests == {}
    assert script.call_count == readback.call_count == 1
    assert "NEW " not in script.call_args.args[0]
    readback.assert_called_once_with(REQUEST, state, existing_pipe_refno="=1/70")


@pytest.mark.parametrize("field,value,stage", [
    ("expected_session_id", "wrong-session", "context"),
    ("expected_plan_revision", "0" * 64, "plan"),
])
def test_recovery_mismatch_stops_before_any_native_script(field, value, stage):
    state, prepared = prepared_state()
    creator = NativePipingRouteCreator(BridgeClient(SETTINGS))
    with patch(MODULE + "_context", return_value=CONTEXT), \
         patch.object(creator, "_prepare", return_value=(state, prepared)), \
         patch.object(BridgeClient, "execute_pml_script") as script:
        result = creator.verify({**verification_args(prepared), field: value})["result"]
    assert result["status"] == "verification_failed" and result["errors"][0]["stage"] == stage
    assert result["native_script_execution_attempted"] is False
    script.assert_not_called()


@pytest.mark.parametrize("failure", [BridgeError("timeout", transport_error=True), None, {"result": None}])
def test_readonly_verification_uncertainty_retains_own_journal_without_retry_or_readback(failure):
    state, prepared = prepared_state()
    creator = NativePipingRouteCreator(BridgeClient(SETTINGS))
    with patch(MODULE + "_context", return_value=CONTEXT), \
         patch.object(creator, "_prepare", return_value=(state, prepared)), \
         patch(MODULE + "_query", return_value={"refno": "=1/71", "type": "BRAN"}), \
         patch.object(BridgeClient, "execute_pml_script", **({"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure})) as script, \
         patch.object(creator, "_verify") as readback:
        result = creator.verify(verification_args(prepared))["result"]
    assert result["status"] == "verification_uncertain" and result["retry_safe"] is False
    assert result["model_write_attempted"] is False
    assert result["pml_script_request"]["expected_session_id"] == "session"
    script.assert_called_once()
    readback.assert_not_called()


@pytest.mark.parametrize("change", [
    lambda r: r.update(result=None),
    lambda r: r["result"].update(source_sha256="0" * 64),
    lambda r: r["result"].update(success=False, outcome_uncertain=True),
    lambda r: r["result"].update(execution_completed=False),
])
def test_creation_preserves_native_or_malformed_receipt_uncertainty(change):
    state, prepared = prepared_state()
    reply = encoding(state["source"], False)
    change(reply)
    creator = NativePipingRouteCreator(BridgeClient(SETTINGS))
    with patch.object(creator, "_prepare", return_value=(state, prepared)), \
         patch.object(BridgeClient, "execute_pml_script", return_value=reply), \
         patch.object(creator, "_verify") as readback:
        result = creator(execution_args(prepared))["result"]
    assert result["status"] == "outcome_uncertain" and result["outcome_uncertain"] is True
    assert result["verification_request"]["arguments"]["expected_plan_revision"] == prepared["plan_revision"]
    readback.assert_not_called()


def test_recovery_schema_permission_and_extra_fields_fail_before_native_activity():
    _, prepared = prepared_state()
    server = McpServer("native-pipe", "0")
    register_native_piping_route(server, BridgeClient(SETTINGS))
    catalog = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    tool = next(row for row in catalog if row["name"] == VERIFY_TOOL)
    assert tool["annotations"]["readOnlyHint"] is True
    args = verification_args(prepared)
    assert _schema_matches(args, tool["inputSchema"]) is True
    for extra in ({"dry_run": False}, {"source": "DELETE CE"}, {"expected_plan_revision": "BAD"}, {"pipe_refno": "/AUTO-PIPE"}):
        assert _schema_matches({**args, **extra}, tool["inputSchema"]) is False
        with pytest.raises(ValueError):
            NativePipingRouteCreator(BridgeClient(SETTINGS)).verify({**args, **extra})
    creator = NativePipingRouteCreator(BridgeClient(Settings(mode="bridge", version="E3D21", allow_commands=False)))
    with patch.object(creator, "_prepare") as prepare:
        with pytest.raises(PermissionError, match="generated read-only PML"):
            creator.verify(args)
    prepare.assert_not_called()

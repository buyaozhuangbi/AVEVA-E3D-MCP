from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
import hashlib
import math
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.piping_native_route import (
    NativePipingRouteCreator, TOOL, VERIFY_TOOL, _direction, _macro, _millimetres, _parameters, _place, _point,
    _plan_revision, _spec_ancestor, _template_bend, _verification_macro, register_native_piping_route,
)
from aveva_e3d_mcp.protocol import McpServer, _schema_matches
from test_piping_centerline import OPTIONS, endpoint


MODULE = "aveva_e3d_mcp.piping_native_route."
SETTINGS = Settings(mode="bridge", version="E3D21", allow_commands=True)
REQUEST = {
    "parent_refno": "=1/1", "pipe_name": "/AUTO-PIPE", "template_branch_refno": "=1/2",
    "template_elbow_refno": "=1/3", "endpoints": [
        {"refno": "=1/4", "expected_type": "NOZZ", "point_number": 1},
        {"refno": "=1/5", "expected_type": "NOZZ", "point_number": 1}], "centerline": deepcopy(OPTIONS),
}
CONTEXT = {"complete": True, "identity": {"version": "E3D21", "session_id": "session", "bridge_build": "build",
    "project": "PRJ", "mdb": "MDB", "current_element": "=1/50"}}
PARENT = {"parent": {"refno": "=1/1", "type": "ZONE", "name": "/ZONE"},
          "members": [{"refno": "=1/60", "type": "PIPE", "name": "/EXISTING", "sibling_index": 1}]}
ID = {
    "/SPEC": {"refno": "=2/1", "type": "SPEC", "name": "SPEC"},
    "/SPEC/TUBE": {"refno": "=2/2", "type": "SPCO", "name": "SPEC/TUBE"},
    "/SPEC/ELBO": {"refno": "=2/3", "type": "SPCO", "name": "SPEC/ELBO"},
}
ATTRS = {
    ("=1/2", "BRAN"): {"values": {"PSPE": "/SPEC", "HSTU": "/SPEC/TUBE"}, "typed_values": {}},
    ("=1/2", "TUBI"): {"values": {"SPRE": "/SPEC/TUBE", "ABOR": "50mm", "LBOR": "50mm"}, "typed_values": {"ABOR": 50, "LBOR": 50}},
    ("=1/3", "ELBO"): {"values": {"SPRE": "/SPEC/ELBO", "LSTU": "/SPEC/TUBE", "CREF": "Nulref"},
                        "typed_values": {"ANGL": 90, "RADI": 0}},
    ("=2/2", "SPCO"): {"values": {"OWNER": "/SPEC"}, "typed_values": {}},
    ("=2/3", "SPCO"): {"values": {"OWNER": "/SPEC"}, "typed_values": {}},
}
POSE = {"position": [0, 0, 0], "axes": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}


@pytest.fixture(autouse=True)
def no_live_requests():
    with patch.object(BridgeClient, "_request", side_effect=AssertionError("No live native activity in tests")):
        yield


def group(position, direction, number=1):
    value = endpoint(position, direction, number)
    point = value["response"]["result"]["points"][0]
    point["bore"] = {"available": True, "unit": "mm", "value_mm": 50}
    return value


def native_points(_bridge, ep, _before, _numbers):
    if ep["refno"] == "=1/3":
        value = group([-100, 0, 0], [-1, 0, 0])
        value["response"]["result"]["points"].append(group([0, 100, 0], [0, 1, 0], 2)["response"]["result"]["points"][0])
        value["response"]["result"]["references"] = {
            "SPRE": {"refno": "=2/3", "status": "resolved"}, "CATR": {"refno": "=2/4", "status": "resolved"}}
    else:
        value = group([0, 0, 0], [1, 0, 0]) if ep["refno"] == "=1/4" else group([1000, 1000, 0], [0, -1, 0])
    return {**value, **ep}


def encoding(source, dry_run, **_kwargs):
    return {"ok": True, "version": "E3D21", "result": {"success": True, "dry_run": dry_run,
        "execution_attempted": not dry_run, "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "execution_completed": not dry_run, "native_run_returned": not dry_run, "outcome_uncertain": False,
        "messages": [{"description": "MCP_NATIVE_PIPE_ROUTE_COMPLETE"}] if not dry_run else []}}


def mock_preparation(stack, *, attrs=None, context=None):
    stack.enter_context(patch(MODULE + "_context", side_effect=lambda _bridge: deepcopy(CONTEXT if context is None else context)))
    stack.enter_context(patch(MODULE + "_members", side_effect=lambda _b, ref, _kind:
        deepcopy(PARENT) if ref == "=1/1" else {"members": []}))
    schema = {"ok": True, "version": "E3D21", "result": {"success": True,
        "element": {"refno": "=1/1", "type": "ZONE"}, "child_types": [{"short_name": "PIPE", "sdk_creatable_now": True}]}}
    stack.enter_context(patch.object(BridgeClient, "element_schema", return_value=schema))
    values = deepcopy(ATTRS if attrs is None else attrs)
    stack.enter_context(patch(MODULE + "_attributes", side_effect=lambda _b, ref, kind, _names: deepcopy(values[ref, kind])))
    stack.enter_context(patch(MODULE + "_query", side_effect=lambda _b, ref, kind=None: deepcopy(ID[ref])))
    stack.enter_context(patch(MODULE + "_spec_ancestor", return_value="=2/1"))
    stack.enter_context(patch(MODULE + "_points", side_effect=native_points))
    stack.enter_context(patch(MODULE + "_pose", return_value=deepcopy(POSE)))
    stack.enter_context(patch(MODULE + "_source_binding", return_value={"matches_enclosing_context": True}))


def prepared_state(request=None):
    creator = NativePipingRouteCreator(BridgeClient(SETTINGS))
    with ExitStack() as stack:
        mock_preparation(stack)
        return creator._prepare(deepcopy(REQUEST if request is None else request))


def execution_args(prepared):
    return {**deepcopy(REQUEST), "dry_run": False, "expected_revision": prepared["revision"],
            "expected_session_id": prepared["expected_session_id"], "request_id": "00000000-0000-4000-8000-000000000001"}


def test_preview_uses_exact_generated_native_commands_without_execution():
    creator = NativePipingRouteCreator(BridgeClient(SETTINGS))
    with ExitStack() as stack:
        mock_preparation(stack)
        call = stack.enter_context(patch.object(BridgeClient, "execute_pml_script", side_effect=encoding))
        result = creator(deepcopy(REQUEST))["result"]
    assert result["success"] is True and result["status"] == "prepared"
    assert result["execution_attempted"] is result["readback_verified"] is False
    call.assert_called_once_with(result["native_source"], True)
    source = result["native_source"]
    assert "NEW PIPE /AUTO-PIPE\n" in source and "NEW BRAN /AUTO-PIPE/B1\n" in source
    assert "NEW ELBO /AUTO-PIPE/E01\n" in source
    assert "SPREF =2/3" in source and "HSTU =2/2" in source and "LSTU =2/2" in source
    assert "HPOS E 0mm N 0mm U 0mm WRT /*" in source
    assert "ORI X IS E AND Y IS N WRT /*" in source
    assert ".wrt(/*)" not in source
    assert "!actualpos = !actualpos.wrt( /* )" in source
    for forbidden in ("NEW TUBI", "CYLI", "BOX", "CONNECT", "COPY", "DELETE", "SAVEWORK", "ADD ", "DESIGN"):
        assert forbidden not in source
    assert result["elbow_placements"][0]["position_world_mm"] == pytest.approx([1000, 0, 0])


def test_straight_native_route_emits_only_pipe_and_branch_with_one_implicit_tube():
    request = deepcopy(REQUEST)
    request["centerline"]["waypoints_world_mm"] = []
    def straight_points(bridge, ep, before, numbers):
        if ep["refno"] == "=1/5":
            return {**group([1000, 0, 0], [-1, 0, 0]), **ep}
        return native_points(bridge, ep, before, numbers)
    creator = NativePipingRouteCreator(BridgeClient(SETTINGS))
    with ExitStack() as stack:
        mock_preparation(stack)
        stack.enter_context(patch(MODULE + "_points", side_effect=straight_points))
        state, _ = creator._prepare(request)
    assert state["elbows"] == []
    assert "NEW ELBO" not in state["source"] and "NEW TUBI" not in state["source"]
    assert state["centerline"]["centerline_length_mm"] == 1000


def test_prewrite_point_guards_precede_the_first_creation_command():
    state, _ = prepared_state()
    source = state["source"]
    guard_end = source.index("Native anchor or template direction changed", source.index("!actualpos = !source.ppos"))
    assert guard_end < source.index("NEW PIPE")
    assert "var !observedref REFNO OF SPRE OF =1/3" in source
    assert "!ref ne '=1/60'" in source


def test_request_and_native_state_changes_invalidate_revision():
    state, first = prepared_state()
    assert prepared_state()[1] == first
    changed = deepcopy(REQUEST)
    changed["pipe_name"] = "/OTHER-PIPE"
    assert prepared_state(changed)[1]["revision"] != first["revision"]
    source = _macro(REQUEST, state)
    changed_state = deepcopy(state)
    changed_state["parent"]["members"].reverse()
    changed_state["parent"]["members"][0]["refno"] = "=1/61"
    assert _macro(REQUEST, changed_state) != source


@pytest.mark.parametrize("change", ["radius", "bore", "variable", "linked", "angle", "tube"])
def test_incompatible_native_templates_fail_before_any_encoding_or_write(change):
    request, attrs = deepcopy(REQUEST), deepcopy(ATTRS)
    if change == "radius": request["centerline"]["bend_radius_mm"] = 90
    elif change == "bore": attrs["=1/2", "TUBI"]["typed_values"]["LBOR"] = 40
    elif change == "variable": attrs["=1/3", "ELBO"]["typed_values"]["RADI"] = 100
    elif change == "linked": attrs["=1/3", "ELBO"]["values"]["CREF"] = "/LINKED"
    elif change == "angle": attrs["=1/3", "ELBO"]["typed_values"]["ANGL"] = 45
    else: attrs["=1/3", "ELBO"]["values"]["LSTU"] = "/SPEC/ELBO"
    creator = NativePipingRouteCreator(BridgeClient(SETTINGS))
    with ExitStack() as stack:
        mock_preparation(stack, attrs=attrs)
        call = stack.enter_context(patch.object(BridgeClient, "execute_pml_script"))
        result = creator(request)["result"]
    assert result["status"] == "preflight_rejected" and result["success"] is False
    assert result["errors"]
    call.assert_not_called()


def test_execution_revision_conflict_never_dispatches():
    state, prepared = prepared_state()
    creator = NativePipingRouteCreator(BridgeClient(SETTINGS))
    args = execution_args(prepared)
    args["expected_revision"] = "0" * 64
    with patch.object(creator, "_prepare", return_value=(state, prepared)), patch.object(BridgeClient, "execute_pml_script") as write:
        result = creator(args)["result"]
    assert result["status"] == "preflight_rejected" and result["execution_attempted"] is False
    write.assert_not_called()


def test_duplicate_completed_request_returns_original_without_native_reads_or_replay():
    state, prepared = prepared_state()
    creator = NativePipingRouteCreator(BridgeClient(SETTINGS))
    args = execution_args(prepared)
    with patch.object(creator, "_prepare", return_value=(state, prepared)) as prepare, \
         patch.object(BridgeClient, "execute_pml_script", side_effect=encoding) as write, \
         patch.object(creator, "_verify", return_value={"created": [{"refno": "=1/70"}]}) as verify:
        first = creator(args)["result"]
        second = creator(args)["result"]
    assert first["status"] == second["status"] == "created_verified"
    assert second["duplicate_request"] is True
    assert prepare.call_count == write.call_count == verify.call_count == 1
    with pytest.raises(ValueError, match="different route arguments"):
        creator({**args, "pipe_name": "/CHANGED"})


def test_uncertain_transport_retains_native_journal_ids_without_followup_or_replay():
    state, prepared = prepared_state()
    creator = NativePipingRouteCreator(BridgeClient(SETTINGS))
    with patch.object(creator, "_prepare", return_value=(state, prepared)), \
         patch.object(BridgeClient, "execute_pml_script", side_effect=BridgeError("timeout", transport_error=True)) as write, \
         patch.object(creator, "_verify") as verify:
        result = creator(execution_args(prepared))["result"]
    assert result["outcome_uncertain"] is True and result["retry_safe"] is False
    assert result["pml_script_request"]["status_tool"] == "e3d_pml_script_status"
    assert result["residual_state_unknown"] is True
    write.assert_called_once()
    verify.assert_not_called()


def test_failed_native_script_stops_without_verification_or_compensating_delete():
    state, prepared = prepared_state()
    creator = NativePipingRouteCreator(BridgeClient(SETTINGS))
    failure = encoding(state["source"], False)
    failure["result"].update(success=False, error="Native setter failed")
    with patch.object(creator, "_prepare", return_value=(state, prepared)), \
         patch.object(BridgeClient, "execute_pml_script", return_value=failure) as write, patch.object(creator, "_verify") as verify:
        result = creator(execution_args(prepared))["result"]
    assert result["status"] == "failed_after_submission" and result["native_execution"] == failure
    assert result["residual_state_unknown"] is True
    write.assert_called_once()
    verify.assert_not_called()


def test_verify_failure_cannot_claim_created_verified():
    state, prepared = prepared_state()
    creator = NativePipingRouteCreator(BridgeClient(SETTINGS))
    with patch.object(creator, "_prepare", return_value=(state, prepared)), \
         patch.object(BridgeClient, "execute_pml_script", side_effect=encoding), \
         patch.object(creator, "_verify", side_effect=ValueError("Tube length mismatch")):
        result = creator(execution_args(prepared))["result"]
    assert result["success"] is result["readback_verified"] is False
    assert result["status"] == "failed_after_submission"


@pytest.mark.parametrize("key,value", [
    ("parent_refno", "CE"), ("parent_refno", "/ZONE"), ("parent_refno", "=01/1"),
    ("pipe_name", "/NAME\nDELETE CE"), ("pipe_name", "/NAME;DELETE"), ("pipe_name", "/A'B"),
    ("pipe_name", "/NAME*"), ("pipe_name", "/NAME//CHILD"), ("pipe_name", "/NAME/"),
    ("pipe_name", "/" + "A" * 36), ("dry_run", 1), ("expected_revision", "x" * 64), ("request_id", "abc"),
])
def test_invalid_inputs_fail_before_native_reads(key, value):
    with pytest.raises(ValueError):
        _parameters({**deepcopy(REQUEST), key: value})


def test_no_permission_or_mock_native_execution_never_reads_sdk():
    _, prepared = prepared_state()
    readonly = NativePipingRouteCreator(BridgeClient(Settings(mode="bridge", version="E3D21", allow_commands=False)))
    with patch.object(readonly, "_prepare") as read:
        with pytest.raises(PermissionError): readonly(execution_args(prepared))
    read.assert_not_called()
    mock = NativePipingRouteCreator(BridgeClient(Settings(mode="mock", version="E3D21")))
    assert mock(REQUEST)["result"]["available"] is False


def test_tool_discovery_and_conditional_execution_schema():
    server = McpServer("native-pipe", "0")
    register_native_piping_route(server, BridgeClient(SETTINGS))
    definition = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"][0]
    assert definition["name"] == TOOL
    assert definition["annotations"] == {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
    schema = definition["inputSchema"]
    assert _schema_matches(REQUEST, schema) is True
    assert _schema_matches({**REQUEST, "dry_run": False}, schema) is False
    assert _schema_matches(execution_args(prepared_state()[1]), schema) is True


@pytest.mark.parametrize("axis", [[1, 0, 0], [0, -1, 0], [0, 0, 1], [0.3, 0.4, math.sqrt(0.75)]])
def test_direction_text_is_numeric_and_axis_format_is_correct(axis):
    text = _direction(axis)
    assert all(char in "NSEWUD0123456789.- " for char in text)
    if axis == [1, 0, 0]: assert text == "E"


def test_rigid_template_mapping_preserves_both_native_points_and_right_handed_axes():
    first = {"position": [-100, 0, 0], "direction": [-1, 0, 0]}
    second = {"position": [0, 100, 0], "direction": [0, 1, 0]}
    template = _template_bend(POSE, first, second, 0.01)
    bend = {"turn_degrees": 90, "start_world_mm": [500, 600, 700],
            "end_world_mm": [600, 600, 600], "incoming_direction": [0, 0, -1],
            "outgoing_direction": [1, 0, 0], "normal_world": [0, -1, 0]}
    placed = _place(template, bend, "/BEND")
    assert placed["position_world_mm"] == pytest.approx([500, 600, 600])
    x, y, z = placed["axes_world"]
    cross = [x[1]*y[2]-x[2]*y[1], x[2]*y[0]-x[0]*y[2], x[0]*y[1]-x[1]*y[0]]
    assert cross == pytest.approx(z)


def test_unknown_or_contradictory_native_fields_never_become_template_dimensions():
    value = group([0, 0, 0], [1, 0, 0])
    value["response"]["result"]["points"][0]["position"]["unit"] = "in"
    with pytest.raises(ValueError): _point(value, 1)


def test_spec_owner_cycle_and_non_spec_owner_are_rejected():
    sample = {key: CONTEXT["identity"][key] for key in ("session_id", "bridge_build", "project", "mdb")}
    sample["current_element"] = {"refno": "=1/50"}
    native = {"ok": True, "version": "E3D21", "session_id": "session", "bridge_build": "build", "result": {
        "context": {"before": sample, "after": deepcopy(sample), "same_project_instance": True,
                    "same_mdb_instance": True, "boundary_agreement": True},
        "trace": {"root": {"ref": "=2/2", "type": "SPCO", "status": "resolved", "owner": "=2/2"},
                  "root.OWNER": {"ref": "=2/2", "type": "SELE", "status": "resolved", "owner": "=2/2"}},
        "db_type_codes": {"root": 2, "root.OWNER": 2}}}
    with patch(MODULE + "paragon_reference_trace", return_value=native):
        with pytest.raises(ValueError, match="Cyclic"):
            _spec_ancestor(BridgeClient(SETTINGS), ID["/SPEC/TUBE"], CONTEXT)


def verified_native_readback(*, mutate=None, recovery=False):
    state, _ = prepared_state()
    creator = NativePipingRouteCreator(BridgeClient(SETTINGS))
    context = deepcopy(CONTEXT)
    context["identity"]["current_element"] = "=1/70"
    pipe = {"refno": "=1/70", "type": "PIPE", "name": "AUTO-PIPE"}
    branch = {"refno": "=1/71", "type": "BRAN", "name": "AUTO-PIPE/B1"}
    parent = deepcopy(PARENT)
    parent["members"][0]["sibling_index"] = 2
    parent["members"].insert(0, {"refno": "=1/70", "type": "PIPE", "name": "/AUTO-PIPE", "sibling_index": 1})
    rows = [{"refno": "=1/71", "type": "BRAN", "parent_index": 0},
            {"refno": "=1/71", "type": "TUBI", "parent_index": 1},
            {"refno": "=1/72", "type": "ELBO", "parent_index": 1, "name": "/AUTO-PIPE/E01"},
            {"refno": "=1/72", "type": "TUBI", "parent_index": 1}]
    manifest_sample = {key: context["identity"][key] for key in ("project", "mdb", "current_element")}
    tree = {"ok": True, "version": "E3D21", "session_id": "session", "bridge_build": "build", "result": {
        "success": True, "complete": True, "traversal_complete": True, "rows": rows,
        "root": {"refno": "=1/70", "type": "PIPE"}, "context": {"before": manifest_sample, "after": deepcopy(manifest_sample),
        "same_project_instance": True, "same_mdb_instance": True, "boundary_agreement": True}}}
    attrs = {
        ("=1/70", "PIPE"): {"values": {"PSPE": "/SPEC"}, "typed_values": {}},
        ("=1/71", "BRAN"): {"values": {"PSPE": "/SPEC", "HSTU": "/SPEC/TUBE", "HREF": "Nulref", "TREF": "Nulref",
            "HBOR": "50mm", "TBOR": "50mm", "HCON": "OPEN", "TCON": "OPEN"}, "typed_values": {"LHEAD": True, "LTAIL": True, "HBOR": 50, "TBOR": 50}},
        ("=1/71", "TUBI"): {"values": {"SPRE": "/SPEC/TUBE", "ITLEN": "900mm", "ABOR": "50mm", "LBOR": "50mm"},
            "typed_values": {"ITLEN": 900, "ABOR": 50, "LBOR": 50}},
        ("=1/72", "TUBI"): {"values": {"SPRE": "/SPEC/TUBE", "ITLEN": "900mm", "ABOR": "50mm", "LBOR": "50mm"},
            "typed_values": {"ITLEN": 900, "ABOR": 50, "LBOR": 50}},
    }
    bend = state["elbows"][0]
    native_group = group(bend["expected_points"][0]["position"], bend["expected_points"][0]["direction"])
    native_group["response"]["result"]["points"].append(
        group(bend["expected_points"][1]["position"], bend["expected_points"][1]["direction"], 2)["response"]["result"]["points"][0])
    native_group["response"]["result"]["references"] = {"SPRE": {"refno": "=2/3"}, "CATR": {"refno": "=2/4"}}
    observed = {"parent": parent, "tree": tree, "attrs": attrs, "group": native_group, "context": context}
    if mutate:
        mutate(observed)
    if recovery:
        state["context"] = deepcopy(context["identity"])
        state["parent"] = deepcopy(parent)
        for sample in tree["result"]["context"]["before"], tree["result"]["context"]["after"]:
            sample["current_element"] = context["identity"]["current_element"]
    identities = {**ID, "/AUTO-PIPE": pipe, "/AUTO-PIPE/B1": branch}
    with ExitStack() as stack:
        stack.enter_context(patch(MODULE + "_context", return_value=context))
        stack.enter_context(patch(MODULE + "_query", side_effect=lambda _b, ref, kind: deepcopy(identities[ref])))
        stack.enter_context(patch(MODULE + "_members", return_value=parent))
        stack.enter_context(patch(MODULE + "pipe_member_manifest", return_value=tree))
        stack.enter_context(patch(MODULE + "_attributes", side_effect=lambda _b, ref, kind, names: deepcopy(attrs[ref, kind])))
        stack.enter_context(patch(MODULE + "_points", return_value=native_group))
        return creator._verify(REQUEST, state, existing_pipe_refno="=1/70") if recovery else creator._verify(REQUEST, state)


def test_real_verifier_accepts_new_first_member_preserving_old_order_and_native_tube_lengths():
    readback = verified_native_readback()
    assert readback["external_connections_verified_unset"] is True
    assert len(readback["elbows"]) == 1
    assert [row["attributes"]["typed_values"]["ITLEN"] for row in readback["implicit_tubes"]] == [900, 900]


@pytest.mark.parametrize("mutate", [
    lambda o: o["parent"]["members"][1].update(refno="=1/61"),
    lambda o: o["tree"]["result"].update(complete=False),
    lambda o: o["tree"].update(session_id="another-session"),
    lambda o: o["tree"]["result"]["root"].update(refno="=1/90"),
    lambda o: o["tree"]["result"]["context"]["after"].update(project="DIFFERENT"),
    lambda o: o["tree"]["result"]["rows"][2].update(type="TEE"),
    lambda o: o["tree"]["result"]["rows"][2].update(name="/OTHER"),
    lambda o: o["tree"]["result"]["rows"][3].update(parent_index=0),
    lambda o: o["attrs"]["=1/71", "BRAN"]["values"].update(HREF="/OTHER"),
    lambda o: o["attrs"]["=1/71", "BRAN"]["typed_values"].update(LHEAD=False),
    lambda o: o["attrs"]["=1/71", "BRAN"]["values"].update(HCON="CLOSED"),
    lambda o: o["attrs"]["=1/70", "PIPE"]["values"].update(PSPE="/SPEC/TUBE"),
    lambda o: o["attrs"]["=1/72", "TUBI"]["values"].update(ITLEN="900in"),
    lambda o: o["attrs"]["=1/72", "TUBI"]["typed_values"].update(ITLEN=500),
    lambda o: o["attrs"]["=1/72", "TUBI"]["typed_values"].update(ABOR=40),
    lambda o: o["group"]["response"]["result"]["points"][0]["position"].update(x=801),
    lambda o: o["group"]["response"]["result"]["points"][0]["direction"].update(x=0, y=-1),
    lambda o: o["group"]["response"]["result"]["references"]["CATR"].update(refno="=2/5"),
    lambda o: o["context"]["identity"].update(session_id="different"),
    lambda o: o["context"]["identity"].update(current_element="=1/999"),
])
def test_real_verifier_rejects_wrong_hierarchy_geometry_units_specification_and_context(mutate):
    with pytest.raises(ValueError):
        verified_native_readback(mutate=mutate)


@pytest.mark.parametrize("text,value", [("10in", 10), ("unset", 10), ("10mm", True), ("10mm", 25.4), ("10mm", float("inf"))])
def test_native_millimetre_interpretation_requires_matching_formatted_and_typed_evidence(text, value):
    with pytest.raises(ValueError):
        _millimetres({"values": {"ITLEN": text}, "typed_values": {"ITLEN": value}}, "ITLEN")

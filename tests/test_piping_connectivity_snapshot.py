from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aveva_e3d_mcp.branch_snapshot import ATTRIBUTES
from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.piping_connectivity_snapshot import (
    PATH, connectivity_snapshot_parameters, connectivity_snapshot_report, piping_connectivity_snapshot,
    register_connectivity_snapshot,
)
from aveva_e3d_mcp.protocol import McpServer

IDENTITY = {"session_id": "session", "bridge_build": "build", "project": "APS", "mdb": "ALL",
            "current_element": "=1/90", "current_element_type": "EQUI"}
START = {"refno": "=1/2", "expected_type": "ELBO", "point_number": 1}
SECOND = {"refno": "=1/3", "expected_type": "TUBI", "point_number": 2}
ARGS = {"branches": [{"refno": "=1/1", "expected_type": "BRAN"}],
        "pairs": [{"first": START, "second": SECOND, "tolerance_mm": 0}], "starts": [START]}


def context():
    return {"before": deepcopy(IDENTITY), "after": deepcopy(IDENTITY), "same_project_instance": True,
            "same_mdb_instance": True, "boundary_agreement": True}


def endpoint(spec):
    return {"requested_refno": spec["refno"], "requested_expected_type": spec["expected_type"],
            "requested_type": spec["expected_type"], "point_number": spec["point_number"],
            "status": "exists", "complete": True, "errors": {},
            "element": {"refno": spec["refno"], "type": spec["expected_type"], "status": "exists"},
            "exists": {"available": True, "value": True},
            "point": {"number": spec["point_number"], "complete": True,
                      "connection_reference": {"available": True, "resolution_complete": True,
                                               "point_number": None, "reference": {"status": "unset"}}}}


def sample(args=None):
    request = connectivity_snapshot_parameters(ARGS if args is None else args)

    def item(kind, index, wanted, data):
        return {"kind": kind, "index": index, "requested": wanted, "read_complete": True, "complete": True,
                "matches_batch_context": True, "context": context(), "read_errors": [], "data": data}

    branches = []
    for index, wanted in enumerate(request["branches"]):
        branch_identity = {"refno": wanted["refno"], "type": "BRAN"}
        branches.append(item("branch", index, wanted, {"element": branch_identity,
            "attributes": {"success": True, **branch_identity, "values": {name: "native:" + name for name in ATTRIBUTES},
                           "typed_values": {}, "errors": {}, "typed_read_errors": {}},
            "members": [{"refno": "=1/3", "type": kind, "sibling_index": ordinal, "complete": True}
                        for ordinal, kind in enumerate(("ELBO", "TUBI"), 1)],
            "returned_count": 2, "member_limit": request["member_limit"], "has_more": False, "truncated": False,
            "member_probe_count": 3, "member_probe_limit": request["member_limit"] + 1,
            "lookahead_probe_count": int(request["member_limit"] == 2), "read_errors": []}))
    pairs = [item("pair", index, wanted, {"endpoints": {"first": endpoint(wanted["first"]), "second": endpoint(wanted["second"])},
             "position_comparison": {"status": "within_tolerance", "distance_mm": 0},
             "direction_relationship": {"relationship": "opposed"}, "pcref_correspondence": {"status": "not_referenced"},
             "tolerance": {"value": wanted["tolerance_mm"], "unit": "mm"}}) for index, wanted in enumerate(request["pairs"])]
    trace = None
    if request["starts"]:
        nodes = [{"index": index, "hops": 0, "requested_refno": wanted["refno"], "requested_type": wanted["expected_type"],
                  "refno": wanted["refno"], "type": wanted["expected_type"], "point_number": wanted["point_number"],
                  "identity_verified": True, "expanded": True, "read_complete": True, "complete": True,
                  "endpoint": endpoint(wanted), "link_status": "open_end", "context_binding_id": "trace:0"}
                 for index, wanted in enumerate(request["starts"])]
        trace = item("trace", 0, {key: request[key] for key in ("starts", "max_hops", "max_nodes")}, {
            "starts": [{"start_index": index, "node_index": index, "requested": wanted} for index, wanted in enumerate(request["starts"])],
            "nodes": nodes, "edges": [], "node_read_count": len(nodes), "returned_count": len(nodes),
            "max_hops": request["max_hops"], "max_nodes": request["max_nodes"], "scope_complete": True,
            "truncated": False, "has_more": False, "unresolved_indices": [], "open_end_indices": list(range(len(nodes))), "read_errors": []})
    return {"ok": True, "version": "E3D21", "session_id": "session", "bridge_build": "build", "result": {
        "operation": "piping_connectivity_snapshot", "success": True, "available": True, "complete": True,
        "read_only": True, "single_dispatch": True, "atomic_snapshot": False, "context": context(),
        "branches": branches, "pairs": pairs, "trace": trace, "read_errors": []}}


class PipingConnectivitySnapshotTests(unittest.TestCase):
    def test_opt_in_read_only_schema_has_all_bounds_without_changing_public_catalog(self):
        server = McpServer("connectivity-snapshot-test", "0")
        register_connectivity_snapshot(server, BridgeClient(Settings(mode="bridge", version="E3D21", allow_commands=False)))
        tool, = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})["result"]["tools"]
        self.assertEqual(tool["name"], "e3d_piping_connectivity_snapshot")
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        properties = tool["inputSchema"]["properties"]
        self.assertEqual((properties["branches"]["maxItems"], properties["pairs"]["maxItems"], properties["starts"]["maxItems"]), (8, 16, 16))
        self.assertEqual((properties["max_nodes"]["maximum"], properties["max_hops"]["maximum"], properties["member_limit"]["maximum"]), (128, 16, 64))
        self.assertFalse(tool["inputSchema"]["additionalProperties"])

    def test_one_native_request_no_cross_route_fallback_and_no_report_evidence_duplication(self):
        bridge = BridgeClient(Settings(mode="bridge", version="E3D21", allow_commands=False))
        native = sample(); original = deepcopy(native)
        with patch.object(bridge, "_request", return_value=native) as request, \
             patch.object(bridge, "connection_pair_check") as pair, \
             patch.object(bridge, "trace_connection_network") as trace, \
             patch.object(bridge, "read_attributes") as attrs, patch.object(bridge, "command") as command:
            reply = piping_connectivity_snapshot(bridge, ARGS)
        request.assert_called_once_with("POST", PATH, connectivity_snapshot_parameters(ARGS), version="E3D21")
        for method in (pair, trace, attrs, command):
            method.assert_not_called()
        self.assertTrue(reply["result"]["complete"], reply)
        self.assertEqual(native, original)
        self.assertTrue(reply["result"]["report"]["context_binding"])
        self.assertNotIn("data", reply["result"]["report"]["items"]["trace"][0])

    def test_proximity_and_opposed_directions_never_become_engineering_connectivity(self):
        report = connectivity_snapshot_report(sample(), connectivity_snapshot_parameters(ARGS))
        self.assertTrue(report["complete"])
        self.assertEqual(report["engineering_connectivity"], "not_assessed")
        self.assertIn("within_tolerance", report["markdown"])
        self.assertIn("not_referenced", report["markdown"])
        self.assertIn("not assessed", report["markdown"])
        self.assertNotIn("engineering_compatible", report)
        self.assertIn("HREF (native)", report["markdown"])

    def test_bad_inputs_fail_before_any_native_call(self):
        invalid = [{}, {"starts": [START], "max_hops": True}, {"starts": [START], "max_hops": 17},
                   {"starts": [START], "max_nodes": 129}, {"starts": [START, START]},
                   {"starts": [START] * 17}, {"starts": [{**START, "refno": "/*"}]},
                   {"starts": [{**START, "refno": "=0/2"}]}, {"starts": [{**START, "point_number": True}]},
                   {"starts": [{"refno": "=1/2", "point_number": 1}]},
                   {"pairs": [{"first": START, "second": SECOND, "tolerance_mm": 10 ** 400}]},
                   {"pairs": [{"first": START, "second": SECOND, "tolerance_mm": float("nan")}]},
                   {"pairs": [{"first": START, "second": START, "tolerance_mm": 0}]},
                   {"branches": [{"refno": "/B", "expected_type": "PIPE"}]},
                   {"branches": [{"refno": "/B", "expected_type": "BRAN"}] * 9},
                   {"starts": [START], "expected_context": {}}, {"starts": [START], "write": True}]
        bridge = BridgeClient(Settings(mode="bridge", version="E3D21"))
        with patch.object(bridge, "_request") as request:
            for args in invalid:
                with self.subTest(args=args), self.assertRaises(ValueError):
                    piping_connectivity_snapshot(bridge, args)
        request.assert_not_called()

    def test_normalization_preserves_same_refno_different_types_and_point_numbers(self):
        starts = [START, {**START, "expected_type": " tubi "}, {**START, "point_number": 2}]
        request = connectivity_snapshot_parameters({"starts": starts, "max_hops": 0,
            "expected_context": {**IDENTITY, "current_element": "=01/090", "current_element_type": "equi"}})
        self.assertEqual(len(request["starts"]), 3)
        self.assertEqual(request["starts"][1]["expected_type"], "TUBI")
        self.assertEqual(request["expected_context"], IDENTITY)

    def test_missing_binding_is_unknown_and_conflicting_binding_is_false(self):
        for missing in (True, False):
            with self.subTest(missing=missing):
                native = sample()
                if missing:
                    del native["result"]["pairs"][0]["context"]["after"]["session_id"]
                else:
                    native["result"]["pairs"][0]["context"]["after"]["session_id"] = "other"
                report = connectivity_snapshot_report(native, connectivity_snapshot_parameters(ARGS))
                self.assertFalse(report["complete"])
                self.assertIs(report["items"]["pairs"][0]["binding"], None if missing else False)
                self.assertTrue(report["items"]["branches"][0]["complete"])

    def test_envelope_mismatch_or_batch_context_change_invalidates_every_item(self):
        for where in ("envelope", "ce_type", "expected"):
            native, args = sample(), deepcopy(ARGS)
            if where == "envelope":
                native["bridge_build"] = "other"
            elif where == "ce_type":
                native["result"]["context"]["after"]["current_element_type"] = "TUBI"
            else:
                args["expected_context"] = {**IDENTITY, "mdb": "OTHER"}
            report = connectivity_snapshot_report(native, connectivity_snapshot_parameters(args))
            self.assertFalse(report["complete"])
            self.assertTrue(all(not item["complete"] for rows in report["items"].values() for item in rows))

    def test_false_complete_claims_do_not_hide_missing_members_or_typed_pair_mismatches(self):
        for scenario in ("member_more", "member_type", "pair_type", "point", "branch_attr", "trace_limit", "trace_count", "seed"):
            with self.subTest(scenario=scenario):
                native = sample(); result = native["result"]
                branch, pair, trace = result["branches"][0]["data"], result["pairs"][0]["data"], result["trace"]["data"]
                if scenario == "member_more": branch["has_more"] = True
                elif scenario == "member_type": branch["members"][1]["type"] = "ELBO"
                elif scenario == "pair_type": pair["endpoints"]["second"]["element"]["type"] = "ELBO"
                elif scenario == "point": pair["endpoints"]["first"]["point_number"] = 2
                elif scenario == "branch_attr": del branch["attributes"]["values"]["HREF"]
                elif scenario == "trace_limit": trace["max_hops"] = 16
                elif scenario == "trace_count": trace["node_read_count"] = 129
                else: trace["starts"][0]["requested"]["expected_type"] = "TUBI"
                self.assertFalse(connectivity_snapshot_report(native, connectivity_snapshot_parameters(ARGS))["complete"])

    def test_report_requires_native_availability_and_consistent_complete_flags(self):
        for mutate in (
            lambda result: result.update(available=False),
            lambda result: result.update(complete=False),
            lambda result: result.update(success=False),
            lambda result: result.update(success=False, complete=True),
        ):
            with self.subTest(mutate=mutate):
                native = sample()
                mutate(native["result"])
                report = connectivity_snapshot_report(native, connectivity_snapshot_parameters(ARGS))
                self.assertFalse(report["complete"])
        native = sample()
        native["result"]["success"] = False
        report = connectivity_snapshot_report(native, connectivity_snapshot_parameters(ARGS))
        self.assertIn("native_success_complete_inconsistent", report["issues"])

    def test_trace_node_context_completeness_is_required_for_report_completeness(self):
        native = sample()
        native["result"]["trace"]["data"]["nodes"][0]["complete"] = False
        report = connectivity_snapshot_report(native, connectivity_snapshot_parameters(ARGS))
        self.assertFalse(report["complete"])
        self.assertFalse(report["items"]["trace"][0]["read_coverage_verified"])

    def test_wrapper_preserves_native_success_and_complete_claims(self):
        native = sample()
        native["result"]["success"] = False
        bridge = BridgeClient(Settings(mode="bridge", version="E3D21", allow_commands=False))
        with patch.object(bridge, "_request", return_value=native):
            reply = piping_connectivity_snapshot(bridge, ARGS)
        result = reply["result"]
        self.assertFalse(result["success"])
        self.assertFalse(result["complete"])
        self.assertFalse(result["native_success"])
        self.assertTrue(result["native_complete"])
        self.assertFalse(result["report"]["complete"])

    def test_zero_hops_and_truncation_stay_partial_with_raw_frontier_visible(self):
        args = {"starts": [START], "max_hops": 0, "max_nodes": 1}; native = sample(args)
        trace = native["result"]["trace"]["data"]
        trace.update(truncated=True, has_more=True, scope_complete=False,
                     edges=[{"from_index": 0, "to_index": None, "status": "hop_limit", "target_refno": "=1/3",
                             "target_type": "TUBI", "target_point_number": 2}])
        report = connectivity_snapshot_report(native, connectivity_snapshot_parameters(args))
        self.assertFalse(report["complete"])
        self.assertIn("max_hops=0", report["markdown"])
        self.assertIn("truncated: True", report["markdown"])
        self.assertIsNone(native["result"]["trace"]["data"]["edges"][0]["to_index"])

    def test_malformed_native_shapes_preserve_unknown_instead_of_crashing(self):
        for mutate in (lambda r: r.update(context={"before": None}), lambda r: r.update(branches=None),
                       lambda r: r["trace"].update(data={"nodes": None}),
                       lambda r: r["branches"][0]["data"]["attributes"].update(values=None)):
            native = sample(); mutate(native["result"])
            report = connectivity_snapshot_report(native, connectivity_snapshot_parameters(ARGS))
            self.assertFalse(report["complete"])

    def test_trace_claims_cannot_omit_native_edges_or_add_orphan_nodes(self):
        request = connectivity_snapshot_parameters({"starts": [START]})
        for scenario in ("missing_edge", "open_end_with_resolved_link", "extra_orphan", "bad_hop", "false_existence", "node_complete_only"):
            with self.subTest(scenario=scenario):
                native = sample({"starts": [START]})
                trace = native["result"]["trace"]["data"]
                node = trace["nodes"][0]
                if scenario in {"missing_edge", "open_end_with_resolved_link"}:
                    node["endpoint"]["point"]["connection_reference"].update(reference={"status": "resolved", "refno": "=1/3", "type": "TUBI"}, point_number=2)
                    if scenario == "missing_edge": node["link_status"] = "resolved"
                elif scenario == "extra_orphan":
                    orphan = deepcopy(node); orphan.update(index=1, refno="=1/4", hops=1)
                    orphan["endpoint"]["element"]["refno"] = "=1/4"
                    trace["nodes"].append(orphan); trace.update(node_read_count=2, returned_count=2, open_end_indices=[0, 1])
                elif scenario == "bad_hop": node["hops"] = 1
                elif scenario == "false_existence": node["endpoint"]["exists"]["value"] = False
                else: node["endpoint"]["complete"] = False
                self.assertFalse(connectivity_snapshot_report(native, request)["complete"])

    def test_mock_and_other_versions_never_contact_a_route(self):
        for settings in (Settings(mode="mock", version="E3D21"), Settings(mode="bridge", version="E3D31")):
            bridge = BridgeClient(settings)
            with patch.object(bridge, "_request") as request:
                reply = piping_connectivity_snapshot(bridge, ARGS)
            request.assert_not_called()
            self.assertFalse(reply["result"]["complete"])

    def test_fault_markers_stop_raw_replies_before_report_or_followup(self):
        bridge = BridgeClient(Settings(mode="bridge", version="E3D21", allow_commands=False))
        markers = [{"has_fault": True}, {"native_fault": True}, {"native_fault_latched": True},
                   {"error_code": "native_fault"}, {"error_code": "native_database_fault"}]
        for marker in markers:
            for ok in (False, True):
                with self.subTest(marker=marker, ok=ok):
                    native = sample()
                    native["ok"] = ok
                    native["result"]["pairs"][0]["data"]["diagnostics"] = [{"nested": [deepcopy(marker)]}]
                    original = deepcopy(native)
                    with patch.object(bridge, "_request", return_value=native) as request, \
                         patch("aveva_e3d_mcp.piping_connectivity_snapshot.connectivity_snapshot_report") as report, \
                         patch.object(bridge, "health") as health, patch.object(bridge, "project") as project, \
                         patch.object(bridge, "command") as command, \
                         patch.object(bridge, "trace_connection_network") as trace, \
                         patch.object(bridge, "connection_pair_check") as pair:
                        with self.assertRaises(BridgeError) as caught:
                            piping_connectivity_snapshot(bridge, ARGS)
                    self.assertIs(caught.exception.diagnostics, native)
                    self.assertEqual(native, original)
                    request.assert_called_once_with("POST", PATH, connectivity_snapshot_parameters(ARGS), version="E3D21")
                    for followup in (report, health, project, command, trace, pair):
                        followup.assert_not_called()

    def test_bridge_error_and_its_fault_diagnostics_propagate_at_the_original_read(self):
        bridge = BridgeClient(Settings(mode="bridge", version="E3D21", allow_commands=False))
        for status in (404, 500):
            diagnostics = {"state": {"native_database_fault": {"has_fault": True}},
                           "result": {"read_errors": [{"details": [{"native_fault_latched": True}]}]}, "retry_safe": False}
            failure = BridgeError("native fault", http_status=status, diagnostics=diagnostics)
            with self.subTest(status=status), patch.object(bridge, "_request", side_effect=failure) as request, \
                 patch("aveva_e3d_mcp.piping_connectivity_snapshot.connectivity_snapshot_report") as report, \
                 patch.object(bridge, "health") as health:
                with self.assertRaises(BridgeError) as caught:
                    piping_connectivity_snapshot(bridge, ARGS)
            self.assertIs(caught.exception, failure)
            self.assertIs(caught.exception.diagnostics, diagnostics)
            self.assertEqual(caught.exception.http_status, status)
            request.assert_called_once()
            report.assert_not_called()
            health.assert_not_called()

    def test_pure_report_also_stops_native_fault_and_false_markers_are_not_faults(self):
        native = sample()
        native["result"]["trace"]["data"]["nodes"][0]["endpoint"]["diagnostics"] = [{"error_code": "native_database_fault"}]
        with self.assertRaises(BridgeError) as caught:
            connectivity_snapshot_report(native, connectivity_snapshot_parameters(ARGS))
        self.assertIs(caught.exception.diagnostics, native)
        for marker in ({"has_fault": False}, {"has_fault": 1}, {"native_fault": "true"},
                       {"native_fault_latched": None}, {"error_code": ["native_fault"]}):
            with self.subTest(marker=marker):
                native = sample(); native["diagnostics"] = [{"nested": marker}]
                self.assertTrue(connectivity_snapshot_report(native, connectivity_snapshot_parameters(ARGS))["complete"])

    def test_member_probe_receipt_includes_terminator_and_extra_lookahead(self):
        for limit, lookahead in ((64, 0), (2, 1)):
            args = {"branches": ARGS["branches"], "member_limit": limit}
            native = sample(args)
            report = connectivity_snapshot_report(native, connectivity_snapshot_parameters(args))
            self.assertTrue(report["complete"], report)
            self.assertIn(f"3/{limit + 1}", report["markdown"])
            self.assertEqual(native["result"]["branches"][0]["data"]["lookahead_probe_count"], lookahead)
        for count in (2, True, 66):
            native = sample(); native["result"]["branches"][0]["data"]["member_probe_count"] = count
            self.assertFalse(connectivity_snapshot_report(native, connectivity_snapshot_parameters(ARGS))["complete"])
        native = sample(); native["result"]["branches"][0]["data"]["lookahead_probe_count"] = 1
        self.assertFalse(connectivity_snapshot_report(native, connectivity_snapshot_parameters(ARGS))["complete"])


if __name__ == "__main__":
    unittest.main()

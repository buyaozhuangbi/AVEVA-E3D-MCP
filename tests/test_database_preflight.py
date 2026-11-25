"""Read-only database candidates, native evidence and additive discovery."""
from contextlib import ExitStack
from copy import deepcopy
import base64
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.database_creation import TOOL, build_database_candidate, database_preflight
from aveva_e3d_mcp.tools import create_server
from catalog_compatibility import assert_legacy_tool_definition

ARGS = {"team": "PIPING", "name": "NEWDB", "database_type": "DESI", "access": "UPDATE"}
CATA_ARGS = {"team": "CATALOGUE", "name": "FITTINGS", "database_type": "CATA", "access": "UPDATE"}
CONTEXT = hashlib.sha256(b"SESSION\nPROJECT\nALL").hexdigest()


def health():
    return {"ok": True, "version": "E3D21", "connected": True, "session_id": "SESSION", "bridge_build": "BUILD",
            "state": {"database_ready": True, "busy": False, "recovery_required": False}}


def project():
    return {"ok": True, "version": "E3D21", "project": {"name": "PROJECT", "mdb": "ALL",
            "current_element": "=1/2", "connected": True, "recovery_only": False}}


def rows(count=1):
    return [{"index": i, "available": True, "name": "OTHER/DB" + str(i), "number": 1000 + i, "extract_number": 1}
            for i in range(count)]


def page(values=None, offset=0, total=None):
    values = rows() if values is None else values
    total = len(values) if total is None else total
    end = offset + len(values)
    return {"ok": True, "version": "E3D21", "result": {
        "success": True, "available": True, "page_complete": True, "complete": offset == 0 and end == total,
        "session_id": "SESSION", "project": "PROJECT", "mdb": "ALL", "context_id": CONTEXT,
        "ce_before": "=1/2", "ce_after": "=1/2", "ce_unchanged": True, "identity_unchanged": True,
        "offset": offset, "limit": 200, "total_count": total, "returned_count": len(values),
        "has_more": end < total, "next_offset": end if end < total else None,
        "databases": values, "read_errors": [],
    }}


def snapshot_from_rows(values, *, context=CONTEXT, total=None, **changes):
    total = len(values) if total is None else total
    valid_rows = all(type(row) is dict and type(row.get("index")) is int and row["index"] == index
                     and type(row.get("name")) is str and type(row.get("number")) is int
                     and type(row.get("extract_number")) is int for index, row in enumerate(values))
    canonical = "".join(f"{row['index']}|{base64.b64encode(row['name'].encode('utf-8')).decode('ascii')}|"
                        f"{row['number']}|{row['extract_number']}\n" for row in values) if valid_rows else None
    result = {"success": True, "available": True, "complete": True,
        "source": "E3D21 SDK MDB.GetDBArray identity scan",
        "consistency": "single_ui_dispatch_not_a_database_lock", "atomic_snapshot": False,
        "project": "PROJECT", "mdb": "ALL", "session_id": "SESSION", "context_id": context,
        "expected_context": context, "total_count": total, "returned_count": len(values),
        "identity_snapshot_sha256": hashlib.sha256(canonical.encode("ascii")).hexdigest() if canonical is not None else None,
        "databases": deepcopy(values), "ce_before": "=1/2", "ce_after": "=1/2",
        "ce_unchanged": True, "identity_unchanged": True, "read_errors": [],
    }
    result.update(changes)
    return {"ok": True, "version": "E3D21", "result": result}


class DatabasePreflightTests(unittest.TestCase):
    def run_preflight(self, arguments=None, pages=None, statuses=None, projects=None):
        bridge = BridgeClient(Settings(mode="bridge", version="E3D21", allow_commands=False))
        pages = [page()] if pages is None else pages
        statuses = [health(), health()] if statuses is None else statuses
        projects = [project(), project()] if projects is None else projects
        with ExitStack() as stack:
            status_read = stack.enter_context(patch.object(bridge, "health", side_effect=statuses))
            project_read = stack.enter_context(patch.object(bridge, "project", side_effect=projects))
            databases = stack.enter_context(patch("aveva_e3d_mcp.database_tools.database_state", side_effect=pages))
            def identity_snapshot(client, expected_context):
                values = []
                native_errors = []
                first_result = None
                expected_offset = 0
                seen = set()
                total = None
                covered = False
                for page_index in range(len(pages)):
                    current = databases(client, {"offset": expected_offset, "limit": 200,
                                                 "expected_context": expected_context if expected_offset else ""})
                    if isinstance(current, BridgeError):
                        raise current
                    current_result = current.get("result", {})
                    if first_result is None:
                        first_result = current_result
                    rows = current_result.get("databases", [])
                    if not isinstance(rows, list):
                        rows = []
                        native_errors.append({"path": "databases", "code": "invalid_rows", "message": "invalid rows"})
                    values.extend(deepcopy(rows))
                    native_errors.extend(current_result.get("read_errors") or [])
                    page_total = current_result.get("total_count")
                    if page_index == 0:
                        total = page_total
                    if not type(page_total) is int or page_total != total:
                        native_errors.append({"path": "total_count", "code": "count_drift", "message": "database count changed"})
                    if (current.get("ok") is not True or current_result.get("success") is not True
                            or current_result.get("available") is not True or current_result.get("page_complete") is not True
                            or current_result.get("offset") != expected_offset
                            or current_result.get("returned_count") != len(rows)
                            or current_result.get("limit") != 200):
                        native_errors.append({"path": "page", "code": "incomplete", "message": "page incomplete"})
                    for row in rows:
                        if not (type(row) is dict and type(row.get("number")) is int
                                and type(row.get("extract_number")) is int):
                            native_errors.append({"path": "database", "code": "identity_incomplete", "message": "identity incomplete"})
                            continue
                        identity = (row["number"], row["extract_number"])
                        if identity in seen:
                            native_errors.append({"path": "database", "code": "duplicate_identity", "message": "duplicate identity"})
                        seen.add(identity)
                    expected_offset += len(rows)
                    more = current_result.get("has_more")
                    if current_result.get("complete") is not (current_result.get("offset") == 0 and more is False):
                        native_errors.append({"path": "complete", "code": "invalid_completion", "message": "completion invalid"})
                    if more is True:
                        if current_result.get("next_offset") != expected_offset:
                            native_errors.append({"path": "next_offset", "code": "invalid_continuation", "message": "continuation invalid"})
                    elif more is False and current_result.get("next_offset") is None and expected_offset == total:
                        covered = True
                    else:
                        native_errors.append({"path": "has_more", "code": "invalid_continuation", "message": "continuation invalid"})
                    for field in ("session_id", "project", "mdb", "context_id", "ce_before", "ce_after",
                                  "ce_unchanged", "identity_unchanged"):
                        if current_result.get(field) != first_result.get(field):
                            native_errors.append({"path": field, "code": "snapshot_mismatch", "message": "fixture page identity changed"})
                first_result = first_result or {}
                valid = not native_errors and type(total) is int and total == len(values) and covered
                # Feed the same raw identities to the snapshot contract. Native
                # production reads this list from one GetDBArray call.
                snap = snapshot_from_rows(values, context=first_result.get("context_id", expected_context),
                                          total=total, success=valid, complete=valid, read_errors=native_errors)
                snap_result = snap["result"]
                for field in ("project", "mdb", "session_id", "ce_before", "ce_after", "ce_unchanged", "identity_unchanged"):
                    if field in first_result:
                        snap_result[field] = first_result[field]
                if first_result.get("context_id") != expected_context:
                    snap_result["context_id"] = first_result.get("context_id")
                    snap_result["read_errors"].append({"path": "context_id", "code": "context_mismatch", "message": "fixture context mismatch"})
                    snap_result["success"] = snap_result["complete"] = False
                return snap
            stack.enter_context(patch("aveva_e3d_mcp.database_tools.database_identity_snapshot", side_effect=identity_snapshot))
            forbidden = [stack.enter_context(patch.object(bridge, name, side_effect=AssertionError("Write call: " + name)))
                         for name in ("command", "command_batch", "execute_pml_script", "create_element", "set_attribute",
                                      "set_attributes", "set_transform", "capture_view", "cancel_command", "reenter_design")]
            output = database_preflight(bridge, ARGS if arguments is None else arguments)
        for call in forbidden:
            call.assert_not_called()
        json.dumps(output, allow_nan=False)
        result = output["result"]
        self.assertIs(result["execution_supported"], False)
        self.assertIs(result["creation_attempted"], False)
        self.assertIs(result["created"], False)
        self.assertEqual(result["scope"], "current_mdb_only")
        self.assertEqual(result["project_uniqueness"], {"name": None, "database_number": None})
        self.assertIsNone(result["admin_context_verified"])
        self.assertIsNone(result["team_exists"])
        self.assertIsNone(result["area_available"])
        return result, status_read, project_read, databases

    def test_candidate_matches_installed_form_order_without_native_validation(self):
        candidate = build_database_candidate({**ARGS, "team": "piping", "name": "newdb", "area": 7,
                                              "access": "MULTIWRITE", "database_number": 1234})
        self.assertEqual(candidate["candidate_command"],
            "CREATE DB PIPING/NEWDB DESI IN AREA 7 ACCESS MULTIWRITE CLAIM IMPLICIT DBNO 1234")
        self.assertFalse(candidate["native_syntax_validated"])
        self.assertEqual(candidate["claim_policy"], "explicit_tool_policy_implicit")
        self.assertEqual(build_database_candidate({**ARGS, "area": 0})["candidate_command"], "CREATE DB PIPING/NEWDB DESI ACCESS UPDATE")
        self.assertTrue(build_database_candidate({**ARGS, "database_number": 7500})["warnings"])

    def test_cata_update_candidate_names_a_database_without_a_root(self):
        candidate = build_database_candidate({**CATA_ARGS, "team": "catalogue", "name": "fittings"})
        self.assertEqual(candidate["normalized_input"], CATA_ARGS)
        self.assertEqual(candidate["full_database_name"], "CATALOGUE/FITTINGS")
        self.assertEqual(candidate["candidate_command"], "CREATE DB CATALOGUE/FITTINGS CATA ACCESS UPDATE")
        self.assertEqual(candidate["claim_policy"], "not_applicable")
        self.assertIs(candidate["native_syntax_validated"], False)

    def test_cata_multiwrite_candidate_uses_implicit_claim(self):
        candidate = build_database_candidate({**CATA_ARGS, "area": 7, "access": "MULTIWRITE", "database_number": 1234})
        self.assertEqual(candidate["candidate_command"],
                         "CREATE DB CATALOGUE/FITTINGS CATA IN AREA 7 ACCESS MULTIWRITE CLAIM IMPLICIT DBNO 1234")
        self.assertEqual(candidate["claim_policy"], "explicit_tool_policy_implicit")
        self.assertIs(candidate["native_syntax_validated"], False)

    def test_cata_candidate_can_include_an_explicit_admin_root_name(self):
        candidate = build_database_candidate({**CATA_ARGS, "root_name": "paragon_root"})
        self.assertEqual(candidate["normalized_input"], {**CATA_ARGS, "root_name": "PARAGON_ROOT"})
        self.assertEqual(candidate["candidate_command"],
                         "CREATE DB CATALOGUE/FITTINGS CATA ACCESS UPDATE WITH CATA /PARAGON_ROOT")
        self.assertIs(candidate["native_syntax_validated"], False)

    def test_cata_preflight_adds_manual_admin_handoff_and_post_create_checks(self):
        result, *_ = self.run_preflight({**CATA_ARGS, "area": 7, "access": "MULTIWRITE", "database_number": 1234})
        self.assertEqual(result["candidate_command"],
                         "CREATE DB CATALOGUE/FITTINGS CATA IN AREA 7 ACCESS MULTIWRITE CLAIM IMPLICIT DBNO 1234")
        handoff = result["admin_handoff"]
        self.assertEqual(handoff["status"], "manual_admin_configuration_required")
        self.assertEqual(handoff["database"], {"team": "CATALOGUE", "name": "FITTINGS", "type": "CATA"})
        self.assertEqual(handoff["initial_element"], {
            "type": "CATA", "name": None,
            "name_input": "Choose a literal Catalogue root name in the ADMIN form",
        })
        self.assertEqual(handoff["claim_mode"], "IMPLICIT")
        self.assertEqual(handoff["suggested_command_template"],
                         result["candidate_command"] + " WITH CATA /<CATALOGUE_ROOT_NAME>")
        self.assertIs(handoff["command_template_is_executable"], False)
        self.assertIsNone(handoff["admin_context_verified"])
        self.assertIsNone(handoff["permission_verified"])
        self.assertIs(handoff["execution_performed"], False)
        self.assertEqual(handoff["database_type_code"], 2)
        self.assertEqual(handoff["root_creation"], {
            "mode": "admin_initial_element", "element_type": "CATA", "storage_type": "Catalogue",
            "created_by_admin": True, "root_name_required_before_apply": True,
        })
        self.assertEqual(result["post_create_verification"]["status"], "instructions_only")
        self.assertEqual([step["tool"] for step in result["post_create_verification"]["steps"]], [
            "e3d_database_state", "e3d_team_database_identity_manifest", "e3d_paragon_catalogue_manifest",
            "e3d_paragon_catalogue_schema",
        ])
        self.assertEqual(result["post_create_verification"]["expected_database_type_code"], 2)
        self.assertEqual(result["catalogue_preflight"], {
            "database_type": "CATA", "database_type_label": "Catalogue", "database_type_code": 2,
            "database_name": "CATALOGUE/FITTINGS", "database_name_status": "no_observed_current_mdb_collision",
            "project_catalog_status": "not_requested", "project_catalog_name_match": None,
            "world_catalog_name_match": None, "root_name": None, "root_name_source": "admin_form",
            "root_collision_status": "not_checked_until_catalogue_root_is_visible",
            "admin_execution_status": "manual_admin_configuration_required",
            "scope": {"current_mdb_name_only": True, "catalogue_root_not_created": True,
                      "project_wide_uniqueness_verified": False, "admin_permission_verified": False},
        })
        self.assertFalse(result["post_create_verification"]["project_wide_uniqueness_verified"])
        self.assertFalse(result["post_create_verification"]["database_created_by_this_request_verified"])

        default_result, *_ = self.run_preflight(CATA_ARGS)
        default_handoff = default_result["admin_handoff"]
        self.assertIsNone(default_handoff["claim_mode"])
        self.assertEqual(default_handoff["area"], 0)
        self.assertIsNone(default_handoff["database_number"])
        self.assertEqual(default_handoff["suggested_command_template"],
                         default_result["candidate_command"] + " WITH CATA /<CATALOGUE_ROOT_NAME>")
        self.assertEqual(default_result["post_create_verification"]["expected_root_name"], "<name entered in ADMIN>")

        named_result, *_ = self.run_preflight({**CATA_ARGS, "root_name": "PARAGON_ROOT"})
        self.assertEqual(named_result["admin_handoff"]["initial_element"], {
            "type": "CATA", "name": "/PARAGON_ROOT", "name_input": "Use this proposed root name in ADMIN",
        })
        self.assertEqual(named_result["admin_handoff"]["suggested_command_template"], named_result["candidate_command"])
        self.assertEqual(named_result["post_create_verification"]["expected_root_name"], "/PARAGON_ROOT")
        self.assertEqual(named_result["post_create_verification"]["steps"][2]["arguments"]["refno"], "/PARAGON_ROOT")

    def test_desi_preflight_does_not_emit_paragon_admin_handoff(self):
        result, *_ = self.run_preflight(ARGS)
        self.assertIsNone(result["admin_handoff"])
        self.assertIsNone(result["post_create_verification"])

    def test_invalid_input_and_command_injection_never_read_bridge(self):
        invalid = [{}, {**ARGS, "access": "CONTROLLED"}, {**ARGS, "database_type": "TRAN"}, {**ARGS, "team": "TRANSACTION"}]
        for key in ("team", "name"):
            invalid += [{**ARGS, key: value} for value in (None, True, 1, "", "UNSET", "a" * 33,
                "PIPING/X", "/*TEAM", "A;DELETE", "A\nCMD", "A B", "A'", "A|", "$!A", "数据库")]
        for key, values in (("database_number", [None, True, 1.0, "1", 0, -1, 255001]),
                            ("area", [None, False, 1.0, "1", -1, 1000])):
            invalid += [{**ARGS, key: value} for value in values]
        invalid += [{**ARGS, key: value} for key, value in (("command", "CREATE DB"), ("dry_run", False),
            ("expected_revision", "a" * 64), ("capture_after", False), ("claim", "EXPLICIT"))]
        invalid += [{**CATA_ARGS, "database_type": value} for value in
                    (None, ["CATA"], "cata", "PROP", "PARAGON", "CATA WITH CATA /FITTINGS")]
        invalid += [{**CATA_ARGS, key: value} for key, value in (
            ("team", "TRANSACTION"), ("name", "FITTINGS WITH CATA /FITTINGS"), ("access", "CONTROLLED"),
            ("database_number", True), ("area", 1000), ("root_name", "/FITTINGS"), ("element_type", "CATA"),
            ("with", "CATA /FITTINGS"), ("dry_run", False))]
        invalid += [{**CATA_ARGS, "root_name": value} for value in
                    (None, True, "", "1ROOT", "ROOT/CHILD", "ROOT WITH CATA /OTHER", "UNSET", "A" * 33, "数据库")]
        invalid += [{**ARGS, "root_name": "ROOT"}]
        bridge = BridgeClient(Settings(mode="bridge", version="E3D21"))
        with patch.object(bridge, "_request", side_effect=AssertionError("Invalid preflight reached the bridge")) as request:
            for arguments in invalid:
                with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                    database_preflight(bridge, arguments)
            request.assert_not_called()

    def test_complete_mdb_no_conflict_does_not_claim_project_uniqueness(self):
        result, statuses, projects, databases = self.run_preflight({**ARGS, "database_number": 1234})
        self.assertTrue(result["success"])
        self.assertTrue(result["complete"])
        self.assertTrue(result["page_complete"])
        self.assertTrue(result["context"]["unchanged"])
        self.assertEqual(result["context"]["context_id"], CONTEXT)
        self.assertIs(result["conflicts"]["name_in_current_mdb"], False)
        self.assertIs(result["conflicts"]["database_number_in_current_mdb"], False)
        self.assertFalse(result["mcp_commands_enabled"])
        self.assertEqual((statuses.call_count, projects.call_count, databases.call_count), (2, 2, 1))

    def test_identity_fingerprint_mismatch_never_produces_negative_conflict_result(self):
        bridge = BridgeClient(Settings(mode="bridge", version="E3D21", allow_commands=False))
        corrupted = snapshot_from_rows(rows())
        corrupted["result"]["identity_snapshot_sha256"] = "0" * 64
        with ExitStack() as stack:
            stack.enter_context(patch.object(bridge, "health", side_effect=[health(), health()]))
            stack.enter_context(patch.object(bridge, "project", side_effect=[project(), project()]))
            stack.enter_context(patch("aveva_e3d_mcp.database_tools.database_identity_snapshot", return_value=corrupted))
            result = database_preflight(bridge, ARGS)["result"]
        self.assertFalse(result["complete"])
        self.assertFalse(result["snapshot_complete"])
        self.assertIsNone(result["conflicts"]["name_in_current_mdb"])
        self.assertTrue(any("fingerprint" in item["message"].lower() or "incomplete" in item["message"].lower()
                            for item in result["read_errors"]))

    def test_name_and_dbno_conflicts_are_independent_observations(self):
        values = rows(2)
        values[0]["name"] = "piping/newdb"
        values[1]["number"] = 1234
        result, *_ = self.run_preflight({**ARGS, "database_number": 1234}, pages=[page(values)])
        self.assertTrue(result["success"], result)
        self.assertIs(result["conflicts"]["name_in_current_mdb"], True)
        self.assertIs(result["conflicts"]["database_number_in_current_mdb"], True)
        self.assertEqual(result["conflicts"]["name_matches"][0]["index"], 0)
        self.assertEqual(result["conflicts"]["database_number_matches"][0]["index"], 1)

    def test_automatic_dbno_stays_unknown_and_empty_mdb_can_be_observed(self):
        result, *_ = self.run_preflight(pages=[page([])])
        self.assertTrue(result["complete"])
        self.assertEqual(result["returned_count"], 0)
        self.assertIsNone(result["conflicts"]["database_number_in_current_mdb"])

    def test_pagination_binds_context_and_aggregates_without_claiming_native_snapshot(self):
        values = rows(201)
        values[-1]["name"] = "PIPING/NEWDB"
        result, _, _, databases = self.run_preflight(pages=[page(values[:200], total=201), page(values[200:], offset=200, total=201)])
        self.assertTrue(result["complete"], result)
        self.assertEqual(result["returned_count"], 201)
        self.assertEqual(result["pages"], [])
        self.assertTrue(result["snapshot_complete"])
        self.assertEqual(result["identity_snapshot_sha256"], result["snapshot"]["identity_snapshot_sha256"])
        self.assertEqual(databases.call_args_list[1].args[1], {"offset": 200, "limit": 200, "expected_context": CONTEXT})
        self.assertFalse(result["atomic_snapshot"])
        self.assertEqual(result["consistency"],
                         "sequential_context_observations_single_dispatch_identity_scan_not_a_database_lock")
        self.assertEqual(result["snapshot"]["consistency"], "single_ui_dispatch_not_a_database_lock")
        self.assertTrue(result["conflicts"]["name_in_current_mdb"])

    def test_partial_page_keeps_positive_evidence_and_negative_results_unknown(self):
        bad = page()
        bad["result"].update(success=False, page_complete=False, complete=False,
            read_errors=[{"path": "databases[0]", "code": "read_failed", "message": "native error"}])
        bad["result"]["databases"][0]["name"] = "PIPING/NEWDB"
        result, *_ = self.run_preflight({**ARGS, "database_number": 1234}, pages=[bad])
        self.assertFalse(result["complete"])
        self.assertTrue(result["conflicts"]["name_in_current_mdb"])
        self.assertIsNone(result["conflicts"]["database_number_in_current_mdb"])
        self.assertTrue(any("native error" in item["message"] for item in result["read_errors"]))

    def test_malformed_counts_rows_and_completion_never_become_complete(self):
        mutations = [lambda p: p.update(total_count=True), lambda p: p.update(total_count=10001),
            lambda p: p.update(returned_count=0), lambda p: p.update(offset=1), lambda p: p.update(limit=1),
            lambda p: p.update(next_offset=1), lambda p: p.update(complete=False), lambda p: p.update(has_more=None),
            lambda p: p["databases"][0].update(number=True), lambda p: p["databases"][0].update(name=None),
            lambda p: p["databases"][0].update(index=True)]
        for index, mutate in enumerate(mutations):
            bad = page(); mutate(bad["result"])
            with self.subTest(mutation=index):
                result, *_ = self.run_preflight(pages=[bad])
                self.assertFalse(result["complete"])
                self.assertIsNone(result["conflicts"]["name_in_current_mdb"])

    def test_changed_page_context_or_boundary_context_invalidates_current_claims(self):
        for field, value in (("session_id", "OTHER"), ("project", "OTHER"), ("mdb", "OTHER"),
                             ("context_id", "a" * 64), ("ce_unchanged", False), ("identity_unchanged", False)):
            bad = page(); bad["result"][field] = value
            with self.subTest(field=field):
                result, *_ = self.run_preflight(pages=[bad])
                self.assertFalse(result["complete"])
                self.assertIsNot(result["context"]["unchanged"], True)
        after = health(); after["session_id"] = "OTHER"
        result, *_ = self.run_preflight(statuses=[health(), after])
        self.assertFalse(result["complete"])
        self.assertIsNone(result["conflicts"]["name_in_current_mdb"])
        after_project = project(); after_project["project"]["current_element"] = "=1/3"
        result, *_ = self.run_preflight(projects=[project(), after_project])
        self.assertFalse(result["complete"])

    def test_not_ready_busy_faulted_and_unknown_native_state_stop_before_project_reads(self):
        for field, value in (("database_ready", False), ("busy", True), ("recovery_required", True), ("busy", None)):
            before = health(); before["state"][field] = value
            with self.subTest(field=field):
                result, _, projects, databases = self.run_preflight(statuses=[before])
                self.assertFalse(result["success"])
                projects.assert_not_called(); databases.assert_not_called()

    def test_transport_failure_does_not_retry_and_keeps_candidate(self):
        result, _, _, databases = self.run_preflight(pages=[BridgeError("read timeout", transport_error=True)])
        self.assertFalse(result["complete"])
        self.assertEqual(databases.call_count, 1)
        self.assertIn("CREATE DB PIPING/NEWDB", result["candidate_command"])
        self.assertTrue(any("read timeout" in item["message"] for item in result["read_errors"]))

    def test_live_count_drift_duplicate_identity_and_invalid_continuation_fail_closed(self):
        values = rows(201)
        for change in ("count", "duplicate", "offset", "no-progress"):
            first = page(values[:200], total=201)
            second = page(values[200:], offset=200, total=201)
            if change == "count": second["result"]["total_count"] = 202
            if change == "duplicate": second["result"]["databases"][0]["number"] = values[0]["number"]
            if change == "offset": second["result"]["offset"] = 0
            if change == "no-progress": first["result"]["next_offset"] = 0
            with self.subTest(change=change):
                result, *_ = self.run_preflight(pages=[first, second])
                self.assertFalse(result["complete"])
                self.assertIsNone(result["conflicts"]["name_in_current_mdb"])

    def test_page_budget_is_finite_even_for_a_slowly_advancing_native_sequence(self):
        pages = []
        for index in range(50):
            row = rows(index + 1)[-1]
            pages.append(page([row], offset=index, total=10000))
        result, _, _, databases = self.run_preflight(pages=pages)
        self.assertEqual(databases.call_count, 50)
        self.assertFalse(result["complete"])
        self.assertTrue(any("snapshot" in item["message"].lower() or "complete" in item["message"].lower()
                            for item in result["read_errors"]))

    def test_conflict_evidence_has_bounded_details_and_retains_total_match_count(self):
        values = rows(70)
        for row in values: row["name"] = "PIPING/NEWDB"
        result, *_ = self.run_preflight(pages=[page(values)])
        self.assertTrue(result["complete"])
        self.assertTrue(result["conflicts"]["name_in_current_mdb"])
        self.assertEqual(result["conflicts"]["name_match_count"], 70)
        self.assertEqual(len(result["conflicts"]["name_matches"]), 64)
        self.assertTrue(result["conflicts"]["matches_truncated"])

    def test_unready_after_read_does_not_issue_another_sdk_project_read(self):
        after = health(); after["state"]["recovery_required"] = True
        result, _, projects, _ = self.run_preflight(statuses=[health(), after])
        self.assertEqual(projects.call_count, 1)
        self.assertFalse(result["complete"])
        self.assertIs(result["native_readiness"]["ready"], False)
        self.assertIsNone(result["conflicts"]["name_in_current_mdb"])

    def test_mock_and_other_versions_are_explicitly_unavailable_without_calls(self):
        for settings in (Settings(mode="mock", version="E3D21"), Settings(mode="bridge", version="E3D31")):
            bridge = BridgeClient(settings)
            with self.subTest(settings=settings), patch.object(bridge, "health") as status, patch.object(bridge, "_request") as request:
                result = database_preflight(bridge, ARGS)["result"]
                self.assertFalse(result["available"])
                self.assertFalse(result["success"])
                self.assertTrue(result["read_errors"])
                status.assert_not_called(); request.assert_not_called()

    def test_input_and_native_observations_are_not_mutated(self):
        arguments = deepcopy(ARGS); p = page(); h = health(); proj = project()
        original = deepcopy((arguments, p, h, proj))
        self.run_preflight(arguments, pages=[p], statuses=[h, h], projects=[proj, proj])
        self.assertEqual((arguments, p, h, proj), original)


class DatabasePreflightToolTests(unittest.TestCase):
    def test_discovery_preserves_previous_98_definitions(self):
        server = create_server(Settings(mode="mock", version="E3D21"))
        current = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})["result"]["tools"]
        baseline = json.loads((ROOT / "tests/fixtures/database_preflight_catalog98.json").read_text(encoding="utf-8"))
        self.assertEqual(len(current), 126)
        current_by_name = {row["name"]: row for row in current}
        self.assertEqual(set(current_by_name) - {row["name"] for row in baseline}, {
            TOOL, "e3d_database_identity_snapshot", "e3d_piping_branch_state", "e3d_equipment_manifest",
            "e3d_pipe_member_manifest", "e3d_equipment_model_plan", "e3d_piping_route_plan", "e3d_paragon_catalogue_manifest", "e3d_module_context", "e3d_paragon_catalogue_schema", "e3d_piping_connectivity_snapshot", "e3d_equipment_child_schema", "e3d_piping_specification_read", "e3d_team_database_identity_manifest", "e3d_paragon_generation_plan", "e3d_paragon_component_template", "e3d_paragon_reference_trace", "e3d_paragon_component_copy", "e3d_paragon_component_copy_status", "e3d_paragon_component_parameters_set", "e3d_paragon_component_parameters_status", "e3d_paragon_generate_from_standard", "e3d_paragon_component_create", "e3d_paragon_component_create_status",
            "e3d_piping_route_create", "e3d_piping_route_verify", "e3d_modeling_team_plan", "e3d_modeling_team_execute",
        })
        for historical in baseline:
            assert_legacy_tool_definition(self, current_by_name[historical["name"]], historical)
        tool = next(row for row in current if row["name"] == TOOL)
        self.assertEqual(tool["annotations"], {"readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False})
        self.assertEqual(set(tool["inputSchema"]["properties"]),
                         {"team", "name", "database_type", "access", "database_number", "area", "root_name", "include_project_catalog"})
        self.assertEqual(tool["inputSchema"]["properties"]["database_type"]["enum"], ["DESI", "CATA"])
        self.assertEqual(tool["inputSchema"]["required"], ["team", "name", "database_type", "access"])
        self.assertEqual(tool["inputSchema"]["properties"]["include_project_catalog"]["default"], False)
        self.assertFalse(tool["inputSchema"]["additionalProperties"])
        snapshot_tool = next(row for row in current if row["name"] == "e3d_database_identity_snapshot")
        self.assertTrue(snapshot_tool["annotations"]["readOnlyHint"])
        self.assertEqual(snapshot_tool["inputSchema"]["required"], ["expected_context"])
        self.assertFalse(snapshot_tool["inputSchema"]["additionalProperties"])

    def test_registered_desi_and_cata_read_one_design_mdb_snapshot_without_verifying_admin(self):
        design = project()
        design["project"].update(module="DESIGN", user="SYSTEM")
        paths = ("/health", "/api/v1/project", "/api/v1/database/identity-snapshot", "/health", "/api/v1/project")

        def read(method, path, *args, **kwargs):
            self.assertEqual(method, "GET")
            self.assertEqual(args, ())
            if path == "/health": return health()
            if path == "/api/v1/project": return design
            self.assertEqual(path, "/api/v1/database/identity-snapshot")
            self.assertEqual(kwargs["query"], {"expected_context": CONTEXT})
            return snapshot_from_rows(rows())

        for arguments, commands_enabled in ((ARGS, False), (CATA_ARGS, True)):
            with self.subTest(database_type=arguments["database_type"], commands_enabled=commands_enabled):
                server = create_server(Settings(mode="bridge", version="E3D21", allow_commands=commands_enabled))
                with patch.object(BridgeClient, "_request", side_effect=read) as request:
                    reply = server._dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                              "params": {"name": TOOL, "arguments": arguments}})["result"]
                self.assertFalse(reply.get("isError", False), reply)
                self.assertEqual([call.args[:2] for call in request.call_args_list], [("GET", path) for path in paths])
                result = reply["structuredContent"]["result"]
                self.assertIs(result["complete"], True)
                self.assertEqual(result["scope"], "current_mdb_only")
                self.assertIs(result["conflicts"]["name_in_current_mdb"], False)
                self.assertEqual(result["project_uniqueness"], {"name": None, "database_number": None})
                self.assertIsNone(result["admin_context_verified"])
                self.assertIn("admin_context_unverified", result["blockers"])
                self.assertIs(result["mcp_commands_enabled"], commands_enabled)
                for flag in ("native_syntax_validated", "execution_supported", "creation_attempted", "created"):
                    self.assertIs(result[flag], False)



if __name__ == "__main__":
    unittest.main()

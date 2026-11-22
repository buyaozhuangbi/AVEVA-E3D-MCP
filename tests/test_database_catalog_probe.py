"""Exercise the opt-in project catalog through the public preflight tool."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from test_database_preflight import ARGS, CONTEXT, health, project, rows, snapshot_from_rows
from aveva_e3d_mcp.bridge import BridgeClient, BridgeError
from aveva_e3d_mcp.config import Settings
from aveva_e3d_mcp.database_catalog_probe import PATH
from aveva_e3d_mcp.tools import create_server


NUMBER = 250500
CANDIDATE = {"team": "PIPING", "name": "NEWDB", "database_number": NUMBER}


def catalog(*, matches=(), teams=(), **changes):
    result = {
        "success": True, "available": True, "source": "E3D21 SDK DbSystem.GetAll(DB/TEAM)",
        "single_dispatch": True, "atomic_snapshot": False,
        "session_id": "SESSION", "project": "PROJECT", "mdb": "ALL", "context_id": CONTEXT,
        "expected_context": CONTEXT, "ce_before": "=1/2", "ce_after": "=1/2",
        "ce_unchanged": True, "identity_unchanged": True,
        "candidate": {**CANDIDATE, "full_database_name": "PIPING/NEWDB"},
        "database_matches": list(matches), "database_match_count": len(matches), "database_matches_truncated": False,
        "team_matches": list(teams), "team_match_count": len(teams), "team_matches_truncated": False,
        "counts": {"catalog_database_count": 4, "catalog_team_count": 2,
                   "mdb_database_positive_control_count": 1, "mdb_team_positive_control_count": 1}, "read_errors": [],
    }
    result.update(changes)
    return {"ok": True, "version": "E3D21", "session_id": "SESSION", "result": result}


def match(**changes):
    return {"name": "PIPING/NEWDB", "database_number": NUMBER, "name_match": True,
            "database_number_match": True, "current_mdb_match": "no_name_or_number_match", **changes}


def world_level(*, matches=(), teams=(), success=True, available=True, status=None, **changes):
    if status is None:
        status = "observed" if success else "partial" if available else "unavailable"
    result = {
        "success": success, "available": available, "status": status,
        "source": "E3D21 SDK DbSystem.GetAllWorldLevel(DB/TEAM)",
        "visibility": "world_level",
        "visibility_scope": "Global-equivalent world-level DB/TEAM visibility only; this is not Admin permission, Area availability, project-wide catalog coverage or create authorization.",
        "admin_permission_verified": False, "area_availability_verified": False,
        "project_coverage_verified": False,
        "single_dispatch": True, "atomic_snapshot": False,
        "session_id": "SESSION", "project": "PROJECT", "mdb": "ALL", "context_id": CONTEXT,
        "expected_context": CONTEXT, "ce_before": "=1/2", "ce_after": "=1/2",
        "ce_unchanged": True, "identity_unchanged": True,
        "candidate": {**CANDIDATE, "full_database_name": "PIPING/NEWDB"},
        "database_matches": list(matches), "database_match_count": len(matches) if available else None,
        "database_matches_truncated": False, "team_matches": list(teams),
        "team_match_count": len(teams) if available else None, "team_matches_truncated": False,
        "counts": {"world_database_count": 4 if available else None,
                   "world_database_inspected_count": 4 if available else None,
                   "world_team_count": 2 if available else None,
                   "world_team_inspected_count": 2 if available else None},
        "read_errors": [],
    }
    result.update(changes)
    return result


class ProjectCatalogProbeTests(unittest.TestCase):
    def call(self, native, *, opt_in=True, snapshot=None, drift=False, args=None, mode="bridge"):
        log = []

        def read(method, path, payload=None, **kwargs):
            log.append((method, path))
            if path == "/health":
                changed = drift and log.count(("GET", path)) > 1
                return {**health(), **({"bridge_build": "OTHER"} if changed else {})}
            if path == "/api/v1/project":
                return project()
            if path == "/api/v1/database/identity-snapshot":
                self.assertEqual(kwargs.get("query"), {"expected_context": CONTEXT})
                return snapshot if snapshot is not None else snapshot_from_rows(rows())
            self.assertEqual((method, path, kwargs.get("version")), ("POST", PATH, "E3D21"))
            self.assertEqual(payload, {**CANDIDATE, "expected_context": CONTEXT})
            if isinstance(native, Exception):
                raise native
            return deepcopy(native)

        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "e3d_database_preflight", "arguments": args if args is not None else
            {**ARGS, "database_number": NUMBER, **({"include_project_catalog": True} if opt_in else {})}}}
        server = create_server(Settings(version="E3D21", mode=mode, allow_commands=False))
        with patch.object(BridgeClient, "_request", side_effect=read):
            outcome = server._dispatch(request)["result"]
        return outcome, log

    def test_opt_in_observes_conflicts_and_team_without_authorizing_creation(self):
        response, log = self.call(catalog(matches=[match()], teams=["PIPING"]))
        self.assertEqual(log, [("GET", "/health"), ("GET", "/api/v1/project"),
                               ("GET", "/api/v1/database/identity-snapshot"), ("POST", PATH),
                               ("GET", "/health"), ("GET", "/api/v1/project")])
        result = response["structuredContent"]["result"]
        self.assertTrue(result["complete"])
        self.assertFalse(result["conflicts"]["name_in_current_mdb"])
        self.assertEqual(result["project_uniqueness"], {"name": False, "database_number": False})
        self.assertTrue(result["team_exists"])
        self.assertFalse(result["project_catalog"]["project_coverage_verified"])
        self.assertFalse(result["creation_attempted"])
        self.assertFalse(result["execution_supported"])

    def test_world_level_positive_evidence_is_merged_without_admin_authorization(self):
        native = catalog(matches=[], teams=[])
        native["result"]["world_level"] = world_level(matches=[match()], teams=["PIPING"])
        response, _ = self.call(native)
        result = response["structuredContent"]["result"]
        project_catalog = result["project_catalog"]
        self.assertEqual(project_catalog["world_level"]["status"], "observed")
        self.assertEqual(project_catalog["positive_matches"], {"name": True, "database_number": True, "team": True})
        self.assertEqual(result["project_uniqueness"], {"name": False, "database_number": False})
        self.assertTrue(result["team_exists"])
        self.assertFalse(project_catalog["world_level"]["native_observation"]["admin_permission_verified"])
        self.assertFalse(project_catalog["world_level"]["native_observation"]["area_availability_verified"])

    def test_world_level_nonmatch_stays_unknown_and_never_proves_absence(self):
        native = catalog(matches=[], teams=[])
        native["result"]["world_level"] = world_level()
        response, _ = self.call(native)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["project_catalog"]["world_level"]["status"], "observed")
        self.assertEqual(result["project_uniqueness"], {"name": None, "database_number": None})
        self.assertIsNone(result["team_exists"])

    def test_world_level_invalid_context_suppresses_only_world_positives(self):
        native = catalog(matches=[], teams=[])
        native["result"]["world_level"] = world_level(matches=[match()], teams=["PIPING"], context_id="0" * 64)
        response, _ = self.call(native)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["project_catalog"]["world_level"]["status"], "unverified")
        self.assertEqual(result["project_uniqueness"], {"name": None, "database_number": None})
        self.assertIsNone(result["team_exists"])

    def test_world_level_unavailable_preserves_base_catalog_contract(self):
        native = catalog(matches=[match()], teams=["PIPING"])
        native["result"]["world_level"] = world_level(success=False, available=False, status="unavailable",
                                                      matches=[], teams=[], read_errors=[{"stage": "world_level_array"}])
        response, _ = self.call(native)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["project_catalog"]["world_level"]["status"], "unavailable")
        self.assertEqual(result["project_uniqueness"], {"name": False, "database_number": False})
        self.assertTrue(result["team_exists"])

    def test_world_level_native_array_failure_and_empty_arrays_remain_unknown(self):
        # GetAllWorldLevel failures leave all counts null; a successful empty
        # array scan reports zero counts. Neither shape provides a match.
        observations = [
            world_level(success=False, available=False, read_errors=[{
                "stage": "world_level_array", "refno": None, "error": "Optional world array is unavailable"}]),
            world_level(counts={"world_database_count": 0, "world_database_inspected_count": 0,
                                "world_team_count": 0, "world_team_inspected_count": 0}),
        ]
        for observation in observations:
            with self.subTest(status=observation["status"]):
                native = catalog()
                native["result"]["world_level"] = observation
                response, _ = self.call(native)
                result = response["structuredContent"]["result"]
                world = result["project_catalog"]["world_level"]
                self.assertEqual(world["status"], observation["status"])
                self.assertEqual(world["native_observation"], observation)
                self.assertEqual(result["project_uniqueness"], {"name": None, "database_number": None})
                self.assertIsNone(result["team_exists"])
                self.assertTrue(result["complete"])

    def test_world_level_native_partial_reads_keep_only_readable_positive_fields(self):
        counts = world_level()["counts"]
        cases = [
            ("world_level_database", [match()], ["PIPING"],
             {**counts, "world_database_inspected_count": 3},
             {"name": True, "database_number": True, "team": True}),
            ("world_level_team", [match()], [],
             {**counts, "world_team_inspected_count": 1},
             {"name": True, "database_number": True, "team": None}),
            ("world_level_database_number", [match(database_number=None, database_number_match=None,
                                                    current_mdb_match="indeterminate")], [], counts,
             {"name": True, "database_number": None, "team": None}),
            ("world_level_database_name", [match(name=None, name_match=None,
                                                  current_mdb_match="indeterminate")], [], counts,
             {"name": None, "database_number": True, "team": None}),
        ]
        for stage, matches, teams, observed_counts, positive in cases:
            with self.subTest(stage=stage):
                native = catalog()
                native["result"]["world_level"] = world_level(
                    success=False, matches=matches, teams=teams, counts=observed_counts,
                    read_errors=[{"stage": stage, "refno": None, "error": "Optional world value is unavailable"}])
                response, _ = self.call(native)
                result = response["structuredContent"]["result"]
                self.assertEqual(result["project_catalog"]["world_level"]["status"], "partial")
                self.assertEqual(result["project_catalog"]["positive_matches"], positive)
                self.assertEqual(result["project_uniqueness"], {
                    field: False if positive[field] is True else None for field in ("name", "database_number")})
                self.assertIs(result["team_exists"], positive["team"])
                self.assertTrue(result["complete"])

    def test_malformed_world_evidence_never_promotes_matches_or_discards_base_matches(self):
        counts = world_level()["counts"]
        cases = [
            ("unavailable_with_matches", {"success": False, "available": False, "status": "unavailable"}),
            ("success_without_availability", {"available": False}),
            ("multiple_dispatches", {"single_dispatch": False}),
            ("claimed_atomic_snapshot", {"atomic_snapshot": True}),
            ("missing_count", {"counts": {key: value for key, value in counts.items()
                                          if key != "world_team_count"}}),
            ("unknown_count", {"counts": {**counts, "world_database_count": None}}),
            ("oversized_array", {"counts": {**counts, "world_database_count": 10001,
                                              "world_database_inspected_count": 10001}}),
            ("inspected_beyond_array", {"counts": {**counts, "world_database_inspected_count": 5}}),
            ("uninspected_database", {"counts": {**counts, "world_database_inspected_count": 3}}),
            ("uninspected_team", {"counts": {**counts, "world_team_inspected_count": 1}}),
            ("database_match_without_inspection", {"success": False, "status": "partial",
                 "counts": {**counts, "world_database_inspected_count": 0}}),
            ("team_match_without_inspection", {"success": False, "status": "partial",
                 "counts": {**counts, "world_team_inspected_count": 0}}),
            ("success_without_name", {"database_matches": [match(name=None, name_match=None,
                                                                  current_mdb_match="indeterminate")]}),
            ("success_without_number", {"database_matches": [match(database_number=None,
                 database_number_match=None, current_mdb_match="indeterminate")]}),
        ]
        for label, changes in cases:
            for base_positive in (False, True):
                with self.subTest(case=label, base_positive=base_positive):
                    native = catalog(matches=[match(database_number=NUMBER - 1, database_number_match=False)]
                                     if base_positive else [])
                    observation = world_level(matches=[match()], teams=["PIPING"])
                    observation.update(changes)
                    native["result"]["world_level"] = observation
                    response, _ = self.call(native)
                    result = response["structuredContent"]["result"]
                    project_catalog = result["project_catalog"]
                    self.assertEqual(project_catalog["world_level"]["status"], "unverified")
                    self.assertEqual(project_catalog["status"], "observed")
                    self.assertEqual(project_catalog["positive_matches"], {
                        "name": True if base_positive else None, "database_number": None, "team": None})
                    self.assertEqual(result["project_uniqueness"], {
                        "name": False if base_positive else None, "database_number": None})
                    self.assertIsNone(result["team_exists"])
                    self.assertTrue(result["complete"])

    def test_partial_match_preserves_positive_identity_and_unknown_fields(self):
        native = catalog(matches=[match(database_number=None, database_number_match=None,
                                        current_mdb_match="indeterminate")],
                         success=False, team_match_count=None, read_errors=[{"code": "identity_read_failed"}])
        response, _ = self.call(native)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["project_catalog"]["status"], "partial")
        self.assertEqual(result["project_uniqueness"], {"name": False, "database_number": None})
        self.assertIsNone(result["team_exists"])

    def test_empty_or_older_catalog_keeps_current_mdb_result_and_unknown_absence(self):
        cases = [(catalog(), "observed"), (BridgeError("Not found", http_status=404), "unavailable")]
        for native, status in cases:
            with self.subTest(status=status):
                response, _ = self.call(native)
                result = response["structuredContent"]["result"]
                self.assertTrue(result["complete"])
                self.assertEqual(result["project_catalog"]["status"], status)
                self.assertEqual(result["project_uniqueness"], {"name": None, "database_number": None})
                self.assertIsNone(result["team_exists"])

    def test_default_invalid_and_unverified_snapshot_do_not_probe(self):
        response, log = self.call(None, opt_in=False)
        self.assertEqual(len(log), 5)
        self.assertNotIn("project_catalog", response["structuredContent"]["result"])
        response, log = self.call(None, snapshot=snapshot_from_rows(rows(), complete=False))
        self.assertNotIn(("POST", PATH), log)
        self.assertEqual(response["structuredContent"]["result"]["project_catalog"]["status"], "not_attempted")
        response, log = self.call(None, args={**ARGS, "include_project_catalog": 1})
        self.assertTrue(response["isError"])
        self.assertEqual(log, [])

    def test_mismatched_native_results_or_outer_drift_suppress_positives(self):
        cases = [(catalog(matches=[match(name_match=False)]), False),
                 (catalog(matches=[match()], ce_after="=1/999"), False),
                 (catalog(matches=[match()]), True)]
        for native, drift in cases:
            with self.subTest(native=native, drift=drift):
                response, _ = self.call(native, drift=drift)
                result = response["structuredContent"]["result"]
                self.assertEqual(result["project_catalog"]["status"], "unverified")
                self.assertEqual(result["project_uniqueness"], {"name": None, "database_number": None})


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import importlib.util
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("pump_hd002_runner", SCRIPTS / "build-pump-hd002-live.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
VERIFIER_SPEC = importlib.util.spec_from_file_location("pump_example_panel_verifier", SCRIPTS / "verify-pump-hd002-panels.py")
verifier = importlib.util.module_from_spec(VERIFIER_SPEC)
VERIFIER_SPEC.loader.exec_module(verifier)


def envelope(value):
    return {"ok": True, "result": {"success": True, **value}}


def spatial(ref, frame, position=None):
    return envelope({"refno": ref, "complete": True, "errors": {},
        "frame": {"refno": frame, "requested": frame, "type": "WORLD" if frame == "WORLD" else "EQUI"},
        "position": dict(zip(("x", "y", "z"), position or [0, 0, 0])),
        "orientation": deepcopy(runner.IDENTITY)})


def owner(ref, parent="=1/20", parent_type="SUBE"):
    return envelope({"parent": {"refno": parent, "type": parent_type}, "returned_count": 1,
        "has_more": False, "next_cursor": None, "read_errors": [],
        "members": [{"refno": ref, "type": "PANE", "sibling_index": 1}]})


def root_request():
    return {"parent_refno": runner.PARENT, "element_type": "EQUI", "name": runner.ROOT_NAME,
        "position": runner.WORLD_POSITION, "orientation": runner.IDENTITY, "wrt": "WORLD",
        "attributes": {"DESC": "Detailed horizontal centrifugal pump; illustrative dimensions"}}


def root_state():
    return {"operations": {"root": {"status": "confirmed", "tool": "e3d_create_element",
        "arguments": {**root_request(), "dry_run": False},
        "response": envelope({"refno": "=1/10", "readback_verified": True, "ce_unchanged": True,
                              "parent": {"refno": runner.PARENT, "type": "ZONE"}})}}}


def root_reads():
    return {"root_recovery_query": {"ok": True, "elements": [{"refno": "=1/10", "type": "EQUI", "name": runner.ROOT_NAME}]},
        "root_recovery_owner": owner("=1/10", runner.PARENT, "ZONE"),
        "root_recovery_spatial": spatial("=1/10", "WORLD", runner.WORLD_POSITION),
        "root_recovery_attributes": envelope({"refno": "=1/10", "errors": {}, "typed_read_errors": {},
            "typed_values": root_request()["attributes"]})}


def panel_item():
    return {"key": "panel", "position": [0, 0, 0], "orientation": deepcopy(runner.IDENTITY),
        "vertices": [[0, 0], [20, 0], [0, 10]], "thickness": 5, "justification": "centre"}


def panel_geometry():
    return {"success": True, "complete": True, "partial": False, "element": {"refno": "=1/30", "type": "PANEL", "name": runner.NAME_PREFIX + "case-panel"},
        "unit": "mm", "coordinate_frame": "=1/30", "coordinate_source": "PAVE POS qualified WRT PANE",
        "thickness_mm": 5, "native_justification": "CENT", "loop_count": 1, "vertex_count": 3,
        "editable": True, "edit_reasons": [], "revision": "a" * 64,
        "topology_valid": True, "unsupported_features": [], "read_errors": {}, "loops": [{
            "element": {"refno": "=1/31", "type": "PLOOP"}, "loop_index": 0,
            "complete": True, "thickness_mm": 5, "native_justification": "CENT", "blind": False,
            "vertex_count": 3, "polygon_valid": True,
            "polygon": {"valid": True, "winding": "counterclockwise", "signed_area_mm2": 100,
                        "issues": [], "issues_truncated": False},
            "unsupported_features": [], "read_errors": {}, "planar_parallel_xy": True,
            "coplanar_in_local_xy": True, "z_range_mm": [0, 0],
            "vertices": [{"refno": f"=1/{32 + index}", "vertex_index": index,
                "position": point + [0], "fillet_radius_mm": 0, "CTYA": "unset", "GTYP": "unset", "BANG": "0deg"}
                for index, point in enumerate(panel_item()["vertices"])]}]}


class RecoveryTests(unittest.TestCase):
    def test_confirmed_root_crash_window_recovers_only_after_independent_reads(self):
        state, values, saves, calls = root_state(), root_reads(), [], []
        original = deepcopy(state["operations"]["root"]["response"])

        def read(key, tool, args):
            self.assertFalse(tool.startswith("e3d_create"))
            calls.append(tool)
            return values[key]

        self.assertTrue(runner.recover_confirmed_root(state, root_request(), read, lambda: saves.append(True)))
        self.assertEqual(state["root"], "=1/10")
        self.assertEqual(calls, ["e3d_query", "e3d_list_members", "e3d_spatial_info", "e3d_read_attributes"])
        self.assertEqual(state["operations"]["root"]["response"], original)
        self.assertEqual(len(saves), 1)

    def test_root_recovery_rejects_missing_foreign_or_changed_object_without_state_adoption(self):
        for defect in ("empty", "identity", "owner", "frame", "desc"):
            with self.subTest(defect=defect):
                state, values = root_state(), root_reads()
                if defect == "empty":
                    values["root_recovery_query"]["elements"] = []
                elif defect == "identity":
                    values["root_recovery_query"]["elements"][0]["refno"] = "=1/999"
                elif defect == "owner":
                    values["root_recovery_owner"]["result"]["has_more"] = True
                elif defect == "frame":
                    values["root_recovery_spatial"]["result"]["frame"]["requested"] = "OWNER"
                else:
                    values["root_recovery_attributes"]["result"]["typed_values"] = {"DESC": "changed"}
                with self.assertRaises(RuntimeError):
                    runner.recover_confirmed_root(state, root_request(), lambda key, *_: values[key], Mock())
                self.assertNotIn("root", state)

    def test_unconfirmed_root_never_replays_or_uses_name_as_confirmation(self):
        state, read = root_state(), Mock()
        state["operations"]["root"]["status"] = "unconfirmed"
        with self.assertRaisesRegex(RuntimeError, "never replay"):
            runner.recover_confirmed_root(state, root_request(), read, Mock())
        read.assert_not_called()

    def reconcile_fixture(self, status="unconfirmed", item=None):
        item = item or panel_item()
        tool, payload = runner.part_request("case", item, "=1/20", "=1/10")
        state = {"created": {}, "operations": {"part:case:panel": {"status": status, "tool": tool,
            "arguments": payload, "response": {"ok": False, "result": {"success": False, "refno": "=1/30",
                "error": "original unknown outcome"}}}}}
        values = {"reconcile_query:case:panel": {"ok": True, "elements": [{"refno": "=1/30", "type": item.get("type", "PANE")}]},
            "reconcile_owner:case:panel": owner("=1/30"),
            "reconcile_spatial:case:panel": spatial("=1/30", "=1/10"),
            "reconcile_panel:case:panel": {"ok": True, "result": panel_geometry()}}
        return item, tool, payload, state, values

    def reconcile(self, fixture, read=None):
        item, tool, payload, state, values = fixture
        return runner.reconcile_part(state, "case:panel", "case", item, "=1/20", "=1/10", tool, payload,
                                     read or (lambda key, *_: values[key]), Mock())

    def test_both_unknown_statuses_recover_complete_panel_and_preserve_failed_receipt(self):
        for status in ("dispatched", "unconfirmed"):
            with self.subTest(status=status):
                fixture = self.reconcile_fixture(status)
                state = fixture[3]
                original = deepcopy(state["operations"]["part:case:panel"]["response"])
                self.assertTrue(self.reconcile(fixture))
                self.assertEqual(state["created"]["case:panel"]["refno"], "=1/30")
                operation = state["operations"]["part:case:panel"]
                self.assertEqual(operation["status"], "confirmed_by_independent_readback")
                self.assertEqual(operation["recovered_from_status"], status)
                self.assertEqual(operation["response"], original)
                self.assertEqual(operation["reconciled_evidence"]["panel_geometry"]["vertex_count"], 3)
                self.assertEqual(state["unresolved_new_parts"], {})

    def test_empty_query_stays_unresolved_and_never_falls_through_to_creation(self):
        for status in ("dispatched", "unconfirmed"):
            with self.subTest(status=status):
                fixture = self.reconcile_fixture(status)
                fixture[4]["reconcile_query:case:panel"]["elements"] = []
                read = Mock(side_effect=lambda key, *_: fixture[4][key])
                with self.assertRaisesRegex(RuntimeError, "without retry"):
                    self.reconcile(fixture, read)
                self.assertEqual(read.call_count, 1)
                self.assertEqual(fixture[3]["created"], {})
                self.assertEqual(fixture[3]["operations"]["part:case:panel"]["status"], status)
                self.assertIn("case:panel", fixture[3]["unresolved_new_parts"])

    def test_reconcile_rejects_changed_request_before_any_read(self):
        fixture, read = self.reconcile_fixture(), Mock()
        fixture[3]["operations"]["part:case:panel"]["arguments"] = {"different": True}
        with self.assertRaisesRegex(RuntimeError, "never replay"):
            self.reconcile(fixture, read)
        read.assert_not_called()

    def test_incomplete_or_different_native_panel_is_never_added_to_created(self):
        for defect in ("partial", "thickness", "justification", "blind", "vertex", "fillet", "winding", "frame"):
            with self.subTest(defect=defect):
                fixture = self.reconcile_fixture()
                geometry = fixture[4]["reconcile_panel:case:panel"]["result"]
                loop = geometry["loops"][0]
                if defect == "partial":
                    geometry["complete"] = False
                elif defect == "thickness":
                    loop["thickness_mm"] = 6
                elif defect == "justification":
                    loop["native_justification"] = "POS"
                elif defect == "blind":
                    loop["blind"] = True
                elif defect == "vertex":
                    loop["vertices"][1]["position"][0] += 1
                elif defect == "fillet":
                    loop["vertices"][1]["fillet_radius_mm"] = 1
                elif defect == "winding":
                    loop["polygon"]["winding"] = "clockwise"
                else:
                    geometry["coordinate_frame"] = "=1/999"
                with self.assertRaises(RuntimeError):
                    self.reconcile(fixture)
                self.assertEqual(fixture[3]["created"], {})
                self.assertEqual(fixture[3]["operations"]["part:case:panel"]["status"], "unconfirmed")

    def test_original_partial_vertex_reference_prefix_must_match(self):
        fixture = self.reconcile_fixture()
        receipt = fixture[3]["operations"]["part:case:panel"]["response"]["result"]
        receipt["vertex_refnos"] = ["=1/999"]
        with self.assertRaisesRegex(RuntimeError, "prefix"):
            self.reconcile(fixture)
        self.assertEqual(fixture[3]["created"], {})

    def test_partial_creation_tree_survives_raw_receipt_crash_window(self):
        for vertex in ("=1/32", "=1/999"):
            with self.subTest(vertex=vertex):
                fixture = self.reconcile_fixture("dispatched")
                operation = fixture[3]["operations"]["part:case:panel"]
                response = operation.pop("response")
                response["result"]["creation_journal"] = [
                    {"refno": "=1/30", "type": "PANE", "owner_refno": "=1/20", "children": ["=1/31"], "creation_ordinal": 0},
                    {"refno": "=1/31", "type": "PLOO", "owner_refno": "=1/30", "children": [vertex], "creation_ordinal": 1},
                    {"refno": vertex, "type": "PAVE", "owner_refno": "=1/31", "children": [], "creation_ordinal": 2},
                ]
                operation["mcp_response"] = {"isError": True, "structuredContent": response}
                if vertex == "=1/32":
                    self.assertTrue(self.reconcile(fixture))
                else:
                    with self.assertRaisesRegex(RuntimeError, "prefix"):
                        self.reconcile(fixture)
                    self.assertEqual(fixture[3]["created"], {})

    def test_direct_member_follows_page_cursor_even_after_finding_target(self):
        first = owner("=1/30")
        first["result"].update(has_more=True, next_cursor="opaque")
        second = owner("=1/31")
        second["result"]["members"][0]["sibling_index"] = 2
        read = Mock(side_effect=[first, second])
        self.assertEqual(runner.direct_member(read, "owner", "=1/20", "=1/30", "SUBE")["refno"], "=1/30")
        self.assertEqual(read.call_args_list[0].args[2], {"refno": "=1/20", "expected_type": "SUBE", "limit": 200})
        self.assertEqual(read.call_args_list[1].args[2]["cursor"], "opaque")


class SelectionAndStorageTests(unittest.TestCase):
    def test_verify_only_restores_confirmed_root_without_create_preview_or_write(self):
        state = {**root_state(), "source_sha256": {}, "created": {}, "reads": {}}
        reads = root_reads()

        class RecoveryClient:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def rpc(self, *_):
                raise AssertionError("verify-only dispatched a write")

            def call(self, tool, args):
                if tool == "e3d_status":
                    return {"ok": True, "connected": True, "session_id": "same", "state": {"database_ready": True}}
                if tool == "e3d_project_info":
                    return {"ok": True, "project": {"name": runner.EXPECTED_PROJECT, "mdb": runner.EXPECTED_MDB, "current_element": "=1/0"}}
                keys = {"e3d_query": "root_recovery_query", "e3d_list_members": "root_recovery_owner",
                        "e3d_spatial_info": "root_recovery_spatial", "e3d_read_attributes": "root_recovery_attributes"}
                if tool in keys:
                    return reads[keys[tool]]
                raise AssertionError("Unexpected create preview or non-read tool: " + tool)

        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "journal.json"
            report.write_text(json.dumps(state))
            with patch.object(runner, "REPORT", report), patch.object(runner, "LiveMcp", RecoveryClient), redirect_stdout(StringIO()):
                runner.run(SimpleNamespace(execute=False, build_only=True), [], {}, set(), set())
            self.assertEqual(json.loads(report.read_text())["root"], "=1/10")

    def test_mutation_raw_receipt_is_durable_before_decode_and_native_error_is_preserved(self):
        cases = [
            {"isError": True, "content": [{"type": "text", "text": "native plain-text failure without JSON"}]},
            {"isError": True, "content": [{"type": "text", "text": "legacy text"}], "structuredContent": {
                "ok": False, "result": {"success": False, "creation_journal": [{"refno": "=1/2", "detail": "x" * 9000}],
                                         "error": "actual native PAVE failure"}}},
        ]
        for raw in cases:
            with self.subTest(raw_format="structuredContent" in raw), tempfile.TemporaryDirectory() as directory:
                report = Path(directory) / "journal.json"

                class FailureClient:
                    def __enter__(self):
                        return self

                    def __exit__(self, *_):
                        pass

                    def call(self, tool, args):
                        if tool == "e3d_status":
                            return {"ok": True, "connected": True, "session_id": "same", "state": {"database_ready": True}}
                        if tool == "e3d_project_info":
                            return {"ok": True, "project": {"name": runner.EXPECTED_PROJECT, "mdb": runner.EXPECTED_MDB, "current_element": "=1/0"}}
                        if tool == "e3d_query":
                            return {"ok": True, "elements": []}
                        if tool == "e3d_create_element":
                            return envelope({"dry_run": True})
                        raise AssertionError(tool)

                    def rpc(self, *_):
                        return raw

                decoder = runner.structured_content

                def decode_after_durable_outer(value):
                    on_disk = json.loads(report.read_text())
                    self.assertEqual(on_disk["operations"]["root"]["mcp_response"], raw)
                    self.assertEqual(on_disk["operations"]["root"]["status"], "dispatched")
                    return decoder(value)

                with patch.object(runner, "REPORT", report), patch.object(runner, "LiveMcp", FailureClient), \
                        patch.object(runner, "structured_content", side_effect=decode_after_durable_outer):
                    with self.assertRaises(runner.LiveMcpToolError):
                        runner.run(SimpleNamespace(execute=True, build_only=False), [], {}, set(), set())
                operation = json.loads(report.read_text())["operations"]["root"]
                self.assertEqual(operation["mcp_response"], raw)
                self.assertEqual(operation["exception_mcp_response"], raw)
                self.assertEqual(operation["status"], "unconfirmed")
                expected = "actual native PAVE failure" if "structuredContent" in raw else "native plain-text failure"
                self.assertIn(expected, operation["error"])

    def test_read_exception_keeps_full_raw_and_structured_receipt(self):
        raw = {"isError": True, "structuredContent": {"ok": False,
            "result": {"error": "read failed", "large_evidence": "y" * 12000}}}

        class ReadFailureClient:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def call(self, tool, args):
                raise runner.LiveMcpToolError(tool, raw)

        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "journal.json"
            with patch.object(runner, "REPORT", report), patch.object(runner, "LiveMcp", ReadFailureClient):
                with self.assertRaises(runner.LiveMcpToolError):
                    runner.run(SimpleNamespace(execute=False, build_only=False), [], {}, set(), set())
            failure = json.loads(report.read_text())["read_failures"]["health_before"]
            self.assertEqual(failure["mcp_response"], raw)
            self.assertEqual(failure["response"], raw["structuredContent"])

    def test_subset_accepts_previously_created_other_groups_and_reports_skips(self):
        state = {"created": {"base:a": {}, "motor:b": {}}, "operations": {
            "part:base:c": {"status": "unconfirmed"}}}
        full = runner.selection_report(state, {"base:a", "base:c"}, {"base:a"}, {"base:c"}, False)
        self.assertFalse(full)
        self.assertEqual(state["selection"]["active_confirmed_parts"], 1)
        self.assertEqual(state["selection"]["missing_active_keys"], [])
        self.assertEqual(state["selection"]["skipped_keys"], ["base:c"])
        self.assertEqual(state["selection"]["unresolved_keys"], ["base:c"])
        self.assertEqual(state["selection"]["created_outside_active_keys"], ["motor:b"])

    def test_atomic_save_syncs_unique_temporary_and_preserves_unrelated_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "journal.json"
            unrelated = report.with_suffix(".tmp")
            unrelated.write_text("other process", encoding="utf-8")
            with patch.object(runner.os, "fsync", wraps=runner.os.fsync) as sync:
                runner.atomic_save(report, {"confirmed": True})
            self.assertEqual(json.loads(report.read_text()), {"confirmed": True})
            self.assertEqual(sync.call_count, 1)
            self.assertEqual(unrelated.read_text(), "other process")
            self.assertEqual(list(Path(directory).glob("journal.json.*.tmp")), [])

    def test_runner_lock_excludes_other_process_and_releases_on_process_exit(self):
        worker = """import importlib.util, os, sys
sys.path.insert(0, sys.argv[1])
spec = importlib.util.spec_from_file_location('runner_lock_worker', os.path.join(sys.argv[1], 'build-pump-hd002-live.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
lock = module.RunnerLock(module.Path(sys.argv[2]))
lock.__enter__()
print('LOCKED', flush=True)
sys.stdin.readline()
os._exit(0)
"""
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "journal.json"
            process = subprocess.Popen([sys.executable, "-u", "-c", worker, str(SCRIPTS), str(report)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(process.stdout.readline().strip(), "LOCKED")
                with self.assertRaisesRegex(RuntimeError, "holds the journal lock"):
                    with runner.RunnerLock(report):
                        self.fail("The other process owns this lock")
                process.stdin.write("exit\n")
                process.stdin.flush()
                self.assertEqual(process.wait(timeout=10), 0)
                with runner.RunnerLock(report):
                    self.assertTrue(Path(str(report) + ".lock").exists())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
                process.stdin.close()
                process.stdout.close()
                process.stderr.close()

    def test_main_locks_before_reading_journal_or_constructing_live_client(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "journal.json"
            report.write_text("invalid journal to prove it was never read")
            with runner.RunnerLock(report), patch.object(runner, "REPORT", report), \
                    patch.object(runner, "validate_plan", return_value=([], {})), \
                    patch.object(runner, "LiveMcp") as live:
                with self.assertRaisesRegex(RuntimeError, "holds the journal lock"):
                    runner.main(["--execute", "--manifest", str(Path(directory) / "manifest.json"),
                                 "--journal", str(report), "--parent-refno", runner.PARENT,
                                 "--root-name", runner.ROOT_NAME, "--world-position", "0", "0", "0",
                                 "--expected-project", runner.EXPECTED_PROJECT, "--expected-mdb", runner.EXPECTED_MDB])
                live.assert_not_called()

    def test_verify_subset_reports_partial_phase_and_keeps_global_created_history(self):
        item = {"key": "one", "type": "BOX", "position": [0, 0, 0],
                "orientation": deepcopy(runner.IDENTITY), "attributes": {"XLEN": "10"}}
        state = {"source_sha256": {"old-source": "old"}, "operations": {},
                 "root": "=1/10", "created": {"base:one": {"refno": "=1/30", "type": "BOX"},
                    "motor:old": {"refno": "=1/40", "type": "BOX"}}, "reads": {}}

        class ReadOnlyClient:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def rpc(self, *_):
                raise AssertionError("Verification must never dispatch a mutation")

            def call(self, tool, args):
                if tool == "e3d_status":
                    return {"ok": True, "connected": True, "session_id": "same", "state": {"database_ready": True}}
                if tool == "e3d_project_info":
                    return {"ok": True, "project": {"name": runner.EXPECTED_PROJECT, "mdb": runner.EXPECTED_MDB, "current_element": "=1/0"}}
                if tool == "e3d_validate_model":
                    return {"ok": True, "validation": {"valid": True, "complete": True,
                        "checks_complete": True, "issues": [], "check_read_errors": []}}
                if tool == "e3d_spatial_info":
                    return spatial("=1/30", "=1/10")
                if tool == "e3d_read_attributes":
                    return envelope({"errors": {}, "typed_read_errors": {}, "typed_values": {"XLEN": 10}})
                raise AssertionError(tool)

        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "journal.json"
            report.write_text(json.dumps(state))
            with patch.object(runner, "REPORT", report), patch.object(runner, "LiveMcp", ReadOnlyClient), redirect_stdout(StringIO()):
                runner.run(SimpleNamespace(execute=False, build_only=False),
                    [{"group": "base", "elements": [item], "panels": []}], {"new-source": "new"}, {"base:one"}, set())
            result = json.loads(report.read_text())
            self.assertEqual(result["phase"], "partial_transforms_dimensions_cuts_verified")
            self.assertFalse(result["full_panel_acceptance"])
            self.assertEqual(result["selection"]["active_confirmed_parts"], 1)
            self.assertEqual(len(result["created"]), 2)
            self.assertEqual(result["source_sha256"], {"old-source": "old", "new-source": "new"})


class ExplicitExampleInputTests(unittest.TestCase):
    def manifest(self, directory):
        path = Path(directory) / "example-pump-base-plan.json"
        path.write_text(json.dumps({"group": "base", "elements": [{
            "key": "one", "type": "BOX", "position": [0, 0, 0],
            "orientation": runner.IDENTITY, "attributes": {"XLEN": "10", "YLEN": "10", "ZLEN": "10"}}],
            "panels": []}), encoding="utf-8")
        return path

    def live_arguments(self, manifest, journal):
        return ["--execute", "--manifest", str(manifest), "--journal", str(journal),
                "--parent-refno", "=2/30", "--root-name", "/EXPLICIT-EXAMPLE",
                "--world-position", "1", "2", "3", "--expected-project", "TEST_PROJECT",
                "--expected-mdb", "TEST_MDB"]

    def test_default_is_an_unconfigured_offline_preview(self):
        with patch.object(runner, "LiveMcp") as live, patch.object(runner, "RunnerLock") as lock, \
                patch.object(runner, "validate_plan") as validate, redirect_stdout(StringIO()) as output:
            self.assertEqual(runner.main([]), 0)
        preview = json.loads(output.getvalue())
        self.assertEqual(preview["mode"], "offline_preview")
        self.assertFalse(preview["configured"])
        self.assertFalse(preview["execution_attempted"])
        self.assertEqual(preview["native_calls"], 0)
        live.assert_not_called()
        lock.assert_not_called()
        validate.assert_not_called()

    def test_manifest_preview_reads_only_explicit_local_input(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.manifest(directory)
            before = manifest.read_bytes()
            with patch.object(runner, "LiveMcp") as live, patch.object(runner, "RunnerLock") as lock, \
                    redirect_stdout(StringIO()) as output:
                self.assertEqual(runner.main(["--manifest", str(manifest)]), 0)
            preview = json.loads(output.getvalue())
            self.assertEqual(preview["parts"], 1)
            self.assertEqual(preview["native_calls"], 0)
            self.assertFalse(preview["execution_attempted"])
            self.assertEqual(manifest.read_bytes(), before)
            self.assertEqual(list(Path(directory).iterdir()), [manifest])
            live.assert_not_called()
            lock.assert_not_called()

    def test_live_modes_require_explicit_target_and_inputs_before_io(self):
        for mode in ("--execute", "--verify-only"):
            with self.subTest(mode=mode), patch.object(runner, "LiveMcp") as live, \
                    patch.object(runner, "validate_plan") as validate, redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as failure:
                    runner.main([mode])
                self.assertEqual(failure.exception.code, 2)
                live.assert_not_called()
                validate.assert_not_called()

    def test_explicit_target_reaches_runner_without_replacing_example_constants(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest, journal = self.manifest(directory), Path(directory) / "journal.json"
            with patch.object(runner, "run") as run, patch.object(runner, "LiveMcp") as live:
                self.assertEqual(runner.main(self.live_arguments(manifest, journal)), 0)
            arguments = run.call_args.args[0]
            self.assertEqual(arguments.parent_refno, "=2/30")
            self.assertEqual(arguments.root_name, "/EXPLICIT-EXAMPLE")
            self.assertEqual(arguments.world_position, [1.0, 2.0, 3.0])
            self.assertEqual((arguments.expected_project, arguments.expected_mdb), ("TEST_PROJECT", "TEST_MDB"))
            self.assertEqual(arguments.journal, journal)
            self.assertEqual((runner.PARENT, runner.ROOT_NAME, runner.WORLD_POSITION), ("=1/20", "/EXAMPLE-PUMP-001", [0, 0, 0]))
            self.assertFalse(journal.exists())
            live.assert_not_called()

    def test_malformed_live_targets_reject_before_manifest_read(self):
        for field, value in (("--parent-refno", " =2/30"), ("--parent-refno", "=02/30"),
                             ("--parent-refno", "=2147483648/1"), ("--root-name", "/EXAMPLE;SAVEWORK"),
                             ("--world-position", "nan")):
            arguments = self.live_arguments("missing-manifest.json", "missing-journal.json")
            arguments[arguments.index(field) + 1] = value
            with self.subTest(field=field, value=value), patch.object(runner, "LiveMcp") as live, \
                    patch.object(runner, "validate_plan") as validate, redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as failure:
                    runner.main(arguments)
                self.assertEqual(failure.exception.code, 2)
                live.assert_not_called()
                validate.assert_not_called()

    def test_changed_explicit_target_does_not_adopt_an_existing_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "journal.json"
            journal.write_text(json.dumps({"root_name": "/ANOTHER-EXAMPLE"}), encoding="utf-8")
            before = journal.read_bytes()
            with patch.object(runner, "LiveMcp") as live:
                with self.assertRaisesRegex(RuntimeError, "Journal target differs"):
                    runner.run(SimpleNamespace(execute=False, build_only=False, journal=journal), [], {}, set(), set())
                live.assert_not_called()
            self.assertEqual(journal.read_bytes(), before)

    def test_verifier_default_is_an_unconfigured_offline_preview(self):
        with patch.object(verifier, "load_inputs") as load, patch.object(verifier, "run_live") as live, \
                redirect_stdout(StringIO()) as output:
            self.assertEqual(verifier.main([]), 0)
        preview = json.loads(output.getvalue())
        self.assertEqual(preview["mode"], "offline_preview")
        self.assertEqual((preview["native_calls"], preview["artifact_writes"]), (0, 0))
        load.assert_not_called()
        live.assert_not_called()

    def test_verifier_live_requires_explicit_context_before_local_inputs(self):
        with patch.object(verifier, "load_inputs") as load, patch.object(verifier, "run_live") as live, \
                redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as failure:
                verifier.main(["--live"])
            self.assertEqual(failure.exception.code, 2)
            load.assert_not_called()
            live.assert_not_called()

    def test_verifier_read_allowlist_rejects_model_writes_without_dispatch(self):
        client = Mock()
        with self.assertRaises(RuntimeError):
            verifier.Reader(client).read("e3d_create_panel", {})
        client.call.assert_not_called()


if __name__ == "__main__":
    unittest.main()

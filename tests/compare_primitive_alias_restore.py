"""Compare saved runs of the same production restoration harness, offline only.

Produce inputs with test_primitive_visual_restore.py --source ... --result ... .
The complete per-invocation evidence is compared; only the explicitly measured
managed entry-reference work counter is excluded from semantic equivalence.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def differences(before, after, path="$", limit=20):
    if type(before) is not type(after):
        return [{"path": path, "before": before, "after": after}]
    if isinstance(before, dict):
        if before.keys() != after.keys():
            return [{"path": path, "before_keys": list(before), "after_keys": list(after)}]
        result = []
        for key in before:
            result.extend(differences(before[key], after[key], f"{path}.{key}", limit))
            if len(result) >= limit:
                break
        return result[:limit]
    if isinstance(before, list):
        if len(before) != len(after):
            return [{"path": path, "before_length": len(before), "after_length": len(after)}]
        result = []
        for index, (left, right) in enumerate(zip(before, after)):
            result.extend(differences(left, right, f"{path}[{index}]", limit))
            if len(result) >= limit:
                break
        return result[:limit]
    return [] if before == after else [{"path": path, "before": before, "after": after}]


def compare(before_path, after_path):
    before = json.loads(before_path.read_text(encoding="utf-8"))
    after = json.loads(after_path.read_text(encoding="utf-8"))
    for run in (before, after):
        assert run["build"]["returncode"] == run["run"]["returncode"] == run["evidence"]["failures"] == 0
    shared_before = {key: value for key, value in before["sha256"].items() if key != before["source"]}
    shared_after = {key: value for key, value in after["sha256"].items() if key != after["source"]}
    assert shared_before == shared_after, "Inputs must use identical harness, runner, context and fault code"
    assert before["production_methods"] == after["production_methods"]
    semantic_before = deepcopy(before["evidence"])
    semantic_after = deepcopy(after["evidence"])
    for evidence in (semantic_before, semantic_after):
        for invocation in evidence["applies"]:
            del invocation["managed_entry_ref_reads"]
    mismatch = differences(semantic_before, semantic_after)
    work = []
    old_work = [item for item in before["evidence"]["applies"] if item["scenario"] == "index managed grouping work"]
    new_work = [item for item in after["evidence"]["applies"] if item["scenario"] == "index managed grouping work"]
    assert len(old_work) == len(new_work) == 2, "Both managed work fixtures must run"
    for old, new in zip(old_work, new_work):
        aliases = new["before"]["alias_order"]
        entries, targets = len(aliases), len({key.split("|")[1] for key in aliases})
        old_scans, indexed_reads = entries * targets, entries + targets
        saved = old["managed_entry_ref_reads"] - new["managed_entry_ref_reads"]
        work.append({
            "entries": entries, "unique_refs": targets,
            "old_total_entry_ref_reads": old["managed_entry_ref_reads"],
            "new_total_entry_ref_reads": new["managed_entry_ref_reads"],
            "measured_reference_reads_saved": saved,
            "old_alias_scan_ref_reads": old_scans,
            "new_index_ref_reads": indexed_reads,
            "counter_delta_matches_actual_algorithm": saved == old_scans - indexed_reads,
            "native_calls_each": new["native_calls"], "writes_each": new["writes"],
        })
    counts = after["evidence"]
    return {
        "utc": datetime.now(timezone.utc).isoformat(),
        "passed": not mismatch and all(item["counter_delta_matches_actual_algorithm"] for item in work),
        "scope": "same harness and actual extracted production Apply body against managed native-behavior fakes; no live E3D or native wall-time claim",
        "sources": {"before": {"path": before["source"], "sha256": before["sha256"][before["source"]]},
                    "after": {"path": after["source"], "sha256": after["sha256"][after["source"]]}},
        "shared_test_inputs": shared_after,
        "evidence_files": {"before": {"path": str(before_path), "sha256": sha256(before_path)},
                           "after": {"path": str(after_path), "sha256": sha256(after_path)}},
        "comparison_runner_sha256": sha256(Path(__file__).resolve()),
        "semantics_exactly_equal": not mismatch,
        "compared": ["scenario outcomes", "requests", "managed before and after state", "complete native traces",
                     "complete write traces", "setter attempts", "aggregate reset members", "exception types, messages and inner exceptions",
                     "native call and write counts", "native fault injection positions, mode, latch and generation"],
        "excluded_from_semantic_comparison": ["managed_entry_ref_reads"],
        "checks_each": counts["checks"], "scenarios_each": counts["scenarios"],
        "apply_invocations_each": len(counts["applies"]), "native_fault_cases_each": counts["native_fault_cases"],
        "native_trace_events_compared": sum(len(item["native_trace"]) for item in counts["applies"]),
        "write_trace_events_compared": sum(len(item["write_trace"]) for item in counts["applies"]),
        "mismatches": mismatch, "work": work,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before-result", type=Path, required=True)
    parser.add_argument("--after-result", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    report = compare(args.before_result.resolve(), args.after_result.resolve())
    args.result.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)

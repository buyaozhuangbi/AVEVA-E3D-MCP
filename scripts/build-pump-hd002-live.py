"""Illustrative pump runner with an offline preview default and no-replay receipts.

Imported helpers and the synthetic constants below support offline tests. Live
use requires explicit target, project/MDB, manifest and journal arguments plus
--execute or --verify-only. These checks do not establish permission, production
suitability, engineering correctness or a rollback guarantee.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time

from live_mcp import LiveMcp, LiveMcpToolError, structured_content

ROOT = Path(__file__).resolve().parents[1]
ROOT_NAME = "/EXAMPLE-PUMP-001"
PARENT = "=1/20"  # Synthetic test identity; the CLI requires an explicit live target.
WORLD_POSITION = [0, 0, 0]
EXPECTED_PROJECT = "EXAMPLE_PROJECT"
EXPECTED_MDB = "EXAMPLE_MDB"
NAME_PREFIX = "/EXAMPLE-PUMP-"
IDENTITY = {"x_axis": [1, 0, 0], "y_axis": [0, 1, 0]}
SOURCES = [ROOT / f"artifacts/example-pump-{key}-plan.json" for key in ("case", "motor", "base", "running")]
REPORT = ROOT / "artifacts/example-pump-journal.json"
RECOVERABLE = {"dispatched", "unconfirmed"}


class RunnerLock:
    """Hold an OS-released exclusive byte lock before reading the journal."""

    def __init__(self, report):
        self.path = Path(str(report) + ".lock")
        self.handle = None

    def __enter__(self):
        self.handle = self.path.open("a+b")
        try:
            if self.handle.seek(0, os.SEEK_END) == 0:
                self.handle.write(b"\0")
                self.handle.flush()
                os.fsync(self.handle.fileno())
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError) as error:
            self.handle.close()
            self.handle = None
            raise RuntimeError("Another pump runner holds the journal lock: " + str(self.path)) from error
        return self

    def __exit__(self, *_):
        if self.handle is not None:
            try:
                self.handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None


def atomic_save(report, state):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=report.parent,
                prefix=report.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False))
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(80):
            try:
                temporary.replace(report)
                return
            except PermissionError:
                if attempt == 79:
                    raise
                time.sleep(.25)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def require(value, reason):
    if not value:
        raise RuntimeError(reason)


def close(left, right, tolerance=.02):
    return len(left) == len(right) and all(type(a) in (int, float) and math.isfinite(a)
            and abs(a - b) <= tolerance for a, b in zip(left, right))


def native_result(response, tool):
    require(response.get("ok") is True and isinstance(response.get("result"), dict)
            and response["result"].get("success") is True, tool + " not confirmed successful")
    return response["result"]


def part_request(group_key, item, parent, root):
    payload = {"parent_refno": parent, "name": f"{NAME_PREFIX}{group_key}-{item['key']}",
               "position": item["position"], "orientation": item["orientation"], "wrt": root, "dry_run": False}
    if "vertices" in item:
        payload.update(vertices=item["vertices"], thickness=item["thickness"], justification=item.get("justification", "centre"))
        return "e3d_create_panel", payload
    payload.update(element_type=item["type"], attributes=item["attributes"])
    return "e3d_create_element", payload


def check_spatial(spatial, ref, frame, position, orientation, label):
    actual_frame = spatial.get("frame", {})
    frame_matches = (actual_frame.get("requested") == "WORLD" and actual_frame.get("type") == "WORLD"
                     if frame == "WORLD" else actual_frame.get("refno") == frame)
    require(spatial.get("refno") == ref and frame_matches and spatial.get("complete") is True
            and spatial.get("errors") == {}
            and close([spatial["position"][a] for a in ("x", "y", "z")], position)
            and all(close(spatial["orientation"][a], orientation[a], 1e-5) for a in ("x_axis", "y_axis")),
            "Independent transform differs from manifest: " + label)


def direct_member(read, key, parent, ref, expected_type):
    members, cursor, cursors = [], None, set()
    for index in range(50):
        request = {"refno": parent, "expected_type": expected_type, "limit": 200}
        if cursor:
            request["cursor"] = cursor
        page = native_result(read(key if index == 0 else f"{key}:{index + 1}", "e3d_list_members", request), "direct ownership")
        require(page.get("parent", {}).get("refno") == parent and page["parent"].get("type") == expected_type
                and page.get("read_errors") == [] and type(page.get("has_more")) is bool,
                "Recovered parent identity or member read is incomplete: " + key)
        rows = page.get("members")
        require(isinstance(rows, list) and page.get("returned_count") == len(rows)
                and [row.get("sibling_index") for row in rows] == list(range(len(members) + 1, len(members) + len(rows) + 1)),
                "Recovered parent member count/order differs: " + key)
        members.extend(rows)
        require(len({row["refno"] for row in members}) == len(members), "Recovered parent has duplicate member refs: " + key)
        if page["has_more"] is False:
            require(page.get("next_cursor") is None and sum(row["refno"] == ref for row in members) == 1,
                    "Recovered object is not a unique direct child: " + key)
            return next(row for row in members if row["refno"] == ref)
        cursor = page.get("next_cursor")
        require(rows and isinstance(cursor, str) and cursor and cursor not in cursors,
                "Recovered parent pagination did not advance: " + key)
        cursors.add(cursor)
    raise RuntimeError("Recovered parent pagination exceeded 50 pages: " + key)


def recover_confirmed_root(state, request, read, save):
    operation = state.get("operations", {}).get("root")
    if operation is None:
        return False
    require(operation.get("tool") == "e3d_create_element" and operation.get("arguments") == {**request, "dry_run": False}
            and operation.get("status") == "confirmed", "Root operation is unresolved or differs; never replay it")
    receipt = native_result(operation.get("response", {}), "recorded root creation")
    require(receipt.get("readback_verified") is True and receipt.get("ce_unchanged") is True
            and isinstance(receipt.get("refno"), str), "Recorded root creation lacks confirmation")
    ref = receipt["refno"]
    matches = read("root_recovery_query", "e3d_query", {"expression": request["name"], "limit": 2}).get("elements", [])
    require(len(matches) == 1 and matches[0].get("refno") == ref and matches[0].get("type") in {"EQUI", "EQUIPMENT"}
            and matches[0].get("name", "").lstrip("/") == request["name"].lstrip("/"),
            "Recorded root no longer has its unique expected identity; never recreate it")
    parent_identity = receipt.get("parent", {})
    require(parent_identity.get("refno") == request["parent_refno"] and parent_identity.get("type") == "ZONE",
            "Recorded root parent identity lacks the expected ZONE")
    member = direct_member(read, "root_recovery_owner", request["parent_refno"], ref, "ZONE")
    spatial = native_result(read("root_recovery_spatial", "e3d_spatial_info", {"refno": ref, "wrt": "WORLD"}), "root transform")
    check_spatial(spatial, ref, "WORLD", request["position"], request["orientation"], "root")
    attributes = native_result(read("root_recovery_attributes", "e3d_read_attributes",
        {"refno": ref, "attributes": list(request["attributes"])}), "root attributes")
    require(attributes.get("refno") == ref and attributes.get("errors") == {} and attributes.get("typed_read_errors") == {}
            and all(attributes.get("typed_values", {}).get(a) == v for a, v in request["attributes"].items()),
            "Recorded root attributes differ; never recreate it")
    state["root"] = ref
    operation["recovered_state_evidence"] = {"identity": matches[0], "parent_member": member,
                                            "transform": spatial, "attributes": attributes}
    save()
    return True


def panel_geometry_check(actual, item, ref, parent, receipt):
    # Share the offline comparator with the full 117-panel acceptance script.
    path = Path(__file__).with_name("verify-pump-hd002-panels.py")
    spec = importlib.util.spec_from_file_location("pump_hd002_panel_verifier", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require(receipt.get("cleanup_succeeded") is not True and not receipt.get("unverified_created_refno"),
            "Panel creation receipt reports cleanup or an unverified reference; inspect before adoption")
    expected_loop = receipt.get("loop_refno")
    expected_refs = receipt.get("vertex_refnos")
    require(expected_refs is None or isinstance(expected_refs, list), "Invalid recorded PAVE references")
    nodes = receipt.get("creation_journal", [])
    require(isinstance(nodes, list), "Invalid recorded PANE creation tree")
    if nodes:
        pane = nodes[0]
        require(pane.get("type") == "PANE" and pane.get("refno") == ref and pane.get("owner_refno") == parent
                and pane.get("creation_ordinal") == 0, "Recorded PANE creation tree has a different root")
        if len(nodes) > 1:
            loop = nodes[1]
            loop_ref = loop.get("refno")
            require(isinstance(loop_ref, str) and loop.get("type") == "PLOO" and pane.get("children") == [loop_ref]
                    and loop.get("owner_refno") == ref and loop.get("creation_ordinal") == 1
                    and expected_loop in (None, loop_ref), "Recorded PLOO identity or ownership differs")
            expected_loop = loop_ref
            prefix = [node.get("refno") for node in nodes[2:]]
            require(loop.get("children") == prefix and all(node.get("type") == "PAVE" and node.get("owner_refno") == loop_ref
                    and node.get("creation_ordinal") == index and node.get("children") == []
                    for index, node in enumerate(nodes[2:], 2)), "Recorded PAVE identity or order differs")
            require(expected_refs is None or expected_refs[:len(prefix)] == prefix,
                    "Recorded PAVE list disagrees with the creation tree")
            if expected_refs is None:
                expected_refs = prefix
        else:
            require(pane.get("children") == [], "Recorded PANE children are unresolved")
    evidence = module.verify_panel_geometry(actual, item, ref, expected_loop_ref=expected_loop,
        expected_vertex_refs=expected_refs if expected_refs is not None and len(expected_refs) == len(item["vertices"]) else None)
    if expected_refs:
        require(evidence["vertex_refs"][:len(expected_refs)] == expected_refs,
                "Recovered PAVE reference prefix differs from the original receipt")
    return evidence


def reconcile_part(state, key, group_key, item, parent, root, tool, payload, read, save):
    previous = state["operations"].get("part:" + key)
    if not previous or previous.get("status") not in RECOVERABLE:
        return False
    require(previous.get("tool") == tool and previous.get("arguments") == payload,
            "Unconfirmed operation differs from the manifest; never replay: " + key)
    found = read("reconcile_query:" + key, "e3d_query", {"expression": payload["name"], "limit": 2})
    matches = found.get("elements", [])
    state.setdefault("unresolved_new_parts", {})[key] = {"operation_status": previous["status"],
        "query": found, "policy": "Independent proof required; an empty name query never authorizes replay."}
    save()
    require(len(matches) == 1 and matches[0].get("type") == item.get("type", "PANE"),
            "Unconfirmed operation has no unique matching object; inspect without retry: " + key)
    ref = matches[0]["refno"]
    stored_response = previous.get("response") or structured_content(previous.get("mcp_response", {}))
    receipt = stored_response.get("result", {})
    recorded_refs = [receipt[a] for a in ("refno", "residual_refno") if receipt.get(a)]
    require(all(recorded == ref for recorded in recorded_refs), "Recovered identity differs from the original receipt: " + key)
    parent_type = (state["created"][group_key + ":" + item["parent_key"]]["type"] if item.get("parent_key") else "SUBE")
    member = direct_member(read, "reconcile_owner:" + key, parent, ref, parent_type)
    spatial = native_result(read("reconcile_spatial:" + key, "e3d_spatial_info", {"refno": ref, "wrt": root}), "created object transform")
    check_spatial(spatial, ref, root, item["position"], item["orientation"], key)
    evidence = {"refno": ref, "transform": spatial, "parent_member": member}
    if "vertices" in item:
        geometry = native_result(read("reconcile_panel:" + key, "e3d_panel_geometry", {"refno": ref}), "created panel geometry")
        evidence["panel_geometry"] = panel_geometry_check(geometry, {**item, "native_name": payload["name"]}, ref, parent, receipt)
    if "attributes" in item:
        attributes = native_result(read("reconcile_attributes:" + key, "e3d_read_attributes",
            {"refno": ref, "attributes": list(item["attributes"])}), "created object dimensions")
        require(attributes.get("refno") == ref and attributes.get("errors") == {} and attributes.get("typed_read_errors") == {}
                and all(abs(attributes["typed_values"][a]-float(v)) < .01 for a, v in item["attributes"].items()),
                "Unconfirmed object attributes differ from manifest: " + key)
        evidence["attributes"] = attributes
    state["created"][key] = {"refno": ref, "type": item.get("type", "PANE"), "group": group_key, "key": item["key"]}
    previous["recovered_from_status"] = previous["status"]
    previous["status"] = "confirmed_by_independent_readback"
    previous["reconciled_evidence"] = evidence
    state.setdefault("reconciled_operations", []).append({"key": key, "evidence": evidence})
    state["unresolved_new_parts"].pop(key, None)
    save()
    return True


def selection_report(state, selected_keys, active_keys, skipped_keys, full_sources):
    created = set(state.get("created", {}))
    unresolved = set(state.get("unresolved_new_parts", {})) - created
    unresolved.update(key[5:] for key, op in state.get("operations", {}).items()
                      if key.startswith("part:") and op.get("status") in RECOVERABLE)
    missing = active_keys - created
    full = full_sources and not skipped_keys and not unresolved and not missing and not (created - selected_keys)
    state["selection"] = {"selected_parts": len(selected_keys), "active_parts": len(active_keys),
        "active_confirmed_parts": len(active_keys & created), "skipped_keys": sorted(skipped_keys),
        "missing_active_keys": sorted(missing), "unresolved_keys": sorted(unresolved),
        "created_outside_active_keys": sorted(created - active_keys), "full_manifest_scope": full}
    return full


def validate_plan(incremental=False, selected_sources=None, source_paths=None):
    groups = []
    sources = {}
    for path in SOURCES if source_paths is None else source_paths:
        if selected_sources and not any(path.name == f"example-pump-{key}-plan.json" for key in selected_sources):
            continue
        if incremental and not path.exists():
            continue
        raw = path.read_bytes()
        sources[str(path)] = hashlib.sha256(raw).hexdigest()
        plan = json.loads(raw)
        groups.extend(plan["groups"] if "groups" in plan else [plan])
    group_keys = [g["group"] for g in groups]
    require(len(group_keys) == len(set(group_keys)), "Duplicate group")
    for group in groups:
        rows = group.get("elements", []) + group.get("panels", [])
        keys = [row["key"] for row in rows]
        require(len(keys) == len(set(keys)), "Duplicate element key in " + group["group"])
        positive = {row["key"]: row for row in group.get("elements", []) if not row["type"].startswith("N")}
        for row in rows:
            name = f"{NAME_PREFIX}{group['group']}-{row['key']}"
            require(len(name) <= 50 and not re.search(r"\s", name), "Invalid native name " + name)
            position = row["position"]
            ori = row["orientation"]
            x, y = ori["x_axis"], ori["y_axis"]
            require(all(len(v) == 3 and all(type(t) in (int, float) and math.isfinite(t) for t in v)
                        for v in (position, x, y)), "Invalid finite vector " + name)
            require(abs(sum(a*a for a in x)-1) < 1e-6 and abs(sum(a*a for a in y)-1) < 1e-6
                    and abs(sum(a*b for a,b in zip(x,y))) < 1e-6, "Non-orthonormal frame " + name)
            require(-1500 < position[0] < 3000 and abs(position[1]) < 1000 and 0 <= position[2] < 1900,
                    "Part location outside planned equipment envelope " + name)
            for attribute, value in row.get("attributes", {}).items():
                require(isinstance(value, str) and math.isfinite(float(value)), "Invalid dimension " + name)
                require(float(value) >= 0 if attribute in ("DTOP", "DBOT", "RADI") else float(value) > 0,
                        "Invalid dimension sign " + name)
            if row.get("type", "").startswith("N"):
                require(row.get("parent_key") in positive and positive[row["parent_key"]]["type"] in ("BOX", "CYLI", "CONE"),
                        "Negative must belong to a new positive primitive " + name)
            if "vertices" in row:
                vertices = row["vertices"]
                require(3 <= len(vertices) <= 256 and all(len(v) == 2 and all(math.isfinite(t) for t in v) for v in vertices),
                        "Invalid panel vertices " + name)
                area = sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(vertices,vertices[1:]+vertices[:1])) / 2
                require(area > .001 and row["thickness"] > 0, "Panel must be CCW and thick " + name)
    require(len({f"{g['group']}:{row['key']}" for g in groups for row in g.get('elements',[])+g.get('panels',[])})
            == sum(len(g.get('elements',[]))+len(g.get('panels',[])) for g in groups), "Duplicate full key")
    return groups, sources


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--verify-only", action="store_true")
    parser.add_argument("--incremental", action="store_true", help="Build ready manifests now, preserving hashes for later additions")
    parser.add_argument("--sources", help="Comma-separated ready component manifests")
    parser.add_argument("--build-only", action="store_true", help="Create and display ready groups, then leave final independent verification for all groups")
    parser.add_argument("--skip-keys", default="", help="Explicit unresolved new parts to leave untouched while other work continues")
    parser.add_argument("--manifest", type=Path, action="append", help="Explicit input manifest; repeat for each source")
    parser.add_argument("--journal", type=Path, help="Explicit durable journal for live use")
    parser.add_argument("--parent-refno", help="Explicit canonical numeric parent DBREF for live use")
    parser.add_argument("--root-name", help="Explicit name for the new illustrative equipment")
    parser.add_argument("--world-position", type=float, nargs=3, metavar=("X", "Y", "Z"))
    parser.add_argument("--expected-project")
    parser.add_argument("--expected-mdb")
    args = parser.parse_args(argv)
    live = args.execute or args.verify_only
    if live:
        required = ("manifest", "journal", "parent_refno", "root_name", "world_position", "expected_project", "expected_mdb")
        missing = ["--" + name.replace("_", "-") for name in required if getattr(args, name) is None]
        if missing:
            parser.error("Live use requires explicit " + ", ".join(missing))
        match = re.fullmatch(r"=([1-9][0-9]{0,9})/(0|[1-9][0-9]{0,9})", args.parent_refno)
        if match is None or any(int(part) > 2147483647 for part in match.groups()):
            parser.error("--parent-refno must be a canonical numeric Int32 DBREF")
        if re.fullmatch(r"/[A-Za-z][A-Za-z0-9_-]{0,48}", args.root_name) is None:
            parser.error("--root-name must be one explicit ASCII example name")
        if not all(math.isfinite(value) for value in args.world_position):
            parser.error("--world-position must contain three finite coordinates")
        if any(not value.strip() or any(ord(char) < 32 for char in value)
               for value in (args.expected_project, args.expected_mdb)):
            parser.error("--expected-project and --expected-mdb must be explicit nonempty names")
    if not args.manifest:
        print(json.dumps({"mode": "offline_preview", "configured": False, "native_calls": 0,
                          "execution_attempted": False, "required_input": "--manifest"}))
        return 0
    groups, hashes = validate_plan(args.incremental, args.sources.split(',') if args.sources else None, args.manifest)
    selected_keys = {g["group"] + ":" + r["key"] for g in groups for r in g.get("elements", []) + g.get("panels", [])}
    skip_keys = {key for key in args.skip_keys.split(',') if key}
    require(skip_keys <= selected_keys, "Skip keys do not occur in the selected manifests")
    for group in groups:
        for collection in ("elements", "panels"):
            group[collection] = [r for r in group.get(collection, []) if group["group"] + ":" + r["key"] not in skip_keys]
    count = sum(len(g.get("elements", [])) + len(g.get("panels", [])) for g in groups)
    if not args.execute and not args.verify_only:
        print(json.dumps({"mode": "offline_preview", "execution_attempted": False, "native_calls": 0,
                          "validated": True, "groups": len(groups), "parts": count,
                          "selected_parts": len(selected_keys), "skipped_keys": sorted(skip_keys), "source_sha256": hashes}))
        return 0
    with RunnerLock(args.journal):
        run(args, groups, hashes, selected_keys, skip_keys)
    return 0


def run(args, groups, hashes, selected_keys, skip_keys):
    parent_ref = getattr(args, "parent_refno", None) or PARENT
    root_name = getattr(args, "root_name", None) or ROOT_NAME
    world_position = getattr(args, "world_position", None) or WORLD_POSITION
    expected_project = getattr(args, "expected_project", None) or EXPECTED_PROJECT
    expected_mdb = getattr(args, "expected_mdb", None) or EXPECTED_MDB
    report = getattr(args, "journal", None) or REPORT
    active_keys = selected_keys - skip_keys
    count = len(active_keys)
    state = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {
        "schema": 1, "root_name": root_name, "parent": parent_ref, "world_position": world_position,
        "expected_project": expected_project, "expected_mdb": expected_mdb,
        "source_sha256": hashes, "planned_parts": count, "operations": {}, "created": {}, "reads": {},
        "scope": "New illustrative pump only; no deletion, rollback, auto-replay, existing-model edits, or clash check."}
    require(all(state.get(key, value) == value for key, value in {
        "root_name": root_name, "parent": parent_ref, "world_position": world_position,
        "expected_project": expected_project, "expected_mdb": expected_mdb}.items()),
        "Journal target differs from the explicit request; inspect before continuing")
    require(all(path not in hashes or hashes[path] == digest for path, digest in state["source_sha256"].items()),
            "Previously built plan changed; inspect before continuing")
    state["source_sha256"].update(hashes)
    state["planned_parts"] = len(selected_keys)
    state["active_planned_parts"] = count
    full_sources = set(hashes) == {str(path) for path in (getattr(args, "manifest", None) or SOURCES)}
    selection_report(state, selected_keys, active_keys, skip_keys, full_sources)

    def save():
        atomic_save(report, state)

    result = native_result

    with LiveMcp() as client:
        def read(key, tool, arguments=None):
            try:
                value = client.call(tool, arguments or {})
            except Exception as error:
                failure = {"tool": tool, "arguments": deepcopy(arguments or {}), "error": str(error)}
                if hasattr(error, "response"):
                    failure["mcp_response"] = error.response
                if hasattr(error, "structured_content"):
                    failure["response"] = error.structured_content
                state.setdefault("read_failures", {})[key] = failure
                save()
                raise
            state["reads"][key] = value
            save()
            require(value.get("ok") is True, tool + " read failed")
            return value

        def mutate(key, tool, arguments):
            old = state["operations"].get(key)
            if old:
                require(old["tool"] == tool and old["arguments"] == arguments and old["status"] == "confirmed",
                        "Inspect unconfirmed/different operation before resuming: " + key)
                return old["response"]["result"]
            require(args.execute, "Verification does not dispatch writes")
            row = {"status": "dispatched", "tool": tool, "arguments": deepcopy(arguments)}
            state["operations"][key] = row
            save()
            try:
                outer = client.rpc("tools/call", {"name": tool, "arguments": arguments})
                row["mcp_response"] = outer
                save()
                response = structured_content(outer)
                row["response"] = response
                save()
                if outer.get("isError"):
                    raise LiveMcpToolError(tool, outer)
                native = result(response, tool)
                if tool in ("e3d_create_element", "e3d_create_panel"):
                    require(native.get("readback_verified") is True and native.get("ce_unchanged") is True,
                            "Creation did not verify native values and CE")
                row["status"] = "confirmed"
                save()
                return native
            except Exception as error:
                row["status"] = "unconfirmed"
                row["error"] = str(error)
                if hasattr(error, "response"):
                    row["exception_mcp_response"] = error.response
                if hasattr(error, "structured_content"):
                    row["exception_response"] = error.structured_content
                save()
                raise

        health = read("health_before", "e3d_status")
        require(health.get("connected") is True and health["state"].get("database_ready") is True,
                "Native database not ready")
        if "session_id" in state:
            require(state["session_id"] == health["session_id"], "Session changed; inspect journal instead of replaying")
        state["session_id"] = health["session_id"]
        project = read("project_before", "e3d_project_info")["project"]
        require(project["name"] == expected_project and project["mdb"] == expected_mdb, "Wrong project/MDB")
        if "ce_before" in state and state["ce_before"] != project["current_element"]:
            state.setdefault("ce_navigation_observed", []).append({"before": state["ce_before"], "current": project["current_element"],
                "policy": "Explicit numeric targets and frames retained; user navigation is not restored."})
        state["ce_before"] = project["current_element"]
        save()
        root_request = {"parent_refno": parent_ref, "element_type": "EQUI", "name": root_name,
            "position": world_position, "orientation": IDENTITY, "wrt": "WORLD",
            "attributes": {"DESC": "Detailed horizontal centrifugal pump; illustrative dimensions"}}
        if "root" not in state and not recover_confirmed_root(state, root_request, read, save):
            require(not read("name_check", "e3d_query", {"expression": root_name, "limit": 1})["elements"],
                    "New equipment name already exists; not overwriting")
            result(read("root_preview", "e3d_create_element", {**root_request, "dry_run": True}), "root preview")
            native = mutate("root", "e3d_create_element", {**root_request, "dry_run": False})
            state["root"] = native["refno"]
            save()
        root = state["root"]
        if args.execute:
            for group in groups:
                group_key = group["group"]
                native = mutate("group:" + group_key, "e3d_create_element", {
                    "parent_refno": root, "element_type": "SUBE", "name": NAME_PREFIX + group_key,
                    "position": [0,0,0], "orientation": IDENTITY, "wrt": root, "dry_run": False})
                state.setdefault("groups", {})[group_key] = native["refno"]
                save()
                ordered = [r for r in group.get("elements", []) if not r["type"].startswith("N")]
                ordered += group.get("panels", [])
                ordered += [r for r in group.get("elements", []) if r["type"].startswith("N")]
                for part_index, item in enumerate(ordered):
                    key = group_key + ":" + item["key"]
                    parent = (state["created"][group_key + ":" + item["parent_key"]]["refno"]
                              if item.get("parent_key") else native["refno"])
                    if key in state["created"]:
                        continue
                    tool, payload = part_request(group_key, item, parent, root)
                    if reconcile_part(state, key, group_key, item, parent, root, tool, payload, read, save):
                        continue
                    if item.get("type", "").startswith("N") and parent not in state.setdefault("cut_parents", {}):
                        schema = result(read("cut_schema:" + parent, "e3d_element_schema", {"refno": parent, "attributes": ["POS","ORI"]}), "negative schema")
                        require(any(row["short_name"] == item["type"] and row["sdk_creatable_now"] is True
                                    for row in schema["child_types"]), "Native parent does not permit negative")
                        volume = result(read("volume_before:" + parent, "e3d_geometry_volume", {"refno": parent}), "uncut volume")
                        require(volume.get("read_complete") is True and volume.get("volume_pair_coherent") is True,
                                "Uncut volume unavailable")
                        state["cut_parents"][parent] = {"group": group_key, "parent_key": item["parent_key"], "before": volume}
                        save()
                    created = mutate("part:" + key, tool, payload)
                    state["created"][key] = {"refno": created["refno"], "type": item.get("type", "PANE"),
                                             "group": group_key, "key": item["key"]}
                    save()
                    if len(state["created"]) == 1 and "live_display_started" not in state:
                        mutate("display:root", "e3d_set_drawlist_visual", {"refno": root, "add": True,
                            "colour": "BLUE", "visibility": True, "edges": False, "dry_run": False})
                        mutate("view:first", "e3d_set_view", {"preset": "isometric", "fit": "target",
                            "target_refno": root, "padding": 1.25, "dry_run": False})
                        state["live_display_started"] = True
                        save()
                    if part_index == 0 and "refresh:" + group_key not in state["reads"]:
                        preset = {"case": "x_positive", "casebolts": "x_positive", "plate": "x_positive",
                                  "base": "z_negative", "shaft": "y_positive", "nozzles": "isometric"}.get(group_key, "isometric")
                        mutate("panorama:group:" + group_key, "e3d_set_view", {"preset": preset,
                            "fit": "target", "target_refno": root, "padding": 1.25, "dry_run": False})
                print(f"Created {group_key}; {len(active_keys & set(state['created']))}/{count} active parts confirmed.", flush=True)
                if "refresh:" + group_key not in state["reads"]:
                    read("refresh:" + group_key, "e3d_capture_view", {"width": 640, "height": 480})
                    # The native capture refreshes newly created graphics; never replay geometry for display.
                    state["reads"]["refresh:" + group_key]["result"].pop("image_base64", None)
                    save()
                mutate("panorama:finished:" + group_key, "e3d_set_view", {"preset": "keep", "fit": "target",
                    "target_refno": root, "padding": 1.25, "dry_run": False})

        full_scope = selection_report(state, selected_keys, active_keys, skip_keys, full_sources)
        save()
        require(active_keys <= set(state["created"]), "Active part creation incomplete; see selection.missing_active_keys")
        if args.build_only:
            state["phase"] = "ready_groups_created" if full_scope else "partial_ready_groups_created"
            save()
            print(json.dumps({"root": root, "confirmed_parts": count, "phase": state["phase"], "selection": state["selection"]}), flush=True)
            return
        state["phase"] = "independent_verification"
        save()
        validation = read("validation", "e3d_validate_model", {"refno": root, "depth": 8, "limit": 2000,
            "include_snapshot": False, "required_types": ["EQUI", "SUBE", "CYLI", "BOX", "PANE", "NCYL"]})["validation"]
        require(validation.get("valid") is True and validation.get("complete") is True and validation.get("checks_complete") is True,
                "Model dimension or traversal validation is incomplete")
        require(validation.get("issues") == [] and validation.get("check_read_errors") == [], "Model validation has findings")
        verified = 0
        for group in groups:
            for item in group.get("elements", []) + group.get("panels", []):
                key = group["group"] + ":" + item["key"]
                ref = state["created"][key]["refno"]
                sample = result(read("spatial:" + key, "e3d_spatial_info", {"refno": ref, "wrt": root}), "independent transform")
                require(sample.get("complete") is True and close([sample["position"][a] for a in ("x","y","z")], item["position"])
                        and all(close(sample["orientation"][a], item["orientation"][a], 1e-5) for a in ("x_axis", "y_axis")),
                        "Transform mismatch: " + key)
                if "attributes" in item:
                    sample = result(read("dimensions:" + key, "e3d_read_attributes", {"refno": ref, "attributes": list(item["attributes"])}), "independent dimensions")
                    require(sample.get("errors") == {} and sample.get("typed_read_errors") == {}
                            and all(abs(sample["typed_values"][a]-float(v)) < .01 for a,v in item["attributes"].items()),
                            "Dimension mismatch: " + key)
                verified += 1
                if verified % 100 == 0:
                    print(f"Independently checked {verified}/{count} part transforms/dimensions.", flush=True)
        state["independently_verified_parts"] = verified
        for parent, record in state.get("cut_parents", {}).items():
            after = result(read("volume_after:" + parent, "e3d_geometry_volume", {"refno": parent}), "cut readback")
            require(after.get("read_complete") is True and after.get("volume_pair_coherent") is True
                    and after["net"]["value_mm3"] > 0
                    and record["before"]["net"]["value_mm3"] - after["net"]["value_mm3"] > .1,
                    "Negative geometry did not independently reduce its intended positive owner volume: " + record["parent_key"])
            record["after"] = after
            record["removal_verified"] = True
            save()
        state["verified_cut_parents"] = len(state.get("cut_parents", {}))
        project_after = read("project_after", "e3d_project_info")["project"]
        require(project_after["name"] == expected_project and project_after["mdb"] == expected_mdb, "Project/MDB changed")
        health_after = read("health_after", "e3d_status")
        require(health_after["session_id"] == state["session_id"] and health_after["state"]["database_ready"] is True,
                "Native session not healthy after modeling")
        full_scope = selection_report(state, selected_keys, active_keys, skip_keys, full_sources)
        state["phase"] = ("transforms_dimensions_cuts_verified" if full_scope
                          else "partial_transforms_dimensions_cuts_verified")
        state["verification_scope"] = "Active manifest transforms, supported primitive dimensions, and recorded negative-owner volume reductions. Full native PANE outline acceptance is a separate verification."
        state["full_panel_acceptance"] = False
        state["type_counts"] = dict(Counter(v["type"] for v in state["created"].values()))
        save()
        print(json.dumps({k: state[k] for k in ("root", "planned_parts", "active_planned_parts", "independently_verified_parts", "verified_cut_parents", "phase", "type_counts", "selection", "full_panel_acceptance")}))


if __name__ == "__main__":
    main()

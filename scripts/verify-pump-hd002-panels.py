"""Read-only comparison of an explicit 117-panel example manifest and journal.

Default/--offline and --self-test never construct LiveMcp or contact E3D.
Manifest and journal paths are explicit inputs; no workstation model is selected.
Use --live explicitly to collect fresh SDK reads into a NEW artifact directory.
Only the five READ_TOOLS below may be dispatched. No journal, manifest, model,
selection, display, or camera is changed. Geometry correspondence is not a
rendering, Boolean-solid, clash, fabrication, or engineering acceptance result.

Pure reusable API:
    verify_panel_geometry(actual, item, pane_ref, expected_loop_ref=None,
                          expected_vertex_refs=None, *, tolerance=.01)
The actual argument is the INNER e3d_panel_geometry result, not its envelope.
Raises RuntimeError on any mismatch; no I/O or client import occurs in the API.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
NAME_PREFIX = "/EXAMPLE-PUMP-"
READ_TOOLS = frozenset({"e3d_status", "e3d_project_info", "e3d_list_members",
                        "e3d_spatial_info", "e3d_panel_geometry"})
REF = re.compile(r"^=[0-9]+/[0-9]+$")
SCOPE = ("Ordered manifest/native PANE geometry, identity, direct membership, "
         "and placement correspondence only. Live reads are not an immutable "
         "snapshot or a database lock. No rendering, solid/Boolean, clash, "
         "fabrication, or engineering acceptance is established.")


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def numeric(value, label):
    require(type(value) in (int, float) and math.isfinite(value), label + " is not finite numeric")
    return float(value)


def vector(value, size, label):
    require(isinstance(value, (list, tuple)) and len(value) == size, label + " has wrong dimension")
    return [numeric(v, label) for v in value]


def close(actual, expected, tolerance, label):
    a, b = vector(actual, len(expected), label), vector(expected, len(expected), label + " expected")
    error = math.dist(a, b)
    require(error <= tolerance, f"{label} differs by {error:.9g} (tolerance {tolerance})")
    return error


def native_justification(item):
    tokens = {"centre": "CENT", "center": "CENT", "cent": "CENT",
              "positive": "BOTT", "bott": "BOTT", "bottom": "BOTT",
              "negative": "TOP", "top": "TOP"}
    token = str(item.get("justification", "centre")).strip().lower()
    require(token in tokens, "Unsupported manifest justification: " + token)
    return tokens[token]


def identity(value, ref, types, label, name=None):
    require(isinstance(value, dict), label + " identity is missing")
    require(value.get("refno") == ref and bool(REF.fullmatch(ref)), label + " numeric ref mismatch")
    require(value.get("type") in types, label + " type mismatch")
    if name is not None:
        require(str(value.get("name", "")).lstrip("/") == name.lstrip("/"), label + " name mismatch")


def _verify_panel_geometry(actual, item, pane_ref, expected_loop_ref,
                           expected_vertex_refs, tolerance):
    require(isinstance(actual, dict), "Panel geometry result is not an object")
    require(numeric(tolerance, "tolerance") > 0, "Tolerance must be positive")
    points = [vector(p, 2, "manifest vertex") for p in item["vertices"]]
    require(3 <= len(points) <= 256, "Manifest vertex count outside 3..256")
    area = sum(a[0]*b[1]-b[0]*a[1] for a, b in zip(points, points[1:]+points[:1])) / 2
    require(area > 0, "Manifest outline is not counterclockwise")
    thickness = numeric(item["thickness"], "manifest thickness")
    require(thickness > 0, "Manifest thickness must be positive")
    sjus = native_justification(item)
    identity(actual.get("element"), pane_ref, {"PANE", "PANEL"}, "PANE", item.get("native_name"))
    require(actual.get("success") is True and actual.get("complete") is True
            and actual.get("partial") is False, "Panel geometry read is incomplete")
    require(actual.get("read_errors") == {} and actual.get("unsupported_features") == [],
            "Panel read errors or unsupported features")
    require(actual.get("unit") == "mm" and actual.get("coordinate_frame") == pane_ref
            and actual.get("coordinate_source") == "PAVE POS qualified WRT PANE", "Panel coordinate contract mismatch")
    require(actual.get("loop_count") == 1 and isinstance(actual.get("loops"), list)
            and len(actual["loops"]) == 1, "Expected exactly one PLOO")
    require(actual.get("vertex_count") == len(points) and actual.get("topology_valid") is True,
            "Panel vertex count or topology differs")
    require(abs(numeric(actual.get("thickness_mm"), "PANE.THICKN")-thickness) <= tolerance,
            "PANE thickness differs")
    require(str(actual.get("native_justification", "")).strip().upper() == sjus, "PANE SJUS differs")
    # This current bridge's read-only edit-state diagnostic additionally proves
    # leaf vertices, no links/rules, unset CTYA/GTYP, zero beta and native frame.
    require(actual.get("editable") is True and actual.get("edit_reasons") == []
            and re.fullmatch(r"[0-9a-f]{64}", str(actual.get("revision", ""))) is not None,
            "Panel unlinked leaf/frame diagnostic is incomplete or unsupported")
    loop = actual["loops"][0]
    loop_ref = loop.get("element", {}).get("refno")
    require(isinstance(loop_ref, str) and bool(REF.fullmatch(loop_ref)), "PLOO ref is not numeric")
    identity(loop.get("element"), expected_loop_ref or loop_ref, {"PLOO", "PLOOP"}, "PLOO")
    require(loop.get("loop_index") == 0 and loop.get("complete") is True
            and loop.get("polygon_valid") is True, "PLOO index/completeness/topology differs")
    require(loop.get("read_errors") == {} and loop.get("unsupported_features") == [], "PLOO read/features incomplete")
    require(loop.get("blind") is False, "PLOO LBLIND must be false")
    require(abs(numeric(loop.get("thickness_mm"), "PLOO.HEIG")-thickness) <= tolerance, "PLOO thickness differs")
    require(str(loop.get("native_justification", "")).strip().upper() == sjus, "PLOO SJUS differs")
    require(loop.get("planar_parallel_xy") is True and loop.get("coplanar_in_local_xy") is True,
            "PLOO is outside its PANE local XY plane")
    close(loop.get("z_range_mm"), [0, 0], min(tolerance, 1e-6), "PLOO local Z range")
    polygon = loop.get("polygon", {})
    require(polygon.get("valid") is True and polygon.get("winding") == "counterclockwise"
            and polygon.get("issues") == [] and polygon.get("issues_truncated") is False,
            "PLOO polygon validation is incomplete or not CCW")
    native_area = numeric(polygon.get("signed_area_mm2"), "native signed area")
    require(native_area > 0, "Native polygon has nonpositive signed area")
    vertices = loop.get("vertices")
    require(isinstance(vertices, list) and len(vertices) == len(points)
            and loop.get("vertex_count") == len(points), "PLOO vertex count differs")
    refs, maximum = [], 0.0
    for index, (vertex, xy) in enumerate(zip(vertices, points)):
        ref = vertex.get("refno")
        require(isinstance(ref, str) and bool(REF.fullmatch(ref)), f"Vertex {index} ref is not numeric")
        require(ref not in refs and ref not in (pane_ref, loop_ref), f"Duplicate/ancestor vertex ref at {index}")
        refs.append(ref)
        require(vertex.get("vertex_index") == index, f"Vertex index/order differs at {index}")
        maximum = max(maximum, close(vertex.get("position"), [*xy, 0], tolerance, f"Vertex {index} position"))
        require(numeric(vertex.get("fillet_radius_mm"), f"Vertex {index} FRAD") == 0,
                f"Vertex {index} FRAD is not zero")
        for field in ("CTYA", "GTYP"):
            require(isinstance(vertex.get(field), str) and vertex[field].strip().lower() in ("", "unset"),
                    f"Vertex {index} {field} is not explicitly read/unset")
        require(isinstance(vertex.get("BANG"), str) and bool(vertex["BANG"].strip()),
                f"Vertex {index} BANG diagnostic missing")
    if expected_vertex_refs is not None:
        require(isinstance(expected_vertex_refs, (list, tuple)) and refs == list(expected_vertex_refs),
                "Ordered vertex identities differ from the creation/repair receipt")
    return {"pane_ref": pane_ref, "loop_ref": loop_ref, "vertex_refs": refs,
            "vertex_count": len(points), "thickness_mm": thickness,
            "native_justification": sjus, "max_vertex_error_mm": maximum,
            "signed_area_mm2": native_area}


def verify_panel_geometry(actual, item, pane_ref, expected_loop_ref=None,
                          expected_vertex_refs=None, *, tolerance=.01):
    """Pure comparison of one inner native result; raises RuntimeError on failure."""
    try:
        return _verify_panel_geometry(actual, item, pane_ref, expected_loop_ref, expected_vertex_refs, tolerance)
    except (KeyError, TypeError, ValueError, IndexError, OverflowError, AttributeError) as error:
        raise RuntimeError("Malformed panel geometry/manifest contract: " + str(error)) from error


def read_json(path):
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def receipt_for(key, item, build, repairs):
    record = build["created"][key]
    pane_ref = record["refno"]
    require(record.get("group") == key.split(":", 1)[0] and record.get("key") == item["key"], "Created key metadata differs: " + key)
    recovered = build.get("recovered_parts", {}).get(key)
    if recovered:
        path = Path(recovered)
        report, digest = read_json(path)
        require(report.get("passed") is True and not report.get("error") and report.get("pane") == pane_ref,
                "Repair receipt is not successful/exact: " + key)
        loop_ref = report["loop"]
        operations = report["operations"]
        require(len(operations) == len(item["vertices"]), "Repair operation count differs: " + key)
        refs = []
        for index, (operation, xy) in enumerate(zip(operations, item["vertices"])):
            result = operation["response"]["result"]
            require(operation.get("index") == index and operation.get("status") == "confirmed"
                    and operation["response"].get("ok") is True and result.get("success") is True
                    and result.get("readback_verified") is True and result.get("parent_membership_verified") is True,
                    "Unconfirmed repair vertex: " + key)
            args = operation["arguments"]
            require(args.get("parent_refno") == loop_ref and args.get("wrt") == pane_ref
                    and args.get("element_type") == "PAVE" and args.get("dry_run") is False,
                    "Repair vertex frame/parent differs: " + key)
            close(args["position"], [*xy, 0], 1e-8, "Repair submitted vertex")
            identity(result.get("parent"), loop_ref, {"PLOO", "PLOOP"}, "Repair vertex parent")
            identity(result.get("frame"), pane_ref, {"PANE", "PANEL"}, "Repair vertex frame")
            refs.append(result["refno"])
        geometry = report["after"]
        repairs[str(path.resolve())] = digest
    else:
        operation = build["operations"].get("part:" + key)
        require(isinstance(operation, dict) and operation.get("status") == "confirmed"
                and operation.get("tool") == "e3d_create_panel", "Confirmed panel creation receipt missing: " + key)
        envelope = operation["response"]
        result = envelope["result"]
        require(envelope.get("ok") is True and result.get("success") is True
                and result.get("refno") == pane_ref and result.get("readback_verified") is True
                and result.get("parent_membership_verified") is True, "Panel creation was not verified: " + key)
        args = operation["arguments"]
        require(args.get("parent_refno") == build["groups"][record["group"]]
                and args.get("wrt") == build["root"] and args.get("dry_run") is False,
                "Creation parent/root frame differs: " + key)
        require(args.get("vertices") == item["vertices"] and args.get("thickness") == item["thickness"]
                and native_justification(args) == native_justification(item), "Creation outline request differs: " + key)
        loop_ref, refs, geometry = result["loop_refno"], result["vertex_refnos"], result["geometry"]
    proof = verify_panel_geometry(geometry, item, pane_ref, loop_ref, refs)
    return {"loop_ref": proof["loop_ref"], "vertex_refs": proof["vertex_refs"], "geometry": geometry}


def load_inputs(journal, source_paths):
    build, journal_hash = read_json(journal)
    require(bool(REF.fullmatch(build.get("root", ""))) and build.get("session_id"), "Ledger root/session is missing")
    panels, sources, repairs = [], {}, {}
    for path in source_paths:
        manifest, digest = read_json(path)
        require(build["source_sha256"].get(str(path)) == digest, "Manifest differs from build ledger: " + str(path))
        sources[str(path)] = digest
        for group in manifest.get("groups", [manifest]):
            group_key = group["group"]
            for raw in group.get("panels", []):
                item = deepcopy(raw)
                key = group_key + ":" + item["key"]
                item["native_name"] = NAME_PREFIX + group_key + "-" + item["key"]
                require(key in build["created"], "Manifest panel not in created ledger: " + key)
                ref = build["created"][key]["refno"]
                require(bool(REF.fullmatch(ref)), "Created PANE ref is not numeric: " + key)
                receipt = receipt_for(key, item, build, repairs)
                panels.append({"key": key, "group": group_key, "pane_ref": ref,
                               "item": item, "receipt": receipt})
    keys = [p["key"] for p in panels]
    refs = [p["pane_ref"] for p in panels]
    recorded = {k for k, v in build["created"].items() if v.get("type") in ("PANE", "PANEL")}
    require(len(panels) == 117 and len(set(keys)) == 117 and len(set(refs)) == 117,
            "Expected exactly 117 unique manifest PANE keys/refs")
    require(set(keys) == recorded, "Manifest/created PANE coverage differs")
    return build, panels, {"journal_path": str(journal), "journal_sha256": journal_hash,
                           "source_sha256": sources, "repair_sha256": repairs}


class ReadFailure(RuntimeError):
    """A failed/unconfirmed read ends this run; no recovery or retry is attempted."""


class Reader:
    def __init__(self, client):
        self.client, self.calls = client, []

    def read(self, tool, arguments=None):
        require(tool in READ_TOOLS, "Refusing non-allowlisted tool: " + tool)
        row = {"tool": tool, "arguments": deepcopy(arguments or {})}
        self.calls.append(row)
        try:
            value = self.client.call(tool, arguments or {})
            row["response"] = value
            if not isinstance(value, dict) or value.get("ok") is not True:
                raise ReadFailure("Read envelope not successful: " + tool)
            return value
        except Exception as error:
            row["error"] = str(error)
            # LiveMcpToolError deliberately retains the full MCP receipt; its
            # string is only a human-sized summary and is not evidence.
            full_response = getattr(error, "response", None)
            if full_response is not None:
                row["mcp_error_response"] = full_response
            structured = getattr(error, "structured_content", None)
            if structured is not None:
                row["structured_error_response"] = structured
            raise ReadFailure(tool + ": " + str(error)) from error

    def result(self, tool, arguments):
        envelope = self.read(tool, arguments)
        result = envelope.get("result")
        if not isinstance(result, dict) or result.get("success") is not True:
            raise ReadFailure(tool + " native result is unsuccessful/incomplete")
        return result

    def members(self, ref, expected_type):
        rows, cursor, cursors = [], None, set()
        for _ in range(50):
            args = {"refno": ref, "expected_type": expected_type, "limit": 200}
            if cursor:
                args["cursor"] = cursor
            page = self.result("e3d_list_members", args)
            identity(page.get("parent"), ref, {expected_type}, "Direct-members parent")
            require(page.get("read_errors") == [] and type(page.get("has_more")) is bool,
                    "Incomplete direct-members page: " + ref)
            members = page.get("members")
            require(isinstance(members, list) and page.get("returned_count") == len(members), "Member count contract differs")
            require([r.get("sibling_index") for r in members] == list(range(len(rows)+1, len(rows)+len(members)+1)),
                    "Member sibling order differs")
            for member in members:
                require(bool(REF.fullmatch(str(member.get("refno", "")))) and isinstance(member.get("type"), str),
                        "Member identity is incomplete")
            rows.extend(members)
            require(len({r["refno"] for r in rows}) == len(rows), "Duplicate direct member ref")
            if not page["has_more"]:
                require(page.get("next_cursor") is None, "Unexpected terminal member cursor")
                return rows
            cursor = page.get("next_cursor")
            require(isinstance(cursor, str) and cursor and cursor not in cursors, "Non-advancing member cursor")
            cursors.add(cursor)
        raise RuntimeError("Member pagination exceeded bounded 50 pages")


def verify_spatial(actual, item, pane_ref, root):
    require(actual.get("refno") == pane_ref and actual.get("type") == "PANE"
            and actual.get("success") is True and actual.get("complete") is True
            and actual.get("position_complete") is True and actual.get("orientation_complete") is True
            and actual.get("errors") == {}, "Spatial result identity/completeness differs")
    frame = actual.get("frame", {})
    require(frame.get("requested") == root and frame.get("refno") == root and frame.get("type") == "EQUI",
            "Spatial WRT root differs")
    p, ori = actual["position"], actual["orientation"]
    require(p.get("unit") == "mm" and p.get("available") is True and ori.get("available") is True,
            "Spatial coordinate availability differs")
    close([p[a] for a in "xyz"], item["position"], .01, "PANE position WRT root")
    for axis in ("x_axis", "y_axis"):
        close(ori[axis], item["orientation"][axis], 1e-6, "PANE " + axis)
    x, y = item["orientation"]["x_axis"], item["orientation"]["y_axis"]
    z = [x[1]*y[2]-x[2]*y[1], x[2]*y[0]-x[0]*y[2], x[0]*y[1]-x[1]*y[0]]
    close(ori["z_axis"], z, 1e-6, "PANE z_axis")


def session_guard(reader, build, expected_project, expected_mdb):
    health = reader.read("e3d_status")
    state = health.get("state", {})
    require(health.get("connected") is True and health.get("session_id") == build["session_id"]
            and state.get("database_ready") is True and state.get("busy") is False
            and state.get("recovery_required") is False
            and state.get("native_database_fault", {}).get("has_fault") is False,
            "Native session/database is not the build session or not ready")
    project = reader.read("e3d_project_info")["project"]
    require(project.get("connected") is True and project.get("name") == expected_project and project.get("mdb") == expected_mdb,
            "Wrong/unavailable project or MDB")
    return {"session_id": health["session_id"], "bridge_build": health.get("bridge_build"),
            "project": project["name"], "mdb": project["mdb"], "current_element": project.get("current_element")}


def owner_guard(reader, build, panels, involved_groups):
    root_members = reader.members(build["root"], "EQUI")
    for group in involved_groups:
        group_ref = build["groups"][group]
        matches = [row for row in root_members if row["refno"] == group_ref]
        require(len(matches) == 1, "Group is not an exact direct child of root: " + group)
        identity(matches[0], group_ref, {"SUBE"}, "Root group", NAME_PREFIX + group)
        members = reader.members(group_ref, "SUBE")
        expected = {p["pane_ref"]: p for p in panels if p["group"] == group}
        actual = {r["refno"]: r for r in members if r["type"] == "PANE"}
        require(set(actual) == set(expected), "Native/manifest PANE coverage differs in group " + group)
        for ref, panel in expected.items():
            identity(actual[ref], ref, {"PANE"}, "Group PANE", panel["item"]["native_name"])


def atomic_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def run_live(build, panels, provenance, artifact_dir, batch_size, expected_project, expected_mdb):
    # Import deliberately lives here: offline/helper use has no client startup.
    from live_mcp import LiveMcp

    artifact_dir.mkdir(parents=True, exist_ok=False)
    summary = {"schema": 1, "scope": SCOPE, "native_writes": False, "mode": "live_read_only",
               "created_utc": datetime.now(timezone.utc).isoformat(), "expected_panels": 117,
               "session_id": build["session_id"], "root": build["root"], **provenance,
               "allowed_tools": sorted(READ_TOOLS), "batches": [], "checked": 0,
               "passed_panels": 0, "failed_panels": 0, "complete": False, "passed": False}
    summary_path = artifact_dir / "summary.json"
    atomic_json(summary_path, summary)
    reader, current_results, batch_no, processed = None, [], 0, 0

    def checkpoint(error=None):
        nonlocal current_results, batch_no
        calls = reader.calls if reader else []
        if not calls and not current_results and error is None:
            return
        batch_no += 1
        path = artifact_dir / f"batch-{batch_no:04d}.json"
        content = {"schema": 1, "scope": SCOPE, "batch": batch_no, "results": current_results,
                   "calls": calls, "aborted_error": error}
        # Batch files are immutable once created; only the small summary moves.
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(content, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        summary["batches"].append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                   "panels": len(current_results), "read_calls": len(calls),
                                   "aborted": error is not None})
        summary["checked"] += len(current_results)
        summary["passed_panels"] += sum(r["passed"] for r in current_results)
        summary["failed_panels"] += sum(not r["passed"] for r in current_results)
        atomic_json(summary_path, summary)
        current_results = []
        if reader:
            reader.calls = []

    try:
        with LiveMcp() as client:
            reader = Reader(client)
            first_context = None
            for start in range(0, len(panels), batch_size):
                chunk = panels[start:start+batch_size]
                context = session_guard(reader, build, expected_project, expected_mdb)
                if first_context is None:
                    first_context = context
                require(context["bridge_build"] == first_context["bridge_build"], "Bridge build changed during verification")
                involved = sorted({p["group"] for p in chunk})
                owner_guard(reader, build, panels, involved)
                for panel in chunk:
                    key, ref, item = panel["key"], panel["pane_ref"], panel["item"]
                    receipt = panel["receipt"]
                    result = {"key": key, "pane_ref": ref, "expected_owner": build["groups"][panel["group"]], "passed": False}
                    # Captured separately, with explicit references on every call.
                    spatial_before = reader.result("e3d_spatial_info", {"refno": ref, "wrt": build["root"]})
                    pane_members = reader.members(ref, "PANE")
                    loop_members = reader.members(receipt["loop_ref"], "PLOO")
                    geometry = reader.result("e3d_panel_geometry", {"refno": ref})
                    spatial_after = reader.result("e3d_spatial_info", {"refno": ref, "wrt": build["root"]})
                    try:
                        require(len(pane_members) == 1, "PANE has additional or missing direct members")
                        identity(pane_members[0], receipt["loop_ref"], {"PLOO"}, "Direct PLOO")
                        require([r["refno"] for r in loop_members] == receipt["vertex_refs"]
                                and all(r["type"] == "PAVE" for r in loop_members),
                                "Direct PLOO/PAVE identity order differs from receipt")
                        verify_spatial(spatial_before, item, ref, build["root"])
                        verify_spatial(spatial_after, item, ref, build["root"])
                        result["evidence"] = verify_panel_geometry(geometry, item, ref, receipt["loop_ref"], receipt["vertex_refs"])
                        result["passed"] = True
                    except (RuntimeError, KeyError, TypeError, ValueError) as error:
                        result["error"] = str(error)
                    current_results.append(result)
                    processed += 1
                owner_guard(reader, build, panels, involved)
                after_context = session_guard(reader, build, expected_project, expected_mdb)
                require(after_context["bridge_build"] == first_context["bridge_build"], "Bridge build changed within batch")
                checkpoint()
                print(json.dumps({"checked": processed, "expected": 117, "failed": summary["failed_panels"],
                                  "checkpoint": str(summary_path)}, ensure_ascii=False), flush=True)
            for path, digest in provenance["source_sha256"].items():
                require(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest, "Manifest changed during verification")
            summary["complete"] = summary["checked"] == 117
            summary["passed"] = summary["complete"] and summary["failed_panels"] == 0
            summary["finished_utc"] = datetime.now(timezone.utc).isoformat()
            atomic_json(summary_path, summary)
    except Exception as error:
        summary["aborted_error"] = str(error)
        summary["finished_utc"] = datetime.now(timezone.utc).isoformat()
        checkpoint(str(error))
        atomic_json(summary_path, summary)
        print(json.dumps({"passed": False, "error": str(error), "artifact": str(summary_path)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({"passed": summary["passed"], "complete": summary["complete"], "checked": summary["checked"],
                      "failed_panels": summary["failed_panels"], "artifact": str(summary_path), "scope": SCOPE}, ensure_ascii=False))
    return 0 if summary["passed"] else 1


def self_test(panels):
    # Every stored successful native creation geometry (plus recovered rearcast)
    # is checked, then contract fields are deliberately corrupted in memory.
    for panel in panels:
        receipt = panel["receipt"]
        verify_panel_geometry(receipt["geometry"], panel["item"], panel["pane_ref"], receipt["loop_ref"], receipt["vertex_refs"])
    p = panels[0]
    mutations = [
        (["success"], False), (["complete"], False), (["partial"], True), (["unit"], "m"),
        (["coordinate_frame"], "=1/2"), (["coordinate_source"], "WORLD"),
        (["element", "refno"], "=1/2"), (["element", "type"], "BOX"),
        (["loop_count"], 2), (["vertex_count"], 0), (["topology_valid"], False),
        (["read_errors"], {"POS": "failed"}), (["unsupported_features"], [{"code": "unknown"}]),
        (["thickness_mm"], -1), (["native_justification"], "BOTT"),
        (["editable"], False), (["revision"], None), (["edit_reasons"], ["linked"]),
        (["loops", 0, "element", "refno"], "=1/2"), (["loops", 0, "loop_index"], 1),
        (["loops", 0, "complete"], False), (["loops", 0, "polygon_valid"], False),
        (["loops", 0, "blind"], True), (["loops", 0, "thickness_mm"], 2),
        (["loops", 0, "native_justification"], "BOTT"), (["loops", 0, "read_errors"], {"FRAD": "failed"}),
        (["loops", 0, "unsupported_features"], [{"code": "arc"}]),
        (["loops", 0, "planar_parallel_xy"], False), (["loops", 0, "coplanar_in_local_xy"], False),
        (["loops", 0, "z_range_mm"], [2, 2]), (["loops", 0, "polygon", "winding"], "clockwise"),
        (["loops", 0, "polygon", "issues_truncated"], True),
        (["loops", 0, "vertices", 0, "vertex_index"], 1),
        (["loops", 0, "vertices", 0, "position"], [0, 0, 9]),
        (["loops", 0, "vertices", 0, "fillet_radius_mm"], 1),
        (["loops", 0, "vertices", 0, "CTYA"], "CONNECTED"),
        (["loops", 0, "vertices", 0, "refno"], "not-numeric"),
        (["loops", 0, "vertices", 1, "refno"], p["receipt"]["vertex_refs"][0]),
    ]
    for path, value in mutations:
        geometry = deepcopy(p["receipt"]["geometry"])
        target = geometry
        for component in path[:-1]:
            target = target[component]
        target[path[-1]] = value
        try:
            verify_panel_geometry(geometry, p["item"], p["pane_ref"], p["receipt"]["loop_ref"], p["receipt"]["vertex_refs"])
        except RuntimeError:
            continue
        raise RuntimeError("Self-test failed to reject mutation: " + repr(path))
    try:
        verify_panel_geometry(p["receipt"]["geometry"], p["item"], p["pane_ref"],
                              p["receipt"]["loop_ref"], list(reversed(p["receipt"]["vertex_refs"])))
    except RuntimeError:
        pass
    else:
        raise RuntimeError("Self-test accepted reversed identity order")
    # The hard dispatcher allowlist is tested without constructing a client.
    try:
        Reader(None).read("e3d_create_panel", {})
    except RuntimeError:
        pass
    else:
        raise RuntimeError("Self-test accepted a native write")
    class StoredToolError(RuntimeError):
        def __init__(self):
            super().__init__("short summary")
            self.response = {"isError": True, "content": [{"type": "text", "text": "x" * 12000}]}
            self.structured_content = {"ok": False, "error": "full structured failure"}

    class FailingClient:
        def call(self, *_args):
            raise StoredToolError()

    failed_reader = Reader(FailingClient())
    try:
        failed_reader.read("e3d_panel_geometry", {"refno": p["pane_ref"]})
    except ReadFailure:
        require(len(failed_reader.calls[0]["mcp_error_response"]["content"][0]["text"]) == 12000
                and failed_reader.calls[0]["structured_error_response"]["error"] == "full structured failure",
                "Self-test discarded/truncated a full MCP error response")
    else:
        raise RuntimeError("Self-test accepted a failed native read")
    return {"stored_native_receipts_checked": len(panels), "rejected_mutations": len(mutations)+1,
            "native_write_allowlist_guard": True, "full_mcp_error_preserved": True,
            "native_calls": 0, "artifact_writes": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="Collect fresh read-only E3D results; no native writes")
    mode.add_argument("--offline", action="store_true", help="Validate local inputs/receipts only (default)")
    mode.add_argument("--self-test", action="store_true", help="Offline real-receipt and corruption tests; no files written")
    parser.add_argument("--journal", type=Path, help="Explicit example build journal")
    parser.add_argument("--manifest", type=Path, action="append", help="Explicit manifest; repeat for every source")
    parser.add_argument("--expected-project", help="Explicit expected project for --live")
    parser.add_argument("--expected-mdb", help="Explicit expected MDB for --live")
    parser.add_argument("--artifact-dir", type=Path, help="NEW live receipt directory; never the build journal")
    parser.add_argument("--batch-size", type=int, default=12, help="Panels per immutable checkpoint, 1..50 (default 12)")
    args = parser.parse_args(argv)
    require(1 <= args.batch_size <= 50, "Batch size must be 1..50")
    require(args.live or args.artifact_dir is None, "--artifact-dir is used only with --live")
    if args.live and (not args.expected_project or not args.expected_mdb):
        parser.error("--live requires explicit --expected-project and --expected-mdb")
    if not args.journal or not args.manifest:
        if args.live or args.offline or args.self_test:
            parser.error("Explicit --journal and --manifest inputs are required")
        print(json.dumps({"mode": "offline_preview", "configured": False, "native_calls": 0,
                          "artifact_writes": 0, "scope": SCOPE,
                          "required_inputs": ["--journal", "--manifest"]}, ensure_ascii=False))
        return 0
    build, panels, provenance = load_inputs(args.journal, args.manifest)
    if args.self_test:
        print(json.dumps({"passed": True, "mode": "offline_self_test", **self_test(panels)}, ensure_ascii=False))
        return 0
    if not args.live:
        print(json.dumps({"passed": True, "mode": "offline", "panels": len(panels), "native_calls": 0,
                          "artifact_writes": 0, "scope": SCOPE, **provenance}, ensure_ascii=False))
        return 0
    output = args.artifact_dir or ROOT / "artifacts" / (
        "example-pump-panels-verify-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6])
    return run_live(build, panels, provenance, output.resolve(), args.batch_size,
                    args.expected_project, args.expected_mdb)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(json.dumps({"passed": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)

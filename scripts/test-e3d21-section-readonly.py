"""Preview an explicit section request, or collect read-only evidence with --live.

The geometric comparisons do not establish model-edit permission, fabrication
acceptance, or engineering validity. The default only reads the request file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from live_mcp import LiveMcp


ROOT = Path(__file__).resolve().parents[1]
TOOL = "e3d_section_element_drawing"


def ready(status):
    state = status.get("state", {})
    assert status.get("ok") is True and status.get("connected") is True
    assert state.get("database_ready") is True and state.get("busy") is False
    assert state.get("recovery_required") is False and state.get("native_database_fault", {}).get("has_fault") is False
    for key, flag in (("native_dialogs", "standard_dialog_present"), ("pml_alerts", "alert_present")):
        assert state.get(key, {}).get("complete") is True and state[key].get(flag) is False


def polygon_metrics(points):
    anchor = points[0]
    shifted = [[p[i] - anchor[i] for i in (0, 1)] for p in points]
    area = abs(math.fsum(a[0] * b[1] - a[1] * b[0] for a, b in zip(shifted, shifted[1:]))) / 2
    length = math.fsum(math.dist(a, b) for a, b in zip(points, points[1:]))
    return area, length


def cad_contours(lines):
    """Recover closed curves from actual CAD endpoints, without source indices."""
    adjacency = {}
    outgoing, incoming = {}, {}
    seen_edges = set()
    for line in lines:
        a, b = tuple(line.dxf.start), tuple(line.dxf.end)
        assert a[2] == 0 and b[2] == 0 and a != b
        edge = tuple(sorted((a, b)))
        assert edge not in seen_edges, "Duplicate CAD edge"
        seen_edges.add(edge)
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
        assert a not in outgoing and b not in incoming, "CAD contour has inconsistent edge directions"
        outgoing[a], incoming[b] = b, a
    assert all(len(neighbors) == 2 for neighbors in adjacency.values()), "CAD contour is not degree two"
    assert set(outgoing) == set(incoming) == set(adjacency)
    unseen = set(adjacency)
    loops = []
    for start in sorted(adjacency):
        if start not in unseen:
            continue
        current = start
        points = []
        while True:
            assert current in unseen, "CAD contour branches or crosses a visited contour node"
            unseen.remove(current)
            points.append(current[:2])
            current = outgoing[current]
            if current == start:
                break
        assert len(points) >= 3
        loops.append([*points, points[0]])
    assert not unseen
    return loops


def cad_semantics(document):
    """Snapshot the exported entity and layer semantics for a real roundtrip."""
    entities = []
    for entity in document.modelspace():
        if entity.dxftype() == "LINE":
            entities.append(("LINE", entity.dxf.layer, tuple(entity.dxf.start), tuple(entity.dxf.end), entity.dxf.linetype))
        else:
            assert entity.dxftype() == "TEXT"
            entities.append(("TEXT", entity.dxf.layer, entity.dxf.text, tuple(entity.dxf.insert),
                             tuple(entity.dxf.align_point), entity.dxf.height, entity.dxf.rotation,
                             entity.dxf.halign, entity.dxf.valign, entity.dxf.style))
    return {"entities": entities, "layers": {layer.dxf.name: (layer.dxf.color, layer.dxf.linetype)
                                            for layer in document.layers},
            "units": document.units, "version": document.dxfversion}


def winding_at(point, loops, tolerance=1e-5):
    """Independent CAD-space nonzero check; boundary locations are admitted."""
    x, y = point
    winding = 0
    for loop in loops:
        for a, b in zip(loop, loop[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy)
            cross = dx * (y - a[1]) - dy * (x - a[0])
            if (abs(cross) <= tolerance * length and
                    min(a[0], b[0]) - tolerance <= x <= max(a[0], b[0]) + tolerance and
                    min(a[1], b[1]) - tolerance <= y <= max(a[1], b[1]) + tolerance):
                return True
            if a[1] <= y < b[1] and cross > 0:
                winding += 1
            elif b[1] <= y < a[1] and cross < 0:
                winding -= 1
    return winding != 0


def verify_cad_hatch_segment(a, b, loops):
    """Check each boundary-separated interval of an actual CAD hatch line."""
    vector = (b[0] - a[0], b[1] - a[1])
    length_squared = math.fsum(value * value for value in vector)
    assert length_squared > 0
    parameters = {0.0, 1.0}

    def cross(left, right):
        return left[0] * right[1] - left[1] * right[0]

    for loop in loops:
        for c, d in zip(loop, loop[1:]):
            edge = (d[0] - c[0], d[1] - c[1])
            relative = (c[0] - a[0], c[1] - a[1])
            denominator = cross(vector, edge)
            if denominator == 0:
                if cross(relative, vector) == 0:
                    for point in (c, d):
                        t = math.fsum((point[i] - a[i]) * vector[i] for i in (0, 1)) / length_squared
                        if 0 < t < 1:
                            parameters.add(t)
            else:
                t, u = cross(relative, edge) / denominator, cross(relative, vector) / denominator
                if 0 < t < 1 and 0 <= u <= 1:
                    parameters.add(t)
    ordered = sorted(parameters)
    assert winding_at(a, loops) and winding_at(b, loops)
    for left, right in zip(ordered, ordered[1:]):
        t = (left + right) / 2
        assert winding_at((a[0] + vector[0] * t, a[1] + vector[1] * t), loops), "CAD hatch crosses an unfilled interval"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, help="New evidence directory required with --live")
    parser.add_argument("--live", action="store_true", help="Explicitly collect read-only MCP evidence")
    parser.add_argument("--tool", choices=(TOOL, "e3d_section_assembly_drawing"), default=TOOL)
    parser.add_argument("--verify-panel-midplane", action="store_true",
                        help="Compare the section with native PANE polygon metrics when the plane is its extrusion midplane")
    args = parser.parse_args(argv)
    tool_name = args.tool
    if args.verify_panel_midplane and tool_name != TOOL:
        parser.error("--verify-panel-midplane applies only to single-element section requests")
    if args.live and args.output_dir is None:
        parser.error("--live requires an explicit --output-dir")
    request = json.loads(args.request_json.read_text(encoding="utf-8"))
    assert request["include_svg"] is True and request["include_dxf"] is True
    if not args.live:
        print(json.dumps({"mode": "offline_preview", "tool": tool_name, "request": request,
                          "native_calls": 0, "artifact_writes": 0, "execution_attempted": False}, ensure_ascii=False))
        return 0
    args.output_dir.mkdir(parents=True, exist_ok=False)
    receipt_path = args.output_dir / "receipt.json"
    receipt = {"utc": datetime.now(timezone.utc).isoformat(), "passed": False, "calls": [],
               "model_writes": 0, "view_writes": 0, "automatic_restarts": 0,
               "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
                   "src/aveva_e3d_mcp/tools.py", "src/aveva_e3d_mcp/mesh_section.py",
                   "src/aveva_e3d_mcp/section_assembly.py", "src/aveva_e3d_mcp/drawing_assembly.py",
                   "src/aveva_e3d_mcp/section_hatching.py",
                   "src/aveva_e3d_mcp/section_hatch_precision.py",
                   "src/aveva_e3d_mcp/orthographic_drawing.py", "src/aveva_e3d_mcp/orthographic_png.py",
                   "src/aveva_e3d_mcp/orthographic_dxf.py", "src/aveva_e3d_mcp/drawing_sheet.py",
                   "src/aveva_e3d_mcp/drawing_sheet_render.py", "scripts/test-e3d21-section-readonly.py")}}

    def save():
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")

    save()
    try:
        with LiveMcp() as client:
            def call(name, parameters=None, image_path=None):
                assert name in {"e3d_status", "e3d_project_info", tool_name}
                row = {"name": name, "arguments": parameters or {}, "state": "dispatched"}
                receipt["calls"].append(row)
                save()
                response = client.call(name, parameters, image_path=image_path)
                row.update(state="returned", response=response)
                save()
                return response

            catalog = client.rpc("tools/list", {})
            tool = next(row for row in catalog["tools"] if row["name"] == tool_name)
            assert tool["annotations"]["readOnlyHint"] is True
            receipt.update(catalog=tool, tool_count=len(catalog["tools"]))
            before = call("e3d_status")
            ready(before)
            project_before = call("e3d_project_info")
            png_path = args.output_dir / "section.png"
            response = call(tool_name, request, image_path=png_path)
            result = response["result"]
            assert all(result[key] is True for key in ("success", "available", "complete"))
            section, drawing = result["section"], result["drawing"]
            if "hatch" in request:
                assert all(result["hatching"][flag] is True for flag in ("success", "available", "complete"))
                assert drawing["hatching_applied"] is True
            else:
                assert "hatching" not in result and drawing["hatching_applied"] is False
            assert section["intersection"] is True
            assert section["counts"]["loops"] > 0
            assert [v["name"] for v in drawing["views"]] == ["SECTION"]
            covered_segments = []
            for loop in section["loops"]:
                assert loop["vertices"][0] == loop["vertices"][-1]
                assert len(loop["segment_indices"]) == len(loop["vertices"]) - 1
                for index, segment_index in enumerate(loop["segment_indices"]):
                    assert section["segments"][segment_index]["vertices"] == loop["vertices"][index:index + 2]
                    covered_segments.append(segment_index)
            assert sorted(covered_segments) == list(range(len(section["segments"])))
            assert all(segment["source_triangles"] for segment in section["segments"])
            if tool_name == "e3d_section_assembly_drawing":
                assert result["context"]["boundary_agreement"] is True
                assert result["source_mesh"]["boolean_union_applied"] is False
                assert result["source_mesh"]["vertices_welded"] is False
                sources = result["sources"]
                assert len(sources) == len(request["targets"]) == len(section["source_sections"])
                segment_members, loop_members = [], []
                for index, (source, membership) in enumerate(zip(sources, section["source_sections"])):
                    assert all(source[flag] is True for flag in ("success", "available", "complete"))
                    assert source["requested_refno"] == request["targets"][index]["refno"]
                    assert source["resolved_refno"] == membership["refno"]
                    assert source["mesh_range"] == result["source_mesh"]["source_ranges"][index]
                    low, high = source["mesh_range"]["triangle_range"]
                    assert membership["source_index"] == index
                    assert membership["intersection"] is bool(membership["segment_indices"])
                    segment_members.extend(membership["segment_indices"])
                    loop_members.extend(membership["loop_indices"])
                    for segment_index in membership["segment_indices"]:
                        segment = section["segments"][segment_index]
                        assert segment["source_index"] == index and segment["refno"] == source["resolved_refno"]
                        assert all(low <= triangle < high for triangle in segment["source_triangles"])
                        assert segment["source_triangle_indices"] == [triangle-low for triangle in segment["source_triangles"]]
                    for loop_index in membership["loop_indices"]:
                        assert section["loops"][loop_index]["source_index"] == index
                        assert section["loops"][loop_index]["refno"] == source["resolved_refno"]
                assert sorted(segment_members) == list(range(len(section["segments"])))
                assert sorted(loop_members) == list(range(len(section["loops"])))
                for line, segment in zip(drawing["views"][0]["lines"], section["segments"]):
                    assert all(line[key] == segment[key] for key in (
                        "source_index", "refno", "source_triangles", "source_triangle_indices"))
                receipt["assembly_source_mapping"] = {"passed": True, "sources": len(sources),
                    "memberships": section["source_sections"], "global_and_local_triangle_indices_verified": True}
            png = png_path.read_bytes()
            assert png.startswith(b"\x89PNG\r\n\x1a\n")
            assert struct.unpack(">II", png[16:24]) == (request.get("preview_width", 1200), request.get("preview_height", 440))
            dxf_path = args.output_dir / "section.dxf"
            dxf_path.write_text(drawing["dxf"], encoding="ascii", newline="\n")
            (args.output_dir / "section.svg").write_text(drawing["svg"], encoding="utf-8")
            expected = None
            if args.verify_panel_midplane:
                source = result["source"]["source_geometry"]
                mesh = result["source_mesh"]
                assert result["resolved_type"] == "PANE" and len(source["loops"]) == 1
                assert len(section["loops"]) == 1
                frame = mesh["world_frame"]
                normal, origin = section["plane"]["normal"], section["plane"]["origin"]
                z_axis = frame["z_axis"]
                assert abs(abs(math.fsum(a * b for a, b in zip(normal, z_axis))) - 1) < 1e-10
                offset = math.fsum((origin[i] - frame["origin"][i]) * z_axis[i] for i in range(3))
                midpoint = {"CENT": 0, "BOTT": .5, "TOP": -.5}[mesh["native_justification"]] * mesh["thickness_mm"]
                assert math.isclose(offset, midpoint, abs_tol=1e-6)
                polygon = source["loops"][0]["polygon"]
                expected = (abs(polygon["signed_area_mm2"]), polygon["perimeter_mm"])
                measured = polygon_metrics([section["vertices"][i] for i in section["loops"][0]["vertices"]])
                assert all(math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-6) for a, b in zip(measured, expected))
                receipt["native_polygon_comparison"] = {"passed": True, "area_mm2": expected[0], "perimeter_mm": expected[1]}
            after = call("e3d_status")
            ready(after)
            project_after = call("e3d_project_info")
            assert before["session_id"] == after["session_id"]
            assert project_before["project"] == project_after["project"]

        try:
            import ezdxf
        except ImportError:
            receipt["dxf_parser"] = {"available": False, "checked": False}
        else:
            document = ezdxf.readfile(dxf_path)
            audit = document.audit()
            assert not audit.errors and not audit.fixes
            assert document.units == 4 and document.dxfversion == "AC1015"
            outlines = list(document.modelspace().query('LINE[layer=="SECTION_OUTLINE"]'))
            assert len(outlines) == len(section["segments"])
            paper = drawing.get("sheet")
            if paper:
                transform = paper["views"][0]["transform"]
                denominator = paper["scale_denominator"]
                assert tuple(document.header["$LIMMAX"]) == tuple(paper["paper_size_mm"])
            else:
                denominator = 1
                # Plain DXF adds margins. Compare its translation through the first endpoint.
                first = section["vertices"][section["segments"][0]["vertices"][0]]
                transform = {"model_min": [0, 0], "paper_origin_mm": [outlines[0].dxf.start[i] - first[i] for i in (0, 1)]}
            drawn_points = {}
            for line, segment in zip(outlines, section["segments"]):
                for point, index in zip((line.dxf.start, line.dxf.end), segment["vertices"]):
                    expected_point = [transform["paper_origin_mm"][i] + (section["vertices"][index][i] - transform["model_min"][i]) / denominator
                                      for i in (0, 1)]
                    assert all(math.isclose(point[i], expected_point[i], rel_tol=0, abs_tol=5.1e-7) for i in (0, 1))
                    if index in drawn_points:
                        assert drawn_points[index] == tuple(point)[:2]
                    drawn_points[index] = tuple(point)[:2]
            if expected:
                # Recover connectivity from parsed CAD geometry independently
                # of the upstream loop vertex order or segment metadata.
                recovered = cad_contours(outlines)
                assert len(recovered) == 1
                measured = polygon_metrics(recovered[0])
                restored = (measured[0] * denominator ** 2, measured[1] * denominator)
                assert all(math.isclose(a, b, rel_tol=1e-7, abs_tol=1e-4) for a, b in zip(restored, expected))
            elif tool_name == "e3d_section_assembly_drawing":
                recovered = cad_contours(outlines)
                wanted_metrics = sorted(polygon_metrics([section["vertices"][i] for i in loop["vertices"]])
                                        for loop in section["loops"])
                cad_metrics = sorted((area * denominator ** 2, perimeter * denominator)
                                     for area, perimeter in map(polygon_metrics, recovered))
                assert len(cad_metrics) == len(wanted_metrics)
                assert all(math.isclose(a, b, rel_tol=1e-7, abs_tol=1e-4)
                           for actual, wanted in zip(cad_metrics, wanted_metrics) for a, b in zip(actual, wanted))
                receipt["assembly_cad_contours"] = {"passed": True, "loops": len(recovered),
                    "model_area_mm2_and_perimeter_mm": cad_metrics,
                    "scope": "Actual CAD endpoint connectivity and inverse-scale contour metrics versus returned section geometry; not a native Boolean result"}
            if "hatch" in request:
                hatch = result["hatching"]
                hatch_entities = list(document.modelspace().query('LINE[layer=="SECTION_HATCH"]'))
                assert len(hatch_entities) == len(hatch["lines"])
                assert document.layers.get("SECTION_HATCH").dxf.color == 8
                assert document.layers.get("SECTION_HATCH").dxf.linetype == "CONTINUOUS"
                angle = math.radians(request["hatch"].get("angle_degrees", 45))
                direction, normal = (math.cos(angle), math.sin(angle)), (-math.sin(angle), math.cos(angle))
                spacing = request["hatch"]["spacing_mm"]
                phase = request["hatch"].get("phase_mm", 0) % spacing
                # Preserve directed CAD contours independently of source IDs.
                actual_loops = cad_contours(outlines)
                for entity, line in zip(hatch_entities, hatch["lines"]):
                    restored_points = []
                    for point, model_point in zip((entity.dxf.start, entity.dxf.end), (line["start"], line["end"])):
                        wanted = [transform["paper_origin_mm"][i] + (model_point[i] - transform["model_min"][i]) / denominator
                                  for i in (0, 1)]
                        assert all(math.isclose(point[i], wanted[i], rel_tol=0, abs_tol=5.1e-7) for i in (0, 1))
                        restored_points.append([(point[i] - transform["paper_origin_mm"][i]) * denominator + transform["model_min"][i]
                                                for i in (0, 1)])
                    a, b = restored_points
                    delta = (b[0] - a[0], b[1] - a[1])
                    assert abs(direction[0] * delta[1] - direction[1] * delta[0]) <= 2e-6 * denominator
                    location = math.fsum(normal[i] * a[i] for i in (0, 1))
                    index = round((location - phase) / spacing)
                    assert abs(location - (index * spacing + phase)) <= 2e-6 * denominator
                    verify_cad_hatch_segment(tuple(entity.dxf.start)[:2], tuple(entity.dxf.end)[:2], actual_loops)
                receipt["hatch_verification"] = {"passed": True, "actual_cad_lines": len(hatch_entities),
                    "spacing_model_mm": spacing, "spacing_paper_mm": spacing / denominator,
                    "angle_degrees": request["hatch"].get("angle_degrees", 45),
                    "cad_coordinates_pattern_and_containment_verified": True,
                    "containment_paper_tolerance_mm": 1e-5,
                    "scope": "Every CAD hatch line is split at actual contour intersections; every resulting interval is checked with nonzero winding. Numerical CAD-coordinate tolerance applies."}
            original_semantics = cad_semantics(document)
            with tempfile.TemporaryDirectory(prefix="section-dxf-roundtrip-") as directory:
                roundtrip_path = Path(directory) / "section.dxf"
                document.saveas(roundtrip_path)
                reread = ezdxf.readfile(roundtrip_path)
                recheck = reread.audit()
                assert not recheck.errors and not recheck.fixes
                assert cad_semantics(reread) == original_semantics
                if paper:
                    assert tuple(reread.header["$LIMMIN"]) == (0.0, 0.0)
                    assert tuple(reread.header["$LIMMAX"]) == tuple(paper["paper_size_mm"])
            receipt["dxf_parser"] = {"available": True, "checked": True, "version": ezdxf.__version__,
                                     "errors": 0, "fixes": 0, "roundtrip_verified": True,
                                     "section_lines": len(outlines), "source_coordinates_verified": True,
                                     "roundtrip_entity_layer_semantics_verified": True,
                                     "cad_endpoint_contours_verified": expected is not None or tool_name == "e3d_section_assembly_drawing",
                                     "native_polygon_metrics_verified": expected is not None}
        receipt.update(passed=True, session_id=after["session_id"], project=project_after["project"],
                       section_counts=section["counts"], drawing_dimensions=drawing["views"][0]["dimensions"],
                       sheet={key: drawing["sheet"][key] for key in ("paper", "scale_denominator")} if drawing.get("sheet") else None,
                       artifacts={p.name: {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                                  for p in (png_path, dxf_path, args.output_dir / "section.svg")})
    except BaseException as exc:
        receipt["failure"] = f"{type(exc).__name__}: {exc}"
        save()
        raise
    save()
    print(json.dumps({key: receipt[key] for key in ("passed", "session_id", "section_counts", "sheet", "dxf_parser", "artifacts")}, ensure_ascii=True))


if __name__ == "__main__":
    main()

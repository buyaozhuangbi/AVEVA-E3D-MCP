"""Isolated, bounded Manifold Mesh64 subtraction worker.

Invoked as a file by primitive_csg_mesh with explicit JSON input/output paths.
It neither imports AVEVA code nor reads the database. Its parent controls the
wall-clock deadline and discards every result after an unsuccessful exit.
"""

from __future__ import annotations

import importlib.metadata
import json
import math
from pathlib import Path
import sys


MAX_IPC_BYTES = 8 * 1024 * 1024
MAX_VERTICES = 10_000
MAX_TRIANGLES = 20_000
MAX_OPERANDS = 33


def _run(payload):
    try:
        import manifold3d as manifold
        import numpy as np
    except ImportError as exc:
        return {"success": False, "error_code": "csg_engine_unavailable", "detail": str(exc)[:512]}
    if not hasattr(manifold, "Mesh64"):
        return {"success": False, "error_code": "csg_mesh64_unavailable"}
    operands = payload.get("operands") if isinstance(payload, dict) else None
    if not isinstance(operands, list) or not 2 <= len(operands) <= MAX_OPERANDS:
        return {"success": False, "error_code": "csg_worker_invalid_operands"}
    solids = []
    input_vertices = input_triangles = 0
    for operand in operands:
        if not isinstance(operand, dict):
            return {"success": False, "error_code": "csg_worker_invalid_operands"}
        vertices, triangles = operand.get("vertices"), operand.get("triangles")
        if not isinstance(vertices, list) or not isinstance(triangles, list) or not vertices or not triangles:
            return {"success": False, "error_code": "csg_worker_invalid_operand_mesh"}
        input_vertices += len(vertices)
        input_triangles += len(triangles)
        if input_vertices > MAX_VERTICES or input_triangles > MAX_TRIANGLES:
            return {"success": False, "error_code": "csg_input_mesh_limit_exceeded"}
        if any(not isinstance(point, list) or len(point) != 3
               or any(type(value) not in {int, float} or not math.isfinite(value) for value in point)
               for point in vertices):
            return {"success": False, "error_code": "csg_worker_invalid_operand_vertices"}
        if any(not isinstance(face, list) or len(face) != 3
               or any(type(index) is not int or not 0 <= index < len(vertices) for index in face)
               for face in triangles):
            return {"success": False, "error_code": "csg_worker_invalid_operand_triangles"}
        mesh = manifold.Mesh64(np.ascontiguousarray(vertices, dtype=np.float64),
                               np.ascontiguousarray(triangles, dtype=np.uint64))
        solid = manifold.Manifold(mesh)
        if solid.status() != manifold.Error.NoError or solid.is_empty():
            return {"success": False, "error_code": "csg_operand_engine_rejected",
                    "engine_status": solid.status().name}
        if not math.isfinite(solid.volume()) or solid.volume() <= 0:
            return {"success": False, "error_code": "csg_operand_volume_invalid"}
        solids.append(solid)
    # Subtract means all tail operands are removed from the first. Overlapping
    # cutters are handled by the solid Boolean, never by adding cut volumes.
    result = manifold.Manifold.batch_boolean(solids, manifold.OpType.Subtract)
    status = result.status()
    if status != manifold.Error.NoError:
        return {"success": False, "error_code": "csg_engine_subtraction_failed", "engine_status": status.name}
    empty = result.is_empty()
    vertex_count, triangle_count = result.num_vert(), result.num_tri()
    if vertex_count > MAX_VERTICES or triangle_count > MAX_TRIANGLES:
        return {"success": False, "error_code": "csg_output_mesh_limit_exceeded",
                "observed_counts": {"vertices": vertex_count, "triangles": triangle_count}}
    net, gross = float(result.volume()), float(solids[0].volume())
    if not math.isfinite(net) or not math.isfinite(gross):
        return {"success": False, "error_code": "csg_engine_volume_invalid"}
    if empty:
        vertices, triangles = [], []
    else:
        mesh = result.to_mesh64()
        vertices = mesh.vert_properties[:, :3].tolist()
        triangles = mesh.tri_verts.tolist()
        # Mesh properties can duplicate positions; the public geometry must
        # still fit the declared indexed-array budgets after export.
        if len(vertices) > MAX_VERTICES or len(triangles) > MAX_TRIANGLES:
            return {"success": False, "error_code": "csg_output_mesh_limit_exceeded"}
    if result.status() != manifold.Error.NoError:
        return {"success": False, "error_code": "csg_engine_subtraction_failed",
                "engine_status": result.status().name}
    return {
        "success": True, "empty": empty, "vertices": vertices, "triangles": triangles,
        "gross_polyhedral_volume_mm3": gross, "net_volume_mm3": net,
        "engine": {"name": "manifold3d", "version": importlib.metadata.version("manifold3d"),
                   "numpy_version": importlib.metadata.version("numpy"), "numeric_precision": "float64",
                   "mesh_api": "Mesh64", "operation": "Subtract", "status": "NoError", "empty": empty,
                   "operand_count": len(solids), "input_vertices": input_vertices,
                   "input_triangles": input_triangles},
    }


def main():
    if len(sys.argv) != 3:
        return 2
    output = Path(sys.argv[2])
    try:
        with open(sys.argv[1], "rb") as stream:
            encoded = stream.read(MAX_IPC_BYTES + 1)
        if len(encoded) > MAX_IPC_BYTES:
            result = {"success": False, "error_code": "csg_worker_input_limit_exceeded"}
        else:
            payload = json.loads(encoded)
            result = _run(payload)
    except Exception as exc:
        result = {"success": False, "error_code": "csg_worker_calculation_failed",
                  "error_type": type(exc).__name__, "detail": str(exc)[:1024]}
    try:
        encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_IPC_BYTES:
            encoded = b'{"success":false,"error_code":"csg_worker_output_limit_exceeded"}'
        output.write_bytes(encoded)
    except Exception:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

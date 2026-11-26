"""Read native reference, OWNER ancestry and MDB database identity evidence."""
from __future__ import annotations

from typing import Any

from .bridge import BridgeClient, element_ancestry_parameters
from .protocol import McpServer


TOOL = "e3d_element_ancestry"


def element_ancestry(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(args, dict) or set(args) - {"refno", "max_depth"} or "refno" not in args:
        raise ValueError("Element ancestry requires only explicit refno and optional max_depth")
    parameters = element_ancestry_parameters(args["refno"], args.get("max_depth", 32))
    return bridge.element_ancestry(**parameters)


def register_element_ancestry(server: McpServer, bridge: BridgeClient) -> None:
    server.add_tool(TOOL,
        "Read one explicit E3D21 element's native OWNER chain, raw SDK references, formatted references, database "
        "Number/ExtractNumber and membership in the current MDB in one bounded UI callback. ancestors includes the "
        "target at depth zero; max_depth bounds returned ancestry entries. The adapter also reads the current Design WORLD "
        "frame and a bounded complete MDB database identity list. Inspect complete, reached_world, truncated, "
        "mdb_membership_complete, context and read_errors. Preserve raw_ref/raw_refno separately from formatted_refno; "
        "they are independent native observations and must never be substituted or used to infer database numbers. "
        "Nullable database identity or in_current_mdb fields remain unknown. This observation does not grant edit permission, "
        "establish a database lock or prove coordinate-frame equivalence. Requires a canonical numeric refno; no implicit "
        "CE or name lookup. Does not execute PML, refresh claims, save, create geometry, change CE or modify the view.",
        {"type": "object", "properties": {
            "refno": {"type": "string", "pattern": r"^=[1-9][0-9]{0,9}/(?:0|[1-9][0-9]{0,9})$", "maxLength": 22,
                      "description": "Explicit canonical =database/element reference with positive/nonnegative Int32 components."},
            "max_depth": {"type": "integer", "minimum": 1, "maximum": 64, "default": 32,
                          "description": "Maximum ancestry entries including the target at depth zero."}},
         "required": ["refno"], "additionalProperties": False},
        lambda args: element_ancestry(bridge, args), read_only=True)

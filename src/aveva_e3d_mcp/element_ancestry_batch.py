"""Read several native OWNER chains against one shared MDB observation."""
from __future__ import annotations

from typing import Any

from .bridge import BridgeClient, element_ancestry_batch_parameters
from .protocol import McpServer


TOOL = "e3d_element_ancestry_batch"


def element_ancestry_batch(bridge: BridgeClient, args: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(args, dict) or set(args) - {"refnos", "max_depth"} or "refnos" not in args:
        raise ValueError("Batch element ancestry requires only explicit refnos and optional max_depth")
    parameters = element_ancestry_batch_parameters(args["refnos"], args.get("max_depth", 32))
    return bridge.element_ancestry_batch(**parameters)


def register_element_ancestry_batch(server: McpServer, bridge: BridgeClient) -> None:
    server.add_tool(TOOL,
        "Read 1 to 16 unique explicit E3D21 numeric references and their bounded native OWNER chains in one UI callback, "
        "sharing one current MDB database identity scan, Design WORLD frame and before/after context observation. "
        "results preserves input order and reports each requested_refno, status, complete, reached_world, truncated and "
        "read_errors; ancestors includes the target at depth zero. Inspect top-level success/complete, shared read_errors, "
        "mdb_membership_complete and context before using any per-target evidence. A native database fault stops further "
        "SDK reads and leaves remaining targets not_started. Preserve raw_ref/raw_refno separately from formatted_refno; "
        "never infer database Number/ExtractNumber from reference spelling or replace nullable membership with false. "
        "Uses the native batch endpoint only, with no fallback to separate single-element requests. No implicit CE, name "
        "lookup, PML, writes, claims refresh, saving or view changes. A single callback is not a database lock or edit permission.",
        {"type": "object", "properties": {
            "refnos": {"type": "array", "minItems": 1, "maxItems": 16, "uniqueItems": True,
                       "items": {"type": "string", "pattern": r"^=[1-9][0-9]{0,9}/(?:0|[1-9][0-9]{0,9})$",
                                 "maxLength": 22},
                       "description": "Ordered unique canonical =positiveInt32/nonnegativeInt32 references."},
            "max_depth": {"type": "integer", "minimum": 1, "maximum": 64, "default": 32,
                          "description": "Maximum ancestry entries per target, including depth zero; at most 1024 total."}},
         "required": ["refnos"], "additionalProperties": False},
        lambda args: element_ancestry_batch(bridge, args), read_only=True)

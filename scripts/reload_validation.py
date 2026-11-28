"""Pure checks for the project identity observed around an E3D DESIGN reload."""

from __future__ import annotations

import json
import os
from typing import Any
from pathlib import Path


def create_restart_journal(report: Path, state: dict[str, Any]) -> None:
    """Reserve a new restart journal without racing another verifier."""
    report.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation is the safety boundary: two verifier processes must
    # never both get far enough to issue SAVEWORK/DESIGN for the same report.
    with report.open("x", encoding="utf-8") as stream:
        json.dump(state, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def validate_project_reload(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Validate stable project/MDB identity and record E3D's CE selection.

    SAVEWORK/DESIGN can select a different legal current element while opening
    the same project and MDB. That selection is evidence to retain, not a
    reload failure. Project and MDB identity still have to remain exact and
    the post-reload project must report a connected session.
    """
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise AssertionError("Reload project observations must be objects.")
    for field in ("name", "mdb"):
        if before.get(field) != after.get(field):
            raise AssertionError("DESIGN changed project %s." % field)
    if not after.get("connected"):
        raise AssertionError("DESIGN did not leave the project connected.")
    current_before = before.get("current_element")
    current_after = after.get("current_element")
    return {
        "current_element_before": current_before,
        "current_element_after": current_after,
        "current_element_changed": current_before != current_after,
        "project_mdb_unchanged": True,
    }

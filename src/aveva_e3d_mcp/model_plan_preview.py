"""Bind a Design edit preview receipt to the plan's resolved input and CE."""
from __future__ import annotations

import re
from typing import Any

from .bridge import BridgeClient, BridgeError, _contains_native_fault


def model_edit_preview(bridge: BridgeClient, tool: str, arguments: dict[str, Any],
                       context: dict[str, Any]) -> dict[str, Any]:
    orientation = arguments.get("orientation", {})
    position, x_axis, y_axis = arguments.get("position"), orientation.get("x_axis"), orientation.get("y_axis")
    wrt = arguments.get("wrt", "OWNER")
    observation = {"tool": tool, "arguments": arguments}
    try:
        if tool == "e3d_create_element":
            reply = bridge.create_element(arguments["parent_refno"], arguments["element_type"], arguments["name"],
                                          arguments["attributes"], position, x_axis, y_axis, wrt, True)
        elif tool == "e3d_copy_element":
            reply = bridge.copy_element(arguments["source_refno"], arguments["parent_refno"], arguments["name"],
                                        position=position, x_axis=x_axis, y_axis=y_axis, wrt=wrt, dry_run=True)
        elif tool == "e3d_set_transform":
            reply = bridge.set_transform(arguments["refno"], position, x_axis, y_axis, wrt, True)
        else:
            raise ValueError(f"Unsupported modeling preview tool: {tool}")
    except BridgeError as error:
        status = "native_fault" if _contains_native_fault(error.diagnostics) else "dispatch_failed"
        return {**observation, "status": status, "error": str(error)[:1024], "diagnostics": error.diagnostics}
    if _contains_native_fault(reply):
        return {**observation, "status": "native_fault", "response": reply}
    verified = _matches_preview(reply, arguments, context)
    return {**observation, "status": "observed" if verified else "unverified", "response": reply}


def _matches_preview(reply: Any, arguments: dict[str, Any], context: dict[str, Any]) -> bool:
    if not isinstance(reply, dict) or reply.get("ok") is not True or reply.get("version") != "E3D21":
        return False
    if any(key in reply and reply[key] != context[key] for key in ("session_id", "bridge_build")):
        return False
    result = reply.get("result")
    if (not isinstance(result, dict) or result.get("success") is not True
            or result.get("dry_run") is not True or result.get("ce_unchanged") is not True
            or result.get("ce_before") != context["current_element"]
            or result.get("ce_after") != context["current_element"]):
        return False
    for argument, field in (("parent_refno", "parent"), ("source_refno", "source"), ("refno", "element")):
        if argument in arguments:
            identity = result.get(field)
            if not isinstance(identity, dict) or identity.get("refno") != arguments[argument]:
                return False
    frame = result.get("frame")
    if not isinstance(frame, dict) or frame.get("requested") != arguments.get("wrt", "OWNER"):
        return False
    submitted = {key: arguments.get(key) for key in ("position", "orientation")}
    submitted.update({key: arguments[key] for key in ("name", "attributes") if key in arguments})
    if result.get("submitted") != submitted:
        return False
    if "parent_refno" in arguments:
        revision = result.get("revision")
        if (not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{64}", revision)
                or result.get("revision_conflict") is True):
            return False
    return True

"""Validate managed output-ring windows without interpreting opaque cursors."""
from __future__ import annotations

import re
from typing import Any


MESSAGE_CURSOR_MAX_LENGTH = 1024
_MAX_SEQUENCE = 9223372036854775807


def message_window_parameters(cursor: Any = "", limit: Any = 100,
                              checkpoint: Any = False) -> dict[str, str]:
    if not isinstance(cursor, str) or len(cursor) > MESSAGE_CURSOR_MAX_LENGTH:
        raise ValueError("cursor must be a string of at most 1024 characters")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError("limit must be an integer between 1 and 500")
    if not isinstance(checkpoint, bool):
        raise ValueError("checkpoint must be a boolean")
    if checkpoint and cursor:
        raise ValueError("checkpoint cannot be combined with a cursor")
    query = {"limit": str(limit), "checkpoint": "true" if checkpoint else "false"}
    if cursor:
        query["cursor"] = cursor
    return query


def _sequence(value: Any, name: str) -> int:
    if (not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]{0,18}", value)
            or int(value) > _MAX_SEQUENCE):
        raise ValueError(name + " must be a canonical nonnegative Int64 decimal string")
    return int(value)


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(name + " is not a valid bounded integer")
    return value


def validate_message_window(value: Any, *, version: str, cursor: str,
                            limit: int, checkpoint: bool) -> None:
    """Reject legacy tails and internally inconsistent purported cursor results.

    Native cursor authentication/session binding belongs to the adapter. These
    checks validate the advertised window contract, not causation, readiness,
    model state, or the completeness of output not captured by the listener.
    """
    if not isinstance(value, dict) or value.get("ok") is not True:
        raise ValueError("message window requires an ok=true object")
    if value.get("version") != version:
        raise ValueError("message window has no matching native version")
    for field in ("session_id", "bridge_build"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ValueError("message window is missing native " + field)
    result = value.get("result")
    if not isinstance(result, dict):
        raise ValueError("message window has no result; a legacy message tail is not a cursor window")
    if type(result.get("schema_version")) is not int or result["schema_version"] != 1:
        raise ValueError("message window schema_version is unsupported")
    for field, expected in (("success", True), ("available", True), ("managed_only", True),
                            ("operation_correlated", False), ("checkpoint", checkpoint)):
        if result.get(field) is not expected:
            raise ValueError("message window did not verify " + field)
    if _integer(result.get("requested_limit"), "requested_limit", 1, 500) != limit:
        raise ValueError("message window requested_limit does not match the request")
    after = _sequence(result.get("after_sequence"), "after_sequence")
    latest = _sequence(result.get("latest_sequence"), "latest_sequence")
    next_sequence = _sequence(result.get("next_sequence"), "next_sequence")
    overwritten = _sequence(result.get("overwritten_count"), "overwritten_count")
    first = result.get("first_retained_sequence")
    if first is not None:
        first = _sequence(first, "first_retained_sequence")
        if not 1 <= first <= latest or latest - first >= 500:
            raise ValueError("message window retention interval is inconsistent")
    elif latest != 0:
        raise ValueError("nonempty message history has no first retained sequence")
    if latest < after or not after <= next_sequence <= latest:
        raise ValueError("message window positions are inconsistent")
    if not cursor and not checkpoint and after != 0:
        raise ValueError("initial message window did not start at sequence zero")
    rows = result.get("messages")
    if not isinstance(rows, list) or len(rows) > limit:
        raise ValueError("message window messages exceed the requested bound or are unavailable")
    returned = _integer(result.get("returned_count"), "returned_count", 0, limit)
    remaining = _integer(result.get("remaining_count"), "remaining_count", 0, 500)
    if returned != len(rows):
        raise ValueError("message window returned_count does not match its messages")
    if checkpoint:
        expected_overwritten = expected_available = 0
        if after != latest or next_sequence != latest:
            raise ValueError("checkpoint did not capture the current high-water sequence")
    else:
        expected_overwritten = max(0, first - after - 1) if first is not None else 0
        expected_available = latest - max(after, first - 1) if first is not None else 0
    if overwritten != expected_overwritten:
        raise ValueError("message window overwritten count is inconsistent")
    if returned != min(limit, expected_available) or remaining != expected_available - returned:
        raise ValueError("message window pagination counts are inconsistent")
    start = max(after, first - 1) if first is not None else after
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or _sequence(row.get("sequence"), "message.sequence") != start + index + 1:
            raise ValueError("message window rows are not an ordered contiguous sequence")
        if any(not isinstance(row.get(field), str) for field in
               ("type", "description", "details", "category", "date")):
            raise ValueError("message window row has missing or malformed captured output")
    if next_sequence != (start + returned if returned else after):
        raise ValueError("message window next position would skip or repeat messages")
    if result.get("has_more") is not (remaining > 0):
        raise ValueError("message window has_more is inconsistent")
    if result.get("complete_since_after_sequence") is not (overwritten == 0 and remaining == 0):
        raise ValueError("message window completeness is inconsistent")
    expected_status = ("checkpoint" if checkpoint else "overwritten" if overwritten
                       else "more" if remaining else "complete")
    if result.get("status") != expected_status:
        raise ValueError("message window status is inconsistent")
    next_cursor = result.get("next_cursor")
    if not isinstance(next_cursor, str) or not next_cursor or len(next_cursor) > MESSAGE_CURSOR_MAX_LENGTH:
        raise ValueError("message window did not return a bounded continuation cursor")

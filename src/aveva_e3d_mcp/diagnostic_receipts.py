"""Bounded process-local storage of full historical diagnostic receipts."""

from __future__ import annotations

import json
import math
import re
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .protocol import ImageToolResult


_STORAGE = "mcp_process_memory"
_RECEIPT_ID = re.compile(r"diag_[0-9a-f]{32}\Z")


@dataclass(frozen=True)
class _Entry:
    metadata_json: bytes
    image_data: str | None
    image_mime: str | None
    created_utc: str
    expires_utc: str
    expires_at: float
    stored_bytes: int


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite_number(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _embedded_image(value: Any) -> bool:
    if isinstance(value, dict):
        return "image_base64" in value or any(_embedded_image(item) for item in value.values())
    return isinstance(value, list) and any(_embedded_image(item) for item in value)


class DiagnosticReceiptStore:
    """Freeze full metadata and optional original PNG data in this MCP process.

    Capacity measures encoded UTF-8 JSON plus encoded image bytes, excluding
    Python object overhead. FIFO order and expiry never change on a lookup.
    Instances share no state; there is no disk persistence, bridge or network.
    """

    def __init__(
        self, version: str, *, max_entries: int = 16, max_bytes: int = 64 * 1024 * 1024,
        ttl_seconds: float = 1800, clock: Callable[[], float] = time.monotonic,
        utc_clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(version, str) or not version.strip():
            raise ValueError("version must be a non-empty string")
        for name, value in (("max_entries", max_entries), ("max_bytes", max_bytes)):
            if type(value) is not int or value < 1:
                raise ValueError(name + " must be a positive integer")
        if not _finite_number(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be a finite positive number")
        if not callable(clock) or utc_clock is not None and not callable(utc_clock):
            raise ValueError("clock and utc_clock must be callable")
        self.version = version.strip()
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._utc_clock = _utc_now if utc_clock is None else utc_clock
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._stored_bytes = 0
        self._lock = threading.RLock()

    @staticmethod
    def _failed(reason: str, **details: Any) -> dict[str, Any]:
        return {"status": "failed", "reason": reason, "storage": _STORAGE, **details}

    def _now(self) -> float:
        value = self._clock()
        if not _finite_number(value):
            raise ValueError("monotonic clock must return a finite number")
        return float(value)

    def put(self, value: dict[str, Any] | ImageToolResult) -> dict[str, Any]:
        """Store only full diagnosis evidence; failure never raises to its caller.

        Freeze and validate before calculating evictions. Oversized or otherwise
        invalid insertions leave every existing entry and FIFO position intact.
        """
        try:
            image_data, image_mime = None, None
            if isinstance(value, ImageToolResult):
                metadata = value.metadata
                if (value.mime_type != "image/png" or not isinstance(value.data, str) or
                        not value.data or not value.data.isascii()):
                    return self._failed("invalid_diagnosis")
                image_data, image_mime = value.data, value.mime_type
            elif isinstance(value, dict):
                metadata = value
            else:
                return self._failed("invalid_diagnosis")
            if (not isinstance(metadata, dict) or metadata.get("ok") is not True or
                    metadata.get("version") != self.version):
                return self._failed("invalid_diagnosis")
            result = metadata.get("result")
            if (not isinstance(result, dict) or type(result.get("schema")) is not int or result["schema"] != 1 or
                    result.get("historical") is True or "diagnosis" in result):
                return self._failed("invalid_diagnosis")
            evidence = result.get("evidence")
            if not isinstance(evidence, dict):
                return self._failed("invalid_diagnosis")
            compaction = result.get("evidence_compaction")
            if isinstance(compaction, dict) and compaction.get("applied") is True:
                return self._failed("compacted_diagnosis")
            if not isinstance(evidence.get("gates"), list):
                return self._failed("compacted_diagnosis")
            if compaction is not None and (not isinstance(compaction, dict) or compaction.get("applied") is not False):
                return self._failed("invalid_diagnosis")
            try:
                if _embedded_image(metadata):
                    return self._failed("invalid_diagnosis")
                encoded = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
            except Exception:
                return self._failed("serialization_failed")
            stored_bytes = len(encoded) + (len(image_data) if image_data is not None else 0)
            if stored_bytes > self.max_bytes:
                return self._failed("too_large", stored_bytes=stored_bytes, max_bytes=self.max_bytes)
            with self._lock:
                now = self._now()
                expires_at = now + self.ttl_seconds
                if not math.isfinite(expires_at):
                    return self._failed("storage_error")
                created = self._utc_clock()
                if not isinstance(created, datetime):
                    return self._failed("storage_error")
                created = (created.replace(tzinfo=timezone.utc) if created.tzinfo is None
                           else created.astimezone(timezone.utc))
                created_utc = _utc_text(created)
                expires_utc = _utc_text(created + timedelta(seconds=self.ttl_seconds))
                receipt_id = "diag_" + secrets.token_hex(16)
                if _RECEIPT_ID.fullmatch(receipt_id) is None or receipt_id in self._entries:
                    return self._failed("storage_error")
                entry = _Entry(encoded, image_data, image_mime, created_utc, expires_utc, expires_at, stored_bytes)
                # Build a replacement map first. An insertion failure must not
                # clear valid old receipts before discovering it cannot store.
                retained = OrderedDict((key, item) for key, item in self._entries.items() if now < item.expires_at)
                retained_bytes = sum(item.stored_bytes for item in retained.values())
                while len(retained) >= self.max_entries or retained_bytes + stored_bytes > self.max_bytes:
                    _, evicted = retained.popitem(last=False)
                    retained_bytes -= evicted.stored_bytes
                retained[receipt_id] = entry
                info = {
                    "status": "stored", "receipt_id": receipt_id, "created_utc": created_utc,
                    "expires_utc": expires_utc, "ttl_seconds": self.ttl_seconds,
                    "stored_bytes": stored_bytes, "image_available": image_data is not None,
                    "storage": _STORAGE,
                }
                self._entries = retained
                self._stored_bytes = retained_bytes + stored_bytes
                return info
        except Exception:
            return self._failed("storage_error")

    def get(self, receipt_id: str, include_image: bool = False) -> dict[str, Any] | ImageToolResult:
        """Return an independent historical value without touching E3D or expiry."""
        if not isinstance(receipt_id, str) or _RECEIPT_ID.fullmatch(receipt_id) is None:
            raise ValueError("receipt_id must match diag_ followed by 32 lowercase hexadecimal characters")
        if not isinstance(include_image, bool):
            raise ValueError("include_image must be a boolean")
        with self._lock:
            now = self._now()
            expired = [key for key, item in self._entries.items() if now >= item.expires_at]
            for key in expired:
                self._stored_bytes -= self._entries.pop(key).stored_bytes
            entry = self._entries.get(receipt_id)
        diagnosis = json.loads(entry.metadata_json) if entry is not None else None
        available = entry is not None
        image_available = available and entry.image_data is not None
        included = image_available and include_image
        envelope = {"ok": True, "version": self.version, "result": {
            "success": True, "available": available, "status": "found" if available else "unknown_or_expired",
            "receipt_id": receipt_id, "created_utc": entry.created_utc if available else None,
            "expires_utc": entry.expires_utc if available else None, "historical": True, "live_read": False,
            "diagnosis": diagnosis, "image": {
                "requested": include_image, "available": bool(image_available), "included": bool(included)},
        }}
        if included:
            return ImageToolResult(entry.image_data, entry.image_mime, envelope)
        return envelope

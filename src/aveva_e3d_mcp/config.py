from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Runtime settings read once when the MCP process starts."""

    mode: str = "mock"
    bridge_url: str = "http://127.0.0.1:8765"
    bridge_urls: dict[str, str] | None = None
    default_version: str = "default"
    version: str | None = None
    install_root: str | None = None
    bridge_timeout: float = 10.0
    allow_commands: bool = False
    log_file: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        mode = os.getenv("E3D_MCP_MODE", "mock").strip().lower()
        if mode not in {"mock", "bridge"}:
            raise ValueError("E3D_MCP_MODE must be either 'mock' or 'bridge'")

        raw_timeout = os.getenv("E3D_BRIDGE_TIMEOUT", "10")
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise ValueError("E3D_BRIDGE_TIMEOUT must be a number") from exc
        if not math.isfinite(timeout) or timeout <= 0 or timeout > 120:
            raise ValueError("E3D_BRIDGE_TIMEOUT must be between 0 and 120 seconds")

        bridge_url = _normalize_bridge_url(os.getenv("E3D_BRIDGE_URL", cls.bridge_url), "E3D_BRIDGE_URL")

        bridge_urls = _parse_bridge_urls(os.getenv("E3D_BRIDGES"))
        selected_version = os.getenv("E3D_MCP_VERSION", "").strip()
        if not selected_version:
            selected_version = os.getenv("E3D_DEFAULT_VERSION", "").strip()
        if bridge_urls:
            if not selected_version:
                selected_version = next(iter(bridge_urls))
            if selected_version not in bridge_urls:
                known = ", ".join(bridge_urls)
                raise ValueError(f"E3D_MCP_VERSION '{selected_version}' is not configured; choose one of: {known}")
        elif not selected_version:
            selected_version = "default"

        if bridge_urls:
            bridge_url = bridge_urls[selected_version]

        return cls(
            mode=mode,
            bridge_url=bridge_url,
            bridge_urls=bridge_urls,
            default_version=selected_version,
            version=selected_version,
            install_root=os.getenv("E3D_INSTALL_ROOT") or None,
            bridge_timeout=timeout,
            allow_commands=_env_bool("E3D_MCP_ALLOW_COMMANDS", False),
            log_file=os.getenv("E3D_LOG_FILE") or None,
        )

    def versions(self) -> list[str]:
        """Return the one version served by this MCP process."""
        return [self.version or self.default_version]

    def url_for(self, version: str | None = None) -> tuple[str, str]:
        instance_version = self.version or self.default_version
        selected = (version or instance_version).strip()
        if not selected:
            selected = instance_version
        if self.version and selected != self.version:
            raise ValueError(f"This MCP instance is configured for '{self.version}', not '{selected}'")
        if self.bridge_urls:
            try:
                return self.bridge_urls[selected], selected
            except KeyError as exc:
                known = ", ".join(self.bridge_urls)
                raise ValueError(f"Unknown E3D version '{selected}'; configured versions: {known}") from exc
        return self.bridge_url, selected


def _normalize_bridge_url(url: object, setting: str) -> str:
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise ValueError(f"{setting} must start with http:// or https://")
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in url):
        raise ValueError(f"{setting} must not contain whitespace or control characters")
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        # Accessing port validates both its numeric form and allowed range.
        parsed.port
    except ValueError as exc:
        raise ValueError(f"{setting} must have a valid hostname and port") from exc
    if not hostname or "\\" in hostname:
        raise ValueError(f"{setting} must have a valid hostname and port")
    if "?" in url or "#" in url:
        raise ValueError(f"{setting} must not contain a query or fragment")
    return url.rstrip("/")


def _reject_duplicate_bridge_labels(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for label, value in pairs:
        if label in result:
            raise ValueError(f"E3D_BRIDGES contains duplicate version label '{label}'")
        result[label] = value
    return result


def _parse_bridge_urls(raw: str | None) -> dict[str, str] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_bridge_labels)
    except json.JSONDecodeError as exc:
        raise ValueError("E3D_BRIDGES must be a JSON object such as {\"E3D31\":\"http://127.0.0.1:8766\"}") from exc
    if not isinstance(value, Mapping) or not value:
        raise ValueError("E3D_BRIDGES must be a non-empty JSON object")
    result: dict[str, str] = {}
    for version, url in value.items():
        if not isinstance(version, str) or not version.strip():
            raise ValueError("E3D_BRIDGES version labels must be non-empty strings")
        label = version.strip()
        if label in result:
            raise ValueError(f"E3D_BRIDGES contains duplicate version label '{label}'")
        result[label] = _normalize_bridge_url(url, f"E3D_BRIDGES URL for '{version}'")
    return result

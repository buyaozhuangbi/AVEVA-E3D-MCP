from __future__ import annotations

import json
import hashlib
import http.client
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .message_window import message_window_parameters, validate_message_window
from .view_observation import capture_observation_result, view_observation_result


PML_SCRIPT_MAX_BYTES = 262144


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-standard JSON numeric constant: {value}")


def _finite_json_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("JSON numeric value is not finite")
    return number


def _strict_json_loads(value: str) -> Any:
    return json.loads(value, parse_constant=_reject_json_constant, parse_float=_finite_json_float)


def normalize_expected_element_type(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("expected_type must be a string")
    text = value.strip()
    if text and (len(text) > 64 or any(not (char.isalnum() or char in ":_") for char in text)):
        raise ValueError("expected_type must be a Design element type name.")
    return text.upper()


def _verify_expected_element_type(response: dict[str, Any], expected_type: str, *, schema: bool) -> dict[str, Any]:
    if not expected_type or response.get("ok") is not True:
        return response
    result = response.get("result")
    actual_type = None
    if isinstance(result, dict):
        if schema:
            element = result.get("element")
            element_type = result.get("element_type")
            if isinstance(element, dict):
                actual_type = element.get("type")
            if not isinstance(actual_type, str) and isinstance(element_type, dict):
                actual_type = element_type.get("short_name") or element_type.get("name")
        else:
            actual_type = result.get("type")
    if isinstance(actual_type, str) and actual_type.strip().upper() == expected_type:
        return response
    resolved = actual_type if isinstance(actual_type, str) and actual_type else "unknown"
    return {
        **response,
        "ok": False,
        "error": (f"The E3D bridge did not resolve expected_type {expected_type}; "
                  f"it returned {resolved}. Update or reload the E3D21 add-in before relying on this read."),
    }


def graphical_selection_parameters(refnos: Any, dry_run: Any = True,
                                   expected_revision: Any = None) -> dict[str, Any]:
    """Require bounded numeric identities; an explicit empty list means clear."""
    if not isinstance(refnos, list) or len(refnos) > 32:
        raise ValueError("refnos must contain 0 to 32 explicit numeric element references")
    normalized = []
    for value in refnos:
        if not isinstance(value, str) or not re.fullmatch(r"=[0-9]+/[0-9]+", value.strip()):
            raise ValueError("refnos must contain explicit =database/element numeric references")
        numbers = [part.lstrip("0") or "0" for part in value.strip()[1:].split("/")]
        if numbers[0] == "0" or any(len(part) > 10 or len(part) == 10 and part > "2147483647" for part in numbers):
            raise ValueError("refnos require a positive Int32 database and nonnegative Int32 element")
        normalized.append("=" + "/".join(numbers))
    if len(set(normalized)) != len(normalized):
        raise ValueError("refnos must not contain duplicate numeric identities")
    if not isinstance(dry_run, bool):
        raise ValueError("dry_run must be a boolean")
    payload = {"refnos": normalized, "dry_run": dry_run}
    if expected_revision is not None:
        if not isinstance(expected_revision, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_revision):
            raise ValueError("expected_revision must contain exactly 64 hexadecimal characters")
        payload["expected_revision"] = expected_revision.lower()
    elif not dry_run:
        raise ValueError("expected_revision from a fresh selection preview is required for execution")
    return payload


def element_access_refnos(values: Any) -> list[str]:
    """Validate explicit ordered input without resolving or merging native aliases."""
    if not isinstance(values, list) or not 1 <= len(values) <= 32:
        raise ValueError("refnos must contain 1 to 32 explicit element references")
    normalized = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("refnos must contain explicit /NAME or =database/element references")
        refno = value.strip()
        valid = bool(refno) and not any(
            char.isspace() or ord(char) < 32 or 127 <= ord(char) < 160 for char in refno)
        if valid and refno.startswith("/") and len(refno) > 1 and not any(char in refno for char in "*?"):
            pass
        elif valid and re.fullmatch(r"=[0-9]+/[0-9]+", refno):
            numbers = [part.lstrip("0") or "0" for part in refno[1:].split("/")]
            valid = numbers[0] != "0" and all(
                len(part) < 10 or len(part) == 10 and part <= "2147483647" for part in numbers)
        else:
            valid = False
        if not valid:
            raise ValueError("refnos must contain explicit /NAME or =database/element references with positive Int32 database and nonnegative Int32 element values")
        normalized.append(refno)
    if len({refno.casefold() for refno in normalized}) != len(normalized):
        raise ValueError("refnos must not contain duplicate references")
    return normalized


def element_ancestry_parameters(refno: Any, max_depth: Any = 32) -> dict[str, Any]:
    """Require one explicit identity without resolving or rewriting native refs."""
    if (not isinstance(refno, str)
            or re.fullmatch(r"=[1-9][0-9]{0,9}/(?:0|[1-9][0-9]{0,9})", refno) is None
            or any(int(part) > 2147483647 for part in refno[1:].split("/"))):
        raise ValueError("refno must be a canonical numeric Int32 =database/element reference")
    if type(max_depth) is not int or not 1 <= max_depth <= 64:
        raise ValueError("max_depth must be an integer between 1 and 64")
    return {"refno": refno, "max_depth": max_depth}


def spatial_map_status_parameters(database_number: Any, extract_number: Any,
                                  expected_context: Any = "") -> dict[str, Any]:
    values = {"database_number": database_number, "extract_number": extract_number}
    for name, minimum in (("database_number", 1), ("extract_number", 0)):
        value = values[name]
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= 2147483647:
            raise ValueError(f"{name} must be an integer between {minimum} and 2147483647")
    if not isinstance(expected_context, str) or (expected_context and not re.fullmatch(r"[a-f0-9]{64}", expected_context)):
        raise ValueError("expected_context must be empty or a 64-character lowercase hexadecimal context_id")
    return {**values, "expected_context": expected_context}


def native_clash_parameters(first_refnos: Any, second_refnos: Any, clearance: Any = 0,
                            result_limit: Any = 50, dry_run: Any = True) -> dict[str, Any]:
    """Validate the explicit input budget; native work is not bounded by it."""
    def references(values: Any, name: str) -> list[str]:
        if not isinstance(values, list) or not 1 <= len(values) <= 8:
            raise ValueError(f"{name} must contain 1 to 8 explicit element references")
        normalized = []
        for value in values:
            if not isinstance(value, str):
                raise ValueError(f"{name} must contain explicit /NAME or =database/element references")
            refno = value.strip()
            if (not refno or any(char.isspace() or ord(char) < 32 or 127 <= ord(char) < 160 for char in refno)
                    or not (refno.startswith("/") and len(refno) > 1 or re.fullmatch(r"=[0-9]+/[0-9]+", refno))):
                raise ValueError(f"{name} must contain explicit /NAME or =database/element references")
            normalized.append(refno)
        if len({refno.casefold() for refno in normalized}) != len(normalized):
            raise ValueError(f"{name} must not contain duplicate references")
        return normalized

    first = references(first_refnos, "first_refnos")
    second = references(second_refnos, "second_refnos")
    if {refno.casefold() for refno in first}.intersection(refno.casefold() for refno in second):
        raise ValueError("first_refnos and second_refnos must not overlap")
    if (isinstance(clearance, bool) or not isinstance(clearance, (int, float))
            or clearance < 0 or clearance > 1000000 or not math.isfinite(clearance)):
        raise ValueError("clearance must be a finite number between 0 and 1000000 in unverified native units")
    if isinstance(result_limit, bool) or not isinstance(result_limit, int) or not 1 <= result_limit <= 200:
        raise ValueError("result_limit must be an integer between 1 and 200")
    if not isinstance(dry_run, bool):
        raise ValueError("dry_run must be a boolean")
    return {"first_refnos": first, "second_refnos": second, "clearance": float(clearance),
            "result_limit": result_limit, "dry_run": dry_run}


def pml_script_request_id(value: Any) -> str:
    """Accept canonical UUID text, preserving its identity across case variants."""
    if not isinstance(value, str) or re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", value
    ) is None:
        raise ValueError("request_id must be a UUID in 8-4-4-4-12 hexadecimal format")
    return str(uuid.UUID(value))


_PARAGON_COMPONENT_WRITES = frozenset({
    "/api/v1/paragon/component-copy",
    "/api/v1/paragon/component-parameters",
    "/api/v1/paragon/component-create",
})
_PARAGON_COMPONENT_PATHS = _PARAGON_COMPONENT_WRITES | {
    path + "/status" for path in _PARAGON_COMPONENT_WRITES
}


_NATIVE_RESULT_DIAGNOSTIC_PATHS = frozenset({
    "/api/v1/element/create", "/api/v1/element/copy", "/api/v1/element/primitive",
    "/api/v1/panel/create", "/api/v1/panel/update", "/api/v1/element/transform",
    "/api/v1/element/transform/revisioned",
    "/api/v1/element/delete", "/api/v1/element/attribute", "/api/v1/element/attributes/set",
    "/api/v1/view/set", "/api/v1/view/camera/set", "/api/v1/view/fit", "/api/v1/view/restore",
    "/api/v1/drawlist/visual", "/api/v1/drawlist/remove", "/api/v1/view/primitive-visual",
    "/api/v1/view/graphical-selection", "/api/v1/clash/check",
}) | _PARAGON_COMPONENT_WRITES


_WRITE_ATTEMPT_FIELDS = {
    "/api/v1/element/create": ("creation_attempted",),
    "/api/v1/element/copy": ("creation_attempted",),
    "/api/v1/panel/create": ("creation_attempted",),
    "/api/v1/element/delete": ("delete_attempted",),
    "/api/v1/clash/check": ("native_check_attempted",),
    "/api/v1/view/fit": ("write_attempted", "drawlist_write_attempted"),
    "/api/v1/drawlist/visual": ("write_attempted", "add_attempted"),
    "/api/v1/command": (),
    "/api/v1/command-batch": (),
    "/api/v1/pml-script": ("execution_attempted",),
    "/api/v1/paragon/component-copy": ("creation_attempted", "native_run_attempted"),
    "/api/v1/paragon/component-parameters": ("parameter_write_attempted",),
    "/api/v1/paragon/component-create": ("creation_attempted",),
}
_ALWAYS_MUTATING_PATHS = frozenset({"/api/v1/view/fit", "/api/v1/view/restore"})
_PML_WRITE_PATHS = frozenset({"/api/v1/command", "/api/v1/command-batch", "/api/v1/pml-script"})


def _uninformative_write_failure(method: str, path: str, payload: Any, value: dict[str, Any]) -> bool:
    """Preserve unknown write outcomes, including failures lacking native evidence.

    HTTP status and current command/fault state do not locate a failure before
    or after a native write. Inspect only exact route contracts, without
    forwarding command/script results or treating replay policy alone as
    evidence that nothing executed.
    """
    if (method != "POST" or path not in _NATIVE_RESULT_DIAGNOSTIC_PATHS | _PML_WRITE_PATHS
            or not isinstance(payload, dict)
            or path not in _ALWAYS_MUTATING_PATHS and payload.get("dry_run") is not False):
        return False
    # These adapters compare their immutable session ID before executing PML.
    expected_session = payload.get("expected_session_id")
    actual_session = value.get("session_id")
    if (path in {"/api/v1/command", "/api/v1/pml-script"}
            and isinstance(expected_session, str) and expected_session
            and isinstance(actual_session, str) and actual_session
            and actual_session != expected_session):
        return False
    receipt = value.get("result")
    receipts = [receipt] if isinstance(receipt, dict) else []
    if path == "/api/v1/pml-script":
        # Session/capacity rejections and duplicate submissions have bounded
        # execution metadata in the envelope, rather than a native result.
        receipts.append(value)
    if path in _PARAGON_COMPONENT_WRITES:
        if isinstance(receipt, dict) and isinstance(receipt.get("progress"), dict):
            receipts.append(receipt["progress"])
        receipts.append(value)
    attempted_fields = _WRITE_ATTEMPT_FIELDS.get(path, ("write_attempted",))
    for receipt in receipts:
        if type(receipt.get("outcome_uncertain")) is bool:
            # Command/script result bodies stay private, but their explicit
            # outcome remains authoritative even when diagnostics omit them.
            return receipt["outcome_uncertain"]
        if receipt.get("success") is True or receipt.get("dry_run") is True:
            return False
        if (path == "/api/v1/pml-script" or path in _PARAGON_COMPONENT_WRITES) and receipt.get("this_submission_executed") is False:
            return False
        if attempted_fields and all(receipt.get(field) is False for field in attempted_fields):
            return False
        # BusyResult is returned before admission, unlike an observed busy flag
        # in the outer error envelope's current command-state diagnostics.
        if receipt.get("busy") is True and not any(field in receipt for field in attempted_fields):
            return False
        if type(receipt.get("write_applied")) is bool:
            return False
        if (any(receipt.get(field) is True for field in attempted_fields)
                and type(receipt.get("retry_safe")) is bool):
            return False
    return True


def _contains_native_fault(value: Any) -> bool:
    """Find actual fault markers without trusting response shape or recursion."""
    pending, seen = [value], set()
    while pending:
        item = pending.pop()
        if not isinstance(item, (dict, list, tuple)) or id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, dict):
            if (any(item.get(key) is True for key in
                    ("has_fault", "native_fault", "native_database_fault", "native_fault_latched"))
                    or any(item.get(key) in ("native_fault", "native_database_fault")
                           for key in ("code", "error_code"))):
                return True
            pending.extend(item.values())
        else:
            pending.extend(item)
    return False


def _response_failure_diagnostics(path: str, value: dict[str, Any]) -> dict[str, Any]:
    """Preserve native operation receipts, never arbitrary response envelopes.

    These exact SDK routes return structured readback, provenance and cleanup
    evidence. Keep their complete result, including a late journal failure;
    truncating it can hide residual geometry and incorrectly suggest a retry.
    Command/PML/script results may contain source or login arguments and must
    not be admitted through a prefix match or generic result forwarding.
    """
    diagnostics = {"state": value["state"]} if isinstance(value.get("state"), dict) else {}
    # An ancestry read can fail after returning some native reference/context
    # evidence without latching a database fault. Preserve that bounded result
    # independently of the write-outcome diagnostic route set.
    if (path in _NATIVE_RESULT_DIAGNOSTIC_PATHS or path == "/api/v1/element/ancestry") and isinstance(value.get("result"), dict):
        diagnostics["result"] = value["result"]
    if path in _PARAGON_COMPONENT_PATHS:
        for key in ("version", "session_id", "bridge_build", "error_code"):
            if isinstance(value.get(key), str):
                diagnostics[key] = value[key]
        for key in ("write_attempted", "outcome_uncertain", "native_fault_latched", "retry_safe"):
            if type(value.get(key)) is bool or key == "write_attempted" and key in value and value[key] is None:
                diagnostics[key] = value[key]
        if isinstance(value.get("result"), dict):
            diagnostics["result"] = value["result"]
    if path in {"/api/v1/pml-script", "/api/v1/pml-script/status"}:
        for key in ("request_id", "session_id"):
            if isinstance(value.get(key), str):
                diagnostics[key] = value[key]
        for key in ("execution_attempted", "outcome_uncertain", "retry_safe"):
            if isinstance(value.get(key), bool):
                diagnostics[key] = value[key]
    if path == "/api/v1/messages/window":
        for key in ("version", "session_id", "bridge_build", "error_code"):
            if isinstance(value.get(key), str):
                diagnostics[key] = value[key]
        for key in ("managed_only", "operation_correlated"):
            if isinstance(value.get(key), bool):
                diagnostics[key] = value[key]
    if path == "/api/v1/paragon/catalogue-manifest":
        for key in ("version", "session_id", "bridge_build", "error_code"):
            if isinstance(value.get(key), str):
                diagnostics[key] = value[key]
        for key in ("native_fault_latched", "retry_safe"):
            if isinstance(value.get(key), bool):
                diagnostics[key] = value[key]
    if path == "/api/v1/piping/connectivity-snapshot":
        for key in ("version", "session_id", "bridge_build", "error_code"):
            if isinstance(value.get(key), str):
                diagnostics[key] = value[key]
        for key in ("native_fault_latched", "retry_safe"):
            if isinstance(value.get(key), bool):
                diagnostics[key] = value[key]
    if path == "/api/v1/equipment/child-schema":
        for key in ("version", "session_id", "bridge_build", "error_code"):
            if isinstance(value.get(key), str):
                diagnostics[key] = value[key]
        for key in ("native_fault_latched", "retry_safe"):
            if isinstance(value.get(key), bool):
                diagnostics[key] = value[key]
    if path == "/api/v1/piping/specification-read":
        for key in ("version", "session_id", "bridge_build", "error_code"):
            if isinstance(value.get(key), str):
                diagnostics[key] = value[key]
        for key in ("native_fault_latched", "retry_safe"):
            if isinstance(value.get(key), bool):
                diagnostics[key] = value[key]
    if path in {"/health", "/api/v1/database/team-identity-manifest", "/api/v1/module/context",
                "/api/v1/element/ancestry",
                "/api/v1/paragon/catalogue-manifest", "/api/v1/paragon/catalogue-schema",
                "/api/v1/paragon/component-template", "/api/v1/paragon/reference-trace",
                "/api/v1/piping/specification-read"} | _PARAGON_COMPONENT_PATHS:
        for key in ("version", "session_id", "bridge_build", "error_code"):
            if isinstance(value.get(key), str):
                diagnostics[key] = value[key]
        for key in ("has_fault", "native_fault", "native_database_fault", "native_fault_latched", "retry_safe"):
            if type(value.get(key)) is bool:
                diagnostics[key] = value[key]
        if value.get("code") in ("native_fault", "native_database_fault"):
            diagnostics["code"] = value["code"]
        # Only these exact read routes may carry additional native diagnostic
        # containers. Keep a fault-bearing container intact so nested SDK
        # evidence cannot be misclassified as a missing optional feature.
        # Arbitrary envelope fields and ordinary results stay private.
        for key in ("details", "diagnostics", "result", "native_fault", "native_database_fault"):
            if isinstance(value.get(key), (dict, list)) and _contains_native_fault(value[key]):
                diagnostics[key] = value[key]
    return diagnostics


def pml_script_utf8(source: Any) -> bytes:
    """Validate without normalizing or echoing potentially sensitive source."""
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source must be a non-empty PML script string")
    if "\x00" in source:
        raise ValueError("source must not contain NUL characters")
    if len(source) > PML_SCRIPT_MAX_BYTES:
        raise ValueError("source must not exceed 262144 UTF-8 bytes")
    try:
        encoded = source.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise ValueError("source must contain valid Unicode encodable as strict UTF-8") from None
    if len(encoded) > PML_SCRIPT_MAX_BYTES:
        raise ValueError("source must not exceed 262144 UTF-8 bytes")
    return encoded


class BridgeError(RuntimeError):
    """A bridge request failed or returned an invalid response."""

    def __init__(self, message: str, *, http_status: int | None = None, transport_error: bool = False,
                 response_invalid: bool = False, outcome_uncertain: bool = False,
                 diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.transport_error = transport_error
        self.response_invalid = response_invalid
        self.outcome_uncertain = outcome_uncertain
        self.diagnostics = diagnostics or {}


@dataclass
class BridgeClient:
    settings: Settings

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
        version: str | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        base_url, selected_version = self.settings.url_for(version)
        url = f"{base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            timeout = self.settings.bridge_timeout if timeout_seconds is None else min(self.settings.bridge_timeout, timeout_seconds)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            diagnostics = {}
            try:
                error_raw = exc.read()
            except (OSError, http.client.HTTPException) as body_exc:
                # HTTPError is itself raised before the normal response-body
                # read path. A truncated error body therefore escapes the
                # sibling HTTPException handler below unless it is wrapped
                # here. Preserve the status, but mark the body transport as
                # incomplete so mutating callers fail closed on the outcome.
                raise BridgeError(
                    f"E3D bridge request failed: HTTP {exc.code} with an incomplete error response",
                    http_status=exc.code, transport_error=True,
                ) from body_exc
            try:
                error_value = _strict_json_loads(error_raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError, RecursionError) as body_exc:
                raise BridgeError(
                    f"E3D bridge request failed: HTTP {exc.code} with an invalid JSON error response",
                    http_status=exc.code, response_invalid=True,
                ) from body_exc
            if not isinstance(error_value, dict):
                raise BridgeError(
                    f"E3D bridge request failed: HTTP {exc.code} error response must be a JSON object",
                    http_status=exc.code, response_invalid=True,
                ) from exc
            # Structured HTTP error bodies must declare an explicit bridge
            # failure. Keep the historical, unstructured proxy form (only an
            # error/message string) for gateways that generate their own body.
            proxy_error_keys = {"error", "message"}
            is_legacy_proxy_error = (
                "ok" not in error_value
                and bool(error_value)
                and set(error_value).issubset(proxy_error_keys)
                and all(isinstance(item, str) for item in error_value.values())
            )
            if not is_legacy_proxy_error:
                if type(error_value.get("ok")) is not bool or error_value["ok"] is not False:
                    raise BridgeError(
                        f"E3D bridge HTTP {exc.code} error response must contain ok: false",
                        http_status=exc.code, response_invalid=True,
                    ) from exc
                if not isinstance(error_value.get("error"), str):
                    raise BridgeError(
                        f"E3D bridge HTTP {exc.code} error response must contain a string error field",
                        http_status=exc.code, response_invalid=True,
                    ) from exc
            detail = str(error_value.get("error") or error_value.get("message") or "")
            diagnostics = _response_failure_diagnostics(path, error_value)
            suffix = f": {detail}" if detail else ""
            known_missing_transform_route = (method == "POST"
                and path == "/api/v1/element/transform/revisioned" and exc.code == 404)
            raise BridgeError(f"E3D bridge request failed: HTTP {exc.code}{suffix}", http_status=exc.code,
                              outcome_uncertain=(False if known_missing_transform_route else
                                  _uninformative_write_failure(method, path, payload, error_value)),
                              diagnostics=diagnostics) from exc
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            raise BridgeError(f"E3D bridge request failed: {exc}", transport_error=True) from exc
        try:
            value = _strict_json_loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise BridgeError("E3D bridge returned invalid JSON", response_invalid=True) from exc
        if not isinstance(value, dict):
            raise BridgeError("E3D bridge response must be a JSON object", response_invalid=True)
        if type(value.get("ok")) is not bool:
            raise BridgeError("E3D bridge response must contain a boolean ok field", response_invalid=True)
        if value.get("ok") is False:
            # Keep the success and failure envelopes symmetric.  HTTP error
            # responses already require a string ``error`` field; accepting a
            # null, number, or object here would turn a malformed bridge
            # response into an apparently authoritative operation failure and
            # could leak arbitrary object text through the MCP receipt.
            if not isinstance(value.get("error"), str):
                raise BridgeError(
                    "E3D bridge failure response must contain a string error field",
                    response_invalid=True,
                )
            raise BridgeError(value["error"],
                              outcome_uncertain=_uninformative_write_failure(method, path, payload, value),
                              diagnostics=_response_failure_diagnostics(path, value))
        # These source-bound replies carry native identity evidence. A configured fallback
        # must not make an absent native version look like an observation to
        # readiness/coherence gates. Other routes (including project, whose
        # native envelope lacks version) keep their existing routing metadata.
        if path not in {"/health", "/api/v1/messages/window", "/api/v1/equipment/child-schema",
                        "/api/v1/element/ancestry",
                        "/api/v1/piping/specification-read", "/api/v1/database/team-identity-manifest",
                        "/api/v1/module/context", "/api/v1/paragon/catalogue-manifest",
                        "/api/v1/paragon/catalogue-schema", "/api/v1/paragon/component-template",
                        "/api/v1/paragon/reference-trace"} | _PARAGON_COMPONENT_PATHS:
            value.setdefault("version", selected_version)
        return value

    def health(self, version: str | None = None) -> dict[str, Any]:
        if self.settings.mode == "mock":
            selected = self._selected_version(version)
            return self._mock_health(selected)
        return self._request("GET", "/health", version=version)

    def project(self, version: str | None = None) -> dict[str, Any]:
        if self.settings.mode == "mock":
            selected_version = self._selected_version(version)
            value = {
                "ok": True,
                "project": {
                    "name": "DEMO",
                    "mdb": "DEMO",
                    "current_element": "/WORLD",
                    "connected": True,
                },
            }
            value["version"] = selected_version
            return value
        return self._request("GET", "/api/v1/project", version=version)

    def spatial_map_status(self, database_number: int, extract_number: int, expected_context: str = "",
                           version: str | None = None) -> dict[str, Any]:
        parameters = spatial_map_status_parameters(database_number, extract_number, expected_context)
        selected = self._selected_version(version)
        if selected != "E3D21" or self.settings.mode == "mock":
            unsupported = selected != "E3D21"
            return {"ok": True, "version": selected, "result": {
                "success": False, "available": False, "complete": False,
                "source": "unsupported" if unsupported else "mock",
                "check_attempted": False, "check_completed": False,
                "status": None, "status_value": None, "messages": [],
                "read_errors": [{"path": "spatial_map_status",
                    "code": "unsupported_version" if unsupported else "native_unavailable",
                    "message": "Spatial map status is implemented only for E3D21." if unsupported else
                               "Native spatial map status is unavailable in mock mode."}],
            }}
        return self._request("GET", "/api/v1/spatial-map/status",
                             query={name: str(value) for name, value in parameters.items()}, version=selected)

    def query(self, expression: str, limit: int, version: str | None = None) -> dict[str, Any]:
        if self.settings.mode == "mock":
            selected_version = self._selected_version(version)
            value = self._mock_query(expression, limit)
            value["version"] = selected_version
            return value
        return self._request("POST", "/api/v1/query", {"expression": expression, "limit": limit}, version=version)

    def command(self, command: str, dry_run: bool, version: str | None = None,
                expected_session_id: str | None = None) -> dict[str, Any]:
        if self.settings.mode == "mock":
            selected_version = self._selected_version(version)
            return {
                "ok": True,
                "dry_run": dry_run,
                "command": command,
                "version": selected_version,
                "output": "mock command accepted; no E3D state was changed",
            }
        payload: dict[str, Any] = {"command": command, "dry_run": dry_run}
        if expected_session_id is not None:
            payload["expected_session_id"] = expected_session_id
        try:
            return self._request("POST", "/api/v1/command", payload, version=version)
        except BridgeError as exc:
            if dry_run or not exc.transport_error or "timed out" not in str(exc).lower():
                raise
            return self._recover_command_timeout(exc, version)

    def execute_pml_script(self, source: str, dry_run: bool = True, version: str | None = None,
                           expected_session_id: str | None = None, request_id: str | None = None) -> dict[str, Any]:
        encoded = pml_script_utf8(source)
        if not isinstance(dry_run, bool):
            raise ValueError("dry_run must be a boolean")
        if expected_session_id is not None and (not isinstance(expected_session_id, str) or not expected_session_id.strip()):
            raise ValueError("expected_session_id must be a non-empty string")
        if request_id is not None:
            request_id = pml_script_request_id(request_id)
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": False, "available": False, "mock": True, "dry_run": dry_run,
                "source_sha256": hashlib.sha256(encoded).hexdigest(), "byte_count": len(encoded),
                "source_echoed": False, "syntax_checked": False,
                "execution_attempted": False, "execution_completed": False,
                "execution_scope": None, "native_run_returned": None,
                "line_count": None, "encoding": None, "error_metadata": None,
                "outcome_uncertain": False, "retry_safe": False,
                "cleanup": {"attempted": False, "status": "not_needed", "retained": False, "path": None, "error": None},
                "error": "Native PML script encoding preview and execution require a running E3D bridge.",
            }}
        if not dry_run:
            request_id = request_id or str(uuid.uuid4())
            if expected_session_id is None:
                try:
                    health = self.health(version=version)
                except BridgeError as exc:
                    # This failure happened before POST. Do not let generic
                    # write recovery mistake a failed health read for a write.
                    raise BridgeError("Unable to establish the E3D session; the script was not submitted.",
                                      http_status=exc.http_status, diagnostics={
                                          **exc.diagnostics, "execution_attempted": False,
                                          "outcome_uncertain": False, "retry_safe": False,
                                      }) from exc
                expected_session_id = health.get("session_id") if isinstance(health, dict) else None
                if not isinstance(expected_session_id, str) or not expected_session_id.strip():
                    raise BridgeError("E3D health did not return a session_id; the script was not submitted.",
                                      diagnostics={"execution_attempted": False,
                                                   "outcome_uncertain": False, "retry_safe": False})
        payload: dict[str, Any] = {"source": source, "dry_run": dry_run}
        if expected_session_id is not None:
            payload["expected_session_id"] = expected_session_id
        if request_id is not None:
            payload["request_id"] = request_id
        try:
            return self._request("POST", "/api/v1/pml-script", payload, version=version)
        except BridgeError as exc:
            if not dry_run:
                lookup = {"request_id": request_id, "expected_session_id": expected_session_id,
                          "status_tool": "e3d_pml_script_status"}
                exc.diagnostics["pml_script_request"] = lookup
                if exc.transport_error or exc.response_invalid or exc.outcome_uncertain:
                    exc.diagnostics.update(outcome_uncertain=True, retry_safe=False)
            # A script may still be running, and its commands may not support
            # cancellation. Preserve the lookup IDs without inspecting command
            # state, cancelling, or replaying; callers can read the stored receipt.
            raise

    def pml_script_status(self, request_id: str, expected_session_id: str,
                          version: str | None = None) -> dict[str, Any]:
        request_id = pml_script_request_id(request_id)
        if not isinstance(expected_session_id, str) or not expected_session_id.strip():
            raise ValueError("expected_session_id must be a non-empty string")
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": False, "available": False, "mock": True, "request_id": request_id,
                "receipt_available": False, "receipt": None,
                "error": "PML script receipt lookup requires a running E3D bridge.",
            }}
        return self._request("GET", "/api/v1/pml-script/status", query={
            "request_id": request_id, "expected_session_id": expected_session_id,
        }, version=version)

    def find_descendants(self, root_refno: str = "", element_type: str | None = None,
                         name_contains: str | None = None, max_depth: int | None = None,
                         limit: int = 50, cursor: str = "", version: str | None = None) -> dict[str, Any]:
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": False, "available": False, "source": "mock",
                "complete": False, "scope_complete": False, "has_more": None,
                "next_cursor": None, "elements": [], "returned_count": 0, "visited_count": 0,
                "read_errors": ["Native descendant traversal is unavailable in mock mode."],
            }}
        payload: dict[str, Any] = {"root_refno": root_refno, "limit": limit, "cursor": cursor}
        for key, value in (("element_type", element_type), ("name_contains", name_contains), ("max_depth", max_depth)):
            if value is not None:
                payload[key] = value
        return self._request("POST", "/api/v1/elements/find-descendants", payload, version=version)

    def command_batch(
        self,
        commands: list[str],
        dry_run: bool,
        stop_on_error: bool = True,
        version: str | None = None,
    ) -> dict[str, Any]:
        if self.settings.mode == "mock":
            selected_version = self._selected_version(version)
            return {
                "ok": True,
                "dry_run": dry_run,
                "stop_on_error": stop_on_error,
                "version": selected_version,
                "results": [
                    {
                        "dry_run": dry_run,
                        "success": True,
                        "command": command,
                        "output": "mock command accepted; no E3D state was changed",
                    }
                    for command in commands
                ],
            }
        try:
            return self._request(
                "POST",
                "/api/v1/command-batch",
                {"commands": commands, "dry_run": dry_run, "stop_on_error": stop_on_error},
                version=version,
            )
        except BridgeError as exc:
            if dry_run or not exc.transport_error or "timed out" not in str(exc).lower():
                raise
            return self._recover_command_timeout(exc, version)

    def reenter_design(self, version: str | None = None, wait_seconds: float = 90.0) -> dict[str, Any]:
        """Save successfully, send DESIGN once, and prove a new matching session."""
        selected_version = self._selected_version(version)
        if self.settings.mode == "mock":
            return {
                "ok": True,
                "version": selected_version,
                "command": "DESIGN",
                "phase": "complete",
                "saved": {"ok": True, "result": {"success": True}},
                "design_attempted": True,
                "dispatch_uncertain": False,
                "reconnected": True,
                "project_ready": True,
                "previous_session_id": "mock-before",
                "health": {"session_id": "mock-after", "connected": True},
                "project": {"name": "DEMO", "mdb": "DEMO"},
            }

        result: dict[str, Any] = {
            "ok": False, "version": selected_version, "command": "DESIGN", "phase": "preflight",
            "saved": None, "design_attempted": False, "dispatch_uncertain": False,
            "reconnected": False, "project_ready": False,
        }

        def cached_csg_recovery(health, project_reply, state_reply):
            """Admit only the managed, idle CSG quarantine cache contract.

            Disconnected/unknown databases and native database faults do not gain
            a new recovery permission through this exception. Full matching
            quarantine snapshots tie all three observations to the same latch.
            """
            cached_source = "Cached identity from the last successful project read; no native database call was made."
            if not all(isinstance(value, dict) and value.get("ok") is True
                       for value in (health, project_reply, state_reply)):
                return None
            project = project_reply.get("project")
            health_state = health.get("state")
            command_state = state_reply.get("state")
            if (health.get("version") != "E3D21" or health.get("connected") is not False
                    or not isinstance(project, dict) or project.get("connected") is not False
                    or project.get("busy") is not False or project.get("recovery_only") is not True
                    or project.get("identity_source") != cached_source or project.get("current_element") is not None
                    or not all(isinstance(project.get(key), str) and bool(project[key].strip())
                               for key in ("name", "mdb"))
                    or not isinstance(health_state, dict) or not isinstance(command_state, dict)):
                return None
            quarantine = health_state.get("generated_csg_quarantine")
            if (not isinstance(quarantine, dict) or quarantine.get("active") is not True
                    or not all(isinstance(quarantine.get(key), str) and bool(quarantine[key].strip())
                               for key in ("stage", "reason", "source_refno", "observed_utc"))
                    or quarantine.get("scope") != "bridge_process"
                    or quarantine.get("retry_safe") is not False or quarantine.get("reset_available") is not False):
                return None
            for observation in (health_state, command_state, project):
                fault = observation.get("native_database_fault")
                if (observation.get("generated_csg_quarantine") != quarantine
                        or not isinstance(fault, dict) or fault.get("has_fault") is not False):
                    return None
            for observation in (health_state, command_state):
                if (observation.get("busy") is not False or observation.get("database_ready") is not False
                        or observation.get("recovery_required") is not True):
                    return None
            return quarantine

        try:
            before = self.health(version=version)
            original_reply = self.project(version=version)
            state_reply = self.command_state(version=version)
            original = original_reply.get("project", {})
            state = state_reply.get("state", {})
        except BridgeError as exc:
            result["error"] = "Cannot establish the original E3D session: " + str(exc)
            return result
        session_id = before.get("session_id")
        actual_version = before.get("version")
        quarantine = cached_csg_recovery(before, original_reply, state_reply)
        cached_recovery = quarantine is not None
        if ((before.get("connected") is not True and not cached_recovery)
                or not isinstance(session_id, str) or not session_id.strip()
                or not actual_version or (selected_version != "default" and actual_version != selected_version)
                or not isinstance(original, dict) or (original.get("connected") is not True and not cached_recovery)
                or original.get("busy") or not original.get("name") or not original.get("mdb")
                or not isinstance(state, dict) or state.get("busy") is not False):
            result["error"] = "An idle, connected E3D session with a session_id, project and MDB is required."
            return result
        if cached_recovery:
            result["recovery_scope"] = "matched_managed_csg_quarantine_cache"
            result["original_generated_csg_quarantine"] = quarantine
        result["previous_session_id"] = session_id
        result["expected_project"] = {key: original[key] for key in ("name", "mdb")}
        result["phase"] = "save"
        try:
            saved = self.command("SAVEWORK", False, version=version, expected_session_id=session_id)
        except BridgeError as exc:
            result["error"] = "SAVEWORK did not return confirmed success: " + str(exc)
            return result
        result["saved"] = saved
        if not self._command_succeeded(saved):
            result["error"] = "SAVEWORK did not succeed; DESIGN was not sent."
            return result

        # Check again after saving. Do not restart a replacement session or a
        # different project if the user/client changed context during SAVEWORK.
        try:
            check = self.health(version=version)
            check_project_reply = self.project(version=version)
            check_project = check_project_reply.get("project", {})
            check_quarantine = (cached_csg_recovery(check, check_project_reply, self.command_state(version=version))
                                if cached_recovery else None)
        except BridgeError as exc:
            result["error"] = "Session could not be rechecked after SAVEWORK: " + str(exc)
            return result
        if (check.get("session_id") != session_id
                or (check.get("connected") is not True and not cached_recovery)
                or check.get("version") != actual_version or not isinstance(check_project, dict)
                or (check_project.get("connected") is not True and not cached_recovery) or check_project.get("busy")
                or any(check_project.get(key) != original[key] for key in ("name", "mdb"))
                or cached_recovery and (check_quarantine is None or check_quarantine != quarantine)):
            result["error"] = "E3D session or project changed after SAVEWORK; DESIGN was not sent."
            return result

        result["phase"] = "dispatch"
        result["design_attempted"] = True
        try:
            # DESIGN tears down the adapter. Unlike an ordinary PML timeout,
            # a restart timeout must not trigger cancellation or command replay.
            dispatched = self._request("POST", "/api/v1/command",
                {"command": "DESIGN", "dry_run": False, "expected_session_id": session_id}, version=version)
            result["command_result"] = dispatched
            if not self._command_succeeded(dispatched):
                result["error"] = "DESIGN was rejected; inspect command_result."
                return result
        except BridgeError as exc:
            result["request_error"] = str(exc)
            if not exc.transport_error:
                result["error"] = "DESIGN request failed; no restart is confirmed."
                return result
            result["dispatch_uncertain"] = True

        result["phase"] = "wait"
        result["next_action"] = "Inspect e3d_status and e3d_project_info if readiness times out; do not automatically send DESIGN again."
        deadline = time.monotonic() + max(5.0, min(120.0, wait_seconds))
        while time.monotonic() < deadline:
            try:
                candidate = self.health(version=version)
                new_session = candidate.get("session_id")
                if (candidate.get("connected") is True and candidate.get("version") == actual_version
                        and isinstance(new_session, str) and new_session and new_session != session_id):
                    result["health"] = candidate
                    result["reconnected"] = True
                    current = self.project(version=version).get("project", {})
                    if (isinstance(current, dict) and current.get("connected") is True
                            and not current.get("busy") and not current.get("recovery_only")
                            and current.get("name") and current.get("mdb")):
                        result["project"] = current
                        if any(current[key] != original[key] for key in ("name", "mdb")):
                            result["error"] = "A new E3D session opened a different project or MDB."
                            return result
                        # The health/project reads must refer to the same new session.
                        confirmed = self.health(version=version)
                        if (confirmed.get("connected") is True and confirmed.get("session_id") == new_session
                                and confirmed.get("version") == actual_version
                                and isinstance(confirmed.get("state"), dict)
                                and confirmed["state"].get("database_ready") is True):
                            result.update(ok=True, phase="complete", project_ready=True, health=confirmed)
                            result.pop("next_action", None)
                            return result
            except BridgeError as exc:
                result["last_observation_error"] = str(exc)
            time.sleep(0.5)
        result["timeout"] = True
        result["error"] = "New E3D session with the original project and MDB was not confirmed before the readiness deadline."
        return result

    @staticmethod
    def _command_succeeded(value: dict[str, Any]) -> bool:
        operation = value.get("result")
        return value.get("ok") is True and isinstance(operation, dict) and operation.get("success") is True

    def _recover_command_timeout(self, exc: BridgeError, version: str | None) -> dict[str, Any]:
        """Attempt cancellation, then attach bounded, non-correlated diagnostics."""
        response = self._recover_command_timeout_state(exc, version)
        response["message_diagnostics"] = self._timeout_message_diagnostics(version)
        return response

    def _recover_command_timeout_state(self, exc: BridgeError, version: str | None) -> dict[str, Any]:
        response: dict[str, Any] = {
            **exc.diagnostics,
            "ok": False,
            "timeout": True,
            "outcome_uncertain": True,
            "retry_safe": False,
            "pml_input_cleared": None,
            "error": (f"{exc} Operation outcome is unknown; the original operation may already have taken effect. "
                      "Do not automatically resend the operation. Inspect command state first and only read back "
                      "the target after database_ready=true has been confirmed. Idle state or cancellation does not "
                      "prove that pending PML input was cleared."),
            "cancel_attempted": False,
        }
        try:
            initial = self.command_state(version=version)
            response["initial_state"] = initial.get("state", {})
            response["version"] = initial.get("version")
        except BridgeError as state_exc:
            response["state_error"] = str(state_exc)
            if state_exc.diagnostics:
                response["state_diagnostics"] = state_exc.diagnostics
            response["final_state"] = state_exc.diagnostics.get("state", response.get("state", {}))
            response["state"] = response["final_state"]
            return response

        state = response["initial_state"]
        if not isinstance(state, dict) or not state.get("busy", False):
            response["final_state"] = state if isinstance(state, dict) else {}
            response["state"] = response["final_state"]
            return response

        final_state = state
        response["cancel_attempted"] = True
        try:
            response["cancel"] = self.cancel_command(version=version)
            settled = response["cancel"].get("final_state", {})
            if isinstance(settled, dict) and "busy" in settled and not settled.get("busy", False):
                response["final_state"] = settled
                response["state"] = settled
                return response
        except BridgeError as cancel_exc:
            response["cancel_error"] = str(cancel_exc)
            if cancel_exc.diagnostics:
                response["cancel_diagnostics"] = cancel_exc.diagnostics
                if isinstance(cancel_exc.diagnostics.get("state"), dict):
                    final_state = cancel_exc.diagnostics["state"]

        # The adapter queues cancellation on E3D's UI thread. Poll briefly so
        # callers receive the settled state instead of having to press Esc.
        deadline = time.monotonic() + min(2.0, max(0.2, self.settings.bridge_timeout))
        while time.monotonic() < deadline:
            try:
                settled = self.command_state(version=version)
                candidate = settled.get("state", {})
                response["version"] = settled.get("version", response.get("version"))
                if isinstance(candidate, dict):
                    final_state = candidate
                    if not candidate.get("busy", False):
                        break
            except BridgeError as state_exc:
                response["state_error"] = str(state_exc)
                if state_exc.diagnostics:
                    response["state_diagnostics"] = state_exc.diagnostics
                    if isinstance(state_exc.diagnostics.get("state"), dict):
                        final_state = state_exc.diagnostics["state"]
                break
            time.sleep(0.1)
        response["final_state"] = final_state if isinstance(final_state, dict) else {}
        response["state"] = response["final_state"]
        return response

    def _timeout_message_diagnostics(self, version: str | None) -> dict[str, Any]:
        """Read the managed output ring once; never echo its arbitrary text."""
        limit = 20
        result: dict[str, Any] = {
            "available": False, "complete": False,
            "source": "E3D captured PdmsOutputEvents via /api/v1/messages",
            "scope": ("Latest available adapter messages observed after timeout. No operation or session cursor "
                      "is available: entries may be older or unrelated, and an empty tail does not prove no error occurred."),
            "operation_correlated": False, "requested_tail": limit,
            "returned_count": 0, "has_more": None,
            "raw_text_included": False,
            "text_policy": ("Only known message types, numeric error codes, fixed diagnostic categories and "
                            "Command/Form Input line numbers are returned. Raw descriptions, PML source, dates, "
                            "details and other fields are omitted to avoid echoing credentials."),
            "messages": [], "read_errors": [],
        }
        try:
            observed = self._request("GET", "/api/v1/messages", query={"tail": str(limit)},
                                     version=version, timeout_seconds=2.0)
        except BridgeError:
            # Exception text/HTTP error bodies can also contain arbitrary PML
            # text. Keep this diagnostic failure separate from the original
            # command error and never copy its untrusted message into receipts.
            result["read_errors"].append({"code": "message_read_failed", "error": "Recent E3D messages could not be read."})
            return result
        messages = observed.get("messages")
        if not isinstance(messages, list):
            result["read_errors"].append({"code": "invalid_messages", "error": "The adapter did not return a message array."})
            return result
        result["available"] = True
        if len(messages) > limit:
            result["read_errors"].append({"code": "message_limit_exceeded", "error": "The adapter returned more than the requested tail; only its last 20 entries are summarized."})
        for index, message in enumerate(messages[-limit:], 1):
            if not isinstance(message, dict):
                result["read_errors"].append({"code": "invalid_message", "tail_index": index,
                                              "error": "A message entry is not an object."})
                continue
            result["messages"].append(self._timeout_message_summary(message, index))
        result["returned_count"] = len(result["messages"])
        return result

    @staticmethod
    def _timeout_message_summary(message: dict[str, Any], index: int) -> dict[str, Any]:
        # Native state diagnostics redact credential-shaped keys and command
        # text. The message ring contains free text instead, so apply a stricter
        # allowlist rather than attempt to recognize every possible password.
        kind = message.get("type")
        kind = kind.upper() if isinstance(kind, str) and len(kind) <= 16 else "UNKNOWN"
        if kind not in {"ERROR", "WARNING", "SYSTEM", "MESSAGE", "INFO"}:
            kind = "UNKNOWN"
        row: dict[str, Any] = {
            "tail_index": index, "type": kind, "code": None,
            "category": "unclassified", "line_number": None,
        }
        description = message.get("description")
        details = message.get("details")
        # Bound parsing work as well as output. Only a leading native header is
        # interpreted; arbitrary trailing text remains excluded from the reply.
        header = description[:512] if isinstance(description, str) else ""
        code = re.match(r"\s*\(([0-9]{1,6}),\s*([0-9]{1,6})\)\s*", header)
        if code:
            row["code"] = [int(code.group(1)), int(code.group(2))]
            header = header[code.end():]
        if re.fullmatch(r"(?:CP:\s*)?Syntax error\s*", header, re.IGNORECASE):
            row["category"] = "syntax_error"
        elif re.fullmatch(r"(?:Unhandled\s+)?Database error\s+[0-9]{1,6}\s*", header, re.IGNORECASE):
            row["category"] = "database_error"
        context = details[:256] if isinstance(details, str) else ""
        line = re.match(r"\s*In line ([0-9]{1,8}) of Command/Form Input(?:\r?\n|$)", context)
        if line:
            row["line_number"] = int(line.group(1))
        return row

    def logs(self, tail: int, version: str | None = None) -> dict[str, Any]:
        if self.settings.mode == "mock":
            selected_version = self._selected_version(version)
            return {
                "ok": True,
                "source": "mock",
                "version": selected_version,
                "lines": ["[mock] E3D bridge is running", "[mock] no diagnostics recorded"],
            }
        return self._request("GET", "/api/v1/logs", query={"tail": str(tail)}, version=version)

    def messages(self, tail: int, version: str | None = None) -> dict[str, Any]:
        if self.settings.mode == "mock":
            selected_version = self._selected_version(version)
            return {
                "ok": True,
                "version": selected_version,
                "messages": [],
            }
        return self._request("GET", "/api/v1/messages", query={"tail": str(tail)}, version=version)

    def message_window(self, cursor: str = "", limit: int = 100, checkpoint: bool = False,
                       version: str | None = None) -> dict[str, Any]:
        query = message_window_parameters(cursor, limit, checkpoint)
        selected = self._selected_version(version)
        if self.settings.mode == "mock" or selected != "E3D21":
            return {"ok": True, "version": selected, "result": {
                "success": False, "available": False, "source": "mock" if self.settings.mode == "mock" else "unsupported",
                "native_validated": False, "status": "native_unavailable" if selected == "E3D21" else "unsupported_version",
                "error": "Session-bound message windows require an E3D21 adapter advertising message_cursor_v1.",
            }}
        try:
            response = self._request("GET", "/api/v1/messages/window", query=query, version=version)
        except BridgeError as exc:
            if exc.http_status is not None:
                exc.diagnostics["http_status"] = exc.http_status
            if exc.http_status in (404, 501):
                raise BridgeError("Native message cursors are unavailable; message_cursor_v1 is required. " + str(exc),
                                  http_status=exc.http_status, diagnostics={**exc.diagnostics,
                                      "available": False, "required_feature": "message_cursor_v1",
                                      "error_code": exc.diagnostics.get("error_code", "message_cursor_unavailable")}) from exc
            raise
        try:
            validate_message_window(response, version=selected, cursor=cursor, limit=limit, checkpoint=checkpoint)
        except ValueError as exc:
            raise BridgeError("Invalid native message window: " + str(exc), response_invalid=True,
                              diagnostics={"error_code": "message_window_response_invalid", "evidence": response}) from exc
        return response

    def command_state(self, version: str | None = None) -> dict[str, Any]:
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "state": {"busy": False, "command": ""}}
        return self._request("GET", "/api/v1/command-state", version=version)

    def cancel_command(self, version: str | None = None) -> dict[str, Any]:
        if self.settings.mode == "mock":
            return {
                "ok": True,
                "version": self._selected_version(version),
                "result": {"requested": True, "input_abort": True, "settled": True},
                "final_state": {"busy": False, "command": ""},
            }
        result = self._request("POST", "/api/v1/command-cancel", {}, version=version)
        final_state = self._wait_for_idle(version)
        result["final_state"] = final_state.get("state", {})
        state = result["final_state"]
        result["command_idle"] = state.get("busy") is False
        # Legacy bridges cannot prove native health or popup absence.
        result["database_ready"] = state.get("database_ready")
        result["settled"] = result["command_idle"] and state.get("database_ready") is True
        result["recovery_required"] = state.get("recovery_required")
        return result

    def _wait_for_idle(self, version: str | None = None) -> dict[str, Any]:
        deadline = time.monotonic() + min(2.0, max(0.2, self.settings.bridge_timeout))
        state = self.command_state(version=version)
        while bool(state.get("state", {}).get("busy", False)) and time.monotonic() < deadline:
            time.sleep(0.1)
            state = self.command_state(version=version)
        return state

    def drawlist(self, version: str | None = None) -> dict[str, Any]:
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "drawlist": {"available": True, "id": 0, "member_count": 0, "members": []}}
        return self._request("GET", "/api/v1/drawlist", version=version)

    def fit_view(
        self,
        shaded: bool = True,
        isometric: bool = True,
        auto_add_current: bool = True,
        version: str | None = None,
    ) -> dict[str, Any]:
        if self.settings.mode == "mock":
            return {
                "ok": True,
                "version": self._selected_version(version),
                "result": {
                    "success": True,
                    "shaded": shaded,
                    "isometric": isometric,
                    "auto_added_current": auto_add_current,
                },
            }
        return self._request(
            "POST",
            "/api/v1/view/fit",
            {"shaded": shaded, "isometric": isometric, "auto_add_current": auto_add_current},
            version=version,
        )

    def restore_view(self, fit: bool = True, version: str | None = None) -> dict[str, Any]:
        """Reload the persisted Drawlist after E3D recreated its view state."""
        if self.settings.mode == "mock":
            return {
                "ok": True,
                "version": self._selected_version(version),
                "result": {
                    "ready": True,
                    "retry": False,
                    "restored_count": 0,
                    "pending_count": 0,
                    "fit": fit,
                },
            }
        return self._request("POST", "/api/v1/view/restore", {"fit": fit}, version=version)

    def view_state(self, version: str | None = None) -> dict[str, Any]:
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": False, "available": False, "source": "mock",
                "error": "Native view state requires a running E3D bridge",
            }}
        return self._request("GET", "/api/v1/view/state", version=version)

    def view_clipping_state(self, version: str | None = None) -> dict[str, Any]:
        """Read the native View3D clipping state without changing the view."""
        selected = self._selected_version(version)
        if selected != "E3D21" or self.settings.mode == "mock":
            unsupported = selected != "E3D21"
            return {"ok": True, "version": selected, "result": {
                "success": False,
                "available": False,
                "complete": False,
                "status": "unsupported" if unsupported else "unavailable",
                "source": "unsupported" if unsupported else "mock",
                "error_code": "unsupported_version" if unsupported else "native_unavailable",
                "view_id": None,
                "clipping_enabled": None,
                "capping": None,
                "cap_colour": None,
                "box_data": None,
                "box_faces": [],
                "read_errors": {
                    "clipping": "View clipping state is implemented only for E3D21." if unsupported else
                                "Native view clipping state requires a running E3D bridge.",
                },
                "coordinate_frame": "SDK native (unverified)",
                "units": "SDK native (unverified)",
                "view_unchanged": None,
                "side_effects": (
                    f"No native view clipping state was read because {selected} is unsupported."
                    if unsupported else "No native view clipping state was read in mock mode."
                ),
            }}
        return self._request("GET", "/api/v1/view/clipping-state", version=selected)

    def graphical_selection(self, limit: int = 50, version: str | None = None) -> dict[str, Any]:
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": False, "available": False, "complete": False, "source": "mock", "limit": limit,
                "read_errors": [{"code": "native_unavailable", "message": "Native graphical selection requires a running E3D bridge."}],
            }}
        return self._request("GET", "/api/v1/view/graphical-selection", query={"limit": str(limit)}, version=version)

    def set_graphical_selection(self, refnos: list[str], dry_run: bool = True,
                                expected_revision: str | None = None,
                                version: str | None = None) -> dict[str, Any]:
        payload = graphical_selection_parameters(refnos, dry_run, expected_revision)
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": False, "available": False, "source": "mock", "dry_run": dry_run,
                "requested_refnos": payload["refnos"], "write_attempted": False,
                "readback_verified": False, "verification_state": "unavailable",
                "outcome_uncertain": False, "retry_safe": False,
                "error": "Native graphical selection editing requires a running E3D bridge.",
            }}
        try:
            return self._request("POST", "/api/v1/view/graphical-selection", payload, version=version)
        except BridgeError as exc:
            if dry_run:
                raise
            result = {"ok": False, "error": str(exc), **exc.diagnostics}
            if (exc.transport_error or exc.response_invalid or exc.outcome_uncertain
                    or not isinstance(exc.diagnostics.get("result"), dict)):
                result.update(outcome_uncertain=True, retry_safe=False, verification_state="unknown",
                              write_attempted=None, readback_verified=False)
                result["error"] += (" Selection write outcome is unknown; do not automatically resend, "
                                    "rollback or clear the selection.")
            if exc.response_invalid:
                result["response_invalid"] = True
            return result

    def observe_view(self, samples: int = 3, interval_ms: int = 100,
                     version: str | None = None, *, include_clipping: bool = False) -> dict[str, Any]:
        """Observe separate UI callbacks without blocking E3D's UI thread."""
        if not isinstance(include_clipping, bool):
            raise ValueError("include_clipping must be a boolean")
        if isinstance(samples, bool) or not isinstance(samples, int) or not 2 <= samples <= 10:
            raise ValueError("samples must be an integer between 2 and 10")
        if isinstance(interval_ms, bool) or not isinstance(interval_ms, int) or not 50 <= interval_ms <= 500:
            raise ValueError("interval_ms must be an integer between 50 and 500")
        errors: dict[str, str] = {}

        def read(label: str, operation: Any) -> Any:
            try:
                return operation(version=version)
            except Exception as exc:
                errors[label] = str(exc)
                return None

        context_before = read("context_before", self.health)
        states = []
        baseline_clipping = final_clipping = None
        for index in range(samples):
            if index:
                time.sleep(interval_ms / 1000.0)
            states.append(read(f"states[{index}]", self.view_state))
            if include_clipping and index == 0:
                baseline_clipping = read("baseline_clipping", self.view_clipping_state)
            elif include_clipping and index == samples - 1:
                final_clipping = read("final_clipping", self.view_clipping_state)
        context_after = read("context_after", self.health)
        if include_clipping:
            return {"ok": True, "version": self._selected_version(version), "result": view_observation_result(
                states, context_before, context_after, errors, include_clipping=True,
                baseline_clipping=baseline_clipping, final_clipping=final_clipping,
            )}
        return {"ok": True, "version": self._selected_version(version), "result": view_observation_result(
            states, context_before, context_after, errors,
        )}

    def set_view(self, preset: str, fit: str, target_refno: str, padding: float,
                 dry_run: bool, version: str | None = None) -> dict[str, Any]:
        payload = {"preset": preset, "fit": fit, "target_refno": target_refno,
                   "padding": padding, "dry_run": dry_run}
        if self.settings.mode == "mock":
            result = {"success": dry_run, "source": "mock", "mock": True,
                      "write_attempted": False, "write_applied": False, **payload}
            if not dry_run:
                result["error"] = "Native view changes require a running E3D bridge"
            return {"ok": True, "version": self._selected_version(version), "result": result}
        return self._request("POST", "/api/v1/view/set", payload, version=version)

    def set_camera(self, centre: list[float], direction: list[float], radius: float,
                   perspective: bool, extra_twist_degrees: float, *, expected_revision: str | None = None,
                   dry_run: bool = True, version: str | None = None) -> dict[str, Any]:
        payload = {"centre": centre, "direction": direction, "radius": radius,
                   "perspective": perspective, "extra_twist_degrees": extra_twist_degrees, "dry_run": dry_run}
        if expected_revision is not None:
            payload["expected_revision"] = expected_revision
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": False, "available": False, "source": "mock", "dry_run": dry_run,
                "write_attempted": False, "write_applied": False, "revision": None,
                "error": "Native camera editing requires a running E3D bridge",
            }}
        return self._request("POST", "/api/v1/view/camera/set", payload, version=version)

    def display_state(self, refno: str, version: str | None = None) -> dict[str, Any]:
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": False, "available": False, "complete": False, "source": "mock", "refno": refno,
                "pixel_visibility_verified": False,
                "error": "Native display ownership and primitive entries require a running E3D bridge",
            }}
        return self._request("GET", "/api/v1/view/element-state", query={"refno": refno}, version=version)

    def display_snapshot(self, version: str | None = None) -> dict[str, Any]:
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": False, "available": False, "complete": False,
                "source": "mock",
                "pixel_visibility_verified": False,
                "error": "Native display maps require a running E3D bridge",
            }}
        return self._request("GET", "/api/v1/view/display-snapshot", version=version)

    def set_primitive_visual(self, refno: str, *, colour: str | None = None,
                             transparency: int | None = None, edges: bool | None = None,
                             visibility: bool | None = None, shallow: bool | None = None,
                             dry_run: bool = True, expected_revision: str | None = None,
                             version: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"refno": refno, "dry_run": dry_run}
        for key, value in (("colour", colour), ("transparency", transparency), ("edges", edges),
                           ("visibility", visibility), ("shallow", shallow), ("expected_revision", expected_revision)):
            if value is not None:
                payload[key] = value
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": False, "available": False, "complete": False, "source": "mock", "mock": True,
                "native_validated": False, "write_attempted": False, "write_applied": False,
                "pixel_visibility_verified": False, **payload,
                "error": "Native existing-entry eligibility, visual revision validation and writes require a running E3D bridge",
            }}
        return self._request("POST", "/api/v1/view/primitive-visual", payload, version=version)

    def remove_drawlist_member(self, refno: str, *, expected_revision: str | None = None,
                               dry_run: bool = True, version: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"refno": refno, "dry_run": dry_run}
        if expected_revision is not None:
            payload["expected_revision"] = expected_revision
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": False, "available": False, "complete": False, "source": "mock", "mock": True,
                "native_validated": False, "write_attempted": False, "write_applied": False,
                "pixel_visibility_verified": False, **payload,
                "error": "Native exact Drawlist membership, removal revision validation and display removal require a running E3D bridge",
            }}
        return self._request("POST", "/api/v1/drawlist/remove", payload, version=version)

    def set_drawlist_visual(
        self,
        refno: str,
        colour: str,
        transparency: int | None,
        edges: bool | None,
        visibility: bool | None,
        shallow: bool | None,
        add: bool,
        dry_run: bool,
        version: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "refno": refno,
            "colour": colour,
            "transparency": transparency,
            "edges": edges,
            "visibility": visibility,
            "shallow": shallow,
            "add": add,
            "dry_run": dry_run,
        }
        if self.settings.mode == "mock":
            payload["success"] = True
            return {"ok": True, "version": self._selected_version(version), "result": payload}
        return self._request("POST", "/api/v1/drawlist/visual", payload, version=version)

    def element(self, refno: str, depth: int, limit: int, version: str | None = None,
                expected_type: str = "") -> dict[str, Any]:
        expected_type = normalize_expected_element_type(expected_type)
        if self.settings.mode == "mock":
            element: dict[str, Any] = {"refno": refno, "members": []}
            if expected_type:
                element["snapshot_info"] = {"complete": False, "truncated": False, "depth": depth,
                    "limit": limit, "element_count": 0, "truncation_reasons": [],
                    "read_errors": ["Mock mode cannot resolve typed element aliases or native hierarchy."]}
            else:
                element.update(type="EQUI", name=refno.strip("/"))
            return {"ok": True, "version": self._selected_version(version), "element": element}
        return self._request("GET", "/api/v1/element", query={"refno": refno, "expected_type": expected_type,
            "depth": str(depth), "limit": str(limit)}, version=version)

    def list_members(self, refno: str, cursor: str, limit: int, version: str | None = None,
                     expected_type: str = "") -> dict[str, Any]:
        expected_type = normalize_expected_element_type(expected_type)
        if self.settings.mode == "mock":
            if cursor:
                raise ValueError("Mock member lists are empty and do not issue continuation cursors")
            if expected_type:
                return {"ok": True, "version": self._selected_version(version), "result": {
                    "success": False, "parent": {"refno": refno or "/WORLD"}, "members": [],
                    "returned_count": 0, "has_more": None, "next_cursor": None, "limit": limit,
                    "consistency": "unavailable", "read_errors": ["Mock mode cannot resolve the requested parent type or native member list."],
                }}
            parent = {"refno": refno or "/WORLD"}
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": True, "parent": parent, "members": [], "returned_count": 0,
                "has_more": False, "next_cursor": None, "limit": limit, "consistency": "live", "read_errors": [],
            }}
        return self._request("GET", "/api/v1/element/members",
                             query={"refno": refno, "expected_type": expected_type, "cursor": cursor,
                                    "limit": str(limit)}, version=version)

    def read_attributes(self, refno: str, attributes: list[str], version: str | None = None,
                        *, expected_type: str = "") -> dict[str, Any]:
        expected_type = normalize_expected_element_type(expected_type)
        if self.settings.mode == "mock":
            if expected_type:
                return {"ok": True, "version": self._selected_version(version), "result": {
                    "success": False, "available": False, "source": "mock", "refno": refno,
                    "type": None, "values": {}, "typed_values": {},
                    "errors": {"$element": "Mock mode cannot resolve native element type aliases."},
                    "typed_read_errors": {},
                }}
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": True, "refno": refno, "type": "EQUI",
                "values": {name: "mock value" for name in attributes}, "errors": {},
            }}
        payload = {"refno": refno, "attributes": attributes}
        if expected_type:
            payload["expected_type"] = expected_type
        response = self._request("POST", "/api/v1/element/attributes", payload, version=version)
        return _verify_expected_element_type(response, expected_type, schema=False)

    def read_attributes_batch(self, refnos: list[str], attributes: list[str],
                              version: str | None = None) -> dict[str, Any]:
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": False, "complete": False, "available": False, "source": "mock",
                "requested_refnos": refnos, "attributes": attributes,
                "error": "Native batch attribute reads require a running E3D bridge.",
            }}
        return self._request("POST", "/api/v1/elements/attributes/batch",
                             {"refnos": refnos, "attributes": attributes}, version=version)

    def element_access(self, refnos: list[str], version: str | None = None) -> dict[str, Any]:
        references = element_access_refnos(refnos)
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": False, "available": False, "scan_complete": False,
                "all_values_available": False, "read_only": True, "source": "mock",
                "requested_refnos": references, "claims_freshness": "current_session_unverified",
                "claims_refresh_attempted": False,
                "read_errors": [{"code": "native_unavailable", "message": "Native element access reads require a running E3D bridge."}],
            }}
        return self._request("POST", "/api/v1/elements/access", {"refnos": references}, version=version)

    def element_ancestry(self, refno: str, max_depth: int = 32,
                         version: str | None = None) -> dict[str, Any]:
        payload = element_ancestry_parameters(refno, max_depth)
        selected = self._selected_version(version)
        if selected != "E3D21" or self.settings.mode == "mock":
            unsupported = selected != "E3D21"
            return {"ok": True, "version": selected, "result": {
                "success": False, "available": False, "complete": False, "read_only": True,
                "source": "unsupported" if unsupported else "mock", "requested_refno": refno,
                "max_depth": max_depth, "mdb_membership_complete": False,
                "reached_world": None, "truncated": None,
                "read_errors": [{"path": "element_ancestry", "code": "unsupported_version" if unsupported else "native_unavailable",
                    "message": "Element ancestry is implemented only for E3D21." if unsupported else
                               "Native element ancestry is unavailable in mock mode."}],
            }}
        return self._request("POST", "/api/v1/element/ancestry", payload, version=selected)

    def measure_elements_batch(self, refnos: list[str], measurements: list[str], wrt: str = "WORLD",
                               volume_scope: str = "element", version: str | None = None) -> dict[str, Any]:
        payload = {"refnos": refnos, "measurements": measurements, "wrt": wrt, "volume_scope": volume_scope}
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": False, "available": False, "complete": False, "source": "mock",
                "requested_refnos": refnos, "measurements": measurements, "wrt": wrt, "volume_scope": volume_scope,
                "read_errors": [{"code": "native_unavailable", "message": "Native element measurements require a running E3D bridge."}],
            }}
        return self._request("POST", "/api/v1/elements/measure", payload, version=version)

    def transform_coordinates(self, from_wrt: str, to_wrt: str, points: list[list[float]],
                              vectors: list[list[float]], version: str | None = None) -> dict[str, Any]:
        selected = self._selected_version(version)
        if selected != "E3D21" or self.settings.mode == "mock":
            unsupported = selected != "E3D21"
            return {"ok": True, "version": selected, "result": {
                "success": False, "available": False, "complete": False,
                "source": "unsupported" if unsupported else "mock",
                "from_wrt": from_wrt, "to_wrt": to_wrt, "points": [], "vectors": [],
                "read_errors": [{"path": "coordinates", "code": "unsupported_version" if unsupported else "native_unavailable",
                    "message": "Coordinate transforms are implemented only for E3D21." if unsupported else
                               "Native coordinate transforms are unavailable in mock mode."}],
            }}
        return self._request("POST", "/api/v1/coordinates/transform", {
            "from_wrt": from_wrt, "to_wrt": to_wrt, "points": points, "vectors": vectors,
        }, version=selected)

    def element_schema(self, refno: str, attributes: list[str], offset: int, limit: int,
                       version: str | None = None, *, include_constraints: bool = False,
                       expected_type: str = "") -> dict[str, Any]:
        if not isinstance(include_constraints, bool):
            raise ValueError("include_constraints must be a boolean")
        expected_type = normalize_expected_element_type(expected_type)
        if include_constraints:
            if not isinstance(attributes, list) or len(attributes) > 50 or any(
                not isinstance(name, str) or not name.strip() or len(name.strip()) > 64 or
                any(not (char.isalnum() or char in ':_') for char in name.strip()) for name in attributes
            ):
                raise ValueError("Constraint reads require explicitly named attributes")
            attributes = list(dict.fromkeys(name.strip().upper() for name in attributes))
            if not 1 <= len(attributes) <= 10:
                raise ValueError("Constraint reads require 1 to 10 explicitly named attributes")
            refno = element_access_refnos([refno])[0]
        if self.settings.mode == "mock":
            if expected_type:
                return {"ok": True, "version": self._selected_version(version), "result": {
                    "success": False, "available": False, "source": "mock",
                    "element": {"refno": refno, "type": None}, "element_type": None,
                    "child_types": [], "owner_types": [], "attributes": [],
                    "read_errors": {"element": "Mock mode cannot resolve native element type aliases."},
                }}
            names = attributes or ["NAME", "DESC"]
            rows = [{"requested_name": name, "name": name, "short_name": name,
                     "known": name in {"NAME", "DESC"}, "supported": name in {"NAME", "DESC"},
                     "data_type": "STRING", "sdk_settable_now": name in {"NAME", "DESC"},
                     "direct_edit_supported": name in {"NAME", "DESC"}}
                    for name in names[offset:offset + limit]]
            has_more = offset + len(rows) < len(names)
            if include_constraints:
                for row in rows:
                    row["constraints"] = {"status": "not_attempted", "reason": "native_unavailable"}
            result = {"ok": True, "version": self._selected_version(version), "result": {
                "success": True, "source": "mock", "element": {"refno": refno, "type": "EQUI"},
                "child_types": [], "owner_types": [], "attributes": rows,
                "attribute_selection": "requested" if attributes else "SDK GetAttributes",
                "attribute_page": {"offset": offset, "limit": limit, "returned_count": len(rows),
                                   "total_count": len(names), "has_more": has_more,
                                   "next_offset": offset + len(rows) if has_more else None},
                "read_errors": {},
            }}
            if include_constraints:
                result["result"]["constraints_requested"] = True
                result["result"]["constraints_complete"] = False
            return result
        payload = {"refno": refno, "attributes": attributes, "offset": offset, "limit": limit}
        if include_constraints:
            payload["include_constraints"] = True
        if expected_type:
            payload["expected_type"] = expected_type
        response = self._request("POST", "/api/v1/element/schema", payload, version=version)
        if include_constraints and response.get("ok") is True:
            result = response.get("result")
            if not isinstance(result, dict) or result.get("constraints_requested") is not True:
                response = {
                    **response,
                    "ok": False,
                    "error": "The selected bridge did not confirm attribute constraint support; update its E3D21 add-in.",
                }
        if expected_type:
            response = _verify_expected_element_type(response, expected_type, schema=True)
        return response

    def connection_points(self, refno: str, wrt: str = "WORLD", point_numbers: list[int] | None = None,
                          version: str | None = None, *, expected_type: str = "") -> dict[str, Any]:
        expected_type = normalize_expected_element_type(expected_type)
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": False, "available": False, "complete": False, "source": "mock",
                "refno": refno, "points": [],
                "error": "Native connection points require a running E3D bridge",
            }}
        payload = {"refno": refno, "wrt": wrt, "point_numbers": point_numbers if point_numbers is not None else []}
        if expected_type:
            payload["expected_type"] = expected_type
        response = self._request("POST", "/api/v1/element/connection-points", payload, version=version)
        return _verify_expected_element_type(response, expected_type, schema=True)

    def trace_connection_network(self, starts: list[dict[str, Any]], max_hops: int = 8,
                                 max_nodes: int = 64, version: str | None = None) -> dict[str, Any]:
        normalized_starts = []
        for start in starts:
            if not isinstance(start, dict):
                raise ValueError("starts must contain reference dictionaries")
            normalized = dict(start)
            if "expected_type" in normalized:
                normalized["expected_type"] = normalize_expected_element_type(normalized["expected_type"])
            normalized_starts.append(normalized)
        selected = self._selected_version(version)
        if selected != "E3D21" or self.settings.mode == "mock":
            unsupported = selected != "E3D21"
            return {"ok": True, "version": selected, "result": {
                "success": False, "available": False, "complete": False, "scope_complete": False,
                "source": "unsupported" if unsupported else "mock", "nodes": [], "edges": [],
                "read_errors": [{"code": "unsupported_version" if unsupported else "native_unavailable",
                    "message": "P-point connectivity tracing is implemented only for E3D21." if unsupported else
                               "Native P-point connectivity tracing is unavailable in mock mode."}],
            }}
        return self._request("POST", "/api/v1/element/connection-trace", {
            "starts": normalized_starts, "max_hops": max_hops, "max_nodes": max_nodes,
        }, version=selected)

    def connection_pair_check(self, first_refno: str, first_point_number: int,
                               second_refno: str, second_point_number: int, tolerance_mm: float,
                               version: str | None = None, *, first_expected_type: str = "",
                               second_expected_type: str = "") -> dict[str, Any]:
        first_expected_type = normalize_expected_element_type(first_expected_type)
        second_expected_type = normalize_expected_element_type(second_expected_type)
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": False, "available": False, "complete": False, "source": "mock",
                "status": "unavailable", "first_refno": first_refno, "first_point_number": first_point_number,
                "second_refno": second_refno, "second_point_number": second_point_number,
                "error": "Native connection-pair checks require a running E3D bridge",
            }}
        payload = {
            "first_refno": first_refno, "first_point_number": first_point_number,
            "second_refno": second_refno, "second_point_number": second_point_number,
            "tolerance_mm": tolerance_mm,
        }
        if first_expected_type:
            payload["first_expected_type"] = first_expected_type
        if second_expected_type:
            payload["second_expected_type"] = second_expected_type
        return self._request("POST", "/api/v1/element/connection-pair-check", payload, version=version)

    def database_identity_snapshot(self, expected_context: str,
                                    version: str | None = None) -> dict[str, Any]:
        selected = self._selected_version(version)
        if selected != "E3D21" or self.settings.mode == "mock":
            code = "unsupported_version" if selected != "E3D21" else "native_unavailable"
            message = ("Database identity snapshots are implemented only for E3D21." if selected != "E3D21"
                       else "Native database identity snapshots require a running E3D bridge")
            return {"ok": True, "version": selected, "result": {
                "success": False, "available": False, "complete": False,
                "source": "unsupported" if selected != "E3D21" else "mock",
                "expected_context": expected_context, "databases": [], "total_count": None,
                "returned_count": 0, "identity_snapshot_sha256": None,
                "read_errors": [{"path": "database_identity_snapshot", "code": code, "message": message}],
            }}
        return self._request("GET", "/api/v1/database/identity-snapshot",
                             query={"expected_context": expected_context}, version=selected)

    def spatial_info(self, refno: str, wrt: str, version: str | None = None) -> dict[str, Any]:
        if self.settings.mode == "mock":
            response = {"ok": True, "version": self._selected_version(version), "result": {
                "success": True, "complete": True, "errors": {},
                "refno": refno or "/WORLD", "type": "EQUI", "source": "mock",
                "frame": {"requested": wrt, "refno": "/WORLD" if wrt == "WORLD" else wrt},
                "position": {"available": True, "unit": "mm", "x": 0.0, "y": 0.0, "z": 0.0},
                "orientation": {"available": True, "x_axis": [1.0, 0.0, 0.0],
                                "y_axis": [0.0, 1.0, 0.0], "z_axis": [0.0, 0.0, 1.0]},
            }}
        else:
            response = self._request("GET", "/api/v1/element/spatial", query={"refno": refno, "wrt": wrt}, version=version)
        result = response.get("result")
        if not isinstance(result, dict):
            return response
        # Older adapters expose only nested availability. Keep every native
        # field, including complete and independently failed reads, unchanged.
        result = dict(result)
        for field in ("position", "orientation"):
            result.setdefault(field + "_complete", self._spatial_field_complete(result, field))
        return {**response, "result": result}

    @staticmethod
    def _spatial_field_complete(result: dict[str, Any], field: str) -> bool:
        value = result.get(field)
        errors = result.get("errors", {})
        if (not isinstance(value, dict) or value.get("available") is not True or
                not isinstance(errors, dict) or field in errors):
            return False

        def finite(component: Any) -> bool:
            # bool is an int subclass, but it is not an SDK coordinate.
            return type(component) is int or type(component) is float and math.isfinite(component)

        if field == "position":
            return value.get("unit") == "mm" and all(finite(value.get(axis)) for axis in ("x", "y", "z"))
        return all(isinstance(value.get(axis), list) and len(value[axis]) == 3 and
                   all(finite(component) for component in value[axis])
                   for axis in ("x_axis", "y_axis", "z_axis"))

    def validate(self, refno: str, depth: int, limit: int, required_types: list[str],
                 version: str | None = None, *, include_snapshot: bool = True) -> dict[str, Any]:
        if not isinstance(include_snapshot, bool):
            raise ValueError("include_snapshot must be a boolean")
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "validation": {
                "valid": None, "checked_elements_valid": None, "issues": [], "type_counts": {},
                "available": False, "source": "mock", "complete": False, "checks_complete": False,
                "snapshot": None, "snapshot_included": False, "requested_include_snapshot": include_snapshot,
                "read_errors": ["Native model validation requires a running E3D bridge."],
            }}
        return self._request("GET", "/api/v1/validate", query={
            "refno": refno, "depth": str(depth), "limit": str(limit),
            "required_types": ",".join(required_types), "include_snapshot": str(include_snapshot).lower(),
        }, version=version)

    def delete_element(self, refno: str, dry_run: bool, version: str | None = None) -> dict[str, Any]:
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {"success": True, "dry_run": dry_run, "refno": refno}}
        return self._request("POST", "/api/v1/element/delete", {"refno": refno, "dry_run": dry_run}, version=version)

    def set_attribute(self, refno: str, attribute: str, value: str, dry_run: bool, version: str | None = None) -> dict[str, Any]:
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {"success": True, "dry_run": dry_run, "refno": refno, "attribute": attribute, "new_value": value}}
        return self._request("POST", "/api/v1/element/attribute", {"refno": refno, "attribute": attribute, "value": value, "dry_run": dry_run}, version=version)

    def set_attributes(self, refno: str, values: dict[str, str], *, dry_run: bool = True,
                       expected_revision: str | None = None, version: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"refno": refno, "values": values, "dry_run": dry_run}
        if expected_revision is not None:
            payload["expected_revision"] = expected_revision
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": False, "available": False, "source": "mock", "mock": True,
                "native_validated": False, "write_attempted": False, "write_applied": False,
                "refno": refno, "values": values, "dry_run": dry_run,
                "error": "Native attribute preflight, revision validation and updates require a running E3D bridge",
            }}
        return self._request("POST", "/api/v1/element/attributes/set", payload, version=version)

    def create_element(self, parent_refno: str, element_type: str, name: str, attributes: dict[str, str],
                       position: list[float] | None, x_axis: list[float] | None, y_axis: list[float] | None,
                       wrt: str, dry_run: bool, version: str | None = None,
                       *, expected_revision: str | None = None) -> dict[str, Any]:
        payload = dict(parent_refno=parent_refno, element_type=element_type, name=name, attributes=attributes,
                       position=position, x_axis=x_axis, y_axis=y_axis, wrt=wrt, dry_run=dry_run)
        if expected_revision is not None:
            payload["expected_revision"] = expected_revision
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {"success": True, "mock": True, **payload}}
        return self._request("POST", "/api/v1/element/create", payload, version=version)

    def set_transform(self, refno: str, position: list[float] | None, x_axis: list[float] | None,
                      y_axis: list[float] | None, wrt: str, dry_run: bool, version: str | None = None,
                      *, expected_revision: str | None = None) -> dict[str, Any]:
        payload = dict(refno=refno, position=position, x_axis=x_axis, y_axis=y_axis, wrt=wrt, dry_run=dry_run)
        if not isinstance(dry_run, bool):
            raise ValueError("dry_run must be a boolean")
        if expected_revision is not None:
            if not isinstance(expected_revision, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_revision):
                raise ValueError("expected_revision must contain exactly 64 hexadecimal characters")
            payload["expected_revision"] = expected_revision.lower()
        elif not dry_run:
            raise ValueError("expected_revision from a fresh transform preview is required for execution")
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {"success": True, "mock": True, **payload}}
        # This route enforces the preview token natively. Never retry through
        # the legacy endpoint, which accepts no revision and would ignore it.
        return self._request("POST", "/api/v1/element/transform/revisioned", payload, version=version)

    def create_panel(self, parent_refno: str, vertices: list[list[float]], thickness: float, name: str,
                     position: list[float], x_axis: list[float], y_axis: list[float], wrt: str,
                     justification: str, dry_run: bool, version: str | None = None) -> dict[str, Any]:
        payload = dict(parent_refno=parent_refno, vertices=vertices, thickness=thickness, name=name,
                       position=position, x_axis=x_axis, y_axis=y_axis, wrt=wrt,
                       justification=justification, dry_run=dry_run)
        if self.settings.mode == "mock":
            result = {"success": dry_run, "source": "mock", "mock": True, "native_validated": False,
                      "write_attempted": False, "write_applied": False, **payload}
            if dry_run:
                result["warning"] = "Mock preview checks Python inputs only; native topology, capabilities and database constraints are unverified"
            else:
                result["error"] = "Native panel creation requires a running E3D bridge"
            return {"ok": True, "version": self._selected_version(version), "result": result}
        return self._request("POST", "/api/v1/panel/create", payload, version=version)

    def panel_geometry(self, refno: str, version: str | None = None, *, include_world: bool = False,
                       include_boundary: bool = False) -> dict[str, Any]:
        if not isinstance(include_world, bool):
            raise ValueError("include_world must be a boolean")
        if not isinstance(include_boundary, bool):
            raise ValueError("include_boundary must be a boolean")
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": False, "available": False, "source": "mock", "refno": refno,
                "error": "Native panel geometry requires a running E3D bridge",
            }}
        query = {"refno": refno}
        if include_world:
            query["include_world"] = "true"
        if include_boundary:
            query["include_boundary"] = "true"
        response = self._request("GET", "/api/v1/panel/geometry", query=query, version=version)
        if include_world and response.get("ok") is True:
            result = response.get("result")
            if not isinstance(result, dict) or result.get("world_positions_requested") is not True:
                raise BridgeError("The connected E3D adapter does not support native WORLD panel positions; update/reload the adapter before retrying.")
        if include_boundary and response.get("ok") is True:
            result = response.get("result")
            if not isinstance(result, dict) or result.get("boundary_points_requested") is not True:
                raise BridgeError("The connected E3D adapter does not support native PAVE boundary-point reads; update/reload the adapter before retrying.")
        return response

    def update_panel(self, refno: str, vertices: list[list[float]], *, allow_vertex_deletion: bool = False,
                     dry_run: bool = True, thickness: float | None = None, justification: str | None = None,
                     expected_revision: str | None = None, version: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = dict(refno=refno, vertices=vertices, allow_vertex_deletion=allow_vertex_deletion, dry_run=dry_run)
        if thickness is not None:
            payload["thickness"] = thickness
        if justification is not None:
            payload["justification"] = justification
        if expected_revision is not None:
            payload["expected_revision"] = expected_revision
        if self.settings.mode == "mock":
            result = {"success": dry_run, "source": "mock", "mock": True, "native_validated": False,
                      "write_attempted": False, "write_applied": False, **payload}
            if dry_run:
                result["warning"] = "Mock preview checks Python inputs only; native topology, revision and database constraints are unverified"
            else:
                result["error"] = "Native panel updates require a running E3D bridge"
            return {"ok": True, "version": self._selected_version(version), "result": result}
        return self._request("POST", "/api/v1/panel/update", payload, version=version)

    def primitive_geometry(self, refno: str, version: str | None = None) -> dict[str, Any]:
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": False, "available": False, "source": "mock", "refno": refno,
                "error": "Native primitive geometry requires a running E3D bridge",
            }}
        return self._request("GET", "/api/v1/element/primitive", query={"refno": refno}, version=version)

    def generated_csg(self, refno: str, *, max_trees: int = 8, max_items: int = 64,
                      version: str | None = None) -> dict[str, Any]:
        if not isinstance(refno, str) or not refno.strip():
            raise ValueError("generated CSG requires an explicit refno")
        if type(max_trees) is not int or not 1 <= max_trees <= 16:
            raise ValueError("max_trees must be an integer between 1 and 16")
        if type(max_items) is not int or not 1 <= max_items <= 256:
            raise ValueError("max_items must be an integer between 1 and 256")
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": False, "available": False, "complete": False, "source": "mock",
                "refno": refno, "trees": [],
                "error": "Native generated CSG requires a running E3D bridge",
            }}
        health = self.health(version=version)
        if health.get("ok") is not True or health.get("connected") is not True:
            return {"ok": False, "error": "Generated CSG requires a connected E3D bridge.", "health": health}
        features = health.get("api_features")
        if not isinstance(features, list) or "generated_csg" not in features:
            return {"ok": False, "error": "Reload the E3D21 add-in to enable generated CSG.",
                    "required_feature": "generated_csg"}
        session = health.get("session_id")
        if not isinstance(session, str) or not session:
            return {"ok": False, "error": "Generated CSG requires a bridge session identity."}
        response = self._request("GET", "/api/v1/element/generated-csg",
                                 query={"refno": refno, "max_trees": max_trees, "max_items": max_items},
                                 version=version)
        if response.get("ok") is True and response.get("session_id") != session:
            return {"ok": False, "error": "E3D session changed during generated CSG observation.",
                    "result": {"success": False, "available": False, "complete": False, "trees": []},
                    "expected_session_id": session, "observed_session_id": response.get("session_id")}
        return response

    def update_primitive(self, refno: str, *, dimensions: dict[str, float] | None = None,
                         position: list[float] | None = None, x_axis: list[float] | None = None,
                         y_axis: list[float] | None = None, wrt: str = "OWNER", dry_run: bool = True,
                         expected_revision: str | None = None, version: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = dict(refno=refno, wrt=wrt, dry_run=dry_run)
        for key, value in (("dimensions", dimensions), ("position", position), ("x_axis", x_axis),
                           ("y_axis", y_axis), ("expected_revision", expected_revision)):
            if value is not None:
                payload[key] = value
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": False, "available": False, "source": "mock", "mock": True, "native_validated": False,
                "write_attempted": False, "write_applied": False, **payload,
                "error": "Native primitive inspection, revision validation and updates require a running E3D bridge",
            }}
        return self._request("POST", "/api/v1/element/primitive", payload, version=version)

    def copy_element(self, source_refno: str, parent_refno: str, name: str, *,
                     position: list[float] | None = None, x_axis: list[float] | None = None,
                     y_axis: list[float] | None = None, wrt: str = "OWNER", dry_run: bool = True,
                     expected_revision: str | None = None, version: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = dict(source_refno=source_refno, parent_refno=parent_refno,
                                       name=name, wrt=wrt, dry_run=dry_run)
        for key, value in (("position", position), ("x_axis", x_axis), ("y_axis", y_axis),
                           ("expected_revision", expected_revision)):
            if value is not None:
                payload[key] = value
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": False, "available": False, "source": "mock", "mock": True, "native_validated": False,
                "write_attempted": False, "write_applied": False, **payload,
                "error": "Native copy eligibility, revision validation and copying require a running E3D bridge",
            }}
        return self._request("POST", "/api/v1/element/copy", payload, version=version)

    def geometry_bounds(self, refno: str, version: str | None = None) -> dict[str, Any]:
        if self.settings.mode == "mock":
            return {"ok": True, "result": {"success": False, "available": False, "error": "Geometry bounds require a running E3D bridge"}}
        return self._request("GET", "/api/v1/element/bounds", query={"refno": refno}, version=version)

    def geometry_relation(self, test_refno: str, against_refno: str, include_box: bool = False,
                          version: str | None = None) -> dict[str, Any]:
        selected = self._selected_version(version)
        if selected != "E3D21" or self.settings.mode == "mock":
            unsupported = selected != "E3D21"
            return {"ok": True, "version": selected, "result": {
                "success": False, "complete": False, "partial": False,
                "available": False, "test_refno": test_refno, "against_refno": against_refno,
                "include_box": include_box, "source": "unsupported" if unsupported else "mock",
                "read_errors": [{"code": "unsupported_version" if unsupported else "native_unavailable",
                                 "message": "Geometry relation is implemented only for E3D21." if unsupported else
                                            "Native geometry relation requires a running E3D bridge."}],
            }}
        return self._request("POST", "/api/v1/element/geometry-relation", {
            "test_refno": test_refno, "against_refno": against_refno, "include_box": include_box,
        }, version=selected)

    def geometry_relation_batch(self, pairs: list[tuple[str, str]], include_box: bool = False,
                                version: str | None = None) -> dict[str, Any]:
        selected = self._selected_version(version)
        if selected != "E3D21" or self.settings.mode == "mock":
            unsupported = selected != "E3D21"
            return {"ok": True, "version": selected, "result": {
                "success": False, "complete": False, "partial": False,
                "available": False, "requested_pair_count": len(pairs),
                "include_box": include_box,
                "source": "unsupported" if unsupported else "mock",
                "read_errors": [{"code": "unsupported_version" if unsupported else "native_unavailable",
                                 "message": "Geometry relation batch is implemented only for E3D21." if unsupported else
                                            "Native geometry relation batch requires a running E3D bridge."}],
            }}
        return self._request("POST", "/api/v1/element/geometry-relation-batch", {
            "pairs": [[test_refno, against_refno] for test_refno, against_refno in pairs],
            "include_box": include_box,
        }, version=selected)

    def native_clash_check(self, first_refnos: list[str], second_refnos: list[str], clearance: float = 0,
                           result_limit: int = 50, dry_run: bool = True,
                           version: str | None = None) -> dict[str, Any]:
        """Forward native group/map gates and evidence without inferring a clear verdict.

        EQUI/SUBE eligibility, bounded descendants, direct-primitive rejection
        for actual runs and UPTODATE/NOWARNINGS admission belong to the native adapter.
        Accepted map status remains distinct from exact UPTODATE and geometry validity.
        Its global diagnostic-setting changes are not restored by this client.
        """
        payload = native_clash_parameters(first_refnos, second_refnos, clearance, result_limit, dry_run)
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": False, "available": False, "mock": True, "dry_run": dry_run,
                "native_check_attempted": False, "native_check_returned": False, "native_check_result": None,
                "complete": False, "scope_complete": False, "no_clear_verdict": True,
                "global_diagnostic_state_unchanged": None,
                "error": "Native clash checks require a running E3D bridge; mock mode has no native classification evidence.",
            }}
        try:
            return self._request("POST", "/api/v1/clash/check", payload, version=version)
        except BridgeError as exc:
            if exc.transport_error or exc.response_invalid or exc.outcome_uncertain:
                # A native Check has no proven safe cancellation/replay path.
                # Keep unknown completion distinct from a known preview.
                exc.diagnostics.update({
                    "native_check_attempted": False if dry_run else None,
                    "native_check_returned": False if dry_run else None,
                    "native_check_result": None, "outcome_uncertain": not dry_run,
                    "retry_safe": False, "no_clear_verdict": True,
                    "global_diagnostic_state_unchanged": None,
                })
            raise

    def geometry_volume(self, refno: str, scope: str = "element", version: str | None = None) -> dict[str, Any]:
        if self.settings.mode == "mock":
            return {"ok": True, "version": self._selected_version(version), "result": {
                "success": False, "available": False, "source": "mock", "refno": refno, "scope": scope,
                "error": "Native geometry volume requires a running E3D bridge",
            }}
        return self._request("GET", "/api/v1/element/volume", query={"refno": refno, "scope": scope}, version=version)

    def capture_view(self, width: int, height: int, version: str | None = None) -> dict[str, Any]:
        if self.settings.mode == "mock":
            return {"ok": True, "result": {"success": False, "error": "Native view capture requires a running E3D bridge"}}
        errors: dict[str, str] = {}
        try:
            before = self.observe_view(samples=3, interval_ms=100, version=version)
        except Exception as exc:
            before = None
            errors["before"] = str(exc)
        # Capture exactly once. No observation failure may replay an earlier
        # operation, move the camera, refresh again, cancel or retry the image.
        response = self._request("GET", "/api/v1/view/capture",
                                 query={"width": str(width), "height": str(height)}, version=version)
        try:
            after = self.view_state(version=version)
        except Exception as exc:
            after = None
            errors["after"] = str(exc)
        try:
            context_after = self.health(version=version)
        except Exception as exc:
            context_after = None
            errors["context_after"] = str(exc)
        result = response.get("result")
        if isinstance(result, dict):
            response = {**response, "result": {**result, "observation": capture_observation_result(
                before, after, context_after, result.get("native_capture_observation"), errors,
            )}}
        return response

    def _mock_health(self, version: str) -> dict[str, Any]:
        return {
            "ok": True,
            "mode": "mock",
            "connected": True,
            "product": f"AVEVA E3D ({version}, mock)",
            "version": version,
        }

    def _selected_version(self, version: str | None) -> str:
        return self.settings.url_for(version)[1]

    @staticmethod
    def _mock_query(expression: str, limit: int) -> dict[str, Any]:
        sample = [
            {"refno": "/WORLD", "type": "WORLD", "name": "WORLD"},
            {"refno": "/SITE", "type": "SITE", "name": "SITE"},
            {"refno": "/SITE/PIPE-001", "type": "PIPE", "name": "PIPE-001"},
            {"refno": "/SITE/PIPE-001/BRAN-001", "type": "BRANCH", "name": "BRAN-001"},
        ]
        token = expression.strip().upper()
        if token and token not in {"*", "/", "/WORLD"}:
            sample = [
                item
                for item in sample
                if token in item["refno"].upper() or token in item["type"] or token in item["name"].upper()
            ]
        return {"ok": True, "expression": expression, "count": min(len(sample), limit), "elements": sample[:limit]}

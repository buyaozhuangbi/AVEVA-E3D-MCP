from __future__ import annotations

import json
import math
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any


JsonObject = dict[str, Any]
ToolHandler = Callable[[JsonObject], Any]
ResourceHandler = Callable[[], Any]
PromptHandler = Callable[[dict[str, str]], str]

# This stdio server currently implements the 2024-11-05 MCP protocol shape.
SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05",)


def _reject_nonstandard_json_constant(_: str) -> None:
    """Reject Python's NaN/Infinity extensions so stdio accepts strict JSON."""
    raise json.JSONDecodeError("Non-standard JSON numeric constant", "", 0)


def _finite_json_number(value: Any) -> bool:
    """Return finiteness without coercing arbitrary-precision integers to float."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return isinstance(value, int) or math.isfinite(value)


def _json_type_matches(value: Any, expected: Any) -> bool:
    """Match the JSON types used by the tool schemas (with bool distinct from number)."""
    if isinstance(expected, list):
        return any(_json_type_matches(value, item) for item in expected)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return _finite_json_number(value)
    if expected == "null":
        return value is None
    return True


def _json_equal(left: Any, right: Any) -> bool:
    """JSON value equality without Python's bool/int equivalence."""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, dict):
        return (left.keys() == right.keys()
                and all(_json_equal(left[key], right[key]) for key in left))
    return left == right


def _schema_matches(value: Any, schema: Any) -> bool:
    """Validate the JSON Schema subset used by registered tools.

    This deliberately stays dependency-free and supports the structural and
    common value constraints in our tool contracts, including conditional
    required fields expressed with allOf/if/then.
    """
    if isinstance(schema, bool):
        return schema
    if not isinstance(schema, dict):
        return True

    expected_type = schema.get("type")
    if expected_type is not None and not _json_type_matches(value, expected_type):
        return False
    if "enum" in schema and isinstance(schema["enum"], list):
        enum_match = any(_json_equal(value, option) for option in schema["enum"])
        # Only schemas that explicitly document handler normalization may
        # accept case/whitespace variants. Ordinary enum contracts stay
        # strict JSON Schema matches.
        if (not enum_match and schema.get("x-normalize") == "trim_casefold"
                and isinstance(value, str)):
            normalized = value.strip().casefold()
            enum_match = any(
                isinstance(option, str) and option.strip().casefold() == normalized
                for option in schema["enum"]
            )
        if not enum_match:
            return False
    if "const" in schema and not _json_equal(value, schema["const"]):
        return False

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not _finite_json_number(value):
            return False
        for keyword, predicate in (("minimum", lambda bound: value >= bound),
                                   ("maximum", lambda bound: value <= bound),
                                   ("exclusiveMinimum", lambda bound: value > bound),
                                   ("exclusiveMaximum", lambda bound: value < bound)):
            bound = schema.get(keyword)
            if isinstance(bound, (int, float)) and not isinstance(bound, bool):
                if not _finite_json_number(bound) or not predicate(bound):
                    return False

    if isinstance(value, str):
        if isinstance(schema.get("minLength"), int) and len(value) < schema["minLength"]:
            return False
        if isinstance(schema.get("maxLength"), int) and len(value) > schema["maxLength"]:
            return False
        # JSON Schema's maxLength counts characters, while some tool contracts
        # cap the exact UTF-8 representation sent to a native adapter.
        max_utf8_bytes = schema.get("x-maxUtf8Bytes")
        if isinstance(max_utf8_bytes, int) and not isinstance(max_utf8_bytes, bool):
            try:
                encoded = value.encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                return False
            if len(encoded) > max_utf8_bytes:
                return False
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            try:
                if re.search(pattern, value) is None:
                    return False
            except re.error:
                # Treat malformed schema patterns as a failed validation rather
                # than silently accepting caller input.
                return False
        if schema.get("format") == "uuid" and re.fullmatch(
                r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
                value) is None:
            return False

    if isinstance(value, list):
        if isinstance(schema.get("minItems"), int) and len(value) < schema["minItems"]:
            return False
        if isinstance(schema.get("maxItems"), int) and len(value) > schema["maxItems"]:
            return False
        if schema.get("uniqueItems") is True:
            for index, item in enumerate(value):
                if any(_json_equal(item, prior) for prior in value[:index]):
                    return False
        items = schema.get("items")
        if isinstance(items, list):
            if len(value) > len(items) and schema.get("additionalItems") is False:
                return False
            if any(not _schema_matches(item, item_schema)
                   for item, item_schema in zip(value, items)):
                return False
        elif items is not None and any(not _schema_matches(item, items) for item in value):
            return False

    if isinstance(value, dict):
        if isinstance(schema.get("minProperties"), int) and len(value) < schema["minProperties"]:
            return False
        if isinstance(schema.get("maxProperties"), int) and len(value) > schema["maxProperties"]:
            return False
        required = schema.get("required")
        if isinstance(required, list) and any(isinstance(key, str) and key not in value for key in required):
            return False
        properties = schema.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        property_names = schema.get("propertyNames")
        if property_names is not None and any(
                not _schema_matches(key, property_names) for key in value):
            return False
        for key, property_schema in properties.items():
            if key in value and not _schema_matches(value[key], property_schema):
                return False
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key not in properties:
                if additional is False or not _schema_matches(item, additional):
                    return False

    all_of = schema.get("allOf")
    if isinstance(all_of, list) and any(not _schema_matches(value, sub_schema) for sub_schema in all_of):
        return False
    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and not any(_schema_matches(value, sub_schema) for sub_schema in any_of):
        return False
    one_of = schema.get("oneOf")
    if isinstance(one_of, list) and sum(_schema_matches(value, sub_schema) for sub_schema in one_of) != 1:
        return False
    not_schema = schema.get("not")
    if not_schema is not None and _schema_matches(value, not_schema):
        return False
    if "if" in schema:
        branch = "then" if _schema_matches(value, schema["if"]) else "else"
        if branch in schema and not _schema_matches(value, schema[branch]):
            return False
    return True


def _tool_schema_failure_message(name: str, arguments: JsonObject, schema: JsonObject) -> str:
    """Preserve common validation text while rejecting before handler dispatch."""
    required = schema.get("required")
    if isinstance(required, list):
        for key in required:
            if isinstance(key, str) and key not in arguments:
                return f"{key} is required"
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for key, property_schema in properties.items():
            if key not in arguments or not isinstance(property_schema, dict):
                continue
            value = arguments[key]
            if property_schema.get("type") == "integer":
                if not _json_type_matches(value, "integer") or not _schema_matches(value, property_schema):
                    if "minimum" in property_schema and "maximum" in property_schema:
                        return (f"{key} must be an integer between {property_schema['minimum']} "
                                f"and {property_schema['maximum']}")
                    return f"{key} must be an integer"
            if property_schema.get("type") == "string" and isinstance(value, str):
                minimum, maximum = property_schema.get("minLength"), property_schema.get("maxLength")
                if (isinstance(minimum, int) and len(value) < minimum or
                        isinstance(maximum, int) and len(value) > maximum):
                    if isinstance(minimum, int) and isinstance(maximum, int):
                        return f"{key} must be between {minimum} and {maximum} characters"
                    if isinstance(maximum, int):
                        return f"{key} must contain at most {maximum} characters"
                    return f"{key} must contain at least {minimum} characters"
    return f"Invalid {name} arguments"


def _tool_argument_failure(name: str, arguments: JsonObject, message: str) -> JsonObject:
    """Build the established tool-error envelope, including capture receipts."""
    failure: JsonObject = {"isError": True, "content": [{"type": "text", "text": message}]}
    capture_width = arguments.get("capture_width", 1280)
    capture_height = arguments.get("capture_height", 960)
    valid_capture_size = all(
        type(size) is int and 64 <= size <= 2048
        for size in (capture_width, capture_height)
    )
    if arguments.get("capture_after") is True and valid_capture_size:
        failure["structuredContent"] = {
            "ok": False, "error": message,
            "receipt": {
                "operation_replayed": False,
                "capture": {
                    "success": False, "status": "skipped", "reason": "operation_failed",
                    "requested": True,
                    "width": capture_width,
                    "height": capture_height,
                },
            },
        }
    return failure


@dataclass(frozen=True)
class ImageToolResult:
    data: str
    mime_type: str
    metadata: JsonObject


@dataclass(frozen=True)
class MultiImageToolResult:
    images: list[tuple[str, str, str]]
    metadata: JsonObject


class McpServer:
    """Small stdio MCP implementation for the stable tools used by this service."""

    def __init__(self, name: str, version: str, instructions: str = "") -> None:
        self.name = name
        self.version = version
        self.instructions = instructions
        self._tools: dict[str, tuple[JsonObject, ToolHandler]] = {}
        self._resources: dict[str, tuple[JsonObject, ResourceHandler]] = {}
        self._prompts: dict[str, tuple[JsonObject, PromptHandler]] = {}

    def add_resource(
        self,
        uri: str,
        name: str,
        description: str,
        mime_type: str,
        handler: ResourceHandler,
    ) -> None:
        if not uri or not name or not mime_type:
            raise ValueError("resource uri, name, and mime_type must be non-empty")
        if uri in self._resources:
            raise ValueError(f"Duplicate resource URI: {uri}")
        self._resources[uri] = (
            {
                "uri": uri,
                "name": name,
                "title": name.replace("_", " ").title(),
                "description": description,
                "mimeType": mime_type,
            },
            handler,
        )

    def add_prompt(
        self,
        name: str,
        description: str,
        arguments: list[JsonObject],
        handler: PromptHandler,
        *,
        title: str | None = None,
    ) -> None:
        if not isinstance(name, str) or not name or not isinstance(description, str):
            raise ValueError("prompt name and description must be valid strings")
        if name in self._prompts:
            raise ValueError(f"Duplicate prompt name: {name}")
        if not callable(handler):
            raise ValueError("prompt handler must be callable")
        if not isinstance(arguments, list) or any(not isinstance(argument, dict) for argument in arguments):
            raise ValueError("prompt arguments must be a list of objects")
        argument_names = [argument.get("name") for argument in arguments]
        if any(not isinstance(argument_name, str) or not argument_name for argument_name in argument_names):
            raise ValueError("prompt arguments require non-empty names")
        if any(not isinstance(argument.get("description"), str) or
               not isinstance(argument.get("required"), bool) for argument in arguments):
            raise ValueError("prompt arguments require string descriptions and boolean required flags")
        if len(set(argument_names)) != len(argument_names):
            raise ValueError("prompt argument names must be unique")
        descriptor: JsonObject = {"name": name, "description": description, "arguments": arguments}
        if title:
            descriptor["title"] = title
        self._prompts[name] = (descriptor, handler)

    def add_tool(
        self,
        name: str,
        description: str,
        input_schema: JsonObject,
        handler: ToolHandler,
        *,
        read_only: bool = False,
        destructive: bool = True,
        open_world: bool = False,
        capture_receipt: bool = False,
    ) -> None:
        if capture_receipt:
            input_schema = dict(input_schema)
            properties = dict(input_schema.get("properties", {}))
            properties.update({
                "capture_after": {"type": "boolean", "default": False,
                                  "description": "After a confirmed successful operation, return a native E3D image receipt."},
                "capture_width": {"type": "integer", "minimum": 64, "maximum": 2048, "default": 1280},
                "capture_height": {"type": "integer", "minimum": 64, "maximum": 2048, "default": 960},
            })
            input_schema["properties"] = properties
        self._tools[name] = (
            {
                "name": name,
                "description": description,
                "inputSchema": input_schema,
                "annotations": {
                    "readOnlyHint": read_only,
                    "destructiveHint": False if read_only else destructive,
                    "idempotentHint": read_only,
                    "openWorldHint": open_world,
                },
            },
            handler,
        )

    def serve(self, input_stream: Iterable[str] | None = None, *, enforce_lifecycle: bool | None = None) -> None:
        """Serve MCP stdio, enforcing lifecycle by default for the real stdio transport.

        Supplying an in-memory stream is a test hook; callers that use a custom
        transport should explicitly pass ``enforce_lifecycle=True``.
        """
        if enforce_lifecycle is None:
            enforce_lifecycle = input_stream is None
        stream = sys.stdin if input_stream is None else input_stream
        # In-memory streams are a compatibility/test hook: callers may pass
        # Python-generated JSON containing NaN/Infinity to exercise handler
        # validation. The real stdio transport must use strict JSON parsing.
        loads = (json.loads if input_stream is not None else
                 lambda value: json.loads(value, parse_constant=_reject_nonstandard_json_constant))
        lifecycle = {"initialize_accepted": False, "initialized": False} if enforce_lifecycle else None
        for line in stream:
            if not line.strip():
                continue
            request: Any = None
            try:
                request = loads(line)
                response = self._dispatch(request, lifecycle=lifecycle)
            except json.JSONDecodeError as exc:
                response = self._error(None, -32700, f"Parse error: {exc.msg}")
            except Exception as exc:  # Keep the stdio process alive for the next request.
                request_id = request.get("id") if isinstance(request, dict) else None
                response = self._error(request_id, -32603, str(exc))
            if response is not None:
                # JSON escapes preserve every string while keeping the wire
                # encodable on strict UTF-8 stdout, including an escaped lone
                # surrogate received in a request id or diagnostic string.
                # Such input must not terminate the process before the next
                # request. Clients decode the original text from the escapes.
                try:
                    wire = json.dumps(response, ensure_ascii=True, separators=(",", ":"),
                                      allow_nan=False)
                except (TypeError, ValueError, OverflowError):
                    # A handler must never put Python's NaN/Infinity
                    # extensions (or another non-JSON value) on the stdio
                    # wire.  Keep the process alive and report a protocol
                    # failure for this request so the next frame can still be
                    # consumed.
                    response_id = response.get("id") if isinstance(response, dict) else None
                    wire = json.dumps(
                        self._error(response_id, -32603,
                                    "Response cannot be encoded as strict JSON"),
                        ensure_ascii=True, separators=(",", ":"), allow_nan=False,
                    )
                sys.stdout.write(wire + "\n")
                sys.stdout.flush()

    def _dispatch(self, request: Any, *, lifecycle: dict[str, bool] | None = None) -> JsonObject | None:
        if not isinstance(request, dict):
            return self._error(None, -32600, "Request must be a JSON object")
        if request.get("jsonrpc") != "2.0":
            return self._error(None, -32600, 'jsonrpc must be "2.0"')
        method = request.get("method")
        request_id = request.get("id")
        if not isinstance(method, str):
            return self._error(None, -32600, "Request method must be a string")
        has_id = "id" in request
        if has_id and not self._valid_request_id(request_id):
            return self._error(None, -32600, "Request id must be an integer or string")
        if method in ("notifications/initialized", "notifications/cancelled") and has_id:
            return self._error(request_id, -32600, "Notification must not include an id")
        # Notifications never receive responses. The initialized notification
        # advances only this stdio session after a successful initialize reply.
        # Cancellation is deliberately a no-op: this serialized transport
        # observes it after the current handler completes. A global E3D CANCEL
        # cannot be correlated to the originating request or undo its writes.
        if not has_id:
            if method == "notifications/initialized" and lifecycle is not None:
                params = request.get("params", {})
                if lifecycle["initialize_accepted"] and isinstance(params, dict):
                    lifecycle["initialized"] = True
            return None
        params = request.get("params", {})
        if not isinstance(params, dict):
            return self._error(request_id, -32602, "Request params must be an object")
        if lifecycle is not None:
            if method == "initialize" and lifecycle["initialize_accepted"]:
                return self._error(request_id, -32600, "MCP session has already been initialized")
            if method not in {"initialize", "ping"} and not lifecycle["initialized"]:
                return self._error(request_id, -32600,
                                   "MCP session requests are unavailable until initialization completes")
        if method == "initialize":
            # MCP initialize parameters are a handshake contract, not a
            # best-effort hint. Do not mark the session accepted until every
            # required field has been validated; otherwise a malformed
            # initialize followed by notifications/initialized unlocks tools.
            requested = params.get("protocolVersion")
            if not isinstance(requested, str):
                return self._error(request_id, -32602, "initialize protocolVersion must be a string")
            capabilities = params.get("capabilities")
            if not isinstance(capabilities, dict):
                return self._error(request_id, -32602, "initialize capabilities must be an object")
            client_info = params.get("clientInfo")
            if (not isinstance(client_info, dict)
                    or not isinstance(client_info.get("name"), str)
                    or not isinstance(client_info.get("version"), str)):
                return self._error(request_id, -32602,
                                   "initialize clientInfo must contain string name and version")
            negotiated = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[-1]
            if lifecycle is not None:
                lifecycle["initialize_accepted"] = True
            return self._result(
                request_id,
                {
                    "protocolVersion": negotiated,
                    "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                    "serverInfo": {"name": self.name, "version": self.version},
                    "instructions": self.instructions,
                },
            )
        if method == "ping":
            return self._result(request_id, {})
        if method == "tools/list":
            return self._result(request_id, {"tools": [definition for definition, _ in self._tools.values()]})
        if method == "resources/list":
            return self._result(request_id, {"resources": [definition for definition, _ in self._resources.values()]})
        if method == "resources/read":
            uri = params.get("uri")
            if not isinstance(uri, str) or not uri:
                return self._error(request_id, -32602, "resources/read requires a non-empty URI")
            resource = self._resources.get(uri)
            if resource is None:
                return self._error(request_id, -32002, f"Resource not found: {uri}")
            definition, handler = resource
            try:
                value = handler()
                text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
                return self._result(request_id, {"contents": [{
                    "uri": uri,
                    "mimeType": definition["mimeType"],
                    "text": text,
                }]})
            except Exception as exc:
                return self._error(request_id, -32603, str(exc))
        if method == "prompts/list":
            return self._result(request_id, {"prompts": [definition for definition, _ in self._prompts.values()]})
        if method == "prompts/get":
            name = params.get("name")
            if not isinstance(name, str) or not name:
                return self._error(request_id, -32602, "prompts/get requires a non-empty prompt name")
            if set(params) - {"name", "arguments", "_meta"}:
                return self._error(request_id, -32602, "prompts/get accepts only name, arguments and _meta")
            if "_meta" in params and not isinstance(params["_meta"], dict):
                return self._error(request_id, -32602, "Prompt _meta must be an object")
            prompt = self._prompts.get(name)
            if prompt is None:
                return self._error(request_id, -32602, "Unknown prompt")
            definition, handler = prompt
            raw_arguments = params.get("arguments", {})
            if not isinstance(raw_arguments, dict) or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in raw_arguments.items()
            ):
                return self._error(request_id, -32602, "Prompt arguments must be an object of string values")
            declared = {argument["name"] for argument in definition["arguments"]}
            if set(raw_arguments) - declared:
                return self._error(request_id, -32602, "Unknown prompt argument")
            missing = [argument["name"] for argument in definition["arguments"]
                       if argument.get("required") and argument["name"] not in raw_arguments]
            if missing:
                return self._error(request_id, -32602, "Required prompt arguments are missing")
            try:
                text = handler(raw_arguments)
                if not isinstance(text, str):
                    raise TypeError("Prompt handler must return text")
                return self._result(request_id, {
                    "description": definition["description"],
                    "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
                })
            except Exception as exc:
                if isinstance(exc, ValueError):
                    return self._error(request_id, -32602, str(exc))
                return self._error(request_id, -32603, str(exc))
        if method == "tools/call":
            if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                return self._error(request_id, -32602, "tools/call requires a tool name")
            name = params["name"]
            tool = self._tools.get(name)
            if tool is None:
                return self._error(request_id, -32602, f"Unknown tool: {name}")
            arguments = params.get("arguments", {})
            if not isinstance(arguments, dict):
                return self._error(request_id, -32602, "Tool arguments must be an object")
            input_schema = tool[0].get("inputSchema")
            if isinstance(input_schema, dict) and input_schema.get("additionalProperties") is False:
                properties = input_schema.get("properties", {})
                allowed = set(properties) if isinstance(properties, dict) else set()
                if set(arguments) - allowed:
                    # Tool schemas are the public input contract. Enforce
                    # closed top-level objects before handlers can trigger
                    # side effects; keep the diagnostic generic so arbitrary
                    # caller-supplied keys are not echoed.
                    message = f"{name} takes no arguments" if not allowed else f"Unknown {name} argument"
                    return self._result(request_id, _tool_argument_failure(name, arguments, message))
            if isinstance(input_schema, dict) and not _schema_matches(arguments, input_schema):
                message = _tool_schema_failure_message(name, arguments, input_schema)
                return self._result(request_id, _tool_argument_failure(name, arguments, message))
            try:
                value = tool[1](arguments)
                if isinstance(value, MultiImageToolResult):
                    content = [{"type": "text", "text": json.dumps(value.metadata, ensure_ascii=False, indent=2)}]
                    for label, data, mime_type in value.images:
                        content.extend(({"type": "text", "text": label},
                                        {"type": "image", "data": data, "mimeType": mime_type}))
                    result = {"content": content, "structuredContent": value.metadata}
                    if self._operation_failed(value.metadata):
                        result["isError"] = True
                    return self._result(request_id, result)
                if isinstance(value, ImageToolResult):
                    result = {
                        "content": [
                            {"type": "text", "text": json.dumps(value.metadata, ensure_ascii=False, indent=2)},
                            {"type": "image", "data": value.data, "mimeType": value.mime_type},
                        ],
                        "structuredContent": value.metadata,
                    }
                    if self._operation_failed(value.metadata):
                        result["isError"] = True
                    return self._result(request_id, result)
                text = json.dumps(value, ensure_ascii=False, indent=2)
                result = {"content": [{"type": "text", "text": text}], "structuredContent": value}
                if self._operation_failed(value):
                    result["isError"] = True
                return self._result(request_id, result)
            except Exception as exc:
                failure = {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
                diagnostics = getattr(exc, "diagnostics", None)
                if isinstance(diagnostics, dict) and diagnostics:
                    failure["structuredContent"] = {**diagnostics, "ok": False, "error": str(exc)}
                return self._result(
                    request_id,
                    failure,
                )
        return self._error(request_id, -32601, f"Method not found: {method}")

    @staticmethod
    def _valid_request_id(value: Any) -> bool:
        return isinstance(value, (str, int)) and not isinstance(value, bool)

    @staticmethod
    def _operation_failed(value: Any) -> bool:
        """Report execution failures while preserving per-command diagnostics.

        Model findings (validation.valid) and captured historical messages are
        data, not failures of the inspection tool itself.
        """
        if not isinstance(value, dict):
            return False
        if value.get("ok") is False or value.get("success") is False:
            return True
        operation = value.get("result")
        if isinstance(operation, dict) and operation.get("success") is False:
            return True
        batch = value.get("results")
        return isinstance(batch, list) and any(
            isinstance(item, dict) and item.get("success") is False for item in batch
        )

    @staticmethod
    def _result(request_id: Any, result: JsonObject) -> JsonObject:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> JsonObject:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

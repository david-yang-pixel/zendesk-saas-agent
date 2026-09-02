"""Read-only MCP Streamable HTTP tool adapter for the sandbox runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

import httpx
from pydantic import JsonValue

from casework_agent_core.runtime_config import SecretResolver
from casework_tool_sdk.registry import ToolExecutionContext
from contracts.tool import ToolDefinition, ToolResult, ToolResultStatus


class McpToolConfigurationError(ValueError):
    """MCP endpoint configuration or credential resolution failed."""


class McpProtocolError(RuntimeError):
    """MCP returned an invalid or unsuccessful JSON-RPC response."""


class McpStreamableHttpTool:
    """Invoke one remote MCP tool using Streamable HTTP JSON-RPC.

    The adapter performs ``initialize`` once per sandbox process and then sends
    ``tools/call`` requests.  The caller's ticket and session identity are
    intentionally not injected into MCP arguments: the configured remote tool
    schema is the source of truth.  Credentials are resolved just-in-time and
    used only in the HTTP Authorization header.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        name: str,
        description: str,
        input_schema: dict[str, JsonValue],
        endpoint_url: str,
        remote_tool_name: str,
        credential_ref: str,
        resolver: SecretResolver,
        timeout_seconds: float = 20.0,
        protocol_version: str = "2025-03-26",
    ) -> None:
        if not endpoint_url.startswith("https://"):
            raise McpToolConfigurationError("MCP endpoint_url must use HTTPS")
        if not remote_tool_name.strip():
            raise McpToolConfigurationError("MCP remote_tool_name is required")
        if not credential_ref.strip():
            raise McpToolConfigurationError("MCP credential_ref is required")
        if timeout_seconds <= 0:
            raise McpToolConfigurationError("MCP timeout_seconds must be positive")
        self._client = client
        self._endpoint_url = endpoint_url
        self._remote_tool_name = remote_tool_name
        self._credential_ref = credential_ref
        self._resolver = resolver
        self._timeout_seconds = timeout_seconds
        self._protocol_version = protocol_version
        self._session_id: str | None = None
        self._initialized = False
        self._next_request_id = 0
        self._initialize_lock = asyncio.Lock()
        self.definition = ToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema,
            read_only=True,
        )

    async def ready(self) -> bool:
        """Validate credentials and MCP initialization without calling a tool."""
        try:
            await self._ensure_initialized()
        except (McpToolConfigurationError, McpProtocolError, httpx.HTTPError):
            return False
        return True

    async def __call__(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        del context
        await self._ensure_initialized()
        response, _headers = await self._request(
            "tools/call",
            {
                "name": self._remote_tool_name,
                "arguments": arguments,
            },
        )
        if "error" in response:
            return self._failure_result("MCP tool call failed")
        raw_result = response.get("result")
        if not isinstance(raw_result, Mapping):
            raise McpProtocolError("MCP tools/call response is missing result")
        result = cast(Mapping[str, Any], raw_result)
        if bool(result.get("isError")):
            return self._normalize_failure(result)
        return self._normalize_result(result)

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            response, headers = await self._request(
                "initialize",
                {
                    "protocolVersion": self._protocol_version,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "zendesk-casework-sandbox",
                        "version": "0.1.0",
                    },
                },
            )
            if "error" in response:
                raise McpProtocolError("MCP initialize failed")
            if not isinstance(response.get("result"), Mapping):
                raise McpProtocolError("MCP initialize response is missing result")
            session_id = headers.get("mcp-session-id")
            self._session_id = session_id.strip() if session_id else None
            self._initialized = True

    async def _request(
        self, method: str, params: dict[str, Any]
    ) -> tuple[dict[str, Any], httpx.Headers]:
        self._next_request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._next_request_id,
            "method": method,
            "params": params,
        }
        secret = self._credential()
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {secret}",
            "Mcp-Protocol-Version": self._protocol_version,
        }
        if self._session_id is not None:
            headers["Mcp-Session-Id"] = self._session_id
        try:
            response = await self._client.post(
                self._endpoint_url,
                headers=headers,
                json=request,
                timeout=self._timeout_seconds,
            )
            if not response.is_success:
                raise McpProtocolError("MCP HTTP request failed")
            payload = _parse_mcp_response(response)
        except McpProtocolError:
            raise
        except (httpx.HTTPError, ValueError) as error:
            raise McpProtocolError("MCP HTTP request failed") from error
        if payload.get("jsonrpc") != "2.0":
            raise McpProtocolError("MCP response is not JSON-RPC 2.0")
        if payload.get("id") != request["id"]:
            raise McpProtocolError("MCP response id does not match request")
        return payload, response.headers

    def _credential(self) -> str:
        secret = self._resolver.resolve(self._credential_ref)
        if secret is None or not secret.strip():
            raise McpToolConfigurationError("MCP credential unavailable")
        return secret

    def _normalize_result(self, result: Mapping[str, Any]) -> ToolResult:
        secret = self._credential()
        safe_result = _redact(result, secret)
        safe_mapping = _mapping(safe_result)
        raw_content = safe_mapping.get("content", [])
        content = (
            cast(list[JsonValue], raw_content) if isinstance(raw_content, list) else []
        )
        text_parts = _text_parts(content)
        summary = " ".join(part for part in text_parts if part).strip()
        if not summary:
            summary = "MCP tool completed"
        data: dict[str, JsonValue] = {}
        if content:
            data["content"] = content
        structured = safe_mapping.get("structuredContent")
        if structured is not None:
            data["structured_content"] = cast(JsonValue, structured)
        fingerprint = json.dumps(
            {"tool": self._remote_tool_name, "result": safe_result},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        evidence_id = f"mcp-{hashlib.sha256(fingerprint).hexdigest()[:24]}"
        return ToolResult(
            status=ToolResultStatus.SUCCESS,
            summary=summary,
            data=data or None,
            evidence_id=evidence_id,
            observed_at=datetime.now(UTC),
        )

    def _normalize_failure(self, result: Mapping[str, Any]) -> ToolResult:
        secret = self._credential()
        safe_mapping = _mapping(_redact(result, secret))
        raw_content = safe_mapping.get("content", [])
        content = (
            cast(list[JsonValue], raw_content) if isinstance(raw_content, list) else []
        )
        summary = " ".join(_text_parts(content)).strip()
        if not summary:
            summary = "MCP tool call returned an error"
        data: dict[str, JsonValue] | None = None
        if content:
            data = {"content": content}
        structured = safe_mapping.get("structuredContent")
        if structured is not None:
            data = data or {}
            data["structured_content"] = cast(JsonValue, structured)
        return ToolResult(
            status=ToolResultStatus.FAILURE,
            summary=summary,
            data=data,
            observed_at=datetime.now(UTC),
        )

    @staticmethod
    def _failure_result(summary: str) -> ToolResult:
        return ToolResult(
            status=ToolResultStatus.FAILURE,
            summary=summary,
            observed_at=datetime.now(UTC),
        )


def _parse_mcp_response(response: httpx.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "").casefold()
    if "text/event-stream" in content_type:
        payload: dict[str, Any] | None = None
        for line in response.text.splitlines():
            if not line.startswith("data:"):
                continue
            data = line.removeprefix("data:").strip()
            if not data or data == "[DONE]":
                continue
            decoded = json.loads(data)
            if not isinstance(decoded, dict):
                raise McpProtocolError("MCP SSE data is not an object")
            payload = cast(dict[str, Any], decoded)
        if payload is None:
            raise McpProtocolError("MCP SSE response has no JSON-RPC message")
        return payload
    try:
        payload = response.json()
    except ValueError as error:
        raise McpProtocolError("MCP response is not JSON") from error
    if not isinstance(payload, dict):
        raise McpProtocolError("MCP response is not an object")
    return payload


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return cast(Mapping[str, Any], value)


def _redact(value: object, secret: str) -> JsonValue:
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Mapping):
        mapping_value = cast(Mapping[object, object], value)
        return {str(key): _redact(item, secret) for key, item in mapping_value.items()}
    if isinstance(value, list):
        list_value = cast(list[object], value)
        return [_redact(item, secret) for item in list_value]
    return "[REDACTED]"


def _text_parts(content: list[JsonValue]) -> tuple[str, ...]:
    parts: list[str] = []
    for value in content:
        block = _mapping(value)
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(cast(str, block["text"]))
            continue
        resource = _mapping(block.get("resource"))
        if isinstance(resource.get("text"), str):
            parts.append(cast(str, resource["text"]))
    return tuple(parts)


__all__ = [
    "McpProtocolError",
    "McpStreamableHttpTool",
    "McpToolConfigurationError",
]

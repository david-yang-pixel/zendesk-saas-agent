from __future__ import annotations

import json

import httpx
import pytest

from casework_agent_core.runtime_config import MappingSecretResolver
from casework_sandbox.mcp import McpStreamableHttpTool
from casework_tool_sdk.registry import ToolExecutionContext
from contracts.tool import ToolResultStatus


def _context() -> ToolExecutionContext:
    return ToolExecutionContext(
        ticket_id=3726,
        session_id="session-1",
        root_session_id="session-1",
    )


def _tool(
    client: httpx.AsyncClient,
    *,
    resolver: MappingSecretResolver | None = None,
    remote_tool_name: str = "workspace_lookup",
) -> McpStreamableHttpTool:
    return McpStreamableHttpTool(
        client=client,
        name="workspace_lookup",
        description="Find a workspace",
        input_schema={"type": "object"},
        endpoint_url="https://mcp.example.com/mcp",
        remote_tool_name=remote_tool_name,
        credential_ref="mcp/tools",
        resolver=resolver or MappingSecretResolver({"mcp/tools": "secret-token"}),
    )


@pytest.mark.asyncio
async def test_mcp_tool_initializes_then_calls_remote_tool_with_bearer() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        if payload["method"] == "initialize":
            return httpx.Response(
                200,
                headers={"Mcp-Session-Id": "mcp-session-1"},
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {"protocolVersion": "2025-03-26"},
                },
            )
        assert payload["method"] == "tools/call"
        assert payload["params"] == {
            "name": "workspace_lookup",
            "arguments": {"email": "user@example.com"},
        }
        assert request.headers["authorization"] == "Bearer secret-token"
        assert request.headers["mcp-session-id"] == "mcp-session-1"
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "content": [{"type": "text", "text": "Found workspace ws-1"}],
                    "structuredContent": {"workspace_id": "ws-1"},
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        tool = _tool(client)
        result = await tool({"email": "user@example.com"}, _context())
    finally:
        await client.aclose()

    assert result.status is ToolResultStatus.SUCCESS
    assert result.evidence_id is not None
    assert result.summary == "Found workspace ws-1"
    assert result.data == {
        "content": [{"type": "text", "text": "Found workspace ws-1"}],
        "structured_content": {"workspace_id": "ws-1"},
    }
    assert len(requests) == 2
    assert requests[0].headers["accept"] == "application/json, text/event-stream"
    assert requests[0].headers["content-type"] == "application/json"
    assert b"secret-token" not in requests[0].content
    assert b"3726" not in requests[1].content


@pytest.mark.asyncio
async def test_mcp_tool_handles_sse_and_initializes_once() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        methods.append(payload["method"])
        if payload["method"] == "initialize":
            message: dict[str, object] = {
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {},
            }
            body = f"event: message\ndata: {json.dumps(message)}\n\n"
            return httpx.Response(
                200,
                headers={
                    "content-type": "text/event-stream",
                    "Mcp-Session-Id": "mcp-session-2",
                },
                content=body.encode(),
            )
        message = {
            "jsonrpc": "2.0",
            "id": payload["id"],
            "result": {"content": [{"type": "text", "text": "ok"}]},
        }
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=f"event: message\ndata: {json.dumps(message)}\n\n".encode(),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        tool = _tool(client)
        first = await tool({}, _context())
        second = await tool({}, _context())
    finally:
        await client.aclose()

    assert first.status is ToolResultStatus.SUCCESS
    assert second.status is ToolResultStatus.SUCCESS
    assert methods == ["initialize", "tools/call", "tools/call"]


@pytest.mark.asyncio
async def test_mcp_tool_never_returns_remote_error_as_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "initialize":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {},
                },
            )
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "error": {"code": -32001, "message": "backend unavailable"},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await _tool(client)({}, _context())
    finally:
        await client.aclose()

    assert result.status is ToolResultStatus.FAILURE
    assert result.evidence_id is None
    assert result.summary == "MCP tool call failed"
    assert result.data is None


@pytest.mark.asyncio
async def test_mcp_tool_preserves_sanitized_tool_error_for_model_repair() -> None:
    remote_error = json.dumps(
        {
            "success": False,
            "code": "QUERY_ERROR",
            "error": 'invalid input syntax for type uuid: "{{app_id}}"',
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": payload["id"], "result": {}},
            )
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": remote_error}],
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await _tool(client)({}, _context())
    finally:
        await client.aclose()

    assert result.status is ToolResultStatus.FAILURE
    assert result.summary == remote_error
    assert result.data == {
        "content": [{"type": "text", "text": remote_error}],
    }


@pytest.mark.asyncio
async def test_mcp_tool_rejects_a_json_rpc_response_for_another_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"] + 100,
                "result": {},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        assert await _tool(client).ready() is False
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_mcp_tool_redacts_credential_if_remote_service_echoes_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": payload["id"], "result": {}},
            )
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "content": [{"type": "text", "text": "token=secret-token"}],
                    "structuredContent": {"token": "secret-token"},
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await _tool(client)({}, _context())
    finally:
        await client.aclose()

    rendered = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
    assert "secret-token" not in rendered
    assert "[REDACTED]" in rendered


@pytest.mark.asyncio
async def test_mcp_tool_ready_only_initializes_and_missing_secret_fails_closed() -> (
    None
):
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        methods.append(payload["method"])
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": payload["id"], "result": {}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        ready_tool = _tool(client)
        assert await ready_tool.ready() is True
        assert methods == ["initialize"]
        missing = _tool(
            client, resolver=MappingSecretResolver({"other": "not-this-tool"})
        )
        assert await missing.ready() is False
    finally:
        await client.aclose()

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from zd_agent.integrations.tool_service_mcp import call_mcp_tool


REAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.mark.asyncio
async def test_call_mcp_tool_uses_streamable_http_and_bearer_auth() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content.decode("utf-8"))
        assert body["method"] == "tools/call"
        assert body["params"]["name"] == "example_tool"
        assert body["params"]["arguments"] == {"x": 1}
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {
                    "structuredContent": {"success": True, "data": {"ok": 1}},
                    "isError": False,
                },
            },
        )

    transport = httpx.MockTransport(handler)
    with patch(
        "zd_agent.integrations.tool_service_mcp.httpx.AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    ):
        result = await call_mcp_tool(
            service="example-service",
            mcp_url="https://ops.example.com/example-service",
            mcp_api_key="secret",
            request_timeout_seconds=35,
            name="example_tool",
            arguments={"x": 1},
            missing_code="EXAMPLE_URL_MISSING",
        )

    assert result == {"success": True, "data": {"ok": 1}}
    assert str(requests[0].url) == "https://ops.example.com/example-service/mcp"
    assert requests[0].headers["authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_call_mcp_tool_parses_sse_text_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        result_text = json.dumps({"success": True, "output": "ok"})
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            text=(
                "event: message\n"
                f'data: {{"jsonrpc":"2.0","id":{body["id"]},"result":{{'
                f'"content":[{{"type":"text","text":{json.dumps(result_text)}}}],'
                '"isError":false}}\n\n'
            ),
        )

    transport = httpx.MockTransport(handler)
    with patch(
        "zd_agent.integrations.tool_service_mcp.httpx.AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    ):
        result = await call_mcp_tool(
            service="example-service",
            mcp_url="https://ops.example.com/example-service/mcp",
            mcp_api_key="secret",
            request_timeout_seconds=35,
            name="example_tool",
            arguments={},
            missing_code="EXAMPLE_URL_MISSING",
        )

    assert result == {"success": True, "output": "ok"}


@pytest.mark.asyncio
async def test_call_mcp_tool_returns_structured_transport_errors() -> None:
    result = await call_mcp_tool(
        service="github-mcp",
        mcp_url="",
        mcp_api_key="secret",
        request_timeout_seconds=35,
        name="github_search_prs",
        arguments={},
        missing_code="GITHUB_MCP_URL_MISSING",
    )
    assert result["success"] is False
    assert result["code"] == "GITHUB_MCP_URL_MISSING"

    transport = httpx.MockTransport(lambda _request: httpx.Response(401, text="no"))
    with patch(
        "zd_agent.integrations.tool_service_mcp.httpx.AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    ):
        result = await call_mcp_tool(
            service="github-mcp",
            mcp_url="https://ops.example.com/github-mcp",
            mcp_api_key="bad",
            request_timeout_seconds=35,
            name="github_search_prs",
            arguments={},
            missing_code="GITHUB_MCP_URL_MISSING",
        )
    assert result["success"] is False
    assert result["code"] == "GITHUB_MCP_UNAUTHORIZED"

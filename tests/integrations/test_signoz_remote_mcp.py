"""Tests for SigNoz remote MCP transport."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from zd_agent.integrations.signoz_mcp import run_signoz_sz


REAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.mark.asyncio
async def test_run_signoz_sz_uses_remote_streamable_http() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content.decode("utf-8"))
        assert body["method"] == "tools/call"
        assert body["params"]["name"] == "sz_run"
        assert body["params"]["arguments"] == {
            "args": ["errors", "--env", "production", "--limit", "3"],
        }
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "success": True,
                                    "data": [{"dep": "dify-api-oltp", "body": "boom"}],
                                },
                            ),
                        },
                    ],
                    "isError": False,
                },
            },
        )

    cfg = SimpleNamespace(
        signoz=SimpleNamespace(
            mcp_url="https://ops.example.com/signoz-mcp",
            mcp_api_key="secret",
            request_timeout_seconds=35,
        ),
    )

    transport = httpx.MockTransport(handler)
    with patch(
        "zd_agent.integrations.signoz_mcp.httpx.AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    ):
        result = await run_signoz_sz(
            "errors",
            "--env",
            "production",
            "--limit",
            "3",
            cfg=cfg,
        )

    assert result == [{"dep": "dify-api-oltp", "body": "boom"}]
    assert len(requests) == 1
    assert str(requests[0].url) == "https://ops.example.com/signoz-mcp/mcp"
    assert requests[0].headers["authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_run_signoz_sz_parses_sse_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        result_text = json.dumps({"success": True, "data": [{"ok": 1}]})
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

    cfg = SimpleNamespace(
        signoz=SimpleNamespace(
            mcp_url="https://ops.example.com/signoz-mcp/mcp",
            mcp_api_key="secret",
            request_timeout_seconds=35,
        ),
    )

    transport = httpx.MockTransport(handler)
    with patch(
        "zd_agent.integrations.signoz_mcp.httpx.AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    ):
        result = await run_signoz_sz("query", "SELECT 1", cfg=cfg)

    assert result == [{"ok": 1}]


@pytest.mark.asyncio
async def test_run_signoz_sz_returns_none_on_mcp_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "success": False,
                                    "code": "SZ_NONZERO",
                                    "error": "bad query",
                                },
                            ),
                        },
                    ],
                    "isError": True,
                },
            },
        )

    cfg = SimpleNamespace(
        signoz=SimpleNamespace(
            mcp_url="https://ops.example.com/signoz-mcp",
            mcp_api_key="secret",
            request_timeout_seconds=35,
        ),
    )

    transport = httpx.MockTransport(handler)
    with patch(
        "zd_agent.integrations.signoz_mcp.httpx.AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    ):
        result = await run_signoz_sz("query", "bad", cfg=cfg)

    assert result is None


@pytest.mark.asyncio
async def test_run_signoz_sz_requires_remote_mcp_url() -> None:
    cfg = SimpleNamespace(
        signoz=SimpleNamespace(
            mcp_url="",
            mcp_api_key="secret",
            request_timeout_seconds=35,
        ),
    )

    result = await run_signoz_sz("services", cfg=cfg)

    assert result is None

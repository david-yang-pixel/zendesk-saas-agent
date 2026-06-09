"""Tests for DB Gateway remote MCP transport."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from zd_agent.integrations.db_gateway_mcp import query_db_gateway_mcp


REAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.mark.asyncio
async def test_query_db_gateway_mcp_uses_remote_streamable_http() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content.decode("utf-8"))
        assert body["method"] == "tools/call"
        assert body["params"]["name"] == "db_gateway_query"
        assert body["params"]["arguments"] == {
            "database": "prod",
            "sql": "select 1 as ok",
            "ticket_id": "2830",
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
                                    "csv": "ok\n1\n",
                                    "row_count": 1,
                                    "elapsed_ms": 12,
                                    "truncated": False,
                                    "code": "",
                                    "error": None,
                                },
                            ),
                        },
                    ],
                    "isError": False,
                },
            },
        )

    cfg = SimpleNamespace(
        db_gateway=SimpleNamespace(
            url="https://db-gateway.example.com/db-gateway",
            api_key="secret",
            request_timeout_seconds=35,
        ),
    )

    transport = httpx.MockTransport(handler)
    with patch(
        "zd_agent.integrations.db_gateway_mcp.httpx.AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    ):
        result = await query_db_gateway_mcp(
            cfg=cfg,
            database="prod",
            sql="select 1 as ok",
            ticket_id="2830",
        )

    assert result.success is True
    assert result.csv == "ok\n1\n"
    assert len(requests) == 1
    assert str(requests[0].url) == "https://db-gateway.example.com/db-gateway/mcp"
    assert requests[0].headers["authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_query_db_gateway_mcp_accepts_url_that_already_ends_with_mcp() -> None:
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        body = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {
                    "content": [{"type": "text", "text": "{\"success\": true}"}],
                    "isError": False,
                },
            },
        )

    cfg = SimpleNamespace(
        db_gateway=SimpleNamespace(
            url="https://db-gateway.example.com/db-gateway/mcp",
            api_key="secret",
            request_timeout_seconds=35,
        ),
    )

    transport = httpx.MockTransport(handler)
    with patch(
        "zd_agent.integrations.db_gateway_mcp.httpx.AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    ):
        result = await query_db_gateway_mcp(
            cfg=cfg,
            database="prod",
            sql="select 1",
            ticket_id="2830",
        )

    assert result.success is True
    assert seen_urls == ["https://db-gateway.example.com/db-gateway/mcp"]

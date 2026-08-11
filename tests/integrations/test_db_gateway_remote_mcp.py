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
async def test_query_db_gateway_mcp_parses_sse_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        result_text = json.dumps(
            {
                "success": True,
                "csv": "ok\n1\n",
                "row_count": 1,
                "elapsed_ms": 12,
                "truncated": False,
            },
        )
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
    assert result.csv == "ok\n1\n"


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
                    "content": [{"type": "text", "text": '{"success": true}'}],
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


@pytest.mark.asyncio
async def test_query_db_gateway_mcp_handles_invalid_json_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

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

    assert result.success is False
    assert result.code == "MCP_BAD_JSON"
    assert "invalid MCP JSON response" in (result.error or "")


@pytest.mark.asyncio
async def test_query_db_gateway_mcp_handles_non_object_json_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "an", "object"])

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

    assert result.success is False
    assert result.code == "MCP_BAD_RESPONSE"


@pytest.mark.asyncio
async def test_query_db_gateway_mcp_handles_non_list_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {"content": {"type": "text", "text": '{"success": true}'}},
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

    assert result.success is False
    assert result.code == "MCP_BAD_RESPONSE"


@pytest.mark.asyncio
async def test_query_db_gateway_mcp_handles_non_object_content_item() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {"content": ["not an object"]},
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

    assert result.success is False
    assert result.code == "MCP_BAD_RESPONSE"


@pytest.mark.asyncio
async def test_query_db_gateway_mcp_handles_non_object_tool_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {
                    "content": [{"type": "text", "text": '["not", "an", "object"]'}],
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

    assert result.success is False
    assert result.code == "MCP_BAD_RESPONSE"


@pytest.mark.asyncio
async def test_query_db_gateway_mcp_handles_bad_numeric_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
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
                                    "row_count": "not-a-number",
                                    "elapsed_ms": "also-bad",
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

    assert result.success is False
    assert result.code == "MCP_BAD_RESPONSE"
    assert "Invalid DB Gateway MCP numeric field" in (result.error or "")

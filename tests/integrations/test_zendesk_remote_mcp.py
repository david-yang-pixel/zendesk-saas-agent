"""Tests for Zendesk remote MCP transport."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from zd_agent.integrations.zendesk_mcp import call_zendesk_tool, zendesk_mcp_enabled


REAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.mark.asyncio
async def test_call_zendesk_tool_uses_remote_streamable_http() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content.decode("utf-8"))
        assert body["method"] == "tools/call"
        assert body["params"]["name"] == "get_ticket"
        assert body["params"]["arguments"] == {
            "ticket_id": 3134,
            "include_conversation_log": True,
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
                                    "data": {"ticket": {"id": 3134}},
                                },
                            ),
                        },
                    ],
                    "structuredContent": {
                        "success": True,
                        "data": {"ticket": {"id": 3134}},
                    },
                    "isError": False,
                },
            },
        )

    cfg = SimpleNamespace(
        zendesk_mcp=SimpleNamespace(
            mcp_url="https://ops.example.com/zendesk-mcp",
            mcp_api_key="secret",
            request_timeout_seconds=35,
        ),
    )

    transport = httpx.MockTransport(handler)
    with patch(
        "zd_agent.integrations.zendesk_mcp.httpx.AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    ):
        result = await call_zendesk_tool(
            "get_ticket",
            {"ticket_id": 3134, "include_conversation_log": True},
            cfg=cfg,
        )

    assert result == {"success": True, "data": {"ticket": {"id": 3134}}}
    assert len(requests) == 1
    assert str(requests[0].url) == "https://ops.example.com/zendesk-mcp/mcp"
    assert requests[0].headers["authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_call_zendesk_tool_parses_sse_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        result_text = json.dumps({"success": True, "data": {"comments": []}})
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
        zendesk_mcp=SimpleNamespace(
            mcp_url="https://ops.example.com/zendesk-mcp/mcp",
            mcp_api_key="secret",
            request_timeout_seconds=35,
        ),
    )

    transport = httpx.MockTransport(handler)
    with patch(
        "zd_agent.integrations.zendesk_mcp.httpx.AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    ):
        result = await call_zendesk_tool("get_ticket_comments", {"ticket_id": 3134}, cfg=cfg)

    assert result == {"success": True, "data": {"comments": []}}


@pytest.mark.asyncio
async def test_call_zendesk_tool_returns_structured_error() -> None:
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
                                    "code": "EXPLICIT_INTERNAL_NOTE_REQUEST_REQUIRED",
                                    "error": "explicit request required",
                                },
                            ),
                        },
                    ],
                    "isError": True,
                },
            },
        )

    cfg = SimpleNamespace(
        zendesk_mcp=SimpleNamespace(
            mcp_url="https://ops.example.com/zendesk-mcp",
            mcp_api_key="secret",
            request_timeout_seconds=35,
        ),
    )

    transport = httpx.MockTransport(handler)
    with patch(
        "zd_agent.integrations.zendesk_mcp.httpx.AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    ):
        result = await call_zendesk_tool("add_internal_note", {"ticket_id": 3134}, cfg=cfg)

    assert result["success"] is False
    assert result["code"] == "EXPLICIT_INTERNAL_NOTE_REQUEST_REQUIRED"


@pytest.mark.asyncio
async def test_call_zendesk_tool_requires_remote_mcp_url() -> None:
    cfg = SimpleNamespace(
        zendesk_mcp=SimpleNamespace(
            mcp_url="",
            mcp_api_key="secret",
            request_timeout_seconds=35,
        ),
    )

    result = await call_zendesk_tool("get_ticket", {"ticket_id": 3134}, cfg=cfg)

    assert result["success"] is False
    assert result["code"] == "ZENDESK_MCP_URL_MISSING"
    assert zendesk_mcp_enabled(cfg) is False

"""Tests for the Zendesk browser relay client."""
from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from zd_agent.integrations.zendesk_browser_relay import send_browser_relay_reply


REAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.mark.asyncio
async def test_send_browser_relay_reply_posts_to_send_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content.decode("utf-8"))
        assert body == {"ticket_id": 2861, "body": "hello"}
        return httpx.Response(
            200,
            json={
                "ok": True,
                "ticket_id": 2861,
                "status": "sent",
                "current_url": "https://langgenius.zendesk.com/agent/tickets/2861",
            },
        )

    transport = httpx.MockTransport(handler)
    with patch(
        "zd_agent.integrations.zendesk_browser_relay.httpx.AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    ):
        result = await send_browser_relay_reply(
            url="http://127.0.0.1:8097/",
            api_key="secret",
            ticket_id=2861,
            body="hello",
            timeout_seconds=12,
        )

    assert result.ok is True
    assert result.status == "sent"
    assert len(requests) == 1
    assert str(requests[0].url) == "http://127.0.0.1:8097/send"
    assert requests[0].headers["authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_send_browser_relay_reply_sends_freshness_snapshot() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content.decode("utf-8"))
        assert body["freshness_check_url"] == "http://127.0.0.1:8080/internal/pre-send-freshness"
        assert body["freshness_check_api_key"] == "secret"
        assert body["snapshot_customer_message_count"] == 1
        assert body["snapshot_customer_last_message_id"] == "m1"
        return httpx.Response(
            200,
            json={"ok": True, "ticket_id": 2861, "status": "sent"},
        )

    transport = httpx.MockTransport(handler)
    with patch(
        "zd_agent.integrations.zendesk_browser_relay.httpx.AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    ):
        result = await send_browser_relay_reply(
            url="http://127.0.0.1:8097/",
            api_key="secret",
            ticket_id=2861,
            body="hello",
            freshness_check_url="http://127.0.0.1:8080/internal/pre-send-freshness",
            freshness_check_api_key="secret",
            snapshot_customer_message_count=1,
            snapshot_customer_last_message_id="m1",
        )

    assert result.ok is True
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_send_browser_relay_reply_can_call_remote_mcp_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content.decode("utf-8"))
        assert body["method"] == "tools/call"
        assert body["params"]["name"] == "send_zendesk_native_message"
        assert body["params"]["arguments"] == {"ticket_id": 2861, "body": "hello"}
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "content": [{"type": "text", "text": "sent"}],
                    "structuredContent": {
                        "ok": True,
                        "ticket_id": 2861,
                        "status": "sent",
                        "current_url": "https://langgenius.zendesk.com/agent/tickets/2861",
                        "error": None,
                    },
                    "isError": False,
                },
            },
        )

    transport = httpx.MockTransport(handler)
    with patch(
        "zd_agent.integrations.zendesk_browser_relay.httpx.AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    ):
        result = await send_browser_relay_reply(
            url="https://zendesk.smlershou.top/zendesk-reply-mcp",
            api_key="secret",
            ticket_id=2861,
            body="hello",
            timeout_seconds=12,
        )

    assert result.ok is True
    assert result.status == "sent"
    assert len(requests) == 1
    assert str(requests[0].url) == "https://zendesk.smlershou.top/zendesk-reply-mcp"
    assert requests[0].headers["authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_send_browser_relay_reply_reports_login_required() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": False,
                "ticket_id": 2861,
                "status": "login_required",
                "current_url": "https://langgenius.zendesk.com/login",
            },
        )

    transport = httpx.MockTransport(handler)
    with patch(
        "zd_agent.integrations.zendesk_browser_relay.httpx.AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    ):
        with pytest.raises(RuntimeError, match="login_required"):
            await send_browser_relay_reply(
                url="http://127.0.0.1:8097",
                api_key="secret",
                ticket_id=2861,
                body="hello",
            )


@pytest.mark.asyncio
async def test_send_browser_relay_reply_rejects_bad_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "Forbidden"})

    transport = httpx.MockTransport(handler)
    with patch(
        "zd_agent.integrations.zendesk_browser_relay.httpx.AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    ):
        with pytest.raises(RuntimeError, match="rejected API key"):
            await send_browser_relay_reply(
                url="http://127.0.0.1:8097",
                api_key="bad-secret",
                ticket_id=2861,
                body="hello",
            )

"""Tests for the Zendesk browser relay client."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from zd_agent.integrations.zendesk_browser_relay import (
    BrowserRelayAmbiguousReceiptError,
    send_browser_relay_reply,
)


REAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["login_required", "freshness_check_failed"])
async def test_pre_send_relay_status_raises_definite_unsent_error(status: str) -> None:
    from zd_agent.integrations.zendesk_browser_relay import (
        BrowserRelayDefiniteUnsentError,
    )

    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={"ok": False, "ticket_id": 2861, "status": status},
        )
    )
    with (
        patch(
            "zd_agent.integrations.zendesk_browser_relay.httpx.AsyncClient",
            lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
        ),
        pytest.raises(BrowserRelayDefiniteUnsentError),
    ):
        await send_browser_relay_reply(
            url="http://127.0.0.1:8097/",
            api_key="secret",
            ticket_id=2861,
            body="hello",
        )


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
        assert (
            body["freshness_check_url"]
            == "http://127.0.0.1:8080/internal/pre-send-freshness"
        )
        assert body["freshness_check_api_key"] == "secret"
        assert body["snapshot_customer_message_count"] == 1
        assert body["snapshot_customer_last_message_id"] == "m1"
        return httpx.Response(
            200,
            json={
                "ok": True,
                "ticket_id": 2861,
                "status": "sent",
                "current_url": ("https://langgenius.zendesk.com/agent/tickets/2861"),
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
async def test_send_browser_relay_reply_can_parse_remote_mcp_sse_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            text=(
                "event: message\n"
                'data: {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"sent"}],'
                '"structuredContent":{"ok":true,"ticket_id":2861,"status":"sent",'
                '"current_url":"https://langgenius.zendesk.com/agent/tickets/2861",'
                '"error":null},"isError":false}}\n\n'
            ),
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
    assert result.current_url == "https://langgenius.zendesk.com/agent/tickets/2861"


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
async def test_send_browser_relay_reply_rejects_non_object_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["unexpected"])

    transport = httpx.MockTransport(handler)
    with patch(
        "zd_agent.integrations.zendesk_browser_relay.httpx.AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    ):
        with pytest.raises(RuntimeError, match="expected JSON object"):
            await send_browser_relay_reply(
                url="http://127.0.0.1:8097",
                api_key="secret",
                ticket_id=2861,
                body="hello",
            )


@pytest.mark.asyncio
async def test_send_browser_relay_reply_rejects_non_object_mcp_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["unexpected"])

    transport = httpx.MockTransport(handler)
    with patch(
        "zd_agent.integrations.zendesk_browser_relay.httpx.AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    ):
        with pytest.raises(RuntimeError, match="expected JSON object"):
            await send_browser_relay_reply(
                url="https://zendesk.smlershou.top/zendesk-reply-mcp",
                api_key="secret",
                ticket_id=2861,
                body="hello",
            )


@pytest.mark.asyncio
async def test_send_browser_relay_reply_reports_mcp_tool_error_text() -> None:
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
                            "text": "login_required: browser session expired",
                        }
                    ],
                    "isError": True,
                },
            },
        )

    transport = httpx.MockTransport(handler)
    with patch(
        "zd_agent.integrations.zendesk_browser_relay.httpx.AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    ):
        with pytest.raises(RuntimeError, match="browser session expired"):
            await send_browser_relay_reply(
                url="https://zendesk.smlershou.top/zendesk-reply-mcp",
                api_key="secret",
                ticket_id=2861,
                body="hello",
            )


@pytest.mark.asyncio
async def test_send_browser_relay_reply_rejects_invalid_ticket_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": True, "ticket_id": "not-a-number", "status": "sent"},
        )

    transport = httpx.MockTransport(handler)
    with patch(
        "zd_agent.integrations.zendesk_browser_relay.httpx.AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    ):
        with pytest.raises(RuntimeError, match="invalid ticket_id"):
            await send_browser_relay_reply(
                url="http://127.0.0.1:8097",
                api_key="secret",
                ticket_id=2861,
                body="hello",
            )


@pytest.mark.asyncio
async def test_send_browser_relay_reply_rejects_invalid_mcp_ticket_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "content": [{"type": "text", "text": "sent"}],
                    "structuredContent": {
                        "ok": True,
                        "ticket_id": "not-a-number",
                        "status": "sent",
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
        with pytest.raises(RuntimeError, match="invalid ticket_id"):
            await send_browser_relay_reply(
                url="https://zendesk.smlershou.top/zendesk-reply-mcp",
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


@pytest.mark.asyncio
@pytest.mark.parametrize("use_mcp", [False, True])
async def test_success_receipt_ticket_mismatch_is_ambiguous(use_mcp: bool) -> None:
    structured = {
        "ok": True,
        "ticket_id": 9999,
        "status": "sent",
        "current_url": "https://langgenius.zendesk.com/agent/tickets/9999",
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        if not use_mcp:
            return httpx.Response(200, json=structured)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "content": [{"type": "text", "text": "sent"}],
                    "structuredContent": structured,
                    "isError": False,
                },
            },
        )

    transport = httpx.MockTransport(handler)
    url = (
        "https://relay.example.test/zendesk-reply-mcp"
        if use_mcp
        else "http://127.0.0.1:8097"
    )
    with (
        patch(
            "zd_agent.integrations.zendesk_browser_relay.httpx.AsyncClient",
            lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
        ),
        pytest.raises(BrowserRelayAmbiguousReceiptError) as raised,
    ):
        await send_browser_relay_reply(
            url=url,
            api_key="secret",
            ticket_id=2861,
            body="hello",
        )

    assert raised.value.receipt.ticket_id == 9999
    assert raised.value.receipt.current_url is not None
    assert raised.value.receipt.current_url.endswith("/9999")


@pytest.mark.asyncio
async def test_success_receipt_stale_tab_url_is_ambiguous() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={
                "ok": True,
                "ticket_id": 2861,
                "status": "sent",
                "current_url": ("https://langgenius.zendesk.com/agent/tickets/9999"),
            },
        )
    )
    with (
        patch(
            "zd_agent.integrations.zendesk_browser_relay.httpx.AsyncClient",
            lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
        ),
        pytest.raises(BrowserRelayAmbiguousReceiptError) as raised,
    ):
        await send_browser_relay_reply(
            url="http://127.0.0.1:8097",
            api_key="secret",
            ticket_id=2861,
            body="hello",
        )

    assert raised.value.receipt.ticket_id == 2861
    assert "9999" in str(raised.value.receipt.current_url)

from __future__ import annotations

from collections.abc import Mapping

import httpx
import pytest

from casework_harness.delivery import (
    BrowserRelayConfig,
    DeliveryUnavailableError,
    UnavailableZendeskReplySender,
    ZendeskBrowserRelaySender,
    ZendeskReplyConfig,
    ZendeskReplyReceipt,
    ZendeskReplySender,
    create_zendesk_reply_sender,
)


def _environment() -> Mapping[str, str]:
    return {
        "ZENDESK_SUBDOMAIN": "support-example",
        "ZENDESK_ACCOUNT_EMAIL": "agent@example.com",
        "ZENDESK_API_TOKEN": "token-value",
    }


def test_zendesk_config_requires_all_credentials() -> None:
    assert ZendeskReplyConfig.from_environment({}) is None
    config = ZendeskReplyConfig.from_environment(_environment())

    assert config is not None
    assert config.base_url == "https://support-example.zendesk.com"
    assert config.email == "agent@example.com"


@pytest.mark.asyncio
async def test_zendesk_sender_uses_ticket_update_and_returns_comment_receipt() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"ticket": {"comment": {"id": "comment-3726"}}},
            request=request,
        )

    config = ZendeskReplyConfig.from_environment(_environment())
    assert config is not None
    client = httpx.AsyncClient(
        base_url=config.base_url,
        auth=(f"{config.email}/token", config.api_token),
        transport=httpx.MockTransport(handler),
    )
    sender = ZendeskReplySender(client=client)

    receipt = await sender.send_reply(
        ticket_id=3726,
        body="Answer",
        idempotency_key="reply-key",
    )
    await client.aclose()

    assert isinstance(receipt, ZendeskReplyReceipt)
    assert receipt.provider_message_id == "comment-3726"
    assert len(requests) == 1
    assert requests[0].method == "PUT"
    assert requests[0].url.path == "/api/v2/tickets/3726.json"
    assert requests[0].headers["Idempotency-Key"] == "reply-key"
    assert requests[0].read() == (
        b'{"ticket":{"comment":{"body":"Answer","public":true}}}'
    )


@pytest.mark.asyncio
async def test_zendesk_sender_without_provider_id_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ticket": {}}, request=request)

    client = httpx.AsyncClient(
        base_url="https://support-example.zendesk.com",
        transport=httpx.MockTransport(handler),
    )
    sender = ZendeskReplySender(client=client)

    with pytest.raises(DeliveryUnavailableError, match="provider message id"):
        await sender.send_reply(
            ticket_id=3726,
            body="Answer",
            idempotency_key="reply-key",
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_sender_factory_is_unavailable_without_credentials_and_mockable_with_them(
) -> None:
    sender, close = create_zendesk_reply_sender({})
    assert isinstance(sender, UnavailableZendeskReplySender)
    assert sender.ready is False
    await close()

    sender, close = create_zendesk_reply_sender(_environment())
    assert isinstance(sender, ZendeskReplySender)
    assert sender.ready is True
    await close()


@pytest.mark.asyncio
async def test_zendesk_sender_extracts_comment_id_from_audit_events() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ticket": {"id": 3726},
                "audit": {
                    "id": 123456,
                    "events": [
                        {
                            "id": 987654,
                            "type": "Comment",
                            "body": "Answer text",
                            "public": True,
                        }
                    ],
                },
            },
            request=request,
        )

    config = ZendeskReplyConfig.from_environment(_environment())
    assert config is not None
    client = httpx.AsyncClient(
        base_url=config.base_url,
        auth=(f"{config.email}/token", config.api_token),
        transport=httpx.MockTransport(handler),
    )
    sender = ZendeskReplySender(client=client)

    receipt = await sender.send_reply(
        ticket_id=3726,
        body="Answer text",
        idempotency_key="reply-key",
    )
    await client.aclose()

    assert receipt.provider_message_id == "987654"


def test_browser_relay_config_from_environment() -> None:
    assert BrowserRelayConfig.from_environment({}) is None
    env = {
        "CASEWORK_BROWSER_RELAY_URL": "http://127.0.0.1:8097",
        "CASEWORK_BROWSER_RELAY_API_KEY": "secret-relay-key",
        "CASEWORK_BROWSER_RELAY_TIMEOUT_SECONDS": "45",
    }
    config = BrowserRelayConfig.from_environment(env)
    assert config is not None
    assert config.relay_url == "http://127.0.0.1:8097"
    assert config.api_key == "secret-relay-key"
    assert config.timeout_seconds == 45.0


@pytest.mark.asyncio
async def test_browser_relay_sender_sends_post_and_returns_receipt() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "ticket_id": 3726,
                "status": "sent",
                "current_url": "https://example.zendesk.com/agent/tickets/3726",
            },
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sender = ZendeskBrowserRelaySender(
        client=client,
        relay_url="https://relay.example.com",
        api_key="relay-secret",
    )

    receipt = await sender.send_reply(
        ticket_id=3726,
        body="Hello customer",
        idempotency_key="reply-key",
    )
    await sender.aclose()

    assert receipt.provider_message_id == "https://example.zendesk.com/agent/tickets/3726"
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert str(requests[0].url) == "https://relay.example.com/send"
    assert requests[0].headers["Authorization"] == "Bearer relay-secret"
    assert requests[0].headers["Content-Type"] == "application/json"
    assert requests[0].read() == b'{"ticket_id":3726,"body":"Hello customer"}'


@pytest.mark.asyncio
async def test_browser_relay_sender_raises_on_failure_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": False,
                "ticket_id": 3726,
                "status": "failed",
                "error": "stale_context",
            },
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sender = ZendeskBrowserRelaySender(
        client=client,
        relay_url="https://relay.example.com",
        api_key="relay-secret",
    )

    with pytest.raises(DeliveryUnavailableError, match="stale_context"):
        await sender.send_reply(
            ticket_id=3726,
            body="Hello customer",
            idempotency_key="reply-key",
        )
    await sender.aclose()


@pytest.mark.asyncio
async def test_create_zendesk_reply_sender_prefers_browser_relay() -> None:
    env = {
        **_environment(),
        "CASEWORK_BROWSER_RELAY_URL": "https://relay.example.com",
        "CASEWORK_BROWSER_RELAY_API_KEY": "secret-relay-key",
    }
    sender, close = create_zendesk_reply_sender(env)
    assert isinstance(sender, ZendeskBrowserRelaySender)
    assert sender.ready is True
    await close()

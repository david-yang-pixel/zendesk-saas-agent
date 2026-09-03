from __future__ import annotations

from collections.abc import Mapping

import httpx
import pytest

from casework_harness.delivery import (
    DeliveryUnavailableError,
    UnavailableZendeskReplySender,
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

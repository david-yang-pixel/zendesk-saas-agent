"""Regression contract for a late Browser Relay ``stale_context`` race.

The ordinary tool-level freshness check can pass and a customer can still send
another message before the browser relay clicks Send.  That final guard is not
an ambiguous delivery failure: no customer-visible reply was sent.  The agent
must therefore receive typed recovery context and a regenerated reply may
replace the stale pending body for the same logical delivery attempt.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from zd_agent.config import AppConfig, PublicReplyConfig
from zd_agent.integrations import zendesk_browser_relay as relay_module
from zd_agent.services.public_reply_delivery_store import PublicReplyDeliveryStore
from zd_agent.tools.public_reply_delivery import send_public_reply_safely


REAL_ASYNC_CLIENT = httpx.AsyncClient
TICKET_ID = 3461


def _late_stale_result() -> relay_module.BrowserRelaySendResult:
    return relay_module.BrowserRelaySendResult(
        ok=False,
        ticket_id=TICKET_ID,
        status="stale_context",
        error="customer message cursor changed immediately before send",
    )


def _config() -> AppConfig:
    cfg = AppConfig(
        public_reply=PublicReplyConfig(
            enabled=True,
            browser_relay_url="https://relay.example.test/zendesk-reply-mcp",
            browser_relay_api_key="relay-secret",
            freshness_callback_url=(
                "https://relay.example.test/webhook/relay-pre-send-freshness"
            ),
        ),
    )
    cfg.webhook.listen_port = 8080
    return cfg


@pytest.mark.asyncio
async def test_browser_relay_raises_typed_stale_context_error() -> None:
    """Callers must distinguish a definite pre-send abort from unknown delivery."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": False,
                "ticket_id": TICKET_ID,
                "status": "stale_context",
                "error": "customer message cursor changed immediately before send",
            },
        )

    transport = httpx.MockTransport(handler)
    with patch(
        "zd_agent.integrations.zendesk_browser_relay.httpx.AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    ):
        with pytest.raises(RuntimeError) as raised:
            await relay_module.send_browser_relay_reply(
                url="http://relay.example.test",
                api_key="relay-secret",
                ticket_id=TICKET_ID,
                body="This reply became stale.",
                freshness_check_url=(
                    "http://127.0.0.1:8080/internal/pre-send-freshness"
                ),
                freshness_check_api_key="relay-secret",
                snapshot_customer_message_count=1,
                snapshot_customer_last_message_id="m1",
            )

    stale_error_type = getattr(
        relay_module,
        "BrowserRelayStaleContextError",
        None,
    )
    assert stale_error_type is not None, (
        "stale_context must have a dedicated exception type so it is not "
        "handled like an ambiguous relay failure"
    )
    assert isinstance(raised.value, stale_error_type)


@pytest.mark.asyncio
async def test_regenerated_reply_replaces_stale_body_for_same_delivery_attempt(
    tmp_path,
) -> None:
    """A definite stale abort must not pin the obsolete body in the outbox."""
    old_body = "Please provide your workspace ID."
    regenerated_body = "Thank you for the workspace ID. Here are the next steps."
    sent_bodies: list[str] = []

    async def relay_sender(**kwargs: Any) -> None:
        sent_bodies.append(kwargs["body"])
        if len(sent_bodies) == 1:
            relay_module._ensure_sent(_late_stale_result())

    store = PublicReplyDeliveryStore(tmp_path / "public-reply-outbox.sqlite3")
    zendesk_tool_caller = AsyncMock(
        return_value={"success": True, "data": {"comments": []}},
    )
    common: dict[str, Any] = {
        "cfg": _config(),
        "ticket_id": TICKET_ID,
        "reply_type": "solution",
        "conversation_id": "conversation-3461",
        "delivery_attempt_id": "session-3461",
        "delivery_store": store,
        "delivery_store_factory": lambda _cfg: pytest.fail(
            "the explicit delivery store must be reused"
        ),
        "zendesk_tool_caller": zendesk_tool_caller,
        "relay_sender": relay_sender,
    }

    with pytest.raises(RuntimeError, match="stale_context"):
        await send_public_reply_safely(
            body_with_footer=old_body,
            snapshot_customer_message_count=1,
            snapshot_customer_last_message_id="m1",
            **common,
        )

    delivered = await send_public_reply_safely(
        body_with_footer=regenerated_body,
        snapshot_customer_message_count=2,
        snapshot_customer_last_message_id="m2",
        **common,
    )

    assert delivered is True
    assert sent_bodies == [old_body, regenerated_body], (
        "the first body is known not to have been sent, so a regenerated body "
        "must replace it even when the logical delivery attempt id is unchanged"
    )


@pytest.mark.asyncio
async def test_mismatched_success_receipt_keeps_public_outbox_ambiguous(
    tmp_path,
) -> None:
    receipt = relay_module.BrowserRelaySendResult(
        ok=True,
        ticket_id=9999,
        status="sent",
        current_url="https://example.zendesk.com/agent/tickets/9999",
    )
    mismatch = relay_module.BrowserRelayAmbiguousReceiptError(
        "success receipt belongs to another ticket",
        receipt=receipt,
    )

    async def relay_sender(**_kwargs: Any) -> None:
        raise mismatch

    store = PublicReplyDeliveryStore(tmp_path / "public-reply-outbox.sqlite3")
    with pytest.raises(relay_module.BrowserRelayAmbiguousReceiptError) as raised:
        await send_public_reply_safely(
            cfg=_config(),
            ticket_id=TICKET_ID,
            body_with_footer="Customer-visible reply",
            reply_type="solution",
            conversation_id="conversation-3461",
            snapshot_customer_message_count=1,
            snapshot_customer_last_message_id="m1",
            delivery_attempt_id="receipt-mismatch",
            delivery_store=store,
            delivery_store_factory=lambda _cfg: store,
            zendesk_tool_caller=AsyncMock(
                return_value={"success": True, "data": {"comments": []}},
            ),
            relay_sender=relay_sender,
        )

    frozen = store.get(
        delivery_attempt_id="receipt-mismatch",
        ticket_id=TICKET_ID,
    )
    assert frozen is not None
    assert frozen.delivered is False
    assert frozen.ambiguous is True
    assert raised.value.receipt == receipt

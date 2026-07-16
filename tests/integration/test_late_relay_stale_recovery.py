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
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools import zendesk_public_reply as public_reply_module
from zd_agent.tools.public_reply_delivery import send_public_reply_safely


REAL_ASYNC_CLIENT = httpx.AsyncClient
TICKET_ID = 3461


def _customer_message(message_id: str, body: str) -> dict[str, Any]:
    return {
        "id": message_id,
        "created_at": "2026-07-16T04:18:53Z",
        "body": body,
        "public": True,
        "image_urls": [],
        "content_type": "formResponse" if message_id == "m2" else "text",
    }


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
async def test_public_reply_refetches_after_late_relay_stale_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A relay-side abort must return typed recovery metadata to the agent loop."""
    initial = _customer_message("m1", "I cannot update our knowledge documents.")
    late_form = _customer_message(
        "m2",
        "Workspace ID: 86628345-731d-4475-b681-5006f48a2287",
    )
    fetch_messages = AsyncMock(
        side_effect=[
            [initial],
            [initial, late_form],
        ],
    )

    monkeypatch.setattr(public_reply_module, "get_config", _config)
    monkeypatch.setattr(
        public_reply_module,
        "public_reply_privacy_blockers",
        lambda _body: [],
    )
    monkeypatch.setattr(
        public_reply_module,
        "public_reply_evidence_blockers",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        public_reply_module,
        "refund_owner_gate_block_reason",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "zd_agent.services.ticket_messages.fetch_customer_messages_for_freshness",
        fetch_messages,
    )

    async def relay_detects_late_message(**_kwargs: Any) -> bool:
        relay_module._ensure_sent(_late_stale_result())
        raise AssertionError("stale relay result must raise")

    monkeypatch.setattr(
        public_reply_module,
        "_send_public_reply_safely",
        relay_detects_late_message,
    )

    session = AgentSession(
        ticket_id=TICKET_ID,
        delivery_attempt_id="session-3461",
    )
    session.customer_msgs_snapshot_captured = True
    session.customer_msgs_snapshot_count = 1
    session.customer_msgs_snapshot_last_id = "m1"

    result = await public_reply_module.ZendeskPublicReply().execute(
        {
            "ticket_id": TICKET_ID,
            "body": "Here is the answer based on the original message.",
            "reply_type": "solution",
            "conversation_id": "conversation-3461",
        },
        session,
    )

    assert result.success is False
    assert fetch_messages.await_count == 2, (
        "the first fetch is the tool guard; a late relay stale_context must "
        "trigger a second fetch"
    )
    recovery = result.metadata.get("freshness_recovery")
    assert recovery is not None, (
        "late relay stale_context must not collapse into a generic tool error"
    )
    assert list(recovery.new_messages) == [late_form]
    assert recovery.next_snapshot_count == 2
    assert recovery.next_snapshot_last_id == "m2"


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

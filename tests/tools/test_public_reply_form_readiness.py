"""Fail-closed public-reply contracts for Zendesk blocking forms."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from zd_agent.config import AppConfig, PublicReplyConfig
from zd_agent.runtime.pre_send_check import FreshnessRecoveryAction
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.zendesk_public_reply import ZendeskPublicReply, send_public_reply


def _config() -> AppConfig:
    config = AppConfig()
    config.public_reply = PublicReplyConfig(enabled=True)
    return config


def _pending_ticket() -> dict:
    return {
        "id": 42,
        "_zd_agent_conversation_events": [
            {
                "id": "m1",
                "type": "Messaging::ConversationMessage",
                "author": {"type": "end-user"},
                "content": {"type": "text", "text": "Please help."},
            },
            {
                "id": "profile-form",
                "type": "Messaging::ConversationMessage",
                "author": {"type": "business"},
                "content": {
                    "type": "form",
                    "block_chat_input": True,
                    "submitted": False,
                    "fields": [
                        {
                            "name": "dataCapture.systemField.requester.email",
                            "label": "Email",
                        },
                    ],
                },
            },
        ],
    }


@pytest.mark.asyncio
async def test_direct_public_reply_blocks_latest_pending_form() -> None:
    sender = AsyncMock(return_value=True)
    with patch(
        "zd_agent.tools.zendesk_public_reply.get_config",
        return_value=_config(),
    ), patch(
        "zd_agent.services.ticket_messages._fetch_customer_messages",
        AsyncMock(return_value=([{"id": "m1", "body": "Please help."}], _pending_ticket())),
    ), patch(
        "zd_agent.tools.zendesk_public_reply._send_public_reply_safely",
        sender,
    ):
        success, message = await send_public_reply(
            ticket_id=42,
            body="Here is the answer.",
            reply_type="solution",
            conversation_id="conversation-42",
            snapshot_customer_message_count=1,
            snapshot_customer_last_message_id="m1",
        )

    assert success is False
    assert "pending" in message.lower()
    sender.assert_not_awaited()


@pytest.mark.asyncio
async def test_handoff_also_blocks_latest_pending_form() -> None:
    sender = AsyncMock(return_value=True)
    with patch(
        "zd_agent.tools.zendesk_public_reply.get_config",
        return_value=_config(),
    ), patch(
        "zd_agent.services.ticket_messages._fetch_customer_messages",
        AsyncMock(return_value=([], _pending_ticket())),
    ), patch(
        "zd_agent.tools.zendesk_public_reply._send_public_reply_safely",
        sender,
    ):
        success, message = await send_public_reply(
            ticket_id=42,
            body="A human support engineer will follow up.",
            reply_type="handoff",
            conversation_id="conversation-42",
        )

    assert success is False
    assert "pending" in message.lower()
    sender.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_tool_returns_typed_defer_for_pending_form() -> None:
    session = AgentSession(ticket_id=42)
    session.customer_msgs_snapshot_captured = True
    session.customer_msgs_snapshot_count = 1
    session.customer_msgs_snapshot_last_id = "m1"
    sender = AsyncMock(return_value=True)

    with patch(
        "zd_agent.tools.zendesk_public_reply.get_config",
        return_value=_config(),
    ), patch(
        "zd_agent.services.ticket_messages._fetch_customer_messages",
        AsyncMock(return_value=([{"id": "m1", "body": "Please help."}], _pending_ticket())),
    ), patch(
        "zd_agent.tools.zendesk_public_reply._send_public_reply_safely",
        sender,
    ):
        result = await ZendeskPublicReply().execute(
            {
                "ticket_id": 42,
                "body": "Here is the answer.",
                "reply_type": "ask",
                "internal_analysis": "Exercise pending-form readiness before delivery.",
                "conversation_id": "conversation-42",
            },
            session,
        )

    assert result.success is False
    assert "pending" in result.error.lower()
    recovery = result.metadata["freshness_recovery"]
    assert recovery.action is FreshnessRecoveryAction.DEFER
    sender.assert_not_awaited()

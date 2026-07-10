"""Tests for silence-window expiry side-effect helpers."""
from unittest.mock import AsyncMock, patch

import pytest

from zd_agent.config import AppConfig
from zd_agent.services.silence_notifications import (
    send_silence_expired_alert_best_effort,
    send_public_nudge_best_effort,
)
from zd_agent.services.ticket_tags import AGENT_ASKED_TAG


def _make_cfg() -> AppConfig:
    return AppConfig()


@pytest.mark.asyncio
async def test_public_nudge_replies_and_tags_ticket() -> None:
    mock_send = AsyncMock(return_value=(True, "ok"))
    mock_mcp = AsyncMock(return_value={"success": True})

    with (
        patch("zd_agent.tools.zendesk_public_reply.send_public_reply", mock_send),
        patch("zd_agent.services.silence_notifications.call_zendesk_tool", mock_mcp),
    ):
        sent = await send_public_nudge_best_effort(
            ticket_id=9999,
            body="Could you share more details?",
            lang="en",
            snapshot_customer_message_count=2,
            snapshot_customer_last_message_id="message-2",
        )

    assert sent is True
    mock_send.assert_awaited_once_with(
        ticket_id=9999,
        body="Could you share more details?",
        reply_type="ask",
        snapshot_customer_message_count=2,
        snapshot_customer_last_message_id="message-2",
        delivery_attempt_id="",
    )
    mock_mcp.assert_awaited_once()
    assert mock_mcp.await_args.args[0] == "update_ticket_metadata"
    assert mock_mcp.await_args.args[1]["add_tags"] == [AGENT_ASKED_TAG]


@pytest.mark.asyncio
async def test_public_nudge_tag_failure_still_counts_as_sent() -> None:
    mock_send = AsyncMock(return_value=(True, "ok"))
    mock_mcp = AsyncMock(
        return_value={"success": False, "code": "ZENDESK_API_ERROR", "error": "tag err"}
    )

    with (
        patch("zd_agent.tools.zendesk_public_reply.send_public_reply", mock_send),
        patch("zd_agent.services.silence_notifications.call_zendesk_tool", mock_mcp),
    ):
        sent = await send_public_nudge_best_effort(
            ticket_id=9999,
            body="Could you share more details?",
            lang="en",
            snapshot_customer_message_count=1,
            snapshot_customer_last_message_id="message-1",
        )

    assert sent is True
    mock_send.assert_awaited_once()
    mock_mcp.assert_awaited_once()


@pytest.mark.asyncio
async def test_public_nudge_reply_failure_is_retryable_failure() -> None:
    mock_send = AsyncMock(return_value=(False, "reply err"))
    mock_mcp = AsyncMock(return_value={"success": True})

    with (
        patch("zd_agent.tools.zendesk_public_reply.send_public_reply", mock_send),
        patch("zd_agent.services.silence_notifications.call_zendesk_tool", mock_mcp),
    ):
        sent = await send_public_nudge_best_effort(
            ticket_id=9999,
            body="Could you share more details?",
            lang="en",
            snapshot_customer_message_count=1,
            snapshot_customer_last_message_id="message-1",
        )

    assert sent is False
    mock_mcp.assert_not_awaited()


@pytest.mark.asyncio
async def test_silence_expired_alert_sends_via_notification_service() -> None:
    push = AsyncMock(return_value={"success": True, "output": "pushed (alert)"})

    with (
        patch("zd_agent.config.get_config", return_value=_make_cfg()),
        patch("zd_agent.services.silence_notifications.push_notification_card", push),
    ):
        sent = await send_silence_expired_alert_best_effort(
            ticket_id=9999,
            verdict="insufficient_info",
        )

    assert sent is True
    push.assert_awaited_once()
    kwargs = push.await_args.kwargs
    assert kwargs["title"] == "Ticket #9999 信息不足等待处理"
    assert "insufficient_info" in kwargs["content"]
    assert kwargs["urgent"] is False


@pytest.mark.asyncio
async def test_silence_expired_alert_returns_false_when_notification_mcp_missing() -> None:
    push = AsyncMock(
        return_value={
            "success": False,
            "code": "NOTIFICATION_SERVICE_URL_MISSING",
            "error": "not configured",
        }
    )

    with (
        patch("zd_agent.config.get_config", return_value=_make_cfg()),
        patch("zd_agent.services.silence_notifications.push_notification_card", push),
    ):
        sent = await send_silence_expired_alert_best_effort(
            ticket_id=9999,
            verdict="greeting",
        )

    assert sent is False
    push.assert_awaited_once()


@pytest.mark.asyncio
async def test_silence_expired_alert_failure_is_best_effort() -> None:
    push = AsyncMock(
        return_value={"success": False, "code": "FEISHU_API_ERROR", "error": "feishu err"}
    )

    with (
        patch("zd_agent.config.get_config", return_value=_make_cfg()),
        patch("zd_agent.services.silence_notifications.push_notification_card", push),
    ):
        sent = await send_silence_expired_alert_best_effort(
            ticket_id=9999,
            verdict="greeting",
        )

    assert sent is False

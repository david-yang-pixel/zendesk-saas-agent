"""Tests for silence-window expiry side-effect helpers."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zd_agent.config import AppConfig, FeishuConfig
from zd_agent.services.silence_notifications import (
    send_silence_expired_alert_best_effort,
    send_public_nudge_best_effort,
)
from zd_agent.services.ticket_tags import AGENT_ASKED_TAG


def _make_cfg(*, feishu_url: str = "") -> AppConfig:
    cfg = AppConfig()
    cfg.feishu = FeishuConfig(webhook_url=feishu_url, webhook_secret="secret")
    return cfg


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
        )

    assert sent is True
    mock_send.assert_awaited_once_with(
        ticket_id=9999,
        body="Could you share more details?",
        reply_type="ask",
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
        )

    assert sent is False
    mock_mcp.assert_not_awaited()


@pytest.mark.asyncio
async def test_silence_expired_alert_sends_when_webhook_configured() -> None:
    feishu_instance = MagicMock()
    feishu_instance.send_alert = AsyncMock()

    with (
        patch("zd_agent.config.get_config", return_value=_make_cfg(
            feishu_url="https://example.com/hook",
        )),
        patch(
            "zd_agent.integrations.feishu.client.FeishuClient",
            return_value=feishu_instance,
        ),
    ):
        sent = await send_silence_expired_alert_best_effort(
            ticket_id=9999,
            verdict="insufficient_info",
        )

    assert sent is True
    feishu_instance.send_alert.assert_called_once()
    kwargs = feishu_instance.send_alert.await_args.kwargs
    assert kwargs["title"] == "Ticket #9999 信息不足等待处理"
    assert "insufficient_info" in kwargs["message"]
    assert kwargs["urgent"] is False


@pytest.mark.asyncio
async def test_silence_expired_alert_skips_when_webhook_empty() -> None:
    mock_feishu_cls = MagicMock()

    with (
        patch("zd_agent.config.get_config", return_value=_make_cfg(
            feishu_url="",
        )),
        patch(
            "zd_agent.integrations.feishu.client.FeishuClient",
            mock_feishu_cls,
        ),
    ):
        sent = await send_silence_expired_alert_best_effort(
            ticket_id=9999,
            verdict="greeting",
        )

    assert sent is False
    mock_feishu_cls.assert_not_called()


@pytest.mark.asyncio
async def test_silence_expired_alert_failure_is_best_effort() -> None:
    feishu_instance = MagicMock()
    feishu_instance.send_alert = AsyncMock(side_effect=RuntimeError("feishu err"))

    with (
        patch("zd_agent.config.get_config", return_value=_make_cfg(
            feishu_url="https://example.com/hook",
        )),
        patch(
            "zd_agent.integrations.feishu.client.FeishuClient",
            return_value=feishu_instance,
        ),
    ):
        sent = await send_silence_expired_alert_best_effort(
            ticket_id=9999,
            verdict="greeting",
        )

    assert sent is False

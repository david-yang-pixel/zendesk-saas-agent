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
    zd_instance = MagicMock()

    with patch("zd.client.ZendeskClient", return_value=zd_instance):
        sent = await send_public_nudge_best_effort(
            ticket_id=9999,
            body="Could you share more details?",
            lang="en",
        )

    assert sent is True
    zd_instance.reply_ticket.assert_called_once_with(
        9999,
        body="Could you share more details?",
        public=True,
    )
    zd_instance.add_ticket_tags.assert_called_once_with(
        9999,
        [AGENT_ASKED_TAG],
    )


@pytest.mark.asyncio
async def test_public_nudge_tag_failure_still_counts_as_sent() -> None:
    zd_instance = MagicMock()
    zd_instance.add_ticket_tags.side_effect = RuntimeError("tag err")

    with patch("zd.client.ZendeskClient", return_value=zd_instance):
        sent = await send_public_nudge_best_effort(
            ticket_id=9999,
            body="Could you share more details?",
            lang="en",
        )

    assert sent is True
    zd_instance.reply_ticket.assert_called_once()
    zd_instance.add_ticket_tags.assert_called_once()


@pytest.mark.asyncio
async def test_public_nudge_reply_failure_is_retryable_failure() -> None:
    zd_instance = MagicMock()
    zd_instance.reply_ticket.side_effect = RuntimeError("reply err")

    with patch("zd.client.ZendeskClient", return_value=zd_instance):
        sent = await send_public_nudge_best_effort(
            ticket_id=9999,
            body="Could you share more details?",
            lang="en",
        )

    assert sent is False


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

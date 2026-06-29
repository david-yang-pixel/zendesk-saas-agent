"""Tests for escalation side-effect helpers."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zd_agent.config import AppConfig, FeishuConfig, ZendeskConfig
from zd_agent.services.escalation_notifications import (
    add_zendesk_escalation_tag_best_effort,
    send_feishu_escalation_best_effort,
    send_handoff_reply_best_effort,
)


def _make_cfg(*, feishu_url: str = "", subdomain: str = "dify-test") -> AppConfig:
    cfg = AppConfig()
    cfg.feishu = FeishuConfig(webhook_url=feishu_url, webhook_secret="secret")
    cfg.zendesk = ZendeskConfig(
        subdomain=subdomain,
        email="x@y.com",
        api_token="t",
    )
    return cfg


@pytest.mark.asyncio
async def test_handoff_reply_uses_customer_name() -> None:
    mock_send = AsyncMock(return_value=(True, "ok"))

    with patch("zd_agent.tools.zendesk_public_reply.send_public_reply", mock_send):
        sent = await send_handoff_reply_best_effort(
            ticket_id=9999,
            reason="customer_requested",
            customer_name="Alice",
        )

    assert sent is True
    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs["ticket_id"] == 9999
    assert kwargs["reply_type"] == "handoff"
    assert "Alice" in kwargs["body"]


@pytest.mark.asyncio
async def test_handoff_reply_skips_human_takeover() -> None:
    mock_send = AsyncMock(return_value=(True, "ok"))

    with patch("zd_agent.tools.zendesk_public_reply.send_public_reply", mock_send):
        sent = await send_handoff_reply_best_effort(
            ticket_id=9999,
            reason="human_takeover_detected",
            customer_name="Alice",
        )

    assert sent is False
    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_handoff_reply_failure_is_best_effort() -> None:
    mock_send = AsyncMock(side_effect=RuntimeError("send err"))

    with patch("zd_agent.tools.zendesk_public_reply.send_public_reply", mock_send):
        sent = await send_handoff_reply_best_effort(
            ticket_id=9999,
            reason="customer_requested",
            customer_name="",
        )

    assert sent is False


@pytest.mark.asyncio
async def test_feishu_escalation_skips_when_webhook_empty() -> None:
    mock_feishu_cls = MagicMock()

    with (
        patch(
            "zd_agent.config.get_config",
            return_value=_make_cfg(feishu_url=""),
        ),
        patch(
            "zd_agent.integrations.feishu.client.FeishuClient",
            mock_feishu_cls,
        ),
    ):
        sent = await send_feishu_escalation_best_effort(
            ticket_id=9999,
            reason="customer_requested",
            source="zendesk_webhook",
        )

    assert sent is False
    mock_feishu_cls.assert_not_called()


@pytest.mark.asyncio
async def test_feishu_escalation_skips_human_takeover() -> None:
    mock_feishu_cls = MagicMock()

    with (
        patch(
            "zd_agent.config.get_config",
            return_value=_make_cfg(feishu_url="https://example.com/hook"),
        ),
        patch(
            "zd_agent.integrations.feishu.client.FeishuClient",
            mock_feishu_cls,
        ),
    ):
        sent = await send_feishu_escalation_best_effort(
            ticket_id=9999,
            reason="human_takeover_detected",
            source="zendesk_webhook",
        )

    assert sent is False
    mock_feishu_cls.assert_not_called()


@pytest.mark.asyncio
async def test_feishu_escalation_sends_reason_context() -> None:
    feishu_instance = MagicMock()
    feishu_instance.send_ticket_escalate = AsyncMock()

    with (
        patch(
            "zd_agent.config.get_config",
            return_value=_make_cfg(
                feishu_url="https://example.com/hook",
                subdomain="dify-test",
            ),
        ),
        patch(
            "zd_agent.integrations.feishu.client.FeishuClient",
            return_value=feishu_instance,
        ),
    ):
        sent = await send_feishu_escalation_best_effort(
            ticket_id=9999,
            reason="high_risk_refund",
            source="zendesk_webhook",
            customer_phrase="我要退款 $200",
            agent_last_reply="I will check this.",
        )

    assert sent is True
    feishu_instance.send_ticket_escalate.assert_called_once()
    kwargs = feishu_instance.send_ticket_escalate.call_args.kwargs
    assert kwargs["ticket_id"] == "9999"
    assert "dify-test" in kwargs["zendesk_url"]
    assert "9999" in kwargs["zendesk_url"]
    reason_text = kwargs["reason"]
    assert "high_risk_refund" in reason_text
    assert "退款" in reason_text
    assert "I will check this." in reason_text
    assert "zendesk_webhook" in reason_text


@pytest.mark.asyncio
async def test_feishu_escalation_failure_is_best_effort() -> None:
    feishu_instance = MagicMock()
    feishu_instance.send_ticket_escalate = AsyncMock(
        side_effect=RuntimeError("feishu err"),
    )

    with (
        patch(
            "zd_agent.config.get_config",
            return_value=_make_cfg(feishu_url="https://example.com/hook"),
        ),
        patch(
            "zd_agent.integrations.feishu.client.FeishuClient",
            return_value=feishu_instance,
        ),
    ):
        sent = await send_feishu_escalation_best_effort(
            ticket_id=9999,
            reason="customer_requested",
            source="zendesk_webhook",
        )

    assert sent is False


@pytest.mark.asyncio
async def test_zendesk_escalation_tag_added() -> None:
    mock_mcp = AsyncMock(return_value={"success": True})

    with patch("zd_agent.services.escalation_notifications.call_zendesk_tool", mock_mcp):
        tagged = await add_zendesk_escalation_tag_best_effort(ticket_id=9999)

    assert tagged is True
    mock_mcp.assert_awaited_once()
    assert mock_mcp.await_args.args[0] == "update_ticket_metadata"
    assert mock_mcp.await_args.args[1]["add_tags"] == ["escalated-to-human"]


@pytest.mark.asyncio
async def test_zendesk_escalation_tag_failure_is_best_effort() -> None:
    mock_mcp = AsyncMock(
        return_value={"success": False, "code": "ZENDESK_API_ERROR", "error": "zd err"}
    )
    with patch("zd_agent.services.escalation_notifications.call_zendesk_tool", mock_mcp):
        tagged = await add_zendesk_escalation_tag_best_effort(ticket_id=9999)

    assert tagged is False

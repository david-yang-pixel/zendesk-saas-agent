"""Test FeishuPush tool with mocked notification-service MCP."""
from unittest.mock import AsyncMock, patch

import pytest

from zd_agent.tools.feishu_tools import FeishuPush


@pytest.mark.asyncio
async def test_push_ticket_done(fake_session):
    with patch(
        "zd_agent.tools.feishu_tools.push_notification_card",
        new_callable=AsyncMock,
        return_value={"success": True, "output": "pushed (ticket_done)"},
    ) as push:
        result = await FeishuPush().execute(
            {
                "card_type": "ticket_done",
                "content": "summary text",
                "zendesk_url": "https://z/12345",
            },
            fake_session,
        )

    assert result.success is True
    assert "ticket_done" in result.output
    push.assert_awaited_once()
    kwargs = push.await_args.kwargs
    assert kwargs["ticket_id"] == "12345"
    assert kwargs["content"] == "summary text"
    assert kwargs["zendesk_url"] == "https://z/12345"


@pytest.mark.asyncio
async def test_push_ticket_done_corrects_stale_no_attachment_claim(fake_session):
    fake_session.images_injected = True
    content = "注意事项：客户说有示例图片，但 Zendesk 返回无附件或图片。"

    with patch(
        "zd_agent.tools.feishu_tools.push_notification_card",
        new_callable=AsyncMock,
        return_value={"success": True, "output": "pushed (ticket_done)"},
    ) as push:
        result = await FeishuPush().execute(
            {"card_type": "ticket_done", "content": content},
            fake_session,
        )

    assert result.success is True
    payload_content = push.await_args.kwargs["content"]
    assert "Zendesk 返回无附件或图片" not in payload_content
    assert "当前会话已有客户图片证据" in payload_content


@pytest.mark.asyncio
async def test_push_alert_urgent(fake_session):
    with patch(
        "zd_agent.tools.feishu_tools.push_notification_card",
        new_callable=AsyncMock,
        return_value={"success": True, "output": "pushed (alert)"},
    ) as push:
        result = await FeishuPush().execute(
            {
                "card_type": "alert",
                "title": "Disk Full",
                "content": "> 95%",
                "urgent": True,
            },
            fake_session,
        )

    assert result.success is True
    kwargs = push.await_args.kwargs
    assert kwargs["urgent"] is True
    assert kwargs["title"] == "Disk Full"


@pytest.mark.asyncio
async def test_push_rejects_invalid_arg_types(fake_session):
    with patch(
        "zd_agent.tools.feishu_tools.push_notification_card",
        new_callable=AsyncMock,
    ) as push:
        result = await FeishuPush().execute(
            {"card_type": "alert", "title": "T", "content": "M", "urgent": "false"},
            fake_session,
        )

    assert result.success is False
    assert "urgent" in (result.error or "")
    push.assert_not_awaited()

    with patch(
        "zd_agent.tools.feishu_tools.push_notification_card",
        new_callable=AsyncMock,
    ) as push:
        result = await FeishuPush().execute(
            {"card_type": "ticket_done", "content": ["summary"]},
            fake_session,
        )

    assert result.success is False
    assert "content" in (result.error or "")
    push.assert_not_awaited()


@pytest.mark.asyncio
async def test_push_rejects_missing_required_args(fake_session):
    result = await FeishuPush().execute({"content": "M"}, fake_session)
    assert result.success is False
    assert result.error == "card_type is required"

    result = await FeishuPush().execute({"card_type": "alert"}, fake_session)
    assert result.success is False
    assert result.error == "content is required"


@pytest.mark.asyncio
async def test_push_rejects_blank_string_args(fake_session):
    result = await FeishuPush().execute(
        {"card_type": "   ", "content": "M"},
        fake_session,
    )
    assert result.success is False
    assert result.error == "card_type is empty"

    result = await FeishuPush().execute(
        {"card_type": "alert", "content": "   "},
        fake_session,
    )
    assert result.success is False
    assert result.error == "content is empty"


@pytest.mark.asyncio
async def test_push_mcp_not_configured_returns_error(fake_session):
    with patch(
        "zd_agent.tools.feishu_tools.push_notification_card",
        new_callable=AsyncMock,
        return_value={
            "success": False,
            "code": "NOTIFICATION_SERVICE_URL_MISSING",
            "error": "not configured",
        },
    ):
        result = await FeishuPush().execute(
            {"card_type": "alert", "title": "T", "content": "M"},
            fake_session,
        )

    assert result.success is False
    assert "not configured" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_push_send_failure_returns_error(fake_session):
    with patch(
        "zd_agent.tools.feishu_tools.push_notification_card",
        new_callable=AsyncMock,
        return_value={"success": False, "code": "NETWORK", "error": "network"},
    ):
        result = await FeishuPush().execute(
            {"card_type": "alert", "title": "T", "content": "M"},
            fake_session,
        )
    assert result.success is False
    assert "network" in (result.error or "").lower()

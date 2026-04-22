"""Test FeishuPush tool with mocked FeishuClient."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zd_agent.tools.feishu_tools import FeishuPush


@pytest.fixture
def feishu_cfg(monkeypatch):
    cfg = MagicMock()
    cfg.feishu.app_id = "cli_xxx"
    cfg.feishu.app_secret = "s"
    cfg.feishu.default_chat_id = "oc_xxx"
    monkeypatch.setattr("zd_agent.tools.feishu_tools.get_config", lambda: cfg)
    return cfg


@pytest.mark.asyncio
async def test_push_ticket_done(fake_session, feishu_cfg):
    tool = FeishuPush()
    with patch("zd_agent.tools.feishu_tools.FeishuClient") as m:
        m.return_value.send_ticket_done = AsyncMock()
        r = await tool.execute(
            {"card_type": "ticket_done", "content": "summary text",
             "zendesk_url": "https://z/12345"},
            fake_session,
        )
    assert r.success is True
    assert "ticket_done" in r.output
    # verify the mock was called with expected ticket_id derived from session
    m.return_value.send_ticket_done.assert_awaited_once()
    kwargs = m.return_value.send_ticket_done.await_args.kwargs
    assert kwargs["ticket_id"] == "12345"
    assert kwargs["summary"] == "summary text"


@pytest.mark.asyncio
async def test_push_ticket_escalate(fake_session, feishu_cfg):
    tool = FeishuPush()
    with patch("zd_agent.tools.feishu_tools.FeishuClient") as m:
        m.return_value.send_ticket_escalate = AsyncMock()
        r = await tool.execute(
            {"card_type": "ticket_escalate",
             "content": "missing ws id", "zendesk_url": "https://z/1"},
            fake_session,
        )
    assert r.success is True


@pytest.mark.asyncio
async def test_push_alert_urgent(fake_session, feishu_cfg):
    tool = FeishuPush()
    with patch("zd_agent.tools.feishu_tools.FeishuClient") as m:
        m.return_value.send_alert = AsyncMock()
        r = await tool.execute(
            {"card_type": "alert", "title": "Disk Full",
             "content": "> 95%", "urgent": True},
            fake_session,
        )
    assert r.success is True
    kwargs = m.return_value.send_alert.await_args.kwargs
    assert kwargs["urgent"] is True
    assert kwargs["title"] == "Disk Full"


@pytest.mark.asyncio
async def test_push_not_configured(fake_session, monkeypatch):
    cfg = MagicMock()
    cfg.feishu.app_id = ""
    cfg.feishu.app_secret = ""
    cfg.feishu.default_chat_id = ""
    monkeypatch.setattr("zd_agent.tools.feishu_tools.get_config", lambda: cfg)
    tool = FeishuPush()
    r = await tool.execute(
        {"card_type": "alert", "title": "T", "content": "M"},
        fake_session,
    )
    assert r.success is False
    assert "not configured" in (r.error or "").lower()


@pytest.mark.asyncio
async def test_push_send_failure_returns_error(fake_session, feishu_cfg):
    tool = FeishuPush()
    with patch("zd_agent.tools.feishu_tools.FeishuClient") as m:
        m.return_value.send_alert = AsyncMock(side_effect=RuntimeError("network"))
        r = await tool.execute(
            {"card_type": "alert", "title": "T", "content": "M"},
            fake_session,
        )
    assert r.success is False
    assert "network" in (r.error or "").lower()

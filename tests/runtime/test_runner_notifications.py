"""Tests for deterministic post-session notifications."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from zd_agent.session.agent_session import AgentSession, SessionStatus


@pytest.mark.asyncio
async def test_run_agent_falls_back_to_ticket_done_when_reply_was_sent(monkeypatch):
    from zd_agent.runtime import runner

    cfg = MagicMock()
    cfg.agent.guardrails = MagicMock()
    cfg.llm.model = "test-model"
    cfg.sandbox.cleanup_on_success = False
    cfg.sandbox.cleanup_on_failure = False
    monkeypatch.setattr(runner, "get_config", lambda: cfg)
    monkeypatch.setattr(runner, "_send_ack_note", AsyncMock())
    monkeypatch.setattr(runner, "_ensure_archive", lambda *_args, **_kwargs: None)

    class FakeSandbox:
        async def create(self, ticket_id):
            return f"/tmp/{ticket_id}"

        async def cleanup(self):
            raise AssertionError("cleanup should be disabled in this test")

    async def fake_tool_loop(session, *_args, **_kwargs):
        session.status = SessionStatus.COMPLETED
        session.reply_sent = True

    notify = AsyncMock()
    monkeypatch.setattr(runner, "create_sandbox", lambda: FakeSandbox())
    monkeypatch.setattr(runner, "create_tool_registry", lambda: MagicMock())
    monkeypatch.setattr(runner, "LLMClient", lambda: MagicMock())
    monkeypatch.setattr(runner, "Guardrails", lambda config: MagicMock(elapsed_seconds=0))
    monkeypatch.setattr(runner, "run_tool_loop", fake_tool_loop)
    monkeypatch.setattr(runner, "notify_ticket_done_best_effort", notify)
    monkeypatch.setattr(
        "zd_agent.runtime.customer_profile.prefetch_customer_profile",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        "zd_agent.tools.saas_version.resolve_deployed_version",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "zd_agent.runtime.prompt_loader.build_static_system_prompt",
        lambda: "system",
    )

    session = AgentSession(ticket_id=2809)
    await runner.run_agent(session)

    notify.assert_awaited_once_with(session)


@pytest.mark.asyncio
async def test_ticket_done_fallback_retries_after_failed_llm_feishu(monkeypatch):
    from zd_agent.services import feishu_notifications

    cfg = MagicMock()
    cfg.feishu.webhook_url = "https://open.feishu.cn/open-apis/bot/v2/hook/test"
    cfg.feishu.webhook_secret = ""
    monkeypatch.setattr(feishu_notifications, "get_config", lambda: cfg)

    session = AgentSession(ticket_id=2809)
    session.reply_sent = True
    session.log_tool_call(
        "feishu_push",
        {"card_type": "ticket_done", "content": "summary"},
        "error",
        success=False,
        error="Feishu webhook error code=11232",
    )

    client_cls = MagicMock()
    client_cls.return_value.send_ticket_done = AsyncMock()
    monkeypatch.setattr(feishu_notifications, "FeishuClient", client_cls)

    pushed = await feishu_notifications.notify_ticket_done_best_effort(session)

    assert pushed is True
    client_cls.return_value.send_ticket_done.assert_awaited_once()


@pytest.mark.asyncio
async def test_ticket_done_fallback_skips_if_llm_already_pushed(monkeypatch):
    from zd_agent.services import feishu_notifications

    session = AgentSession(ticket_id=2809)
    session.reply_sent = True
    session.log_tool_call(
        "feishu_push",
        {"card_type": "ticket_done", "content": "summary"},
        "pushed",
        success=True,
        error=None,
    )

    client_cls = MagicMock()
    monkeypatch.setattr(feishu_notifications, "FeishuClient", client_cls)

    pushed = await feishu_notifications.notify_ticket_done_best_effort(session)

    assert pushed is False
    client_cls.assert_not_called()

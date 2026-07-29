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
    monkeypatch.setattr(runner, "_capture_customer_message_snapshot", AsyncMock())

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
async def test_run_agent_does_not_send_ticket_done_for_handoff(monkeypatch):
    from zd_agent.runtime import runner

    cfg = MagicMock()
    cfg.agent.guardrails = MagicMock()
    cfg.llm.model = "test-model"
    cfg.sandbox.cleanup_on_success = False
    cfg.sandbox.cleanup_on_failure = False
    monkeypatch.setattr(runner, "get_config", lambda: cfg)
    monkeypatch.setattr(runner, "_send_ack_note", AsyncMock())
    monkeypatch.setattr(runner, "_capture_customer_message_snapshot", AsyncMock())

    class FakeSandbox:
        async def create(self, ticket_id):
            return f"/tmp/{ticket_id}"

        async def cleanup(self):
            raise AssertionError("cleanup should be disabled in this test")

    async def fake_tool_loop(session, *_args, **_kwargs):
        session.status = SessionStatus.COMPLETED
        session.reply_sent = True
        session.public_reply_type = "handoff"

    notify = AsyncMock()
    monkeypatch.setattr(runner, "create_sandbox", lambda: FakeSandbox())
    monkeypatch.setattr(runner, "create_tool_registry", lambda: MagicMock())
    monkeypatch.setattr(runner, "LLMClient", lambda: MagicMock())
    monkeypatch.setattr(
        runner,
        "Guardrails",
        lambda config: MagicMock(elapsed_seconds=0),
    )
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

    await runner.run_agent(AgentSession(ticket_id=2810))

    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_agent_writes_failure_note_when_tool_loop_times_out(monkeypatch):
    from zd_agent.runtime import runner

    cfg = MagicMock()
    cfg.agent.guardrails = MagicMock()
    cfg.llm.model = "test-model"
    cfg.sandbox.cleanup_on_success = False
    cfg.sandbox.cleanup_on_failure = False
    monkeypatch.setattr(runner, "get_config", lambda: cfg)
    monkeypatch.setattr(runner, "_send_ack_note", AsyncMock())
    send_error_note = AsyncMock()
    monkeypatch.setattr(runner, "_send_error_note", send_error_note)
    notify_failure = AsyncMock(return_value=True)
    monkeypatch.setattr(
        runner,
        "send_feishu_escalation_best_effort",
        notify_failure,
        raising=False,
    )
    monkeypatch.setattr(runner, "_capture_customer_message_snapshot", AsyncMock())

    class FakeSandbox:
        async def create(self, ticket_id):
            return f"/tmp/{ticket_id}"

        async def cleanup(self):
            raise AssertionError("cleanup should be disabled in this test")

    async def fake_tool_loop(session, *_args, **_kwargs):
        session.status = SessionStatus.TIMEOUT
        session.error_message = "processing timeout after 600 seconds"

    monkeypatch.setattr(runner, "create_sandbox", lambda: FakeSandbox())
    monkeypatch.setattr(runner, "create_tool_registry", lambda: MagicMock())
    monkeypatch.setattr(runner, "LLMClient", lambda: MagicMock())
    monkeypatch.setattr(runner, "Guardrails", lambda config: MagicMock(elapsed_seconds=0))
    monkeypatch.setattr(runner, "run_tool_loop", fake_tool_loop)
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

    session = AgentSession(ticket_id=3436)
    await runner.run_agent(session)

    send_error_note.assert_awaited_once_with(
        3436,
        "processing timeout after 600 seconds",
    )
    notify_failure.assert_awaited_once_with(
        ticket_id=3436,
        reason="processing timeout after 600 seconds",
        source="agent_session_failure",
        handoff_reply_sent=False,
    )


@pytest.mark.asyncio
async def test_ticket_done_fallback_retries_after_failed_llm_feishu(monkeypatch):
    from zd_agent.services import feishu_notifications

    session = AgentSession(ticket_id=2809)
    session.reply_sent = True
    session.log_tool_call(
        "feishu_push",
        {"card_type": "ticket_done", "content": "summary"},
        "error",
        success=False,
        error="Feishu webhook error code=11232",
    )

    push = AsyncMock(return_value={"success": True, "output": "pushed (ticket_done)"})
    monkeypatch.setattr(feishu_notifications, "push_notification_card", push)

    pushed = await feishu_notifications.notify_ticket_done_best_effort(session)

    assert pushed is True
    push.assert_awaited_once()
    assert push.await_args.kwargs["card_type"] == "ticket_done"


def test_ticket_done_fallback_summary_formats_customer_reply_digest():
    from zd_agent.services.feishu_notifications import _ticket_done_summary

    session = AgentSession(ticket_id=3066)
    session.reply_draft = (
        "Hi,\n\n"
        "The workspace subscription is healthy in billing DB. "
        "Please retry /subscription/info and provide the endpoint and timestamp if it persists.\n\n"
        "🤖 This message is sent by Dify AI (Dify SaaS Agent). "
        "If you would prefer a human teammate, reply with \"human\"."
    )

    summary = _ticket_done_summary(session)

    assert summary.startswith("处理结果：已回复客户\n")
    assert "客户回复：" in summary
    assert "The workspace subscription is healthy in billing DB." in summary
    assert "Dify SaaS Agent" not in summary


def test_ticket_done_fallback_summary_caps_long_low_punctuation_reply():
    from zd_agent.services.feishu_notifications import _ticket_done_summary

    session = AgentSession(ticket_id=3066)
    session.reply_draft = (
        "Hello,\n\n"
        + " ".join(["This reply has important details but no sentence break"] * 40)
        + " tail-marker\n\n"
        "🤖 This message is sent by Dify AI (Dify SaaS Agent)."
    )

    summary = _ticket_done_summary(session)
    digest = summary.split("客户回复：", 1)[1]

    assert "tail-marker" not in digest
    assert len(digest) <= 323


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

    push = AsyncMock()
    monkeypatch.setattr(feishu_notifications, "push_notification_card", push)

    pushed = await feishu_notifications.notify_ticket_done_best_effort(session)

    assert pushed is False
    push.assert_not_awaited()

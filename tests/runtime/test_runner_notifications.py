"""Tests for deterministic post-session notifications."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from zd_agent.session.agent_session import AgentSession, SessionStatus


@pytest.mark.asyncio
async def test_v2_runner_notifies_after_confirmed_direct_reply(monkeypatch):
    from zd_agent.runtime import runner

    cfg = MagicMock()
    cfg.agent.guardrails = MagicMock()
    cfg.casework.mode.value = "casework"
    monkeypatch.setattr(runner, "_send_ack_note", AsyncMock())
    notify = AsyncMock()
    archive = MagicMock()
    monkeypatch.setattr(runner, "create_casework_tool_registry", MagicMock())
    monkeypatch.setattr(runner, "LLMClient", lambda: MagicMock(_provider=object()))
    monkeypatch.setattr(runner, "Guardrails", lambda config: MagicMock(elapsed_seconds=0))
    monkeypatch.setattr(runner, "execute_casework_v2", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        runner,
        "apply_casework_execution_to_session",
        lambda session, execution: (
            setattr(session, "status", SessionStatus.COMPLETED),
            setattr(session, "reply_sent", True),
            setattr(session, "casework_ownership_action", "none"),
        ),
    )
    monkeypatch.setattr(runner, "notify_ticket_done_best_effort", notify)
    monkeypatch.setattr(runner, "ensure_archive", archive, raising=False)

    session = AgentSession(ticket_id=2809)
    await runner._run_casework_agent(session, cfg)

    notify.assert_awaited_once_with(session)
    archive.assert_called_once()
    assert archive.call_args.args[0] is session


@pytest.mark.asyncio
async def test_v2_runner_does_not_notify_when_ownership_is_required(monkeypatch):
    from zd_agent.runtime import runner

    cfg = MagicMock()
    cfg.agent.guardrails = MagicMock()
    cfg.casework.mode.value = "casework"
    monkeypatch.setattr(runner, "_send_ack_note", AsyncMock())
    notify = AsyncMock()
    monkeypatch.setattr(runner, "create_casework_tool_registry", MagicMock())
    monkeypatch.setattr(runner, "LLMClient", lambda: MagicMock(_provider=object()))
    monkeypatch.setattr(
        runner,
        "Guardrails",
        lambda config: MagicMock(elapsed_seconds=0),
    )
    monkeypatch.setattr(runner, "execute_casework_v2", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        runner,
        "apply_casework_execution_to_session",
        lambda session, execution: (
            setattr(session, "status", SessionStatus.COMPLETED),
            setattr(session, "reply_sent", True),
            setattr(session, "casework_ownership_action", "billing"),
        ),
    )
    monkeypatch.setattr(runner, "notify_ticket_done_best_effort", notify)
    monkeypatch.setattr(runner, "ensure_archive", MagicMock())

    await runner._run_casework_agent(AgentSession(ticket_id=2810), cfg)

    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_v2_runner_failure_never_writes_legacy_customer_handoff(monkeypatch):
    from zd_agent.runtime import runner

    cfg = MagicMock()
    cfg.agent.guardrails = MagicMock()
    cfg.casework.mode.value = "casework"
    monkeypatch.setattr(runner, "_send_ack_note", AsyncMock())
    monkeypatch.setattr(runner, "create_casework_tool_registry", MagicMock())
    monkeypatch.setattr(runner, "LLMClient", lambda: MagicMock(_provider=object()))
    monkeypatch.setattr(runner, "Guardrails", lambda config: MagicMock(elapsed_seconds=0))
    monkeypatch.setattr(
        runner,
        "execute_casework_v2",
        AsyncMock(side_effect=TimeoutError("provider timeout")),
    )
    monkeypatch.setattr(runner, "ensure_archive", MagicMock())
    notify = AsyncMock()
    monkeypatch.setattr(runner, "notify_ticket_done_best_effort", notify)

    session = AgentSession(ticket_id=3436)
    await runner._run_casework_agent(session, cfg)

    assert session.status is SessionStatus.FAILED
    assert session.reply_sent is False
    assert not hasattr(runner, "_send_error_note")
    assert not hasattr(runner, "send_feishu_escalation_best_effort")
    notify.assert_not_awaited()


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

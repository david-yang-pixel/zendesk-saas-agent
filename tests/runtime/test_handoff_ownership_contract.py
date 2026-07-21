"""RED contracts for deterministic ownership after an Agent handoff reply.

Sending ``reply_type=handoff`` is both a customer-visible delivery and an
ownership transfer.  The transfer must not depend on the model remembering to
call another tool in a later round: the successful public-reply boundary must
record the reply type and deterministically attempt both operator side effects.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from zd_agent import config as config_module
from zd_agent.config import AppConfig, PublicReplyConfig
from zd_agent.services import escalation_notifications
from zd_agent.services.trigger_gates import handle_trigger_entry_gate
from zd_agent.services.trigger_state_machine import apply_session_finished
from zd_agent.session.agent_session import AgentSession, SessionStatus
from zd_agent.tools.zendesk_public_reply import ZendeskPublicReply
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


_TICKET_ID = 3461
_NOW = datetime(2026, 7, 16, 7, 0, tzinfo=timezone.utc)


def _config() -> AppConfig:
    cfg = AppConfig()
    cfg.public_reply = PublicReplyConfig(enabled=True)
    cfg.zendesk.subdomain = "langgenius"
    return cfg


async def _execute_real_handoff_tool(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    *,
    notification_push: AsyncMock | None = None,
    zendesk_metadata_update: AsyncMock | None = None,
    sent_now: bool = True,
) -> tuple[AgentSession, object]:
    """Run the real tool while replacing every external write with a fake."""
    cfg = _config()
    cfg.resources.base_dir = str(tmp_path)
    cfg.resources.state_dir = ".state"
    monkeypatch.setattr(
        "zd_agent.tools.zendesk_public_reply.get_config",
        lambda: cfg,
    )
    monkeypatch.setattr(config_module, "get_config", lambda: cfg)
    monkeypatch.setattr(
        "zd_agent.tools.zendesk_public_reply._send_public_reply_safely",
        AsyncMock(return_value=sent_now),
    )
    monkeypatch.setattr(
        "zd_agent.tools.zendesk_public_reply._check_public_reply_readiness",
        AsyncMock(
            return_value=type(
                "Ready",
                (),
                {"continue_send": True, "recovery": None},
            )()
        ),
    )
    monkeypatch.setattr(
        "zd_agent.tools.zendesk_public_reply._prepare_durable_delivery_payload",
        lambda **kwargs: (
            kwargs["body_with_footer"],
            kwargs["internal_analysis"],
            kwargs["reply_type"],
            None,
            None,
        ),
    )
    monkeypatch.setattr(
        "zd_agent.tools.zendesk_public_reply.ensure_post_public_internal_note",
        AsyncMock(return_value=True),
    )

    if notification_push is not None:
        monkeypatch.setattr(
            escalation_notifications,
            "push_notification_card",
            notification_push,
        )
    if zendesk_metadata_update is not None:
        monkeypatch.setattr(
            escalation_notifications,
            "call_zendesk_tool",
            zendesk_metadata_update,
        )

    session = AgentSession(
        ticket_id=_TICKET_ID,
        trigger_type="production",
        delivery_attempt_id="zendesk_webhook:3461:customer-message-1",
    )
    result = await ZendeskPublicReply().execute(
        {
            "ticket_id": _TICKET_ID,
            "body": (
                "Thank you for your patience. This case needs review by a "
                "support engineer, and we will follow up in this ticket."
            ),
            "reply_type": "handoff",
            "conversation_id": "conversation-3461",
            "internal_analysis": (
                "自动调查无法安全完成结论，需要人工技术支持继续审核。"
            ),
        },
        session,
    )
    return session, result


@pytest.mark.asyncio
async def test_successful_handoff_reply_is_recorded_on_agent_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """The terminal session fact must distinguish handoff from other replies."""
    session, result = await _execute_real_handoff_tool(monkeypatch, tmp_path)

    assert result.success is True
    assert session.reply_sent is True
    assert session.public_reply_type == "handoff"


@pytest.mark.asyncio
async def test_successful_handoff_deterministically_attempts_both_escalations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Feishu failure must not prevent the independent Zendesk tag attempt."""
    notification_push = AsyncMock(side_effect=RuntimeError("feishu unavailable"))
    zendesk_metadata_update = AsyncMock(
        return_value={"success": True, "data": {"ticket_id": _TICKET_ID}},
    )

    _, result = await _execute_real_handoff_tool(
        monkeypatch,
        tmp_path,
        notification_push=notification_push,
        zendesk_metadata_update=zendesk_metadata_update,
    )

    assert result.success is True
    notification_push.assert_awaited_once()
    notification_kwargs = notification_push.await_args.kwargs
    assert notification_kwargs["card_type"] == "ticket_escalate"
    assert notification_kwargs["ticket_id"] == str(_TICKET_ID)
    assert "Agent 已完成初步处理，后续需要人工继续跟进。" in notification_kwargs[
        "content"
    ]
    assert "agent_handoff" not in notification_kwargs["content"]

    zendesk_metadata_update.assert_awaited_once()
    tool_name, tool_args = zendesk_metadata_update.await_args.args
    assert tool_name == "update_ticket_metadata"
    assert tool_args["ticket_id"] == _TICKET_ID
    assert tool_args["add_tags"] == ["escalated-to-human"]


@pytest.mark.asyncio
async def test_reconciled_handoff_does_not_repeat_operator_notifications(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    notification_push = AsyncMock(return_value={"success": True})
    zendesk_metadata_update = AsyncMock(return_value={"success": True})

    first_session, first_result = await _execute_real_handoff_tool(
        monkeypatch,
        tmp_path,
        notification_push=notification_push,
        zendesk_metadata_update=zendesk_metadata_update,
        sent_now=True,
    )
    session, result = await _execute_real_handoff_tool(
        monkeypatch,
        tmp_path,
        notification_push=notification_push,
        zendesk_metadata_update=zendesk_metadata_update,
        sent_now=False,
    )

    assert first_result.success is True
    assert first_session.reply_sent is True
    assert result.success is True
    assert session.reply_sent is True
    notification_push.assert_awaited_once()
    zendesk_metadata_update.assert_awaited_once()


def test_handoff_completion_transfers_state_and_gates_non_mention_trigger(
    tmp_path,
) -> None:
    """A delivered handoff keeps human ownership after the Agent exits."""
    tracker = TicketStateTracker(tmp_path / "tickets.json")
    session = AgentSession(
        ticket_id=_TICKET_ID,
        session_id="session-handoff",
        trigger_type="production",
    )
    tracker.set(
        _TICKET_ID,
        TicketState(
            state="processing",
            first_seen=_NOW - timedelta(minutes=5),
            active_session_id=session.session_id,
        ),
    )
    session.reply_sent = True
    session.public_reply_type = "handoff"
    session.status = SessionStatus.COMPLETED

    changed = apply_session_finished(tracker, session, now=_NOW)

    persisted = TicketStateTracker(tmp_path / "tickets.json").get(_TICKET_ID)
    assert changed is True
    assert persisted is not None
    assert persisted.state == "escalated_to_human"
    assert persisted.escalated_reason == "agent_handoff"
    assert persisted.escalated_at == _NOW
    assert persisted.active_session_id == ""

    gate = handle_trigger_entry_gate(
        ticket_id=_TICKET_ID,
        source="zendesk_webhook",
        state=tracker,
        log_path=tmp_path / "trigger.jsonl",
        cfg=_config(),
        is_paused_fn=lambda _cfg: False,
    )
    assert gate.handled is True
    assert gate.action == "escalated_to_human"

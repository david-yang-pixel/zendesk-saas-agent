"""Regression tests for trigger-state ownership by AgentSession."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from zd_agent.services.direct_dispatch import handle_direct_dispatch_source
from zd_agent.services.trigger_state_machine import apply_session_finished
from zd_agent.session.agent_session import AgentSession, SessionStatus
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


def _now() -> datetime:
    return datetime(2026, 7, 16, 6, 30, tzinfo=timezone.utc)


def _cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.trigger_decider.post_agent_cooldown_seconds = 180
    return cfg


@pytest.mark.asyncio
async def test_direct_dispatch_persists_returned_session_as_state_owner(
    tmp_path,
) -> None:
    """The durable processing state must identify the dispatched session."""
    state_path = tmp_path / "tickets.json"
    state = TicketStateTracker(state_path)
    dispatched_session = AgentSession(
        ticket_id=3461,
        session_id="session-current",
    )
    manager = MagicMock()
    manager.dispatch = AsyncMock(return_value=dispatched_session)

    result = await handle_direct_dispatch_source(
        ticket_id=3461,
        msg={"source": "mention", "hint": "retry investigation"},
        manager=manager,
        state=state,
        log_path=tmp_path / "trigger.jsonl",
        cfg=_cfg(),
        now=_now(),
    )

    persisted = TicketStateTracker(state_path).get(3461)
    assert result.dispatched is True
    assert persisted is not None
    assert persisted.state == "processing"
    assert persisted.active_session_id == dispatched_session.session_id


def test_older_session_completion_cannot_overwrite_new_processing_owner(
    tmp_path,
) -> None:
    """A stale callback must not finish a newer session's processing state."""
    tracker = TicketStateTracker(tmp_path / "tickets.json")
    tracker.set(
        3461,
        TicketState(
            state="processing",
            first_seen=_now() - timedelta(minutes=10),
            active_session_id="session-new",
        ),
    )
    stale_session = AgentSession(
        ticket_id=3461,
        session_id="session-old",
    )
    stale_session.status = SessionStatus.COMPLETED
    stale_session.reply_sent = True

    apply_session_finished(tracker, stale_session, now=_now())

    persisted = TicketStateTracker(tmp_path / "tickets.json").get(3461)
    assert persisted is not None
    assert persisted.state == "processing"
    assert persisted.active_session_id == "session-new"
    assert persisted.last_agent_finish_ts is None


def test_session_completion_cannot_overwrite_human_escalation(tmp_path) -> None:
    """Human ownership remains terminal even when the agent sent a reply."""
    escalated_at = _now() - timedelta(minutes=1)
    tracker = TicketStateTracker(tmp_path / "tickets.json")
    tracker.set(
        3461,
        TicketState(
            state="escalated_to_human",
            first_seen=_now() - timedelta(minutes=10),
            escalated_at=escalated_at,
            escalated_reason="customer_requested",
        ),
    )
    session = AgentSession(ticket_id=3461, session_id="session-agent")
    session.status = SessionStatus.COMPLETED
    session.reply_sent = True

    apply_session_finished(tracker, session, now=_now())

    persisted = TicketStateTracker(tmp_path / "tickets.json").get(3461)
    assert persisted is not None
    assert persisted.state == "escalated_to_human"
    assert persisted.escalated_at == escalated_at
    assert persisted.escalated_reason == "customer_requested"


def test_session_completion_cannot_overwrite_new_form_wait(tmp_path) -> None:
    """A new customer-side lifecycle state wins over an older session callback."""
    tracker = TicketStateTracker(tmp_path / "tickets.json")
    tracker.set(
        3461,
        TicketState(
            state="waiting_for_form",
            first_seen=_now() - timedelta(minutes=10),
            last_customer_comment_id="message-newer-than-session",
            last_verdict="waiting_for_form",
        ),
    )
    session = AgentSession(ticket_id=3461, session_id="session-old")
    session.status = SessionStatus.COMPLETED
    session.reply_sent = True

    apply_session_finished(tracker, session, now=_now())

    persisted = TicketStateTracker(tmp_path / "tickets.json").get(3461)
    assert persisted is not None
    assert persisted.state == "waiting_for_form"
    assert persisted.last_customer_comment_id == "message-newer-than-session"
    assert persisted.last_agent_finish_ts is None

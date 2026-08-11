"""A completed reply cannot synthesize v2 ownership without a typed effect."""

from datetime import datetime, timedelta, timezone

from zd_agent.config import AppConfig
from zd_agent.services.trigger_gates import handle_trigger_entry_gate
from zd_agent.services.trigger_state_machine import apply_session_finished
from zd_agent.session.agent_session import AgentSession, SessionStatus
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


_TICKET_ID = 3461
_NOW = datetime(2026, 7, 16, 7, 0, tzinfo=timezone.utc)


def test_completed_reply_without_typed_effect_cannot_synthesize_human_ownership(
    tmp_path,
) -> None:
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
    session.status = SessionStatus.COMPLETED

    changed = apply_session_finished(tracker, session, now=_NOW)

    persisted = TicketStateTracker(tmp_path / "tickets.json").get(_TICKET_ID)
    assert changed is True
    assert persisted is not None
    assert persisted.state == "processed"
    assert persisted.escalated_reason == ""
    assert persisted.escalated_at is None
    assert persisted.active_session_id == ""

    gate = handle_trigger_entry_gate(
        ticket_id=_TICKET_ID,
        source="zendesk_webhook",
        state=tracker,
        log_path=tmp_path / "trigger.jsonl",
        cfg=AppConfig(),
        is_paused_fn=lambda _cfg: False,
    )
    assert gate.handled is False
    assert gate.action == "continue"

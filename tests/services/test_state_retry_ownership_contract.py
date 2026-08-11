"""Contracts for durable Agent retry budgets and session ownership."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from zd_agent.services.trigger_state_machine import (
    apply_escalated_to_human,
    apply_session_finished,
    apply_verdict_enter_silence,
    apply_verdict_processed,
    apply_verdict_waiting_for_issue,
    apply_waiting_for_form,
)
from zd_agent.session.agent_session import AgentSession, SessionStatus
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


_NOW = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)


def _owned_session(
    ticket_id: int,
    *,
    status: SessionStatus,
    reply_sent: bool = False,
    reply_type: str = "solution",
) -> AgentSession:
    session = AgentSession(ticket_id=ticket_id, session_id="session-current")
    session.status = status
    session.reply_sent = reply_sent
    session.public_reply_type = reply_type
    return session


@pytest.mark.parametrize(
    "status",
    [
        SessionStatus.FAILED,
        SessionStatus.TIMEOUT,
        SessionStatus.DEFERRED,
        SessionStatus.COMPLETED,
    ],
)
def test_every_owned_no_reply_outcome_consumes_retry_budget(
    tmp_path,
    status: SessionStatus,
) -> None:
    """A third no-delivery outcome must stop the persistent retry loop."""
    tracker = TicketStateTracker(tmp_path / f"{status.value}.json")
    tracker.set(
        3461,
        TicketState(
            state="processing",
            first_seen=_NOW - timedelta(minutes=10),
            active_session_id="session-current",
            delivery_attempt_id="delivery-stable",
            consecutive_agent_failures=2,
        ),
    )

    changed = apply_session_finished(
        tracker,
        _owned_session(3461, status=status),
        now=_NOW,
    )

    persisted = TicketStateTracker(tracker.path).get(3461)
    assert changed is True
    assert persisted is not None
    assert persisted.state == "escalated_to_human"
    assert persisted.escalated_reason == "agent_retry_exhausted"
    assert persisted.consecutive_agent_failures == 3
    assert persisted.active_session_id == ""
    assert persisted.delivery_attempt_id == "delivery-stable"


def test_deferred_retry_consumes_budget_and_keeps_backoff_before_exhaustion(
    tmp_path,
) -> None:
    tracker = TicketStateTracker(tmp_path / "tickets.json")
    tracker.set(
        3461,
        TicketState(
            state="processing",
            first_seen=_NOW - timedelta(minutes=10),
            active_session_id="session-current",
            consecutive_agent_failures=0,
        ),
    )

    apply_session_finished(
        tracker,
        _owned_session(3461, status=SessionStatus.DEFERRED),
        now=_NOW,
    )

    persisted = TicketStateTracker(tracker.path).get(3461)
    assert persisted is not None
    assert persisted.state == "in_silence_window"
    assert persisted.consecutive_agent_failures == 1
    assert persisted.last_verdict == "agent_deferred"
    assert persisted.silence_window_end == _NOW + timedelta(seconds=60)


def test_rebuilding_transitions_preserve_durable_failure_budget(tmp_path) -> None:
    """Classification/form transitions cannot silently reset retry history."""
    existing = TicketState(
        state="processing",
        first_seen=_NOW - timedelta(hours=1),
        delivery_attempt_id="delivery-stable",
        consecutive_agent_failures=2,
    )
    transitions = (
        lambda tracker, ticket_id: apply_waiting_for_form(
            tracker,
            ticket_id,
            existing=existing,
            latest_cid="cid-form",
            latest_ts=_NOW,
            now=_NOW,
        ),
        lambda tracker, ticket_id: apply_escalated_to_human(
            tracker,
            ticket_id,
            existing=existing,
            latest_cid="cid-human",
            latest_ts=_NOW,
            now=_NOW,
            verdict_str="request_human",
            reason="customer_requested",
        ),
        lambda tracker, ticket_id: apply_verdict_processed(
            tracker,
            ticket_id,
            existing=existing,
            latest_cid="cid-processed",
            latest_ts=_NOW,
            now=_NOW,
            verdict_str="self_resolved",
            ask_count_now=0,
        ),
        lambda tracker, ticket_id: apply_verdict_enter_silence(
            tracker,
            ticket_id,
            existing=existing,
            latest_cid="cid-silence",
            latest_ts=_NOW,
            now=_NOW,
            verdict_str="substantial_issue",
            ask_count_now=0,
            silence_minutes=3,
        ),
        lambda tracker, ticket_id: apply_verdict_waiting_for_issue(
            tracker,
            ticket_id,
            existing=existing,
            latest_cid="cid-waiting",
            latest_ts=_NOW,
            now=_NOW,
            verdict_str="insufficient_info",
            ask_count_now=0,
            sent=False,
        ),
    )

    for ticket_id, transition in enumerate(transitions, start=3500):
        tracker = TicketStateTracker(tmp_path / f"{ticket_id}.json")
        transition(tracker, ticket_id)
        persisted = TicketStateTracker(tracker.path).get(ticket_id)
        assert persisted is not None
        assert persisted.consecutive_agent_failures == 2
        assert persisted.delivery_attempt_id == "delivery-stable"


@pytest.mark.parametrize(
    "state_name",
    ["waiting_for_issue", "processed", "escalated_to_human"],
)
def test_reply_callback_cannot_overwrite_non_processing_state(
    tmp_path,
    state_name: str,
) -> None:
    tracker = TicketStateTracker(tmp_path / f"{state_name}.json")
    tracker.set(
        3461,
        TicketState(
            state=state_name,
            first_seen=_NOW - timedelta(minutes=10),
            active_session_id="session-current",
            last_verdict="newer-lifecycle",
            escalated_reason=(
                "customer_requested"
                if state_name == "escalated_to_human"
                else ""
            ),
        ),
    )
    session = _owned_session(
        3461,
        status=SessionStatus.COMPLETED,
        reply_sent=True,
        reply_type="handoff",
    )

    apply_session_finished(tracker, session, now=_NOW)

    persisted = TicketStateTracker(tracker.path).get(3461)
    assert persisted is not None
    assert persisted.state == state_name
    assert persisted.last_verdict == "newer-lifecycle"
    assert persisted.last_agent_finish_ts is None


def test_unowned_legacy_processing_cannot_accept_terminal_reply_callback(
    tmp_path,
) -> None:
    """Without a durable owner, a reply callback cannot prove its generation."""
    tracker = TicketStateTracker(tmp_path / "terminal.json")
    tracker.set(
        3461,
        TicketState(
            state="processing",
            first_seen=_NOW - timedelta(minutes=10),
            active_session_id="",
            last_verdict="substantial_issue",
        ),
    )

    apply_session_finished(
        tracker,
        _owned_session(
            3461,
            status=SessionStatus.COMPLETED,
            reply_sent=True,
            reply_type="handoff",
        ),
        now=_NOW,
    )

    persisted = TicketStateTracker(tracker.path).get(3461)
    assert persisted is not None
    assert persisted.state == "processing"
    assert persisted.last_agent_finish_ts is None


def test_unowned_legacy_processing_allows_only_no_reply_recovery(tmp_path) -> None:
    """Legacy ownerless records may safely leave processing after a failure."""
    tracker = TicketStateTracker(tmp_path / "retry.json")
    tracker.set(
        3461,
        TicketState(
            state="processing",
            first_seen=_NOW - timedelta(minutes=10),
            active_session_id="",
        ),
    )

    apply_session_finished(
        tracker,
        _owned_session(3461, status=SessionStatus.FAILED),
        now=_NOW,
    )

    persisted = TicketStateTracker(tracker.path).get(3461)
    assert persisted is not None
    assert persisted.state == "in_silence_window"
    assert persisted.consecutive_agent_failures == 1


def test_v2_typed_ownership_terminalizes_without_handoff_reply_type(tmp_path) -> None:
    tracker = TicketStateTracker(tmp_path / "v2-ownership.json")
    tracker.set(
        4001,
        TicketState(
            state="processing",
            first_seen=_NOW - timedelta(minutes=5),
            active_session_id="session-current",
        ),
    )
    session = _owned_session(
        4001,
        status=SessionStatus.COMPLETED,
        reply_sent=True,
        reply_type="",
    )
    session.casework_ownership_action = "engineering"
    session.casework_reason_code = "confirmed_platform_defect"

    apply_session_finished(tracker, session, now=_NOW)

    persisted = TicketStateTracker(tracker.path).get(4001)
    assert persisted is not None
    assert persisted.state == "escalated_to_human"
    assert persisted.escalated_reason == "confirmed_platform_defect"
    assert persisted.last_verdict == "confirmed_platform_defect"

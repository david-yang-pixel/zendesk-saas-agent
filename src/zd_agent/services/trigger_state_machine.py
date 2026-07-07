"""Centralized trigger-state transitions shared by entry points."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from zd_agent.log import get_logger
from zd_agent.session.agent_session import SessionStatus
from zd_agent.trigger.state_store import TicketState, TicketStateTracker

logger = get_logger(module="services.trigger_state_machine")


@dataclass(frozen=True)
class SilenceExpiryTransition:
    ticket_id: int
    queue_source: str
    verdict: str
    silence_end: datetime


@dataclass(frozen=True)
class WaitingPollerTransition:
    ticket_id: int
    kind: str
    prev_state: str
    age_seconds: float = 0.0
    last_verdict: str = ""
    ask_count: int = 0
    should_alert: bool = False


@dataclass(frozen=True)
class VerdictStateTransition:
    ticket_id: int
    kind: str
    prev_state: str | None
    ask_count_before: int
    ask_count_after: int
    silence_min: int = 0
    silence_window_end: datetime | None = None


@dataclass(frozen=True)
class EscalationTransition:
    ticket_id: int
    kind: str
    prev_state: str | None
    reason: str
    ask_count: int
    escalated_at: datetime


def apply_escalated_to_human(
    state: TicketStateTracker,
    ticket_id: str | int,
    *,
    existing: TicketState | None,
    latest_cid: str | None,
    latest_ts: datetime | None,
    now: datetime,
    verdict_str: str,
    reason: str,
) -> EscalationTransition:
    """Persist state for a human handoff before best-effort side effects."""
    ask_count = existing.ask_count if existing else 0
    state.set(ticket_id, TicketState(
        state="escalated_to_human",
        first_seen=existing.first_seen if existing else now,
        last_customer_comment_id=latest_cid,
        last_comment_ts=latest_ts,
        last_eval_at=now,
        last_verdict=verdict_str,
        ask_count=ask_count,
        escalated_at=now,
        escalated_reason=reason,
        # Keep retry/alert timestamps for audit after agent exits the ticket.
        nudge_sent_at=existing.nudge_sent_at if existing else None,
        alert_sent_at=existing.alert_sent_at if existing else None,
        abandoned_alert_sent_at=(
            existing.abandoned_alert_sent_at if existing else None
        ),
        last_agent_finish_ts=(
            existing.last_agent_finish_ts if existing else None
        ),
    ))
    return EscalationTransition(
        ticket_id=int(ticket_id),
        kind="escalated_to_human",
        prev_state=existing.state if existing else None,
        reason=reason,
        ask_count=ask_count,
        escalated_at=now,
    )


def apply_verdict_processed(
    state: TicketStateTracker,
    ticket_id: str | int,
    *,
    existing: TicketState | None,
    latest_cid: str | None,
    latest_ts: datetime | None,
    now: datetime,
    verdict_str: str,
    ask_count_now: int,
) -> VerdictStateTransition:
    """Persist the post-verdict terminal processed state."""
    state.set(ticket_id, TicketState(
        state="processed",
        first_seen=existing.first_seen if existing else now,
        last_customer_comment_id=latest_cid,
        last_comment_ts=latest_ts,
        last_eval_at=now,
        last_verdict=verdict_str,
        ask_count=ask_count_now,
        nudge_sent_at=existing.nudge_sent_at if existing else None,
        abandoned_alert_sent_at=(
            existing.abandoned_alert_sent_at if existing else None
        ),
        last_agent_finish_ts=(
            existing.last_agent_finish_ts if existing else None
        ),
    ))
    return VerdictStateTransition(
        ticket_id=int(ticket_id),
        kind="processed",
        prev_state=existing.state if existing else None,
        ask_count_before=ask_count_now,
        ask_count_after=ask_count_now,
    )


def apply_verdict_enter_silence(
    state: TicketStateTracker,
    ticket_id: str | int,
    *,
    existing: TicketState | None,
    latest_cid: str | None,
    latest_ts: datetime | None,
    now: datetime,
    verdict_str: str,
    ask_count_now: int,
    silence_minutes: int,
) -> VerdictStateTransition:
    """Persist an in-silence-window state for a substantial verdict."""
    window_end = now + timedelta(minutes=silence_minutes)
    state.set(ticket_id, TicketState(
        state="in_silence_window",
        first_seen=existing.first_seen if existing else now,
        last_customer_comment_id=latest_cid,
        last_comment_ts=latest_ts,
        last_eval_at=now,
        silence_window_end=window_end,
        last_verdict=verdict_str,
        ask_count=ask_count_now,
        nudge_sent_at=None,
        alert_sent_at=None,
        abandoned_alert_sent_at=(
            existing.abandoned_alert_sent_at if existing else None
        ),
        last_agent_finish_ts=(
            existing.last_agent_finish_ts if existing else None
        ),
    ))
    return VerdictStateTransition(
        ticket_id=int(ticket_id),
        kind="in_silence_window",
        prev_state=existing.state if existing else None,
        ask_count_before=ask_count_now,
        ask_count_after=ask_count_now,
        silence_min=silence_minutes,
        silence_window_end=window_end,
    )


def apply_verdict_waiting_for_issue(
    state: TicketStateTracker,
    ticket_id: str | int,
    *,
    existing: TicketState | None,
    latest_cid: str | None,
    latest_ts: datetime | None,
    now: datetime,
    verdict_str: str,
    ask_count_now: int,
    sent: bool,
) -> VerdictStateTransition:
    """Persist waiting-for-issue state after the public ask attempt."""
    new_ask_count = ask_count_now + 1 if sent else ask_count_now
    state.set(ticket_id, TicketState(
        state="waiting_for_issue",
        first_seen=existing.first_seen if existing else now,
        last_customer_comment_id=latest_cid,
        last_comment_ts=latest_ts,
        last_eval_at=now,
        last_verdict=verdict_str,
        ask_count=new_ask_count,
        # A new customer message resets one-shot ask/alert markers.
        nudge_sent_at=None,
        alert_sent_at=None,
        abandoned_alert_sent_at=(
            existing.abandoned_alert_sent_at if existing else None
        ),
        last_agent_finish_ts=(
            existing.last_agent_finish_ts if existing else None
        ),
    ))
    return VerdictStateTransition(
        ticket_id=int(ticket_id),
        kind="waiting_for_issue",
        prev_state=existing.state if existing else None,
        ask_count_before=ask_count_now,
        ask_count_after=new_ask_count,
    )


def apply_session_finished(
    state: TicketStateTracker,
    session: Any,
    *,
    now: datetime | None = None,
) -> bool:
    """Apply the state transition for a finished AgentSession.

    Returns ``False`` when the ticket is not tracked by the trigger state store,
    which happens for CLI/manual runs that did not enter via webhook trigger
    state.
    """
    ticket_state = state.get(session.ticket_id)
    if ticket_state is None:
        return False

    finished_at = now or datetime.now(timezone.utc)
    ticket_state.last_agent_finish_ts = finished_at

    reply_sent = bool(getattr(session, "reply_sent", False))
    completed_with_reply = (
        session.status == SessionStatus.COMPLETED
        and reply_sent
    )
    completed_without_reply_from_processing = (
        ticket_state.state == "processing"
        and session.status == SessionStatus.COMPLETED
        and not reply_sent
    )
    failed_from_processing = (
        ticket_state.state == "processing"
        and session.status == SessionStatus.FAILED
    )
    if (
        completed_with_reply
        or completed_without_reply_from_processing
        or failed_from_processing
    ):
        new_state = "processed" if completed_with_reply else "in_silence_window"
        logger.info(
            "session_finished_state_reset",
            ticket_id=session.ticket_id,
            from_state=ticket_state.state,
            to_state=new_state,
            session_status=session.status.value,
            reply_sent=reply_sent,
        )
        ticket_state.state = new_state
        if not completed_with_reply:
            ticket_state.silence_window_end = None

    state.set(session.ticket_id, ticket_state)
    return True


def apply_silence_window_expired(
    state: TicketStateTracker,
    ticket_id: str | int,
    *,
    now: datetime | None = None,
) -> SilenceExpiryTransition | None:
    """Advance an expired silence-window ticket and describe the queue action."""
    rec = state.get(ticket_id)
    if rec is None:
        return None
    end = rec.silence_window_end
    if end is None:
        return None

    current_time = now or datetime.now(timezone.utc)
    if end > current_time:
        return None

    verdict = rec.last_verdict or ""
    if verdict == "substantial_issue":
        rec.state = "processing"
        queue_source = "silence_waker"
    else:
        rec.state = "waiting_for_issue"
        queue_source = "silence_expired_ask"

    state.set(ticket_id, rec)
    return SilenceExpiryTransition(
        ticket_id=int(ticket_id),
        queue_source=queue_source,
        verdict=verdict,
        silence_end=end,
    )


def apply_waiting_poller_abandoned(
    state: TicketStateTracker,
    ticket_id: str | int,
    *,
    reason: str,
) -> WaitingPollerTransition | None:
    """Mark a waiting-poller candidate terminal when Zendesk is out of scope."""
    rec = state.get(ticket_id)
    if rec is None or rec.state == "abandoned":
        return None

    prev_state = rec.state
    rec.state = "abandoned"
    state.set(ticket_id, rec)
    return WaitingPollerTransition(
        ticket_id=int(ticket_id),
        kind="abandoned",
        prev_state=prev_state,
        last_verdict=reason,
        ask_count=rec.ask_count,
    )


def apply_waiting_poller_timeouts(
    state: TicketStateTracker,
    ticket_id: str | int,
    *,
    abandoned_hours: int,
    greeting_alert_minutes: int,
    now: datetime | None = None,
) -> WaitingPollerTransition | None:
    """Apply waiting-for-issue timeout transitions and one-shot alert markers."""
    rec = state.get(ticket_id)
    if rec is None or rec.state != "waiting_for_issue":
        return None

    current_time = now or datetime.now(timezone.utc)
    first_seen = rec.first_seen
    if first_seen.tzinfo is None:
        first_seen = first_seen.replace(tzinfo=timezone.utc)
    age_seconds = (current_time - first_seen).total_seconds()

    if age_seconds >= abandoned_hours * 3600:
        prev_state = rec.state
        should_alert = rec.abandoned_alert_sent_at is None
        if should_alert:
            rec.abandoned_alert_sent_at = current_time
        rec.state = "abandoned"
        state.set(ticket_id, rec)
        return WaitingPollerTransition(
            ticket_id=int(ticket_id),
            kind="abandoned",
            prev_state=prev_state,
            age_seconds=age_seconds,
            last_verdict=rec.last_verdict or "",
            ask_count=rec.ask_count,
            should_alert=should_alert,
        )

    if (
        rec.last_verdict == "greeting"
        and age_seconds >= greeting_alert_minutes * 60
        and rec.alert_sent_at is None
    ):
        prev_state = rec.state
        rec.alert_sent_at = current_time
        state.set(ticket_id, rec)
        return WaitingPollerTransition(
            ticket_id=int(ticket_id),
            kind="greeting_alert",
            prev_state=prev_state,
            age_seconds=age_seconds,
            last_verdict=rec.last_verdict or "",
            ask_count=rec.ask_count,
            should_alert=True,
        )

    return None

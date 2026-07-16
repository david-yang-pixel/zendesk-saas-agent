"""Centralized trigger-state transitions shared by entry points."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from zd_agent.log import get_logger
from zd_agent.session.agent_session import SessionStatus
from zd_agent.trigger.state_store import TicketState, TicketStateTracker

logger = get_logger(module="services.trigger_state_machine")

_MAX_CONSECUTIVE_AGENT_FAILURES = 3


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


def apply_waiting_for_form(
    state: TicketStateTracker,
    ticket_id: str | int,
    *,
    existing: TicketState | None,
    latest_cid: str | None,
    latest_ts: datetime,
    now: datetime,
    delivery_attempt_id: str = "",
) -> None:
    """Persist an Answer Bot readiness wait without customer-ask semantics."""
    state.set(ticket_id, TicketState(
        state="waiting_for_form",
        first_seen=existing.first_seen if existing else now,
        last_customer_comment_id=latest_cid,
        last_comment_ts=latest_ts,
        last_eval_at=now,
        last_verdict="waiting_for_form",
        ask_count=existing.ask_count if existing else 0,
        last_agent_finish_ts=(
            existing.last_agent_finish_ts if existing else None
        ),
        delivery_attempt_id=(
            delivery_attempt_id
            or (existing.delivery_attempt_id if existing else "")
        ),
        consecutive_agent_failures=(
            existing.consecutive_agent_failures if existing else 0
        ),
    ))


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
        delivery_attempt_id=(existing.delivery_attempt_id if existing else ""),
        consecutive_agent_failures=(
            existing.consecutive_agent_failures if existing else 0
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
        delivery_attempt_id=(existing.delivery_attempt_id if existing else ""),
        consecutive_agent_failures=(
            existing.consecutive_agent_failures if existing else 0
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
    delivery_attempt_id: str = "",
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
        delivery_attempt_id=(
            delivery_attempt_id
            or (existing.delivery_attempt_id if existing else "")
        ),
        consecutive_agent_failures=(
            existing.consecutive_agent_failures if existing else 0
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
    delivery_attempt_id: str = "",
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
        delivery_attempt_id=(
            delivery_attempt_id
            or (existing.delivery_attempt_id if existing else "")
        ),
        consecutive_agent_failures=(
            existing.consecutive_agent_failures if existing else 0
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

    session_id = str(getattr(session, "session_id", "") or "")
    if ticket_state.state != "processing":
        logger.info(
            "session_finished_ignored_non_processing_state",
            ticket_id=session.ticket_id,
            session_id=session_id,
            current_state=ticket_state.state,
        )
        return True

    active_session_id = ticket_state.active_session_id
    if active_session_id and active_session_id != session_id:
        logger.warning(
            "session_finished_ignored_stale_owner",
            ticket_id=session.ticket_id,
            active_session_id=active_session_id,
            stale_session_id=session_id,
        )
        return True

    finished_at = now or datetime.now(timezone.utc)
    reply_sent = bool(getattr(session, "reply_sent", False))
    if reply_sent and not active_session_id:
        # Old state files may contain an ownerless ``processing`` record.  A
        # no-reply failure can safely release that zombie for retry, but a
        # public-delivery callback cannot prove which session generation sent
        # the message and therefore must not terminalize the ticket.
        logger.warning(
            "session_finished_ignored_unowned_delivery",
            ticket_id=session.ticket_id,
            session_id=session_id,
        )
        return True

    if reply_sent:
        # A confirmed customer-visible delivery is the terminal business fact.
        # Cleanup/notification failures may subsequently mark the in-memory
        # session FAILED or TIMEOUT, but must never schedule a second reply.
        logger.info(
            "session_finished_after_public_delivery",
            ticket_id=session.ticket_id,
            from_state=ticket_state.state,
            session_status=session.status.value,
        )
        is_handoff = getattr(session, "public_reply_type", "") == "handoff"
        ticket_state.state = (
            "escalated_to_human" if is_handoff else "processed"
        )
        ticket_state.active_session_id = ""
        ticket_state.last_agent_finish_ts = finished_at
        ticket_state.silence_window_end = None
        ticket_state.consecutive_agent_failures = 0
        if (
            bool(getattr(session, "customer_msgs_snapshot_captured", False))
            and getattr(session, "customer_msgs_snapshot_last_id", "")
        ):
            ticket_state.last_customer_comment_id = str(
                session.customer_msgs_snapshot_last_id,
            )
            ticket_state.last_eval_at = finished_at
        if is_handoff:
            ticket_state.escalated_at = finished_at
            ticket_state.escalated_reason = "agent_handoff"
            ticket_state.last_verdict = "agent_handoff"
        state.set(session.ticket_id, ticket_state)
        return True

    ticket_state.last_agent_finish_ts = finished_at

    completed_without_reply_from_processing = (
        ticket_state.state == "processing"
        and session.status == SessionStatus.COMPLETED
        and not reply_sent
    )
    unsuccessful_from_processing = (
        ticket_state.state == "processing"
        and session.status in (SessionStatus.FAILED, SessionStatus.TIMEOUT)
    )
    deferred_from_processing = (
        ticket_state.state == "processing"
        and session.status == SessionStatus.DEFERRED
    )
    if (
        completed_without_reply_from_processing
        or unsuccessful_from_processing
        or deferred_from_processing
    ):
        # Every run that returns ownership without a confirmed public delivery
        # consumes the same durable budget.  DEFERRED is intentionally bounded:
        # otherwise repeated stale-context deferrals can wake forever.
        ticket_state.consecutive_agent_failures += 1

        retry_exhausted = (
            ticket_state.consecutive_agent_failures
            >= _MAX_CONSECUTIVE_AGENT_FAILURES
        )
        if retry_exhausted:
            new_state = "escalated_to_human"
        else:
            new_state = "in_silence_window"
        logger.info(
            "session_finished_state_reset",
            ticket_id=session.ticket_id,
            from_state=ticket_state.state,
            to_state=new_state,
            session_status=session.status.value,
            reply_sent=reply_sent,
            consecutive_agent_failures=ticket_state.consecutive_agent_failures,
            retry_exhausted=retry_exhausted,
        )
        ticket_state.state = new_state
        ticket_state.active_session_id = ""
        if retry_exhausted:
            ticket_state.silence_window_end = None
            ticket_state.escalated_at = finished_at
            ticket_state.escalated_reason = "agent_retry_exhausted"
            ticket_state.last_verdict = "agent_retry_exhausted"
        else:
            delay_seconds = 60 if deferred_from_processing else 0
            ticket_state.silence_window_end = finished_at + timedelta(
                seconds=delay_seconds,
            )
            if deferred_from_processing:
                ticket_state.last_verdict = "agent_deferred"
            else:
                # Every failed Agent attempt must wake the Agent again.  Do not
                # reuse a pre-agent greeting/insufficient verdict, which would
                # route the recovery into an unrelated customer nudge.
                ticket_state.last_verdict = "agent_retry"

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
    if verdict in {"substantial_issue", "agent_deferred", "agent_retry"}:
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
    """Apply bounded waiting timeouts and one-shot alert markers."""
    rec = state.get(ticket_id)
    if rec is None or rec.state not in {"waiting_for_issue", "waiting_for_form"}:
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
        rec.state == "waiting_for_issue"
        and rec.last_verdict == "greeting"
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

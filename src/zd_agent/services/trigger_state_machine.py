"""Centralized trigger-state transitions shared by entry points."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from zd_agent.log import get_logger
from zd_agent.casework.domain.enums import (
    DeliveryState,
    OwnershipAction,
    OwnershipState,
    ReasonCode,
)
from zd_agent.session.agent_session import SessionStatus
from zd_agent.trigger.state_store import TicketState, TicketStateTracker

logger = get_logger(module="services.trigger_state_machine")

_MAX_CONSECUTIVE_AGENT_FAILURES = 3


@dataclass(frozen=True)
class WaitingPollerTransition:
    ticket_id: int
    kind: str
    prev_state: str
    age_seconds: float = 0.0
    last_verdict: str = ""
    ask_count: int = 0


@dataclass(frozen=True)
class CaseworkIntakeTransition:
    ticket_id: int
    kind: str
    prev_state: str | None
    ask_count_before: int
    ask_count_after: int
    silence_min: int = 0
    silence_window_end: datetime | None = None


def apply_human_takeover(
    state: TicketStateTracker,
    ticket_id: str | int,
    *,
    existing: TicketState | None,
    latest_cid: str | None,
    latest_ts: datetime | None,
    now: datetime,
    reason: str,
    expected_revision: int | None = None,
    expected_state: str | None = None,
) -> bool:
    """Stop automation after a real human reply without creating side effects."""

    def _takeover(_current: TicketState | None) -> TicketState:
        return TicketState(
            state="externally_owned",
            first_seen=existing.first_seen if existing else now,
            last_customer_comment_id=latest_cid,
            last_comment_ts=latest_ts,
            last_eval_at=now,
            last_verdict=reason,
            ask_count=existing.ask_count if existing else 0,
            nudge_sent_at=existing.nudge_sent_at if existing else None,
            alert_sent_at=existing.alert_sent_at if existing else None,
            abandoned_alert_sent_at=(
                existing.abandoned_alert_sent_at if existing else None
            ),
            last_agent_finish_ts=(existing.last_agent_finish_ts if existing else None),
            delivery_attempt_id=(existing.delivery_attempt_id if existing else ""),
            consecutive_agent_failures=(
                existing.consecutive_agent_failures if existing else 0
            ),
            pending_customer_fields=(),
            pending_issue_question="",
            ownership_action="support",
            ownership_reason=reason,
        )

    if expected_revision is not None:
        return (
            state.update_if_revision(
                ticket_id,
                expected_revision=expected_revision,
                expected_state=expected_state,
                mutate=_takeover,
            )
            is not None
        )
    state.set(ticket_id, _takeover(existing))
    return True


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
    existing_form_wait_started_at = None
    if existing is not None and existing.state == "waiting_for_form":
        existing_form_wait_started_at = (
            existing.form_wait_started_at or existing.last_eval_at
        )
    state.set(
        ticket_id,
        TicketState(
            state="waiting_for_form",
            first_seen=existing.first_seen if existing else now,
            last_customer_comment_id=latest_cid,
            last_comment_ts=latest_ts,
            last_eval_at=now,
            form_wait_started_at=existing_form_wait_started_at or now,
            last_verdict="waiting_for_form",
            ask_count=existing.ask_count if existing else 0,
            last_agent_finish_ts=(existing.last_agent_finish_ts if existing else None),
            delivery_attempt_id=(
                delivery_attempt_id
                or (existing.delivery_attempt_id if existing else "")
            ),
            consecutive_agent_failures=(
                existing.consecutive_agent_failures if existing else 0
            ),
            pending_customer_fields=(
                existing.pending_customer_fields if existing else ()
            ),
            pending_issue_question=(
                existing.pending_issue_question if existing else ""
            ),
            pending_trigger_hint=(existing.pending_trigger_hint if existing else ""),
        ),
    )


def enter_casework_silence(
    state: TicketStateTracker,
    ticket_id: str | int,
    *,
    existing: TicketState | None,
    latest_cid: str | None,
    latest_ts: datetime | None,
    now: datetime,
    intake_reason: str,
    ask_count_now: int,
    silence_minutes: int,
    delivery_attempt_id: str = "",
) -> CaseworkIntakeTransition:
    """Persist a debounced v2 intake that is ready for casework."""
    window_end = now + timedelta(minutes=silence_minutes)
    state.set(
        ticket_id,
        TicketState(
            state="in_silence_window",
            first_seen=existing.first_seen if existing else now,
            last_customer_comment_id=latest_cid,
            last_comment_ts=latest_ts,
            last_eval_at=now,
            silence_window_end=window_end,
            last_verdict=intake_reason,
            ask_count=ask_count_now,
            nudge_sent_at=None,
            alert_sent_at=None,
            abandoned_alert_sent_at=(
                existing.abandoned_alert_sent_at if existing else None
            ),
            # This transition is caused by a newly observed customer message.
            # It starts a fresh analysis generation, so the previous agent
            # reply must not throttle the matching silence-waker dispatch.
            last_agent_finish_ts=None,
            delivery_attempt_id=(
                delivery_attempt_id
                or (existing.delivery_attempt_id if existing else "")
            ),
            consecutive_agent_failures=(
                existing.consecutive_agent_failures if existing else 0
            ),
            pending_customer_fields=(
                existing.pending_customer_fields if existing else ()
            ),
            pending_issue_question=(
                existing.pending_issue_question if existing else ""
            ),
            pending_trigger_hint=(existing.pending_trigger_hint if existing else ""),
        ),
    )
    return CaseworkIntakeTransition(
        ticket_id=int(ticket_id),
        kind="in_silence_window",
        prev_state=existing.state if existing else None,
        ask_count_before=ask_count_now,
        ask_count_after=ask_count_now,
        silence_min=silence_minutes,
        silence_window_end=window_end,
    )


def apply_session_finished(
    state: TicketStateTracker,
    session: Any,
    *,
    now: datetime | None = None,
    casework_effect_store: Any | None = None,
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
    casework_owner = str(getattr(session, "casework_ownership_action", "") or "")
    raw_ownership_state = str(
        getattr(session, "casework_ownership_state", "") or "",
    )
    raw_delivery_state = str(
        getattr(session, "casework_delivery_state", "") or "",
    )
    raw_reason = str(getattr(session, "casework_reason_code", "") or "")
    casework_effect_reference = str(
        getattr(session, "casework_effect_reference", "") or "",
    )
    typed_values = (
        casework_owner,
        raw_ownership_state,
        raw_delivery_state,
        raw_reason,
    )
    if not any(typed_values):
        typed_owner = OwnershipAction.NONE
        typed_ownership_state = OwnershipState.NOT_REQUIRED
        typed_delivery_state = DeliveryState.DEFERRED
        typed_reason = ReasonCode.INTERNAL_RETRY_PENDING
    else:
        if not all(typed_values):
            raise RuntimeError("partial typed casework receipt cannot update state")
        try:
            typed_owner = OwnershipAction(casework_owner)
            typed_ownership_state = OwnershipState(raw_ownership_state)
            typed_delivery_state = DeliveryState(raw_delivery_state)
            typed_reason = ReasonCode(raw_reason)
        except ValueError as error:
            raise RuntimeError(
                "invalid typed casework receipt cannot update state",
            ) from error
    verified_effect = None
    if (
        typed_owner is not OwnershipAction.NONE
        and typed_ownership_state in {OwnershipState.PENDING, OwnershipState.CONFIRMED}
        and casework_effect_reference
        and (
            reply_sent
            or typed_delivery_state
            in {DeliveryState.NOT_REQUIRED, DeliveryState.PAUSED}
        )
    ):
        if casework_effect_store is None:
            raise RuntimeError(
                "typed ownership completion requires the durable effect store",
            )
        verified_effect = casework_effect_store.get(casework_effect_reference)
        expected_delivery_attempt = ticket_state.delivery_attempt_id
        actual_root_attempt = str(
            getattr(verified_effect, "root_delivery_attempt_id", "") or "",
        )
        root_matches = bool(
            not expected_delivery_attempt
            or actual_root_attempt == expected_delivery_attempt
            or actual_root_attempt == f"{expected_delivery_attempt}:runtime-failure"
        )
        snapshot_matches = bool(
            not getattr(session, "customer_msgs_snapshot_captured", False)
            or getattr(verified_effect, "snapshot_customer_last_message_id", "")
            == getattr(session, "customer_msgs_snapshot_last_id", "")
        )
        if not all(
            (
                verified_effect is not None,
                getattr(verified_effect, "reference", "") == casework_effect_reference,
                getattr(verified_effect, "ticket_id", None) == session.ticket_id,
                getattr(verified_effect, "ownership_action", "") == typed_owner.value,
                getattr(verified_effect, "reason_code", "") == typed_reason.value,
                bool(getattr(verified_effect, "ownership_required", False)),
                bool(getattr(verified_effect, "ownership_ready", False)),
                root_matches,
                snapshot_matches,
            ),
        ):
            raise RuntimeError(
                "typed ownership receipt does not match its durable effect",
            )
    has_durable_casework_ownership = bool(
        typed_owner is not OwnershipAction.NONE
        and typed_ownership_state is OwnershipState.CONFIRMED
        and casework_effect_reference
        and verified_effect is not None
        and bool(getattr(verified_effect, "complete", False))
    )
    casework_disposition = str(
        getattr(session, "casework_disposition", "") or "",
    )
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

    if bool(getattr(session, "dry_run", False)) and not reply_sent:
        ticket_state.state = "processed"
        ticket_state.active_session_id = ""
        ticket_state.last_agent_finish_ts = finished_at
        ticket_state.silence_window_end = None
        ticket_state.consecutive_agent_failures = 0
        ticket_state.ownership_action = ""
        ticket_state.ownership_reason = ""
        ticket_state.ownership_effect_reference = ""
        ticket_state.pending_trigger_hint = ""
        ticket_state.last_verdict = "dry_run_drafted"
        if bool(getattr(session, "customer_msgs_snapshot_captured", False)) and getattr(
            session,
            "customer_msgs_snapshot_last_id",
            "",
        ):
            ticket_state.last_customer_comment_id = str(
                session.customer_msgs_snapshot_last_id,
            )
            ticket_state.last_eval_at = finished_at
        state.set(session.ticket_id, ticket_state)
        return True

    no_reply_ownership_pending = bool(
        typed_owner is not OwnershipAction.NONE
        and typed_ownership_state is OwnershipState.PENDING
        and casework_effect_reference
        and verified_effect is not None
        and not bool(getattr(verified_effect, "complete", False))
        and not reply_sent
        and typed_delivery_state in {DeliveryState.NOT_REQUIRED, DeliveryState.PAUSED}
    )
    if no_reply_ownership_pending:
        ticket_state.state = "ownership_pending"
        ticket_state.active_session_id = session_id
        ticket_state.last_agent_finish_ts = finished_at
        ticket_state.silence_window_end = None
        ticket_state.consecutive_agent_failures = 0
        ticket_state.ownership_action = typed_owner.value
        ticket_state.ownership_reason = typed_reason.value
        ticket_state.ownership_effect_reference = casework_effect_reference
        ticket_state.pending_trigger_hint = ""
        ticket_state.last_verdict = "ownership_effect_pending"
        if bool(getattr(session, "customer_msgs_snapshot_captured", False)) and getattr(
            session,
            "customer_msgs_snapshot_last_id",
            "",
        ):
            ticket_state.last_customer_comment_id = str(
                session.customer_msgs_snapshot_last_id,
            )
            ticket_state.last_eval_at = finished_at
        state.set(session.ticket_id, ticket_state)
        return True

    no_reply_ownership_confirmed = bool(
        has_durable_casework_ownership
        and not reply_sent
        and typed_delivery_state in {DeliveryState.NOT_REQUIRED, DeliveryState.PAUSED}
    )
    if no_reply_ownership_confirmed:
        ticket_state.state = "owned"
        ticket_state.active_session_id = ""
        ticket_state.last_agent_finish_ts = finished_at
        ticket_state.silence_window_end = None
        ticket_state.consecutive_agent_failures = 0
        ticket_state.ownership_action = casework_owner
        ticket_state.ownership_reason = typed_reason.value
        ticket_state.ownership_effect_reference = casework_effect_reference
        ticket_state.pending_trigger_hint = ""
        ticket_state.last_verdict = ticket_state.ownership_reason
        state.set(session.ticket_id, ticket_state)
        return True

    if typed_delivery_state is DeliveryState.PAUSED and not reply_sent:
        # The operator kill switch is an intentional no-write outcome, not an
        # automation failure. Archive the draft and mark this customer cursor
        # evaluated without consuming retry budget or synthesizing ownership.
        ticket_state.state = "processed"
        ticket_state.active_session_id = ""
        ticket_state.last_agent_finish_ts = finished_at
        ticket_state.silence_window_end = None
        ticket_state.consecutive_agent_failures = 0
        ticket_state.ownership_action = ""
        ticket_state.ownership_reason = ""
        ticket_state.ownership_effect_reference = ""
        ticket_state.pending_trigger_hint = ""
        ticket_state.last_verdict = "public_reply_paused"
        if bool(getattr(session, "customer_msgs_snapshot_captured", False)) and getattr(
            session,
            "customer_msgs_snapshot_last_id",
            "",
        ):
            ticket_state.last_customer_comment_id = str(
                session.customer_msgs_snapshot_last_id,
            )
            ticket_state.last_eval_at = finished_at
        state.set(session.ticket_id, ticket_state)
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
        has_casework_ownership = has_durable_casework_ownership
        ownership_is_pending = bool(
            typed_owner is not OwnershipAction.NONE
            and typed_ownership_state is OwnershipState.PENDING
            and casework_effect_reference
            and verified_effect is not None
            and not bool(getattr(verified_effect, "complete", False))
        )
        if ownership_is_pending:
            # The customer-visible ownership promise is already delivered, but
            # its notification/tag command still needs durable completion.
            # Keep this generation fenced until the effect reconciler promotes
            # the exact reference to ``owned``.
            ticket_state.state = "ownership_pending"
            ticket_state.active_session_id = session_id
            ticket_state.last_agent_finish_ts = finished_at
            ticket_state.silence_window_end = None
            ticket_state.consecutive_agent_failures = 0
            ticket_state.ownership_action = typed_owner.value
            ticket_state.ownership_reason = typed_reason.value
            ticket_state.ownership_effect_reference = casework_effect_reference
            ticket_state.last_verdict = "ownership_effect_pending"
            ticket_state.pending_customer_fields = ()
            ticket_state.pending_issue_question = ""
            ticket_state.pending_trigger_hint = ""
            if bool(
                getattr(session, "customer_msgs_snapshot_captured", False)
            ) and getattr(session, "customer_msgs_snapshot_last_id", ""):
                ticket_state.last_customer_comment_id = str(
                    session.customer_msgs_snapshot_last_id,
                )
                ticket_state.last_eval_at = finished_at
            state.set(session.ticket_id, ticket_state)
            return True
        ticket_state.state = (
            "owned"
            if has_casework_ownership
            else (
                "awaiting_customer"
                if casework_disposition == "awaiting_customer"
                else "processed"
            )
        )
        ticket_state.active_session_id = ""
        ticket_state.last_agent_finish_ts = finished_at
        ticket_state.silence_window_end = None
        ticket_state.consecutive_agent_failures = 0
        if bool(getattr(session, "customer_msgs_snapshot_captured", False)) and getattr(
            session, "customer_msgs_snapshot_last_id", ""
        ):
            ticket_state.last_customer_comment_id = str(
                session.customer_msgs_snapshot_last_id,
            )
            ticket_state.last_eval_at = finished_at
        if has_casework_ownership:
            ownership_reason = str(
                getattr(session, "casework_reason_code", "") or "",
            )
            ticket_state.ownership_action = casework_owner
            ticket_state.ownership_reason = ownership_reason
            ticket_state.ownership_effect_reference = casework_effect_reference
            ticket_state.last_verdict = ownership_reason
            ticket_state.pending_customer_fields = ()
            ticket_state.pending_issue_question = ""
        elif ticket_state.state == "awaiting_customer":
            ticket_state.last_verdict = "awaiting_customer"
            ticket_state.pending_customer_fields = tuple(
                getattr(session, "casework_required_customer_fields", ()) or (),
            )
            ticket_state.pending_issue_question = str(
                getattr(session, "casework_question", "") or "",
            )
        else:
            ticket_state.last_verdict = "resolved"
            ticket_state.pending_customer_fields = ()
            ticket_state.pending_issue_question = ""
        ticket_state.pending_trigger_hint = ""
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
        ticket_state.state == "processing" and session.status == SessionStatus.DEFERRED
    )
    deferred_for_customer_refresh = bool(
        deferred_from_processing
        and (
            bool(getattr(session, "customer_context_dirty", False))
            or any(
                marker in str(getattr(session, "error_message", "") or "").casefold()
                for marker in (
                    "customer added",
                    "stale_context",
                    "newer customer message remains unanswered",
                )
            )
        )
    )
    if deferred_for_customer_refresh:
        # A customer supplement is new work, not an Agent failure. Release the
        # stale run immediately and re-fetch the complete conversation.
        ticket_state.state = "in_silence_window"
        ticket_state.active_session_id = ""
        ticket_state.silence_window_end = finished_at
        ticket_state.consecutive_agent_failures = 0
        ticket_state.last_agent_finish_ts = None
        ticket_state.last_verdict = "customer_context_refresh"
        state.set(session.ticket_id, ticket_state)
        return True
    if (
        completed_without_reply_from_processing
        or unsuccessful_from_processing
        or deferred_from_processing
    ):
        # Operational delivery/model failures remain bounded. Customer-driven
        # freshness invalidation was handled above and never reaches this budget.
        ticket_state.consecutive_agent_failures += 1

        ownership_persistence_retry = (
            ticket_state.consecutive_agent_failures >= _MAX_CONSECUTIVE_AGENT_FAILURES
        )
        new_state = "in_silence_window"
        logger.info(
            "session_finished_state_reset",
            ticket_id=session.ticket_id,
            from_state=ticket_state.state,
            to_state=new_state,
            session_status=session.status.value,
            reply_sent=reply_sent,
            consecutive_agent_failures=ticket_state.consecutive_agent_failures,
            ownership_persistence_retry=ownership_persistence_retry,
        )
        ticket_state.state = new_state
        ticket_state.active_session_id = ""
        if ownership_persistence_retry:
            # The runner must create a typed durable ownership command before
            # this lifecycle can become terminal.  If the effect store itself
            # is unavailable, retry only that operational step; never synthesize
            # a legacy handoff or customer message from state alone.
            ticket_state.silence_window_end = finished_at + timedelta(minutes=5)
            ticket_state.last_verdict = "ownership_retry"
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


def apply_waiting_poller_out_of_scope(
    state: TicketStateTracker,
    ticket_id: str | int,
    *,
    reason: str,
    expected_revision: int | None = None,
    expected_state: str | None = None,
) -> WaitingPollerTransition | None:
    """Stop polling when Zendesk lifecycle moved outside automation scope."""
    rec = state.get(ticket_id)
    if rec is None or rec.state == "out_of_scope":
        return None

    revision = rec.state_revision if expected_revision is None else expected_revision
    required_state = rec.state if expected_state is None else expected_state

    def _retire(current: TicketState | None) -> TicketState:
        if current is None:
            raise RuntimeError("waiting poller state disappeared")
        current.state = "out_of_scope"
        current.last_verdict = reason
        current.silence_window_end = None
        return current

    commit = state.update_if_revision(
        ticket_id,
        expected_revision=revision,
        expected_state=required_state,
        mutate=_retire,
    )
    if commit is None or commit.before is None:
        return None
    return WaitingPollerTransition(
        ticket_id=int(ticket_id),
        kind="out_of_scope",
        prev_state=commit.before.state,
        last_verdict=reason,
        ask_count=commit.after.ask_count,
    )


def apply_waiting_poller_timeouts(
    state: TicketStateTracker,
    ticket_id: str | int,
    *,
    customer_wait_timeout_hours: int,
    now: datetime | None = None,
    expected_revision: int | None = None,
    expected_state: str | None = None,
) -> WaitingPollerTransition | None:
    """Stop v2 customer waits after their bounded response window.

    The timeout is a lifecycle boundary, not a human escalation.
    """
    rec = state.get(ticket_id)
    if rec is None or rec.state not in {
        "awaiting_customer",
        "waiting_for_form",
    }:
        return None

    current_time = now or datetime.now(timezone.utc)
    if rec.state == "awaiting_customer":
        wait_started_at = rec.last_agent_finish_ts or rec.first_seen
    else:
        wait_started_at = rec.form_wait_started_at or rec.last_eval_at or rec.first_seen
    if wait_started_at.tzinfo is None:
        wait_started_at = wait_started_at.replace(tzinfo=timezone.utc)
    age_seconds = (current_time - wait_started_at).total_seconds()

    if age_seconds >= customer_wait_timeout_hours * 3600:
        prev_state = rec.state
        reason = (
            "customer_response_timeout"
            if prev_state == "awaiting_customer"
            else "form_submission_timeout"
        )
        revision = (
            rec.state_revision if expected_revision is None else expected_revision
        )
        required_state = rec.state if expected_state is None else expected_state

        def _expire(current: TicketState | None) -> TicketState:
            if current is None:
                raise RuntimeError("waiting poller state disappeared")
            current.state = "out_of_scope"
            current.last_verdict = reason
            current.silence_window_end = None
            return current

        commit = state.update_if_revision(
            ticket_id,
            expected_revision=revision,
            expected_state=required_state,
            mutate=_expire,
        )
        if commit is None:
            return None
        return WaitingPollerTransition(
            ticket_id=int(ticket_id),
            kind="out_of_scope",
            prev_state=prev_state,
            age_seconds=age_seconds,
            last_verdict=reason,
            ask_count=commit.after.ask_count,
        )

    return None

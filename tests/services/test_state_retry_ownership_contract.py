"""Contracts for durable v2 retry budgets and session ownership."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from zd_agent.services.casework_effect_store import CaseworkEffectStore
from zd_agent.services.trigger_state_machine import (
    apply_session_finished,
    enter_casework_silence,
    apply_waiting_for_form,
)
from zd_agent.session.agent_session import AgentSession, SessionStatus
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


NOW = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)


def _session(ticket_id: int, status: SessionStatus) -> AgentSession:
    session = AgentSession(ticket_id=ticket_id, session_id="session-current")
    session.status = status
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
def test_every_owned_no_reply_outcome_consumes_retry_budget(tmp_path, status):
    tracker = TicketStateTracker(tmp_path / f"{status.value}.json")
    tracker.set(
        3461,
        TicketState(
            state="processing",
            first_seen=NOW - timedelta(minutes=10),
            active_session_id="session-current",
            delivery_attempt_id="delivery-stable",
            consecutive_agent_failures=2,
        ),
    )

    apply_session_finished(tracker, _session(3461, status), now=NOW)

    record = TicketStateTracker(tracker.path).get(3461)
    assert record is not None
    assert record.state == "in_silence_window"
    assert record.last_verdict == "ownership_retry"
    assert record.silence_window_end == NOW + timedelta(minutes=5)
    assert record.consecutive_agent_failures == 3
    assert record.delivery_attempt_id == "delivery-stable"


def test_deferred_retry_is_bounded_and_backed_off(tmp_path):
    tracker = TicketStateTracker(tmp_path / "deferred.json")
    tracker.set(
        3461,
        TicketState(
            state="processing",
            first_seen=NOW,
            active_session_id="session-current",
        ),
    )

    apply_session_finished(
        tracker,
        _session(3461, SessionStatus.DEFERRED),
        now=NOW,
    )

    record = tracker.get(3461)
    assert record is not None
    assert record.last_verdict == "agent_deferred"
    assert record.silence_window_end == NOW + timedelta(seconds=60)
    assert record.consecutive_agent_failures == 1


def test_repeated_customer_supplements_do_not_consume_failure_budget(tmp_path):
    tracker = TicketStateTracker(tmp_path / "customer-refresh.json")
    tracker.set(
        3461,
        TicketState(
            state="processing",
            first_seen=NOW,
            active_session_id="session-0",
            consecutive_agent_failures=2,
        ),
    )

    for attempt in range(3):
        current = tracker.get(3461)
        assert current is not None
        current.state = "processing"
        current.active_session_id = f"session-{attempt}"
        tracker.set(3461, current)
        session = AgentSession(ticket_id=3461, session_id=f"session-{attempt}")
        session.status = SessionStatus.DEFERRED
        session.customer_context_dirty = True

        apply_session_finished(tracker, session, now=NOW)

        refreshed = tracker.get(3461)
        assert refreshed is not None
        assert refreshed.state == "in_silence_window"
        assert refreshed.last_verdict == "customer_context_refresh"
        assert refreshed.silence_window_end == NOW
        assert refreshed.consecutive_agent_failures == 0


def test_freshness_abort_without_webhook_signal_is_customer_refresh(tmp_path):
    tracker = TicketStateTracker(tmp_path / "freshness-refresh.json")
    tracker.set(
        3461,
        TicketState(
            state="processing",
            first_seen=NOW,
            active_session_id="session-current",
        ),
    )
    session = _session(3461, SessionStatus.DEFERRED)
    session.error_message = (
        "Pre-relay freshness check abort: customer added 1 new message(s)."
    )

    apply_session_finished(tracker, session, now=NOW)

    refreshed = tracker.get(3461)
    assert refreshed is not None
    assert refreshed.last_verdict == "customer_context_refresh"
    assert refreshed.consecutive_agent_failures == 0


def test_typed_ownership_terminalizes_without_public_reply(tmp_path):
    tracker = TicketStateTracker(tmp_path / "owned.json")
    tracker.set(
        3461,
        TicketState(
            state="processing",
            first_seen=NOW,
            active_session_id="session-current",
            consecutive_agent_failures=2,
        ),
    )
    session = _session(3461, SessionStatus.COMPLETED)
    session.casework_ownership_action = "support"
    session.casework_reason_code = "automation_retry_exhausted"
    session.casework_delivery_state = "not_required"
    session.casework_ownership_state = "confirmed"
    store = CaseworkEffectStore(tmp_path / "effects.sqlite3")
    effect = store.prepare(
        ticket_id=3461,
        delivery_attempt_id="runtime-owner:runtime-failure",
        generation=1,
        source_event_id="runtime-failure:runtime-owner",
        reply_type="none",
        body="",
        internal_analysis="retry exhausted",
        snapshot_customer_message_count=None,
        snapshot_customer_last_message_id="runtime-failure:runtime-owner",
        ownership_action="support",
        reason_code="automation_retry_exhausted",
        ownership_packet="Support must complete this case.",
        delivery_required=False,
    )
    token = store.claim_ownership(effect.reference)
    assert token is not None
    assert store.mark_ownership_effect(effect.reference, token, "notification_sent")
    assert store.mark_ownership_effect(effect.reference, token, "zendesk_tag_sent")
    store.release(effect.reference, token)
    session.casework_effect_reference = effect.reference

    apply_session_finished(
        tracker,
        session,
        now=NOW,
        casework_effect_store=store,
    )

    record = tracker.get(3461)
    assert record is not None
    assert record.state == "owned"
    assert record.ownership_action == "support"
    assert record.ownership_reason == "automation_retry_exhausted"
    assert record.consecutive_agent_failures == 0


def test_deferred_ownership_reply_remains_retryable_until_effects_confirm(tmp_path):
    tracker = TicketStateTracker(tmp_path / "pending-owned.json")
    tracker.set(
        3461,
        TicketState(
            state="processing",
            first_seen=NOW,
            active_session_id="session-current",
        ),
    )
    session = _session(3461, SessionStatus.DEFERRED)
    session.casework_ownership_action = "engineering"
    session.casework_reason_code = "confirmed_platform_defect"
    session.casework_delivery_state = "deferred"
    session.casework_ownership_state = "pending"
    session.casework_effect_reference = "casework-effect:engineering"

    apply_session_finished(tracker, session, now=NOW)

    record = tracker.get(3461)
    assert record is not None
    assert record.state == "in_silence_window"
    assert record.last_verdict == "agent_deferred"
    assert record.ownership_action == ""


def test_current_rebuilding_transitions_preserve_retry_identity(tmp_path):
    existing = TicketState(
        state="processing",
        first_seen=NOW - timedelta(hours=1),
        delivery_attempt_id="delivery-stable",
        consecutive_agent_failures=2,
    )
    tracker = TicketStateTracker(tmp_path / "form.json")
    apply_waiting_for_form(
        tracker,
        3500,
        existing=existing,
        latest_cid="form-1",
        latest_ts=NOW,
        now=NOW,
    )
    form = tracker.get(3500)
    assert form is not None
    assert form.delivery_attempt_id == "delivery-stable"
    assert form.consecutive_agent_failures == 2

    tracker = TicketStateTracker(tmp_path / "silence.json")
    enter_casework_silence(
        tracker,
        3501,
        existing=existing,
        latest_cid="customer-1",
        latest_ts=NOW,
        now=NOW,
        intake_reason="casework_ready",
        ask_count_now=0,
        silence_minutes=3,
    )
    silence = tracker.get(3501)
    assert silence is not None
    assert silence.delivery_attempt_id == "delivery-stable"
    assert silence.consecutive_agent_failures == 2


@pytest.mark.parametrize(
    "state_name",
    ["awaiting_customer", "processed", "externally_owned", "out_of_scope"],
)
def test_reply_callback_cannot_overwrite_non_processing_state(
    tmp_path,
    state_name,
):
    tracker = TicketStateTracker(tmp_path / f"{state_name}.json")
    tracker.set(
        3461,
        TicketState(
            state=state_name,
            first_seen=NOW,
            active_session_id="session-current",
            last_verdict="newer-lifecycle",
        ),
    )
    session = _session(3461, SessionStatus.COMPLETED)
    session.reply_sent = True

    apply_session_finished(tracker, session, now=NOW)

    record = tracker.get(3461)
    assert record is not None
    assert record.state == state_name
    assert record.last_verdict == "newer-lifecycle"
    assert record.last_agent_finish_ts is None


def test_ownerless_historical_processing_rejects_delivery_callback(tmp_path):
    tracker = TicketStateTracker(tmp_path / "unowned.json")
    tracker.set(
        3461,
        TicketState(state="processing", first_seen=NOW, active_session_id=""),
    )
    session = _session(3461, SessionStatus.COMPLETED)
    session.reply_sent = True

    apply_session_finished(tracker, session, now=NOW)

    record = tracker.get(3461)
    assert record is not None
    assert record.state == "processing"
    assert record.last_agent_finish_ts is None


def test_ownerless_historical_processing_allows_no_reply_recovery(tmp_path):
    tracker = TicketStateTracker(tmp_path / "retry.json")
    tracker.set(
        3461,
        TicketState(state="processing", first_seen=NOW, active_session_id=""),
    )

    apply_session_finished(
        tracker,
        _session(3461, SessionStatus.FAILED),
        now=NOW,
    )

    record = tracker.get(3461)
    assert record is not None
    assert record.state == "in_silence_window"
    assert record.consecutive_agent_failures == 1

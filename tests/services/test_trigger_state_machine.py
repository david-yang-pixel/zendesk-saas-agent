"""Current-v2 contracts for durable trigger-state transitions."""

from datetime import datetime, timedelta, timezone

import pytest

from zd_agent.services.casework_effect_store import CaseworkEffectStore
from zd_agent.services.trigger_state_machine import (
    apply_human_takeover,
    apply_session_finished,
    enter_casework_silence,
    apply_waiting_for_form,
    apply_waiting_poller_out_of_scope,
    apply_waiting_poller_timeouts,
)
from zd_agent.session.agent_session import AgentSession, SessionStatus
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


NOW = datetime(2026, 6, 5, tzinfo=timezone.utc)


def _state(tmp_path) -> TicketStateTracker:
    return TicketStateTracker(tmp_path / "tickets.json")


def _processing(session: AgentSession) -> TicketState:
    return TicketState(
        state="processing",
        first_seen=NOW - timedelta(minutes=5),
        active_session_id=session.session_id,
        delivery_attempt_id="delivery-stable",
    )


def _ownership_store(
    tmp_path,
    *,
    ticket_id: int,
    owner: str,
    reason: str,
    snapshot_last_id: str,
    delivery_required: bool,
    complete: bool,
) -> tuple[CaseworkEffectStore, str]:
    store = CaseworkEffectStore(tmp_path / f"effects-{ticket_id}.sqlite3")
    effect = store.prepare(
        ticket_id=ticket_id,
        delivery_attempt_id="delivery-stable",
        generation=1,
        source_event_id=snapshot_last_id,
        reply_type="solution",
        body="Verified typed ownership outcome.",
        internal_analysis="typed audit",
        snapshot_customer_message_count=1,
        snapshot_customer_last_message_id=snapshot_last_id,
        ownership_action=owner,
        reason_code=reason,
        ownership_packet="Verified action packet",
        delivery_required=delivery_required,
    )
    if delivery_required:
        assert store.confirm_public_delivery(effect.reference)
    if complete:
        token = store.claim_ownership(effect.reference)
        assert token is not None
        assert store.mark_ownership_effect(
            effect.reference,
            token,
            "notification_sent",
        )
        assert store.mark_ownership_effect(
            effect.reference,
            token,
            "zendesk_tag_sent",
        )
        store.release(effect.reference, token)
    return store, effect.reference


def test_confirmed_solution_marks_owned_generation_processed(tmp_path):
    tracker = _state(tmp_path)
    session = AgentSession(ticket_id=2808)
    tracker.set(2808, _processing(session))
    session.status = SessionStatus.COMPLETED
    session.reply_sent = True

    assert apply_session_finished(tracker, session, now=NOW) is True

    record = tracker.get(2808)
    assert record is not None
    assert record.state == "processed"
    assert record.last_verdict == "resolved"
    assert record.last_agent_finish_ts == NOW
    assert record.active_session_id == ""


def test_confirmed_ask_projects_to_awaiting_customer(tmp_path):
    tracker = _state(tmp_path)
    session = AgentSession(ticket_id=2809)
    tracker.set(2809, _processing(session))
    session.status = SessionStatus.COMPLETED
    session.reply_sent = True
    session.casework_disposition = "awaiting_customer"
    session.casework_required_customer_fields = ("target_workspace_choice",)
    session.casework_question = "Please approve a refund for this renewal."
    session.customer_msgs_snapshot_captured = True
    session.customer_msgs_snapshot_last_id = "customer-2"

    apply_session_finished(tracker, session, now=NOW)

    record = tracker.get(2809)
    assert record is not None
    assert record.state == "awaiting_customer"
    assert record.last_verdict == "awaiting_customer"
    assert record.last_customer_comment_id == "customer-2"
    assert record.pending_customer_fields == ("target_workspace_choice",)
    assert record.pending_issue_question == "Please approve a refund for this renewal."


def test_confirmed_reply_with_pending_typed_owner_stays_generation_fenced(tmp_path):
    tracker = _state(tmp_path)
    session = AgentSession(ticket_id=2816)
    tracker.set(2816, _processing(session))
    session.status = SessionStatus.COMPLETED
    session.reply_sent = True
    session.casework_delivery_state = "confirmed"
    session.casework_ownership_state = "pending"
    session.casework_ownership_action = "engineering"
    session.casework_reason_code = "confirmed_platform_defect"
    session.customer_msgs_snapshot_captured = True
    session.customer_msgs_snapshot_last_id = "customer-owner-pending"
    store, reference = _ownership_store(
        tmp_path,
        ticket_id=2816,
        owner="engineering",
        reason="confirmed_platform_defect",
        snapshot_last_id="customer-owner-pending",
        delivery_required=True,
        complete=False,
    )
    session.casework_effect_reference = reference

    apply_session_finished(
        tracker,
        session,
        now=NOW,
        casework_effect_store=store,
    )

    record = tracker.get(2816)
    assert record is not None
    assert record.state == "ownership_pending"
    assert record.active_session_id == session.session_id
    assert record.ownership_action == "engineering"
    assert record.ownership_reason == "confirmed_platform_defect"
    assert record.ownership_effect_reference == reference
    assert record.last_customer_comment_id == "customer-owner-pending"


def test_failed_processing_schedules_bounded_v2_retry(tmp_path):
    tracker = _state(tmp_path)
    session = AgentSession(ticket_id=2810)
    tracker.set(2810, _processing(session))
    session.status = SessionStatus.FAILED

    apply_session_finished(tracker, session, now=NOW)

    record = tracker.get(2810)
    assert record is not None
    assert record.state == "in_silence_window"
    assert record.silence_window_end == NOW
    assert record.last_verdict == "agent_retry"
    assert record.consecutive_agent_failures == 1


def test_customer_context_refresh_bypasses_post_agent_cooldown(tmp_path):
    tracker = _state(tmp_path)
    session = AgentSession(ticket_id=2824)
    processing = _processing(session)
    processing.consecutive_agent_failures = 2
    tracker.set(2824, processing)
    session.status = SessionStatus.DEFERRED
    session.customer_context_dirty = True
    session.error_message = "stale_context: customer added a newer message"

    assert apply_session_finished(tracker, session, now=NOW) is True

    record = tracker.get(2824)
    assert record is not None
    assert record.state == "in_silence_window"
    assert record.silence_window_end == NOW
    assert record.last_verdict == "customer_context_refresh"
    assert record.consecutive_agent_failures == 0
    assert record.last_agent_finish_ts is None


def test_public_reply_kill_switch_archives_draft_without_retry_or_ownership(
    tmp_path,
):
    tracker = _state(tmp_path)
    session = AgentSession(ticket_id=2815)
    processing = _processing(session)
    processing.consecutive_agent_failures = 2
    tracker.set(2815, processing)
    session.status = SessionStatus.COMPLETED
    session.casework_delivery_state = "paused"
    session.casework_ownership_action = "none"
    session.casework_ownership_state = "not_required"
    session.casework_reason_code = "bounded_authoritative_answer"
    session.customer_msgs_snapshot_captured = True
    session.customer_msgs_snapshot_last_id = "customer-paused"

    for _ in range(4):
        assert apply_session_finished(tracker, session, now=NOW) is True

    record = tracker.get(2815)
    assert record is not None
    assert record.state == "processed"
    assert record.last_verdict == "public_reply_paused"
    assert record.last_customer_comment_id == "customer-paused"
    assert record.consecutive_agent_failures == 0
    assert record.ownership_action == ""
    assert record.ownership_effect_reference == ""


def test_public_reply_kill_switch_preserves_confirmed_typed_ownership(tmp_path):
    tracker = _state(tmp_path)
    session = AgentSession(ticket_id=2822)
    tracker.set(2822, _processing(session))
    session.status = SessionStatus.COMPLETED
    session.casework_delivery_state = "paused"
    session.casework_ownership_action = "billing"
    session.casework_ownership_state = "confirmed"
    session.casework_reason_code = "business_approval_required"
    store, reference = _ownership_store(
        tmp_path,
        ticket_id=2822,
        owner="billing",
        reason="business_approval_required",
        snapshot_last_id="event-paused-billing",
        delivery_required=False,
        complete=True,
    )
    session.casework_effect_reference = reference

    assert (
        apply_session_finished(
            tracker,
            session,
            now=NOW,
            casework_effect_store=store,
        )
        is True
    )

    record = tracker.get(2822)
    assert record is not None
    assert record.state == "owned"
    assert record.ownership_action == "billing"
    assert record.ownership_reason == "business_approval_required"
    assert record.ownership_effect_reference == reference


def test_public_reply_kill_switch_fences_pending_typed_ownership(tmp_path):
    tracker = _state(tmp_path)
    session = AgentSession(ticket_id=2823)
    tracker.set(2823, _processing(session))
    session.status = SessionStatus.COMPLETED
    session.casework_delivery_state = "paused"
    session.casework_ownership_action = "security"
    session.casework_ownership_state = "pending"
    session.casework_reason_code = "security_or_privacy_review_required"
    store, reference = _ownership_store(
        tmp_path,
        ticket_id=2823,
        owner="security",
        reason="security_or_privacy_review_required",
        snapshot_last_id="event-paused-security",
        delivery_required=False,
        complete=False,
    )
    session.casework_effect_reference = reference

    assert (
        apply_session_finished(
            tracker,
            session,
            now=NOW,
            casework_effect_store=store,
        )
        is True
    )

    record = tracker.get(2823)
    assert record is not None
    assert record.state == "ownership_pending"
    assert record.active_session_id == session.session_id
    assert record.ownership_action == "security"
    assert record.ownership_effect_reference == reference


def test_fabricated_effect_reference_cannot_terminalize_typed_ownership(tmp_path):
    tracker = _state(tmp_path)
    session = AgentSession(ticket_id=2824)
    tracker.set(2824, _processing(session))
    session.status = SessionStatus.COMPLETED
    session.casework_delivery_state = "not_required"
    session.casework_ownership_action = "support"
    session.casework_ownership_state = "confirmed"
    session.casework_reason_code = "automation_retry_exhausted"
    session.casework_effect_reference = "casework-effect:fabricated"
    store = CaseworkEffectStore(tmp_path / "empty-effects.sqlite3")

    with pytest.raises(RuntimeError, match="durable effect"):
        apply_session_finished(
            tracker,
            session,
            now=NOW,
            casework_effect_store=store,
        )

    record = tracker.get(2824)
    assert record is not None
    assert record.state == "processing"
    assert record.ownership_effect_reference == ""


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("casework_ownership_action", "not-an-owner"),
        ("casework_ownership_state", "not-a-state"),
        ("casework_delivery_state", "not-a-delivery"),
        ("casework_reason_code", "not-a-reason"),
    ],
)
def test_invalid_or_partial_typed_receipt_cannot_mark_session_processed(
    tmp_path,
    field: str,
    value: str,
) -> None:
    tracker = _state(tmp_path)
    session = AgentSession(ticket_id=2825)
    tracker.set(2825, _processing(session))
    session.status = SessionStatus.COMPLETED
    session.reply_sent = True
    setattr(session, field, value)

    with pytest.raises(RuntimeError, match="typed casework receipt"):
        apply_session_finished(tracker, session, now=NOW)

    record = tracker.get(2825)
    assert record is not None
    assert record.state == "processing"


def test_server_dry_run_archives_draft_without_retry_or_ownership(tmp_path):
    tracker = _state(tmp_path)
    session = AgentSession(ticket_id=2817, dry_run=True)
    processing = _processing(session)
    processing.consecutive_agent_failures = 2
    tracker.set(2817, processing)
    session.status = SessionStatus.COMPLETED
    session.reply_draft = "Verified draft only"
    session.customer_msgs_snapshot_captured = True
    session.customer_msgs_snapshot_last_id = "customer-dry-run"

    assert apply_session_finished(tracker, session, now=NOW) is True

    record = tracker.get(2817)
    assert record is not None
    assert record.state == "processed"
    assert record.last_verdict == "dry_run_drafted"
    assert record.last_customer_comment_id == "customer-dry-run"
    assert record.consecutive_agent_failures == 0
    assert record.ownership_action == ""
    assert record.ownership_effect_reference == ""


def test_non_processing_callback_cannot_overwrite_newer_lifecycle(tmp_path):
    tracker = _state(tmp_path)
    tracker.set(
        2811,
        TicketState(
            state="awaiting_customer",
            first_seen=NOW - timedelta(minutes=5),
            last_verdict="awaiting_customer",
        ),
    )
    session = AgentSession(ticket_id=2811)
    session.status = SessionStatus.FAILED

    assert apply_session_finished(tracker, session, now=NOW) is True
    record = tracker.get(2811)
    assert record is not None
    assert record.state == "awaiting_customer"
    assert record.last_agent_finish_ts is None


def test_missing_ticket_state_is_noop(tmp_path):
    tracker = _state(tmp_path)
    session = AgentSession(ticket_id=9999)
    session.status = SessionStatus.COMPLETED
    session.reply_sent = True

    assert apply_session_finished(tracker, session) is False
    assert tracker.get(9999) is None


def test_casework_intake_enters_generation_bound_silence(tmp_path):
    tracker = _state(tmp_path)
    existing = TicketState(
        state="awaiting_customer",
        first_seen=NOW - timedelta(hours=1),
        consecutive_agent_failures=2,
        delivery_attempt_id="delivery-stable",
    )

    transition = enter_casework_silence(
        tracker,
        2812,
        existing=existing,
        latest_cid="cid-3",
        latest_ts=NOW,
        now=NOW,
        intake_reason="casework_ready",
        ask_count_now=0,
        silence_minutes=3,
    )

    record = tracker.get(2812)
    assert transition.kind == "in_silence_window"
    assert record is not None
    assert record.silence_window_end == NOW + timedelta(minutes=3)
    assert record.consecutive_agent_failures == 2
    assert record.delivery_attempt_id == "delivery-stable"


def test_customer_answer_clears_previous_agent_cooldown(tmp_path):
    tracker = _state(tmp_path)
    existing = TicketState(
        state="awaiting_customer",
        first_seen=NOW - timedelta(hours=1),
        last_agent_finish_ts=NOW - timedelta(seconds=2),
        last_customer_comment_id="form-only-message",
    )

    enter_casework_silence(
        tracker,
        2814,
        existing=existing,
        latest_cid="issue-description",
        latest_ts=NOW,
        now=NOW,
        intake_reason="casework_ready",
        ask_count_now=1,
        silence_minutes=0,
        delivery_attempt_id="delivery-issue-description",
    )

    record = tracker.get(2814)
    assert record is not None
    assert record.state == "in_silence_window"
    assert record.last_agent_finish_ts is None


def test_waiting_for_form_preserves_retry_generation(tmp_path):
    tracker = _state(tmp_path)
    existing = TicketState(
        state="in_silence_window",
        first_seen=NOW - timedelta(hours=1),
        consecutive_agent_failures=2,
        delivery_attempt_id="delivery-stable",
    )

    apply_waiting_for_form(
        tracker,
        2813,
        existing=existing,
        latest_cid="form-1",
        latest_ts=NOW,
        now=NOW,
    )

    record = tracker.get(2813)
    assert record is not None
    assert record.state == "waiting_for_form"
    assert record.consecutive_agent_failures == 2
    assert record.delivery_attempt_id == "delivery-stable"


def test_new_form_wait_does_not_timeout_from_the_ticket_first_seen(tmp_path):
    tracker = _state(tmp_path)
    ticket_first_seen = NOW - timedelta(days=30)
    existing = TicketState(
        state="in_silence_window",
        first_seen=ticket_first_seen,
        last_eval_at=NOW - timedelta(hours=2),
    )

    apply_waiting_for_form(
        tracker,
        2820,
        existing=existing,
        latest_cid="form-1",
        latest_ts=NOW,
        now=NOW,
    )

    transition = apply_waiting_poller_timeouts(
        tracker,
        2820,
        customer_wait_timeout_hours=24,
        now=NOW + timedelta(minutes=5),
    )
    waiting = tracker.get(2820)

    assert transition is None
    assert waiting is not None
    assert waiting.state == "waiting_for_form"
    assert waiting.first_seen == ticket_first_seen
    assert waiting.form_wait_started_at == NOW


def test_reobserving_the_same_form_wait_preserves_its_start(tmp_path):
    tracker = _state(tmp_path)
    apply_waiting_for_form(
        tracker,
        2821,
        existing=None,
        latest_cid="form-1",
        latest_ts=NOW,
        now=NOW,
    )
    current = tracker.get(2821)
    assert current is not None

    apply_waiting_for_form(
        tracker,
        2821,
        existing=current,
        latest_cid="form-1",
        latest_ts=NOW,
        now=NOW + timedelta(hours=1),
    )

    waiting = tracker.get(2821)
    assert waiting is not None
    assert waiting.form_wait_started_at == NOW


def test_human_takeover_is_side_effect_free_external_ownership(tmp_path):
    tracker = _state(tmp_path)
    existing = TicketState(
        state="awaiting_customer",
        first_seen=NOW - timedelta(hours=1),
    )

    apply_human_takeover(
        tracker,
        2814,
        existing=existing,
        latest_cid="cid-human",
        latest_ts=NOW,
        now=NOW,
        reason="human_reply",
    )

    record = tracker.get(2814)
    assert record is not None
    assert record.state == "externally_owned"
    assert record.ownership_action == "support"
    assert record.ownership_reason == "human_reply"
    assert record.ownership_effect_reference == ""


def test_customer_response_timeout_stops_automation_without_handoff(tmp_path):
    tracker = _state(tmp_path)
    tracker.set(
        2815,
        TicketState(
            state="awaiting_customer",
            first_seen=NOW - timedelta(days=3),
            last_agent_finish_ts=NOW - timedelta(hours=25),
        ),
    )

    transition = apply_waiting_poller_timeouts(
        tracker,
        2815,
        customer_wait_timeout_hours=24,
        now=NOW,
    )

    record = tracker.get(2815)
    assert transition is not None
    assert transition.kind == "out_of_scope"
    assert transition.last_verdict == "customer_response_timeout"
    assert record is not None
    assert record.state == "out_of_scope"
    assert record.escalated_at is None
    assert record.ownership_effect_reference == ""


def test_form_timeout_stops_automation_without_handoff(tmp_path):
    tracker = _state(tmp_path)
    tracker.set(
        2816,
        TicketState(
            state="waiting_for_form",
            first_seen=NOW - timedelta(hours=25),
        ),
    )

    transition = apply_waiting_poller_timeouts(
        tracker,
        2816,
        customer_wait_timeout_hours=24,
        now=NOW,
    )

    record = tracker.get(2816)
    assert transition is not None
    assert transition.last_verdict == "form_submission_timeout"
    assert record is not None
    assert record.state == "out_of_scope"


def test_wait_timeout_ignores_non_waiting_and_unexpired_records(tmp_path):
    tracker = _state(tmp_path)
    tracker.set(
        2817,
        TicketState(
            state="in_silence_window",
            first_seen=NOW - timedelta(days=2),
        ),
    )
    tracker.set(
        2818,
        TicketState(
            state="awaiting_customer",
            first_seen=NOW - timedelta(minutes=5),
            last_agent_finish_ts=NOW - timedelta(minutes=5),
        ),
    )

    assert (
        apply_waiting_poller_timeouts(
            tracker,
            2817,
            customer_wait_timeout_hours=24,
            now=NOW,
        )
        is None
    )
    assert (
        apply_waiting_poller_timeouts(
            tracker,
            2818,
            customer_wait_timeout_hours=24,
            now=NOW,
        )
        is None
    )


def test_status_scope_guard_uses_out_of_scope_terminal_state(tmp_path):
    tracker = _state(tmp_path)
    tracker.set(
        2819,
        TicketState(state="awaiting_customer", first_seen=NOW),
    )

    transition = apply_waiting_poller_out_of_scope(
        tracker,
        2819,
        reason="status_out_of_scope",
    )

    assert transition is not None
    assert transition.kind == "out_of_scope"
    assert tracker.get(2819).state == "out_of_scope"

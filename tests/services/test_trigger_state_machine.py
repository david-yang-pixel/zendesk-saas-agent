"""Tests for trigger state transitions that are independent of aiohttp wiring."""
from datetime import datetime, timedelta, timezone

from zd_agent.services.trigger_state_machine import (
    apply_escalated_to_human,
    apply_session_finished,
    apply_silence_window_expired,
    apply_verdict_enter_silence,
    apply_verdict_processed,
    apply_verdict_waiting_for_issue,
    apply_waiting_poller_abandoned,
    apply_waiting_poller_timeouts,
)
from zd_agent.session.agent_session import AgentSession, SessionStatus
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


def _state(tmp_path):
    return TicketStateTracker(tmp_path / "tickets.json")


def _ticket_state(state: str = "processing") -> TicketState:
    return TicketState(
        state=state,
        first_seen=datetime.now(timezone.utc) - timedelta(minutes=5),
        silence_window_end=datetime.now(timezone.utc) + timedelta(minutes=1),
    )


def test_completed_reply_marks_owned_processing_ticket_processed(tmp_path):
    tracker = _state(tmp_path)
    session = AgentSession(ticket_id=2808)
    ticket_state = _ticket_state("processing")
    ticket_state.active_session_id = session.session_id
    tracker.set("2808", ticket_state)
    session.status = SessionStatus.COMPLETED
    session.reply_sent = True
    now = datetime(2026, 6, 5, tzinfo=timezone.utc)

    changed = apply_session_finished(tracker, session, now=now)

    rec = tracker.get("2808")
    assert changed is True
    assert rec is not None
    assert rec.state == "processed"
    assert rec.last_agent_finish_ts == now


def test_failed_processing_schedules_immediate_retry(tmp_path):
    tracker = _state(tmp_path)
    tracker.set("2809", _ticket_state("processing"))
    session = AgentSession(ticket_id=2809)
    session.status = SessionStatus.FAILED
    now = datetime(2026, 6, 5, tzinfo=timezone.utc)

    changed = apply_session_finished(tracker, session, now=now)

    rec = tracker.get("2809")
    assert changed is True
    assert rec is not None
    assert rec.state == "in_silence_window"
    assert rec.silence_window_end == now
    assert rec.last_agent_finish_ts == now


def test_completed_without_reply_schedules_immediate_retry(tmp_path):
    tracker = _state(tmp_path)
    tracker.set("2811", _ticket_state("processing"))
    session = AgentSession(ticket_id=2811)
    session.status = SessionStatus.COMPLETED
    session.reply_sent = False
    now = datetime(2026, 6, 5, tzinfo=timezone.utc)

    changed = apply_session_finished(tracker, session, now=now)

    rec = tracker.get("2811")
    assert changed is True
    assert rec is not None
    assert rec.state == "in_silence_window"
    assert rec.silence_window_end == now
    assert rec.last_agent_finish_ts == now


def test_timeout_processing_schedules_immediate_retry(tmp_path):
    tracker = _state(tmp_path)
    tracker.set("2812", _ticket_state("processing"))
    session = AgentSession(ticket_id=2812)
    session.status = SessionStatus.TIMEOUT
    now = datetime(2026, 6, 5, tzinfo=timezone.utc)

    changed = apply_session_finished(tracker, session, now=now)

    rec = tracker.get("2812")
    assert changed is True
    assert rec is not None
    assert rec.state == "in_silence_window"
    assert rec.silence_window_end == now
    assert rec.last_agent_finish_ts == now


def test_failed_non_processing_does_not_mutate_newer_lifecycle_state(tmp_path):
    tracker = _state(tmp_path)
    tracker.set("2810", _ticket_state("waiting_for_issue"))
    session = AgentSession(ticket_id=2810)
    session.status = SessionStatus.FAILED
    now = datetime(2026, 6, 5, tzinfo=timezone.utc)

    changed = apply_session_finished(tracker, session, now=now)

    rec = tracker.get("2810")
    assert changed is True
    assert rec is not None
    assert rec.state == "waiting_for_issue"
    assert rec.silence_window_end is not None
    assert rec.last_agent_finish_ts is None


def test_missing_ticket_state_is_noop(tmp_path):
    tracker = _state(tmp_path)
    session = AgentSession(ticket_id=9999)
    session.status = SessionStatus.COMPLETED
    session.reply_sent = True

    changed = apply_session_finished(tracker, session)

    assert changed is False
    assert tracker.get("9999") is None


def test_expired_substantial_silence_moves_to_processing(tmp_path):
    tracker = _state(tmp_path)
    past = datetime(2026, 6, 5, tzinfo=timezone.utc)
    tracker.set("1", TicketState(
        state="in_silence_window",
        first_seen=past - timedelta(minutes=10),
        silence_window_end=past,
        last_verdict="substantial_issue",
    ))

    transition = apply_silence_window_expired(
        tracker,
        "1",
        now=past + timedelta(seconds=1),
    )

    rec = tracker.get("1")
    assert transition is not None
    assert transition.queue_source == "silence_waker"
    assert transition.ticket_id == 1
    assert transition.verdict == "substantial_issue"
    assert rec is not None
    assert rec.state == "processing"


def test_expired_non_substantial_silence_moves_to_waiting_for_issue(tmp_path):
    tracker = _state(tmp_path)
    past = datetime(2026, 6, 5, tzinfo=timezone.utc)
    tracker.set("1", TicketState(
        state="in_silence_window",
        first_seen=past - timedelta(minutes=10),
        silence_window_end=past,
        last_verdict="insufficient_info",
    ))

    transition = apply_silence_window_expired(
        tracker,
        "1",
        now=past + timedelta(seconds=1),
    )

    rec = tracker.get("1")
    assert transition is not None
    assert transition.queue_source == "silence_expired_ask"
    assert transition.verdict == "insufficient_info"
    assert rec is not None
    assert rec.state == "waiting_for_issue"


def test_unexpired_or_malformed_silence_is_noop(tmp_path):
    tracker = _state(tmp_path)
    now = datetime(2026, 6, 5, tzinfo=timezone.utc)
    tracker.set("future", TicketState(
        state="in_silence_window",
        first_seen=now,
        silence_window_end=now + timedelta(minutes=1),
    ))
    tracker.set("missing-end", TicketState(
        state="in_silence_window",
        first_seen=now,
        silence_window_end=None,
    ))

    assert apply_silence_window_expired(tracker, "future", now=now) is None
    assert apply_silence_window_expired(tracker, "missing-end", now=now) is None
    assert tracker.get("future").state == "in_silence_window"
    assert tracker.get("missing-end").state == "in_silence_window"


def test_waiting_poller_terminal_guard_marks_abandoned(tmp_path):
    tracker = _state(tmp_path)
    now = datetime(2026, 6, 5, tzinfo=timezone.utc)
    tracker.set("1", TicketState(
        state="waiting_for_issue",
        first_seen=now - timedelta(minutes=10),
        last_verdict="insufficient_info",
    ))

    transition = apply_waiting_poller_abandoned(
        tracker,
        "1",
        reason="status_out_of_scope",
    )

    rec = tracker.get("1")
    assert transition is not None
    assert transition.kind == "abandoned"
    assert transition.prev_state == "waiting_for_issue"
    assert rec is not None
    assert rec.state == "abandoned"


def test_waiting_poller_24h_timeout_marks_abandoned_and_alert_sent(tmp_path):
    tracker = _state(tmp_path)
    now = datetime(2026, 6, 5, tzinfo=timezone.utc)
    tracker.set("1", TicketState(
        state="waiting_for_issue",
        first_seen=now - timedelta(hours=25),
        last_verdict="insufficient_info",
        ask_count=2,
    ))

    transition = apply_waiting_poller_timeouts(
        tracker,
        "1",
        abandoned_hours=24,
        greeting_alert_minutes=30,
        now=now,
    )

    rec = tracker.get("1")
    assert transition is not None
    assert transition.kind == "abandoned"
    assert transition.should_alert is True
    assert transition.last_verdict == "insufficient_info"
    assert transition.ask_count == 2
    assert rec is not None
    assert rec.state == "abandoned"
    assert rec.abandoned_alert_sent_at == now


def test_waiting_poller_24h_timeout_does_not_double_alert(tmp_path):
    tracker = _state(tmp_path)
    now = datetime(2026, 6, 5, tzinfo=timezone.utc)
    already = now - timedelta(minutes=5)
    tracker.set("1", TicketState(
        state="waiting_for_issue",
        first_seen=now - timedelta(hours=25),
        last_verdict="insufficient_info",
        abandoned_alert_sent_at=already,
    ))

    transition = apply_waiting_poller_timeouts(
        tracker,
        "1",
        abandoned_hours=24,
        greeting_alert_minutes=30,
        now=now,
    )

    rec = tracker.get("1")
    assert transition is not None
    assert transition.kind == "abandoned"
    assert transition.should_alert is False
    assert rec is not None
    assert rec.abandoned_alert_sent_at == already


def test_waiting_poller_greeting_timeout_sets_one_shot_alert(tmp_path):
    tracker = _state(tmp_path)
    now = datetime(2026, 6, 5, tzinfo=timezone.utc)
    tracker.set("1", TicketState(
        state="waiting_for_issue",
        first_seen=now - timedelta(minutes=31),
        last_verdict="greeting",
    ))

    transition = apply_waiting_poller_timeouts(
        tracker,
        "1",
        abandoned_hours=24,
        greeting_alert_minutes=30,
        now=now,
    )

    rec = tracker.get("1")
    assert transition is not None
    assert transition.kind == "greeting_alert"
    assert rec is not None
    assert rec.state == "waiting_for_issue"
    assert rec.alert_sent_at == now


def test_waiting_poller_timeouts_skip_non_waiting_state(tmp_path):
    tracker = _state(tmp_path)
    now = datetime(2026, 6, 5, tzinfo=timezone.utc)
    tracker.set("1", TicketState(
        state="in_silence_window",
        first_seen=now - timedelta(hours=25),
        last_verdict="greeting",
    ))

    transition = apply_waiting_poller_timeouts(
        tracker,
        "1",
        abandoned_hours=24,
        greeting_alert_minutes=30,
        now=now,
    )

    assert transition is None
    assert tracker.get("1").state == "in_silence_window"


def test_verdict_self_resolved_marks_processed_and_preserves_fields(tmp_path):
    tracker = _state(tmp_path)
    now = datetime(2026, 6, 5, tzinfo=timezone.utc)
    existing = TicketState(
        state="waiting_for_issue",
        first_seen=now - timedelta(hours=1),
        ask_count=2,
        nudge_sent_at=now - timedelta(minutes=20),
        abandoned_alert_sent_at=now - timedelta(minutes=10),
        last_agent_finish_ts=now - timedelta(minutes=5),
    )

    transition = apply_verdict_processed(
        tracker,
        2810,
        existing=existing,
        latest_cid="cid-2",
        latest_ts=now - timedelta(seconds=30),
        now=now,
        verdict_str="self_resolved",
        ask_count_now=existing.ask_count,
    )

    rec = tracker.get(2810)
    assert transition.kind == "processed"
    assert transition.ask_count_before == 2
    assert transition.ask_count_after == 2
    assert rec is not None
    assert rec.state == "processed"
    assert rec.first_seen == existing.first_seen
    assert rec.last_customer_comment_id == "cid-2"
    assert rec.last_verdict == "self_resolved"
    assert rec.ask_count == 2
    assert rec.nudge_sent_at == existing.nudge_sent_at
    assert rec.abandoned_alert_sent_at == existing.abandoned_alert_sent_at
    assert rec.last_agent_finish_ts == existing.last_agent_finish_ts


def test_verdict_substantial_enters_silence_and_clears_retry_markers(tmp_path):
    tracker = _state(tmp_path)
    now = datetime(2026, 6, 5, tzinfo=timezone.utc)
    existing = TicketState(
        state="waiting_for_issue",
        first_seen=now - timedelta(hours=1),
        ask_count=3,
        nudge_sent_at=now - timedelta(minutes=20),
        alert_sent_at=now - timedelta(minutes=15),
        abandoned_alert_sent_at=now - timedelta(minutes=10),
        last_agent_finish_ts=now - timedelta(minutes=5),
    )

    transition = apply_verdict_enter_silence(
        tracker,
        2809,
        existing=existing,
        latest_cid="cid-3",
        latest_ts=now - timedelta(seconds=30),
        now=now,
        verdict_str="substantial_issue",
        ask_count_now=existing.ask_count,
        silence_minutes=3,
    )

    rec = tracker.get(2809)
    assert transition.kind == "in_silence_window"
    assert transition.silence_min == 3
    assert transition.silence_window_end == now + timedelta(minutes=3)
    assert rec is not None
    assert rec.state == "in_silence_window"
    assert rec.silence_window_end == now + timedelta(minutes=3)
    assert rec.nudge_sent_at is None
    assert rec.alert_sent_at is None
    assert rec.abandoned_alert_sent_at == existing.abandoned_alert_sent_at
    assert rec.last_agent_finish_ts == existing.last_agent_finish_ts


def test_verdict_waiting_for_issue_increments_only_when_ask_was_sent(tmp_path):
    tracker = _state(tmp_path)
    now = datetime(2026, 6, 5, tzinfo=timezone.utc)
    existing = TicketState(
        state="waiting_for_issue",
        first_seen=now - timedelta(hours=1),
        ask_count=3,
        nudge_sent_at=now - timedelta(minutes=20),
        alert_sent_at=now - timedelta(minutes=15),
        abandoned_alert_sent_at=now - timedelta(minutes=10),
        last_agent_finish_ts=now - timedelta(minutes=5),
    )

    sent_transition = apply_verdict_waiting_for_issue(
        tracker,
        2808,
        existing=existing,
        latest_cid="cid-4",
        latest_ts=now - timedelta(seconds=30),
        now=now,
        verdict_str="insufficient_info",
        ask_count_now=existing.ask_count,
        sent=True,
    )

    rec = tracker.get(2808)
    assert sent_transition.kind == "waiting_for_issue"
    assert sent_transition.ask_count_before == 3
    assert sent_transition.ask_count_after == 4
    assert rec is not None
    assert rec.state == "waiting_for_issue"
    assert rec.ask_count == 4
    assert rec.nudge_sent_at is None
    assert rec.alert_sent_at is None
    assert rec.abandoned_alert_sent_at == existing.abandoned_alert_sent_at

    failed_transition = apply_verdict_waiting_for_issue(
        tracker,
        2808,
        existing=rec,
        latest_cid="cid-5",
        latest_ts=now,
        now=now,
        verdict_str="greeting",
        ask_count_now=rec.ask_count,
        sent=False,
    )

    rec = tracker.get(2808)
    assert failed_transition.ask_count_before == 4
    assert failed_transition.ask_count_after == 4
    assert rec is not None
    assert rec.ask_count == 4
    assert rec.last_verdict == "greeting"


def test_escalated_to_human_marks_state_and_preserves_audit_fields(tmp_path):
    tracker = _state(tmp_path)
    now = datetime(2026, 6, 5, tzinfo=timezone.utc)
    existing = TicketState(
        state="waiting_for_issue",
        first_seen=now - timedelta(hours=1),
        ask_count=5,
        nudge_sent_at=now - timedelta(minutes=20),
        alert_sent_at=now - timedelta(minutes=15),
        abandoned_alert_sent_at=now - timedelta(minutes=10),
        last_agent_finish_ts=now - timedelta(minutes=5),
    )

    transition = apply_escalated_to_human(
        tracker,
        2811,
        existing=existing,
        latest_cid="cid-6",
        latest_ts=now - timedelta(seconds=30),
        now=now,
        verdict_str="request_human",
        reason="customer_requested",
    )

    rec = tracker.get(2811)
    assert transition.kind == "escalated_to_human"
    assert transition.reason == "customer_requested"
    assert transition.ask_count == 5
    assert rec is not None
    assert rec.state == "escalated_to_human"
    assert rec.first_seen == existing.first_seen
    assert rec.last_customer_comment_id == "cid-6"
    assert rec.last_verdict == "request_human"
    assert rec.ask_count == 5
    assert rec.escalated_at == now
    assert rec.escalated_reason == "customer_requested"
    assert rec.nudge_sent_at == existing.nudge_sent_at
    assert rec.alert_sent_at == existing.alert_sent_at
    assert rec.abandoned_alert_sent_at == existing.abandoned_alert_sent_at
    assert rec.last_agent_finish_ts == existing.last_agent_finish_ts

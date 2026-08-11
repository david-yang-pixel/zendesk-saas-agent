"""Tests for trigger consumer routing helpers."""

from datetime import datetime, timedelta, timezone

from zd_agent.services.trigger_routing import (
    customer_reopen_metric,
    direct_trigger_cooldown,
    is_duplicate_customer_comment,
    latest_customer_message_context,
    post_agent_cooldown,
)
from zd_agent.trigger.state_store import TicketState


def _now() -> datetime:
    return datetime(2026, 6, 5, 13, 0, tzinfo=timezone.utc)


def _state(*, finished_delta: timedelta | None = None) -> TicketState:
    now = _now()
    return TicketState(
        state="processed",
        first_seen=now - timedelta(minutes=10),
        last_agent_finish_ts=(
            now - finished_delta if finished_delta is not None else None
        ),
    )


def test_latest_customer_message_context_parses_timestamp() -> None:
    latest, latest_cid, latest_ts = latest_customer_message_context(
        [{"id": "c1", "created_at": "2026-06-05T04:59:30Z"}],
        now=_now(),
    )

    assert latest["id"] == "c1"
    assert latest_cid == "c1"
    assert latest_ts == datetime(2026, 6, 5, 4, 59, 30, tzinfo=timezone.utc)


def test_latest_customer_message_context_falls_back_to_now_on_bad_timestamp() -> None:
    _latest, latest_cid, latest_ts = latest_customer_message_context(
        [{"id": "c2", "created_at": "not-a-time"}],
        now=_now(),
    )

    assert latest_cid == "c2"
    assert latest_ts == _now()


def test_direct_trigger_cooldown_uses_short_mention_window_when_hint_present() -> None:
    decision = direct_trigger_cooldown(
        existing=_state(finished_delta=timedelta(seconds=20)),
        source="mention",
        hint="please rerun",
        full_cooldown_seconds=180,
        now=_now(),
    )

    assert decision.should_skip is True
    assert decision.cooldown_seconds == 30
    assert decision.elapsed_seconds == 20


def test_direct_trigger_cooldown_uses_full_window_for_silence_waker() -> None:
    decision = direct_trigger_cooldown(
        existing=_state(finished_delta=timedelta(seconds=60)),
        source="silence_waker",
        hint="",
        full_cooldown_seconds=180,
        now=_now(),
    )

    assert decision.should_skip is True
    assert decision.cooldown_seconds == 180
    assert decision.elapsed_seconds == 60


def test_direct_trigger_cooldown_allows_when_finish_time_missing() -> None:
    decision = direct_trigger_cooldown(
        existing=_state(finished_delta=None),
        source="mention",
        hint="please rerun",
        full_cooldown_seconds=180,
        now=_now(),
    )

    assert decision.should_skip is False


def test_post_agent_cooldown_skips_within_window() -> None:
    decision = post_agent_cooldown(
        existing=_state(finished_delta=timedelta(seconds=100)),
        cooldown_seconds=180,
        now=_now(),
    )

    assert decision.should_skip is True
    assert decision.elapsed_seconds == 100
    assert decision.cooldown_seconds == 180


def test_post_agent_cooldown_allows_after_window() -> None:
    decision = post_agent_cooldown(
        existing=_state(finished_delta=timedelta(seconds=300)),
        cooldown_seconds=180,
        now=_now(),
    )

    assert decision.should_skip is False


def test_duplicate_customer_comment_matches_existing_last_comment_id() -> None:
    existing = _state()
    existing.last_customer_comment_id = "cid-1"

    assert is_duplicate_customer_comment(existing, "cid-1") is True
    assert is_duplicate_customer_comment(existing, "cid-2") is False
    assert is_duplicate_customer_comment(None, "cid-1") is False


def test_customer_reopen_metric_returns_hours_since_processed() -> None:
    existing = _state()
    existing.state = "processed"
    existing.last_eval_at = _now() - timedelta(hours=2, minutes=30)

    metric = customer_reopen_metric(
        existing=existing,
        source="zendesk_webhook",
        now=_now(),
    )

    assert metric is not None
    assert metric.prev_state == "processed"
    assert metric.source == "zendesk_webhook"
    assert metric.hours_since_processed == 2.5


def test_customer_reopen_metric_handles_naive_last_eval_at() -> None:
    existing = _state()
    existing.state = "processed"
    existing.last_eval_at = datetime(2026, 6, 5, 10, 0)

    metric = customer_reopen_metric(
        existing=existing,
        source="waiting_poller",
        now=_now(),
    )

    assert metric is not None
    assert metric.hours_since_processed == 3.0


def test_customer_reopen_metric_skips_non_processed_state() -> None:
    existing = _state()
    existing.state = "waiting_for_issue"

    assert (
        customer_reopen_metric(
            existing=existing,
            source="zendesk_webhook",
            now=_now(),
        )
        is None
    )

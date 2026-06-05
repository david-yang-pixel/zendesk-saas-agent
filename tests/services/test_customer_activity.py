"""Tests for customer activity gates after message fetch."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from zd_agent.services.customer_activity import handle_customer_activity_gate
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


def _now() -> datetime:
    return datetime(2026, 6, 5, 14, 15, tzinfo=timezone.utc)


def _state(tmp_path) -> TicketStateTracker:
    return TicketStateTracker(tmp_path / "tickets.json")


def test_duplicate_customer_comment_is_handled_without_metric(tmp_path) -> None:
    state = _state(tmp_path)
    state.set(
        12345,
        TicketState(
            state="waiting_for_issue",
            first_seen=_now(),
            last_customer_comment_id="cid-1",
        ),
    )
    emit_reopen = MagicMock()

    result = handle_customer_activity_gate(
        ticket_id=12345,
        customer_msgs=[{"id": "cid-1", "created_at": "2026-06-05T06:00:00Z"}],
        state=state,
        log_path=tmp_path / "trigger.jsonl",
        source="zendesk_webhook",
        cooldown_seconds=180,
        now=_now(),
        emit_customer_reopen_fn=emit_reopen,
    )

    assert result.handled is True
    assert result.action == "duplicate_customer_comment"
    assert result.latest_cid == "cid-1"
    emit_reopen.assert_not_called()


def test_processed_ticket_new_customer_comment_emits_reopen_metric(tmp_path) -> None:
    state = _state(tmp_path)
    state.set(
        12345,
        TicketState(
            state="processed",
            first_seen=_now() - timedelta(hours=3),
            last_eval_at=_now() - timedelta(hours=2),
            last_customer_comment_id="old",
        ),
    )
    emit_reopen = MagicMock()

    result = handle_customer_activity_gate(
        ticket_id=12345,
        customer_msgs=[{"id": "cid-2", "created_at": "2026-06-05T06:10:00Z"}],
        state=state,
        log_path=tmp_path / "trigger.jsonl",
        source="waiting_poller",
        cooldown_seconds=180,
        now=_now(),
        emit_customer_reopen_fn=emit_reopen,
    )

    assert result.handled is False
    assert result.action == "continue"
    assert result.latest_cid == "cid-2"
    assert result.existing is state.get(12345)
    emit_reopen.assert_called_once_with(
        ticket_id=12345,
        prev_state="processed",
        source="waiting_poller",
        hours_since_processed=2.0,
    )


def test_post_agent_cooldown_advances_comment_id_and_logs_skip(tmp_path) -> None:
    state = _state(tmp_path)
    state.set(
        12345,
        TicketState(
            state="waiting_for_issue",
            first_seen=_now() - timedelta(minutes=10),
            last_agent_finish_ts=_now() - timedelta(seconds=20),
            last_customer_comment_id="cid-1",
        ),
    )
    log_path = tmp_path / "trigger.jsonl"

    result = handle_customer_activity_gate(
        ticket_id=12345,
        customer_msgs=[{"id": "cid-2", "created_at": "2026-06-05T06:10:00Z"}],
        state=state,
        log_path=log_path,
        source="zendesk_webhook",
        cooldown_seconds=180,
        now=_now(),
        emit_customer_reopen_fn=MagicMock(),
    )

    assert result.handled is True
    assert result.action == "post_agent_cooldown"
    assert state.get(12345).last_customer_comment_id == "cid-2"

    [record] = log_path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(record)
    assert payload["ticket"] == "12345"
    assert payload["verdict"] == "post_agent_cooldown"
    assert payload["action"] == "skip"


def test_allowed_activity_returns_latest_context(tmp_path) -> None:
    state = _state(tmp_path)

    result = handle_customer_activity_gate(
        ticket_id=12345,
        customer_msgs=[{"id": "cid-3", "created_at": "bad-time"}],
        state=state,
        log_path=tmp_path / "trigger.jsonl",
        source="zendesk_webhook",
        cooldown_seconds=180,
        now=_now(),
        emit_customer_reopen_fn=MagicMock(),
    )

    assert result.handled is False
    assert result.latest_cid == "cid-3"
    assert result.latest_ts == _now()
    assert result.existing is None

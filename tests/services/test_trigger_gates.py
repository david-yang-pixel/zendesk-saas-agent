"""Tests for trigger consumer entry gates."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

from zd_agent.config import AppConfig, ZendeskConfig
from zd_agent.services.trigger_gates import handle_trigger_entry_gate
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


def _now() -> datetime:
    return datetime(2026, 6, 5, 14, 5, tzinfo=timezone.utc)


def _cfg(*, min_ticket_id: int = 0) -> AppConfig:
    cfg = AppConfig()
    cfg.zendesk = ZendeskConfig(min_ticket_id=min_ticket_id)
    return cfg


def _state(tmp_path) -> TicketStateTracker:
    return TicketStateTracker(tmp_path / "tickets.json")


def _read_one_log(log_path) -> dict:
    [line] = log_path.read_text(encoding="utf-8").splitlines()
    return json.loads(line)


def test_below_min_ticket_id_is_skipped_and_logged(tmp_path) -> None:
    log_path = tmp_path / "trigger.jsonl"

    result = handle_trigger_entry_gate(
        ticket_id=2500,
        source="zendesk_webhook",
        state=_state(tmp_path),
        log_path=log_path,
        cfg=_cfg(min_ticket_id=2624),
        is_paused_fn=MagicMock(return_value=False),
    )

    assert result.handled is True
    assert result.action == "below_min_ticket_id"
    payload = _read_one_log(log_path)
    assert payload["verdict"] == "below_min_ticket_id"
    assert payload["action"] == "skip"
    assert payload["source"] == "zendesk_webhook"
    assert payload["min_ticket_id"] == 2624


def test_paused_state_is_skipped_after_min_ticket_gate(tmp_path) -> None:
    log_path = tmp_path / "trigger.jsonl"

    result = handle_trigger_entry_gate(
        ticket_id=2624,
        source="mention",
        state=_state(tmp_path),
        log_path=log_path,
        cfg=_cfg(min_ticket_id=2624),
        is_paused_fn=MagicMock(return_value=True),
    )

    assert result.handled is True
    assert result.action == "paused"
    payload = _read_one_log(log_path)
    assert payload["verdict"] == "paused"
    assert payload["source"] == "mention"


def test_escalated_ticket_is_skipped_and_reason_logged(tmp_path) -> None:
    state = _state(tmp_path)
    state.set(
        9999,
        TicketState(
            state="escalated_to_human",
            first_seen=_now(),
            escalated_at=_now(),
            escalated_reason="high_risk_refund",
        ),
    )
    log_path = tmp_path / "trigger.jsonl"

    result = handle_trigger_entry_gate(
        ticket_id=9999,
        source="waiting_poller",
        state=state,
        log_path=log_path,
        cfg=_cfg(),
        is_paused_fn=MagicMock(return_value=False),
    )

    assert result.handled is True
    assert result.action == "escalated_to_human"
    payload = _read_one_log(log_path)
    assert payload["verdict"] == "escalated_to_human"
    assert payload["escalated_reason"] == "high_risk_refund"


def test_mention_trigger_bypasses_escalated_state(tmp_path) -> None:
    state = _state(tmp_path)
    state.set(
        9999,
        TicketState(
            state="escalated_to_human",
            first_seen=_now(),
            escalated_at=_now(),
            escalated_reason="customer_requested",
        ),
    )

    result = handle_trigger_entry_gate(
        ticket_id=9999,
        source="mention",
        state=state,
        log_path=tmp_path / "trigger.jsonl",
        cfg=_cfg(),
        is_paused_fn=MagicMock(return_value=False),
    )

    assert result.handled is False
    assert result.action == "continue"


def test_gate_allows_when_no_skip_condition_matches(tmp_path) -> None:
    result = handle_trigger_entry_gate(
        ticket_id=9999,
        source="mention",
        state=_state(tmp_path),
        log_path=tmp_path / "trigger.jsonl",
        cfg=_cfg(min_ticket_id=0),
        is_paused_fn=MagicMock(return_value=False),
    )

    assert result.handled is False
    assert result.action == "continue"

"""Tests for human takeover dispatch orchestration."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from zd_agent.services.human_takeover_dispatch import handle_human_takeover_dispatch
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


def _now() -> datetime:
    return datetime(2026, 6, 5, 14, 30, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_no_takeover_returns_unhandled(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    escalate = AsyncMock()

    result = await handle_human_takeover_dispatch(
        ticket_id=12345,
        ticket_info={"id": 12345},
        customer_msgs=[{"id": "cid-1", "created_at": "2026-06-05T06:00:00Z"}],
        state=state,
        log_path=tmp_path / "trigger.jsonl",
        source="zendesk_webhook",
        agent_user_id=45433304089364,
        detect_human_takeover_fn=AsyncMock(return_value=(False, "")),
        escalate_fn=escalate,
        now=_now(),
    )

    assert result.handled is False
    assert result.reason == ""
    escalate.assert_not_awaited()


@pytest.mark.asyncio
async def test_takeover_logs_and_escalates_with_latest_customer_context(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    existing = TicketState(state="waiting_for_issue", first_seen=_now())
    state.set(12345, existing)
    escalate = AsyncMock()
    log_path = tmp_path / "trigger.jsonl"

    result = await handle_human_takeover_dispatch(
        ticket_id=12345,
        ticket_info={"id": 12345},
        customer_msgs=[
            {"id": "cid-1", "created_at": "2026-06-05T06:00:00Z"},
            {"id": "cid-2", "created_at": "2026-06-05T06:10:00Z"},
        ],
        state=state,
        log_path=log_path,
        source="waiting_poller",
        agent_user_id=45433304089364,
        detect_human_takeover_fn=AsyncMock(
            return_value=(True, "last public reply by other SE"),
        ),
        escalate_fn=escalate,
        now=_now(),
    )

    assert result.handled is True
    assert result.reason == "last public reply by other SE"
    escalate.assert_awaited_once()
    kwargs = escalate.await_args.kwargs
    assert kwargs["ticket_id"] == 12345
    assert kwargs["reason"] == "human_takeover_detected"
    assert kwargs["existing"] is existing
    assert kwargs["source"] == "waiting_poller"
    assert kwargs["verdict_str"] == "human_takeover"
    assert kwargs["latest_cid"] == "cid-2"
    assert kwargs["latest_ts"] == datetime(
        2026, 6, 5, 6, 10, tzinfo=timezone.utc,
    )
    assert kwargs["extra_log"] == {
        "takeover_reason": "last public reply by other SE",
    }

    [record] = log_path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(record)
    assert payload["ticket"] == "12345"
    assert payload["verdict"] == "human_takeover"
    assert payload["action"] == "escalate"
    assert payload["reason"] == "last public reply by other SE"

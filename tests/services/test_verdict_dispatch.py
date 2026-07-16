"""Tests for verdict dispatch orchestration service."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from zd_agent.config import AppConfig, PublicReplyConfig, TriggerDeciderConfig
from zd_agent.services.verdict_dispatch import handle_post_verdict_dispatch
from zd_agent.trigger.decider import PreCheckVerdict
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


def _now():
    return datetime(2026, 6, 5, 13, 35, tzinfo=timezone.utc)


def _cfg(*, max_asks: int = 10, silence_min: int = 3) -> AppConfig:
    cfg = AppConfig()
    cfg.public_reply = PublicReplyConfig(max_ask_count=max_asks)
    cfg.trigger_decider = TriggerDeciderConfig(silence_minutes_default=silence_min)
    return cfg


def _verdict(kind: str, completeness: int = 80) -> PreCheckVerdict:
    return PreCheckVerdict(verdict=kind, completeness=completeness, reason="test")


@pytest.mark.asyncio
async def test_self_resolved_marks_processed(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    send_closed = AsyncMock(return_value=True)

    await handle_post_verdict_dispatch(
        ticket_id=12345,
        verdict=_verdict("self_resolved"),
        customer_msgs=[],
        ticket_info={"id": 12345},
        latest_cid="cid-1",
        latest_ts=_now(),
        existing=None,
        state=state,
        log_path=Path(tmp_path) / "trigger.jsonl",
        source="zendesk_webhook",
        now=_now(),
        cfg=_cfg(),
        escalate_fn=AsyncMock(),
        send_ask_fn=AsyncMock(),
        send_closed_fn=send_closed,
    )

    rec = state.get(12345)
    assert rec is not None
    assert rec.state == "processed"
    assert rec.last_verdict == "self_resolved"
    send_closed.assert_awaited_once()


@pytest.mark.asyncio
async def test_self_resolved_uses_cursor_accepted_by_final_freshness_gate(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    send_closed = AsyncMock(return_value=SimpleNamespace(
        success=True,
        accepted_snapshot_last_id="thanks-after-close",
    ))

    await handle_post_verdict_dispatch(
        ticket_id=12345,
        verdict=_verdict("self_resolved"),
        customer_msgs=[{"id": "original", "body": "I fixed it."}],
        ticket_info={"id": 12345},
        latest_cid="original",
        latest_ts=_now(),
        existing=None,
        state=state,
        log_path=Path(tmp_path) / "trigger.jsonl",
        source="zendesk_webhook",
        now=_now(),
        cfg=_cfg(),
        escalate_fn=AsyncMock(),
        send_ask_fn=AsyncMock(),
        send_closed_fn=send_closed,
    )

    assert state.get(12345).last_customer_comment_id == "thanks-after-close"


@pytest.mark.asyncio
async def test_self_resolved_failed_close_reply_keeps_ticket_retryable(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")

    with pytest.raises(RuntimeError, match="public closed reply delivery failed"):
        await handle_post_verdict_dispatch(
            ticket_id=12345,
            verdict=_verdict("self_resolved"),
            customer_msgs=[],
            ticket_info={"id": 12345},
            latest_cid="cid-1",
            latest_ts=_now(),
            existing=None,
            state=state,
            log_path=Path(tmp_path) / "trigger.jsonl",
            source="zendesk_webhook",
            now=_now(),
            cfg=_cfg(),
            escalate_fn=AsyncMock(),
            send_ask_fn=AsyncMock(),
            send_closed_fn=AsyncMock(return_value=False),
        )

    assert state.get(12345) is None


@pytest.mark.asyncio
async def test_ask_path_calls_send_ask_and_increments_when_sent(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    send_ask = AsyncMock(return_value=True)

    await handle_post_verdict_dispatch(
        ticket_id=12345,
        verdict=_verdict("greeting"),
        customer_msgs=[],
        ticket_info={"id": 12345},
        latest_cid="cid-1",
        latest_ts=_now(),
        existing=None,
        state=state,
        log_path=Path(tmp_path) / "trigger.jsonl",
        source="zendesk_webhook",
        now=_now(),
        cfg=_cfg(),
        escalate_fn=AsyncMock(),
        send_ask_fn=send_ask,
    )

    send_ask.assert_awaited_once()
    assert send_ask.await_args.kwargs["verdict_kind"] == "greeting"
    assert state.get(12345).ask_count == 1


@pytest.mark.asyncio
async def test_ask_uses_cursor_accepted_by_final_freshness_gate(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    send_ask = AsyncMock(return_value=SimpleNamespace(
        success=True,
        accepted_snapshot_last_id="thanks-after-ask",
    ))

    await handle_post_verdict_dispatch(
        ticket_id=12345,
        verdict=_verdict("greeting"),
        customer_msgs=[{"id": "original", "body": "hello"}],
        ticket_info={"id": 12345},
        latest_cid="original",
        latest_ts=_now(),
        existing=None,
        state=state,
        log_path=Path(tmp_path) / "trigger.jsonl",
        source="zendesk_webhook",
        now=_now(),
        cfg=_cfg(),
        escalate_fn=AsyncMock(),
        send_ask_fn=send_ask,
    )

    assert state.get(12345).last_customer_comment_id == "thanks-after-ask"


@pytest.mark.asyncio
async def test_ask_send_failure_is_retryable_and_does_not_advance_state(
    tmp_path,
) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    existing = TicketState(
        state="waiting_for_issue",
        first_seen=_now(),
        ask_count=2,
    )
    state.set(12345, existing)

    with pytest.raises(RuntimeError, match="public ask delivery failed"):
        await handle_post_verdict_dispatch(
            ticket_id=12345,
            verdict=_verdict("greeting"),
            customer_msgs=[{"id": "cid-1", "body": "hello"}],
            ticket_info={"id": 12345},
            latest_cid="cid-1",
            latest_ts=_now(),
            existing=existing,
            state=state,
            log_path=Path(tmp_path) / "trigger.jsonl",
            source="zendesk_webhook",
            now=_now(),
            cfg=_cfg(),
            escalate_fn=AsyncMock(),
            send_ask_fn=AsyncMock(return_value=False),
        )

    assert state.get(12345) == existing


@pytest.mark.asyncio
async def test_substantial_issue_enters_silence_without_ask(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    send_ask = AsyncMock(return_value=True)

    await handle_post_verdict_dispatch(
        ticket_id=12345,
        verdict=_verdict("substantial_issue", completeness=65),
        customer_msgs=[{"body": "I need a refund for my subscription renewal."}],
        ticket_info={"id": 12345},
        latest_cid="cid-1",
        latest_ts=_now(),
        existing=None,
        state=state,
        log_path=Path(tmp_path) / "trigger.jsonl",
        source="zendesk_webhook",
        now=_now(),
        cfg=_cfg(),
        escalate_fn=AsyncMock(),
        send_ask_fn=send_ask,
    )

    rec = state.get(12345)
    assert rec is not None
    assert rec.state == "in_silence_window"
    assert rec.last_verdict == "substantial_issue"
    send_ask.assert_not_awaited()


@pytest.mark.asyncio
async def test_max_ask_count_escalates_without_sending_ask(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    existing = TicketState(
        state="waiting_for_issue",
        first_seen=_now(),
        ask_count=10,
    )
    state.set(12345, existing)
    escalate = AsyncMock()
    send_ask = AsyncMock(return_value=True)

    await handle_post_verdict_dispatch(
        ticket_id=12345,
        verdict=_verdict("insufficient_info"),
        customer_msgs=[{"body": "still not enough"}],
        ticket_info={"id": 12345},
        latest_cid="cid-2",
        latest_ts=_now(),
        existing=existing,
        state=state,
        log_path=Path(tmp_path) / "trigger.jsonl",
        source="zendesk_webhook",
        now=_now(),
        cfg=_cfg(max_asks=10),
        escalate_fn=escalate,
        send_ask_fn=send_ask,
    )

    escalate.assert_awaited_once()
    assert escalate.await_args.kwargs["reason"] == "max_asks_reached"
    assert escalate.await_args.kwargs["customer_phrase"] == "still not enough"
    send_ask.assert_not_awaited()

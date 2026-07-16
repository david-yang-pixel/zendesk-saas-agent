"""Tests for silence-expired ask orchestration."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from zd_agent.services.silence_expiry import handle_silence_expired_ask
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


def _now() -> datetime:
    return datetime(2026, 6, 5, 13, 55, tzinfo=timezone.utc)


def _cfg(*, enable_public_nudge: bool) -> MagicMock:
    cfg = MagicMock()
    cfg.trigger_decider.enable_public_nudge = enable_public_nudge
    return cfg


def _state(tmp_path) -> TicketStateTracker:
    return TicketStateTracker(tmp_path / "tickets.json")


@pytest.mark.asyncio
async def test_no_state_returns_without_side_effects(tmp_path) -> None:
    send_alert = AsyncMock(return_value=True)

    result = await handle_silence_expired_ask(
        ticket_id=12345,
        state=_state(tmp_path),
        log_path=tmp_path / "trigger.jsonl",
        nudge_message_fn=MagicMock(return_value=("en", "More details?")),
        cfg=_cfg(enable_public_nudge=False),
        fetch_customer_messages_fn=AsyncMock(return_value=([], {})),
        send_silence_alert_fn=send_alert,
        now=_now(),
    )

    assert result.action == "no_state"
    send_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_feishu_alert_path_sets_nudge_sent_and_logs(tmp_path) -> None:
    state = _state(tmp_path)
    state.set(
        12345,
        TicketState(
            state="waiting_for_issue",
            first_seen=_now(),
            last_verdict="insufficient_info",
        ),
    )
    send_alert = AsyncMock(return_value=True)
    fetch_messages = AsyncMock(return_value=(
        [{"body": "Name: Alice\nEmail: alice@example.com"}],
        {"id": 12345},
    ))
    log_path = tmp_path / "trigger.jsonl"

    result = await handle_silence_expired_ask(
        ticket_id=12345,
        state=state,
        log_path=log_path,
        nudge_message_fn=MagicMock(return_value=("en", "More details?")),
        cfg=_cfg(enable_public_nudge=False),
        fetch_customer_messages_fn=fetch_messages,
        send_silence_alert_fn=send_alert,
        now=_now(),
    )

    assert result.action == "silence_expired_feishu_alert"
    assert state.get(12345).nudge_sent_at == _now()
    send_alert.assert_awaited_once_with(
        ticket_id=12345,
        verdict="insufficient_info",
    )
    [record] = log_path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(record)
    assert payload["action"] == "silence_expired_feishu_alert"
    assert payload["verdict"] == "insufficient_info"


@pytest.mark.asyncio
async def test_public_nudge_failure_does_not_mark_sent(tmp_path) -> None:
    state = _state(tmp_path)
    state.set(
        12345,
        TicketState(
            state="waiting_for_issue",
            first_seen=_now(),
            last_verdict="greeting",
        ),
    )

    send_nudge = AsyncMock(return_value=False)
    with pytest.raises(RuntimeError, match="public reply delivery failed"):
        await handle_silence_expired_ask(
            ticket_id=12345,
            state=state,
            log_path=tmp_path / "trigger.jsonl",
            nudge_message_fn=MagicMock(return_value=("en", "More details?")),
            cfg=_cfg(enable_public_nudge=True),
            fetch_customer_messages_fn=AsyncMock(return_value=([
                {"id": "message-1", "body": "Hello"},
            ], {})),
            send_public_nudge_fn=send_nudge,
            now=_now(),
        )

    assert state.get(12345).nudge_sent_at is None
    send_nudge.assert_awaited_once_with(
        ticket_id=12345,
        body="More details?",
        lang="en",
        snapshot_customer_message_count=1,
        snapshot_customer_last_message_id="message-1",
        delivery_attempt_id="",
    )


@pytest.mark.asyncio
async def test_second_fetch_with_new_customer_message_never_sends_stale_nudge(
    tmp_path,
) -> None:
    """The final live fetch is a guard, not just personalization input."""
    state = _state(tmp_path)
    state.set(
        12345,
        TicketState(
            state="in_silence_window",
            first_seen=_now(),
            last_verdict="insufficient_info",
            last_customer_comment_id="message-1",
        ),
    )
    send_nudge = AsyncMock(return_value=True)

    result = await handle_silence_expired_ask(
        ticket_id=12345,
        state=state,
        log_path=tmp_path / "trigger.jsonl",
        nudge_message_fn=MagicMock(return_value=("en", "More details?")),
        cfg=_cfg(enable_public_nudge=True),
        fetch_customer_messages_fn=AsyncMock(return_value=([
            {"id": "message-1", "body": "Initial report"},
            {"id": "message-2", "body": "The missing run ID is run-123"},
        ], {"id": 12345})),
        send_public_nudge_fn=send_nudge,
        now=_now(),
        expected_customer_last_id="message-1",
    )

    assert result.action == "new_customer_message"
    send_nudge.assert_not_awaited()
    assert state.get(12345).nudge_sent_at is None

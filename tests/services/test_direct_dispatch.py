"""Tests for direct trigger dispatch sources."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from zd_agent.services.direct_dispatch import handle_direct_dispatch_source
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


def _now() -> datetime:
    return datetime(2026, 6, 5, 13, 45, tzinfo=timezone.utc)


def _cfg(*, cooldown_seconds: int = 180) -> MagicMock:
    cfg = MagicMock()
    cfg.trigger_decider.post_agent_cooldown_seconds = cooldown_seconds
    return cfg


@pytest.mark.asyncio
async def test_non_direct_source_is_not_handled(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    manager = MagicMock()
    manager.dispatch = AsyncMock()

    result = await handle_direct_dispatch_source(
        ticket_id=12345,
        msg={"source": "zendesk_webhook"},
        manager=manager,
        state=state,
        log_path=tmp_path / "trigger.jsonl",
        cfg=_cfg(),
        now=_now(),
    )

    assert result.handled is False
    assert result.dispatched is False
    manager.dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_source_dispatches_with_hint(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    manager = MagicMock()
    manager.dispatch = AsyncMock()

    result = await handle_direct_dispatch_source(
        ticket_id=12345,
        msg={"source": "mention", "hint": "please rerun"},
        manager=manager,
        state=state,
        log_path=tmp_path / "trigger.jsonl",
        cfg=_cfg(),
        now=_now(),
    )

    assert result.handled is True
    assert result.dispatched is True
    assert result.action == "dispatch"
    manager.dispatch.assert_awaited_once_with(
        12345,
        dry_run=False,
        trigger_type="webhook-mention",
        hint="please rerun",
    )


@pytest.mark.asyncio
async def test_direct_source_cooldown_skips_dispatch_and_logs_decision(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    state.set(
        12345,
        TicketState(
            state="processed",
            first_seen=_now() - timedelta(minutes=10),
            last_agent_finish_ts=_now() - timedelta(seconds=20),
        ),
    )
    manager = MagicMock()
    manager.dispatch = AsyncMock()
    log_path = tmp_path / "trigger.jsonl"

    result = await handle_direct_dispatch_source(
        ticket_id=12345,
        msg={"source": "mention", "hint": "please rerun"},
        manager=manager,
        state=state,
        log_path=log_path,
        cfg=_cfg(),
        now=_now(),
    )

    assert result.handled is True
    assert result.dispatched is False
    assert result.action == "skip_post_agent_cooldown"
    assert result.cooldown_seconds == 30
    manager.dispatch.assert_not_awaited()

    [record] = log_path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(record)
    assert payload["ticket"] == "12345"
    assert payload["verdict"] == "post_agent_cooldown_mention"
    assert payload["action"] == "skip"

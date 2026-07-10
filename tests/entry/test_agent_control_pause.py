from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from zd_agent.config import AppConfig, PublicReplyConfig, ResourcesConfig, ZendeskConfig
from zd_agent.services.durable_trigger_queue import DurableTriggerQueue
from zd_agent.services.trigger_consumer import run_saas_trigger_consumer
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


def _make_cfg(tmp_path) -> AppConfig:
    cfg = AppConfig()
    cfg.public_reply = PublicReplyConfig(enabled=False)
    cfg.zendesk = ZendeskConfig(subdomain="x", email="x@y.com", api_token="t")
    cfg.resources = ResourcesConfig(base_dir=str(tmp_path), state_dir=".state")
    return cfg


async def _run_consumer_once(
    state,
    queue,
    manager,
    cfg: AppConfig,
    timeout: float = 0.3,
) -> None:
    try:
        await asyncio.wait_for(
            run_saas_trigger_consumer(queue, manager, state, cfg=cfg),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        pass


@pytest.mark.asyncio
async def test_trigger_consumer_skips_new_work_when_globally_paused(tmp_path):
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    (state_dir / "control.json").write_text(
        json.dumps(
            {
                "paused": True,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "updated_by": "ops@dify.ai",
            }
        ),
        encoding="utf-8",
    )
    state = TicketStateTracker(state_dir / "tickets.json")
    queue: asyncio.Queue = asyncio.Queue()
    manager = MagicMock()
    manager.dispatch = AsyncMock()
    await queue.put({"ticket_id": 2804, "source": "mention", "hint": "check"})

    await _run_consumer_once(state, queue, manager, _make_cfg(tmp_path))

    manager.dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_trigger_consumer_dispatches_when_not_paused(tmp_path):
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    (state_dir / "control.json").write_text(
        json.dumps({"paused": False}),
        encoding="utf-8",
    )
    state = TicketStateTracker(state_dir / "tickets.json")
    queue: asyncio.Queue = asyncio.Queue()
    manager = MagicMock()
    manager.dispatch = AsyncMock()
    await queue.put({"ticket_id": 2804, "source": "mention", "hint": "check"})

    await _run_consumer_once(state, queue, manager, _make_cfg(tmp_path))

    manager.dispatch.assert_called_once()


@pytest.mark.asyncio
async def test_paused_startup_recovery_is_deferred_without_retry_loss(tmp_path):
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    (state_dir / "control.json").write_text(
        json.dumps({"paused": True}),
        encoding="utf-8",
    )
    state = TicketStateTracker(state_dir / "tickets.json")
    state.set(
        2804,
        TicketState(
            state="processing",
            first_seen=datetime.now(timezone.utc),
        ),
    )
    queue = DurableTriggerQueue(
        state_dir / "queue.sqlite3",
        max_attempts=1,
        retry_base_seconds=0,
    )
    queue.ensure_processing_recovery(2804)
    manager = MagicMock()
    manager.dispatch = AsyncMock()

    await _run_consumer_once(state, queue, manager, _make_cfg(tmp_path), timeout=0.1)

    assert await queue.pending_count() == 1
    assert await queue.list_dead_letters() == []
    manager.dispatch.assert_not_called()

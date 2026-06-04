from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zd_agent.config import AppConfig, PublicReplyConfig, ResourcesConfig, ZendeskConfig
from zd_agent.entry.saas_app import _trigger_consumer
from zd_agent.trigger.state_store import TicketStateTracker


def _make_cfg(tmp_path) -> AppConfig:
    cfg = AppConfig()
    cfg.public_reply = PublicReplyConfig(enabled=False)
    cfg.zendesk = ZendeskConfig(subdomain="x", email="x@y.com", api_token="t")
    cfg.resources = ResourcesConfig(base_dir=str(tmp_path), state_dir=".state")
    return cfg


async def _run_consumer_once(state, queue, manager, timeout: float = 0.3) -> None:
    try:
        await asyncio.wait_for(
            _trigger_consumer(queue, manager, state),
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

    with patch("zd_agent.entry.saas_app.get_config", return_value=_make_cfg(tmp_path)):
        await _run_consumer_once(state, queue, manager)

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

    with patch("zd_agent.entry.saas_app.get_config", return_value=_make_cfg(tmp_path)):
        await _run_consumer_once(state, queue, manager)

    manager.dispatch.assert_called_once()

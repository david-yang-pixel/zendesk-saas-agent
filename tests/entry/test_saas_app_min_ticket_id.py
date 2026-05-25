"""Tests for min_ticket_id 灰度过滤（trigger_consumer 顶部）。

确保 min_ticket_id=N 时：
- ticket_id < N → skip
- ticket_id == N → 处理（边界 inclusive）
- ticket_id > N → 处理
- min_ticket_id = 0 → 不过滤（默认行为）
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zd_agent.config import AppConfig, PublicReplyConfig, ZendeskConfig
from zd_agent.entry.saas_app import _trigger_consumer
from zd_agent.trigger.state_store import TicketStateTracker


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_cfg(min_ticket_id: int = 2624) -> AppConfig:
    cfg = AppConfig()
    cfg.public_reply = PublicReplyConfig(enabled=False)
    cfg.zendesk = ZendeskConfig(
        subdomain="x", email="x@y.com", api_token="t",
        min_ticket_id=min_ticket_id,
    )
    return cfg


async def _run_consumer_once(state, queue, manager, timeout: float = 0.3) -> None:
    try:
        await asyncio.wait_for(
            _trigger_consumer(queue, manager, state),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        pass


class TestMinTicketIdFilter:
    @pytest.mark.asyncio
    async def test_below_min_is_skipped(self, tmp_path) -> None:
        """ticket_id < min_ticket_id → skip，不 dispatch"""
        state = TicketStateTracker(tmp_path / "tickets.json")
        queue: asyncio.Queue = asyncio.Queue()
        manager = MagicMock()
        manager.dispatch = AsyncMock()

        await queue.put({"ticket_id": 2500, "source": "zendesk_webhook"})

        with patch(
            "zd_agent.entry.saas_app.get_config",
            return_value=_make_cfg(min_ticket_id=2624),
        ):
            await _run_consumer_once(state, queue, manager)

        # 不该 dispatch，不该 fetch（因为顶部就 skip 了）
        manager.dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_at_boundary_is_processed(self, tmp_path) -> None:
        """ticket_id == min_ticket_id → 处理（inclusive）"""
        state = TicketStateTracker(tmp_path / "tickets.json")
        queue: asyncio.Queue = asyncio.Queue()
        manager = MagicMock()
        manager.dispatch = AsyncMock()

        # mention 走直 dispatch（最简单的路径，能立刻验证未被顶部过滤掉）
        await queue.put({
            "ticket_id": 2624, "source": "mention", "hint": "test",
        })

        with patch(
            "zd_agent.entry.saas_app.get_config",
            return_value=_make_cfg(min_ticket_id=2624),
        ):
            await _run_consumer_once(state, queue, manager)

        # 2624 == 2624 不该被过滤 → mention 走 dispatch
        manager.dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_above_min_is_processed(self, tmp_path) -> None:
        state = TicketStateTracker(tmp_path / "tickets.json")
        queue: asyncio.Queue = asyncio.Queue()
        manager = MagicMock()
        manager.dispatch = AsyncMock()

        await queue.put({
            "ticket_id": 2999, "source": "mention", "hint": "test",
        })

        with patch(
            "zd_agent.entry.saas_app.get_config",
            return_value=_make_cfg(min_ticket_id=2624),
        ):
            await _run_consumer_once(state, queue, manager)

        manager.dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_min_zero_does_not_filter(self, tmp_path) -> None:
        """min_ticket_id=0（默认）→ 不过滤，所有 ID 都处理"""
        state = TicketStateTracker(tmp_path / "tickets.json")
        queue: asyncio.Queue = asyncio.Queue()
        manager = MagicMock()
        manager.dispatch = AsyncMock()

        await queue.put({
            "ticket_id": 100, "source": "mention", "hint": "test",
        })

        with patch(
            "zd_agent.entry.saas_app.get_config",
            return_value=_make_cfg(min_ticket_id=0),
        ):
            await _run_consumer_once(state, queue, manager)

        # min=0 → 100 该被处理
        manager.dispatch.assert_called_once()

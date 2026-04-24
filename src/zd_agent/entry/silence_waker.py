"""Silence Waker: scan tickets in silence window, wake those whose timer expired."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from zd_agent.log import get_logger
from zd_agent.trigger.state_store import TicketStateTracker

logger = get_logger(module="entry.silence_waker")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def scan_once(
    *,
    state: TicketStateTracker,
    trigger_queue: asyncio.Queue,
) -> int:
    """Single scan pass. Returns the count of tickets woken."""
    tickets_in_silence = state.list_by_state("in_silence_window")
    now = _now()
    woken = 0
    for ticket_id in tickets_in_silence:
        rec = state.get(ticket_id)
        if rec is None:
            continue
        end = rec.silence_window_end
        if end is None:
            # malformed — skip
            continue
        if end <= now:
            # 立刻 advance state 避免下次扫时重复 fire
            rec.state = "processing"
            state.set(ticket_id, rec)
            await trigger_queue.put({
                "source": "silence_waker",
                "ticket_id": ticket_id,
            })
            logger.info("silence_woken", ticket_id=ticket_id,
                        silence_end=end.isoformat())
            woken += 1
    return woken


async def run_silence_waker(
    *,
    state: TicketStateTracker,
    trigger_queue: asyncio.Queue,
    scan_interval_seconds: int,
) -> None:
    """Infinite loop: scan_once → sleep → repeat. Catches exceptions per iter."""
    logger.info("silence_waker_started", interval_s=scan_interval_seconds)
    while True:
        try:
            await scan_once(state=state, trigger_queue=trigger_queue)
        except Exception as e:
            logger.error("silence_waker_error", error=str(e))
        await asyncio.sleep(scan_interval_seconds)

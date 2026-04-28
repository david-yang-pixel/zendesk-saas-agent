"""Silence Waker: scan tickets in silence window, route them by verdict.

On expiry of ``silence_window_end``:
  - last_verdict == "substantial_issue" → state="processing", push
    ``{source: "silence_waker"}`` so AgentSession picks it up.
  - else (greeting / insufficient_info / blank) → push
    ``{source: "silence_expired_ask"}``; ``_trigger_consumer`` will either
    send a public nudge (when ``enable_public_nudge`` is true) or push a
    Feishu internal alert (when off). State is moved into
    ``waiting_for_issue`` either way.
"""
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
    """Single scan pass. Returns the count of tickets advanced."""
    tickets_in_silence = state.list_by_state("in_silence_window")
    now = _now()
    advanced = 0
    for ticket_id in tickets_in_silence:
        rec = state.get(ticket_id)
        if rec is None:
            continue
        end = rec.silence_window_end
        if end is None:
            # malformed — skip
            continue
        if end > now:
            continue

        verdict = rec.last_verdict or ""
        if verdict == "substantial_issue":
            # 真的去处理
            rec.state = "processing"
            state.set(ticket_id, rec)
            await trigger_queue.put({
                "source": "silence_waker",
                "ticket_id": int(ticket_id),
            })
            logger.info(
                "silence_expired_dispatch",
                ticket_id=ticket_id, silence_end=end.isoformat(),
                verdict=verdict,
            )
        else:
            # 信息不够 / 寒暄 → 主动 ask 客户 OR 内部告警（_trigger_consumer 决定）
            rec.state = "waiting_for_issue"
            state.set(ticket_id, rec)
            await trigger_queue.put({
                "source": "silence_expired_ask",
                "ticket_id": int(ticket_id),
            })
            logger.info(
                "silence_expired_ask",
                ticket_id=ticket_id, silence_end=end.isoformat(),
                verdict=verdict,
            )
        advanced += 1
    return advanced


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

"""Silence-window expiry scanner."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from zd_agent.log import get_logger
from zd_agent.services.trigger_state_machine import apply_silence_window_expired
from zd_agent.trigger.state_store import TicketStateTracker

logger = get_logger(module="services.silence_waker")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def scan_once(
    *,
    state: TicketStateTracker,
    trigger_queue: Any,
) -> int:
    """Single scan pass. Returns the count of tickets advanced."""
    tickets_in_silence = state.list_by_state("in_silence_window")
    now = _now()
    advanced = 0
    for ticket_id in tickets_in_silence:
        record = state.get(ticket_id)
        if (
            record is None
            or record.silence_window_end is None
            or record.silence_window_end > now
        ):
            continue

        source = (
            "silence_waker"
            if record.last_verdict in {"substantial_issue", "agent_deferred"}
            else "silence_expired_ask"
        )
        await trigger_queue.put({
            "source": source,
            "ticket_id": int(ticket_id),
            "_queue_key": (
                f"silence:{ticket_id}:{source}:"
                f"{record.silence_window_end.isoformat()}"
            ),
        })
        transition = apply_silence_window_expired(state, ticket_id, now=now)
        if transition is None:
            continue
        if transition.queue_source == "silence_waker":
            logger.info(
                "silence_expired_dispatch",
                ticket_id=ticket_id, silence_end=transition.silence_end.isoformat(),
                verdict=transition.verdict,
            )
        else:
            logger.info(
                "silence_expired_ask",
                ticket_id=ticket_id, silence_end=transition.silence_end.isoformat(),
                verdict=transition.verdict,
            )
        advanced += 1
    return advanced


async def run_silence_waker(
    *,
    state: TicketStateTracker,
    trigger_queue: Any,
    scan_interval_seconds: int,
) -> None:
    """Infinite loop: scan_once -> sleep -> repeat. Catches exceptions per iter."""
    logger.info("silence_waker_started", interval_s=scan_interval_seconds)
    while True:
        try:
            await scan_once(state=state, trigger_queue=trigger_queue)
        except Exception as e:
            logger.error("silence_waker_error", error=str(e))
        await asyncio.sleep(scan_interval_seconds)

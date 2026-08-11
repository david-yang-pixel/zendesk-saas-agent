"""Silence-window expiry scanner."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any

from zd_agent.log import get_logger
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

        source = "silence_waker"
        message = {
            "source": source,
            "ticket_id": int(ticket_id),
            "delivery_attempt_id": record.delivery_attempt_id,
            "expected_state_revision": record.state_revision,
            "expected_silence_window_end": record.silence_window_end.isoformat(),
            "_queue_key": (
                f"silence:{ticket_id}:{source}:{record.silence_window_end.isoformat()}"
            ),
        }
        recoverable_method = getattr(type(trigger_queue), "put_recoverable", None)
        if callable(recoverable_method):
            inserted = await trigger_queue.put_recoverable(message)
        else:
            inserted = await trigger_queue.put(message)
        # asyncio.Queue.put() returns None; durable queues return a boolean.
        # Only an explicit False means the durable event was not inserted.
        if inserted is False:
            continue
        # Enqueueing is not ownership. Keep the durable state in the silence
        # window until the consumer validates this generation and either binds
        # a real AgentSession owner or successfully sends the scheduled ask.
        logger.info(
            "silence_expired_dispatch_queued",
            ticket_id=ticket_id,
            silence_end=record.silence_window_end.isoformat(),
            verdict=record.last_verdict,
            state_revision=record.state_revision,
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
    next_internal_note_reconcile_at = 0.0
    next_dead_letter_reconcile_at = 0.0
    reconcile_task: asyncio.Task[None] | None = None
    try:
        while True:
            try:
                await scan_once(state=state, trigger_queue=trigger_queue)
            except Exception as e:
                logger.error("silence_waker_error", error=str(e))
            loop_time = asyncio.get_running_loop().time()
            if loop_time >= next_internal_note_reconcile_at and (
                reconcile_task is None or reconcile_task.done()
            ):
                next_internal_note_reconcile_at = loop_time + 60.0
                reconcile_task = asyncio.create_task(
                    _reconcile_post_public_internal_notes(state)
                )
            if loop_time >= next_dead_letter_reconcile_at:
                next_dead_letter_reconcile_at = loop_time + 60.0
                await _reconcile_dead_lettered_triggers(
                    state=state,
                    trigger_queue=trigger_queue,
                )
            await asyncio.sleep(scan_interval_seconds)
    finally:
        if reconcile_task is not None and not reconcile_task.done():
            reconcile_task.cancel()
            with suppress(asyncio.CancelledError):
                await reconcile_task


async def _reconcile_post_public_internal_notes(state: TicketStateTracker) -> None:
    """Run one bounded reconciliation pass without delaying silence scans."""
    try:
        from zd_agent.integrations.zendesk_mcp import call_zendesk_tool
        from zd_agent.services.public_reply_internal_note import (
            PostPublicInternalNoteStore,
            reconcile_pending_post_public_internal_notes,
        )
        from zd_agent.services.public_reply_delivery_store import (
            PublicReplyDeliveryStore,
        )
        from zd_agent.services.public_reply_handoff_effects import (
            PostPublicHandoffStore,
            reconcile_pending_post_public_handoff_effects,
        )
        from zd_agent.services.casework_effect_store import CaseworkEffectStore
        from zd_agent.services.casework_ownership_effects import (
            reconcile_casework_ownership_effects,
        )

        note_store = PostPublicInternalNoteStore(
            state.path.parent / "post_public_internal_note_outbox.sqlite3"
        )
        handoff_store = PostPublicHandoffStore(
            state.path.parent / "post_public_handoff_outbox.sqlite3"
        )
        delivery_store = PublicReplyDeliveryStore(
            state.path.parent / "public_reply_outbox.sqlite3"
        )
        casework_store = CaseworkEffectStore(
            state.path.parent / "casework_effects.sqlite3"
        )
        if note_store.pending(limit=1):
            await asyncio.wait_for(
                reconcile_pending_post_public_internal_notes(
                    note_store=note_store,
                    zendesk_tool_caller=call_zendesk_tool,
                    delivery_store=delivery_store,
                ),
                timeout=90,
            )
        if handoff_store.pending(limit=1):
            await asyncio.wait_for(
                reconcile_pending_post_public_handoff_effects(
                    store=handoff_store,
                    delivery_store=delivery_store,
                ),
                timeout=90,
            )
        if casework_store.pending_ownership(limit=1) or state.list_by_state(
            "ownership_pending"
        ):
            await asyncio.wait_for(
                reconcile_casework_ownership_effects(
                    store=casework_store,
                    state=state,
                ),
                timeout=90,
            )
        purged = casework_store.purge_completed()
        if purged:
            logger.info("casework_effect_retention_purged", count=purged)
    except Exception as e:  # noqa: BLE001 - reconciliation stays best effort.
        logger.warning(
            "post_public_internal_note_periodic_reconcile_failed",
            error=str(e)[:200],
        )


async def _reconcile_dead_lettered_triggers(
    *,
    state: TicketStateTracker,
    trigger_queue: Any,
) -> None:
    """Periodically complete typed v2 ownership after a consumer crash."""
    try:
        from zd_agent.services.trigger_consumer import (
            reconcile_dead_lettered_triggers,
        )

        await reconcile_dead_lettered_triggers(
            queue=trigger_queue,
            state=state,
        )
    except Exception as exc:  # noqa: BLE001 - next periodic pass retries.
        logger.warning(
            "trigger_dead_letter_periodic_reconcile_failed",
            error=str(exc)[:200],
        )

"""Backfill active SaaS ticket updates that were missed by webhooks."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from zd_agent.integrations.zendesk_mcp import call_zendesk_tool
from zd_agent.log import get_logger

logger = get_logger(module="services.intake_poller")

ZendeskCallFn = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
_MAX_SEARCH_PAGES = 10


async def _search_all_active_tickets(
    *,
    query: str,
    call_fn: ZendeskCallFn,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for page in range(1, _MAX_SEARCH_PAGES + 1):
        payload = await call_fn(
            "search_tickets",
            {"query": query, "page": page, "per_page": 100},
        )
        if not payload.get("success"):
            raise RuntimeError(f"{payload.get('code')}: {payload.get('error')}")
        data = payload.get("data") or {}
        results.extend(
            item for item in (data.get("results") or []) if isinstance(item, dict)
        )
        if not data.get("next_page"):
            return results
    raise RuntimeError(f"active ticket search exceeded {_MAX_SEARCH_PAGES} pages")


async def scan_once(
    *,
    trigger_queue: Any,
    query: str,
    ticket_update_cache: dict[int, str],
    call_fn: ZendeskCallFn = call_zendesk_tool,
) -> int:
    """Search active tickets and durably enqueue each newly observed revision."""
    try:
        results = await _search_all_active_tickets(query=query, call_fn=call_fn)
    except Exception as error:  # noqa: BLE001 - one failed scan retries next cycle.
        logger.warning(
            "intake_backfill_search_failed",
            error_type=type(error).__name__,
            error=str(error)[:300],
        )
        return 0

    inserted_count = 0
    visible_ticket_ids: set[int] = set()
    for ticket in results:
        try:
            ticket_id = int(ticket.get("id"))
        except (TypeError, ValueError):
            continue
        visible_ticket_ids.add(ticket_id)
        revision = str(ticket.get("updated_at") or "unknown").strip() or "unknown"
        if ticket_update_cache.get(ticket_id) == revision:
            continue
        try:
            inserted = await trigger_queue.put(
                {
                    "source": "intake_backfill",
                    "ticket_id": ticket_id,
                    "_queue_key": f"intake_backfill:{ticket_id}:{revision}",
                }
            )
        except Exception as error:  # noqa: BLE001 - retry this ticket next scan.
            logger.warning(
                "intake_backfill_enqueue_failed",
                ticket_id=ticket_id,
                error_type=type(error).__name__,
            )
            continue
        ticket_update_cache[ticket_id] = revision
        if inserted is not False:
            inserted_count += 1

    for ticket_id in tuple(ticket_update_cache):
        if ticket_id not in visible_ticket_ids:
            ticket_update_cache.pop(ticket_id, None)
    return inserted_count


async def run_intake_poller(
    *,
    trigger_queue: Any,
    query: str,
    poll_interval_seconds: int,
) -> None:
    """Continuously reconcile active-ticket search results into the queue."""
    ticket_update_cache: dict[int, str] = {}
    logger.info(
        "intake_backfill_started",
        query=query,
        interval_s=poll_interval_seconds,
    )
    while True:
        try:
            inserted = await scan_once(
                trigger_queue=trigger_queue,
                query=query,
                ticket_update_cache=ticket_update_cache,
            )
            if inserted:
                logger.info("intake_backfill_cycle_done", enqueued=inserted)
        except Exception as error:  # noqa: BLE001 - keep the critical task alive.
            logger.exception(
                "intake_backfill_cycle_failed",
                error_type=type(error).__name__,
            )
        await asyncio.sleep(max(1, poll_interval_seconds))

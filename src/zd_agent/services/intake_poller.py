"""Backfill active SaaS ticket updates that were missed by webhooks."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from zd_agent.integrations.zendesk_mcp import call_zendesk_tool
from zd_agent.log import get_logger
from zd_agent.services.atomic_write import atomic_write_text
from zd_agent.services.event_watermark import (
    accept_after_unix,
    is_after_online_watermark,
)

logger = get_logger(module="services.intake_poller")

ZendeskCallFn = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
_MAX_SEARCH_PAGES = 10
_REVISION_CACHE_FILE = "intake_revisions.json"


@dataclass
class IntakeSearchProgress:
    """Bounded pagination state carried across intake scan cycles."""

    next_page: int = 1
    visible_ticket_ids: set[int] = field(default_factory=set)
    baseline_existing_revisions: bool = False


def _load_revision_cache(path: Path) -> dict[int, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as error:
        logger.warning(
            "intake_revision_cache_read_failed",
            error_type=type(error).__name__,
        )
        return {}
    if not isinstance(value, dict):
        return {}
    result: dict[int, str] = {}
    for key, revision in value.items():
        try:
            result[int(key)] = str(revision)
        except (TypeError, ValueError):
            continue
    return result


def _save_revision_cache(path: Path, cache: dict[int, str]) -> None:
    atomic_write_text(
        path,
        json.dumps({str(key): value for key, value in cache.items()}),
    )
    path.chmod(0o600)


async def _search_active_ticket_batch(
    *,
    query: str,
    call_fn: ZendeskCallFn,
    start_page: int = 1,
) -> tuple[list[dict[str, Any]], int | None]:
    results: list[dict[str, Any]] = []
    stop_page = start_page + _MAX_SEARCH_PAGES
    for page in range(start_page, stop_page):
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
            return results, None
    return results, stop_page


async def scan_once(
    *,
    trigger_queue: Any,
    query: str,
    ticket_update_cache: dict[int, str],
    call_fn: ZendeskCallFn = call_zendesk_tool,
    search_progress: IntakeSearchProgress | None = None,
) -> int:
    """Search active tickets and durably enqueue each newly observed revision."""
    progress = search_progress or IntakeSearchProgress()
    try:
        results, next_page = await _search_active_ticket_batch(
            query=query,
            call_fn=call_fn,
            start_page=progress.next_page,
        )
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
        if progress.baseline_existing_revisions or not is_after_online_watermark(
            ticket.get("updated_at")
        ):
            # Prime the cache without creating work for the offline interval.
            ticket_update_cache[ticket_id] = revision
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

    progress.visible_ticket_ids.update(visible_ticket_ids)
    if next_page is None:
        for ticket_id in tuple(ticket_update_cache):
            if ticket_id not in progress.visible_ticket_ids:
                ticket_update_cache.pop(ticket_id, None)
        progress.next_page = 1
        progress.visible_ticket_ids.clear()
        progress.baseline_existing_revisions = False
    else:
        progress.next_page = next_page
    return inserted_count


async def run_intake_poller(
    *,
    trigger_queue: Any,
    query: str,
    poll_interval_seconds: int,
) -> None:
    """Continuously reconcile active-ticket search results into the queue."""
    queue_path = Path(getattr(trigger_queue, "path", Path("trigger_queue.sqlite3")))
    cache_path = queue_path.parent / _REVISION_CACHE_FILE
    ticket_update_cache = _load_revision_cache(cache_path)
    search_progress = IntakeSearchProgress()
    logger.info(
        "intake_backfill_started",
        query=query,
        interval_s=poll_interval_seconds,
    )
    try:
        boundary = accept_after_unix()
        results, next_page = await _search_active_ticket_batch(
            query=query,
            call_fn=call_zendesk_tool,
        )
        for ticket in results:
            try:
                ticket_id = int(ticket.get("id"))
            except (TypeError, ValueError):
                continue
            updated_at = ticket.get("updated_at")
            revision = str(updated_at or "unknown").strip() or "unknown"
            # Baseline only revisions that provably predate this online epoch.
            # Newer revisions must flow through scan_once and be enqueued.
            if boundary <= 0 or not is_after_online_watermark(updated_at):
                ticket_update_cache[ticket_id] = revision
            if next_page is not None:
                search_progress.visible_ticket_ids.add(ticket_id)
        if next_page is not None:
            search_progress.next_page = next_page
            search_progress.baseline_existing_revisions = boundary <= 0
        _save_revision_cache(cache_path, ticket_update_cache)
        logger.info("intake_backfill_baseline_primed", tickets=len(ticket_update_cache))
    except Exception as error:  # noqa: BLE001 - next cycle remains available.
        logger.warning(
            "intake_backfill_baseline_failed",
            error_type=type(error).__name__,
        )
    while True:
        try:
            inserted = await scan_once(
                trigger_queue=trigger_queue,
                query=query,
                ticket_update_cache=ticket_update_cache,
                call_fn=call_zendesk_tool,
                search_progress=search_progress,
            )
            if inserted:
                logger.info("intake_backfill_cycle_done", enqueued=inserted)
            _save_revision_cache(cache_path, ticket_update_cache)
        except Exception as error:  # noqa: BLE001 - keep the critical task alive.
            logger.exception(
                "intake_backfill_cycle_failed",
                error_type=type(error).__name__,
            )
        await asyncio.sleep(max(1, poll_interval_seconds))

"""Mention Poller: scan recently-updated tickets for @mention internal notes,
push them to ``trigger_queue`` for direct AgentSession dispatch.

Why this exists
---------------
SaaS messaging tickets typically don't fire a Zendesk Tickets-webhook event
when a human agent adds an Internal Note that mentions ``@saas-agent``. Even
when the webhook does fire, ``run_saas_trigger_consumer``'s existing ``customer_msgs``
diff path skips it because internal notes are *not* end-user events.

So we need a separate poller that:
  1. Searches tickets updated since the last poll (status open / new)
  2. Walks recent comments, detects @mention internal notes
  3. Idempotency-guards via ``MentionTracker``
  4. Pushes ``{source: "mention", ticket_id, comment_id, hint}`` to the queue
     so ``run_saas_trigger_consumer`` dispatches the AgentSession directly.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from zd_agent.config import get_config
from zd_agent.integrations.zendesk_mcp import call_zendesk_tool
from zd_agent.log import get_logger
from zd_agent.mention.detector import configured_mention_triggers, is_any_mention
from zd_agent.mention.tracker import MentionTracker
from zd_agent.services.mention_comments import fetch_recent_ticket_comments

logger = get_logger(module="services.mention_poller")

# Zendesk search has eventual-consistency lag. Keep a generous overlap and let
# MentionTracker's comment-id idempotency prevent duplicate dispatch.
_SEARCH_LOOKBACK_SECONDS = 30 * 60


def _now_unix() -> float:
    return time.time()


async def _fetch_ticket_comments_for_mentions(ticket_id: int) -> list[dict]:
    """Fetch enough ticket comments to see recent internal @mentions.

    The shared reader starts from the newest bounded page. Fetching only page 1
    would miss the latest note once a ticket has enough history.
    """
    return list(
        await fetch_recent_ticket_comments(ticket_id, call_fn=call_zendesk_tool)
    )


async def scan_once(
    *,
    trigger_queue: asyncio.Queue,
    tracker: MentionTracker,
    last_poll_unix: float,
    ticket_update_cache: dict[int, str] | None = None,
) -> tuple[int, float]:
    """Single scan pass.

    Returns (mentions_dispatched, new_last_poll_unix).
    """
    cfg = get_config()
    triggers = configured_mention_triggers(cfg.zendesk)
    if not triggers:
        logger.warning("mention_poller_no_trigger_configured")
        return 0, last_poll_unix

    since_dt = datetime.fromtimestamp(
        max(0, last_poll_unix - _SEARCH_LOOKBACK_SECONDS),
        tz=timezone.utc,
    )
    since_iso = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    # Advance cursor on successful scans. The search query still overlaps
    # previous windows to tolerate Zendesk search-index delay.
    new_cursor = _now_unix()

    query = f"status<solved updated>{since_iso}"
    try:
        payload = await call_zendesk_tool("search_tickets", {"query": query})
        if not payload.get("success"):
            raise RuntimeError(f"{payload.get('code')}: {payload.get('error')}")
        data = payload.get("data") or {}
    except Exception as e:
        logger.warning("mention_poller_search_failed", error=str(e))
        return 0, last_poll_unix  # don't advance cursor on failure

    results = data.get("results", []) or []
    dispatched = 0
    visible_ticket_ids: set[int] = set()

    for ticket in results:
        tid = ticket.get("id")
        if not tid:
            continue
        ticket_id = int(tid)
        visible_ticket_ids.add(ticket_id)
        updated_at = str(ticket.get("updated_at") or "").strip()
        if (
            ticket_update_cache is not None
            and updated_at
            and ticket_update_cache.get(ticket_id) == updated_at
        ):
            continue

        try:
            comments = await _fetch_ticket_comments_for_mentions(ticket_id)
        except Exception as e:
            logger.warning(
                "mention_poller_comments_failed",
                ticket_id=tid,
                error=str(e),
            )
            continue

        # Walk newest → oldest, only handle one mention per ticket per cycle.
        # If one is dispatched, leave this ticket uncached so a second pending
        # mention with the same ``updated_at`` can be drained next cycle.
        dispatched_for_ticket = False
        queue_conflict = False
        for comment in comments:
            comment_id = str(comment.get("id", ""))
            if not comment_id:
                continue
            if tracker.is_processed(comment_id):
                continue
            matched_trigger = is_any_mention(comment, triggers)
            if not matched_trigger:
                continue

            body = (comment.get("body") or "")[:500]
            hint = f"[来自 @mention 的请求]\n{body}"
            inserted = await trigger_queue.put(
                {
                    "source": "mention",
                    "ticket_id": ticket_id,
                    "comment_id": comment_id,
                    "hint": hint,
                    "_queue_key": f"mention:{comment_id}",
                }
            )
            if inserted is False:
                # The signed webhook candidate already owns this durable key.
                # Leave the tracker and update cache incomplete. If that
                # candidate terminally fails, reconciliation deletes the hint
                # row and the next poll can safely claim the same key.
                queue_conflict = True
                break
            logger.info(
                "mention_poller_enqueued",
                ticket_id=tid,
                comment_id=comment_id,
                trigger=matched_trigger,
            )
            dispatched += 1
            dispatched_for_ticket = True
            break  # one ticket, one dispatch per cycle
        # Cache only after the comments were fully inspected and any durable
        # queue write succeeded.  A failed read/write must remain retryable.
        if (
            ticket_update_cache is not None
            and updated_at
            and not dispatched_for_ticket
            and not queue_conflict
        ):
            ticket_update_cache[ticket_id] = updated_at

    if ticket_update_cache is not None:
        for ticket_id in tuple(ticket_update_cache):
            if ticket_id not in visible_ticket_ids:
                ticket_update_cache.pop(ticket_id, None)

    return dispatched, new_cursor


async def run_mention_poller(
    *,
    trigger_queue: asyncio.Queue,
    tracker: MentionTracker,
    poll_interval_seconds: int,
) -> None:
    """Infinite loop: scan → sleep → repeat. Catches per-iter exceptions."""
    last_poll_unix = _now_unix()  # 启动时刻起算，忽略历史 mention
    ticket_update_cache: dict[int, str] = {}
    logger.info(
        "mention_poller_started",
        interval_s=poll_interval_seconds,
        triggers=configured_mention_triggers(get_config().zendesk),
    )
    # 启动时清理 30 天以上的旧记录
    try:
        tracker.cleanup(days=30)
    except Exception as e:
        logger.warning("mention_tracker_cleanup_failed", error=str(e))

    while True:
        try:
            n, last_poll_unix = await scan_once(
                trigger_queue=trigger_queue,
                tracker=tracker,
                last_poll_unix=last_poll_unix,
                ticket_update_cache=ticket_update_cache,
            )
            if n:
                logger.info("mention_poller_cycle_done", dispatched=n)
        except Exception as e:
            logger.error("mention_poller_error", error=str(e))
        await asyncio.sleep(poll_interval_seconds)

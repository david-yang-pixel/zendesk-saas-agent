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
from zd_agent.log import get_logger
from zd_agent.mention.detector import configured_mention_triggers, is_any_mention
from zd_agent.mention.tracker import MentionTracker

logger = get_logger(module="services.mention_poller")

# Zendesk search has eventual-consistency lag. Keep a generous overlap and let
# MentionTracker's comment-id idempotency prevent duplicate dispatch.
_SEARCH_LOOKBACK_SECONDS = 30 * 60


def _now_unix() -> float:
    return time.time()


async def scan_once(
    *,
    trigger_queue: asyncio.Queue,
    tracker: MentionTracker,
    last_poll_unix: float,
) -> tuple[int, float]:
    """Single scan pass.

    Returns (mentions_dispatched, new_last_poll_unix).
    """
    from zd.client import ZendeskClient

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
        client = ZendeskClient()
        data = await asyncio.to_thread(client.search_tickets, query)
    except Exception as e:
        logger.warning("mention_poller_search_failed", error=str(e))
        return 0, last_poll_unix  # don't advance cursor on failure

    results = data.get("results", []) or []
    dispatched = 0

    for ticket in results:
        tid = ticket.get("id")
        if not tid:
            continue

        try:
            comments_data = await asyncio.to_thread(
                client.get_ticket_comments, tid, 1, 10,
            )
        except Exception as e:
            logger.warning(
                "mention_poller_comments_failed",
                ticket_id=tid, error=str(e),
            )
            continue

        comments = comments_data.get("comments", []) or []
        # Walk newest → oldest, only handle one mention per ticket per cycle
        for comment in reversed(comments):
            comment_id = str(comment.get("id", ""))
            if not comment_id:
                continue
            if tracker.is_processed(comment_id):
                continue
            matched_trigger = is_any_mention(comment, triggers)
            if not matched_trigger:
                continue

            tracker.mark_processed(comment_id)
            body = (comment.get("body") or "")[:500]
            hint = f"[来自 @mention 的请求]\n{body}"
            await trigger_queue.put({
                "source": "mention",
                "ticket_id": int(tid),
                "comment_id": comment_id,
                "hint": hint,
            })
            logger.info(
                "mention_poller_dispatched",
                ticket_id=tid, comment_id=comment_id,
                trigger=matched_trigger,
            )
            dispatched += 1
            break  # one ticket, one dispatch per cycle

    return dispatched, new_cursor


async def run_mention_poller(
    *,
    trigger_queue: asyncio.Queue,
    tracker: MentionTracker,
    poll_interval_seconds: int,
) -> None:
    """Infinite loop: scan → sleep → repeat. Catches per-iter exceptions."""
    last_poll_unix = _now_unix()  # 启动时刻起算，忽略历史 mention
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
            )
            if n:
                logger.info("mention_poller_cycle_done", dispatched=n)
        except Exception as e:
            logger.error("mention_poller_error", error=str(e))
        await asyncio.sleep(poll_interval_seconds)

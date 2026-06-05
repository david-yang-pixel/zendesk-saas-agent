"""Customer message activity gates for trigger consumer processing."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from zd_agent.log import get_logger
from zd_agent.services.trigger_routing import (
    customer_reopen_metric,
    is_duplicate_customer_comment,
    latest_customer_message_context,
    post_agent_cooldown,
)

logger = get_logger(module="services.customer_activity")

EmitCustomerReopenFn = Callable[..., None]


@dataclass(frozen=True)
class CustomerActivityResult:
    handled: bool
    action: str
    latest: dict
    latest_cid: object
    latest_ts: datetime
    existing: Any
    now: datetime


def handle_customer_activity_gate(
    *,
    ticket_id: int,
    customer_msgs: list[dict],
    state: Any,
    log_path: Path,
    source: str,
    cooldown_seconds: int,
    now: datetime | None = None,
    emit_customer_reopen_fn: EmitCustomerReopenFn | None = None,
) -> CustomerActivityResult:
    """Handle duplicate, reopen metric, and post-agent cooldown gates."""
    from zd_agent.observability import metrics
    from zd_agent.trigger import decision_log

    now = now or datetime.now(timezone.utc)
    latest, latest_cid, latest_ts = latest_customer_message_context(
        customer_msgs,
        now=now,
    )
    existing = state.get(ticket_id)

    if is_duplicate_customer_comment(existing, latest_cid):
        logger.info(
            "trigger_skip_duplicate_comment",
            ticket_id=ticket_id,
            comment_id=latest_cid,
            current_state=existing.state,
        )
        return CustomerActivityResult(
            handled=True,
            action="duplicate_customer_comment",
            latest=latest,
            latest_cid=latest_cid,
            latest_ts=latest_ts,
            existing=existing,
            now=now,
        )

    reopen_metric = customer_reopen_metric(
        existing=existing,
        source=source,
        now=now,
    )
    if reopen_metric is not None:
        emit_customer_reopen_fn = emit_customer_reopen_fn or metrics.emit_customer_reopen
        emit_customer_reopen_fn(
            ticket_id=ticket_id,
            prev_state=reopen_metric.prev_state,
            source=reopen_metric.source,
            hours_since_processed=reopen_metric.hours_since_processed,
        )

    cooldown = post_agent_cooldown(
        existing=existing,
        cooldown_seconds=cooldown_seconds,
        now=now,
    )
    if cooldown.should_skip:
        existing.last_customer_comment_id = latest_cid
        state.set(ticket_id, existing)
        logger.info(
            "trigger_skip_post_agent_cooldown",
            ticket_id=ticket_id,
            comment_id=latest_cid,
            elapsed_s=round(cooldown.elapsed_seconds or 0, 1),
            cooldown_s=cooldown.cooldown_seconds,
        )
        decision_log.log_decision(
            log_path=log_path,
            ticket_id=ticket_id,
            verdict="post_agent_cooldown",
            action="skip",
        )
        return CustomerActivityResult(
            handled=True,
            action="post_agent_cooldown",
            latest=latest,
            latest_cid=latest_cid,
            latest_ts=latest_ts,
            existing=existing,
            now=now,
        )

    return CustomerActivityResult(
        handled=False,
        action="continue",
        latest=latest,
        latest_cid=latest_cid,
        latest_ts=latest_ts,
        existing=existing,
        now=now,
    )

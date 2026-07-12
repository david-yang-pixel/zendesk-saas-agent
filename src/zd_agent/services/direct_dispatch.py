"""Direct dispatch handling for trigger sources that bypass verdict checks."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zd_agent.log import get_logger
from zd_agent.services.trigger_routing import direct_trigger_cooldown
from zd_agent.trigger.decision_log import log_decision
from zd_agent.trigger.state_store import TicketState

logger = get_logger(module="services.direct_dispatch")

DIRECT_DISPATCH_SOURCES = {"silence_waker", "mention"}


@dataclass(frozen=True)
class DirectDispatchResult:
    handled: bool
    dispatched: bool = False
    action: str = "not_handled"
    cooldown_seconds: int = 0
    elapsed_seconds: float | None = None


async def handle_direct_dispatch_source(
    *,
    ticket_id: int,
    msg: dict,
    manager: Any,
    state: Any,
    log_path: Path,
    cfg: Any,
    now: datetime | None = None,
) -> DirectDispatchResult:
    """Handle mention/silence_waker sources that directly dispatch sessions."""
    source = msg.get("source", "unknown")
    if source not in DIRECT_DISPATCH_SOURCES:
        return DirectDispatchResult(handled=False)

    now = now or datetime.now(timezone.utc)
    hint = msg.get("hint") or ""
    existing_state = state.get(ticket_id)
    cooldown = direct_trigger_cooldown(
        existing=existing_state,
        source=source,
        hint=hint,
        full_cooldown_seconds=cfg.trigger_decider.post_agent_cooldown_seconds,
        now=now,
    )
    if cooldown.should_skip:
        logger.info(
            "trigger_skip_post_agent_cooldown_direct",
            ticket_id=ticket_id,
            source=source,
            elapsed_s=round(cooldown.elapsed_seconds or 0, 1),
            cooldown_s=cooldown.cooldown_seconds,
            had_hint=bool(hint),
        )
        log_decision(
            log_path=log_path,
            ticket_id=ticket_id,
            verdict=f"post_agent_cooldown_{source}",
            action="skip",
        )
        return DirectDispatchResult(
            handled=True,
            action="skip_post_agent_cooldown",
            cooldown_seconds=cooldown.cooldown_seconds,
            elapsed_seconds=cooldown.elapsed_seconds,
        )

    processing_state = existing_state or TicketState(
        state="processing",
        first_seen=now,
    )
    processing_state.state = "processing"
    state.set(ticket_id, processing_state)

    logger.info(
        "trigger_dispatch_direct",
        ticket_id=ticket_id,
        source=source,
        has_hint=bool(hint),
    )
    await manager.dispatch(
        ticket_id,
        dry_run=False,
        trigger_type=f"webhook-{source}",
        hint=hint,
        delivery_attempt_id=str(
            msg.get("_queue_key") or f"{source}:{ticket_id}"
        ),
    )
    return DirectDispatchResult(handled=True, dispatched=True, action="dispatch")

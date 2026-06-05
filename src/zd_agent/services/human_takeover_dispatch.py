"""Human takeover dispatch orchestration for trigger consumer messages."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from zd_agent.log import get_logger
from zd_agent.services.human_takeover import detect_human_takeover
from zd_agent.services.trigger_routing import latest_customer_message_context
from zd_agent.trigger.decision_log import log_decision

logger = get_logger(module="services.human_takeover_dispatch")

DetectHumanTakeoverFn = Callable[..., Awaitable[tuple[bool, str]]]
EscalateFn = Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class HumanTakeoverDispatchResult:
    handled: bool
    reason: str = ""


async def handle_human_takeover_dispatch(
    *,
    ticket_id: int,
    ticket_info: dict,
    customer_msgs: list[dict],
    state: Any,
    log_path: Path,
    source: str,
    agent_user_id: int,
    detect_human_takeover_fn: DetectHumanTakeoverFn | None = None,
    escalate_fn: EscalateFn,
    now: datetime | None = None,
) -> HumanTakeoverDispatchResult:
    """Detect human takeover and escalate the ticket when one is found."""
    detect_human_takeover_fn = detect_human_takeover_fn or detect_human_takeover
    takeover, takeover_reason = await detect_human_takeover_fn(
        ticket_id=ticket_id,
        ticket_info=ticket_info,
        agent_user_id=agent_user_id,
    )
    if not takeover:
        return HumanTakeoverDispatchResult(handled=False)

    logger.info(
        "trigger_human_takeover_detected",
        ticket_id=ticket_id,
        reason=takeover_reason,
    )
    log_decision(
        log_path=log_path,
        ticket_id=ticket_id,
        verdict="human_takeover",
        action="escalate",
        extra={"reason": takeover_reason},
    )

    now = now or datetime.now(timezone.utc)
    existing = state.get(ticket_id)
    _, latest_cid, latest_ts = latest_customer_message_context(
        customer_msgs,
        now=now,
    )
    await escalate_fn(
        ticket_id=ticket_id,
        reason="human_takeover_detected",
        existing=existing,
        state=state,
        log_path=log_path,
        source=source,
        now=now,
        verdict_str="human_takeover",
        latest_cid=latest_cid,
        latest_ts=latest_ts,
        completeness=0,
        customer_msgs=customer_msgs,
        customer_phrase="",
        extra_log={"takeover_reason": takeover_reason},
    )
    return HumanTakeoverDispatchResult(handled=True, reason=takeover_reason)

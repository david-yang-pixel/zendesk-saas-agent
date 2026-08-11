"""Human takeover dispatch orchestration for trigger consumer messages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from zd_agent.log import get_logger
from zd_agent.services.human_takeover import detect_human_takeover
from zd_agent.services.trigger_routing import latest_customer_message_context

logger = get_logger(module="services.human_takeover_dispatch")

DetectHumanTakeoverFn = Callable[..., Awaitable[tuple[bool, str]]]
HumanTakeoverFn = Callable[..., Awaitable[None]]


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
    human_takeover_fn: HumanTakeoverFn,
    now: datetime | None = None,
) -> HumanTakeoverDispatchResult:
    """Detect an existing human owner and stop automation without replying."""
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
    now = now or datetime.now(timezone.utc)
    existing = state.get(ticket_id)
    _, latest_cid, latest_ts = latest_customer_message_context(
        customer_msgs,
        now=now,
    )
    await human_takeover_fn(
        ticket_id=ticket_id,
        existing=existing,
        state=state,
        log_path=log_path,
        source=source,
        now=now,
        latest_cid=latest_cid,
        latest_ts=latest_ts,
        detection_reason=takeover_reason,
    )
    return HumanTakeoverDispatchResult(handled=True, reason=takeover_reason)

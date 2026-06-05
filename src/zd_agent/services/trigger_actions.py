"""Trigger side-effect adapters used by the SaaS entrypoint."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from zd_agent.services.ask_notifications import send_ask_template_best_effort
from zd_agent.services.escalation import set_escalated_to_human
from zd_agent.services.silence_expiry import handle_silence_expired_ask
from zd_agent.services.verdict_dispatch import handle_post_verdict_dispatch


async def handle_silence_expired_ask_action(
    *,
    ticket_id: int,
    state: Any,
    log_path: Path,
    nudge_message_fn: Any,
) -> None:
    """Delegate silence-expired ask handling to the orchestration service."""
    await handle_silence_expired_ask(
        ticket_id=ticket_id,
        state=state,
        log_path=log_path,
        nudge_message_fn=nudge_message_fn,
    )


async def send_ask_template_action(
    *,
    ticket_id: int,
    verdict_kind: str,
    customer_msgs: list[dict],
    ticket_info: dict,
    ask_count: int,
    source: str,
) -> bool:
    """Delegate public ask template sending to the notification service."""
    return await send_ask_template_best_effort(
        ticket_id=ticket_id,
        verdict_kind=verdict_kind,
        customer_msgs=customer_msgs,
        ask_count=ask_count,
        source=source,
    )


async def set_escalated_to_human_action(
    *,
    ticket_id: int,
    reason: str,
    existing: Any,
    state: Any,
    log_path: Path,
    source: str,
    now: Any,
    verdict_str: str,
    latest_cid: Any,
    latest_ts: Any,
    completeness: int,
    customer_msgs: list[dict] | None = None,
    customer_phrase: str = "",
    agent_last_reply: str = "",
    extra_log: dict | None = None,
) -> None:
    """Delegate escalation persistence and side effects to the service layer."""
    await set_escalated_to_human(
        ticket_id=ticket_id,
        reason=reason,
        existing=existing,
        state=state,
        log_path=log_path,
        source=source,
        now=now,
        verdict_str=verdict_str,
        latest_cid=latest_cid,
        latest_ts=latest_ts,
        completeness=completeness,
        customer_msgs=customer_msgs,
        customer_phrase=customer_phrase,
        agent_last_reply=agent_last_reply,
        extra_log=extra_log,
    )


async def post_verdict_dispatch_action(
    *,
    ticket_id: int,
    verdict: Any,
    customer_msgs: list[dict],
    ticket_info: dict,
    latest_cid: Any,
    latest_ts: Any,
    existing: Any,
    state: Any,
    log_path: Path,
    source: str,
    now: Any,
    cfg: Any,
    escalate_fn: Any = set_escalated_to_human_action,
    send_ask_fn: Any = send_ask_template_action,
) -> None:
    """Delegate verdict dispatch with default trigger action side effects."""
    await handle_post_verdict_dispatch(
        ticket_id=ticket_id,
        verdict=verdict,
        customer_msgs=customer_msgs,
        ticket_info=ticket_info,
        latest_cid=latest_cid,
        latest_ts=latest_ts,
        existing=existing,
        state=state,
        log_path=log_path,
        source=source,
        now=now,
        cfg=cfg,
        escalate_fn=escalate_fn,
        send_ask_fn=send_ask_fn,
    )

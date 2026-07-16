"""Single-message orchestration for the trigger consumer."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from zd_agent.log import get_logger
from zd_agent.services.customer_activity import handle_customer_activity_gate
from zd_agent.services.direct_dispatch import handle_direct_dispatch_source
from zd_agent.services.human_takeover_dispatch import handle_human_takeover_dispatch
from zd_agent.services.silence_expiry import handle_silence_expired_ask
from zd_agent.services.trigger_gates import handle_trigger_entry_gate
from zd_agent.services.trigger_errors import TriggerDeferredError
from zd_agent.services.verdict_classification import classify_ticket_messages

logger = get_logger(module="services.trigger_message_handler")

FetchMessagesFn = Callable[[int], Awaitable[tuple[list[dict], dict | None]]]
SilenceExpiredAskFn = Callable[..., Awaitable[Any]]
PostVerdictDispatchFn = Callable[..., Awaitable[Any]]
EscalateFn = Callable[..., Awaitable[Any]]
ClassifyTicketMessagesFn = Callable[..., Awaitable[Any]]
DetectHumanTakeoverFn = Callable[..., Awaitable[tuple[bool, str]]]


@dataclass(frozen=True)
class TriggerMessageResult:
    handled: bool
    action: str
    ticket_id: int
    source: str


async def handle_trigger_message(
    *,
    msg: dict,
    manager: Any,
    state: Any,
    cfg: Any,
    env: Any,
    log_path: Path,
    silence_expired_ask_fn: SilenceExpiredAskFn | None = None,
    fetch_customer_messages_fn: FetchMessagesFn | None = None,
    post_verdict_dispatch_fn: PostVerdictDispatchFn | None = None,
    escalate_fn: EscalateFn | None = None,
    classify_ticket_messages_fn: ClassifyTicketMessagesFn | None = None,
    detect_human_takeover_fn: DetectHumanTakeoverFn | None = None,
) -> TriggerMessageResult:
    """Handle one trigger queue message and return the terminal action."""
    from zd_agent.services import ticket_messages
    from zd_agent.trigger import decision_log
    from zd_agent.trigger.nudge import nudge_message

    ticket_id = int(msg["ticket_id"])
    source = msg.get("source", "unknown")

    entry_gate = handle_trigger_entry_gate(
        ticket_id=ticket_id,
        source=source,
        state=state,
        log_path=log_path,
        cfg=cfg,
    )
    if entry_gate.handled:
        if entry_gate.action == "paused":
            raise TriggerDeferredError("paused")
        return TriggerMessageResult(True, entry_gate.action, ticket_id, source)

    if source == "mention":
        direct_dispatch = await handle_direct_dispatch_source(
            ticket_id=ticket_id,
            msg=msg,
            manager=manager,
            state=state,
            log_path=log_path,
            cfg=cfg,
        )
        if direct_dispatch.handled:
            return TriggerMessageResult(
                True,
                "direct_dispatch",
                ticket_id,
                source,
            )

    if source == "silence_expired_ask":
        silence_expired_ask_fn = silence_expired_ask_fn or handle_silence_expired_ask
        await silence_expired_ask_fn(
            ticket_id=ticket_id,
            state=state,
            log_path=log_path,
            nudge_message_fn=nudge_message,
            delivery_attempt_id=str(
                msg.get("_queue_key") or f"silence_expired_ask:{ticket_id}"
            ),
        )
        return TriggerMessageResult(True, "silence_expired_ask", ticket_id, source)

    fetch_customer_messages_fn = (
        fetch_customer_messages_fn or ticket_messages._fetch_customer_messages
    )
    customer_msgs, ticket_info = await fetch_customer_messages_fn(ticket_id)
    if ticket_info is None:
        raise TriggerDeferredError(
            "ticket_not_materialized",
            delay_seconds=30.0,
        )

    if not customer_msgs:
        logger.info("trigger_skip_no_customer_msg", ticket_id=ticket_id)
        decision_log.log_decision(
            log_path=log_path,
            ticket_id=ticket_id,
            verdict="no_customer_message",
            action="skip",
        )
        return TriggerMessageResult(True, "no_customer_message", ticket_id, source)

    if escalate_fn is None:
        raise RuntimeError("escalate_fn is required for trigger message handling")
    takeover_dispatch = await handle_human_takeover_dispatch(
        ticket_id=ticket_id,
        ticket_info=ticket_info,
        customer_msgs=customer_msgs,
        state=state,
        log_path=log_path,
        source=source,
        agent_user_id=cfg.zendesk.agent_user_id,
        detect_human_takeover_fn=detect_human_takeover_fn,
        escalate_fn=escalate_fn,
    )
    if takeover_dispatch.handled:
        return TriggerMessageResult(True, "human_takeover", ticket_id, source)

    from zd_agent.services.ticket_message_parsing import (
        conversation_has_pending_form,
    )

    conversation_events = ticket_info.get("_zd_agent_conversation_events") or []
    if conversation_has_pending_form(conversation_events):
        from datetime import datetime, timezone

        from zd_agent.services.trigger_routing import latest_customer_message_context
        from zd_agent.services.trigger_state_machine import apply_waiting_for_form

        now = datetime.now(timezone.utc)
        _, latest_cid, latest_ts = latest_customer_message_context(
            customer_msgs,
            now=now,
        )
        apply_waiting_for_form(
            state,
            ticket_id,
            existing=state.get(ticket_id),
            latest_cid=str(latest_cid or "") or None,
            latest_ts=latest_ts,
            now=now,
        )
        logger.info(
            "trigger_waiting_for_answer_bot_form",
            ticket_id=ticket_id,
            source=source,
            last_customer_comment_id=latest_cid,
        )
        return TriggerMessageResult(True, "waiting_for_form", ticket_id, source)

    if source == "silence_waker":
        direct_dispatch = await handle_direct_dispatch_source(
            ticket_id=ticket_id,
            msg=msg,
            manager=manager,
            state=state,
            log_path=log_path,
            cfg=cfg,
        )
        if direct_dispatch.handled:
            return TriggerMessageResult(
                True,
                "direct_dispatch",
                ticket_id,
                source,
            )

    customer_activity = handle_customer_activity_gate(
        ticket_id=ticket_id,
        customer_msgs=customer_msgs,
        state=state,
        log_path=log_path,
        source=source,
        cooldown_seconds=cfg.trigger_decider.post_agent_cooldown_seconds,
    )
    if customer_activity.handled:
        if customer_activity.action == "post_agent_cooldown":
            raise TriggerDeferredError(
                "post_agent_cooldown",
                delay_seconds=customer_activity.defer_seconds,
            )
        return TriggerMessageResult(True, customer_activity.action, ticket_id, source)

    classify_ticket_messages_fn = classify_ticket_messages_fn or classify_ticket_messages
    classification = await classify_ticket_messages_fn(
        ticket_id=ticket_id,
        customer_msgs=customer_msgs,
        model=cfg.trigger_decider.llm_model,
        api_key=env.openai_api_key,
        base_url=cfg.llm.base_url,
        enable_vision_input=cfg.trigger_decider.enable_vision_input,
    )

    if post_verdict_dispatch_fn is None:
        raise RuntimeError("post_verdict_dispatch_fn is required")
    await post_verdict_dispatch_fn(
        ticket_id=ticket_id,
        verdict=classification.verdict,
        customer_msgs=customer_msgs,
        ticket_info=ticket_info,
        latest_cid=customer_activity.latest_cid,
        latest_ts=customer_activity.latest_ts,
        existing=customer_activity.existing,
        state=state,
        log_path=log_path,
        source=source,
        now=customer_activity.now,
        cfg=cfg,
        conversation_id=msg.get("conversation_id") or None,
        messaging_app_id=msg.get("messaging_app_id") or None,
        delivery_attempt_id=str(
            msg.get("_queue_key")
            or f"{source}:{ticket_id}:{customer_activity.latest_cid or customer_activity.latest_ts}"
        ),
    )
    return TriggerMessageResult(True, "verdict_dispatched", ticket_id, source)

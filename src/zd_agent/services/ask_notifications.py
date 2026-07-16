"""Best-effort public ask template reply helper."""
from __future__ import annotations

from typing import overload

from zd_agent.log import get_logger
from zd_agent.services.ticket_messages import extract_form_data_from_msgs

logger = get_logger(module="services.ask_notifications")


def message_snapshot(customer_msgs: list[dict]) -> tuple[int, str | None]:
    msgs = customer_msgs or []
    if not msgs:
        return 0, None
    last = msgs[-1] or {}
    raw_id = (
        last.get("id")
        or last.get("comment_id")
        or last.get("message_id")
        or last.get("created_at")
        or last.get("timestamp")
    )
    return len(msgs), str(raw_id) if raw_id else None


@overload
async def send_ask_template_best_effort(
    *,
    ticket_id: int,
    verdict_kind: str,
    customer_msgs: list[dict],
    ask_count: int,
    source: str,
    conversation_id: str | None = None,
    messaging_app_id: str | None = None,
    delivery_attempt_id: str = "",
    return_delivery_result: bool = False,
) -> bool: ...


@overload
async def send_ask_template_best_effort(
    *,
    ticket_id: int,
    verdict_kind: str,
    customer_msgs: list[dict],
    ask_count: int,
    source: str,
    conversation_id: str | None = None,
    messaging_app_id: str | None = None,
    delivery_attempt_id: str = "",
    return_delivery_result: bool,
) -> object: ...


async def send_ask_template_best_effort(
    *,
    ticket_id: int,
    verdict_kind: str,
    customer_msgs: list[dict],
    ask_count: int,
    source: str,
    conversation_id: str | None = None,
    messaging_app_id: str | None = None,
    delivery_attempt_id: str = "",
    return_delivery_result: bool = False,
) -> object:
    """Build and send a public ask reply without mutating trigger state."""
    from zd_agent.config import EnvSettings, get_config
    from zd_agent.prompts.public_reply.templates import build_ask_body
    from zd_agent.tools.zendesk_public_reply import send_public_reply_with_freshness

    cfg = get_config()
    env = EnvSettings()

    form = extract_form_data_from_msgs(customer_msgs or [])
    customer_name = (form.get("name", "") or "").strip()
    customer_bodies = [m.get("body", "") for m in (customer_msgs or [])]
    snapshot_count, snapshot_last_id = message_snapshot(customer_msgs or [])

    try:
        body, template_name = await build_ask_body(
            verdict_kind=verdict_kind,
            ask_count=ask_count,
            customer_name=customer_name,
            customer_messages=customer_bodies,
            model=cfg.trigger_decider.llm_model,
            api_key=env.openai_api_key,
            reasoning_effort=cfg.trigger_decider.reasoning_effort,
            base_url=cfg.llm.base_url,
        )
    except Exception as e:
        logger.error(
            "ask_template_build_failed",
            ticket_id=ticket_id,
            verdict_kind=verdict_kind,
            error=str(e)[:200],
            exc_info=True,
        )
        return False

    logger.info(
        "ask_template_built",
        ticket_id=ticket_id,
        verdict_kind=verdict_kind,
        ask_count=ask_count,
        template=template_name,
        customer_name=customer_name or "(none)",
        body_len=len(body),
        source=source,
    )

    result = await send_public_reply_with_freshness(
        ticket_id=ticket_id,
        body=body,
        reply_type="ask",
        conversation_id=conversation_id,
        messaging_app_id=messaging_app_id,
        snapshot_customer_message_count=snapshot_count,
        snapshot_customer_last_message_id=snapshot_last_id,
        delivery_attempt_id=delivery_attempt_id,
    )
    if not result.success:
        logger.warning(
            "ask_template_send_failed",
            ticket_id=ticket_id,
            template=template_name,
            error=result.message[:200],
            source=source,
        )
        return result if return_delivery_result else False

    logger.info(
        "ask_template_sent",
        ticket_id=ticket_id,
        template=template_name,
        ask_count_now=ask_count + 1,
        source=source,
    )
    return result if return_delivery_result else True


async def send_closed_template_best_effort(
    *,
    ticket_id: int,
    customer_msgs: list[dict],
    source: str,
    conversation_id: str | None = None,
    messaging_app_id: str | None = None,
    delivery_attempt_id: str = "",
    return_delivery_result: bool = False,
) -> object:
    """Send the deterministic customer-safe close acknowledgement.

    This uses the same public-reply delivery path as every other reply, so a
    successful send also creates the durable internal-analysis follow-up.
    Trigger state is deliberately left to ``verdict_dispatch``: a failed
    delivery must not silently mark a ticket as processed.
    """
    from zd_agent.prompts.public_reply.templates import closed_template
    from zd_agent.tools.zendesk_public_reply import send_public_reply_with_freshness

    form = extract_form_data_from_msgs(customer_msgs or [])
    customer_name = (form.get("name", "") or "").strip()
    snapshot_count, snapshot_last_id = message_snapshot(customer_msgs or [])
    body = closed_template(customer_name=customer_name)

    result = await send_public_reply_with_freshness(
        ticket_id=ticket_id,
        body=body,
        reply_type="closed",
        conversation_id=conversation_id,
        messaging_app_id=messaging_app_id,
        snapshot_customer_message_count=snapshot_count,
        snapshot_customer_last_message_id=snapshot_last_id,
        delivery_attempt_id=delivery_attempt_id,
    )
    if not result.success:
        logger.warning(
            "closed_template_send_failed",
            ticket_id=ticket_id,
            error=result.message[:200],
            source=source,
        )
        return result if return_delivery_result else False

    logger.info(
        "closed_template_sent",
        ticket_id=ticket_id,
        source=source,
    )
    return result if return_delivery_result else True

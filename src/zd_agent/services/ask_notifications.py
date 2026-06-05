"""Best-effort public ask template reply helper."""
from __future__ import annotations

from zd_agent.log import get_logger
from zd_agent.services.ticket_messages import extract_form_data_from_msgs

logger = get_logger(module="services.ask_notifications")


async def send_ask_template_best_effort(
    *,
    ticket_id: int,
    verdict_kind: str,
    customer_msgs: list[dict],
    ask_count: int,
    source: str,
) -> bool:
    """Build and send a public ask reply without mutating trigger state."""
    from zd_agent.config import EnvSettings, get_config
    from zd_agent.prompts.public_reply.templates import build_ask_body
    from zd_agent.tools.zendesk_public_reply import send_public_reply

    cfg = get_config()
    env = EnvSettings()

    form = extract_form_data_from_msgs(customer_msgs or [])
    customer_name = (form.get("name", "") or "").strip()
    customer_bodies = [m.get("body", "") for m in (customer_msgs or [])]

    try:
        body, template_name = await build_ask_body(
            verdict_kind=verdict_kind,
            ask_count=ask_count,
            customer_name=customer_name,
            customer_messages=customer_bodies,
            model=cfg.trigger_decider.llm_model,
            api_key=env.openai_api_key,
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

    success, msg = await send_public_reply(
        ticket_id=ticket_id,
        body=body,
        reply_type="ask",
    )
    if not success:
        logger.warning(
            "ask_template_send_failed",
            ticket_id=ticket_id,
            template=template_name,
            error=msg[:200],
            source=source,
        )
        return False

    logger.info(
        "ask_template_sent",
        ticket_id=ticket_id,
        template=template_name,
        ask_count_now=ask_count + 1,
        source=source,
    )
    return True

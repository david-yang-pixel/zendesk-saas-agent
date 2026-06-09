"""Route registration for the SaaS aiohttp app."""
from __future__ import annotations

from typing import Any

from aiohttp import web

from zd_agent.config import EnvSettings
from zd_agent.log import get_logger
from zd_agent.runtime.pre_send_check import pre_send_check_or_abort
from zd_agent.services.app_context import app_config
from zd_agent.services.ticket_messages import _fetch_customer_messages
from zd_agent.services.zendesk_messaging_webhook import (
    handle_zendesk_messaging_webhook,
)
from zd_agent.services.zendesk_webhook import handle_zendesk_webhook

logger = get_logger(module="services.saas_routes")


async def handle_healthz(request: web.Request) -> web.Response:
    return web.Response(status=200, text="ok")


async def handle_pre_send_freshness(request: web.Request) -> web.Response:
    """Internal callback used by browser relay immediately before clicking send."""
    cfg = app_config(request.app)
    expected_token = (cfg.public_reply.browser_relay_api_key or "").strip()
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
    if not expected_token or token != expected_token:
        return web.json_response({"ok": False, "status": "unauthorized"}, status=401)

    try:
        payload = await request.json()
        ticket_id = int(payload.get("ticket_id"))
        snapshot_count = int(payload.get("snapshot_customer_message_count"))
        snapshot_last_id = payload.get("snapshot_customer_last_message_id") or None
    except Exception:
        return web.json_response({"ok": False, "status": "bad_request"}, status=400)

    async def _fetch() -> list[dict]:
        msgs, _ticket = await _fetch_customer_messages(ticket_id)
        return msgs or []

    env = EnvSettings()
    check = await pre_send_check_or_abort(
        ticket_id=ticket_id,
        snapshot_count=snapshot_count,
        snapshot_last_id=snapshot_last_id,
        fetch_messages_fn=_fetch,
        model=cfg.trigger_decider.llm_model,
        api_key=env.openai_api_key,
        skip_llm=True,
    )
    status = "fresh" if check.continue_send else "stale_context"
    logger.info(
        "pre_send_freshness_checked",
        ticket_id=ticket_id,
        status=status,
        reason=check.reason,
        new_messages_count=check.new_messages_count,
    )
    return web.json_response({
        "ok": True,
        "status": status,
        "continue_send": check.continue_send,
        "reason": check.reason,
        "new_messages_count": check.new_messages_count,
    })


def register_saas_routes(*, app: web.Application, cfg: Any) -> None:
    """Register all HTTP routes exposed by the SaaS service."""
    app.router.add_post(cfg.webhook.zendesk_path, handle_zendesk_webhook)
    app.router.add_post(
        cfg.webhook.zendesk_messaging_path,
        handle_zendesk_messaging_webhook,
    )
    app.router.add_post("/internal/pre-send-freshness", handle_pre_send_freshness)
    app.router.add_get("/healthz", handle_healthz)

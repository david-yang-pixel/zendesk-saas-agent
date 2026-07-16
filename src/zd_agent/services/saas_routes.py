"""Route registration for the SaaS aiohttp app."""
from __future__ import annotations

import hmac
from typing import Any
from urllib.parse import urlparse

from aiohttp import web

from zd_agent.config import EnvSettings
from zd_agent.log import get_logger
from zd_agent.services.agent_management import (
    build_agent_config_manifest,
    build_agent_tools_manifest,
    list_agent_models,
    update_agent_config,
)
from zd_agent.services.app_context import app_config
from zd_agent.services.zendesk_messaging_webhook import (
    handle_zendesk_messaging_webhook,
)
from zd_agent.services.zendesk_webhook import handle_zendesk_webhook

logger = get_logger(module="services.saas_routes")

PUBLIC_PRE_SEND_FRESHNESS_PATH = "/webhook/relay-pre-send-freshness"


async def handle_healthz(request: web.Request) -> web.Response:
    return web.Response(status=200, text="ok")


def _bearer_token(request: web.Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return ""
    return auth.removeprefix("Bearer ").strip()


def _management_auth_ok(request: web.Request) -> bool:
    expected = EnvSettings().hc_client_api_key.strip()
    token = _bearer_token(request)
    return bool(expected and token and hmac.compare_digest(expected, token))


def _require_management_auth(request: web.Request) -> web.Response | None:
    if _management_auth_ok(request):
        return None
    return web.json_response({"ok": False, "error": "unauthorized"}, status=401)


async def handle_agent_config(request: web.Request) -> web.Response:
    unauthorized = _require_management_auth(request)
    if unauthorized is not None:
        return unauthorized
    return web.json_response(
        build_agent_config_manifest(app_config(request.app), env=EnvSettings())
    )


async def handle_update_agent_config(request: web.Request) -> web.Response:
    unauthorized = _require_management_auth(request)
    if unauthorized is not None:
        return unauthorized
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad_request"}, status=400)
    if not isinstance(payload, dict):
        return web.json_response({"ok": False, "error": "bad_request"}, status=400)
    try:
        manifest = update_agent_config(
            app_config(request.app),
            payload,
            env=EnvSettings(),
        )
    except ValueError as exc:
        return web.json_response(
            {"ok": False, "error": str(exc)},
            status=400,
        )
    return web.json_response(manifest)


async def handle_agent_models(request: web.Request) -> web.Response:
    unauthorized = _require_management_auth(request)
    if unauthorized is not None:
        return unauthorized
    if request.query.get("base_url"):
        return web.json_response(
            {
                "ok": False,
                "error": "request-level base_url overrides are not allowed",
            },
            status=400,
        )
    return web.json_response(
        await list_agent_models(
            app_config(request.app),
            target=request.query.get("target"),
            api_key=request.headers.get("X-Agent-Model-Api-Key"),
            env=EnvSettings(),
        )
    )


async def handle_agent_tools(request: web.Request) -> web.Response:
    unauthorized = _require_management_auth(request)
    if unauthorized is not None:
        return unauthorized
    return web.json_response(build_agent_tools_manifest())


async def handle_pre_send_freshness(request: web.Request) -> web.Response:
    """Internal callback used by browser relay immediately before clicking send."""
    cfg = app_config(request.app)
    expected_token = (cfg.public_reply.browser_relay_api_key or "").strip()
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
    if (
        not expected_token
        or not token
        or not hmac.compare_digest(expected_token, token)
    ):
        return web.json_response({"ok": False, "status": "unauthorized"}, status=401)

    try:
        payload = await request.json()
        raw_ticket_id = payload.get("ticket_id")
        ticket_id = int(raw_ticket_id)
        raw_snapshot_count = payload.get("snapshot_customer_message_count")
        snapshot_count = (
            None
            if raw_snapshot_count is None
            else int(raw_snapshot_count)
        )
        raw_snapshot_last_id = payload.get("snapshot_customer_last_message_id")
        if (
            ticket_id <= 0
            or isinstance(raw_ticket_id, bool)
            or isinstance(raw_snapshot_count, bool)
            or (snapshot_count is not None and snapshot_count < 0)
            or (
                raw_snapshot_last_id is not None
                and not isinstance(raw_snapshot_last_id, str)
            )
            or len(raw_snapshot_last_id or "") > 512
            or (snapshot_count is None and raw_snapshot_last_id)
        ):
            raise ValueError("invalid freshness snapshot")
        snapshot_last_id = raw_snapshot_last_id or None
    except Exception:
        return web.json_response({"ok": False, "status": "bad_request"}, status=400)

    # This is the last check before the relay clicks Zendesk Send. Use the
    # complete readiness gate rather than a message-only comparison: a new
    # Answer Bot blocking form is a system event and otherwise remains unseen.
    from zd_agent.tools.zendesk_public_reply import _check_public_reply_readiness

    check = await _check_public_reply_readiness(
        ticket_id=ticket_id,
        snapshot_count=snapshot_count,
        snapshot_last_id=snapshot_last_id,
        skip_llm=True,
        cfg=cfg,
    )
    # The caller cannot persist a cursor learned only by this remote final
    # callback.  Do not let a harmless late "thanks" through here: aborting
    # forces the owning path to fetch it itself and durably advance its cursor.
    # This is intentionally stricter than the initial local freshness gate.
    if check.continue_send and check.new_messages_count > 0:
        return web.json_response({
            "ok": True,
            "status": "stale_context",
            "continue_send": False,
            "reason": "new_customer_message_at_relay",
            "new_messages_count": check.new_messages_count,
        })
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
    app.router.add_get("/agent/config", handle_agent_config)
    app.router.add_put("/agent/config", handle_update_agent_config)
    app.router.add_get("/agent/models", handle_agent_models)
    app.router.add_get("/agent/tools", handle_agent_tools)
    app.router.add_post("/internal/pre-send-freshness", handle_pre_send_freshness)
    # Stable production route: never let a missing/overridden config value
    # silently remove the relay's final Send gate. The handler still requires
    # the browser-relay Bearer token for every request.
    app.router.add_post(
        PUBLIC_PRE_SEND_FRESHNESS_PATH,
        handle_pre_send_freshness,
    )
    callback_url = getattr(cfg.public_reply, "freshness_callback_url", "")
    if isinstance(callback_url, str) and callback_url.strip():
        callback_path = urlparse(callback_url).path
        if callback_path and callback_path not in {
            "/internal/pre-send-freshness",
            PUBLIC_PRE_SEND_FRESHNESS_PATH,
        }:
            app.router.add_post(callback_path, handle_pre_send_freshness)
    app.router.add_get("/healthz", handle_healthz)

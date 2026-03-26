"""Webhook HTTP 入口。

当前实现基于 aiohttp.web，支持：
- POST /webhook/zendesk — 接收 Zendesk Event Webhook 推送
- GET  /health — 健康检查
- GET  /status — 查看当前处理状态
"""

from __future__ import annotations

import json

from aiohttp import web

from zd_agent.config import get_config
from zd_agent.log import get_logger

logger = get_logger(module="webhook")
MANAGER_APP_KEY = web.AppKey("manager", object)
SUPPORTED_EVENT_TYPE = "zen:event-type:ticket.created"
ENTERPRISE_TAG = "enterprise"


async def handle_health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def handle_status(request: web.Request) -> web.Response:
    """返回当前 SessionManager 状态（如已初始化）"""
    manager = request.app.get(MANAGER_APP_KEY)
    if manager is None:
        return web.json_response({"error": "manager not initialized"}, status=503)

    info = manager.get_all_sessions()
    return web.json_response({
        "max_concurrent": info["max_concurrent"],
        "active": info["active"],
        "completed": info["completed"],
    })


def _build_ignored_response(reason: str) -> web.Response:
    """返回统一的忽略响应。"""
    return web.json_response({
        "accepted": False,
        "ignored": True,
        "reason": reason,
    })


def _validate_webhook_auth(request: web.Request) -> web.Response | None:
    """校验 webhook 请求头鉴权。"""
    cfg = get_config()
    auth_header = _clean_optional_string(cfg.webhook.auth_header)
    auth_value = _clean_optional_string(cfg.webhook.auth_value)

    if not auth_header and not auth_value:
        return None

    if not auth_header or not auth_value:
        logger.error(
            "webhook_auth_misconfigured",
            auth_header=bool(auth_header),
            auth_value=bool(auth_value),
        )
        return web.json_response({"error": "webhook auth misconfigured"}, status=500)

    request_value = request.headers.get(auth_header)
    if request_value != auth_value:
        return web.json_response({"error": "unauthorized"}, status=401)

    return None


def _clean_optional_string(value: str | None) -> str | None:
    """将空字符串归一化为 None。"""
    if value is None:
        return None
    if value.strip() == "":
        return None
    return value


def _extract_event_detail(body: object) -> dict:
    """提取 webhook detail 对象。"""
    if not isinstance(body, dict):
        raise ValueError("JSON body must be an object")

    detail = body.get("detail")
    if not isinstance(detail, dict):
        raise ValueError("detail object required")

    return detail


def _extract_ticket_id(detail: dict) -> int:
    """从 Zendesk 事件中提取工单号。"""
    raw_ticket_id = detail.get("id")
    try:
        return int(raw_ticket_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("detail.id must be a valid int") from exc


async def handle_zendesk_webhook(request: web.Request) -> web.Response:
    """处理 Zendesk Event Webhook 推送。"""
    manager = request.app.get(MANAGER_APP_KEY)
    if manager is None:
        return web.json_response({"error": "manager not initialized"}, status=503)

    auth_error = _validate_webhook_auth(request)
    if auth_error is not None:
        return auth_error

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid JSON"}, status=400)

    try:
        detail = _extract_event_detail(body)
        ticket_id = _extract_ticket_id(detail)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    event_type = body.get("type")
    if event_type != SUPPORTED_EVENT_TYPE:
        return _build_ignored_response("unsupported_event_type")

    tags = detail.get("tags", [])
    if not isinstance(tags, list) or ENTERPRISE_TAG not in tags:
        return _build_ignored_response("missing_enterprise_tag")

    logger.info("webhook_received", ticket_id=ticket_id, event_type=event_type)

    session = await manager.dispatch(ticket_id, hint="", dry_run=False)

    return web.json_response({
        "accepted": True,
        "ticket_id": ticket_id,
        "session_id": session.session_id,
    })


def create_app(manager: object | None = None) -> web.Application:
    """创建 aiohttp web 应用"""
    app = web.Application()
    app[MANAGER_APP_KEY] = manager

    app.router.add_get("/health", handle_health)
    app.router.add_get("/status", handle_status)
    app.router.add_post("/webhook/zendesk", handle_zendesk_webhook)

    return app


async def run_webhook_server(
    host: str = "0.0.0.0",
    port: int = 8080,
    manager: object | None = None,
) -> None:
    """启动 Webhook HTTP 服务器"""
    app = create_app(manager)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("webhook_server_started", host=host, port=port)

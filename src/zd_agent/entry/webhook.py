"""Webhook HTTP 入口。

当前实现基于 aiohttp.web，支持：
- POST /webhook/zendesk — 接收 Zendesk Event Webhook 推送
- GET  /health — 健康检查
- GET  /status — 查看当前处理状态
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

from aiohttp import web

from zd_agent.config import get_config
from zd_agent.log import get_logger

logger = get_logger(module="webhook")
MANAGER_APP_KEY = web.AppKey("manager", object)
SUPPORTED_EVENT_TYPE = "zen:event-type:ticket.created"
ENTERPRISE_TAG = "enterprise"
SIGNATURE_HEADER = "X-Zendesk-Webhook-Signature"
SIGNATURE_TIMESTAMP_HEADER = "X-Zendesk-Webhook-Signature-Timestamp"
SIGNATURE_MAX_AGE_SECONDS = 300


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


def _get_signature_header_info(request: web.Request) -> tuple[str | None, str | None]:
    """获取当前请求中的 Zendesk 签名头信息。"""
    return (
        request.headers.get(SIGNATURE_HEADER),
        request.headers.get(SIGNATURE_TIMESTAMP_HEADER),
    )


def _validate_webhook_signature(request: web.Request, raw_body: str) -> web.Response | None:
    """校验 Zendesk Webhook 签名与时间窗口。"""
    cfg = get_config()
    signing_secret = _clean_optional_string(cfg.webhook.signing_secret)
    signature = request.headers.get(SIGNATURE_HEADER)
    signature_timestamp = request.headers.get(SIGNATURE_TIMESTAMP_HEADER)

    if signing_secret is None:
        return None

    if signature is None or signature_timestamp is None:
        logger.error(
            "webhook_signature_missing",
            signature_header=signature,
            signature_timestamp=signature_timestamp,
        )
        return web.json_response({"error": "missing webhook signature headers"}, status=401)

    try:
        timestamp_value = _parse_timestamp(signature_timestamp)
    except ValueError:
        logger.warning(
            "webhook_signature_timestamp_invalid",
            signature_timestamp=signature_timestamp,
        )
        return web.json_response({"error": "invalid signature timestamp"}, status=401)

    current_time = int(time.time())
    if abs(current_time - timestamp_value) > SIGNATURE_MAX_AGE_SECONDS:
        logger.warning(
            "webhook_signature_timestamp_expired",
            signature_timestamp=signature_timestamp,
            current_time=current_time,
        )
        return web.json_response({"error": "signature timestamp expired"}, status=401)

    expected_signature = base64.b64encode(
        hmac.new(
            signing_secret.encode("utf-8"),
            f"{signature_timestamp}{raw_body}".encode("utf-8"),
            hashlib.sha256,
        ).digest(),
    ).decode("utf-8")

    if not hmac.compare_digest(signature, expected_signature):
        logger.warning(
            "webhook_signature_invalid",
            signature_header=signature,
            signature_timestamp=signature_timestamp,
        )
        return web.json_response({"error": "unauthorized"}, status=401)

    return None


def _clean_optional_string(value: str | None) -> str | None:
    """将空字符串归一化为 None。"""
    if value is None:
        return None
    if value.strip() == "":
        return None
    return value


def _parse_timestamp(raw: str) -> int:
    """解析时间戳，支持 Unix epoch 整数和 ISO 8601 两种格式。"""
    try:
        return int(raw)
    except ValueError:
        pass
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return int(dt.timestamp())


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

    try:
        raw_body = (await request.read()).decode("utf-8")
    except UnicodeDecodeError:
        return web.json_response({"error": "request body must be utf-8"}, status=400)

    signature_error = _validate_webhook_signature(request, raw_body)
    if signature_error is not None:
        return signature_error

    try:
        body = json.loads(raw_body)
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

    signature_header, signature_timestamp = _get_signature_header_info(request)
    logger.info(
        "webhook_received",
        ticket_id=ticket_id,
        event_type=event_type,
        signature_header=signature_header,
        signature_timestamp=signature_timestamp,
    )

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

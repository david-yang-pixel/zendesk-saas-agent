"""Feishu webhook handler: URL verification + event decryption + command dispatch."""
from __future__ import annotations

import hmac
import json
from typing import Any

import lark_oapi as lark
from aiohttp import web

from zd_agent.integrations.feishu.commands import Command, parse_command
from zd_agent.log import get_logger

logger = get_logger(module="entry.feishu_webhook")


def _decrypt_if_needed(body: dict[str, Any], encrypt_key: str) -> dict[str, Any]:
    """If body is encrypted (has 'encrypt' field), decrypt it."""
    if "encrypt" in body and encrypt_key:
        cipher = lark.AESCipher(encrypt_key)
        plain = cipher.decrypt_string(body["encrypt"])
        return json.loads(plain)
    return body


def extract_message_text(payload: dict[str, Any]) -> str:
    """From a Feishu v2 event, extract the text of a message event.

    Returns empty string if this isn't a text message event.
    """
    event = payload.get("event", {})
    message = event.get("message", {}) if isinstance(event, dict) else {}
    if message.get("message_type") != "text":
        return ""
    raw = message.get("content", "")
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return ""
    return str(parsed.get("text", ""))


async def handle_feishu_webhook(request: web.Request) -> web.Response:
    """aiohttp handler.

    App state required:
      - `config` (AppConfig)
      - `feishu_command_queue` (asyncio.Queue for parsed Commands)
    """
    cfg = request.app["config"]
    verify_token = cfg.feishu.verify_token or ""
    encrypt_key = cfg.feishu.encrypt_key or ""

    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400, text="bad json")

    # 1. Decrypt if needed
    try:
        payload = _decrypt_if_needed(body, encrypt_key)
    except Exception as e:
        logger.warning("feishu_decrypt_failed", error=str(e))
        return web.Response(status=400, text="bad encrypt")

    # 2. URL verification (both v1 and v2 handshake forms)
    if payload.get("type") == "url_verification":
        if not hmac.compare_digest(str(payload.get("token", "")), verify_token):
            logger.warning("feishu_url_verify_bad_token")
            return web.Response(status=401, text="bad token")
        return web.json_response({"challenge": payload.get("challenge", "")})

    # 3. Verify token on real events (v2 has it under header, v1 at top level)
    event_token = ""
    header = payload.get("header") if isinstance(payload, dict) else None
    if isinstance(header, dict):
        event_token = header.get("token", "")
    if not event_token:
        event_token = payload.get("token", "")
    if not hmac.compare_digest(event_token, verify_token):
        logger.warning("feishu_event_bad_token")
        return web.Response(status=401, text="bad token")

    # 4. Extract text + parse command
    text = extract_message_text(payload)
    if not text:
        # Not a text message — ignore (could be file, image, etc.)
        return web.Response(status=200, text="ignored")

    cmd = parse_command(text)
    if cmd is None:
        # Plain chat text, not a command — ignore
        return web.Response(status=200, text="not a command")

    # 5. Enqueue for dispatch (the main loop / command dispatcher handles execution)
    queue = request.app.get("feishu_command_queue")
    if queue is not None:
        await queue.put(cmd)
    logger.info("feishu_command_received", name=cmd.name, args=cmd.args)
    return web.Response(status=200, text="ok")

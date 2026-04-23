"""Feishu custom bot (incoming webhook) client.

Sends interactive cards via HTTPS POST to a fixed webhook URL.
One-way only — does NOT receive events.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

import httpx

from zd_agent.integrations.feishu.cards import (
    alert_card,
    auth_renew_card,
    daily_summary_card,
    ticket_done_card,
    ticket_escalate_card,
)
from zd_agent.log import get_logger

logger = get_logger(module="integrations.feishu")


def _gen_sign(timestamp: int, secret: str) -> str:
    """飞书自定义机器人加签算法（HMAC-SHA256）。

    sign = base64(hmac_sha256(key=f"{timestamp}\\n{secret}".encode(), data=b""))
    """
    key = f"{timestamp}\n{secret}".encode("utf-8")
    mac = hmac.new(key, b"", hashlib.sha256).digest()
    return base64.b64encode(mac).decode("utf-8")


class FeishuClient:
    """Incoming-webhook-only Feishu client. One-way push via HTTPS POST."""

    def __init__(self, webhook_url: str, webhook_secret: str = "", *, timeout: float = 15.0):
        self.webhook_url = webhook_url
        self.webhook_secret = webhook_secret
        self.timeout = timeout

    async def send_card(self, *, chat_id: str = "", card: dict[str, Any]) -> None:
        """Send an interactive card.

        `chat_id` is ignored (webhook is bound to a single group); kept for
        signature compatibility with older call sites. A debug log notes it
        if supplied so callers can see it's being ignored.
        """
        if chat_id:
            logger.debug("feishu_chat_id_ignored", chat_id=chat_id,
                         reason="webhook bot binds to one group")

        if not self.webhook_url:
            raise RuntimeError("Feishu webhook_url not configured")

        body: dict[str, Any] = {
            "msg_type": "interactive",
            "card": card,
        }

        if self.webhook_secret:
            ts = int(time.time())
            body["timestamp"] = str(ts)
            body["sign"] = _gen_sign(ts, self.webhook_secret)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(self.webhook_url, json=body)

        if resp.status_code != 200:
            raise RuntimeError(
                f"Feishu webhook HTTP {resp.status_code}: {resp.text[:500]}"
            )
        try:
            data = resp.json()
        except Exception:
            raise RuntimeError(f"Feishu webhook invalid JSON response: {resp.text[:200]}")
        if data.get("code") != 0:
            raise RuntimeError(
                f"Feishu webhook error code={data.get('code')} msg={data.get('msg')}"
            )

    async def send_ticket_done(self, *, chat_id: str = "", ticket_id: str,
                               summary: str, zendesk_url: str) -> None:
        await self.send_card(
            chat_id=chat_id,
            card=ticket_done_card(
                ticket_id=ticket_id, summary=summary, zendesk_url=zendesk_url,
            ),
        )

    async def send_ticket_escalate(self, *, chat_id: str = "", ticket_id: str,
                                    reason: str, zendesk_url: str) -> None:
        await self.send_card(
            chat_id=chat_id,
            card=ticket_escalate_card(
                ticket_id=ticket_id, reason=reason, zendesk_url=zendesk_url,
            ),
        )

    async def send_auth_renew(self, *, chat_id: str = "", url: str, user: str) -> None:
        await self.send_card(
            chat_id=chat_id, card=auth_renew_card(url=url, user=user),
        )

    async def send_daily_summary(self, *, chat_id: str = "", date: str,
                                 stats: dict[str, Any]) -> None:
        await self.send_card(
            chat_id=chat_id, card=daily_summary_card(date=date, stats=stats),
        )

    async def send_alert(self, *, chat_id: str = "", title: str, message: str,
                         urgent: bool = False) -> None:
        await self.send_card(
            chat_id=chat_id,
            card=alert_card(title=title, message=message, urgent=urgent),
        )

"""Feishu custom bot (incoming webhook) client.

Sends interactive cards via HTTPS POST to a fixed webhook URL.
One-way only — does NOT receive events.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
import asyncio
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

    def __init__(
        self,
        webhook_url: str,
        webhook_secret: str = "",
        *,
        timeout: float = 15.0,
        retry_delays: tuple[float, ...] = (1.0, 3.0, 8.0),
    ):
        self.webhook_url = webhook_url
        self.webhook_secret = webhook_secret
        self.timeout = timeout
        self.retry_delays = retry_delays

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

        for attempt in range(len(self.retry_delays) + 1):
            body: dict[str, Any] = {
                "msg_type": "interactive",
                "card": card,
            }

            if self.webhook_secret:
                ts = int(time.time())
                body["timestamp"] = str(ts)
                body["sign"] = _gen_sign(ts, self.webhook_secret)

            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(self.webhook_url, json=body)
            except httpx.HTTPError as e:
                if self._should_retry_transport_error(attempt):
                    await self._sleep_before_retry(
                        attempt,
                        reason=f"transport_{type(e).__name__}",
                    )
                    continue
                raise RuntimeError(
                    f"Feishu webhook request failed: {type(e).__name__}"
                ) from e

            if resp.status_code != 200:
                if self._should_retry_http(resp.status_code, attempt):
                    await self._sleep_before_retry(attempt, reason=f"http_{resp.status_code}")
                    continue
                raise RuntimeError(
                    f"Feishu webhook HTTP {resp.status_code}: {resp.text[:500]}"
                )
            try:
                data = resp.json()
            except Exception:
                raise RuntimeError(f"Feishu webhook invalid JSON response: {resp.text[:200]}")
            if data.get("code") == 0:
                return
            if self._should_retry_code(data.get("code"), attempt):
                await self._sleep_before_retry(attempt, reason=f"code_{data.get('code')}")
                continue
            raise RuntimeError(
                f"Feishu webhook error code={data.get('code')} msg={data.get('msg')}"
            )

    def _should_retry_http(self, status_code: int, attempt: int) -> bool:
        return status_code in {429, 500, 502, 503, 504} and attempt < len(self.retry_delays)

    def _should_retry_code(self, code: Any, attempt: int) -> bool:
        return code == 11232 and attempt < len(self.retry_delays)

    def _should_retry_transport_error(self, attempt: int) -> bool:
        return attempt < len(self.retry_delays)

    async def _sleep_before_retry(self, attempt: int, *, reason: str) -> None:
        delay = self.retry_delays[attempt]
        logger.warning(
            "feishu_send_retry",
            attempt=attempt + 1,
            delay_seconds=delay,
            reason=reason,
        )
        if delay > 0:
            await asyncio.sleep(delay)

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

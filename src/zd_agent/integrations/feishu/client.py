"""Feishu client: send interactive cards + verify/decrypt webhook payloads."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
)

from zd_agent.integrations.feishu.cards import (
    alert_card,
    auth_renew_card,
    daily_summary_card,
    ticket_done_card,
    ticket_escalate_card,
)


class FeishuClient:
    """Thin wrapper around lark-oapi sync client, exposing async-friendly methods."""

    def __init__(self, *, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._client: Optional[Any] = None

    def _lark(self):
        if self._client is None:
            self._client = (
                lark.Client.builder()
                .app_id(self.app_id)
                .app_secret(self.app_secret)
                .log_level(lark.LogLevel.WARNING)
                .build()
            )
        return self._client

    async def send_card(self, *, chat_id: str, card: dict[str, Any]) -> None:
        """Post an interactive card message to a chat_id."""
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("interactive")
                .content(json.dumps(card, ensure_ascii=False))
                .build()
            )
            .build()
        )
        client = self._lark()
        # lark-oapi v1 is sync; offload to thread
        resp = await asyncio.to_thread(client.im.v1.message.create, req)
        if not resp.success():
            raise RuntimeError(
                f"Feishu send_card failed: code={resp.code} msg={resp.msg}"
            )

    async def send_ticket_done(
        self, *, chat_id: str, ticket_id: str, summary: str, zendesk_url: str,
    ) -> None:
        await self.send_card(
            chat_id=chat_id,
            card=ticket_done_card(
                ticket_id=ticket_id, summary=summary, zendesk_url=zendesk_url,
            ),
        )

    async def send_ticket_escalate(
        self, *, chat_id: str, ticket_id: str, reason: str, zendesk_url: str,
    ) -> None:
        await self.send_card(
            chat_id=chat_id,
            card=ticket_escalate_card(
                ticket_id=ticket_id, reason=reason, zendesk_url=zendesk_url,
            ),
        )

    async def send_auth_renew(self, *, chat_id: str, url: str, user: str) -> None:
        await self.send_card(
            chat_id=chat_id, card=auth_renew_card(url=url, user=user),
        )

    async def send_daily_summary(
        self, *, chat_id: str, date: str, stats: dict[str, Any],
    ) -> None:
        await self.send_card(
            chat_id=chat_id, card=daily_summary_card(date=date, stats=stats),
        )

    async def send_alert(
        self, *, chat_id: str, title: str, message: str, urgent: bool = False,
    ) -> None:
        await self.send_card(
            chat_id=chat_id,
            card=alert_card(title=title, message=message, urgent=urgent),
        )


def verify_webhook_request(
    *, verify_token: str, body: dict[str, Any],
) -> bool:
    """Verify Feishu webhook `token` field (plaintext mode).

    Feishu includes `token` in the request body for non-encrypted events,
    and inside the decrypted payload for encrypted events.
    """
    return body.get("token") == verify_token


def decrypt_feishu_payload(
    *, encrypt_key: str, encrypted: str,
) -> dict[str, Any]:
    """Decrypt Feishu AES-encrypted event payload using lark-oapi's AESCipher.

    Encrypted payload format: { "encrypt": "<base64>" }
    """
    cipher = lark.AESCipher(encrypt_key)
    plain = cipher.decrypt_string(encrypted)
    return json.loads(plain)

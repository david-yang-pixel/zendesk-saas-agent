import asyncio
import base64
import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest

from zd_agent.services.zendesk_messaging_webhook import (
    handle_zendesk_messaging_webhook,
)
from zd_agent.services.app_context import trigger_queue
from zd_agent.services.webhook_dedup import WebhookDedup


class FakeRequest:
    def __init__(self, payload: dict, dedup_path, secret: str = "secret") -> None:
        body = json.dumps(payload).encode("utf-8")
        timestamp = "1780460000"
        signature = base64.b64encode(
            hmac.new(
                secret.encode("utf-8"),
                timestamp.encode("utf-8") + body,
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")
        self._body = body
        self.headers = {
            "X-Zendesk-Webhook-Signature": signature,
            "X-Zendesk-Webhook-Signature-Timestamp": timestamp,
        }
        self.app = {
            "config": SimpleNamespace(
                webhook=SimpleNamespace(messaging_signing_secret=secret)
            ),
            "webhook_dedup": WebhookDedup(dedup_path),
            "trigger_queue": asyncio.Queue(),
        }

    async def read(self) -> bytes:
        return self._body


@pytest.mark.asyncio
async def test_messaging_webhook_enqueues_end_user_actor(tmp_path):
    payload = {
        "id": "evt-1",
        "app": {"id": "app-1"},
        "detail": {"id": "2757"},
        "event": {
            "actor": {"type": "end_user"},
            "conversation": {"id": "conv-1"},
            "message": {"id": "msg-1", "body": "hello"},
        },
        "subject": "zen:ticket:2757",
        "type": "zen:event-type:messaging_ticket.message_added",
    }
    request = FakeRequest(payload, tmp_path / "dedup.json")

    response = await handle_zendesk_messaging_webhook(request)

    assert response.status == 200
    queued = trigger_queue(request.app).get_nowait()
    assert queued["source"] == "messaging_webhook"
    assert queued["ticket_id"] == "2757"
    assert queued["conversation_id"] == "conv-1"
    assert queued["messaging_app_id"] == "app-1"

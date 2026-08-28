import asyncio
import base64
import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from zd_agent.services.zendesk_messaging_webhook import (
    handle_zendesk_messaging_webhook,
)
from zd_agent.services.app_context import trigger_queue
from zd_agent.services.webhook_dedup import WebhookDedup


class FakeRequest:
    def __init__(
        self,
        payload: dict,
        dedup_path,
        secret: str = "secret",
        timestamp: str = "1780460000",
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
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
                webhook=SimpleNamespace(messaging_signing_secret=secret),
                public_reply=SimpleNamespace(silence_window_seconds=30),
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

    with patch(
        "zd_agent.services.zendesk_messaging_webhook.time.time",
        return_value=1780460000.0,
    ):
        response = await handle_zendesk_messaging_webhook(request)

    assert response.status == 200
    queued = trigger_queue(request.app).get_nowait()
    assert queued["source"] == "messaging_webhook"
    assert queued["ticket_id"] == "2757"
    assert queued["conversation_id"] == "conv-1"
    assert queued["messaging_app_id"] == "app-1"
    assert queued["available_at"] > 0


@pytest.mark.asyncio
async def test_messaging_webhook_persists_context_before_skipping_system_actor(
    tmp_path,
):
    payload = {
        "id": "evt-system-context",
        "app": {"id": "app-1"},
        "detail": {"id": "4186"},
        "event": {
            "actor": {"type": "system"},
            "conversation": {"id": "conv-4186"},
            "message": {"id": "msg-system", "body": "internal status update"},
        },
        "subject": "zen:ticket:4186",
        "type": "zen:event-type:messaging_ticket.message_added",
    }
    request = FakeRequest(payload, tmp_path / "dedup.json")

    with (
        patch(
            "zd_agent.services.zendesk_messaging_webhook.time.time",
            return_value=1780460000.0,
        ),
        patch(
            "zd_agent.services.zendesk_messaging_webhook.save_messaging_context",
        ) as save_context,
    ):
        response = await handle_zendesk_messaging_webhook(request)

    assert response.status == 200
    assert response.text == "skip non-user actor"
    assert trigger_queue(request.app).empty()
    save_context.assert_called_once_with(
        ticket_id="4186",
        conversation_id="conv-4186",
        app_id="app-1",
    )


@pytest.mark.asyncio
async def test_messaging_webhook_immediately_marks_active_session_context_dirty(
    tmp_path,
):
    payload = {
        "id": "evt-context-1",
        "app": {"id": "app-1"},
        "detail": {"id": "3485"},
        "event": {
            "actor": {"type": "end_user"},
            "conversation": {"id": "conv-1"},
            "message": {"id": "msg-2", "body": "another screenshot"},
        },
        "subject": "zen:ticket:3485",
        "type": "zen:event-type:messaging_ticket.message_added",
    }
    request = FakeRequest(payload, tmp_path / "dedup.json")
    manager = MagicMock()
    manager.mark_customer_context_dirty.return_value = True
    request.app["session_manager"] = manager

    with patch(
        "zd_agent.services.zendesk_messaging_webhook.time.time",
        return_value=1780460000.0,
    ):
        response = await handle_zendesk_messaging_webhook(request)

    assert response.status == 200
    manager.mark_customer_context_dirty.assert_called_once_with(
        3485,
        source="messaging_webhook",
        event_id="evt-context-1",
    )


@pytest.mark.asyncio
async def test_messaging_webhook_uses_customer_message_merge_window(tmp_path):
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
    request = FakeRequest(payload, tmp_path / "dedup.json", timestamp="1000")

    with patch(
        "zd_agent.services.zendesk_messaging_webhook.time.time", return_value=1000.0
    ):
        response = await handle_zendesk_messaging_webhook(request)

    assert response.status == 200
    queued = trigger_queue(request.app).get_nowait()
    assert queued["available_at"] == 1030.0


@pytest.mark.asyncio
async def test_messaging_webhook_rejects_stale_signature_timestamp(tmp_path):
    payload = {
        "id": "evt-stale",
        "detail": {"id": "2757"},
        "event": {
            "actor": {"type": "end_user"},
            "message": {"id": "msg-stale", "body": "hello"},
        },
    }
    request = FakeRequest(payload, tmp_path / "dedup.json", timestamp="1000")

    with patch(
        "zd_agent.services.zendesk_messaging_webhook.time.time",
        return_value=1301.0,
    ):
        response = await handle_zendesk_messaging_webhook(request)

    assert response.status == 401
    assert trigger_queue(request.app).empty()


@pytest.mark.asyncio
async def test_messaging_queue_failure_does_not_mark_event_processed(tmp_path):
    class FailingQueue:
        async def put(self, message):
            raise OSError("disk unavailable")

    payload = {
        "id": "evt-failed",
        "detail": {"id": "2757"},
        "event": {
            "actor": {"type": "end_user"},
            "message": {"id": "msg-failed", "body": "hello"},
        },
    }
    request = FakeRequest(payload, tmp_path / "dedup.json")
    request.app["trigger_queue"] = FailingQueue()

    with patch(
        "zd_agent.services.zendesk_messaging_webhook.time.time",
        return_value=1780460000.0,
    ):
        response = await handle_zendesk_messaging_webhook(request)

    assert response.status == 503
    assert len(request.app["webhook_dedup"]) == 0


@pytest.mark.asyncio
async def test_messaging_event_without_id_uses_body_hash_for_queue_key(tmp_path):
    payload = {
        "detail": {"id": "2757"},
        "event": {
            "actor": {"type": "end_user"},
            "message": {"id": "msg-no-event-id", "body": "hello"},
        },
    }
    request = FakeRequest(payload, tmp_path / "dedup.json")

    with patch(
        "zd_agent.services.zendesk_messaging_webhook.time.time",
        return_value=1780460000.0,
    ):
        response = await handle_zendesk_messaging_webhook(request)

    assert response.status == 200
    queued = trigger_queue(request.app).get_nowait()
    assert queued["_queue_key"].startswith("messaging:body:")


@pytest.mark.asyncio
async def test_messaging_bad_json_log_does_not_include_customer_body(tmp_path):
    request = FakeRequest({}, tmp_path / "dedup.json")
    body = b'{"token":"super-secret"'
    timestamp = request.headers["X-Zendesk-Webhook-Signature-Timestamp"]
    request._body = body
    request.headers["X-Zendesk-Webhook-Signature"] = base64.b64encode(
        hmac.new(
            b"secret",
            timestamp.encode("utf-8") + body,
            hashlib.sha256,
        ).digest()
    ).decode("utf-8")
    warning = MagicMock()

    with (
        patch(
            "zd_agent.services.zendesk_messaging_webhook.time.time",
            return_value=1780460000.0,
        ),
        patch(
            "zd_agent.services.zendesk_messaging_webhook.logger.warning",
            warning,
        ),
    ):
        response = await handle_zendesk_messaging_webhook(request)

    assert response.status == 400
    rendered = repr(warning.call_args_list)
    assert "super-secret" not in rendered
    warning.assert_called_once_with(
        "zendesk_messaging_webhook_bad_json",
        body_size=len(body),
        body_sha256=hashlib.sha256(body).hexdigest(),
    )

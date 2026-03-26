import os
import base64
import hashlib
import hmac
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import zd_agent.config as config_module
from aiohttp.test_utils import TestClient, TestServer

from zd_agent.entry.webhook import create_app


class WebhookTest(unittest.IsolatedAsyncioTestCase):
    signing_secret = "RyqsCLbrnJtjQofdJVbPjy9M8bbHbjl95747HzOI5hY="
    current_timestamp = 1_700_000_000

    async def asyncSetUp(self) -> None:
        self.manager = SimpleNamespace(
            dispatch=AsyncMock(return_value=SimpleNamespace(session_id="session-123")),
        )
        self.server = TestServer(create_app(self.manager))
        self.client = TestClient(self.server)
        await self.client.start_server()
        config_module._config = None

    async def asyncTearDown(self) -> None:
        await self.client.close()
        config_module._config = None

    def _event_payload(
        self,
        *,
        event_type: str = "zen:event-type:ticket.created",
        tags: list[str] | None = None,
        ticket_id: str = "5158",
    ) -> dict:
        return {
            "account_id": 22129848,
            "detail": {
                "id": ticket_id,
                "tags": tags or ["enterprise"],
            },
            "id": "cbe4028c-7239-495d-b020-f22348516046",
            "type": event_type,
        }

    def _encode_body(self, payload: dict) -> str:
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

    def _build_signature(self, body: str, timestamp: str) -> str:
        digest = hmac.new(
            self.signing_secret.encode("utf-8"),
            f"{timestamp}{body}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    async def _post_signed_event(
        self,
        payload: dict,
        *,
        timestamp: int | None = None,
        signature: str | None = None,
    ):
        raw_body = self._encode_body(payload)
        timestamp_text = str(timestamp if timestamp is not None else self.current_timestamp)
        signature_text = signature or self._build_signature(raw_body, timestamp_text)
        return await self.client.post(
            "/webhook/zendesk",
            data=raw_body.encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Zendesk-Webhook-Signature": signature_text,
                "X-Zendesk-Webhook-Signature-Timestamp": timestamp_text,
            },
        )

    async def test_dispatches_ticket_created_event_with_enterprise_tag(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "ZD_WEBHOOK_SIGNING_SECRET": self.signing_secret,
                },
                clear=False,
            ),
            patch("zd_agent.entry.webhook.time.time", return_value=self.current_timestamp),
            patch("zd_agent.entry.webhook.logger.info") as mock_logger_info,
        ):
            config_module._config = None
            response = await self._post_signed_event(self._event_payload())

        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertTrue(payload["accepted"])
        self.assertEqual(payload["ticket_id"], 5158)
        self.assertEqual(payload["session_id"], "session-123")
        self.manager.dispatch.assert_awaited_once_with(5158, hint="", dry_run=False)
        mock_logger_info.assert_any_call(
            "webhook_received",
            ticket_id=5158,
            event_type="zen:event-type:ticket.created",
            signature_header=self._build_signature(
                self._encode_body(self._event_payload()),
                str(self.current_timestamp),
            ),
            signature_timestamp=str(self.current_timestamp),
        )

    async def test_rejects_request_when_signature_is_invalid(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ZD_WEBHOOK_SIGNING_SECRET": self.signing_secret,
            },
            clear=False,
        ), patch("zd_agent.entry.webhook.time.time", return_value=self.current_timestamp):
            config_module._config = None
            response = await self._post_signed_event(
                self._event_payload(),
                signature="invalid-signature",
            )

        self.assertEqual(response.status, 401)
        payload = await response.json()
        self.assertEqual(payload["error"], "unauthorized")
        self.manager.dispatch.assert_not_awaited()

    async def test_rejects_request_when_signature_timestamp_is_expired(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ZD_WEBHOOK_SIGNING_SECRET": self.signing_secret,
            },
            clear=False,
        ), patch("zd_agent.entry.webhook.time.time", return_value=self.current_timestamp):
            config_module._config = None
            response = await self._post_signed_event(
                self._event_payload(),
                timestamp=self.current_timestamp - 301,
            )

        self.assertEqual(response.status, 401)
        payload = await response.json()
        self.assertEqual(payload["error"], "signature timestamp expired")
        self.manager.dispatch.assert_not_awaited()

    async def test_ignores_event_when_type_is_not_ticket_created(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ZD_WEBHOOK_SIGNING_SECRET": self.signing_secret,
            },
            clear=False,
        ), patch("zd_agent.entry.webhook.time.time", return_value=self.current_timestamp):
            config_module._config = None
            response = await self._post_signed_event(
                self._event_payload(event_type="zen:event-type:ticket.updated"),
            )

        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertFalse(payload["accepted"])
        self.assertTrue(payload["ignored"])
        self.assertEqual(payload["reason"], "unsupported_event_type")
        self.manager.dispatch.assert_not_awaited()

    async def test_ignores_event_when_enterprise_tag_is_missing(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ZD_WEBHOOK_SIGNING_SECRET": self.signing_secret,
            },
            clear=False,
        ), patch("zd_agent.entry.webhook.time.time", return_value=self.current_timestamp):
            config_module._config = None
            response = await self._post_signed_event(
                self._event_payload(tags=["ticket-info-test-tag"]),
            )

        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertFalse(payload["accepted"])
        self.assertTrue(payload["ignored"])
        self.assertEqual(payload["reason"], "missing_enterprise_tag")
        self.manager.dispatch.assert_not_awaited()

    async def test_rejects_request_when_ticket_id_is_invalid(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ZD_WEBHOOK_SIGNING_SECRET": self.signing_secret,
            },
            clear=False,
        ), patch("zd_agent.entry.webhook.time.time", return_value=self.current_timestamp):
            config_module._config = None
            response = await self._post_signed_event(
                self._event_payload(ticket_id="invalid"),
            )

        self.assertEqual(response.status, 400)
        payload = await response.json()
        self.assertEqual(payload["error"], "detail.id must be a valid int")
        self.manager.dispatch.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

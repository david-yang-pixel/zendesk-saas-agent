import os
import base64
import hashlib
import hmac
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import zd_agent.config as config_module
from aiohttp.test_utils import TestClient, TestServer

from zd_agent.entry.webhook import create_app


class WebhookTest(unittest.IsolatedAsyncioTestCase):
    signing_secret = "RyqsCLbrnJtjQofdJVbPjy9M8bbHbjl95747HzOI5hY="
    current_timestamp = 1_700_000_000
    current_timestamp_iso = "2023-11-14T22:13:20Z"

    async def asyncSetUp(self) -> None:
        self.manager = SimpleNamespace(
            dispatch=AsyncMock(return_value=SimpleNamespace(session_id="session-123")),
        )
        self.tracker = SimpleNamespace(
            is_processed=MagicMock(return_value=False),
            mark_processed=MagicMock(),
        )
        self.server = TestServer(create_app(self.manager, tracker=self.tracker))
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

    def _comment_added_payload(
        self,
        *,
        ticket_id: str = "342757",
        comment_id: str = "8659716087550",
        comment_body: str = "Hey @support-agent please help",
        is_public: bool = False,
    ) -> dict:
        return {
            "account_id": 22129848,
            "detail": {
                "id": ticket_id,
                "tags": [],
            },
            "event": {
                "comment": {
                    "id": comment_id,
                    "body": comment_body,
                    "is_public": is_public,
                    "author": {"id": "12345", "name": "Test User", "is_staff": True},
                },
            },
            "id": "test-event-id",
            "type": "zen:event-type:ticket.comment_added",
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
        timestamp: str | None = None,
        signature: str | None = None,
    ):
        raw_body = self._encode_body(payload)
        timestamp_text = timestamp if timestamp is not None else self.current_timestamp_iso
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
                self.current_timestamp_iso,
            ),
            signature_timestamp=self.current_timestamp_iso,
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
                timestamp="2023-11-14T22:07:59Z",
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


    async def test_dispatches_on_mention_in_internal_note(self) -> None:
        with (
            patch.dict(os.environ, {"ZD_WEBHOOK_SIGNING_SECRET": self.signing_secret}, clear=False),
            patch("zd_agent.entry.webhook.time.time", return_value=self.current_timestamp),
        ):
            config_module._config = None
            response = await self._post_signed_event(self._comment_added_payload())

        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertTrue(payload["accepted"])
        self.assertEqual(payload["ticket_id"], 342757)
        self.manager.dispatch.assert_awaited_once()
        call_kwargs = self.manager.dispatch.call_args
        self.assertIn("@support-agent", call_kwargs.kwargs["hint"])

    async def test_ignores_mention_in_public_comment(self) -> None:
        with (
            patch.dict(os.environ, {"ZD_WEBHOOK_SIGNING_SECRET": self.signing_secret}, clear=False),
            patch("zd_agent.entry.webhook.time.time", return_value=self.current_timestamp),
        ):
            config_module._config = None
            response = await self._post_signed_event(
                self._comment_added_payload(is_public=True),
            )

        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertFalse(payload["accepted"])
        self.assertEqual(payload["reason"], "no_mention_trigger")
        self.manager.dispatch.assert_not_awaited()

    async def test_ignores_already_processed_comment(self) -> None:
        self.tracker.is_processed.return_value = True
        with (
            patch.dict(os.environ, {"ZD_WEBHOOK_SIGNING_SECRET": self.signing_secret}, clear=False),
            patch("zd_agent.entry.webhook.time.time", return_value=self.current_timestamp),
        ):
            config_module._config = None
            response = await self._post_signed_event(self._comment_added_payload())

        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertFalse(payload["accepted"])
        self.assertEqual(payload["reason"], "already_processed")
        self.manager.dispatch.assert_not_awaited()

    async def test_ignores_comment_without_mention(self) -> None:
        with (
            patch.dict(os.environ, {"ZD_WEBHOOK_SIGNING_SECRET": self.signing_secret}, clear=False),
            patch("zd_agent.entry.webhook.time.time", return_value=self.current_timestamp),
        ):
            config_module._config = None
            response = await self._post_signed_event(
                self._comment_added_payload(comment_body="Regular internal note, no mention here"),
            )

        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertFalse(payload["accepted"])
        self.assertEqual(payload["reason"], "no_mention_trigger")
        self.manager.dispatch.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

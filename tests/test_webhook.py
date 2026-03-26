import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import zd_agent.config as config_module
from aiohttp.test_utils import TestClient, TestServer

from zd_agent.entry.webhook import create_app


class WebhookTest(unittest.IsolatedAsyncioTestCase):
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

    async def test_dispatches_ticket_created_event_with_enterprise_tag(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ZD_WEBHOOK_AUTH_HEADER": "Authorization",
                "ZD_WEBHOOK_AUTH_VALUE": "Bearer difyai123456",
            },
            clear=False,
        ):
            config_module._config = None
            response = await self.client.post(
                "/webhook/zendesk",
                json=self._event_payload(),
                headers={"Authorization": "Bearer difyai123456"},
            )

        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertTrue(payload["accepted"])
        self.assertEqual(payload["ticket_id"], 5158)
        self.assertEqual(payload["session_id"], "session-123")
        self.manager.dispatch.assert_awaited_once_with(5158, hint="", dry_run=False)

    async def test_rejects_request_when_auth_value_is_invalid(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ZD_WEBHOOK_AUTH_HEADER": "Authorization",
                "ZD_WEBHOOK_AUTH_VALUE": "Bearer difyai123456",
            },
            clear=False,
        ):
            config_module._config = None
            response = await self.client.post(
                "/webhook/zendesk",
                json=self._event_payload(),
                headers={"Authorization": "Bearer wrong-value"},
            )

        self.assertEqual(response.status, 401)
        payload = await response.json()
        self.assertEqual(payload["error"], "unauthorized")
        self.manager.dispatch.assert_not_awaited()

    async def test_ignores_event_when_type_is_not_ticket_created(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ZD_WEBHOOK_AUTH_HEADER": "Authorization",
                "ZD_WEBHOOK_AUTH_VALUE": "Bearer difyai123456",
            },
            clear=False,
        ):
            config_module._config = None
            response = await self.client.post(
                "/webhook/zendesk",
                json=self._event_payload(event_type="zen:event-type:ticket.updated"),
                headers={"Authorization": "Bearer difyai123456"},
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
                "ZD_WEBHOOK_AUTH_HEADER": "Authorization",
                "ZD_WEBHOOK_AUTH_VALUE": "Bearer difyai123456",
            },
            clear=False,
        ):
            config_module._config = None
            response = await self.client.post(
                "/webhook/zendesk",
                json=self._event_payload(tags=["ticket-info-test-tag"]),
                headers={"Authorization": "Bearer difyai123456"},
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
                "ZD_WEBHOOK_AUTH_HEADER": "Authorization",
                "ZD_WEBHOOK_AUTH_VALUE": "Bearer difyai123456",
            },
            clear=False,
        ):
            config_module._config = None
            response = await self.client.post(
                "/webhook/zendesk",
                json=self._event_payload(ticket_id="invalid"),
                headers={"Authorization": "Bearer difyai123456"},
            )

        self.assertEqual(response.status, 400)
        payload = await response.json()
        self.assertEqual(payload["error"], "detail.id must be a valid int")
        self.manager.dispatch.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

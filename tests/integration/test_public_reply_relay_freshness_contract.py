"""Contract tests for the final freshness guard sent to the browser relay.

These tests cross the public-reply delivery and browser-relay client boundary so
they exercise the JSON-RPC payload used by the production remote MCP endpoint.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from zd_agent.config import AppConfig, load_config
from zd_agent.integrations.zendesk_browser_relay import send_browser_relay_reply
from zd_agent.services.admin_config import AdminConfigStore
from zd_agent.tools.public_reply_delivery import (
    PublicReplyTransportError,
    send_public_reply_safely,
    validate_public_reply_transport_config,
)


REAL_ASYNC_CLIENT = httpx.AsyncClient
FRESHNESS_FIELDS = {
    "freshness_check_url",
    "freshness_check_api_key",
    "snapshot_customer_message_count",
    "snapshot_customer_last_message_id",
}


def _relay_config() -> AppConfig:
    cfg = AppConfig()
    cfg.public_reply.enabled = True
    cfg.public_reply.browser_relay_url = "https://relay.example.test/zendesk-reply-mcp"
    cfg.public_reply.browser_relay_api_key = "relay-secret"
    cfg.public_reply.freshness_callback_url = (
        "https://relay.example.test/webhook/relay-pre-send-freshness"
    )
    cfg.webhook.listen_port = 8080
    return cfg


def test_remote_relay_env_override_without_callback_is_rejected(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
public_reply:
  enabled: true
  browser_relay_url: http://127.0.0.1:8097/mcp
  browser_relay_api_key: relay-secret
  freshness_callback_url: ""
resources:
  base_dir: resources
""".strip()
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "ZENDESK_BROWSER_RELAY_URL",
        "https://relay.example.test/zendesk-reply-mcp",
    )
    monkeypatch.delenv("ZENDESK_FRESHNESS_CALLBACK_URL", raising=False)

    cfg = load_config(str(config_path))

    with pytest.raises(PublicReplyTransportError, match="HTTPS.*non-loopback"):
        validate_public_reply_transport_config(cfg)


def test_admin_override_is_applied_before_public_reply_config_validation(
    tmp_path,
) -> None:
    cfg = _relay_config()
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    (state_dir / "admin_overrides.yaml").write_text(
        "public_reply:\n  freshness_callback_url: ''\n"
    )

    AdminConfigStore(state_dir).apply_saved(cfg)

    with pytest.raises(PublicReplyTransportError, match="HTTPS.*non-loopback"):
        validate_public_reply_transport_config(cfg)


def test_loopback_relay_can_use_local_freshness_fallback() -> None:
    cfg = _relay_config()
    cfg.public_reply.browser_relay_url = "http://127.0.0.1:8097/mcp"
    cfg.public_reply.freshness_callback_url = ""

    validate_public_reply_transport_config(cfg)


@pytest.mark.parametrize("listen_port", [0, 65536, 99999])
def test_loopback_freshness_fallback_rejects_invalid_webhook_port(
    listen_port: int,
) -> None:
    cfg = _relay_config()
    cfg.public_reply.browser_relay_url = "http://127.0.0.1:8097/mcp"
    cfg.public_reply.freshness_callback_url = ""
    cfg.webhook.listen_port = listen_port

    with pytest.raises(PublicReplyTransportError, match="port between"):
        validate_public_reply_transport_config(cfg)


@pytest.mark.parametrize(
    "relay_url",
    [
        "http://relay.example.test/zendesk-reply-mcp",
        "https://user@relay.example.test/zendesk-reply-mcp",
        "https://relay.example.test/zendesk-reply-mcp?token=unsafe",
        "https://relay.example.test/zendesk-reply-mcp#fragment",
    ],
)
def test_remote_relay_requires_clean_https_url(relay_url: str) -> None:
    cfg = _relay_config()
    cfg.public_reply.browser_relay_url = relay_url

    with pytest.raises(PublicReplyTransportError, match="HTTPS.*URL"):
        validate_public_reply_transport_config(cfg)


@pytest.mark.parametrize(
    "relay_url",
    [
        "http://127.0.0.1:0/mcp",
        "http://localhost:99999/mcp",
        "http://localhost:bad/mcp",
    ],
)
def test_relay_rejects_invalid_port(relay_url: str) -> None:
    cfg = _relay_config()
    cfg.public_reply.browser_relay_url = relay_url

    with pytest.raises(PublicReplyTransportError, match="port between"):
        validate_public_reply_transport_config(cfg)


@pytest.mark.parametrize(
    "callback_url",
    [
        "https://relay.example.test:0/webhook/relay-pre-send-freshness",
        "https://relay.example.test:99999/webhook/relay-pre-send-freshness",
        "https://relay.example.test:bad/webhook/relay-pre-send-freshness",
    ],
)
def test_callback_rejects_invalid_port(callback_url: str) -> None:
    cfg = _relay_config()
    cfg.public_reply.freshness_callback_url = callback_url

    with pytest.raises(PublicReplyTransportError, match="port between"):
        validate_public_reply_transport_config(cfg)


@pytest.mark.parametrize(
    "callback_url",
    [
        "https://agent.example.test",
        "https://user@agent.example.test/webhook/relay-pre-send-freshness",
        "https://127.0.0.2/webhook/relay-pre-send-freshness",
        "https://[::ffff:127.0.0.1]/webhook/relay-pre-send-freshness",
    ],
)
def test_remote_relay_rejects_unsafe_freshness_callback(
    callback_url: str,
) -> None:
    cfg = _relay_config()
    cfg.public_reply.freshness_callback_url = callback_url

    with pytest.raises(PublicReplyTransportError, match="freshness callback URL"):
        validate_public_reply_transport_config(cfg)


def test_loopback_relay_rejects_explicit_remote_callback() -> None:
    cfg = _relay_config()
    cfg.public_reply.browser_relay_url = "http://127.0.0.1:8097/mcp"
    cfg.public_reply.freshness_callback_url = (
        "https://attacker.example.test/webhook/relay-pre-send-freshness"
    )

    with pytest.raises(PublicReplyTransportError, match="loopback callback URL"):
        validate_public_reply_transport_config(cfg)


def test_remote_relay_requires_callback_on_same_https_origin() -> None:
    cfg = _relay_config()
    cfg.public_reply.freshness_callback_url = (
        "https://other.example.test/webhook/relay-pre-send-freshness"
    )

    with pytest.raises(PublicReplyTransportError, match="same HTTPS origin"):
        validate_public_reply_transport_config(cfg)


async def _send_and_capture_relay_arguments(reply_type: str) -> dict[str, object]:
    relay_arguments: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        relay_arguments.update(payload["params"]["arguments"])
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "content": [{"type": "text", "text": "sent"}],
                    "structuredContent": {
                        "ok": True,
                        "ticket_id": 3461,
                        "status": "sent",
                        "current_url": (
                            "https://example.zendesk.com/agent/tickets/3461"
                        ),
                    },
                    "isError": False,
                },
            },
        )

    transport = httpx.MockTransport(handler)
    with patch(
        "zd_agent.integrations.zendesk_browser_relay.httpx.AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    ):
        delivered = await send_public_reply_safely(
            cfg=_relay_config(),
            ticket_id=3461,
            body_with_footer="Customer-visible reply",
            reply_type=reply_type,
            conversation_id="conversation-3461",
            snapshot_customer_message_count=2,
            snapshot_customer_last_message_id="customer-message-2",
            delivery_attempt_id=None,
            delivery_store=None,
            delivery_store_factory=lambda _cfg: pytest.fail(
                "delivery store must not be created without an attempt id"
            ),
            zendesk_tool_caller=AsyncMock(),
            relay_sender=send_browser_relay_reply,
        )

    assert delivered is True
    return relay_arguments


@pytest.mark.asyncio
@pytest.mark.parametrize("reply_type", ["handoff"])
async def test_handoff_keeps_form_readiness_callback_at_relay_boundary(
    reply_type: str,
) -> None:
    """Handoff skips stale-answer semantics but cannot bypass a late form."""
    relay_arguments = await _send_and_capture_relay_arguments(reply_type)

    assert relay_arguments["freshness_check_url"] == (
        "https://relay.example.test/webhook/relay-pre-send-freshness"
    )
    assert relay_arguments["freshness_check_api_key"] == "relay-secret"
    assert relay_arguments["snapshot_customer_message_count"] == 2
    assert relay_arguments["snapshot_customer_last_message_id"] == (
        "customer-message-2"
    )


@pytest.mark.asyncio
async def test_handoff_without_snapshot_still_sends_form_readiness_callback() -> None:
    relay = AsyncMock()

    await send_public_reply_safely(
        cfg=_relay_config(),
        ticket_id=3461,
        body_with_footer="Transferred to a support engineer.",
        reply_type="handoff",
        conversation_id="conversation-3461",
        snapshot_customer_message_count=None,
        snapshot_customer_last_message_id=None,
        delivery_attempt_id=None,
        delivery_store=None,
        delivery_store_factory=lambda _cfg: pytest.fail("unexpected store"),
        zendesk_tool_caller=AsyncMock(),
        relay_sender=relay,
    )

    assert relay.await_args is not None
    assert relay.await_args.kwargs["freshness_check_url"] == (
        "https://relay.example.test/webhook/relay-pre-send-freshness"
    )
    assert relay.await_args.kwargs["snapshot_customer_message_count"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("reply_type", ["ask", "solution", "closed"])
async def test_customer_resolvable_reply_keeps_final_relay_freshness_contract(
    reply_type: str,
) -> None:
    """Resolvable replies retain the relay's last check immediately before send."""
    relay_arguments = await _send_and_capture_relay_arguments(reply_type)

    assert relay_arguments["freshness_check_url"] == (
        "https://relay.example.test/webhook/relay-pre-send-freshness"
    )
    assert relay_arguments["freshness_check_api_key"] == "relay-secret"
    assert relay_arguments["snapshot_customer_message_count"] == 2
    assert relay_arguments["snapshot_customer_last_message_id"] == (
        "customer-message-2"
    )


@pytest.mark.asyncio
async def test_remote_relay_refuses_loopback_freshness_callback() -> None:
    cfg = _relay_config()
    cfg.public_reply.freshness_callback_url = (
        "http://127.0.0.1:8080/internal/pre-send-freshness"
    )

    with pytest.raises(PublicReplyTransportError, match="HTTPS.*non-loopback"):
        await send_public_reply_safely(
            cfg=cfg,
            ticket_id=3461,
            body_with_footer="Customer-visible reply",
            reply_type="solution",
            conversation_id="conversation-3461",
            snapshot_customer_message_count=2,
            snapshot_customer_last_message_id="customer-message-2",
            delivery_attempt_id=None,
            delivery_store=None,
            delivery_store_factory=lambda _cfg: pytest.fail("unexpected store"),
            zendesk_tool_caller=AsyncMock(),
            relay_sender=AsyncMock(),
        )

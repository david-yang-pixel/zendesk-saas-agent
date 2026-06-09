"""Tests for public ask template side-effect helper."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zd_agent.config import AppConfig, TriggerDeciderConfig
from zd_agent.services.ask_notifications import send_ask_template_best_effort


def _make_cfg() -> AppConfig:
    cfg = AppConfig()
    cfg.trigger_decider = TriggerDeciderConfig(llm_model="gpt-test")
    return cfg


@pytest.mark.asyncio
async def test_ask_template_builds_from_form_name_and_sends_public_reply() -> None:
    mock_build = AsyncMock(return_value=("Hi Alice, please share details.", "template-C"))
    mock_send = AsyncMock(return_value=(True, "ok"))

    with (
        patch("zd_agent.config.get_config", return_value=_make_cfg()),
        patch(
            "zd_agent.prompts.public_reply.templates.build_ask_body",
            mock_build,
        ),
        patch("zd_agent.tools.zendesk_public_reply.send_public_reply", mock_send),
    ):
        sent = await send_ask_template_best_effort(
            ticket_id=9999,
            verdict_kind="insufficient_info",
            customer_msgs=[
                {
                    "id": "m1",
                    "body": "Name: Alice\nEmail: alice@example.com\nWorkspace ID: ws-1",
                },
                {"id": "m2", "body": "I get an error when upgrading."},
            ],
            ask_count=0,
            source="zendesk_webhook",
            conversation_id="conv-1",
            messaging_app_id="app-1",
        )

    assert sent is True
    mock_build.assert_awaited_once()
    build_kwargs = mock_build.await_args.kwargs
    assert build_kwargs["verdict_kind"] == "insufficient_info"
    assert build_kwargs["ask_count"] == 0
    assert build_kwargs["customer_name"] == "Alice"
    assert build_kwargs["customer_messages"] == [
        "Name: Alice\nEmail: alice@example.com\nWorkspace ID: ws-1",
        "I get an error when upgrading.",
    ]
    assert build_kwargs["model"] == "gpt-test"

    mock_send.assert_awaited_once_with(
        ticket_id=9999,
        body="Hi Alice, please share details.",
        reply_type="ask",
        conversation_id="conv-1",
        messaging_app_id="app-1",
        snapshot_customer_message_count=2,
        snapshot_customer_last_message_id="m2",
    )


@pytest.mark.asyncio
async def test_ask_template_build_failure_does_not_send() -> None:
    mock_build = AsyncMock(side_effect=RuntimeError("template err"))
    mock_send = AsyncMock(return_value=(True, "ok"))

    with (
        patch("zd_agent.config.get_config", return_value=_make_cfg()),
        patch(
            "zd_agent.prompts.public_reply.templates.build_ask_body",
            mock_build,
        ),
        patch("zd_agent.tools.zendesk_public_reply.send_public_reply", mock_send),
    ):
        sent = await send_ask_template_best_effort(
            ticket_id=9999,
            verdict_kind="greeting",
            customer_msgs=[],
            ask_count=0,
            source="zendesk_webhook",
        )

    assert sent is False
    mock_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_ask_template_send_failure_is_retryable_failure() -> None:
    mock_build = AsyncMock(return_value=("Please share details.", "template-B"))
    mock_send = AsyncMock(return_value=(False, "kill switch off"))

    with (
        patch("zd_agent.config.get_config", return_value=_make_cfg()),
        patch(
            "zd_agent.prompts.public_reply.templates.build_ask_body",
            mock_build,
        ),
        patch("zd_agent.tools.zendesk_public_reply.send_public_reply", mock_send),
    ):
        sent = await send_ask_template_best_effort(
            ticket_id=9999,
            verdict_kind="greeting",
            customer_msgs=[{"body": "hello"}],
            ask_count=1,
            source="waiting_poller",
        )

    assert sent is False


def test_extract_form_data_from_customer_messages() -> None:
    from zd_agent.services.ticket_messages import extract_form_data_from_msgs

    form = extract_form_data_from_msgs([
        {"body": "hello"},
        {"body": "Name: Alice\nEmail: alice@example.com\nWorkspace ID: ws-1"},
    ])

    assert form == {
        "name": "Alice",
        "email": "alice@example.com",
        "workspace_id": "ws-1",
    }

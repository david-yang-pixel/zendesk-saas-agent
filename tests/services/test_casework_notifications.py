from unittest.mock import AsyncMock, patch

from zd_agent.config import AppConfig, ZendeskConfig
from zd_agent.services.casework_notifications import (
    add_casework_ownership_tags_best_effort,
    send_casework_ownership_notification_best_effort,
)


def _config() -> AppConfig:
    config = AppConfig()
    config.zendesk = ZendeskConfig(
        subdomain="dify-test",
        email="agent@example.test",
        api_token="secret",
    )
    return config


async def test_casework_notification_describes_typed_ownership_without_handoff_wording() -> (
    None
):
    push = AsyncMock(return_value={"success": True})
    with (
        patch("zd_agent.config.get_config", return_value=_config()),
        patch("zd_agent.services.casework_notifications.push_notification_card", push),
    ):
        sent = await send_casework_ownership_notification_best_effort(
            ticket_id=4001,
            ownership_action="engineering",
            reason_code="confirmed_platform_defect",
            ownership_packet="Target: engineering\nCompleted checks:\n- current runtime",
            customer_reply="We confirmed the affected boundary.",
            source="casework_v2:engineering:confirmed_platform_defect",
            idempotency_reference="casework-effect:one",
        )

    assert sent is True
    content = push.await_args.kwargs["content"]
    assert "engineering" in content
    assert "confirmed_platform_defect" in content
    assert "current runtime" in content
    assert "We confirmed the affected boundary." in content
    assert "转人工" not in content
    assert "handoff" not in content.casefold()


async def test_casework_tags_encode_the_typed_owner_and_reason() -> None:
    update = AsyncMock(return_value={"success": True})
    with patch(
        "zd_agent.services.casework_notifications.call_zendesk_tool",
        update,
    ):
        tagged = await add_casework_ownership_tags_best_effort(
            ticket_id=4001,
            ownership_action="billing",
            reason_code="business_approval_required",
        )

    assert tagged is True
    assert update.await_args.args[1]["add_tags"] == [
        "casework-v2",
        "casework-owner-billing",
        "casework-reason-business_approval_required",
    ]

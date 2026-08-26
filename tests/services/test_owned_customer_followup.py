from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from zd_agent.services.notification_outbox import NotificationOutbox
from zd_agent.services.owned_customer_followup import notify_owned_customer_followup
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


@pytest.mark.asyncio
async def test_owned_ticket_new_customer_message_sends_urgent_durable_reminder(tmp_path):
    state = TicketStateTracker(tmp_path / "state.json")
    state.set(
        4051,
        TicketState(
            state="owned",
            first_seen=datetime.now(timezone.utc),
            last_customer_comment_id="old",
            ownership_action="engineering",
            ownership_reason="confirmed_platform_defect",
        ),
    )
    push = AsyncMock(return_value={"success": True})

    handled = await notify_owned_customer_followup(
        ticket_id=4051,
        customer_msgs=[
            {"id": "old", "body": "original"},
            {"id": "new", "body": "The problem is now affecting production."},
        ],
        state=state,
        state_dir=tmp_path,
        push=push,
    )

    assert handled is True
    assert state.get(4051).last_customer_comment_id == "new"
    assert push.await_args.kwargs["urgent"] is True
    assert "affecting production" in push.await_args.kwargs["content"]
    assert push.await_args.kwargs["idempotency_reference"].endswith(":new")
    assert NotificationOutbox(tmp_path / "notification_outbox.sqlite3").pending(
        limit=10
    ) == []


@pytest.mark.asyncio
async def test_owned_ticket_duplicate_customer_cursor_does_not_notify(tmp_path):
    state = TicketStateTracker(tmp_path / "state.json")
    state.set(
        4051,
        TicketState(
            state="externally_owned",
            first_seen=datetime.now(timezone.utc),
            last_customer_comment_id="same",
        ),
    )
    push = AsyncMock()

    handled = await notify_owned_customer_followup(
        ticket_id=4051,
        customer_msgs=[{"id": "same", "body": "already seen"}],
        state=state,
        state_dir=tmp_path,
        push=push,
    )

    assert handled is True
    push.assert_not_awaited()

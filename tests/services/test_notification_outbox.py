import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from zd_agent.services.notification_outbox import NotificationOutbox


def test_notification_outbox_prepare_is_idempotent_and_keeps_full_body(tmp_path):
    store = NotificationOutbox(tmp_path / "notifications.sqlite3")
    body = "Line one\n\nLine two with the actual customer reply."

    first = store.prepare(
        reference="session-outcome:1:attempt:success",
        ticket_id=1,
        card_type="ticket_done",
        title="自动回复发送成功",
        content=body,
        zendesk_url="https://example.test/tickets/1",
        urgent=False,
    )
    second = store.prepare(
        reference="session-outcome:1:attempt:success",
        ticket_id=1,
        card_type="ticket_done",
        title="ignored replacement",
        content="ignored replacement",
        zendesk_url="https://example.test/tickets/1",
        urgent=False,
    )

    assert first == second
    assert first.content == body
    assert store.pending(limit=10) == [first]


@pytest.mark.asyncio
async def test_notification_outbox_retries_failed_push_with_stable_idempotency_key(
    tmp_path,
):
    from zd_agent.services.notification_outbox import reconcile_notifications

    store = NotificationOutbox(tmp_path / "notifications.sqlite3")
    notice = store.prepare(
        reference="session-outcome:2:attempt:failed",
        ticket_id=2,
        card_type="ticket_escalate",
        title="自动回复发送失败",
        content="stage=delivery\nerror=relay timeout",
        zendesk_url="https://example.test/tickets/2",
        urgent=True,
    )
    push = AsyncMock(
        side_effect=[
            {"success": False, "error": "relay timeout"},
            {"success": True},
        ]
    )

    assert await reconcile_notifications(store=store, push=push) == 0
    pending = store.pending(limit=10)
    assert [item.reference for item in pending] == [notice.reference]
    assert pending[0].attempts == 1
    assert await reconcile_notifications(store=store, push=push) == 1
    assert store.pending(limit=10) == []
    assert [call.kwargs["idempotency_reference"] for call in push.await_args_list] == [
        notice.reference,
        notice.reference,
    ]


@pytest.mark.asyncio
async def test_session_success_notification_contains_actual_reply_body(
    tmp_path,
    monkeypatch,
):
    from zd_agent.services import feishu_notifications
    from zd_agent.session.agent_session import AgentSession, SessionStatus

    cfg = MagicMock()
    cfg.resources.base_dir = str(tmp_path)
    cfg.resources.state_dir = ".state"
    session = AgentSession(ticket_id=3, delivery_attempt_id="attempt-3")
    session.status = SessionStatus.COMPLETED
    session.reply_sent = True
    session.reply_draft = "Hi,\n\nThis is the complete customer-visible answer.\n\nRegards"
    session.casework_ownership_action = "none"
    push = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(feishu_notifications, "push_notification_card", push)

    sent = await feishu_notifications.notify_session_outcome_durable(session, cfg)

    assert sent is True
    content = push.await_args.kwargs["content"]
    assert session.reply_draft in content
    assert push.await_args.kwargs["title"] == "自动回复发送成功"


@pytest.mark.asyncio
async def test_session_failure_notification_is_urgent_and_has_failure_stage(
    tmp_path,
    monkeypatch,
):
    from zd_agent.services import feishu_notifications
    from zd_agent.session.agent_session import AgentSession, SessionStatus

    cfg = MagicMock()
    cfg.resources.base_dir = str(tmp_path)
    cfg.resources.state_dir = ".state"
    session = AgentSession(ticket_id=4, delivery_attempt_id="attempt-4")
    session.status = SessionStatus.FAILED
    session.error_message = "provider timeout"
    session.casework_delivery_state = "failed"
    push = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(feishu_notifications, "push_notification_card", push)

    sent = await feishu_notifications.notify_session_outcome_durable(session, cfg)

    assert sent is True
    assert push.await_args.kwargs["urgent"] is True
    assert push.await_args.kwargs["title"] == "自动回复发送失败"
    assert "failed" in push.await_args.kwargs["content"]
    assert "provider timeout" in push.await_args.kwargs["content"]


@pytest.mark.asyncio
async def test_parallel_reconcilers_claim_each_notification_only_once(tmp_path):
    from zd_agent.services.notification_outbox import reconcile_notifications

    store = NotificationOutbox(tmp_path / "notifications.sqlite3")
    store.prepare(
        reference="session-outcome:9:attempt:success",
        ticket_id=9,
        card_type="ticket_done",
        title="done",
        content="actual reply",
        zendesk_url="",
        urgent=False,
    )
    async def delayed_success(**_kwargs):
        await asyncio.sleep(0.01)
        return {"success": True}

    push = AsyncMock(side_effect=delayed_success)

    results = await asyncio.gather(
        reconcile_notifications(store=store, push=push),
        reconcile_notifications(store=store, push=push),
    )

    assert sum(results) == 1
    push.assert_awaited_once()

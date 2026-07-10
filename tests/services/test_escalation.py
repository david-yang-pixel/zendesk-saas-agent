"""Tests for escalation orchestration service."""
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zd_agent.services.escalation import set_escalated_to_human
from zd_agent.trigger.state_store import TicketStateTracker


def _now():
    return datetime(2026, 6, 5, 13, 25, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_set_escalated_to_human_writes_state_and_skips_handoff_for_takeover(
    tmp_path,
) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")

    with (
        patch(
            "zd_agent.services.escalation_notifications.send_handoff_reply_best_effort",
            AsyncMock(return_value=False),
        ) as mock_handoff,
        patch(
            "zd_agent.services.escalation_notifications.send_feishu_escalation_best_effort",
            AsyncMock(return_value=False),
        ),
        patch(
            "zd_agent.services.escalation_notifications.add_zendesk_escalation_tag_best_effort",
            AsyncMock(return_value=True),
        ),
    ):
        await set_escalated_to_human(
            ticket_id=9999,
            reason="human_takeover_detected",
            existing=None,
            state=state,
            log_path=Path(tmp_path) / "trigger.jsonl",
            source="zendesk_webhook",
            now=_now(),
            verdict_str="human_takeover",
            latest_cid="cid-1",
            latest_ts=_now(),
            completeness=0,
            customer_msgs=[{"body": "Name: Alice\nWorkspace ID: ws-1"}],
            extra_log={"takeover_reason": "manual public reply"},
        )

    rec = state.get(9999)
    assert rec is not None
    assert rec.state == "escalated_to_human"
    assert rec.escalated_reason == "human_takeover_detected"
    mock_handoff.assert_awaited_once()
    assert mock_handoff.await_args.kwargs["reason"] == "human_takeover_detected"
    assert mock_handoff.await_args.kwargs["customer_name"] == "Alice"
    assert mock_handoff.await_args.kwargs["delivery_attempt_id"] == (
        "zendesk_webhook:9999:cid-1:handoff"
    )


@pytest.mark.asyncio
async def test_customer_requested_emits_metric(tmp_path) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    mock_metric = MagicMock()

    with (
        patch(
            "zd_agent.services.escalation_notifications.send_handoff_reply_best_effort",
            AsyncMock(return_value=True),
        ),
        patch(
            "zd_agent.services.escalation_notifications.send_feishu_escalation_best_effort",
            AsyncMock(return_value=False),
        ),
        patch(
            "zd_agent.services.escalation_notifications.add_zendesk_escalation_tag_best_effort",
            AsyncMock(return_value=True),
        ),
        patch(
            "zd_agent.observability.metrics.emit_customer_requested_human",
            mock_metric,
        ),
    ):
        await set_escalated_to_human(
            ticket_id=9999,
            reason="customer_requested",
            existing=None,
            state=state,
            log_path=Path(tmp_path) / "trigger.jsonl",
            source="zendesk_webhook",
            now=_now(),
            verdict_str="request_human",
            latest_cid="cid-1",
            latest_ts=_now(),
            completeness=100,
            customer_phrase="human please",
        )

    mock_metric.assert_called_once()
    assert mock_metric.call_args.kwargs["ticket_id"] == 9999
    assert mock_metric.call_args.kwargs["customer_phrase"] == "human please"

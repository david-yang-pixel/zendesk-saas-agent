from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from zd_agent.services.manual_reconciliation import reconcile_manual_delivery
from zd_agent.services.public_reply_delivery_store import PublicReplyDeliveryStore
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def _quarantined(tmp_path, *, attempt_id: str) -> TicketStateTracker:
    tracker = TicketStateTracker(tmp_path / "tickets.json")
    tracker.set(
        9001,
        TicketState(
            state="manual_reconciliation",
            first_seen=NOW,
            delivery_attempt_id=attempt_id,
            last_verdict="public_reply_delivery_requires_reconciliation",
        ),
    )
    return tracker


def test_operator_confirmed_ask_resumes_customer_wait_with_audit(tmp_path) -> None:
    _quarantined(tmp_path, attempt_id="ambiguous-ask")
    outbox = PublicReplyDeliveryStore(
        tmp_path / "public_reply_outbox.sqlite3",
        now_fn=lambda: NOW.timestamp(),
    )
    attempt = outbox.prepare(
        delivery_attempt_id="ambiguous-ask",
        ticket_id=9001,
        reply_type="ask",
        body="Please provide the timestamp.",
    )
    token = outbox.claim(attempt.delivery_key)
    assert token is not None
    assert outbox.begin_send(attempt.delivery_key, token)

    result = reconcile_manual_delivery(
        state_dir=tmp_path,
        ticket_id=9001,
        delivery_attempt_id="ambiguous-ask",
        outcome="delivered",
        reason="verified exact Zendesk comment",
        actor="operator@example.test",
        now=NOW,
    )

    assert result.state == "awaiting_customer"
    assert result.delivery_attempt_id == "ambiguous-ask"
    confirmed = outbox.inspect_logical_attempt(
        delivery_attempt_id="ambiguous-ask",
        ticket_id=9001,
    )
    assert confirmed.delivered is True
    audit = json.loads((tmp_path / "manual_reconciliation.jsonl").read_text())
    assert audit["outcome"] == "delivered"
    assert audit["reason"] == "verified exact Zendesk comment"


def test_operator_proven_unsent_attempt_rotates_generation_for_v2(tmp_path) -> None:
    _quarantined(tmp_path, attempt_id="ambiguous-unsent")
    outbox = PublicReplyDeliveryStore(tmp_path / "public_reply_outbox.sqlite3")
    attempt = outbox.prepare(
        delivery_attempt_id="ambiguous-unsent",
        ticket_id=9001,
        reply_type="ask",
        body="Please provide details.",
    )
    token = outbox.claim(attempt.delivery_key)
    assert token is not None
    assert outbox.begin_send(attempt.delivery_key, token)

    result = reconcile_manual_delivery(
        state_dir=tmp_path,
        ticket_id=9001,
        delivery_attempt_id="ambiguous-unsent",
        outcome="unsent",
        reason="Zendesk has no matching comment",
        now=NOW,
    )

    assert result.state == "in_silence_window"
    assert result.delivery_attempt_id.startswith("manual-reconcile:9001:")
    assert (
        outbox.inspect_logical_attempt(
            delivery_attempt_id="ambiguous-unsent",
            ticket_id=9001,
        ).exists
        is False
    )


def test_manual_reconciliation_requires_exact_ticket_attempt_and_reason(
    tmp_path,
) -> None:
    _quarantined(tmp_path, attempt_id="exact-attempt")

    with pytest.raises(ValueError, match="does not match"):
        reconcile_manual_delivery(
            state_dir=tmp_path,
            ticket_id=9001,
            delivery_attempt_id="other-attempt",
            outcome="unsent",
            reason="verified",
        )
    with pytest.raises(ValueError, match="reason"):
        reconcile_manual_delivery(
            state_dir=tmp_path,
            ticket_id=9001,
            delivery_attempt_id="exact-attempt",
            outcome="unsent",
            reason="",
        )

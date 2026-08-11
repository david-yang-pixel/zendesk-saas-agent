from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock
from datetime import datetime, timezone

import pytest

from zd_agent.services.casework_effect_store import (
    CaseworkEffectStore,
    casework_effect_reference,
)
from zd_agent.services.silence_waker import _reconcile_post_public_internal_notes
from zd_agent.trigger.state_store import TicketStateTracker
from zd_agent.trigger.state_store import TicketState


def _confirmed_pending_effect(store: CaseworkEffectStore) -> str:
    effect = store.prepare(
        ticket_id=4101,
        delivery_attempt_id="casework:4101:event-7",
        generation=7,
        source_event_id="event-7",
        reply_type="solution",
        body="Customer-safe answer",
        internal_analysis="Typed casework audit",
        snapshot_customer_message_count=7,
        snapshot_customer_last_message_id="event-7",
        ownership_action="engineering",
        reason_code="confirmed_platform_defect",
        ownership_packet="Engineering must complete the correction.",
    )
    assert store.confirm_public_delivery(effect.reference)
    return effect.reference


@pytest.mark.asyncio
async def test_periodic_reconcile_retries_pending_casework_ownership_without_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    store = CaseworkEffectStore(tmp_path / "casework_effects.sqlite3")
    reference = _confirmed_pending_effect(store)
    state.set(
        4101,
        TicketState(
            state="ownership_pending",
            first_seen=datetime(2026, 8, 11, tzinfo=timezone.utc),
            active_session_id="session-owner-pending",
            delivery_attempt_id="casework:4101:event-7",
            ownership_action="engineering",
            ownership_reason="confirmed_platform_defect",
            ownership_effect_reference=reference,
            last_verdict="ownership_effect_pending",
        ),
    )
    notification_sender = AsyncMock(side_effect=[False, True])
    tag_sender = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "zd_agent.services.casework_notifications."
        "send_casework_ownership_notification_best_effort",
        notification_sender,
    )
    monkeypatch.setattr(
        "zd_agent.services.casework_notifications."
        "add_casework_ownership_tags_best_effort",
        tag_sender,
    )

    await _reconcile_post_public_internal_notes(state)

    after_first_pass = store.get(reference)
    assert after_first_pass is not None
    assert after_first_pass.notification_sent is False
    assert after_first_pass.zendesk_tag_sent is True
    assert store.pending_ownership_count() == 1
    pending_state = state.get(4101)
    assert pending_state is not None and pending_state.state == "ownership_pending"

    await _reconcile_post_public_internal_notes(state)

    after_second_pass = store.get(reference)
    assert after_second_pass is not None
    assert after_second_pass.complete is True
    assert store.pending_ownership_count() == 0
    assert notification_sender.await_count == 2
    assert tag_sender.await_count == 1
    owned_state = state.get(4101)
    assert owned_state is not None and owned_state.state == "owned"
    assert owned_state.ownership_effect_reference == reference


@pytest.mark.asyncio
async def test_periodic_reconcile_runs_bounded_casework_retention(
    tmp_path: Path,
) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    store = CaseworkEffectStore(
        tmp_path / "casework_effects.sqlite3",
        now_fn=lambda: 0.0,
    )
    completed = store.prepare(
        ticket_id=4102,
        delivery_attempt_id="old-completed",
        generation=1,
        source_event_id="event-1",
        reply_type="solution",
        body="Old completed answer",
        internal_analysis="old audit",
        snapshot_customer_message_count=1,
        snapshot_customer_last_message_id="event-1",
        ownership_action="none",
        reason_code="resolved_with_evidence",
        ownership_packet="",
    )
    assert store.confirm_public_delivery(completed.reference)

    await _reconcile_post_public_internal_notes(state)

    assert CaseworkEffectStore(store.path).get(completed.reference) is None


@pytest.mark.asyncio
async def test_periodic_reconcile_promotes_runtime_ownership_after_pending_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = TicketStateTracker(tmp_path / "tickets.json")
    store = CaseworkEffectStore(tmp_path / "casework_effects.sqlite3")
    effect = store.prepare(
        ticket_id=4103,
        delivery_attempt_id="D1:runtime-failure",
        generation=7,
        source_event_id="event-7",
        reply_type="none",
        body="",
        internal_analysis="runtime exhausted",
        snapshot_customer_message_count=None,
        snapshot_customer_last_message_id="event-7",
        ownership_action="support",
        reason_code="automation_retry_exhausted",
        ownership_packet="Support must finish this case.",
        delivery_required=False,
    )
    state.set(
        4103,
        TicketState(
            state="ownership_pending",
            first_seen=datetime(2026, 8, 11, tzinfo=timezone.utc),
            active_session_id="runtime-owner-token",
            delivery_attempt_id="D1",
            ownership_action="support",
            ownership_reason="automation_retry_exhausted",
            ownership_effect_reference=casework_effect_reference(
                4103,
                "D1:runtime-failure",
            ),
        ),
    )
    notification_sender = AsyncMock(side_effect=[False, True])
    tag_sender = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "zd_agent.services.casework_notifications."
        "send_casework_ownership_notification_best_effort",
        notification_sender,
    )
    monkeypatch.setattr(
        "zd_agent.services.casework_notifications."
        "add_casework_ownership_tags_best_effort",
        tag_sender,
    )

    await _reconcile_post_public_internal_notes(state)
    first = state.get(4103)
    assert first is not None and first.state == "ownership_pending"

    await _reconcile_post_public_internal_notes(state)
    owned = state.get(4103)
    persisted = store.get(effect.reference)

    assert persisted is not None and persisted.complete
    assert owned is not None and owned.state == "owned"
    assert owned.active_session_id == ""
    assert owned.ownership_effect_reference == effect.reference
    assert notification_sender.await_count == 2
    assert tag_sender.await_count == 1

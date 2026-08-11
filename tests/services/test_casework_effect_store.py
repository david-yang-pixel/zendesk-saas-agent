from pathlib import Path

import pytest

from zd_agent.services.casework_effect_store import (
    CASEWORK_EFFECT_RETENTION_SECONDS,
    CaseworkEffectStore,
)
from zd_agent.services.public_reply_delivery_store import PublicReplyDeliveryStore


def _prepare(store: CaseworkEffectStore, *, body: str = "First body"):
    return store.prepare(
        ticket_id=4001,
        delivery_attempt_id="attempt-1",
        generation=4,
        source_event_id="event-4",
        reply_type="solution",
        body=body,
        internal_analysis="typed audit",
        snapshot_customer_message_count=5,
        snapshot_customer_last_message_id="event-4",
        ownership_action="engineering",
        reason_code="confirmed_platform_defect",
        ownership_packet="actionable packet",
    )


@pytest.mark.parametrize(
    "overrides",
    (
        {"ownership_action": "agent_handoff"},
        {"reason_code": "not_a_reason"},
        {
            "ownership_action": "billing",
            "reason_code": "confirmed_platform_defect",
        },
        {"ownership_packet": ""},
        {"reply_type": "handoff"},
        {"reply_type": "none", "body": "customer text"},
    ),
)
def test_effect_store_rejects_invalid_typed_commands_before_persistence(
    tmp_path: Path,
    overrides: dict[str, str],
) -> None:
    store = CaseworkEffectStore(tmp_path / "effects.sqlite3")
    values = {
        "ticket_id": 4001,
        "delivery_attempt_id": "attempt-invalid",
        "generation": 4,
        "source_event_id": "event-4",
        "reply_type": "solution",
        "body": "Verified answer",
        "internal_analysis": "typed audit",
        "snapshot_customer_message_count": 5,
        "snapshot_customer_last_message_id": "event-4",
        "ownership_action": "engineering",
        "reason_code": "confirmed_platform_defect",
        "ownership_packet": "actionable packet",
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        store.prepare(**values)

    assert store.pending_ownership_count() == 0


def test_effect_store_freezes_reply_and_ownership_in_one_row(tmp_path: Path) -> None:
    store = CaseworkEffectStore(tmp_path / "effects.sqlite3")

    first = _prepare(store)
    repeated = _prepare(store, body="Changed after restart")

    assert repeated.reference == first.reference
    assert repeated.body == "First body"
    assert repeated.ownership_action == "engineering"
    assert repeated.reason_code == "confirmed_platform_defect"
    assert repeated.ownership_packet == "actionable packet"
    assert repeated.public_delivery_confirmed is False


def test_confirmed_effect_rolls_to_new_customer_message_generation(
    tmp_path: Path,
) -> None:
    store = CaseworkEffectStore(tmp_path / "effects.sqlite3")
    first = _prepare(store)
    assert store.confirm_public_delivery(first.reference)

    next_effect = store.prepare(
        ticket_id=4001,
        delivery_attempt_id="attempt-1",
        generation=5,
        source_event_id="event-5",
        reply_type="solution",
        body="Answer for the next customer message",
        internal_analysis="next audit",
        snapshot_customer_message_count=6,
        snapshot_customer_last_message_id="event-5",
        ownership_action="none",
        reason_code="resolved_with_evidence",
        ownership_packet="",
    )
    repeated = store.prepare(
        ticket_id=4001,
        delivery_attempt_id="attempt-1",
        generation=5,
        source_event_id="event-5",
        reply_type="solution",
        body="Regenerated text must stay frozen",
        internal_analysis="changed audit",
        snapshot_customer_message_count=6,
        snapshot_customer_last_message_id="event-5",
        ownership_action="engineering",
        reason_code="confirmed_platform_defect",
        ownership_packet="changed packet",
    )

    assert next_effect.reference != first.reference
    assert next_effect.delivery_attempt_id != first.delivery_attempt_id
    assert next_effect.body == "Answer for the next customer message"
    assert repeated == next_effect


@pytest.mark.parametrize("transport_state", ["pending", "ambiguous", "active_lease"])
def test_one_unresolved_descendant_blocks_later_delivery_generation(
    transport_state: str,
    tmp_path: Path,
) -> None:
    effect_store = CaseworkEffectStore(tmp_path / "effects.sqlite3")
    first = _prepare(effect_store)
    assert effect_store.confirm_public_delivery(first.reference)
    second = effect_store.prepare(
        ticket_id=4001,
        delivery_attempt_id="attempt-1",
        generation=5,
        source_event_id="event-5",
        reply_type="solution",
        body="Answer for event five",
        internal_analysis="event five audit",
        snapshot_customer_message_count=6,
        snapshot_customer_last_message_id="event-5",
        ownership_action="none",
        reason_code="resolved_with_evidence",
        ownership_packet="",
    )
    delivery_store = PublicReplyDeliveryStore(
        tmp_path / "public.sqlite3",
        claim_lease_seconds=300,
    )
    public_attempt = delivery_store.prepare(
        delivery_attempt_id=second.delivery_attempt_id,
        ticket_id=4001,
        reply_type="solution",
        body=second.body,
        snapshot_customer_message_count=6,
        snapshot_customer_last_message_id="event-5",
    )
    if transport_state in {"ambiguous", "active_lease"}:
        token = delivery_store.claim(public_attempt.delivery_key)
        assert token is not None
        if transport_state == "ambiguous":
            assert delivery_store.begin_send(public_attempt.delivery_key, token)

    attempted_third = effect_store.prepare(
        ticket_id=4001,
        delivery_attempt_id="attempt-1",
        generation=6,
        source_event_id="event-6",
        reply_type="solution",
        body="This D3 must remain blocked while D2 is unresolved",
        internal_analysis="event six audit",
        snapshot_customer_message_count=7,
        snapshot_customer_last_message_id="event-6",
        ownership_action="none",
        reason_code="resolved_with_evidence",
        ownership_packet="",
    )

    assert attempted_third == second
    assert attempted_third.snapshot_customer_last_message_id == "event-5"
    assert effect_store.confirm_public_delivery(second.reference)
    third = effect_store.prepare(
        ticket_id=4001,
        delivery_attempt_id="attempt-1",
        generation=6,
        source_event_id="event-6",
        reply_type="solution",
        body="Answer for event six",
        internal_analysis="event six audit",
        snapshot_customer_message_count=7,
        snapshot_customer_last_message_id="event-6",
        ownership_action="none",
        reason_code="resolved_with_evidence",
        ownership_packet="",
    )
    assert third.reference != second.reference
    assert third.snapshot_customer_last_message_id == "event-6"


def test_ownership_cannot_be_claimed_until_public_delivery_is_confirmed(
    tmp_path: Path,
) -> None:
    store = CaseworkEffectStore(tmp_path / "effects.sqlite3")
    pending = _prepare(store)

    assert store.claim_ownership(pending.reference) is None
    assert store.confirm_public_delivery(pending.reference) is True
    token = store.claim_ownership(pending.reference)

    assert token
    assert store.mark_ownership_effect(
        pending.reference,
        token,
        "notification_sent",
    )
    assert store.mark_ownership_effect(
        pending.reference,
        token,
        "zendesk_tag_sent",
    )
    store.release(pending.reference, token)
    completed = store.get(pending.reference)
    assert completed is not None
    assert completed.complete is True


def test_definitely_unsent_effect_can_be_atomically_replaced(tmp_path: Path) -> None:
    store = CaseworkEffectStore(tmp_path / "effects.sqlite3")
    stale = _prepare(store)

    assert store.discard_definitely_unsent(stale.reference) is True
    assert store.get(stale.reference) is None

    fresh = store.prepare(
        ticket_id=4001,
        delivery_attempt_id="attempt-1",
        generation=5,
        source_event_id="event-5",
        reply_type="solution",
        body="Answer for the current customer message",
        internal_analysis="current audit",
        snapshot_customer_message_count=6,
        snapshot_customer_last_message_id="event-5",
        ownership_action="none",
        reason_code="resolved_with_evidence",
        ownership_packet="",
    )

    assert fresh.snapshot_customer_last_message_id == "event-5"
    assert fresh.body == "Answer for the current customer message"


def test_confirmed_effect_cannot_be_discarded_as_definitely_unsent(
    tmp_path: Path,
) -> None:
    store = CaseworkEffectStore(tmp_path / "effects.sqlite3")
    delivered = _prepare(store)
    assert store.confirm_public_delivery(delivered.reference)

    assert store.discard_definitely_unsent(delivered.reference) is False
    assert store.get(delivered.reference) is not None


def test_ownership_only_command_is_durable_without_customer_delivery(
    tmp_path: Path,
) -> None:
    store = CaseworkEffectStore(tmp_path / "effects.sqlite3")

    pending = store.prepare(
        ticket_id=4001,
        delivery_attempt_id="attempt-1:runtime-failure",
        generation=4,
        source_event_id="event-4",
        reply_type="none",
        body="",
        internal_analysis="v2 runtime retry exhausted",
        snapshot_customer_message_count=5,
        snapshot_customer_last_message_id="event-4",
        ownership_action="support",
        reason_code="automation_retry_exhausted",
        ownership_packet="Support must finish the investigation.",
        delivery_required=False,
    )

    assert pending.delivery_required is False
    assert pending.public_delivery_confirmed is False
    assert pending.ownership_ready is True
    assert store.pending_ownership() == [pending]
    assert store.claim_ownership(pending.reference)


def test_retention_purges_only_completed_effects(tmp_path: Path) -> None:
    now = [1_000.0]
    store = CaseworkEffectStore(
        tmp_path / "effects.sqlite3",
        now_fn=lambda: now[0],
    )
    completed = _prepare(store)
    assert store.confirm_public_delivery(completed.reference)
    token = store.claim_ownership(completed.reference)
    assert token is not None
    assert store.mark_ownership_effect(
        completed.reference,
        token,
        "notification_sent",
    )
    assert store.mark_ownership_effect(
        completed.reference,
        token,
        "zendesk_tag_sent",
    )
    unresolved = store.prepare(
        ticket_id=4002,
        delivery_attempt_id="attempt-pending",
        generation=1,
        source_event_id="event-pending",
        reply_type="solution",
        body="Pending customer answer",
        internal_analysis="pending audit",
        snapshot_customer_message_count=1,
        snapshot_customer_last_message_id="event-pending",
        ownership_action="none",
        reason_code="resolved_with_evidence",
        ownership_packet="",
    )
    transport_store = PublicReplyDeliveryStore(tmp_path / "public.sqlite3")
    transport = transport_store.prepare(
        delivery_attempt_id=unresolved.delivery_attempt_id,
        ticket_id=unresolved.ticket_id,
        reply_type="solution",
        body=unresolved.body,
        snapshot_customer_message_count=1,
        snapshot_customer_last_message_id="event-pending",
    )
    transport_token = transport_store.claim(transport.delivery_key)
    assert transport_token is not None
    assert transport_store.begin_send(transport.delivery_key, transport_token)

    now[0] += CASEWORK_EFFECT_RETENTION_SECONDS - 1
    assert store.purge_completed() == 0
    now[0] += 2
    assert store.purge_completed() == 1

    assert store.get(completed.reference) is None
    assert store.get(unresolved.reference) == unresolved
    ambiguous_transport = transport_store.get(
        delivery_attempt_id=unresolved.delivery_attempt_id,
        ticket_id=unresolved.ticket_id,
    )
    assert ambiguous_transport is not None and ambiguous_transport.ambiguous

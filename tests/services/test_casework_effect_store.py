from pathlib import Path

from zd_agent.services.casework_effect_store import CaseworkEffectStore


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

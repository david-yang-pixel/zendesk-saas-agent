from pathlib import Path

from zd_agent.services.casework_effect_store import CaseworkEffectStore
from zd_agent.services.casework_ownership_effects import (
    ensure_casework_ownership_effects,
    reconcile_casework_ownership_effects,
)


def _effect(store: CaseworkEffectStore):
    effect = store.prepare(
        ticket_id=4001,
        delivery_attempt_id="attempt-1",
        generation=4,
        source_event_id="event-4",
        reply_type="solution",
        body="Customer-safe answer",
        internal_analysis="typed audit",
        snapshot_customer_message_count=5,
        snapshot_customer_last_message_id="event-4",
        ownership_action="engineering",
        reason_code="confirmed_platform_defect",
        ownership_packet="Target: engineering\nReason: confirmed_platform_defect",
    )
    assert store.confirm_public_delivery(effect.reference)
    current = store.get(effect.reference)
    assert current is not None
    return current


async def test_typed_ownership_effects_complete_after_confirmed_public_delivery(
    tmp_path: Path,
) -> None:
    store = CaseworkEffectStore(tmp_path / "effects.sqlite3")
    effect = _effect(store)
    notifications = []
    tags = []

    async def notify(**kwargs) -> bool:
        notifications.append(kwargs)
        return True

    async def tag(**kwargs) -> bool:
        tags.append(kwargs)
        return True

    completed = await ensure_casework_ownership_effects(
        effect=effect,
        store=store,
        notification_sender=notify,
        tag_sender=tag,
    )

    assert completed is True
    assert notifications[0]["reason"].startswith("Target: engineering")
    assert "casework_v2:engineering" in notifications[0]["source"]
    assert tags == [{"ticket_id": 4001}]
    persisted = store.get(effect.reference)
    assert persisted is not None and persisted.complete is True


async def test_notification_exception_is_fenced_from_automatic_duplicate(
    tmp_path: Path,
) -> None:
    store = CaseworkEffectStore(tmp_path / "effects.sqlite3")
    effect = _effect(store)
    notification_calls = 0

    async def ambiguous_notify(**kwargs) -> bool:
        nonlocal notification_calls
        notification_calls += 1
        raise TimeoutError("remote outcome unknown")

    async def tag(**kwargs) -> bool:
        return True

    first = await ensure_casework_ownership_effects(
        effect=effect,
        store=store,
        notification_sender=ambiguous_notify,
        tag_sender=tag,
    )
    second = await ensure_casework_ownership_effects(
        effect=effect,
        store=store,
        notification_sender=ambiguous_notify,
        tag_sender=tag,
    )

    assert first is False
    assert second is False
    assert notification_calls == 1
    persisted = store.get(effect.reference)
    assert persisted is not None
    assert persisted.notification_ambiguous is True
    assert persisted.zendesk_tag_sent is True


async def test_startup_reconciliation_processes_confirmed_pending_ownership(
    tmp_path: Path,
) -> None:
    store = CaseworkEffectStore(tmp_path / "effects.sqlite3")
    _effect(store)

    async def notify(**kwargs) -> bool:
        return True

    async def tag(**kwargs) -> bool:
        return True

    count = await reconcile_casework_ownership_effects(
        store=store,
        notification_sender=notify,
        tag_sender=tag,
    )

    assert count == 1
    assert store.pending_ownership() == []

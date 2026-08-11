from pathlib import Path

from zd_agent.services.casework_effect_store import CaseworkEffectStore
from zd_agent.services.casework_runtime_failures import (
    persist_runtime_failure_ownership,
)


async def test_runtime_failure_ownership_is_typed_and_replayable(
    tmp_path: Path,
) -> None:
    notifications = []
    tags = []

    async def notify(**kwargs) -> bool:
        notifications.append(kwargs)
        return True

    async def tag(**kwargs) -> bool:
        tags.append(kwargs)
        return True

    receipt = await persist_runtime_failure_ownership(
        state_dir=tmp_path,
        ticket_id=4001,
        delivery_attempt_id="delivery-1",
        generation=7,
        source_event_id="event-7",
        snapshot_customer_message_count=3,
        snapshot_customer_last_message_id="event-7",
        automation_attempt=3,
        failure_type="TimeoutError",
        notification_sender=notify,
        tag_sender=tag,
    )

    assert receipt.ownership_action == "support"
    assert receipt.reason_code == "automation_retry_exhausted"
    assert receipt.ownership_state == "confirmed"
    assert receipt.reference.startswith("casework-effect:")
    assert notifications[0]["customer_reply"] == ""
    assert tags[0]["reason_code"] == "automation_retry_exhausted"

    persisted = CaseworkEffectStore(
        tmp_path / "casework_effects.sqlite3",
    ).get(receipt.reference)
    assert persisted is not None
    assert persisted.delivery_required is False
    assert persisted.complete is True


async def test_pre_snapshot_runtime_failure_uses_a_durable_synthetic_cursor(
    tmp_path: Path,
) -> None:
    async def succeed(**_kwargs) -> bool:
        return True

    receipt = await persist_runtime_failure_ownership(
        state_dir=tmp_path,
        ticket_id=4002,
        delivery_attempt_id="delivery-before-snapshot",
        generation=0,
        source_event_id="",
        snapshot_customer_message_count=None,
        snapshot_customer_last_message_id="",
        automation_attempt=3,
        failure_type="TicketLoadError",
        notification_sender=succeed,
        tag_sender=succeed,
    )

    persisted = CaseworkEffectStore(
        tmp_path / "casework_effects.sqlite3",
    ).get(receipt.reference)
    assert persisted is not None
    assert persisted.source_event_id == ("runtime-failure:delivery-before-snapshot")
    assert persisted.snapshot_customer_last_message_id == (
        "runtime-failure:delivery-before-snapshot"
    )
    assert persisted.complete is True

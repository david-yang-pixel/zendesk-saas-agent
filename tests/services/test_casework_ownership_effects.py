import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from zd_agent.services.casework_effect_store import (
    CaseworkEffectStore,
    casework_effect_reference,
)
from zd_agent.services.casework_ownership_effects import (
    ensure_casework_ownership_effects,
    reconcile_casework_ownership_effects,
)
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


def _runtime_failure_effect(store: CaseworkEffectStore):
    return store.prepare(
        ticket_id=4002,
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


def _pending_runtime_state(path: Path) -> TicketStateTracker:
    state = TicketStateTracker(path)
    state.set(
        4002,
        TicketState(
            state="ownership_pending",
            first_seen=datetime(2026, 8, 11, tzinfo=timezone.utc),
            active_session_id="runtime-owner-token",
            delivery_attempt_id="D1",
            ownership_action="support",
            ownership_reason="automation_retry_exhausted",
            ownership_effect_reference=casework_effect_reference(
                4002,
                "D1:runtime-failure",
            ),
        ),
    )
    return state


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
    assert notifications[0]["ownership_action"] == "engineering"
    assert notifications[0]["reason_code"] == "confirmed_platform_defect"
    assert notifications[0]["ownership_packet"].startswith("Target: engineering")
    assert notifications[0]["customer_reply"] == "Customer-safe answer"
    assert "casework_v2:engineering" in notifications[0]["source"]
    assert tags == [
        {
            "ticket_id": 4001,
            "ownership_action": "engineering",
            "reason_code": "confirmed_platform_defect",
        },
    ]
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


async def test_production_notification_response_loss_is_not_reissued(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from zd_agent import config as config_module
    from zd_agent.config import AppConfig
    from zd_agent.integrations import tool_service_mcp

    store = CaseworkEffectStore(tmp_path / "effects.sqlite3")
    effect = _effect(store)
    cfg = AppConfig()
    cfg.zendesk.subdomain = "example"
    cfg.notification_service.mcp_url = "https://notify.example.test/mcp"
    cfg.notification_service.mcp_api_key = "secret"
    accepted_requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        rpc = json.loads(request.content.decode("utf-8"))
        assert rpc["params"]["name"] == "feishu_push"
        accepted_requests.append(rpc["params"]["arguments"])
        raise httpx.ReadTimeout("response lost after accept", request=request)

    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(config_module, "get_config", lambda: cfg)
    monkeypatch.setattr(
        tool_service_mcp.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(transport=transport, **kwargs),
    )

    async def tag(**kwargs) -> bool:
        return True

    first = await ensure_casework_ownership_effects(
        effect=effect,
        store=store,
        tag_sender=tag,
    )
    second = await ensure_casework_ownership_effects(
        effect=effect,
        store=store,
        tag_sender=tag,
    )

    assert first is False
    assert second is False
    assert len(accepted_requests) == 1
    assert accepted_requests[0]["idempotency_reference"] == effect.reference
    persisted = store.get(effect.reference)
    assert persisted is not None
    assert persisted.notification_ambiguous is True


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


async def test_ownership_only_command_does_not_require_a_customer_reply(
    tmp_path: Path,
) -> None:
    store = CaseworkEffectStore(tmp_path / "effects.sqlite3")
    effect = store.prepare(
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
    notifications = []

    async def notify(**kwargs) -> bool:
        notifications.append(kwargs)
        return True

    async def tag(**kwargs) -> bool:
        return True

    completed = await ensure_casework_ownership_effects(
        effect=effect,
        store=store,
        notification_sender=notify,
        tag_sender=tag,
    )

    assert completed is True
    assert notifications[0]["customer_reply"] == ""
    persisted = store.get(effect.reference)
    assert persisted is not None and persisted.complete is True


async def test_completed_runtime_effect_promotes_matching_pending_state(
    tmp_path: Path,
) -> None:
    store = CaseworkEffectStore(tmp_path / "effects.sqlite3")
    effect = _runtime_failure_effect(store)
    token = store.claim_ownership(effect.reference)
    assert token is not None
    assert store.mark_ownership_effect(effect.reference, token, "notification_sent")
    assert store.mark_ownership_effect(effect.reference, token, "zendesk_tag_sent")
    state = _pending_runtime_state(tmp_path / "tickets.json")

    reconciled = await reconcile_casework_ownership_effects(
        store=store,
        state=state,
    )

    assert reconciled == 1
    owned = state.get(4002)
    assert owned is not None
    assert owned.state == "owned"
    assert owned.active_session_id == ""
    assert owned.ownership_effect_reference == effect.reference
    assert owned.ownership_action == "support"
    assert owned.ownership_reason == "automation_retry_exhausted"


async def test_runtime_effect_reconcile_cannot_overwrite_newer_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = CaseworkEffectStore(tmp_path / "effects.sqlite3")
    effect = _runtime_failure_effect(store)
    token = store.claim_ownership(effect.reference)
    assert token is not None
    assert store.mark_ownership_effect(effect.reference, token, "notification_sent")
    assert store.mark_ownership_effect(effect.reference, token, "zendesk_tag_sent")
    state = _pending_runtime_state(tmp_path / "tickets.json")
    original_get = store.get

    def race_get(reference: str):
        newer = state.get(4002)
        assert newer is not None
        newer.state = "awaiting_customer"
        newer.active_session_id = ""
        newer.delivery_attempt_id = "D2"
        newer.ownership_action = ""
        newer.ownership_reason = ""
        newer.ownership_effect_reference = ""
        state.set(4002, newer)
        return original_get(reference)

    monkeypatch.setattr(store, "get", race_get)

    reconciled = await reconcile_casework_ownership_effects(
        store=store,
        state=state,
    )

    assert reconciled == 0
    current = state.get(4002)
    assert current is not None
    assert current.state == "awaiting_customer"
    assert current.delivery_attempt_id == "D2"

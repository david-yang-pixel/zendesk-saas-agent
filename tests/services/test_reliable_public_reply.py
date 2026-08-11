from __future__ import annotations

import hashlib
import sqlite3
import time
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from zd_agent.config import AppConfig, PublicReplyConfig
from zd_agent.runtime.pre_send_check import (
    FreshnessRecovery,
    FreshnessRecoveryAction,
    FreshnessResult,
)
from zd_agent.services.public_reply_delivery_store import PublicReplyDeliveryStore
from zd_agent.services.public_reply_internal_note import PostPublicInternalNoteStore
from zd_agent.services.reliable_public_reply import (
    ReliablePublicReplyType,
    deliver_prepared_public_reply,
    prepare_reliable_public_reply,
    send_casework_public_reply_with_freshness,
)
from zd_agent.tools.public_reply_format import _append_footer


TICKET_ID = 4101
ATTEMPT_ID = "casework:4101:event-7"


def _config(tmp_path) -> AppConfig:
    cfg = AppConfig(
        public_reply=PublicReplyConfig(
            enabled=True,
            browser_relay_url=("https://relay.example.test/zendesk-reply-mcp"),
            browser_relay_api_key="relay-secret",
            freshness_callback_url=(
                "https://relay.example.test/webhook/relay-pre-send-freshness"
            ),
        ),
    )
    cfg.resources.base_dir = str(tmp_path)
    cfg.resources.state_dir = ".state"
    return cfg


def _patch_safe_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cfg: AppConfig,
    readiness: FreshnessResult | None = None,
    relay: AsyncMock | None = None,
    zendesk: AsyncMock | None = None,
) -> tuple[AsyncMock, AsyncMock]:
    relay = relay or AsyncMock()
    zendesk = zendesk or AsyncMock(
        return_value={"success": True, "data": {"comments": []}},
    )
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply.get_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply._check_freshness_before_relay",
        AsyncMock(
            return_value=readiness
            or FreshnessResult(continue_send=True, reason="fresh"),
        ),
    )
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply.send_browser_relay_reply",
        relay,
    )
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply.call_zendesk_tool",
        zendesk,
    )
    return relay, zendesk


@pytest.mark.asyncio
async def test_casework_rejects_requested_handoff_before_context_or_outbox(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    context = MagicMock(side_effect=AssertionError("context must not be read"))
    store_factory = MagicMock(side_effect=AssertionError("outbox must not open"))
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply.get_messaging_context",
        context,
    )
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply._delivery_store_for_config",
        store_factory,
    )
    relay, _ = _patch_safe_boundaries(monkeypatch, cfg=cfg)

    result = await send_casework_public_reply_with_freshness(
        ticket_id=TICKET_ID,
        body="A support engineer will follow up.",
        reply_type=cast(ReliablePublicReplyType, "handoff"),
        conversation_id=None,
        snapshot_customer_message_count=1,
        snapshot_customer_last_message_id="m1",
        delivery_attempt_id=ATTEMPT_ID,
        internal_analysis="Typed ownership belongs to casework.",
    )

    assert result.success is False
    assert "reply_type" in result.message
    context.assert_not_called()
    store_factory.assert_not_called()
    relay.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    (
        "请提供错误信息。",
        "Confirmamos que su suscripción está activa.",
    ),
)
async def test_non_english_reply_is_blocked_before_context_or_outbox(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
) -> None:
    cfg = _config(tmp_path)
    context = MagicMock(side_effect=AssertionError("context must not be read"))
    store_factory = MagicMock(side_effect=AssertionError("outbox must not open"))
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply.get_messaging_context",
        context,
    )
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply._delivery_store_for_config",
        store_factory,
    )
    relay, _ = _patch_safe_boundaries(monkeypatch, cfg=cfg)

    result = await send_casework_public_reply_with_freshness(
        ticket_id=TICKET_ID,
        body=body,
        reply_type="ask",
        conversation_id=None,
        snapshot_customer_message_count=1,
        snapshot_customer_last_message_id="m1",
        delivery_attempt_id=ATTEMPT_ID,
        internal_analysis="The model returned non-English customer text.",
    )

    assert result.success is False
    assert "English" in result.message
    context.assert_not_called()
    store_factory.assert_not_called()
    relay.assert_not_awaited()


@pytest.mark.asyncio
async def test_kill_switch_returns_typed_paused_without_delivery_side_effects(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    cfg.public_reply.enabled = False
    context = MagicMock(side_effect=AssertionError("context must not be read"))
    store_factory = MagicMock(side_effect=AssertionError("outbox must not open"))
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply.get_messaging_context",
        context,
    )
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply._delivery_store_for_config",
        store_factory,
    )
    relay, _ = _patch_safe_boundaries(monkeypatch, cfg=cfg)

    result = await send_casework_public_reply_with_freshness(
        ticket_id=TICKET_ID,
        body="This valid reply remains frozen until resume.",
        reply_type="solution",
        snapshot_customer_message_count=1,
        snapshot_customer_last_message_id="m1",
        delivery_attempt_id=ATTEMPT_ID,
        internal_analysis="Paused by operator.",
    )

    assert result.success is False
    assert result.paused is True
    context.assert_not_called()
    store_factory.assert_not_called()
    relay.assert_not_awaited()
    assert not (tmp_path / ".state").exists()


@pytest.mark.asyncio
async def test_casework_reconciles_frozen_legacy_handoff_without_creating_legacy_intent(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    outbox_path = state_dir / "public_reply_outbox.sqlite3"
    legacy_body = _append_footer("A human will follow up.", "handoff")
    legacy_key = hashlib.sha256(
        f"v1\0{ATTEMPT_ID}\0{TICKET_ID}\0handoff".encode(),
    ).hexdigest()
    with sqlite3.connect(outbox_path) as connection:
        connection.execute(
            "CREATE TABLE public_reply_deliveries ("
            "delivery_key TEXT PRIMARY KEY,body TEXT NOT NULL,"
            "created_at REAL NOT NULL,delivered_at REAL)"
        )
        connection.execute(
            "INSERT INTO public_reply_deliveries("
            "delivery_key,body,created_at,delivered_at) VALUES (?,?,?,?)",
            (legacy_key, legacy_body, 100.0, 101.0),
        )
    relay, _ = _patch_safe_boundaries(monkeypatch, cfg=cfg)

    prepared = await prepare_reliable_public_reply(
        ticket_id=TICKET_ID,
        body="A regenerated solution must not replace the frozen body.",
        reply_type="solution",
        conversation_id="conversation-4101",
        snapshot_customer_message_count=7,
        snapshot_customer_last_message_id="m7",
        delivery_attempt_id=ATTEMPT_ID,
        internal_analysis="Reconcile the frozen legacy delivery.",
    )

    assert prepared.requested_reply_type == "solution"
    assert prepared.effective_reply_type == "handoff"
    assert prepared.body_with_footer == legacy_body
    assert not (state_dir / "post_public_handoff_outbox.sqlite3").exists()

    result = await deliver_prepared_public_reply(prepared)

    assert result.success is False
    assert result.historical_delivery_confirmed is True
    assert result.covers_requested_snapshot is False
    relay.assert_not_awaited()
    assert not (state_dir / "post_public_handoff_outbox.sqlite3").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_state", ["pending", "ambiguous", "active_lease"])
async def test_casework_quarantines_undelivered_legacy_handoff_before_transport(
    legacy_state: str,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    outbox_path = state_dir / "public_reply_outbox.sqlite3"
    PublicReplyDeliveryStore(outbox_path)
    legacy_key = hashlib.sha256(
        f"v1\0{ATTEMPT_ID}\0{TICKET_ID}\0handoff".encode(),
    ).hexdigest()
    with sqlite3.connect(outbox_path) as connection:
        connection.execute(
            "INSERT INTO public_reply_deliveries("
            "delivery_key,reply_type,body,internal_analysis,created_at,"
            "ambiguous_at,claim_token,lease_until) VALUES (?,?,?,?,?,?,?,?)",
            (
                legacy_key,
                "handoff",
                _append_footer("A human will follow up.", "handoff"),
                "legacy handoff",
                time.time() - 60,
                time.time() - 30 if legacy_state == "ambiguous" else None,
                "active-token" if legacy_state == "active_lease" else "",
                time.time() + 600 if legacy_state == "active_lease" else 0,
            ),
        )
    relay, zendesk = _patch_safe_boundaries(monkeypatch, cfg=cfg)

    result = await send_casework_public_reply_with_freshness(
        ticket_id=TICKET_ID,
        body="Regenerated customer-resolvable answer.",
        reply_type="solution",
        conversation_id="conversation-4101",
        snapshot_customer_message_count=7,
        snapshot_customer_last_message_id="m7",
        delivery_attempt_id=ATTEMPT_ID,
        internal_analysis="The legacy handoff must not be sent by v2.",
    )

    assert result.success is False
    assert any(
        marker in result.message.casefold()
        for marker in ("handoff", "legacy public reply")
    )
    relay.assert_not_awaited()
    zendesk.assert_not_awaited()
    assert not (state_dir / "post_public_internal_note_outbox.sqlite3").exists()
    assert not (state_dir / "post_public_handoff_outbox.sqlite3").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup_fails", [False, True])
async def test_exact_ambiguous_attempt_blocks_before_freshness_without_remote_proof(
    lookup_fails: bool,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    outbox = PublicReplyDeliveryStore(
        tmp_path / ".state" / "public_reply_outbox.sqlite3",
    )
    frozen = outbox.prepare(
        delivery_attempt_id=ATTEMPT_ID,
        ticket_id=TICKET_ID,
        reply_type="solution",
        body=_append_footer("Frozen possible send.", "solution"),
        internal_analysis="frozen audit",
        snapshot_customer_message_count=1,
        snapshot_customer_last_message_id="m1",
    )
    token = outbox.claim(frozen.delivery_key)
    assert token is not None
    assert outbox.begin_send(frozen.delivery_key, token)
    readiness = AsyncMock(
        side_effect=AssertionError("freshness must follow ambiguous reconciliation")
    )
    zendesk = AsyncMock(
        return_value=(
            {"success": False, "code": "LOOKUP_FAILED", "error": "unavailable"}
            if lookup_fails
            else {"success": True, "data": {"comments": []}}
        ),
    )
    relay, _ = _patch_safe_boundaries(
        monkeypatch,
        cfg=cfg,
        relay=AsyncMock(),
        zendesk=zendesk,
    )
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply._check_freshness_before_relay",
        readiness,
    )

    result = await send_casework_public_reply_with_freshness(
        ticket_id=TICKET_ID,
        body="Regenerated answer must not send.",
        reply_type="solution",
        conversation_id="conversation-4101",
        snapshot_customer_message_count=2,
        snapshot_customer_last_message_id="m2",
        delivery_attempt_id=ATTEMPT_ID,
        internal_analysis="new audit",
    )

    assert result.success is False
    assert "ambiguous" in result.message.casefold()
    assert "manual reconciliation" in result.message.casefold()
    readiness.assert_not_awaited()
    relay.assert_not_awaited()
    persisted = outbox.get(
        delivery_attempt_id=ATTEMPT_ID,
        ticket_id=TICKET_ID,
    )
    assert persisted is not None and persisted.ambiguous


@pytest.mark.asyncio
async def test_privacy_blocker_prevents_all_delivery_side_effects(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    context = MagicMock(side_effect=AssertionError("context must not be read"))
    readiness = AsyncMock(side_effect=AssertionError("freshness must not run"))
    store_factory = MagicMock(side_effect=AssertionError("outbox must not open"))
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply.get_messaging_context",
        context,
    )
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply._check_freshness_before_relay",
        readiness,
    )
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply._delivery_store_for_config",
        store_factory,
    )
    relay, _ = _patch_safe_boundaries(monkeypatch, cfg=cfg)
    monkeypatch.setattr(
        "zd_agent.services.reliable_public_reply._check_freshness_before_relay",
        readiness,
    )

    result = await send_casework_public_reply_with_freshness(
        ticket_id=TICKET_ID,
        body="See src/zd_agent/runtime/runner.py for the root cause.",
        reply_type="solution",
        conversation_id=None,
        snapshot_customer_message_count=1,
        snapshot_customer_last_message_id="m1",
        delivery_attempt_id=ATTEMPT_ID,
        internal_analysis="Must remain private.",
    )

    assert result.success is False
    assert "privacy hard gate" in result.message
    context.assert_not_called()
    readiness.assert_not_awaited()
    store_factory.assert_not_called()
    relay.assert_not_awaited()
    assert not (tmp_path / ".state").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["stale_customer_context", "pending_blocking_form"])
async def test_freshness_or_pending_form_blocks_before_relay(
    reason: str,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    readiness = FreshnessResult(
        continue_send=False,
        reason=reason,
        new_messages_count=1,
        recovery=FreshnessRecovery(
            action=FreshnessRecoveryAction.DEFER,
            reason=reason,
        ),
    )
    relay, _ = _patch_safe_boundaries(
        monkeypatch,
        cfg=cfg,
        readiness=readiness,
    )

    result = await send_casework_public_reply_with_freshness(
        ticket_id=TICKET_ID,
        body="Please retry the request now.",
        reply_type="solution",
        conversation_id="conversation-4101",
        snapshot_customer_message_count=6,
        snapshot_customer_last_message_id="m6",
        delivery_attempt_id=ATTEMPT_ID,
        internal_analysis="The customer snapshot must stay current.",
    )

    assert result.success is False
    assert (
        "pending" in result.message.casefold()
        or "freshness" in result.message.casefold()
    )
    relay.assert_not_awaited()
    assert not (tmp_path / ".state").exists()


@pytest.mark.asyncio
async def test_relay_receives_snapshot_and_freshness_callback(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    relay, _ = _patch_safe_boundaries(monkeypatch, cfg=cfg)

    result = await send_casework_public_reply_with_freshness(
        ticket_id=TICKET_ID,
        body="Please retry the request now.",
        reply_type="solution",
        conversation_id="conversation-4101",
        snapshot_customer_message_count=6,
        snapshot_customer_last_message_id="m6",
        delivery_attempt_id=ATTEMPT_ID,
        internal_analysis="Evidence confirms the request can be retried.",
    )

    assert result.success is True
    relay.assert_awaited_once()
    assert relay.await_args is not None
    relay_args = relay.await_args.kwargs
    assert relay_args["snapshot_customer_message_count"] == 6
    assert relay_args["snapshot_customer_last_message_id"] == "m6"
    assert relay_args["freshness_check_url"] == (
        "https://relay.example.test/webhook/relay-pre-send-freshness"
    )
    assert relay_args["freshness_check_api_key"] == "relay-secret"


@pytest.mark.asyncio
async def test_delivered_attempt_does_not_claim_a_newer_snapshot_is_answered(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    relay, _ = _patch_safe_boundaries(monkeypatch, cfg=cfg)

    first = await send_casework_public_reply_with_freshness(
        ticket_id=TICKET_ID,
        body="Answer for the pre-crash customer message.",
        reply_type="solution",
        conversation_id="conversation-4101",
        snapshot_customer_message_count=6,
        snapshot_customer_last_message_id="m6",
        delivery_attempt_id=ATTEMPT_ID,
        internal_analysis="Delivered immediately before process loss.",
    )
    recovered = await send_casework_public_reply_with_freshness(
        ticket_id=TICKET_ID,
        body="Regenerated answer after a downtime customer message.",
        reply_type="solution",
        conversation_id="conversation-4101",
        snapshot_customer_message_count=7,
        snapshot_customer_last_message_id="m7",
        delivery_attempt_id=ATTEMPT_ID,
        internal_analysis="The new customer message still needs a new generation.",
    )

    assert first.success is True
    assert recovered.success is False
    assert recovered.historical_delivery_confirmed is True
    assert recovered.covers_requested_snapshot is False
    assert recovered.accepted_snapshot_count == 6
    assert recovered.accepted_snapshot_last_id == "m6"
    assert "remains unanswered" in recovered.message
    assert relay.await_count == 1


@pytest.mark.asyncio
async def test_confirmed_casework_reply_emits_delivery_metric(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    _patch_safe_boundaries(monkeypatch, cfg=cfg)
    emit = MagicMock()
    monkeypatch.setattr(
        "zd_agent.observability.metrics.emit_agent_public_reply_sent",
        emit,
    )

    result = await send_casework_public_reply_with_freshness(
        ticket_id=TICKET_ID,
        body="Please retry the request now.",
        reply_type="solution",
        conversation_id="conversation-4101",
        snapshot_customer_message_count=6,
        snapshot_customer_last_message_id="m6",
        delivery_attempt_id=ATTEMPT_ID,
        internal_analysis="Evidence confirms the request can be retried.",
    )

    assert result.success is True
    emit.assert_called_once_with(
        ticket_id=TICKET_ID,
        reply_type="solution",
        body_chars=len("Please retry the request now."),
    )


@pytest.mark.asyncio
async def test_internal_note_intent_exists_before_relay_and_survives_note_failure(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    state_dir = tmp_path / ".state"
    note_path = state_dir / "post_public_internal_note_outbox.sqlite3"
    intent_seen_before_relay = False

    async def relay_side_effect(**_kwargs: Any) -> None:
        nonlocal intent_seen_before_relay
        pending = PostPublicInternalNoteStore(note_path).pending()
        intent_seen_before_relay = (
            len(pending) == 1 and not pending[0].public_delivery_confirmed
        )

    async def zendesk_side_effect(name: str, _arguments: dict[str, Any]) -> dict:
        if name == "get_ticket_comments":
            return {"success": True, "data": {"comments": []}}
        if name == "add_internal_note":
            return {"success": False, "code": "UNAVAILABLE", "error": "down"}
        raise AssertionError(name)

    relay = AsyncMock(side_effect=relay_side_effect)
    zendesk = AsyncMock(side_effect=zendesk_side_effect)
    _patch_safe_boundaries(
        monkeypatch,
        cfg=cfg,
        relay=relay,
        zendesk=zendesk,
    )

    result = await send_casework_public_reply_with_freshness(
        ticket_id=TICKET_ID,
        body="Please retry the request now.",
        reply_type="solution",
        conversation_id="conversation-4101",
        snapshot_customer_message_count=6,
        snapshot_customer_last_message_id="m6",
        delivery_attempt_id=ATTEMPT_ID,
        internal_analysis="The internal analysis must survive a note outage.",
    )

    assert result.success is True
    assert "internal analysis note delivery failed" in result.message
    assert intent_seen_before_relay is True
    pending = PostPublicInternalNoteStore(note_path).pending()
    assert len(pending) == 1
    assert pending[0].public_delivery_confirmed is True
    assert pending[0].internal_analysis == (
        "The internal analysis must survive a note outage."
    )
    assert not (state_dir / "post_public_handoff_outbox.sqlite3").exists()


def test_reliable_service_has_no_legacy_handoff_effect_import() -> None:
    import ast
    from pathlib import Path

    path = (
        Path(__file__).parents[2]
        / "src"
        / "zd_agent"
        / "services"
        / "reliable_public_reply.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "zd_agent.services.public_reply_handoff_effects" not in modules
    assert not any(
        module == "zd_agent.tools.zendesk_public_reply" for module in modules
    )


def test_prepared_store_uses_type_independent_attempt_key(tmp_path) -> None:
    store = PublicReplyDeliveryStore(tmp_path / "outbox.sqlite3")
    first = store.prepare(
        delivery_attempt_id=ATTEMPT_ID,
        ticket_id=TICKET_ID,
        reply_type="solution",
        body="body A",
    )
    second = store.prepare(
        delivery_attempt_id=ATTEMPT_ID,
        ticket_id=TICKET_ID,
        reply_type="ask",
        body="body B",
    )
    assert second.delivery_key == first.delivery_key
    assert second.body == "body A"

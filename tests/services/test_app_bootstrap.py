"""Tests for SaaS app runtime bootstrap."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import stat
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web

from zd_agent.config import AppConfig
from zd_agent.services.admin_config import AdminConfigStore
from zd_agent.services.app_bootstrap import (
    create_configured_session_manager,
    prepare_saas_app_runtime,
)
from zd_agent.services.app_context import (
    app_config,
    session_manager,
    ticket_state,
    trigger_queue,
    webhook_dedup,
)
from zd_agent.services.casework_effect_store import (
    CaseworkEffectStore,
    casework_effect_reference,
)
from zd_agent.services.durable_trigger_queue import DurableTriggerQueue
from zd_agent.services.public_reply_internal_note import PostPublicInternalNoteStore
from zd_agent.session.agent_session import AgentSession, SessionStatus
from zd_agent.tools.public_reply_delivery import PublicReplyTransportError
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


def _cfg(tmp_path) -> AppConfig:
    cfg = AppConfig()
    cfg.resources.base_dir = str(tmp_path)
    cfg.resources.state_dir = ".state"
    return cfg


def test_prepare_saas_app_runtime_installs_context(tmp_path) -> None:
    app = web.Application()
    cfg = _cfg(tmp_path)
    manager = MagicMock()

    runtime = prepare_saas_app_runtime(app=app, cfg=cfg, manager=manager)

    assert runtime.state_dir == tmp_path / ".state"
    assert runtime.state_dir.exists()
    assert stat.S_IMODE(runtime.state_dir.stat().st_mode) == 0o700
    assert isinstance(runtime.trigger_queue, DurableTriggerQueue)
    assert (runtime.state_dir / "trigger_queue.sqlite3").exists()
    assert app_config(app) is cfg
    assert trigger_queue(app) is runtime.trigger_queue
    assert ticket_state(app) is runtime.state
    assert webhook_dedup(app) is not None
    assert session_manager(app) is manager


def test_prepare_runtime_discards_disabled_internal_audit_note_intents(
    tmp_path,
) -> None:
    cfg = _cfg(tmp_path)
    assert cfg.public_reply.internal_audit_note_enabled is False
    state_dir = tmp_path / ".state"
    note_store = PostPublicInternalNoteStore(
        state_dir / "post_public_internal_note_outbox.sqlite3"
    )
    note_store.prepare(
        ticket_id=3726,
        delivery_attempt_id="old-delivery",
        reply_type="solution",
        body_with_footer="Previously delivered customer reply.",
        internal_analysis="Do not send this old audit note.",
        public_delivery_confirmed=True,
    )
    assert note_store.pending_count() == 1

    prepare_saas_app_runtime(
        app=web.Application(),
        cfg=cfg,
        manager=MagicMock(),
    )

    assert PostPublicInternalNoteStore(note_store.path).pending_count() == 0


def test_configured_session_manager_loads_saved_concurrency_before_creation(
    tmp_path,
) -> None:
    cfg = _cfg(tmp_path)
    store = AdminConfigStore(tmp_path / ".state")
    store.apply(
        cfg,
        {
            "changes": {"agent.max_concurrent_sessions": 11},
            "reason": "qa",
        },
        actor="qa",
        ip="127.0.0.1",
    )
    manager = MagicMock()

    with patch(
        "zd_agent.session.manager.SessionManager",
        return_value=manager,
    ) as manager_cls:
        result = create_configured_session_manager(cfg)

    assert result is manager
    manager_cls.assert_called_once_with(max_concurrent=11, force_dry_run=False)


def test_configured_session_manager_can_force_server_wide_dry_run(tmp_path) -> None:
    cfg = _cfg(tmp_path)
    manager = MagicMock()

    with patch(
        "zd_agent.session.manager.SessionManager",
        return_value=manager,
    ) as manager_cls:
        result = create_configured_session_manager(cfg, force_dry_run=True)

    assert result is manager
    manager_cls.assert_called_once_with(max_concurrent=5, force_dry_run=True)


def test_configured_session_manager_rejects_invalid_public_reply_transport(
    tmp_path,
) -> None:
    cfg = _cfg(tmp_path)
    cfg.public_reply.enabled = True
    cfg.public_reply.browser_relay_url = "https://relay.example.test/zendesk-reply-mcp"
    cfg.public_reply.browser_relay_api_key = "relay-secret"
    cfg.public_reply.freshness_callback_url = (
        "https://relay.example.test/webhook/relay-pre-send-freshness"
    )
    state_dir = Path(cfg.resources.base_dir) / cfg.resources.state_dir
    state_dir.mkdir(parents=True)
    (state_dir / "admin_overrides.yaml").write_text(
        "public_reply:\n  freshness_callback_url: ''\n"
    )

    with patch("zd_agent.session.manager.SessionManager") as manager_cls:
        with pytest.raises(PublicReplyTransportError, match="freshness callback URL"):
            create_configured_session_manager(cfg)

    manager_cls.assert_not_called()


@pytest.mark.asyncio
async def test_prepare_saas_app_runtime_wires_session_finish_callback(tmp_path) -> None:
    app = web.Application()
    cfg = _cfg(tmp_path)
    manager = MagicMock()

    runtime = prepare_saas_app_runtime(app=app, cfg=cfg, manager=manager)
    runtime.state.set(
        "2808",
        TicketState(
            state="processing",
            first_seen=datetime.now(timezone.utc),
            active_session_id="session-current",
        ),
    )

    callback = manager.set_on_finished.call_args.args[0]
    session = AgentSession(
        ticket_id=2808,
        session_id="session-current",
        trigger_type="webhook-mention",
    )
    session.status = SessionStatus.COMPLETED
    session.reply_sent = True
    await callback(session)

    rec = runtime.state.get("2808")
    assert rec is not None
    assert rec.state == "processed"
    assert rec.last_agent_finish_ts is not None


@pytest.mark.asyncio
async def test_prepare_saas_app_runtime_recovers_processing_ticket(tmp_path) -> None:
    cfg = _cfg(tmp_path)
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    stored = TicketStateTracker(state_dir / "tickets.json")
    stored.set(
        "2809",
        TicketState(
            state="processing",
            first_seen=datetime.now(timezone.utc),
        ),
    )

    runtime = prepare_saas_app_runtime(
        app=web.Application(),
        cfg=cfg,
        manager=MagicMock(),
    )
    recovered = await runtime.trigger_queue.get()

    assert recovered["ticket_id"] == 2809
    assert recovered["source"] == "startup_recovery"
    assert recovered["_queue_key"] == "startup-recovery:2809"
    await runtime.trigger_queue.ack(recovered)


@pytest.mark.asyncio
async def test_processing_recovery_uses_legacy_active_session_delivery_key(
    tmp_path,
) -> None:
    cfg = _cfg(tmp_path)
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    stored = TicketStateTracker(state_dir / "tickets.json")
    stored.set(
        "2812",
        TicketState(
            state="processing",
            first_seen=datetime.now(timezone.utc),
            active_session_id="legacy-session-outbox-key",
            delivery_attempt_id="",
        ),
    )

    runtime = prepare_saas_app_runtime(
        app=web.Application(),
        cfg=cfg,
        manager=MagicMock(),
    )
    recovered = await runtime.trigger_queue.get()

    assert recovered["ticket_id"] == 2812
    assert recovered["delivery_attempt_id"] == "legacy-session-outbox-key"
    await runtime.trigger_queue.ack(recovered)


@pytest.mark.asyncio
async def test_startup_reconciliation_runs_bounded_casework_retention(
    tmp_path,
) -> None:
    cfg = _cfg(tmp_path)
    state_dir = tmp_path / ".state"
    store = CaseworkEffectStore(
        state_dir / "casework_effects.sqlite3",
        now_fn=lambda: 0.0,
    )
    completed = store.prepare(
        ticket_id=2810,
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
    app = web.Application()
    prepare_saas_app_runtime(app=app, cfg=cfg, manager=MagicMock())
    reconcile = next(
        callback
        for callback in app.on_startup
        if callback.__name__ == "_reconcile_internal_notes"
    )

    await reconcile(app)

    assert CaseworkEffectStore(store.path).get(completed.reference) is None


@pytest.mark.asyncio
async def test_startup_reconciliation_promotes_completed_runtime_ownership(
    tmp_path,
) -> None:
    cfg = _cfg(tmp_path)
    state_dir = tmp_path / ".state"
    store = CaseworkEffectStore(state_dir / "casework_effects.sqlite3")
    effect = store.prepare(
        ticket_id=2811,
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
    token = store.claim_ownership(effect.reference)
    assert token is not None
    assert store.mark_ownership_effect(effect.reference, token, "notification_sent")
    assert store.mark_ownership_effect(effect.reference, token, "zendesk_tag_sent")
    state = TicketStateTracker(state_dir / "tickets.json")
    state.set(
        2811,
        TicketState(
            state="ownership_pending",
            first_seen=datetime(2026, 8, 11, tzinfo=timezone.utc),
            active_session_id="runtime-owner-token",
            delivery_attempt_id="D1",
            ownership_action="support",
            ownership_reason="automation_retry_exhausted",
            ownership_effect_reference=casework_effect_reference(
                2811,
                "D1:runtime-failure",
            ),
        ),
    )
    app = web.Application()
    prepare_saas_app_runtime(app=app, cfg=cfg, manager=MagicMock())
    reconcile = next(
        callback
        for callback in app.on_startup
        if callback.__name__ == "_reconcile_internal_notes"
    )

    await reconcile(app)

    owned = TicketStateTracker(state.path).get(2811)
    assert owned is not None
    assert owned.state == "owned"
    assert owned.active_session_id == ""
    assert owned.ownership_effect_reference == effect.reference

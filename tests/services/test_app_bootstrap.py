"""Tests for SaaS app runtime bootstrap."""
from __future__ import annotations

from datetime import datetime, timezone
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
from zd_agent.services.durable_trigger_queue import DurableTriggerQueue
from zd_agent.session.agent_session import AgentSession, SessionStatus
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
    manager_cls.assert_called_once_with(max_concurrent=11)


@pytest.mark.asyncio
async def test_prepare_saas_app_runtime_wires_session_finish_callback(tmp_path) -> None:
    app = web.Application()
    cfg = _cfg(tmp_path)
    manager = MagicMock()

    runtime = prepare_saas_app_runtime(app=app, cfg=cfg, manager=manager)
    runtime.state.set(
        "2808",
        TicketState(
            state="waiting_for_issue",
            first_seen=datetime.now(timezone.utc),
        ),
    )

    callback = manager.set_on_finished.call_args.args[0]
    session = AgentSession(ticket_id=2808, trigger_type="webhook-mention")
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

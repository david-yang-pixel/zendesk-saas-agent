"""Tests for SaaS app runtime bootstrap."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from aiohttp import web

from zd_agent.config import AppConfig
from zd_agent.services.app_bootstrap import prepare_saas_app_runtime
from zd_agent.services.app_context import (
    app_config,
    session_manager,
    ticket_state,
    trigger_queue,
    webhook_dedup,
)
from zd_agent.session.agent_session import AgentSession, SessionStatus
from zd_agent.trigger.state_store import TicketState


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
    assert app_config(app) is cfg
    assert trigger_queue(app) is runtime.trigger_queue
    assert ticket_state(app) is runtime.state
    assert webhook_dedup(app) is not None
    assert session_manager(app) is manager


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

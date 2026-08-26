from __future__ import annotations

from zd_agent.config import AppConfig
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from zd_agent.services.app_context import (
    BACKGROUND_TASK_KEYS,
    admin_config,
    agent_lifecycle,
)
from zd_agent.services.control_plane_app import (
    create_control_plane_app,
    handle_control_plane_health,
)


def test_control_plane_exposes_admin_webhooks_and_health_without_worker_routes(
    tmp_path,
) -> None:
    cfg = AppConfig()
    cfg.resources.base_dir = str(tmp_path)
    cfg.resources.state_dir = ".state"

    app = create_control_plane_app(cfg)
    routes = {route.resource.canonical for route in app.router.routes()}

    assert "/admin" in routes
    assert "/admin/api/agent/actions" in routes
    assert cfg.webhook.zendesk_path in routes
    assert cfg.webhook.zendesk_messaging_path in routes
    assert "/healthz" in routes
    assert agent_lifecycle(app) is not None
    assert all(key not in app for key in BACKGROUND_TASK_KEYS)


@pytest.mark.asyncio
async def test_corrupt_lifecycle_state_makes_control_plane_health_unready(
    tmp_path,
) -> None:
    cfg = AppConfig()
    cfg.resources.base_dir = str(tmp_path)
    cfg.resources.state_dir = ".state"
    app = create_control_plane_app(cfg)
    lifecycle = agent_lifecycle(app)
    lifecycle.store.path.write_text("{broken", encoding="utf-8")

    response = await handle_control_plane_health(SimpleNamespace(app=app))

    body = json.loads(response.text)
    assert response.status == 503
    assert body["ready"] is False
    assert body["agent"]["status"] == "failed"


def test_transition_callback_writes_final_audit_result(tmp_path) -> None:
    cfg = AppConfig()
    cfg.resources.base_dir = str(tmp_path)
    cfg.resources.state_dir = ".state"
    app = create_control_plane_app(cfg)
    lifecycle = agent_lifecycle(app)

    result = {
        "action": "stop",
        "status": "blocked",
        "operation_id": "operation-stop-1234",
        "actor": "admin",
        "reason": "operator requested shutdown",
        "ip": "127.0.0.1",
        "generation": 2,
        "error": "unsafe_outbox",
        "blockers": {"public_reply_ambiguous": 1},
    }
    lifecycle._transition_callback(result)
    lifecycle._transition_callback(result)

    audit = admin_config(app).list_audit_logs(action="agent_stop_blocked")
    assert audit["summary"]["filtered"] == 1
    assert audit["items"][0]["reason"] == "operator requested shutdown"
    assert audit["items"][0]["diff"][0]["new"]["error"] == "unsafe_outbox"


@pytest.mark.asyncio
async def test_stopped_health_requires_worker_process_to_be_absent(tmp_path) -> None:
    cfg = AppConfig()
    cfg.resources.base_dir = str(tmp_path)
    cfg.resources.state_dir = ".state"
    app = create_control_plane_app(cfg)
    lifecycle = agent_lifecycle(app)
    lifecycle.store.write(
        {
            "desired_state": "stopped",
            "dispatch_enabled": False,
            "dispatch_generation": 1,
            "generation": 1,
            "accept_after": 0,
        }
    )
    lifecycle._process = MagicMock(pid=4321, returncode=None)

    response = await handle_control_plane_health(SimpleNamespace(app=app))

    assert response.status == 503
    assert json.loads(response.text)["ready"] is False

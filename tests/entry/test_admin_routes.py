from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from aiohttp import web

from zd_agent.config import AppConfig
from zd_agent.services.admin_routes import ADMIN_HANDLERS_KEY, register_admin_routes
from zd_agent.services.app_context import app_config, install_saas_app_context


class _Request:
    def __init__(self, *, app, payload=None, cookies=None, headers=None, remote="127.0.0.1"):
        self.app = app
        self._payload = payload or {}
        self.cookies = cookies or {}
        self.headers = headers or {}
        self.remote = remote

    async def json(self):
        return self._payload


def _app(tmp_path) -> web.Application:
    app = web.Application()
    cfg = AppConfig()
    cfg.resources.base_dir = str(tmp_path)
    cfg.resources.state_dir = ".state"
    install_saas_app_context(
        app=app,
        cfg=cfg,
        manager=MagicMock(),
        trigger_queue=MagicMock(),
        state=MagicMock(),
        webhook_dedup=MagicMock(),
    )
    register_admin_routes(app=app, cfg=cfg, state_dir=tmp_path / ".state")
    return app


def test_register_admin_routes_installs_expected_routes(tmp_path) -> None:
    app = _app(tmp_path)

    route_paths = {route.resource.canonical for route in app.router.routes()}

    assert "/admin" in route_paths
    assert "/admin/" in route_paths
    assert "/admin/api/auth/login" in route_paths
    assert "/admin/api/config/schema" in route_paths
    assert "/admin/api/run-records" in route_paths
    assert "/admin/api/audit-logs" in route_paths


@pytest.mark.asyncio
async def test_admin_config_requires_session(tmp_path) -> None:
    app = _app(tmp_path)
    request = _Request(app=app)

    response = await app[ADMIN_HANDLERS_KEY]["config_schema"](request)

    assert response.status == 401


@pytest.mark.asyncio
async def test_admin_login_sets_session_cookie(tmp_path) -> None:
    app = _app(tmp_path)
    request = _Request(
        app=app,
        payload={"username": "admin", "password": "admin123"},
    )

    response = await app[ADMIN_HANDLERS_KEY]["login"](request)

    assert response.status == 200
    assert "zd_admin_session" in response.cookies


@pytest.mark.asyncio
async def test_admin_config_requires_default_password_change(tmp_path) -> None:
    app = _app(tmp_path)
    login_response = await app[ADMIN_HANDLERS_KEY]["login"](
        _Request(
            app=app,
            payload={"username": "admin", "password": "admin123"},
        )
    )
    cookie = {"zd_admin_session": login_response.cookies["zd_admin_session"].value}

    blocked_response = await app[ADMIN_HANDLERS_KEY]["config_schema"](
        _Request(app=app, cookies=cookie)
    )

    assert blocked_response.status == 403
    assert json.loads(blocked_response.text)["error"] == "password_change_required"

    password_response = await app[ADMIN_HANDLERS_KEY]["password"](
        _Request(
            app=app,
            cookies=cookie,
            payload={
                "current_password": "admin123",
                "new_password": "new-admin-password",
            },
        )
    )
    allowed_response = await app[ADMIN_HANDLERS_KEY]["config_schema"](
        _Request(app=app, cookies=cookie)
    )

    assert password_response.status == 200
    assert allowed_response.status == 200


@pytest.mark.asyncio
async def test_admin_run_records_reads_recent_jsonl_events(tmp_path) -> None:
    app = _app(tmp_path)
    cfg = app_config(app)
    cfg.logging.log_dir = str(tmp_path / "logs")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "events-2026-06-29.jsonl").write_text(
        "\n".join([
            json.dumps({"event": "agent_started", "ticket_id": 3134}),
            json.dumps({"event": "agent_done", "ticket_id": 3134, "rounds": 5}),
        ]),
        encoding="utf-8",
    )
    login_response = await app[ADMIN_HANDLERS_KEY]["login"](
        _Request(
            app=app,
            payload={"username": "admin", "password": "admin123"},
        )
    )
    cookie = {"zd_admin_session": login_response.cookies["zd_admin_session"].value}
    await app[ADMIN_HANDLERS_KEY]["password"](
        _Request(
            app=app,
            cookies=cookie,
            payload={
                "current_password": "admin123",
                "new_password": "new-admin-password",
            },
        )
    )

    response = await app[ADMIN_HANDLERS_KEY]["run_records"](
        _Request(app=app, cookies=cookie)
    )

    body = json.loads(response.text)
    assert response.status == 200
    assert body["items"][0]["event"] == "agent_done"
    assert body["items"][0]["ticket_id"] == 3134
    assert body["items"][0]["_source_file"] == "events-2026-06-29.jsonl"

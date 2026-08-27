from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from aiohttp import web

from zd_agent.config import AppConfig
from zd_agent.services.admin_routes import ADMIN_HANDLERS_KEY, register_admin_routes
from zd_agent.services.app_context import (
    SESSION_MANAGER_KEY,
    TICKET_STATE_KEY,
    install_saas_app_context,
)
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


class Request:
    def __init__(self, *, app, payload=None, cookies=None, query=None):
        self.app = app
        self.payload = payload or {}
        self.cookies = cookies or {}
        self.query = query or {}
        self.headers = {}
        self.remote = "127.0.0.1"

    async def json(self):
        return self.payload


def _app(tmp_path):
    app = web.Application()
    cfg = AppConfig()
    cfg.resources.base_dir = str(tmp_path)
    install_saas_app_context(
        app=app,
        cfg=cfg,
        trigger_queue=MagicMock(),
        state=TicketStateTracker(tmp_path / "tickets.json"),
        webhook_dedup=MagicMock(),
        manager=MagicMock(),
    )
    register_admin_routes(
        app=app,
        cfg=cfg,
        state_dir=tmp_path,
        initial_admin_password="admin-password",
    )
    return app


async def _login(app):
    response = await app[ADMIN_HANDLERS_KEY]["login"](
        Request(app=app, payload={"username": "admin", "password": "admin-password"})
    )
    cookie = {"zd_admin_session": response.cookies["zd_admin_session"].value}
    changed = await app[ADMIN_HANDLERS_KEY]["password"](
        Request(
            app=app,
            cookies=cookie,
            payload={
                "current_password": "admin-password",
                "new_password": "changed-admin-password",
            },
        )
    )
    return {"zd_admin_session": changed.cookies["zd_admin_session"].value}


@pytest.mark.asyncio
async def test_ticket_center_requires_admin_and_returns_business_state_only(tmp_path):
    app = _app(tmp_path)
    unauthorized = await app[ADMIN_HANDLERS_KEY]["ticket_center"](Request(app=app))
    assert unauthorized.status == 401

    cookie = await _login(app)
    tracker = app[TICKET_STATE_KEY]
    tracker.set(
        501,
        TicketState(
            state="awaiting_customer",
            first_seen=datetime(2026, 8, 27, tzinfo=timezone.utc),
            last_comment_ts=datetime(2026, 8, 27, 1, tzinfo=timezone.utc),
            last_eval_at=datetime(2026, 8, 27, 2, tzinfo=timezone.utc),
            pending_customer_fields=("workspace_id",),
            pending_issue_question="Why is the request waiting?",
            ownership_action="",
            ownership_reason="",
            consecutive_agent_failures=2,
        ),
    )
    app[SESSION_MANAGER_KEY].get_all_sessions.return_value = {
        "active_sessions": [{"ticket_id": 502, "status": "analyzing"}],
    }

    response = await app[ADMIN_HANDLERS_KEY]["ticket_center"](
        Request(app=app, cookies=cookie)
    )
    body = json.loads(response.text)
    assert response.status == 200
    assert {item["ticket_id"] for item in body["items"]} == {501, 502}
    waiting = next(item for item in body["items"] if item["ticket_id"] == 501)
    active = next(item for item in body["items"] if item["ticket_id"] == 502)
    assert waiting["business_status"] == "awaiting_customer"
    assert waiting["waiting_for_customer"] is True
    assert waiting["pending_customer_fields"] == ["workspace_id"]
    assert waiting["failure_count"] == 2
    assert active["status"] == "processing"
    assert active["active"] is True
    assert body["summary"]["total"] == 2
    assert body["summary"]["visible"] == 2
    assert body["summary"]["awaiting_customer"] == 1
    assert body["summary"]["failures"] == 2
    assert "events" not in body
    assert all("run_records" not in item for item in body["items"])


@pytest.mark.asyncio
async def test_ticket_center_status_filter_does_not_read_run_records(tmp_path):
    app = _app(tmp_path)
    cookie = await _login(app)
    app[SESSION_MANAGER_KEY].get_all_sessions.return_value = {
        "active_sessions": [{"ticket_id": 503}],
    }
    response = await app[ADMIN_HANDLERS_KEY]["ticket_center"](
        Request(app=app, cookies=cookie, query={"status": "awaiting_customer"})
    )
    body = json.loads(response.text)
    assert response.status == 200
    assert body["items"] == []
    assert body["summary"]["total"] == 1
    assert body["summary"]["visible"] == 0
    assert body["summary"]["by_status"] == {"processing": 1}
    assert body["filters"]["status"] == "awaiting_customer"


@pytest.mark.asyncio
async def test_ticket_center_normalizes_legacy_naive_timestamps(tmp_path):
    app = _app(tmp_path)
    cookie = await _login(app)
    tracker = app[TICKET_STATE_KEY]
    tracker.set(
        504,
        TicketState(
            state="processed",
            first_seen=datetime(2026, 8, 26, 12, 0),
            last_eval_at=datetime(2026, 8, 27, 1, 0, tzinfo=timezone.utc),
        ),
    )
    app[SESSION_MANAGER_KEY].get_all_sessions.return_value = {"active_sessions": []}

    response = await app[ADMIN_HANDLERS_KEY]["ticket_center"](
        Request(app=app, cookies=cookie)
    )
    body = json.loads(response.text)
    assert response.status == 200
    assert body["items"][0]["ticket_id"] == 504
    assert body["items"][0]["updated_at"].endswith("+00:00")

from __future__ import annotations

import json
import re
from importlib import resources
from unittest.mock import MagicMock
from unittest.mock import patch

import httpx

import pytest
from aiohttp import web

from zd_agent.config import AppConfig
from zd_agent.services.admin_routes import (
    ADMIN_HANDLERS_KEY,
    _load_index_html,
    register_admin_routes,
)
from zd_agent.services.app_context import app_config, install_saas_app_context

REAL_ASYNC_CLIENT = httpx.AsyncClient


class _Request:
    def __init__(
        self,
        *,
        app,
        payload=None,
        cookies=None,
        headers=None,
        remote="127.0.0.1",
        query=None,
        match_info=None,
    ):
        self.app = app
        self._payload = payload or {}
        self.cookies = cookies or {}
        self.headers = headers or {}
        self.remote = remote
        self.query = query or {}
        self.match_info = match_info or {}

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
    assert "/admin/api/integrations/check" in route_paths
    assert "/admin/static/{name}" in route_paths


def test_admin_static_assets_are_split_from_html_shell() -> None:
    html = _load_index_html()

    assert "系统摘要" not in html
    assert '/admin/static/styles.css' in html
    assert '/admin/static/app.js' in html
    assert "最近运行事件" not in html


def test_admin_table_layout_keeps_rows_as_vertical_items() -> None:
    css = (
        resources.files("zd_agent.admin_static")
        .joinpath("styles.css")
        .read_text(encoding="utf-8")
    )
    table_grid_block = re.search(r"\.table-grid\s*\{(?P<body>[^}]*)\}", css)
    row_block = re.search(r"\.table-header,\s*\.table-row\s*\{(?P<body>[^}]*)\}", css)

    assert table_grid_block is not None
    assert row_block is not None
    assert "display: grid" not in table_grid_block.group("body")
    assert "display: grid" in row_block.group("body")


def test_admin_shell_uses_chinese_management_navigation_and_sso_login() -> None:
    html = _load_index_html()
    app_js = (
        resources.files("zd_agent.admin_static")
        .joinpath("app.js")
        .read_text(encoding="utf-8")
    )

    for label in [
        "总览",
        "运行记录",
        "触发配置",
        "回复策略",
        "模型与运行",
        "工具与集成",
        "存储配置",
        "部署状态",
        "审计日志",
    ]:
        assert label in app_js
    assert "管理后台" in html
    assert "生产环境管理后台" not in html
    assert "使用 Google 登录" in html
    assert "使用飞书登录" in html
    assert "admin123" in html
    assert "Storage Configuration" not in app_js
    assert "Deployment Status" not in app_js
    assert "Operations" not in html


def test_admin_run_records_use_expandable_run_round_tool_layout() -> None:
    app_js = (
        resources.files("zd_agent.admin_static")
        .joinpath("app.js")
        .read_text(encoding="utf-8")
    )
    css = (
        resources.files("zd_agent.admin_static")
        .joinpath("styles.css")
        .read_text(encoding="utf-8")
    )

    for text in ["总处理数", "成功率", "平均耗时", "Token 消耗", "轮次", "触发方式"]:
        assert text in app_js
    for symbol in ["run-record-card", "record-expand", "round-timeline", "tool-call-row"]:
        assert symbol in app_js
        assert symbol in css
    assert ".run-record-row" in css
    assert "grid-template-columns" in css


def test_admin_storage_config_page_is_not_blank() -> None:
    app_js = (
        resources.files("zd_agent.admin_static")
        .joinpath("app.js")
        .read_text(encoding="utf-8")
    )

    for text in [
        "运行记录存储",
        "审计日志存储",
        "附件与截图缓存",
        "配置持久化",
        "存储检查结果",
        "配置运行记录、审计日志、附件缓存和配置持久化路径",
    ]:
        assert text in app_js


@pytest.mark.asyncio
async def test_admin_static_asset_serves_known_files_and_rejects_unknown(tmp_path) -> None:
    app = _app(tmp_path)
    login_response = await app[ADMIN_HANDLERS_KEY]["login"](
        _Request(app=app, payload={"username": "admin", "password": "admin123"})
    )
    cookie = {"zd_admin_session": login_response.cookies["zd_admin_session"].value}

    response = await app[ADMIN_HANDLERS_KEY]["static_asset"](
        _Request(app=app, cookies=cookie, match_info={"name": "app.js"})
    )
    blocked = await app[ADMIN_HANDLERS_KEY]["static_asset"](
        _Request(app=app, cookies=cookie, match_info={"name": "../config.yaml"})
    )

    assert response.status == 200
    assert "renderRunRecords" in response.text
    assert blocked.status == 404


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
async def test_admin_run_records_reads_recent_jsonl_events_with_summary_filters_and_redaction(tmp_path) -> None:
    app = _app(tmp_path)
    cfg = app_config(app)
    cfg.logging.log_dir = str(tmp_path / "logs")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "events-2026-06-29.jsonl").write_text(
        "\n".join([
            json.dumps({
                "timestamp": "2026-06-29T10:00:00Z",
                "event": "agent_started",
                "ticket_id": 3134,
                "module": "runtime",
            }),
            json.dumps({
                "timestamp": "2026-06-29T10:01:00Z",
                "event": "agent_failed",
                "ticket_id": 3135,
                "module": "runtime",
                "input_tokens": 1234,
                "output_tokens": 56,
                "api_key": "sk-test-secret",
                "nested": {"token": "secret-token"},
                "error": "boom",
            }),
            json.dumps({
                "timestamp": "2026-06-29T10:02:00Z",
                "event": "agent_done",
                "ticket_id": 3134,
                "module": "reply",
                "rounds": 5,
            }),
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
        _Request(
            app=app,
            cookies=cookie,
            query={"ticket_id": "3135", "errors_only": "true"},
        )
    )

    body = json.loads(response.text)
    assert response.status == 200
    assert body["items"][0]["event"] == "agent_failed"
    assert body["items"][0]["ticket_id"] == 3135
    assert body["items"][0]["_source_file"] == "events-2026-06-29.jsonl"
    assert body["items"][0]["input_tokens"] == 1234
    assert body["items"][0]["output_tokens"] == 56
    assert body["items"][0]["api_key"] == "[REDACTED]"
    assert body["items"][0]["nested"]["token"] == "[REDACTED]"
    assert body["summary"]["total"] == 3
    assert body["summary"]["filtered"] == 1
    assert body["summary"]["error_events"] == 1


@pytest.mark.asyncio
async def test_admin_audit_logs_support_filters_and_summary(tmp_path) -> None:
    app = _app(tmp_path)
    login_response = await app[ADMIN_HANDLERS_KEY]["login"](
        _Request(app=app, payload={"username": "admin", "password": "admin123"})
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

    response = await app[ADMIN_HANDLERS_KEY]["audit_logs"](
        _Request(app=app, cookies=cookie, query={"action": "admin_password_changed"})
    )

    body = json.loads(response.text)
    assert response.status == 200
    assert body["items"][0]["action"] == "admin_password_changed"
    assert body["summary"]["filtered"] == 1
    assert body["summary"]["total"] >= 2

    limited_response = await app[ADMIN_HANDLERS_KEY]["audit_logs"](
        _Request(app=app, cookies=cookie, query={"limit": "1"})
    )
    limited_body = json.loads(limited_response.text)
    assert len(limited_body["items"]) == 1
    assert limited_body["summary"]["filtered"] >= 2
    assert limited_body["summary"]["total"] >= 2


@pytest.mark.asyncio
async def test_admin_integration_check_reports_reachability_and_writes_audit(tmp_path) -> None:
    app = _app(tmp_path)
    cfg = app_config(app)
    cfg.signoz.mcp_url = "https://signoz.example.test/mcp"
    cfg.signoz.mcp_api_key = "super-secret-key"
    login_response = await app[ADMIN_HANDLERS_KEY]["login"](
        _Request(app=app, payload={"username": "admin", "password": "admin123"})
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

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer super-secret-key"
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

    transport = httpx.MockTransport(handler)
    with patch(
        "zd_agent.services.admin_routes.httpx.AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    ):
        response = await app[ADMIN_HANDLERS_KEY]["integration_check"](
            _Request(
                app=app,
                cookies=cookie,
                payload={"integration": "signoz"},
            )
        )

    body = json.loads(response.text)
    audit = await app[ADMIN_HANDLERS_KEY]["audit_logs"](
        _Request(app=app, cookies=cookie, query={"action": "integration_check"})
    )
    audit_body = json.loads(audit.text)
    assert response.status == 200
    assert body["configured"] is True
    assert body["reachable"] is True
    assert body["status"] == "ok"
    assert audit_body["items"][0]["action"] == "integration_check"
    assert "super-secret-key" not in audit.text


@pytest.mark.asyncio
async def test_admin_deploy_status_includes_runtime_metadata_and_pending_restart(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ZD_AGENT_IMAGE_TAG", "sha-test")
    monkeypatch.setenv("GITHUB_SHA", "abc123")
    app = _app(tmp_path)
    login_response = await app[ADMIN_HANDLERS_KEY]["login"](
        _Request(app=app, payload={"username": "admin", "password": "admin123"})
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
    await app[ADMIN_HANDLERS_KEY]["config_apply"](
        _Request(
            app=app,
            cookies=cookie,
            payload={
                "changes": {"zendesk.poll_interval_seconds": 120},
                "reason": "test pending restart",
            },
        )
    )

    response = await app[ADMIN_HANDLERS_KEY]["deploy_status"](
        _Request(app=app, cookies=cookie)
    )

    body = json.loads(response.text)
    assert response.status == 200
    assert body["image"]["tag"] == "sha-test"
    assert body["image"]["commit_sha"] == "abc123"
    assert body["pending_restart"] is True

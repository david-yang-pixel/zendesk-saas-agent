from __future__ import annotations

import json
import re
from importlib import resources
from unittest.mock import MagicMock
from unittest.mock import patch

import httpx

import pytest
from aiohttp import web

from zd_agent.services import admin_routes
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
    assert "auth-shell" in html
    assert "/admin/static/dify-logo.svg" in html
    assert "/admin/static/dify-logo-on-blue.svg" in html
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
    for symbol in ["run-record-card", "record-expand", "round-timeline", "tool-call-row", "event-trace"]:
        assert symbol in app_js
        assert symbol in css
    for symbol in ["module-guide", "summary-strip", "raw-json", "notice-card", "toast"]:
        assert symbol in app_js or symbol in css
    assert ".run-record-row" in css
    assert "grid-template-columns" in css
    assert "position: sticky" in css
    assert 'numberFromKeys(item, ["rounds", "round_count"]) || rounds.length || 1' in app_js
    assert 'round: numberFromKeys(round, ["round", "round_number"]) ||' in app_js
    assert "round.round || round.index || index + 1" in app_js
    assert "仅聚合轮次" in app_js
    assert "聚合轮次" in app_js
    assert "后台刷新中" in app_js
    assert "缓存命中" in app_js
    assert "rawEvents" in app_js
    assert "查看完整日志" in app_js
    assert "renderOverviewShell" in app_js
    assert "renderRecentRunsIntoOverview" in app_js
    assert "renderRunRecordsShell" in app_js
    assert 'detail: false' in app_js
    assert "ensureRunRecordDetail" in app_js
    assert 'detail: true' in app_js
    assert "await Promise.all" not in app_js[app_js.index("async function renderOverview"):app_js.index("function goMenu")]


def test_admin_config_pages_render_prompt_textareas() -> None:
    app_js = (
        resources.files("zd_agent.admin_static")
        .joinpath("app.js")
        .read_text(encoding="utf-8")
    )

    assert "prompt-textarea" in app_js
    assert 'field.input === "textarea"' in app_js
    assert "module-overview" in app_js
    assert "module-guide" in app_js
    assert "FIELD_SECTIONS" in app_js
    assert "selectConfigSection" in app_js
    assert "renderConfigSectionPicker" in app_js
    assert "renderConfigSectionDetails" in app_js
    assert "点击卡片查看配置" in app_js
    assert "config-section-tile" in app_js
    assert "config-section-card" in app_js
    assert "触发判定与等待策略" in app_js
    assert "主模型与判定模型" in app_js
    assert "判定模型放在「模型与运行」里" in app_js
    assert "Ask 提示词补充规则" in (
        resources.files("zd_agent.services")
        .joinpath("admin_config.py")
        .read_text(encoding="utf-8")
    )


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
    logo_response = await app[ADMIN_HANDLERS_KEY]["static_asset"](
        _Request(app=app, cookies=cookie, match_info={"name": "dify-logo.svg"})
    )
    blocked = await app[ADMIN_HANDLERS_KEY]["static_asset"](
        _Request(app=app, cookies=cookie, match_info={"name": "../config.yaml"})
    )

    assert response.status == 200
    assert "renderRunRecords" in response.text
    assert logo_response.status == 200
    assert logo_response.content_type == "image/svg+xml"
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
    assert body["items"][0]["event_count"] == 1
    assert body["items"][0]["events"][0]["event"] == "agent_failed"
    assert body["summary"]["total"] == 2
    assert body["summary"]["filtered"] == 1
    assert body["summary"]["error_events"] == 1
    assert body["raw_events_total"] == 3


@pytest.mark.asyncio
async def test_admin_run_records_ignore_internal_events_and_normalize_agent_done(tmp_path) -> None:
    app = _app(tmp_path)
    cfg = app_config(app)
    cfg.logging.log_dir = str(tmp_path / "logs")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    lines = [
        json.dumps({
            "timestamp": f"2026-06-29T10:00:{second:02d}Z",
            "event": "self_user_id_resolved",
            "module": "mention.detector",
            "source": "webhook",
        })
        for second in range(60)
    ]
    lines.append(
        json.dumps({
            "timestamp": "2026-06-29T10:01:00Z",
            "event": "agent_done",
            "ticket_id": 3172,
            "status": "completed",
            "module": "runtime.runner",
            "rounds": 17,
            "prompt_tokens": 18_200,
            "completion_tokens": 127,
            "total_tokens": 18_327,
            "elapsed": "4.6s",
            "trigger_type": "webhook",
            "model": "gpt-5.5",
        })
    )
    lines.extend(
        json.dumps({
            "timestamp": f"2026-06-29T10:02:{second:02d}Z",
            "event": "self_user_id_resolved",
            "module": "mention.detector",
            "source": "webhook",
        })
        for second in range(60)
    )
    (log_dir / "events-2026-06-29.jsonl").write_text(
        "\n".join(lines),
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
        _Request(app=app, cookies=cookie, query={"limit": "200"})
    )

    body = json.loads(response.text)
    assert response.status == 200
    assert [item["event"] for item in body["items"]] == ["agent_done"]
    assert body["items"][0]["ticket_id"] == 3172
    assert body["items"][0]["status"] == "completed"
    assert body["items"][0]["input_tokens"] == 18_200
    assert body["items"][0]["output_tokens"] == 127
    assert body["items"][0]["total_tokens"] == 18_327
    assert body["items"][0]["duration_ms"] == 4_600
    assert body["items"][0]["trigger"] == "webhook"
    assert body["items"][0]["model"] == "gpt-5.5"
    assert body["items"][0]["event_count"] == 1
    assert body["summary"]["total"] == 1
    assert body["summary"]["filtered"] == 1


@pytest.mark.asyncio
async def test_admin_run_records_collapse_agent_lifecycle_to_one_ticket_run(tmp_path) -> None:
    app = _app(tmp_path)
    cfg = app_config(app)
    cfg.logging.log_dir = str(tmp_path / "logs")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "events-2026-06-29.jsonl").write_text(
        "\n".join([
            json.dumps({
                "timestamp": "2026-06-29T10:00:00Z",
                "event": "session_dispatched",
                "ticket_id": 3172,
                "session_id": "session-3172",
                "module": "session_manager",
            }),
            json.dumps({
                "timestamp": "2026-06-29T10:00:01Z",
                "event": "agent_start",
                "ticket_id": 3172,
                "session_id": "session-3172",
                "trigger_type": "webhook",
                "module": "runtime.runner",
            }),
            json.dumps({
                "timestamp": "2026-06-29T10:04:36Z",
                "event": "agent_done",
                "ticket_id": 3172,
                "status": "completed",
                "module": "runtime.runner",
                "rounds": 17,
                "prompt_tokens": 18_200,
                "completion_tokens": 127,
                "total_tokens": 18_327,
                "elapsed": "4.6min",
                "model": "gpt-5.5",
            }),
            json.dumps({
                "timestamp": "2026-06-29T10:04:37Z",
                "event": "session_finished",
                "ticket_id": 3172,
                "status": "completed",
                "rounds": 17,
                "tokens": 18_327,
                "elapsed": "4.6min",
                "remaining_active": 0,
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
        _Request(app=app, cookies=cookie, query={"limit": "200"})
    )

    body = json.loads(response.text)
    assert response.status == 200
    assert len(body["items"]) == 1
    assert body["items"][0]["event"] == "agent_done"
    assert body["items"][0]["ticket_id"] == 3172
    assert body["items"][0]["session_id"] == "session-3172"
    assert body["items"][0]["rounds"] == 17
    assert body["items"][0]["total_tokens"] == 18_327
    assert body["items"][0]["duration_ms"] == 276_000
    assert body["items"][0]["trigger"] == "webhook"
    assert body["items"][0]["event_count"] == 4
    assert [event["event"] for event in body["items"][0]["events"]] == [
        "session_dispatched",
        "agent_start",
        "agent_done",
        "session_finished",
    ]
    assert body["summary"]["total"] == 1


@pytest.mark.asyncio
async def test_admin_run_records_enriches_archive_tool_call_rounds(tmp_path) -> None:
    app = _app(tmp_path)
    cfg = app_config(app)
    cfg.logging.log_dir = str(tmp_path / "logs")
    cfg.knowledge.archive_dir = str(tmp_path / "archive")
    log_dir = tmp_path / "logs"
    archive_dir = tmp_path / "archive" / "3172"
    log_dir.mkdir()
    archive_dir.mkdir(parents=True)
    (log_dir / "events-2026-06-29.jsonl").write_text(
        "\n".join([
            json.dumps({
                "timestamp": "2026-06-29T10:04:36Z",
                "event": "agent_done",
                "ticket_id": 3172,
                "status": "completed",
                "module": "runtime.runner",
                "rounds": 17,
                "prompt_tokens": 18_200,
                "completion_tokens": 127,
                "total_tokens": 18_327,
                "elapsed": "4.6min",
                "model": "gpt-5.5",
            }),
            json.dumps({
                "timestamp": "2026-06-29T10:04:37Z",
                "event": "session_finished",
                "ticket_id": 3172,
                "status": "completed",
                "rounds": 17,
                "tokens": 18_327,
                "elapsed": "4.6min",
            }),
        ]),
        encoding="utf-8",
    )
    (archive_dir / "meta.json").write_text(
        json.dumps({
            "ticket_id": 3172,
            "session_id": "session-3172",
            "status": "completed",
            "rounds": 17,
            "token_usage": {"prompt": 18_200, "completion": 127, "total": 18_327},
            "tool_call_count": 3,
            "tool_calls": [
                {
                    "round": 1,
                    "tool": "zendesk_get_ticket",
                    "success": True,
                    "error": None,
                    "result_preview": "loaded ticket",
                },
                {
                    "round": 2,
                    "tool": "sql_query",
                    "success": False,
                    "error": "database unavailable",
                    "result_preview": "",
                },
                {
                    "round": 17,
                    "tool": "archive_ticket",
                    "success": True,
                    "error": None,
                    "result_preview": "archived",
                },
            ],
        }),
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
        _Request(app=app, cookies=cookie, query={"limit": "200"})
    )

    body = json.loads(response.text)
    assert response.status == 200
    item = body["items"][0]
    assert item["ticket_id"] == 3172
    assert item["session_id"] == "session-3172"
    assert item["archive_available"] is True
    assert item["tool_call_count"] == 3
    assert [round_item["round"] for round_item in item["llm_rounds"]] == [1, 2, 17]
    assert item["llm_rounds"][0]["tools"][0]["name"] == "zendesk_get_ticket"
    assert item["llm_rounds"][1]["tools"][0]["status"] == "failed"
    assert item["llm_rounds"][1]["tools"][0]["error"] == "database unavailable"
    assert item["event_count"] == 2


def test_admin_run_records_trace_index_supports_module_filters(tmp_path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "events-2026-07-02.jsonl").write_text(
        "\n".join([
            json.dumps({
                "timestamp": "2026-07-02T08:00:00Z",
                "event": "agent_done",
                "ticket_id": 4001,
                "session_id": "session-4001",
                "module": "runtime.runner",
            }),
            json.dumps({
                "timestamp": "2026-07-02T08:00:01Z",
                "event": "tool_call",
                "ticket_id": 4001,
                "session_id": "session-4001",
                "module": "db.gateway",
                "tool": "sql_query",
            }),
            json.dumps({
                "timestamp": "2026-07-02T08:01:00Z",
                "event": "agent_done",
                "ticket_id": 4002,
                "session_id": "session-4002",
                "module": "runtime.runner",
            }),
        ]),
        encoding="utf-8",
    )

    body = admin_routes._read_recent_log_events(
        log_dir,
        limit=20,
        filters={"module": "db.gateway"},
    )

    assert [item["ticket_id"] for item in body["items"]] == [4001]
    assert body["items"][0]["event_count"] == 2
    assert [event["event"] for event in body["items"][0]["events"]] == [
        "agent_done",
        "tool_call",
    ]


def test_admin_run_records_builds_rounds_from_jsonl_trace_events(tmp_path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "events-2026-07-02.jsonl").write_text(
        "\n".join([
            json.dumps({
                "timestamp": "2026-07-02T08:00:00Z",
                "event": "agent_start",
                "ticket_id": 4101,
                "session_id": "session-4101",
                "model": "gpt-5.5",
            }),
            json.dumps({
                "timestamp": "2026-07-02T08:00:03Z",
                "event": "llm_round",
                "ticket_id": 4101,
                "session_id": "session-4101",
                "round": 1,
                "model": "gpt-5.5",
                "prompt_tokens": 1200,
                "completion_tokens": 80,
                "llm_elapsed_seconds": 2.4,
                "tool_call_count": 1,
                "stop_reason": "tool_calls",
            }),
            json.dumps({
                "timestamp": "2026-07-02T08:00:05Z",
                "event": "tool_call",
                "ticket_id": 4101,
                "session_id": "session-4101",
                "round": 1,
                "tool": "zendesk_get_ticket",
                "success": True,
                "duration_seconds": 1.5,
            }),
            json.dumps({
                "timestamp": "2026-07-02T08:00:10Z",
                "event": "llm_round",
                "ticket_id": 4101,
                "session_id": "session-4101",
                "round": 2,
                "model": "gpt-5.5",
                "prompt_tokens": 1500,
                "completion_tokens": 120,
                "llm_elapsed_seconds": 3.1,
                "tool_call_count": 1,
                "stop_reason": "tool_calls",
            }),
            json.dumps({
                "timestamp": "2026-07-02T08:00:13Z",
                "event": "tool_call",
                "ticket_id": 4101,
                "session_id": "session-4101",
                "round": 2,
                "tool": "sql_query",
                "success": False,
                "duration_seconds": 2.2,
                "error": "database unavailable",
            }),
            json.dumps({
                "timestamp": "2026-07-02T08:00:20Z",
                "event": "agent_done",
                "ticket_id": 4101,
                "session_id": "session-4101",
                "status": "completed",
                "rounds": 2,
                "total_tokens": 2900,
            }),
        ]),
        encoding="utf-8",
    )

    body = admin_routes._read_recent_log_events(log_dir, limit=20)

    item = body["items"][0]
    assert item["rounds"] == 2
    assert [round_item["round"] for round_item in item["llm_rounds"]] == [1, 2]
    assert item["llm_rounds"][0]["input_tokens"] == 1200
    assert item["llm_rounds"][1]["output_tokens"] == 120
    assert item["llm_rounds"][0]["tools"][0]["name"] == "zendesk_get_ticket"
    assert item["llm_rounds"][1]["tools"][0]["name"] == "sql_query"
    assert item["llm_rounds"][1]["tools"][0]["status"] == "failed"
    assert item["llm_rounds"][1]["tools"][0]["error"] == "database unavailable"


def test_admin_run_records_quick_list_skips_detail_and_reads_newest_tail_first(
    tmp_path,
    monkeypatch,
) -> None:
    log_dir = tmp_path / "logs"
    archive_dir = tmp_path / "archive"
    log_dir.mkdir()
    archive_dir.mkdir()
    (log_dir / "events-2026-07-01.jsonl").write_text(
        json.dumps({
            "timestamp": "2026-07-01T08:00:00Z",
            "event": "agent_done",
            "ticket_id": 3999,
            "session_id": "session-3999",
        }),
        encoding="utf-8",
    )
    (log_dir / "events-2026-07-02.jsonl").write_text(
        "\n".join([
            json.dumps({
                "timestamp": "2026-07-02T08:00:00Z",
                "event": "agent_started",
                "ticket_id": 4101,
                "session_id": "session-4101",
            }),
            json.dumps({
                "timestamp": "2026-07-02T08:01:00Z",
                "event": "tool_call",
                "ticket_id": 4101,
                "session_id": "session-4101",
                "round": 1,
                "tool": "zendesk_get_ticket",
            }),
            json.dumps({
                "timestamp": "2026-07-02T08:02:00Z",
                "event": "agent_done",
                "ticket_id": 4101,
                "session_id": "session-4101",
                "rounds": 1,
            }),
        ]),
        encoding="utf-8",
    )

    def fail_archive_read(*_args):
        raise AssertionError("quick list must not read archive details")

    with monkeypatch.context() as context:
        context.setattr(admin_routes, "_read_archive_meta", fail_archive_read)
        body = admin_routes._read_recent_log_events(
            log_dir,
            limit=1,
            filters={"quick": True},
            archive_dir=archive_dir,
            include_detail=False,
        )

    item = body["items"][0]
    assert item["ticket_id"] == 4101
    assert item["detail_loaded"] is False
    assert "record_key" in item
    assert "events" not in item
    assert "llm_rounds" not in item
    assert body["scanned_files"] == 1
    assert body["raw_events_total"] == 1
    assert body["partial"] is True

    detail = admin_routes._read_recent_log_events(
        log_dir,
        limit=1,
        filters={"record_key": item["record_key"]},
        archive_dir=archive_dir,
        include_detail=True,
    )

    detail_item = detail["items"][0]
    assert detail_item["detail_loaded"] is True
    assert detail_item["ticket_id"] == 4101
    assert detail_item["event_count"] == 3
    assert [event["event"] for event in detail_item["events"]] == [
        "agent_started",
        "tool_call",
        "agent_done",
    ]


def test_admin_run_records_quick_list_uses_short_lived_cache(tmp_path) -> None:
    log_dir = tmp_path / "logs"
    archive_dir = tmp_path / "archive"
    log_dir.mkdir()
    archive_dir.mkdir()
    (log_dir / "events-2026-07-02.jsonl").write_text(
        json.dumps({
            "timestamp": "2026-07-02T08:02:00Z",
            "event": "agent_done",
            "ticket_id": 4101,
            "session_id": "session-4101",
            "rounds": 3,
        }),
        encoding="utf-8",
    )
    admin_routes._RUN_RECORD_CACHE.clear()

    first = admin_routes._read_recent_log_events_cached(
        log_dir,
        limit=50,
        filters={"quick": True},
        archive_dir=archive_dir,
        include_detail=False,
    )
    second = admin_routes._read_recent_log_events_cached(
        log_dir,
        limit=50,
        filters={"quick": True},
        archive_dir=archive_dir,
        include_detail=False,
    )

    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert second["items"][0]["ticket_id"] == 4101
    assert second["has_more"] is False


def test_admin_run_records_only_reads_archive_meta_for_returned_page(
    tmp_path,
    monkeypatch,
) -> None:
    log_dir = tmp_path / "logs"
    archive_dir = tmp_path / "archive"
    log_dir.mkdir()
    archive_dir.mkdir()
    (log_dir / "events-2026-07-02.jsonl").write_text(
        "\n".join([
            json.dumps({
                "timestamp": "2026-07-02T08:00:00Z",
                "event": "agent_done",
                "ticket_id": 4001,
                "session_id": "session-4001",
            }),
            json.dumps({
                "timestamp": "2026-07-02T08:01:00Z",
                "event": "agent_done",
                "ticket_id": 4002,
                "session_id": "session-4002",
            }),
        ]),
        encoding="utf-8",
    )
    calls = []

    def fake_read_archive_meta(_archive_dir, ticket_id, _cache):
        calls.append(ticket_id)
        return {
            "_archive_meta_path": f"/archive/{ticket_id}/meta.json",
            "tool_call_count": 1,
        }

    monkeypatch.setattr(admin_routes, "_read_archive_meta", fake_read_archive_meta)

    body = admin_routes._read_recent_log_events(
        log_dir,
        limit=1,
        archive_dir=archive_dir,
    )

    assert [item["ticket_id"] for item in body["items"]] == [4002]
    assert calls == ["4002"]
    assert body["summary"]["total"] == 2


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

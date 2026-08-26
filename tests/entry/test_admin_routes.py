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
from zd_agent.config import AppConfig, CustomMcpServiceConfig
from zd_agent.services.admin_routes import (
    ADMIN_HANDLERS_KEY,
    _load_index_html,
    register_admin_routes,
)
from zd_agent.services.app_context import (
    SESSION_MANAGER_KEY,
    TRIGGER_QUEUE_KEY,
    admin_config,
    app_config,
    install_saas_app_context,
)

REAL_ASYNC_CLIENT = httpx.AsyncClient
TEST_ADMIN_PASSWORD = "test-admin-password"


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
    register_admin_routes(
        app=app,
        cfg=cfg,
        state_dir=tmp_path / ".state",
        initial_admin_password=TEST_ADMIN_PASSWORD,
    )
    return app


async def _change_default_password(app, cookie: dict[str, str]) -> dict[str, str]:
    response = await app[ADMIN_HANDLERS_KEY]["password"](
        _Request(
            app=app,
            cookies=cookie,
            payload={
                "current_password": TEST_ADMIN_PASSWORD,
                "new_password": "new-admin-password",
            },
        )
    )
    return {"zd_admin_session": response.cookies["zd_admin_session"].value}


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
    assert "/admin/api/operations/status" in route_paths
    assert "/admin/api/models/discover" in route_paths
    assert "/admin/static/{name}" in route_paths


def test_admin_static_assets_are_split_from_html_shell() -> None:
    html = _load_index_html()
    shell_asset = admin_routes._load_static_asset("shell.css")

    assert "系统摘要" not in html
    assert "/admin/static/styles.css" in html
    assert "/admin/static/shell.css" in html
    assert "/admin/static/app.js" in html
    assert "最近运行事件" not in html
    assert shell_asset is not None
    assert shell_asset[1] == "text/css"
    assert ".nav-group-label" in shell_asset[0]


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
    assert "生产环境管理后台" not in html
    assert "使用 Google 登录" in html
    assert "使用飞书登录" in html
    assert "admin123" not in html
    assert "Storage Configuration" not in app_js
    assert "Deployment Status" not in app_js
    assert "Operations" not in html


def test_admin_shell_uses_focused_auth_and_grouped_responsive_navigation() -> None:
    html = _load_index_html()
    app_js = (
        resources.files("zd_agent.admin_static")
        .joinpath("app.js")
        .read_text(encoding="utf-8")
    )
    css = resources.files("zd_agent.admin_static").joinpath("styles.css").read_text(
        encoding="utf-8"
    ) + resources.files("zd_agent.admin_static").joinpath("shell.css").read_text(
        encoding="utf-8"
    )

    for symbol in [
        "auth-panel",
        "login-form",
        "mobile-nav-toggle",
        "sidebar-backdrop",
        "page-context",
    ]:
        assert symbol in html
    for symbol in [
        "PRIMARY_MENU",
        "MENU_ICONS",
        "toggleSidebar",
        "aria-current",
        "SIDEBAR_FOCUSABLE",
        "syncSidebarAccessibility",
        "handleSidebarKeydown",
        "syncOverlayAccessibility",
        "handleOverlayKeydown",
        "resetLoginPassword",
        "resetPasswordChangeFields",
        "!isMobile",
        'backdrop?.classList.remove("visible")',
        ".inert",
    ]:
        assert symbol in app_js
    for symbol in ["nav-group-label", ".sidebar.open", ".sidebar-backdrop.visible"]:
        assert symbol in css
    assert "const MENUS" not in app_js
    assert "event.target === event.currentTarget" in app_js
    assert "@media (max-width: 1100px)" in css
    assert ".integration-grid {" in css
    assert "把工单自动化控制权收回到一个地方" not in html
    assert "auth-visual" not in html


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
    for symbol in [
        "run-record-card",
        "record-expand",
        "round-timeline",
        "tool-call-row",
        "event-trace",
    ]:
        assert symbol in app_js
        assert symbol in css
    for text in ["原始事件", "扫描文件", "加载更多"]:
        assert text in app_js
    for symbol in ["module-guide", "summary-strip", "raw-json", "notice-card", "toast"]:
        assert symbol in app_js or symbol in css
    assert ".run-record-row" in css
    assert "grid-template-columns" in css
    assert "position: sticky" in css
    assert (
        'numberFromKeys(item, ["rounds", "round_count"]) || rounds.length || 1'
        in app_js
    )
    assert 'round: numberFromKeys(round, ["round", "round_number"]) ||' in app_js
    assert "round.round || round.index || index + 1" in app_js
    assert "仅聚合轮次" in app_js
    assert "聚合轮次" in app_js
    assert "后台刷新中" in app_js
    assert "缓存命中" in app_js
    assert "rawEvents" in app_js
    assert "查看完整日志" in app_js
    assert "renderCommandCenter" in app_js
    assert "quick: shouldQuickLoadRunRecords()" not in app_js
    assert "quick: true" in app_js
    assert "refreshRunRecordsInBackground();" in app_js
    assert "quick: false" in app_js
    assert "mergeCommandTickets" in app_js
    assert "renderRunRecordsShell" in app_js
    assert "detail: false" in app_js
    assert "ensureRunRecordDetail" in app_js
    assert "detail: true" in app_js
    assert "await Promise.allSettled" in app_js[
        app_js.index("async function renderOverview") : app_js.index("function goMenu")
    ]


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
    assert "mcp_services.custom" in app_js
    assert "openIntegrationConfig" in app_js
    assert "openCustomMcpConfig" in app_js
    assert "openMcpManager" in app_js
    assert "zendesk.mention_aliases" in app_js
    assert "fieldComparableValue" in app_js
    assert "json-textarea" in app_js
    assert "Intake 与等待策略" in app_js
    assert "模型连接与能力" in app_js
    assert "v2" not in app_js
    assert "模型运行参数" in app_js
    assert "llm.reasoning_effort" in app_js
    assert 'field.input === "model_picker"' in app_js
    assert "toggleModelPicker" in app_js
    assert "modelDiscoveryContext" in app_js
    assert "selectedModelCapabilities" in app_js
    assert "syncSelectedModelCapabilities" in app_js
    assert "能力未知时按不支持处理；不可人工覆盖" in app_js
    assert 'field.input === "vision_capability"' in app_js
    assert "syncProviderSecretVisibility" in app_js
    assert "pendingModelApiKey" in app_js
    assert 'api_key: apiKey' in app_js
    assert 'field.input === "reasoning_effort"' in app_js
    assert "reasoningEffortsForModel" in app_js
    assert "refreshReasoningEffortOptions" in app_js
    assert "reasoningEffortsForModel(model).length === 0" in app_js
    assert "reasoningUnsupportedReason" in app_js
    assert "reasoning_capabilities" in app_js
    assert "trigger_decider" not in app_js
    assert "llm.subagent" not in app_js
    assert "skills.enabled" not in app_js
    assert "skills.custom" not in app_js


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
async def test_admin_static_asset_serves_known_files_and_rejects_unknown(
    tmp_path,
) -> None:
    app = _app(tmp_path)
    login_response = await app[ADMIN_HANDLERS_KEY]["login"](
        _Request(
            app=app, payload={"username": "admin", "password": TEST_ADMIN_PASSWORD}
        )
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
        payload={"username": "admin", "password": TEST_ADMIN_PASSWORD},
    )

    response = await app[ADMIN_HANDLERS_KEY]["login"](request)

    assert response.status == 200
    assert "zd_admin_session" in response.cookies


@pytest.mark.asyncio
async def test_admin_login_honors_password_login_disabled(tmp_path) -> None:
    app = _app(tmp_path)
    admin_config(app).apply(
        app_config(app),
        {
            "reason": "disable password login",
            "changes": {"admin_auth.password_login_enabled": False},
        },
        actor="test",
        ip="127.0.0.1",
    )

    response = await app[ADMIN_HANDLERS_KEY]["login"](
        _Request(
            app=app,
            payload={"username": "admin", "password": TEST_ADMIN_PASSWORD},
        ),
    )

    assert response.status == 403
    assert json.loads(response.text)["error"] == "password_login_disabled"
    assert "zd_admin_session" not in response.cookies


@pytest.mark.asyncio
async def test_admin_login_rate_limits_failures_per_ip_and_username(tmp_path) -> None:
    app = _app(tmp_path)
    request = _Request(
        app=app,
        payload={"username": "admin", "password": "wrong"},
        remote="203.0.113.10",
    )

    responses = [
        await app[ADMIN_HANDLERS_KEY]["login"](request) for _attempt in range(6)
    ]
    other_ip_response = await app[ADMIN_HANDLERS_KEY]["login"](
        _Request(
            app=app,
            payload={"username": "admin", "password": TEST_ADMIN_PASSWORD},
            remote="203.0.113.11",
        ),
    )

    assert [response.status for response in responses[:5]] == [401] * 5
    assert responses[5].status == 429
    assert json.loads(responses[5].text)["error"] == "too_many_attempts"
    assert int(responses[5].headers["Retry-After"]) > 0
    assert other_ip_response.status == 200


@pytest.mark.asyncio
async def test_admin_login_does_not_trust_spoofed_forwarded_ip_from_public_peer(
    tmp_path,
) -> None:
    app = _app(tmp_path)

    responses = [
        await app[ADMIN_HANDLERS_KEY]["login"](
            _Request(
                app=app,
                payload={"username": "admin", "password": "wrong"},
                remote="8.8.8.8",
                headers={"X-Forwarded-For": f"198.51.100.{attempt}"},
            ),
        )
        for attempt in range(1, 7)
    ]

    assert [response.status for response in responses[:5]] == [401] * 5
    assert responses[5].status == 429


@pytest.mark.asyncio
async def test_admin_login_does_not_use_untrusted_x_forwarded_for_behind_proxy(
    tmp_path,
) -> None:
    app = _app(tmp_path)

    responses = [
        await app[ADMIN_HANDLERS_KEY]["login"](
            _Request(
                app=app,
                payload={"username": "admin", "password": "wrong"},
                remote="127.0.0.1",
                headers={"X-Forwarded-For": f"198.51.100.{attempt}"},
            ),
        )
        for attempt in range(1, 7)
    ]

    assert [response.status for response in responses[:5]] == [401] * 5
    assert responses[5].status == 429


@pytest.mark.asyncio
async def test_admin_login_uses_x_real_ip_from_loopback_proxy(tmp_path) -> None:
    app = _app(tmp_path)

    responses = [
        await app[ADMIN_HANDLERS_KEY]["login"](
            _Request(
                app=app,
                payload={"username": "admin", "password": "wrong"},
                remote="127.0.0.1",
                headers={"X-Real-IP": f"198.51.100.{attempt}"},
            ),
        )
        for attempt in range(1, 7)
    ]

    assert [response.status for response in responses] == [401] * 6


@pytest.mark.asyncio
async def test_admin_login_does_not_trust_x_real_ip_from_private_peer(tmp_path) -> None:
    app = _app(tmp_path)

    responses = [
        await app[ADMIN_HANDLERS_KEY]["login"](
            _Request(
                app=app,
                payload={"username": "admin", "password": "wrong"},
                remote="10.0.0.5",
                headers={"X-Real-IP": f"198.51.100.{attempt}"},
            ),
        )
        for attempt in range(1, 7)
    ]

    assert [response.status for response in responses[:5]] == [401] * 5
    assert responses[5].status == 429


def test_admin_client_ip_falls_back_without_recursion() -> None:
    request = _Request(app={}, remote="")

    assert admin_routes._login_rate_limit_ip(request) == "unknown"


@pytest.mark.asyncio
async def test_admin_config_requires_default_password_change(tmp_path) -> None:
    app = _app(tmp_path)
    login_response = await app[ADMIN_HANDLERS_KEY]["login"](
        _Request(
            app=app,
            payload={"username": "admin", "password": TEST_ADMIN_PASSWORD},
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
                "current_password": TEST_ADMIN_PASSWORD,
                "new_password": "new-admin-password",
            },
        )
    )
    new_cookie = {
        "zd_admin_session": password_response.cookies["zd_admin_session"].value,
    }
    allowed_response = await app[ADMIN_HANDLERS_KEY]["config_schema"](
        _Request(app=app, cookies=new_cookie)
    )

    assert password_response.status == 200
    assert "zd_admin_session" in password_response.cookies
    assert cookie["zd_admin_session"] != new_cookie["zd_admin_session"]
    assert allowed_response.status == 200


@pytest.mark.asyncio
async def test_admin_run_records_reads_recent_jsonl_events_with_summary_filters_and_redaction(
    tmp_path,
) -> None:
    app = _app(tmp_path)
    cfg = app_config(app)
    cfg.logging.log_dir = str(tmp_path / "logs")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "events-2026-06-29.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-06-29T10:00:00Z",
                        "event": "agent_started",
                        "ticket_id": 3134,
                        "module": "runtime",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-06-29T10:01:00Z",
                        "event": "agent_failed",
                        "ticket_id": 3135,
                        "module": "runtime",
                        "input_tokens": 1234,
                        "output_tokens": 56,
                        "api_key": "sk-test-secret",
                        "nested": {"token": "secret-token"},
                        "error": "boom",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-06-29T10:02:00Z",
                        "event": "agent_done",
                        "ticket_id": 3134,
                        "module": "reply",
                        "rounds": 5,
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    login_response = await app[ADMIN_HANDLERS_KEY]["login"](
        _Request(
            app=app,
            payload={"username": "admin", "password": TEST_ADMIN_PASSWORD},
        )
    )
    cookie = {"zd_admin_session": login_response.cookies["zd_admin_session"].value}
    cookie = await _change_default_password(app, cookie)

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
async def test_admin_run_records_ignore_internal_events_and_normalize_agent_done(
    tmp_path,
) -> None:
    app = _app(tmp_path)
    cfg = app_config(app)
    cfg.logging.log_dir = str(tmp_path / "logs")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    lines = [
        json.dumps(
            {
                "timestamp": f"2026-06-29T10:00:{second:02d}Z",
                "event": "self_user_id_resolved",
                "module": "mention.detector",
                "source": "webhook",
            }
        )
        for second in range(60)
    ]
    lines.append(
        json.dumps(
            {
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
            }
        )
    )
    lines.extend(
        json.dumps(
            {
                "timestamp": f"2026-06-29T10:02:{second:02d}Z",
                "event": "self_user_id_resolved",
                "module": "mention.detector",
                "source": "webhook",
            }
        )
        for second in range(60)
    )
    (log_dir / "events-2026-06-29.jsonl").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )
    login_response = await app[ADMIN_HANDLERS_KEY]["login"](
        _Request(
            app=app,
            payload={"username": "admin", "password": TEST_ADMIN_PASSWORD},
        )
    )
    cookie = {"zd_admin_session": login_response.cookies["zd_admin_session"].value}
    cookie = await _change_default_password(app, cookie)

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
async def test_admin_run_records_collapse_agent_lifecycle_to_one_ticket_run(
    tmp_path,
) -> None:
    app = _app(tmp_path)
    cfg = app_config(app)
    cfg.logging.log_dir = str(tmp_path / "logs")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "events-2026-06-29.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-06-29T10:00:00Z",
                        "event": "session_dispatched",
                        "ticket_id": 3172,
                        "session_id": "session-3172",
                        "module": "session_manager",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-06-29T10:00:01Z",
                        "event": "agent_start",
                        "ticket_id": 3172,
                        "session_id": "session-3172",
                        "trigger_type": "webhook",
                        "module": "runtime.runner",
                    }
                ),
                json.dumps(
                    {
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
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-06-29T10:04:37Z",
                        "event": "session_finished",
                        "ticket_id": 3172,
                        "status": "completed",
                        "rounds": 17,
                        "tokens": 18_327,
                        "elapsed": "4.6min",
                        "remaining_active": 0,
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    login_response = await app[ADMIN_HANDLERS_KEY]["login"](
        _Request(
            app=app,
            payload={"username": "admin", "password": TEST_ADMIN_PASSWORD},
        )
    )
    cookie = {"zd_admin_session": login_response.cookies["zd_admin_session"].value}
    cookie = await _change_default_password(app, cookie)

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
    cfg.archive.archive_dir = str(tmp_path / "archive")
    log_dir = tmp_path / "logs"
    archive_dir = tmp_path / "archive" / "3172"
    log_dir.mkdir()
    archive_dir.mkdir(parents=True)
    (log_dir / "events-2026-06-29.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
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
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-06-29T10:04:37Z",
                        "event": "session_finished",
                        "ticket_id": 3172,
                        "status": "completed",
                        "rounds": 17,
                        "tokens": 18_327,
                        "elapsed": "4.6min",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    (archive_dir / "meta.json").write_text(
        json.dumps(
            {
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
            }
        ),
        encoding="utf-8",
    )
    login_response = await app[ADMIN_HANDLERS_KEY]["login"](
        _Request(
            app=app,
            payload={"username": "admin", "password": TEST_ADMIN_PASSWORD},
        )
    )
    cookie = {"zd_admin_session": login_response.cookies["zd_admin_session"].value}
    cookie = await _change_default_password(app, cookie)

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


def test_admin_run_records_keep_retries_isolated_from_legacy_ticket_meta(
    tmp_path,
) -> None:
    log_dir = tmp_path / "logs"
    archive_base = tmp_path / "archive"
    legacy_archive = archive_base / "3485"
    log_dir.mkdir()
    legacy_archive.mkdir(parents=True)
    (log_dir / "events-2026-07-17.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-07-17T06:21:42Z",
                        "event": "agent_start",
                        "ticket_id": 3485,
                        "session_id": "first-session",
                        "model": "gpt-test",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-07-17T06:22:00Z",
                        "event": "llm_round",
                        "ticket_id": 3485,
                        "session_id": "first-session",
                        "round": 1,
                        "prompt_tokens": 1_000,
                        "completion_tokens": 100,
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-07-17T06:22:47Z",
                        "event": "connection_error_retry",
                        "ticket_id": 3485,
                        "round": 1,
                        "error_type": "APIConnectionError",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-07-17T06:25:20Z",
                        "event": "agent_done",
                        "ticket_id": 3485,
                        "session_id": "first-session",
                        "status": "deferred",
                        "rounds": 6,
                        "prompt_tokens": 214_982,
                        "completion_tokens": 4_956,
                        "total_tokens": 219_938,
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-07-17T06:25:21Z",
                        "event": "session_finished",
                        "ticket_id": 3485,
                        "session_id": "first-session",
                        "status": "deferred",
                        "rounds": 6,
                        "tokens": 219_938,
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-07-17T06:29:13Z",
                        "event": "agent_start",
                        "ticket_id": 3485,
                        "session_id": "second-session",
                        "model": "gpt-test",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-07-17T06:30:00Z",
                        "event": "llm_round",
                        "ticket_id": 3485,
                        "session_id": "second-session",
                        "round": 1,
                        "prompt_tokens": 2_000,
                        "completion_tokens": 200,
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-07-17T06:33:03Z",
                        "event": "tool_execution_timeout",
                        "ticket_id": 3485,
                        "round": 3,
                        "tool": "inspector_exec",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-07-17T06:35:26Z",
                        "event": "agent_done",
                        "ticket_id": 3485,
                        "session_id": "second-session",
                        "status": "completed",
                        "rounds": 7,
                        "prompt_tokens": 189_607,
                        "completion_tokens": 5_850,
                        "total_tokens": 195_457,
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-07-17T06:35:27Z",
                        "event": "session_finished",
                        "ticket_id": 3485,
                        "session_id": "second-session",
                        "status": "completed",
                        "rounds": 7,
                        "tokens": 195_457,
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    (legacy_archive / "meta.json").write_text(
        json.dumps(
            {
                "ticket_id": 3485,
                "session_id": "first-session",
                "status": "deferred",
                "rounds": 6,
                "total_tokens": 219_938,
                "tool_calls": [
                    {
                        "round": 1,
                        "tool": "legacy_first_run_tool",
                        "success": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    body = admin_routes._read_recent_log_events(
        log_dir,
        limit=10,
        filters={"ticket_id": "3485"},
        archive_dir=archive_base,
        include_detail=True,
    )

    by_session = {item["session_id"]: item for item in body["items"]}
    assert set(by_session) == {"first-session", "second-session"}
    assert by_session["first-session"]["status"] == "deferred"
    assert by_session["first-session"]["total_tokens"] == 219_938
    assert by_session["second-session"]["status"] == "completed"
    assert by_session["second-session"]["rounds"] == 7
    assert by_session["second-session"]["total_tokens"] == 195_457
    assert by_session["second-session"]["llm_rounds"][0]["input_tokens"] == 2_000

    first_events = {event["event"] for event in by_session["first-session"]["events"]}
    second_events = {event["event"] for event in by_session["second-session"]["events"]}
    assert "connection_error_retry" in first_events
    assert "connection_error_retry" not in second_events
    assert "tool_execution_timeout" not in first_events
    assert "tool_execution_timeout" in second_events


def test_admin_run_records_isolate_reused_session_id_by_run_window(tmp_path) -> None:
    log_dir = tmp_path / "logs"
    archive_base = tmp_path / "archive"
    legacy_archive = archive_base / "3485"
    log_dir.mkdir()
    legacy_archive.mkdir(parents=True)
    (log_dir / "events-2026-07-17.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-07-17T06:21:42Z",
                        "event": "agent_start",
                        "ticket_id": 3485,
                        "session_id": "reused-session",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-07-17T06:22:00Z",
                        "event": "tool_call",
                        "ticket_id": 3485,
                        "session_id": "reused-session",
                        "round": 1,
                        "tool": "first_run_tool",
                        "success": True,
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-07-17T06:25:20Z",
                        "event": "agent_done",
                        "ticket_id": 3485,
                        "session_id": "reused-session",
                        "status": "deferred",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-07-17T06:29:13Z",
                        "event": "agent_start",
                        "ticket_id": 3485,
                        "session_id": "reused-session",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-07-17T06:30:00Z",
                        "event": "tool_call",
                        "ticket_id": 3485,
                        "session_id": "reused-session",
                        "round": 1,
                        "tool": "second_run_tool",
                        "success": True,
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-07-17T06:35:26Z",
                        "event": "agent_done",
                        "ticket_id": 3485,
                        "session_id": "reused-session",
                        "status": "completed",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    (legacy_archive / "meta.json").write_text(
        json.dumps(
            {
                "ticket_id": 3485,
                "session_id": "reused-session",
                "status": "deferred",
                "archived_at": "2026-07-17T06:25:20Z",
                "tool_calls": [
                    {
                        "round": 1,
                        "tool": "first_run_archive_tool",
                        "success": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    body = admin_routes._read_recent_log_events(
        log_dir,
        limit=10,
        filters={"ticket_id": "3485"},
        archive_dir=archive_base,
        include_detail=True,
    )

    assert len(body["items"]) == 2
    by_status = {item["status"]: item for item in body["items"]}
    deferred_tools = {
        event.get("tool")
        for event in by_status["deferred"]["events"]
        if event.get("event") == "tool_call"
    }
    completed_tools = {
        event.get("tool")
        for event in by_status["completed"]["events"]
        if event.get("event") == "tool_call"
    }
    assert deferred_tools == {"first_run_tool"}
    assert completed_tools == {"second_run_tool"}
    assert by_status["deferred"]["archive_available"] is True
    assert by_status["deferred"]["tool_calls"][0]["name"] == "first_run_archive_tool"
    assert "archive_available" not in by_status["completed"]


def test_archive_meta_lookup_rejects_session_path_traversal(tmp_path) -> None:
    archive_dir = tmp_path / "archive"
    ticket_dir = archive_dir / "3485"
    (ticket_dir / "runs").mkdir(parents=True)
    outside_meta = tmp_path / "outside" / "meta.json"
    outside_meta.parent.mkdir(parents=True)
    outside_meta.write_text(
        json.dumps(
            {
                "session_id": "../../../outside",
                "status": "completed",
                "total_tokens": 999_999,
            }
        ),
        encoding="utf-8",
    )

    result = admin_routes._read_archive_meta(
        archive_dir,
        "3485",
        "../../../outside",
        {},
    )

    assert result is None

    (ticket_dir / "meta.json").write_text(
        json.dumps({"session_id": "..", "status": "completed"}),
        encoding="utf-8",
    )
    assert admin_routes._read_archive_meta(archive_dir, "3485", "..", {}) is None


def test_admin_run_records_trace_index_supports_module_filters(tmp_path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "events-2026-07-02.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-07-02T08:00:00Z",
                        "event": "agent_done",
                        "ticket_id": 4001,
                        "session_id": "session-4001",
                        "module": "runtime.runner",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-07-02T08:00:01Z",
                        "event": "tool_call",
                        "ticket_id": 4001,
                        "session_id": "session-4001",
                        "module": "db.gateway",
                        "tool": "sql_query",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-07-02T08:01:00Z",
                        "event": "agent_done",
                        "ticket_id": 4002,
                        "session_id": "session-4002",
                        "module": "runtime.runner",
                    }
                ),
            ]
        ),
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
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-07-02T08:00:00Z",
                        "event": "agent_start",
                        "ticket_id": 4101,
                        "session_id": "session-4101",
                        "model": "gpt-5.5",
                    }
                ),
                json.dumps(
                    {
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
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-07-02T08:00:05Z",
                        "event": "tool_call",
                        "ticket_id": 4101,
                        "session_id": "session-4101",
                        "round": 1,
                        "tool": "zendesk_get_ticket",
                        "success": True,
                        "duration_seconds": 1.5,
                    }
                ),
                json.dumps(
                    {
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
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-07-02T08:00:13Z",
                        "event": "tool_call",
                        "ticket_id": 4101,
                        "session_id": "session-4101",
                        "round": 2,
                        "tool": "sql_query",
                        "success": False,
                        "duration_seconds": 2.2,
                        "error": "database unavailable",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-07-02T08:00:20Z",
                        "event": "agent_done",
                        "ticket_id": 4101,
                        "session_id": "session-4101",
                        "status": "completed",
                        "rounds": 2,
                        "total_tokens": 2900,
                    }
                ),
            ]
        ),
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
        json.dumps(
            {
                "timestamp": "2026-07-01T08:00:00Z",
                "event": "agent_done",
                "ticket_id": 3999,
                "session_id": "session-3999",
            }
        ),
        encoding="utf-8",
    )
    (log_dir / "events-2026-07-02.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-07-02T08:00:00Z",
                        "event": "agent_started",
                        "ticket_id": 4101,
                        "session_id": "session-4101",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-07-02T08:01:00Z",
                        "event": "tool_call",
                        "ticket_id": 4101,
                        "session_id": "session-4101",
                        "round": 1,
                        "tool": "zendesk_get_ticket",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-07-02T08:02:00Z",
                        "event": "agent_done",
                        "ticket_id": 4101,
                        "session_id": "session-4101",
                        "rounds": 1,
                    }
                ),
            ]
        ),
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
        json.dumps(
            {
                "timestamp": "2026-07-02T08:02:00Z",
                "event": "agent_done",
                "ticket_id": 4101,
                "session_id": "session-4101",
                "rounds": 3,
            }
        ),
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
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-07-02T08:00:00Z",
                        "event": "agent_done",
                        "ticket_id": 4001,
                        "session_id": "session-4001",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-07-02T08:01:00Z",
                        "event": "agent_done",
                        "ticket_id": 4002,
                        "session_id": "session-4002",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_read_archive_meta(_archive_dir, ticket_id, _session_id, _cache):
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
        _Request(
            app=app, payload={"username": "admin", "password": TEST_ADMIN_PASSWORD}
        )
    )
    cookie = {"zd_admin_session": login_response.cookies["zd_admin_session"].value}
    cookie = await _change_default_password(app, cookie)

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
async def test_admin_integration_check_reports_reachability_and_writes_audit(
    tmp_path,
) -> None:
    app = _app(tmp_path)
    cfg = app_config(app)
    cfg.signoz.mcp_url = "https://signoz.example.test/mcp"
    cfg.signoz.mcp_api_key = "super-secret-key"
    login_response = await app[ADMIN_HANDLERS_KEY]["login"](
        _Request(
            app=app, payload={"username": "admin", "password": TEST_ADMIN_PASSWORD}
        )
    )
    cookie = {"zd_admin_session": login_response.cookies["zd_admin_session"].value}
    cookie = await _change_default_password(app, cookie)

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
async def test_admin_integration_check_supports_custom_mcp_services(tmp_path) -> None:
    app = _app(tmp_path)
    cfg = app_config(app)
    cfg.mcp_services.custom = [
        CustomMcpServiceConfig(
            name="linear_mcp",
            label="Linear MCP",
            description="Issue tracking",
            mcp_url="https://linear.example.test/custom",
            mcp_api_key="linear-secret-key",
            request_timeout_seconds=5,
            enabled=True,
        ),
    ]
    login_response = await app[ADMIN_HANDLERS_KEY]["login"](
        _Request(
            app=app, payload={"username": "admin", "password": TEST_ADMIN_PASSWORD}
        )
    )
    cookie = {"zd_admin_session": login_response.cookies["zd_admin_session"].value}
    cookie = await _change_default_password(app, cookie)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://linear.example.test/custom/mcp"
        assert request.headers["authorization"] == "Bearer linear-secret-key"
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
                payload={"integration": "linear_mcp"},
            )
        )

    body = json.loads(response.text)
    audit = await app[ADMIN_HANDLERS_KEY]["audit_logs"](
        _Request(app=app, cookies=cookie, query={"action": "integration_check"})
    )

    assert response.status == 200
    assert body["configured"] is True
    assert body["reachable"] is True
    assert body["label"] == "Linear MCP"
    assert "linear-secret-key" not in audit.text


@pytest.mark.asyncio
async def test_admin_operations_status_reports_live_capacity_and_queue(tmp_path) -> None:
    class Queue:
        path = tmp_path / ".state" / "trigger_queue.sqlite3"

        async def pending_count(self):
            return 4

        async def dead_letter_count(self):
            return 1

    class Manager:
        def get_all_sessions(self):
            return {
                "active": 1,
                "max_concurrent": 3,
                "active_sessions": [
                    {
                        "ticket_id": 4051,
                        "session_id": "run-secret-token",
                        "status": "analyzing",
                        "round_count": 2,
                        "total_tokens": 1940,
                        "elapsed": 83.2,
                        "start_time": "must-not-leak",
                    }
                ],
                "recent_completed": [],
            }

    app = _app(tmp_path)
    app[TRIGGER_QUEUE_KEY] = Queue()
    app[SESSION_MANAGER_KEY] = Manager()
    login_response = await app[ADMIN_HANDLERS_KEY]["login"](
        _Request(
            app=app, payload={"username": "admin", "password": TEST_ADMIN_PASSWORD}
        )
    )
    cookie = {"zd_admin_session": login_response.cookies["zd_admin_session"].value}
    cookie = await _change_default_password(app, cookie)

    response = await app[ADMIN_HANDLERS_KEY]["operations_status"](
        _Request(app=app, cookies=cookie)
    )

    body = json.loads(response.text)
    assert response.status == 200
    assert body["active"] == 1
    assert body["max_concurrent"] == 3
    assert body["queue"] == {"pending": 4, "dead_letter": 1}
    assert body["active_sessions"][0]["ticket_id"] == 4051
    assert "start_time" not in body["active_sessions"][0]
    assert set(body["background_tasks"]) == {
        "silence_task",
        "waiting_poller_task",
        "intake_poller_task",
        "mention_poller_task",
        "trigger_task",
    }


@pytest.mark.asyncio
async def test_admin_model_discovery_lists_models_without_exposing_key(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "stored-model-secret")
    app = _app(tmp_path)
    login_response = await app[ADMIN_HANDLERS_KEY]["login"](
        _Request(
            app=app, payload={"username": "admin", "password": TEST_ADMIN_PASSWORD}
        )
    )
    cookie = {"zd_admin_session": login_response.cookies["zd_admin_session"].value}
    cookie = await _change_default_password(app, cookie)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://models.example.test/v1/models"
        assert request.headers["authorization"] == "Bearer stored-model-secret"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "gpt-z", "owned_by": "OpenAI"},
                    {"id": "gpt-a", "owned_by": "OpenAI"},
                    {"id": "gpt-a", "owned_by": "duplicate"},
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    with patch(
        "zd_agent.services.admin_routes.httpx.AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    ):
        response = await app[ADMIN_HANDLERS_KEY]["model_discovery"](
            _Request(
                app=app,
                cookies=cookie,
                payload={
                    "provider": "openai",
                    "base_url": "https://models.example.test",
                },
            )
        )

    body = json.loads(response.text)
    audit = await app[ADMIN_HANDLERS_KEY]["audit_logs"](
        _Request(app=app, cookies=cookie, query={"action": "model_discovery"})
    )
    assert response.status == 200
    assert body["endpoint"] == "https://models.example.test/v1/models"
    assert [model["id"] for model in body["models"]] == ["gpt-a", "gpt-z"]
    assert body["count"] == 2
    assert body["models"][0]["capabilities"] == {
        "reasoning": "unknown",
        "reasoning_efforts": [],
        "reasoning_source": "unreported",
        "vision": "unknown",
        "vision_source": "unreported",
    }
    assert "stored-model-secret" not in response.text
    assert "stored-model-secret" not in audit.text


@pytest.mark.asyncio
async def test_admin_model_discovery_prefers_unsaved_key_without_exposing_it(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "stored-model-secret")
    app = _app(tmp_path)
    login_response = await app[ADMIN_HANDLERS_KEY]["login"](
        _Request(
            app=app, payload={"username": "admin", "password": TEST_ADMIN_PASSWORD}
        )
    )
    cookie = {"zd_admin_session": login_response.cookies["zd_admin_session"].value}
    cookie = await _change_default_password(app, cookie)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer unsaved-model-secret"
        return httpx.Response(200, json={"data": [{"id": "gpt-private"}]})

    transport = httpx.MockTransport(handler)
    with patch(
        "zd_agent.services.admin_routes.httpx.AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    ):
        response = await app[ADMIN_HANDLERS_KEY]["model_discovery"](
            _Request(
                app=app,
                cookies=cookie,
                payload={
                    "provider": "openai",
                    "base_url": "https://models.example.test",
                    "api_key": "unsaved-model-secret",
                },
            )
        )

    audit = await app[ADMIN_HANDLERS_KEY]["audit_logs"](
        _Request(app=app, cookies=cookie, query={"action": "model_discovery"})
    )
    assert response.status == 200
    assert "unsaved-model-secret" not in response.text
    assert "unsaved-model-secret" not in audit.text


def test_discovered_model_capabilities_are_model_specific_and_tri_state() -> None:
    reasoning_vision = admin_routes._discovered_model_capabilities(
        {"id": "gpt-5.6-terra"},
        provider="openai",
    )
    non_reasoning_vision = admin_routes._discovered_model_capabilities(
        {"id": "gpt-4.1"},
        provider="openai",
    )
    explicit_metadata = admin_routes._discovered_model_capabilities(
        {
            "id": "private-multimodal-model",
            "capabilities": {"vision": True, "reasoning": False},
        },
        provider="openai",
    )
    unknown = admin_routes._discovered_model_capabilities(
        {"id": "private-model"},
        provider="openai",
    )

    assert reasoning_vision["reasoning"] == "supported"
    assert reasoning_vision["reasoning_efforts"] == [
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
    assert reasoning_vision["vision"] == "supported"
    assert non_reasoning_vision["reasoning"] == "unsupported"
    assert non_reasoning_vision["vision"] == "supported"
    assert explicit_metadata["reasoning"] == "unsupported"
    assert explicit_metadata["vision"] == "supported"
    assert explicit_metadata["vision_source"] == "provider_metadata"
    assert unknown["reasoning"] == "unknown"
    assert unknown["vision"] == "unknown"


def test_anthropic_discovery_disables_unimplemented_reasoning_effort_adapter() -> None:
    capabilities = admin_routes._discovered_model_capabilities(
        {
            "id": "claude-sonnet-4-5",
            "capabilities": {"reasoning": True},
            "input_modalities": ["text", "image"],
        },
        provider="anthropic",
    )

    assert capabilities["reasoning"] == "unsupported"
    assert capabilities["reasoning_source"] == "agent_adapter"
    assert capabilities["vision"] == "supported"


@pytest.mark.asyncio
async def test_admin_deploy_status_includes_runtime_metadata_and_pending_restart(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("ZD_AGENT_IMAGE_TAG", "sha-test")
    monkeypatch.setenv("GITHUB_SHA", "abc123")
    app = _app(tmp_path)
    login_response = await app[ADMIN_HANDLERS_KEY]["login"](
        _Request(
            app=app, payload={"username": "admin", "password": TEST_ADMIN_PASSWORD}
        )
    )
    cookie = {"zd_admin_session": login_response.cookies["zd_admin_session"].value}
    cookie = await _change_default_password(app, cookie)
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

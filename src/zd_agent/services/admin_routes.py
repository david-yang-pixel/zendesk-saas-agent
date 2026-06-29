"""Admin console routes."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any

import httpx
from aiohttp import web

from zd_agent.config import AppConfig
from zd_agent.services.admin_auth import AdminAuthStore
from zd_agent.services.admin_config import AdminConfigStore
from zd_agent.services.app_context import (
    admin_auth,
    admin_config,
    app_config,
    install_admin_context,
)

SESSION_COOKIE = "zd_admin_session"
ADMIN_HANDLERS_KEY = web.AppKey("zd_agent.admin_handlers", dict)
ADMIN_STATIC_FILES = {
    "app.js": "application/javascript",
    "styles.css": "text/css",
}
SENSITIVE_KEY_RE = re.compile(
    r"password|token|secret|api[_-]?key|apikey|authorization",
    re.IGNORECASE,
)
ERROR_TOKEN_RE = re.compile(r"error|failed|failure|exception|traceback", re.IGNORECASE)
INTEGRATION_SPECS: dict[str, dict[str, str]] = {
    "zendesk_mcp": {
        "label": "Zendesk MCP",
        "url_path": "zendesk_mcp.mcp_url",
        "key_path": "zendesk_mcp.mcp_api_key",
        "timeout_path": "zendesk_mcp.request_timeout_seconds",
        "kind": "mcp",
    },
    "db_gateway": {
        "label": "DB Gateway",
        "url_path": "db_gateway.url",
        "key_path": "db_gateway.api_key",
        "timeout_path": "db_gateway.request_timeout_seconds",
        "kind": "mcp",
    },
    "signoz": {
        "label": "SigNoz MCP",
        "url_path": "signoz.mcp_url",
        "key_path": "signoz.mcp_api_key",
        "timeout_path": "signoz.request_timeout_seconds",
        "kind": "mcp",
    },
    "source_mcp": {
        "label": "Source MCP",
        "url_path": "source_mcp.mcp_url",
        "key_path": "source_mcp.mcp_api_key",
        "timeout_path": "source_mcp.request_timeout_seconds",
        "kind": "mcp",
    },
    "github_mcp": {
        "label": "GitHub MCP",
        "url_path": "github_mcp.mcp_url",
        "key_path": "github_mcp.mcp_api_key",
        "timeout_path": "github_mcp.request_timeout_seconds",
        "kind": "mcp",
    },
    "docs_tool_service": {
        "label": "Docs Tool Service",
        "url_path": "docs_tool_service.mcp_url",
        "key_path": "docs_tool_service.mcp_api_key",
        "timeout_path": "docs_tool_service.request_timeout_seconds",
        "kind": "mcp",
    },
    "notification_service": {
        "label": "Notification Service",
        "url_path": "notification_service.mcp_url",
        "key_path": "notification_service.mcp_api_key",
        "timeout_path": "notification_service.request_timeout_seconds",
        "kind": "mcp",
    },
    "sandbox_worker": {
        "label": "Sandbox Worker",
        "url_path": "sandbox_worker.mcp_url",
        "key_path": "sandbox_worker.mcp_api_key",
        "timeout_path": "sandbox_worker.request_timeout_seconds",
        "kind": "mcp",
    },
    "public_reply_browser_relay": {
        "label": "Public Reply Browser Relay",
        "url_path": "public_reply.browser_relay_url",
        "key_path": "public_reply.browser_relay_api_key",
        "timeout_path": "public_reply.browser_relay_timeout_seconds",
        "kind": "http",
    },
}


def _remote_ip(request: web.Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.remote or ""


def _session_token(request: web.Request) -> str:
    return request.cookies.get(SESSION_COOKIE, "")


def _cfg_value(cfg: AppConfig, path: str) -> Any:
    cur: Any = cfg
    for part in path.split("."):
        cur = getattr(cur, part)
    return cur


def _current_admin(request: web.Request) -> str | None:
    return admin_auth(request.app).validate_session(_session_token(request))


def _require_admin(
    request: web.Request,
    *,
    allow_password_change: bool = False,
) -> tuple[str | None, web.Response | None]:
    username = _current_admin(request)
    if username is None:
        return None, web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    if (
        not allow_password_change
        and admin_auth(request.app).requires_password_change(username)
    ):
        return username, web.json_response(
            {"ok": False, "error": "password_change_required"},
            status=403,
        )
    return username, None


def _json_error(error: str, *, status: int) -> web.Response:
    return web.json_response({"ok": False, "error": error}, status=status)


def _request_is_secure(request: web.Request) -> bool:
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "").lower()
    return bool(getattr(request, "secure", False)) or forwarded_proto == "https"


def _load_index_html() -> str:
    return (
        resources.files("zd_agent.admin_static")
        .joinpath("index.html")
        .read_text(encoding="utf-8")
    )


def _load_static_asset(name: str) -> tuple[str, str] | None:
    if name not in ADMIN_STATIC_FILES:
        return None
    text = (
        resources.files("zd_agent.admin_static")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )
    return text, ADMIN_STATIC_FILES[name]


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "开启"}


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds = seconds / 1000
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    raw = str(value).strip()
    if not raw:
        return None
    if raw.isdigit():
        return _parse_datetime(int(raw))
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _event_datetime(item: dict[str, Any]) -> datetime | None:
    for key in ("timestamp", "ts", "time", "created_at"):
        parsed = _parse_datetime(item.get(key))
        if parsed is not None:
            return parsed
    return None


def _redact_value(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {
            str(entry_key): _redact_value(entry_value, str(entry_key))
            for entry_key, entry_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item, key) for item in value]
    if SENSITIVE_KEY_RE.search(key):
        return "[REDACTED]" if value else value
    return value


def _is_error_event(item: dict[str, Any]) -> bool:
    text = " ".join(
        str(item.get(key) or "")
        for key in ("event", "status", "level", "error", "exception", "traceback")
    )
    return bool(ERROR_TOKEN_RE.search(text))


def _query_limit(request: web.Request, *, default: int = 100) -> int:
    try:
        value = int(request.query.get("limit", str(default)))  # type: ignore[attr-defined]
    except Exception:
        value = default
    return max(1, min(value, 500))


def _run_filters_from_query(query: Any) -> dict[str, Any]:
    return {
        "ticket_id": str(query.get("ticket_id", "") or "").strip(),
        "event": str(query.get("event", "") or "").strip().lower(),
        "module": str(query.get("module", "") or "").strip().lower(),
        "level": str(query.get("level", "") or "").strip().lower(),
        "errors_only": _truthy(query.get("errors_only")),
        "since": _parse_datetime(query.get("since")),
        "until": _parse_datetime(query.get("until")),
    }


def _public_run_filters(filters: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in filters.items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def _matches_run_filters(item: dict[str, Any], filters: dict[str, Any]) -> bool:
    ticket_id = filters.get("ticket_id")
    if ticket_id and ticket_id not in {
        str(item.get("ticket_id") or ""),
        str(item.get("ticket") or ""),
    }:
        return False
    event = filters.get("event")
    if event and event not in str(item.get("event") or "").lower():
        return False
    module = filters.get("module")
    if module and module not in str(item.get("module") or "").lower():
        return False
    level = filters.get("level")
    if level and level not in str(item.get("level") or "").lower():
        return False
    if filters.get("errors_only") and not _is_error_event(item):
        return False
    item_time = _event_datetime(item)
    since = filters.get("since")
    until = filters.get("until")
    if since and (item_time is None or item_time < since):
        return False
    if until and (item_time is None or item_time > until):
        return False
    return True


def _run_summary(
    *,
    total: int,
    filtered: list[dict[str, Any]],
    all_items: list[dict[str, Any]],
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    today_items = [
        item for item in all_items
        if (event_time := _event_datetime(item)) is not None
        and event_time.date() == now.date()
    ]
    today_ticket_ids = {
        str(item.get("ticket_id") or item.get("ticket"))
        for item in today_items
        if item.get("ticket_id") or item.get("ticket")
    }
    latest_timestamp = ""
    if filtered:
        latest_time = _event_datetime(filtered[0])
        if latest_time is not None:
            latest_timestamp = latest_time.isoformat()
    return {
        "total": total,
        "filtered": len(filtered),
        "today_events": len(today_items),
        "today_tickets": len(today_ticket_ids),
        "error_events": sum(1 for item in filtered if _is_error_event(item)),
        "latest_timestamp": latest_timestamp,
    }


def _read_recent_log_events(
    log_dir: Path,
    *,
    limit: int,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    limit = max(1, min(limit, 500))
    filters = filters or {}
    if not log_dir.exists():
        return {
            "items": [],
            "summary": _run_summary(total=0, filtered=[], all_items=[]),
            "filters": _public_run_filters(filters),
            "log_dir": str(log_dir),
            "exists": False,
        }

    items: list[dict[str, Any]] = []
    all_items: list[dict[str, Any]] = []
    matched_items: list[dict[str, Any]] = []
    paths = sorted(log_dir.glob("events-*.jsonl"), reverse=True)
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            item["_source_file"] = path.name
            all_items.append(item)
            if not _matches_run_filters(item, filters):
                continue
            matched_items.append(item)
            if len(items) < limit:
                items.append(_redact_value(item))
    return {
        "items": items,
        "summary": _run_summary(
            total=len(all_items),
            filtered=matched_items,
            all_items=all_items,
        ),
        "filters": _public_run_filters(filters),
        "log_dir": str(log_dir),
        "exists": True,
    }


def _mcp_endpoint(url: str) -> str:
    base = (url or "").strip().rstrip("/")
    if not base:
        return ""
    return base if base.endswith("/mcp") else f"{base}/mcp"


async def _probe_integration(
    *,
    name: str,
    cfg: AppConfig,
) -> dict[str, Any]:
    spec = INTEGRATION_SPECS.get(name)
    checked_at = datetime.now(timezone.utc).isoformat()
    if spec is None:
        raise ValueError("unsupported_integration")

    url = str(_cfg_value(cfg, spec["url_path"]) or "").strip().rstrip("/")
    api_key = str(_cfg_value(cfg, spec["key_path"]) or "").strip()
    timeout_seconds = float(_cfg_value(cfg, spec["timeout_path"]) or 10)
    configured = bool(url and api_key)
    base = {
        "ok": True,
        "integration": name,
        "label": spec["label"],
        "configured": configured,
        "reachable": False,
        "status": "missing_config" if not configured else "unknown",
        "detail": "URL or API key is not configured" if not configured else "",
        "checked_at": checked_at,
    }
    if not configured:
        return base

    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(
            timeout=min(max(timeout_seconds, 3), 20),
            trust_env=False,
        ) as client:
            if spec["kind"] == "mcp":
                response = await client.post(
                    _mcp_endpoint(url),
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "zd-agent-admin", "version": "1"},
                        },
                    },
                    headers={
                        **headers,
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                    },
                )
            else:
                response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return {
            **base,
            "status": "unreachable",
            "detail": exc.__class__.__name__,
        }

    reachable = response.status_code < 500
    status = "ok" if 200 <= response.status_code < 400 else "http_error"
    return {
        **base,
        "reachable": reachable,
        "status": status,
        "detail": f"HTTP {response.status_code}",
    }


async def handle_admin_index(_request: web.Request) -> web.Response:
    return web.Response(text=_load_index_html(), content_type="text/html")


async def handle_admin_static(request: web.Request) -> web.Response:
    name = str(request.match_info.get("name", ""))
    asset = _load_static_asset(name)
    if asset is None:
        return web.Response(status=404, text="not found")
    text, content_type = asset
    return web.Response(text=text, content_type=content_type, charset="utf-8")


async def handle_admin_login(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:
        return _json_error("bad_request", status=400)
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    result = admin_auth(request.app).verify_password(username, password)
    if not result.ok:
        return _json_error("invalid_credentials", status=401)
    token = admin_auth(request.app).create_session(username)
    response = web.json_response({
        "ok": True,
        "username": username,
        "force_password_change": result.force_password_change,
    })
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="Lax",
        secure=_request_is_secure(request),
        path="/",
        max_age=24 * 60 * 60,
    )
    admin_config(request.app).audit_event(
        actor=username,
        ip=_remote_ip(request),
        action="admin_login",
        reason="",
    )
    return response


async def handle_admin_logout(request: web.Request) -> web.Response:
    username = _current_admin(request) or "anonymous"
    admin_auth(request.app).revoke_session(_session_token(request))
    response = web.json_response({"ok": True})
    response.del_cookie(SESSION_COOKIE, path="/")
    admin_config(request.app).audit_event(
        actor=username,
        ip=_remote_ip(request),
        action="admin_logout",
    )
    return response


async def handle_admin_me(request: web.Request) -> web.Response:
    username = _current_admin(request)
    if username is None:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    return web.json_response({
        "ok": True,
        "username": username,
        "force_password_change": admin_auth(request.app).requires_password_change(username),
    })


async def handle_admin_password(request: web.Request) -> web.Response:
    username, unauthorized = _require_admin(request, allow_password_change=True)
    if unauthorized is not None:
        return unauthorized
    try:
        payload = await request.json()
    except Exception:
        return _json_error("bad_request", status=400)
    current_password = str(payload.get("current_password") or "")
    new_password = str(payload.get("new_password") or "")
    if not admin_auth(request.app).change_password(
        username=username or "",
        current_password=current_password,
        new_password=new_password,
    ):
        return _json_error("password_change_failed", status=400)
    admin_config(request.app).audit_event(
        actor=username or "",
        ip=_remote_ip(request),
        action="admin_password_changed",
    )
    return web.json_response({"ok": True})


async def handle_admin_config_schema(request: web.Request) -> web.Response:
    _username, unauthorized = _require_admin(request)
    if unauthorized is not None:
        return unauthorized
    return web.json_response(admin_config(request.app).schema())


async def handle_admin_config_effective(request: web.Request) -> web.Response:
    _username, unauthorized = _require_admin(request)
    if unauthorized is not None:
        return unauthorized
    return web.json_response(admin_config(request.app).effective_config(app_config(request.app)))


async def handle_admin_config_preview(request: web.Request) -> web.Response:
    _username, unauthorized = _require_admin(request)
    if unauthorized is not None:
        return unauthorized
    try:
        payload = await request.json()
    except Exception:
        return _json_error("bad_request", status=400)
    try:
        preview = admin_config(request.app).preview(app_config(request.app), payload)
    except ValueError as exc:
        return _json_error(str(exc), status=400)
    return web.json_response(preview)


async def handle_admin_config_apply(request: web.Request) -> web.Response:
    username, unauthorized = _require_admin(request)
    if unauthorized is not None:
        return unauthorized
    try:
        payload = await request.json()
    except Exception:
        return _json_error("bad_request", status=400)
    try:
        result = admin_config(request.app).apply(
            app_config(request.app),
            payload,
            actor=username or "",
            ip=_remote_ip(request),
        )
    except ValueError as exc:
        return _json_error(str(exc), status=400)
    return web.json_response(result)


async def handle_admin_audit_logs(request: web.Request) -> web.Response:
    _username, unauthorized = _require_admin(request)
    if unauthorized is not None:
        return unauthorized
    return web.json_response(
        admin_config(request.app).list_audit_logs(
            limit=_query_limit(request),
            action=str(request.query.get("action", "") or ""),  # type: ignore[attr-defined]
            actor=str(request.query.get("actor", "") or ""),  # type: ignore[attr-defined]
        )
    )


async def handle_admin_run_records(request: web.Request) -> web.Response:
    _username, unauthorized = _require_admin(request)
    if unauthorized is not None:
        return unauthorized
    cfg = app_config(request.app)
    filters = _run_filters_from_query(request.query)  # type: ignore[attr-defined]
    payload = _read_recent_log_events(
        Path(cfg.logging.log_dir),
        limit=_query_limit(request),
        filters=filters,
    )
    payload["jsonl_enabled"] = bool(cfg.logging.jsonl_enabled)
    return web.json_response(payload)


async def handle_admin_integration_check(request: web.Request) -> web.Response:
    username, unauthorized = _require_admin(request)
    if unauthorized is not None:
        return unauthorized
    try:
        payload = await request.json()
    except Exception:
        return _json_error("bad_request", status=400)
    integration = str(payload.get("integration") or "").strip()
    try:
        result = await _probe_integration(name=integration, cfg=app_config(request.app))
    except ValueError as exc:
        return _json_error(str(exc), status=400)
    admin_config(request.app).audit_event(
        actor=username or "",
        ip=_remote_ip(request),
        action="integration_check",
        reason=integration,
        diff=[{
            "path": integration,
            "label": result.get("label", integration),
            "group": "工具与集成",
            "old": "",
            "new": {
                "configured": result.get("configured"),
                "reachable": result.get("reachable"),
                "status": result.get("status"),
                "detail": result.get("detail"),
            },
            "secret": False,
            "restart_required": False,
        }],
    )
    return web.json_response(result)


async def handle_admin_deploy_status(request: web.Request) -> web.Response:
    _username, unauthorized = _require_admin(request)
    if unauthorized is not None:
        return unauthorized
    cfg = app_config(request.app)
    effective = admin_config(request.app).effective_config(cfg)["values"]
    google_secret = effective.get("admin_auth.google_client_secret") or {}
    feishu_secret = effective.get("admin_auth.feishu_app_secret") or {}
    return web.json_response({
        "ok": True,
        "agent": {"status": "running"},
        "image": {
            "name": os.environ.get("ZD_AGENT_IMAGE", "zd-agent"),
            "tag": (
                os.environ.get("ZD_AGENT_IMAGE_TAG")
                or os.environ.get("DEPLOY_TAG")
                or "latest"
            ),
            "commit_sha": (
                os.environ.get("ZD_AGENT_COMMIT_SHA")
                or os.environ.get("GITHUB_SHA")
                or ""
            ),
        },
        "pending_restart": admin_config(request.app).pending_restart(cfg),
        "admin_auth": {
            "password_login": {
                "enabled": bool(effective.get("admin_auth.password_login_enabled", True)),
                "default_username": "admin",
            },
            "google_sso": {
                "enabled": bool(effective.get("admin_auth.google_sso_enabled", False)),
                "configured": bool(effective.get("admin_auth.google_client_id"))
                and bool(google_secret.get("configured")),
            },
            "feishu_sso": {
                "enabled": bool(effective.get("admin_auth.feishu_sso_enabled", False)),
                "configured": bool(effective.get("admin_auth.feishu_app_id"))
                and bool(feishu_secret.get("configured")),
            },
        },
        "storage": {
            "base_dir": cfg.resources.base_dir,
            "state_dir": cfg.resources.state_dir,
            "log_dir": cfg.logging.log_dir,
        },
    })


def register_admin_routes(
    *,
    app: web.Application,
    cfg: AppConfig,
    state_dir: Path,
) -> None:
    """Register admin UI and JSON API routes."""
    state_dir.mkdir(parents=True, exist_ok=True)
    auth_store = AdminAuthStore(state_dir / "admin_auth.json")
    config_store = AdminConfigStore(state_dir)
    install_admin_context(app=app, auth_store=auth_store, config_store=config_store)

    handlers: dict[str, Any] = {
        "static_asset": handle_admin_static,
        "login": handle_admin_login,
        "logout": handle_admin_logout,
        "me": handle_admin_me,
        "password": handle_admin_password,
        "config_schema": handle_admin_config_schema,
        "config_effective": handle_admin_config_effective,
        "config_preview": handle_admin_config_preview,
        "config_apply": handle_admin_config_apply,
        "audit_logs": handle_admin_audit_logs,
        "run_records": handle_admin_run_records,
        "integration_check": handle_admin_integration_check,
        "deploy_status": handle_admin_deploy_status,
    }
    app[ADMIN_HANDLERS_KEY] = handlers

    app.router.add_get("/admin", handle_admin_index)
    app.router.add_get("/admin/", handle_admin_index)
    app.router.add_get("/admin/static/{name}", handle_admin_static)
    app.router.add_post("/admin/api/auth/login", handle_admin_login)
    app.router.add_post("/admin/api/auth/logout", handle_admin_logout)
    app.router.add_get("/admin/api/auth/me", handle_admin_me)
    app.router.add_post("/admin/api/auth/password", handle_admin_password)
    app.router.add_get("/admin/api/config/schema", handle_admin_config_schema)
    app.router.add_get("/admin/api/config/effective", handle_admin_config_effective)
    app.router.add_post("/admin/api/config/preview", handle_admin_config_preview)
    app.router.add_post("/admin/api/config/apply", handle_admin_config_apply)
    app.router.add_get("/admin/api/run-records", handle_admin_run_records)
    app.router.add_get("/admin/api/audit-logs", handle_admin_audit_logs)
    app.router.add_post("/admin/api/integrations/check", handle_admin_integration_check)
    app.router.add_get("/admin/api/deploy/status", handle_admin_deploy_status)

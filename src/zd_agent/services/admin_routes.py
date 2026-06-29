"""Admin console routes."""
from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

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


def _remote_ip(request: web.Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.remote or ""


def _session_token(request: web.Request) -> str:
    return request.cookies.get(SESSION_COOKIE, "")


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


def _read_recent_log_events(log_dir: Path, *, limit: int) -> dict[str, Any]:
    if limit <= 0:
        limit = 100
    limit = min(limit, 500)
    if not log_dir.exists():
        return {"items": [], "log_dir": str(log_dir), "exists": False}

    items: list[dict[str, Any]] = []
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
            items.append(item)
            if len(items) >= limit:
                return {"items": items, "log_dir": str(log_dir), "exists": True}
    return {"items": items, "log_dir": str(log_dir), "exists": True}


async def handle_admin_index(_request: web.Request) -> web.Response:
    return web.Response(text=_load_index_html(), content_type="text/html")


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
    try:
        limit = int(request.query.get("limit", "100"))  # type: ignore[attr-defined]
    except Exception:
        limit = 100
    return web.json_response(admin_config(request.app).list_audit_logs(limit=limit))


async def handle_admin_run_records(request: web.Request) -> web.Response:
    _username, unauthorized = _require_admin(request)
    if unauthorized is not None:
        return unauthorized
    try:
        limit = int(request.query.get("limit", "100"))  # type: ignore[attr-defined]
    except Exception:
        limit = 100
    cfg = app_config(request.app)
    payload = _read_recent_log_events(Path(cfg.logging.log_dir), limit=limit)
    payload["jsonl_enabled"] = bool(cfg.logging.jsonl_enabled)
    return web.json_response(payload)


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
        "deploy_status": handle_admin_deploy_status,
    }
    app[ADMIN_HANDLERS_KEY] = handlers

    app.router.add_get("/admin", handle_admin_index)
    app.router.add_get("/admin/", handle_admin_index)
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
    app.router.add_get("/admin/api/deploy/status", handle_admin_deploy_status)

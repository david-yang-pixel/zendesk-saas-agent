"""Admin console routes."""

from __future__ import annotations

import json
import ipaddress
import os
import re
import time
from dataclasses import asdict, is_dataclass
from copy import deepcopy
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from aiohttp import web

from zd_agent.config import AppConfig, EnvSettings
from zd_agent.services.admin_auth import AdminAuthStore
from zd_agent.services.admin_config import AdminConfigStore
from zd_agent.services.app_context import (
    BACKGROUND_TASK_KEYS,
    BACKGROUND_TASK_NAMES,
    admin_auth,
    admin_config,
    app_config,
    install_admin_context,
    session_manager,
    trigger_queue,
)

SESSION_COOKIE = "zd_admin_session"
ADMIN_HANDLERS_KEY = web.AppKey("zd_agent.admin_handlers", dict)
ADMIN_STATIC_FILES = {
    "app.js": "application/javascript",
    "dify-logo-on-blue.svg": "image/svg+xml",
    "dify-logo.svg": "image/svg+xml",
    "shell.css": "text/css",
    "styles.css": "text/css",
}
SENSITIVE_KEY_RE = re.compile(
    r"password|token|secret|api[_-]?key|apikey|authorization",
    re.IGNORECASE,
)
ERROR_TOKEN_RE = re.compile(r"error|failed|failure|exception|traceback", re.IGNORECASE)
TOKEN_COUNT_KEYS = {
    "input_tokens",
    "output_tokens",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "token_count",
    "tokens_in",
    "tokens_out",
    "tokens",
    "max_tokens",
}
RUN_RECORD_EVENTS = {
    "agent_start",
    "agent_started",
    "agent_done",
    "agent_error",
    "agent_failed",
    "session_dispatched",
    "session_queued",
    "session_finished",
    "session_crash",
}
RUNNING_RECORD_EVENTS = {
    "agent_start",
    "agent_started",
    "session_dispatched",
    "session_queued",
}
FAILED_RECORD_EVENTS = {
    "agent_error",
    "agent_failed",
    "session_crash",
}
SESSION_END_RECORD_EVENTS = {
    "session_finished",
}
RUN_EVENT_PRIORITY = {
    "session_queued": 10,
    "session_dispatched": 20,
    "agent_start": 30,
    "agent_started": 30,
    "agent_error": 70,
    "agent_failed": 70,
    "session_crash": 75,
    "session_finished": 80,
    "agent_done": 90,
}
DURATION_RE = re.compile(
    r"^\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>ms|millisecond|milliseconds|s|sec|secs|second|seconds|m|min|mins|minute|minutes)?\s*$",
    re.IGNORECASE,
)
TICKET_ID_RE = re.compile(r"^\d+$")
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
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
    "public_reply_browser_relay": {
        "label": "Public Reply Browser Relay",
        "url_path": "public_reply.browser_relay_url",
        "key_path": "public_reply.browser_relay_api_key",
        "timeout_path": "public_reply.browser_relay_timeout_seconds",
        "kind": "http",
    },
}
RUN_RECORD_CACHE_TTL_SECONDS = 3.0
RUN_RECORD_CACHE_MAX_ITEMS = 24
RUN_RECORD_FAST_MAX_SCAN_LINES = 50_000
_RUN_RECORD_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _remote_ip(request: web.Request) -> str:
    return _trusted_client_ip(request)


def _login_rate_limit_ip(request: web.Request) -> str:
    return _trusted_client_ip(request)


def _trusted_client_ip(request: web.Request) -> str:
    peer_ip = (request.remote or "").strip()
    try:
        peer_address = ipaddress.ip_address(peer_ip)
    except ValueError:
        return "unknown"
    if peer_address.is_loopback:
        forwarded_ip = request.headers.get("X-Real-IP", "").strip()
        try:
            return str(ipaddress.ip_address(forwarded_ip))
        except ValueError:
            pass
    return str(peer_address)


def _session_token(request: web.Request) -> str:
    return request.cookies.get(SESSION_COOKIE, "")


def _cfg_value(cfg: AppConfig, path: str) -> Any:
    cur: Any = cfg
    for part in path.split("."):
        cur = getattr(cur, part)
    return cur


def _integration_spec(name: str, cfg: AppConfig) -> dict[str, Any] | None:
    spec = INTEGRATION_SPECS.get(name)
    if spec is not None:
        return {
            "label": spec["label"],
            "url": str(_cfg_value(cfg, spec["url_path"]) or "").strip().rstrip("/"),
            "api_key": str(_cfg_value(cfg, spec["key_path"]) or "").strip(),
            "timeout_seconds": float(_cfg_value(cfg, spec["timeout_path"]) or 10),
            "kind": spec["kind"],
        }
    for service in cfg.mcp_services.custom:
        if service.name == name:
            return {
                "label": service.label or service.name,
                "url": service.mcp_url.strip().rstrip("/"),
                "api_key": service.mcp_api_key.strip(),
                "timeout_seconds": float(service.request_timeout_seconds or 10),
                "kind": "mcp",
                "enabled": service.enabled,
            }
    return None


def _current_admin(request: web.Request) -> str | None:
    return admin_auth(request.app).validate_session(_session_token(request))


def _require_admin(
    request: web.Request,
    *,
    allow_password_change: bool = False,
) -> tuple[str | None, web.Response | None]:
    username = _current_admin(request)
    if username is None:
        return None, web.json_response(
            {"ok": False, "error": "unauthorized"}, status=401
        )
    if not allow_password_change and admin_auth(request.app).requires_password_change(
        username
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
    key_normalized = key.lower().replace("-", "_")
    if (
        key_normalized in TOKEN_COUNT_KEYS
        and isinstance(value, int | float)
        and not isinstance(value, bool)
    ):
        return value
    if SENSITIVE_KEY_RE.search(key):
        return "[REDACTED]" if value else value
    return value


def _is_error_event(item: dict[str, Any]) -> bool:
    text = " ".join(
        str(item.get(key) or "")
        for key in ("event", "status", "level", "error", "exception", "traceback")
    )
    return bool(ERROR_TOKEN_RE.search(text))


def _number_from_keys(item: dict[str, Any], keys: tuple[str, ...]) -> int:
    for key in keys:
        value = item.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            if value > 0:
                return int(value)
            continue
        try:
            numeric = float(str(value).strip())
        except (TypeError, ValueError):
            continue
        if numeric > 0:
            return int(numeric)
    return 0


def _duration_value_to_ms(value: Any, *, default_unit: str = "seconds") -> int:
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, int | float):
        numeric = float(value)
        if numeric <= 0:
            return 0
        return int(numeric if default_unit == "milliseconds" else numeric * 1000)
    match = DURATION_RE.match(str(value))
    if match is None:
        return 0
    numeric = float(match.group("value"))
    unit = (match.group("unit") or default_unit).lower()
    if unit.startswith("ms") or unit.startswith("millisecond"):
        return int(numeric)
    if unit in {"m", "min", "mins", "minute", "minutes"}:
        return int(numeric * 60_000)
    return int(numeric * 1000)


def _duration_ms_from_item(item: dict[str, Any]) -> int:
    for key in ("duration_ms", "elapsed_ms", "latency_ms"):
        ms = _duration_value_to_ms(item.get(key), default_unit="milliseconds")
        if ms:
            return ms
    for key in ("duration_seconds", "elapsed_seconds", "latency_seconds"):
        ms = _duration_value_to_ms(item.get(key), default_unit="seconds")
        if ms:
            return ms
    for key in ("duration", "elapsed", "latency"):
        ms = _duration_value_to_ms(item.get(key), default_unit="seconds")
        if ms:
            return ms
    return 0


def _normalize_run_record_event(item: dict[str, Any]) -> dict[str, Any] | None:
    event = str(item.get("event") or "").strip()
    event_normalized = event.lower()
    if event_normalized not in RUN_RECORD_EVENTS:
        return None
    if not (item.get("ticket_id") or item.get("ticket") or item.get("session_id")):
        return None

    record = dict(item)
    if not record.get("status"):
        if event_normalized in FAILED_RECORD_EVENTS:
            record["status"] = "failed"
        elif event_normalized in RUNNING_RECORD_EVENTS:
            record["status"] = "running"
        else:
            record["status"] = "completed"

    input_tokens = _number_from_keys(
        record, ("input_tokens", "prompt_tokens", "tokens_in")
    )
    output_tokens = _number_from_keys(
        record,
        ("output_tokens", "completion_tokens", "tokens_out"),
    )
    total_tokens = _number_from_keys(record, ("total_tokens", "token_count", "tokens"))
    if input_tokens and not record.get("input_tokens"):
        record["input_tokens"] = input_tokens
    if output_tokens and not record.get("output_tokens"):
        record["output_tokens"] = output_tokens
    if not total_tokens and (input_tokens or output_tokens):
        total_tokens = input_tokens + output_tokens
    if total_tokens and not record.get("total_tokens"):
        record["total_tokens"] = total_tokens

    duration_ms = _duration_ms_from_item(record)
    if duration_ms and not record.get("duration_ms"):
        record["duration_ms"] = duration_ms

    if not record.get("trigger"):
        record["trigger"] = (
            record.get("trigger_type")
            or record.get("source")
            or record.get("event")
            or "-"
        )
    return record


def _event_priority(item: dict[str, Any]) -> int:
    return RUN_EVENT_PRIORITY.get(str(item.get("event") or "").lower(), 0)


def _run_group_key(item: dict[str, Any]) -> str:
    session_id = str(item.get("session_id") or "").strip()
    if session_id:
        return f"session:{session_id}"
    ticket_id = str(item.get("ticket_id") or item.get("ticket") or "").strip()
    return f"ticket:{ticket_id}" if ticket_id else ""


def _ticket_group_key(item: dict[str, Any]) -> str:
    ticket_id = str(item.get("ticket_id") or item.get("ticket") or "").strip()
    return f"ticket:{ticket_id}" if ticket_id else ""


def _session_group_key(item: dict[str, Any]) -> str:
    session_id = str(item.get("session_id") or "").strip()
    return f"session:{session_id}" if session_id else ""


def _is_missing(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _merge_numeric_max(
    merged: dict[str, Any], update: dict[str, Any], key: str
) -> None:
    if key not in update or _is_missing(update.get(key)):
        return
    current = _number_from_keys(merged, (key,))
    incoming = _number_from_keys(update, (key,))
    if incoming >= current:
        merged[key] = update[key]


def _merge_latest_time(
    merged: dict[str, Any], update: dict[str, Any], key: str
) -> None:
    if key not in update or _is_missing(update.get(key)):
        return
    current = _parse_datetime(merged.get(key))
    incoming = _parse_datetime(update.get(key))
    if incoming is not None and (current is None or incoming >= current):
        merged[key] = update[key]


def _merge_run_records(
    current: dict[str, Any] | None,
    update: dict[str, Any],
) -> dict[str, Any]:
    if current is None:
        return dict(update)

    merged = dict(current)
    current_priority = _event_priority(current)
    update_priority = _event_priority(update)
    priority_keys = {
        "event",
        "status",
        "level",
        "error",
        "exception",
        "traceback",
        "model",
        "llm_model",
        "provider_model",
    }
    numeric_keys = {
        "rounds",
        "round_count",
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "token_count",
        "tokens",
        "duration_ms",
        "elapsed_ms",
        "latency_ms",
    }
    time_keys = {"timestamp", "ts", "time", "created_at"}

    for key, value in update.items():
        if _is_missing(value):
            continue
        if key in priority_keys:
            if update_priority >= current_priority or _is_missing(merged.get(key)):
                merged[key] = value
            continue
        if key in numeric_keys:
            _merge_numeric_max(merged, update, key)
            continue
        if key in time_keys:
            _merge_latest_time(merged, update, key)
            continue
        if key == "trigger":
            if (
                _is_missing(merged.get(key))
                or str(merged.get(key)).lower() in RUN_RECORD_EVENTS
            ):
                merged[key] = value
            continue
        if key == "_source_file":
            merged[key] = value
            continue
        if _is_missing(merged.get(key)):
            merged[key] = value

    duration_ms = max(_duration_ms_from_item(merged), _duration_ms_from_item(update))
    if duration_ms:
        merged["duration_ms"] = duration_ms
    if not merged.get("total_tokens"):
        input_tokens = _number_from_keys(
            merged, ("input_tokens", "prompt_tokens", "tokens_in")
        )
        output_tokens = _number_from_keys(
            merged,
            ("output_tokens", "completion_tokens", "tokens_out"),
        )
        if input_tokens or output_tokens:
            merged["total_tokens"] = input_tokens + output_tokens
    return merged


def _aggregate_run_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    completed: list[dict[str, Any]] = []
    open_records: dict[str, dict[str, Any]] = {}
    open_by_ticket: dict[str, str] = {}

    for record in records:
        key = _run_group_key(record)
        if not key:
            completed.append(record)
            continue

        ticket_key = _ticket_group_key(record)
        session_key = _session_group_key(record)
        if not session_key and ticket_key and ticket_key in open_by_ticket:
            key = open_by_ticket[ticket_key]

        event = str(record.get("event") or "").lower()
        current = open_records.get(key)
        current_event = str((current or {}).get("event") or "").lower()
        if (
            event in RUNNING_RECORD_EVENTS
            and current is not None
            and current_event not in RUNNING_RECORD_EVENTS
        ):
            next_start = _event_datetime(record)
            if next_start is not None:
                current["_next_run_started_at"] = next_start.isoformat()
            completed.append(current)
            current = None

        if session_key and ticket_key and event in RUNNING_RECORD_EVENTS:
            open_by_ticket[ticket_key] = key

        merged = _merge_run_records(current, record)
        event_time = _event_datetime(record)
        if event in RUNNING_RECORD_EVENTS and event_time is not None:
            current_start = _parse_datetime(merged.get("_run_started_at"))
            if current_start is None or event_time < current_start:
                merged["_run_started_at"] = event_time.isoformat()
        if event in SESSION_END_RECORD_EVENTS:
            if event_time is not None:
                merged["_run_finished_at"] = event_time.isoformat()
            completed.append(merged)
            open_records.pop(key, None)
            if ticket_key and open_by_ticket.get(ticket_key) == key:
                open_by_ticket.pop(ticket_key, None)
        else:
            open_records[key] = merged

    completed.extend(open_records.values())
    return completed


def _run_record_sort_key(item: dict[str, Any]) -> tuple[datetime, int]:
    return (
        _event_datetime(item) or datetime.min.replace(tzinfo=timezone.utc),
        _event_priority(item),
    )


def _run_record_public_key(item: dict[str, Any]) -> str:
    parts = [
        str(item.get("_source_file") or "events"),
        str(item.get("session_id") or ""),
        str(
            item.get("timestamp")
            or item.get("ts")
            or item.get("time")
            or item.get("created_at")
            or ""
        ),
        str(item.get("event") or ""),
        str(item.get("ticket_id") or item.get("ticket") or ""),
    ]
    return "|".join(parts)


def _ticket_id_for_archive(item: dict[str, Any]) -> str:
    raw = str(item.get("ticket_id") or item.get("ticket") or "").strip()
    return raw if TICKET_ID_RE.fullmatch(raw) else ""


def _read_archive_meta(
    archive_dir: Path | None,
    ticket_id: str,
    session_id: str,
    cache: dict[str, dict[str, Any] | None],
) -> dict[str, Any] | None:
    if archive_dir is None or not ticket_id:
        return None
    cache_key = f"{ticket_id}:{session_id}"
    if cache_key in cache:
        return cache[cache_key]
    if session_id and (
        session_id in {".", ".."} or not SESSION_ID_RE.fullmatch(session_id)
    ):
        cache[cache_key] = None
        return None

    ticket_dir = archive_dir / ticket_id
    candidates: list[Path] = []
    if session_id:
        candidates.append(ticket_dir / "runs" / session_id / "meta.json")
    candidates.append(ticket_dir / "meta.json")

    for meta_path in candidates:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(meta, dict):
            continue
        meta_session_id = str(meta.get("session_id") or "").strip()
        if session_id and meta_session_id != session_id:
            continue
        meta["_archive_meta_path"] = str(meta_path)
        cache[cache_key] = meta
        return meta

    cache[cache_key] = None
    return None


def _archive_meta_matches_run_window(
    meta: dict[str, Any],
    record: dict[str, Any],
) -> bool:
    """Prevent one legacy session archive from enriching multiple executions."""
    archived_at = _parse_datetime(meta.get("archived_at") or meta.get("created_at"))
    if archived_at is None:
        return True

    run_start = _parse_datetime(record.get("_run_started_at"))
    run_end = _parse_datetime(record.get("_run_finished_at"))
    next_run_start = _parse_datetime(record.get("_next_run_started_at"))
    if run_start is not None and archived_at < run_start:
        return False
    if run_end is not None and archived_at > run_end:
        return False
    if next_run_start is not None and archived_at >= next_run_start:
        return False
    return True


def _positive_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int | float):
        return int(value) if value > 0 else 0
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _archive_tool_to_ui_tool(call: dict[str, Any]) -> dict[str, Any]:
    success = bool(call.get("success", False))
    return {
        "name": call.get("tool") or call.get("tool_name") or "unknown_tool",
        "status": "success" if success else "failed",
        "error": call.get("error") or "",
        "message": call.get("result_preview") or call.get("error") or "",
        "arguments": call.get("args") if isinstance(call.get("args"), dict) else {},
    }


def _archive_rounds(
    meta: dict[str, Any], record: dict[str, Any]
) -> list[dict[str, Any]]:
    raw_tool_calls = meta.get("tool_calls")
    if not isinstance(raw_tool_calls, list):
        return []

    grouped: dict[int, dict[str, Any]] = {}
    for raw_call in raw_tool_calls:
        if not isinstance(raw_call, dict):
            continue
        round_number = _positive_int(raw_call.get("round")) or 1
        round_item = grouped.setdefault(
            round_number,
            {
                "round": round_number,
                "model": record.get("model") or record.get("llm_model") or "-",
                "type": "tool_calls",
                "tools": [],
            },
        )
        round_item["tools"].append(_archive_tool_to_ui_tool(raw_call))

    return [grouped[key] for key in sorted(grouped)]


def _round_number_from_item(item: dict[str, Any]) -> int:
    return (
        _positive_int(item.get("round"))
        or _positive_int(item.get("round_number"))
        or _positive_int(item.get("iteration"))
    )


def _trace_tool_to_ui_tool(event: dict[str, Any]) -> dict[str, Any]:
    success_raw = event.get("success")
    success_text = str(success_raw).strip().lower()
    failed = (
        success_raw is False
        or success_text in {"false", "0", "failed", "failure", "error"}
        or _is_error_event(event)
    )
    return {
        "name": event.get("tool") or event.get("tool_name") or "unknown_tool",
        "status": "failed" if failed else "success",
        "error": event.get("error") or event.get("exception") or "",
        "message": (
            event.get("result_preview")
            or event.get("error")
            or event.get("status")
            or event.get("module")
            or ""
        ),
        "duration_ms": _duration_ms_from_item(event),
        "arguments": event.get("arguments")
        if isinstance(event.get("arguments"), dict)
        else {},
    }


def _trace_rounds(
    events: list[dict[str, Any]], record: dict[str, Any]
) -> list[dict[str, Any]]:
    grouped: dict[int, dict[str, Any]] = {}
    for event in events:
        round_number = _round_number_from_item(event)
        if not round_number:
            continue
        event_name = str(event.get("event") or "").lower()
        is_tool_event = event_name == "tool_call" or bool(
            event.get("tool") or event.get("tool_name")
        )
        is_round_event = (
            event_name in {"llm_round", "round_tool_calls"} or is_tool_event
        )
        if not is_round_event:
            continue

        round_item = grouped.setdefault(
            round_number,
            {
                "round": round_number,
                "model": record.get("model")
                or record.get("llm_model")
                or event.get("model")
                or "-",
                "type": "tool_calls",
                "tools": [],
            },
        )
        if event.get("model") and _is_missing(round_item.get("model")):
            round_item["model"] = event["model"]
        input_tokens = _number_from_keys(
            event, ("input_tokens", "prompt_tokens", "tokens_in")
        )
        output_tokens = _number_from_keys(
            event,
            ("output_tokens", "completion_tokens", "tokens_out"),
        )
        total_tokens = _number_from_keys(
            event, ("total_tokens", "token_count", "tokens")
        )
        duration_ms = _duration_ms_from_item(event) or _duration_value_to_ms(
            event.get("llm_elapsed_seconds"), default_unit="seconds"
        )
        if input_tokens:
            _merge_numeric_max(
                round_item, {"input_tokens": input_tokens}, "input_tokens"
            )
        if output_tokens:
            _merge_numeric_max(
                round_item, {"output_tokens": output_tokens}, "output_tokens"
            )
        if total_tokens:
            _merge_numeric_max(
                round_item, {"total_tokens": total_tokens}, "total_tokens"
            )
        if duration_ms:
            _merge_numeric_max(round_item, {"duration_ms": duration_ms}, "duration_ms")
        if event.get("stop_reason"):
            round_item["type"] = event["stop_reason"]
        if event.get("tool_call_count"):
            _merge_numeric_max(
                round_item,
                {"tool_call_count": event["tool_call_count"]},
                "tool_call_count",
            )
        if is_tool_event:
            round_item["tools"].append(_trace_tool_to_ui_tool(event))

    return [grouped[key] for key in sorted(grouped)]


def _enrich_with_archive_meta(
    record: dict[str, Any],
    *,
    archive_dir: Path | None,
    cache: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    ticket_id = _ticket_id_for_archive(record)
    session_id = str(record.get("session_id") or "").strip()
    meta = _read_archive_meta(archive_dir, ticket_id, session_id, cache)
    if meta is None or not _archive_meta_matches_run_window(meta, record):
        return record

    enriched = dict(record)
    enriched["archive_available"] = True
    enriched["archive_path"] = meta.get("_archive_meta_path")

    for key in (
        "session_id",
        "reply_sent",
        "failed_tool_count",
        "tool_call_count",
        "failed_tools",
        "customer_reply_available",
        "casework_phase",
        "casework",
    ):
        if (
            _is_missing(enriched.get(key))
            and key in meta
            and not _is_missing(meta.get(key))
        ):
            enriched[key] = meta[key]

    for key in ("rounds", "round_count", "total_tokens"):
        if _is_missing(enriched.get(key)) and not _is_missing(meta.get(key)):
            enriched[key] = meta[key]

    token_usage = meta.get("token_usage")
    if isinstance(token_usage, dict):
        if _is_missing(enriched.get("input_tokens")) and token_usage.get("prompt"):
            enriched["input_tokens"] = token_usage["prompt"]
        if _is_missing(enriched.get("output_tokens")) and token_usage.get("completion"):
            enriched["output_tokens"] = token_usage["completion"]
        if _is_missing(enriched.get("total_tokens")) and token_usage.get("total"):
            enriched["total_tokens"] = token_usage["total"]

    if _is_missing(enriched.get("duration_ms")) and meta.get("elapsed_seconds"):
        enriched["duration_ms"] = _duration_value_to_ms(
            meta.get("elapsed_seconds"),
            default_unit="seconds",
        )

    raw_tool_calls = meta.get("tool_calls")
    if isinstance(raw_tool_calls, list) and _is_missing(enriched.get("tool_calls")):
        enriched["tool_calls"] = [
            _archive_tool_to_ui_tool(call)
            for call in raw_tool_calls
            if isinstance(call, dict)
        ]

    rounds = _archive_rounds(meta, enriched)
    if rounds and _is_missing(enriched.get("llm_rounds")):
        enriched["llm_rounds"] = rounds
    return enriched


def _record_has_error(item: dict[str, Any]) -> bool:
    if _is_error_event(item):
        return True
    events = item.get("events")
    if not isinstance(events, list):
        return False
    return any(_is_error_event(event) for event in events if isinstance(event, dict))


def _trace_index_keys(item: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    session_key = _session_group_key(item)
    ticket_key = _ticket_group_key(item)
    if session_key:
        keys.add(session_key)
    if ticket_key:
        keys.add(ticket_key)
    return keys


def _build_trace_index(
    trace_events: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for event in trace_events:
        for key in _trace_index_keys(event):
            index.setdefault(key, []).append(event)
    for events in index.values():
        events.sort(key=_trace_sort_key)
    return index


def _trace_events_for_record(
    record: dict[str, Any],
    trace_index: dict[str, list[dict[str, Any]]] | None,
) -> list[dict[str, Any]]:
    if trace_index is None:
        events = record.get("events")
        return (
            [event for event in events if isinstance(event, dict)]
            if isinstance(events, list)
            else []
        )

    matched: list[dict[str, Any]] = []
    seen: set[int] = set()
    keys = _trace_index_keys(record)
    for key in keys:
        for event in trace_index.get(key, []):
            event_id = id(event)
            if event_id in seen:
                continue
            if not _trace_matches(record, event):
                continue
            seen.add(event_id)
            matched.append(event)
    if len(keys) > 1:
        matched.sort(key=_trace_sort_key)
    return matched


def _record_has_error_with_trace(
    item: dict[str, Any],
    trace_index: dict[str, list[dict[str, Any]]] | None,
) -> bool:
    if _is_error_event(item):
        return True
    return any(
        _is_error_event(event) for event in _trace_events_for_record(item, trace_index)
    )


def _trace_matches(record: dict[str, Any], event: dict[str, Any]) -> bool:
    record_session = str(record.get("session_id") or "").strip()
    event_session = str(event.get("session_id") or "").strip()
    if record_session and event_session and record_session != event_session:
        return False

    record_ticket = str(record.get("ticket_id") or record.get("ticket") or "").strip()
    event_ticket = str(event.get("ticket_id") or event.get("ticket") or "").strip()
    if record_ticket and event_ticket and record_ticket != event_ticket:
        return False
    if not (record_session and event_session) and (
        not record_ticket or record_ticket != event_ticket
    ):
        return False

    run_start = _parse_datetime(record.get("_run_started_at"))
    run_end = _parse_datetime(record.get("_run_finished_at"))
    next_run_start = _parse_datetime(record.get("_next_run_started_at"))
    if run_start is not None or run_end is not None or next_run_start is not None:
        event_time = _event_datetime(event)
        if event_time is None:
            return False
        if run_start is not None and event_time < run_start:
            return False
        if run_end is not None and event_time > run_end:
            return False
        if next_run_start is not None and event_time >= next_run_start:
            return False
    return True


def _trace_sort_key(item: dict[str, Any]) -> tuple[datetime, int]:
    return (
        _event_datetime(item) or datetime.min.replace(tzinfo=timezone.utc),
        _event_priority(item),
    )


def _attach_event_trace(
    record: dict[str, Any],
    trace_index: dict[str, list[dict[str, Any]]] | None,
) -> dict[str, Any]:
    events = _trace_events_for_record(record, trace_index)
    if not events:
        return record
    enriched = dict(record)
    enriched["events"] = events
    enriched["event_count"] = len(events)
    if _is_missing(enriched.get("llm_rounds")):
        rounds = _trace_rounds(events, enriched)
        if rounds:
            enriched["llm_rounds"] = rounds
            round_count = max(
                _positive_int(round_item.get("round")) for round_item in rounds
            )
            if _is_missing(enriched.get("rounds")):
                enriched["rounds"] = round_count
            if _is_missing(enriched.get("round_count")):
                enriched["round_count"] = round_count
    return enriched


def _response_record(
    record: dict[str, Any],
    *,
    include_detail: bool,
    trace_index: dict[str, list[dict[str, Any]]] | None,
    archive_dir: Path | None,
    cache: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    record_key = _run_record_public_key(record)
    if include_detail:
        enriched = _enrich_with_archive_meta(
            _attach_event_trace(record, trace_index),
            archive_dir=archive_dir,
            cache=cache,
        )
        enriched["detail_loaded"] = True
    else:
        enriched = dict(record)
        if trace_index is not None:
            events = _trace_events_for_record(record, trace_index)
            if events:
                enriched["event_count"] = len(events)
        enriched.pop("events", None)
        enriched.pop("llm_rounds", None)
        enriched.pop("tool_calls", None)
        enriched["detail_loaded"] = False
    enriched.pop("_run_started_at", None)
    enriched.pop("_run_finished_at", None)
    enriched.pop("_next_run_started_at", None)
    enriched["record_key"] = record_key
    return enriched


def _iter_jsonl_lines(path: Path, *, reverse: bool = False):
    if not reverse:
        with path.open(encoding="utf-8") as lines:
            yield from lines
        return

    chunk_size = 64 * 1024
    with path.open("rb") as handle:
        position = handle.seek(0, os.SEEK_END)
        buffer = b""
        while position > 0:
            read_size = min(chunk_size, position)
            position -= read_size
            handle.seek(position)
            chunk = handle.read(read_size)
            parts = (chunk + buffer).split(b"\n")
            buffer = parts[0]
            for line in reversed(parts[1:]):
                if line:
                    yield line.decode("utf-8", errors="replace")
        if buffer:
            yield buffer.decode("utf-8", errors="replace")


def _query_limit(request: web.Request, *, default: int = 100) -> int:
    try:
        value = int(request.query.get("limit", str(default)))  # type: ignore[attr-defined]
    except Exception:
        value = default
    return max(1, min(value, 500))


def _run_filters_from_query(query: Any) -> dict[str, Any]:
    return {
        "record_key": str(query.get("record_key", "") or "").strip(),
        "ticket_id": str(query.get("ticket_id", "") or "").strip(),
        "event": str(query.get("event", "") or "").strip().lower(),
        "module": str(query.get("module", "") or "").strip().lower(),
        "level": str(query.get("level", "") or "").strip().lower(),
        "errors_only": _truthy(query.get("errors_only")),
        "quick": _truthy(query.get("quick")),
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


def _log_dir_signature(log_dir: Path) -> str:
    if not log_dir.exists():
        return "missing"
    parts: list[str] = []
    for path in sorted(log_dir.glob("events-*.jsonl"), reverse=True)[:4]:
        try:
            stat = path.stat()
        except OSError:
            continue
        parts.append(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}")
    return "|".join(parts) or "empty"


def _cacheable_run_records_request(
    filters: dict[str, Any], include_detail: bool
) -> bool:
    return (
        bool(filters.get("quick"))
        and not include_detail
        and not any(
            filters.get(key)
            for key in (
                "record_key",
                "ticket_id",
                "event",
                "module",
                "level",
                "errors_only",
                "since",
                "until",
            )
        )
    )


def _run_records_cache_key(
    *,
    log_dir: Path,
    archive_dir: Path | None,
    limit: int,
    filters: dict[str, Any],
    include_detail: bool,
) -> str:
    return json.dumps(
        {
            "log_dir": str(log_dir),
            "archive_dir": str(archive_dir or ""),
            "signature": _log_dir_signature(log_dir),
            "limit": limit,
            "filters": _public_run_filters(filters),
            "detail": include_detail,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _prune_run_records_cache() -> None:
    if len(_RUN_RECORD_CACHE) <= RUN_RECORD_CACHE_MAX_ITEMS:
        return
    for key, _value in sorted(_RUN_RECORD_CACHE.items(), key=lambda item: item[1][0])[
        : len(_RUN_RECORD_CACHE) - RUN_RECORD_CACHE_MAX_ITEMS
    ]:
        _RUN_RECORD_CACHE.pop(key, None)


def _read_recent_log_events_cached(
    log_dir: Path,
    *,
    limit: int,
    filters: dict[str, Any],
    archive_dir: Path | None,
    include_detail: bool,
) -> dict[str, Any]:
    if not _cacheable_run_records_request(filters, include_detail):
        return _read_recent_log_events(
            log_dir,
            limit=limit,
            filters=filters,
            archive_dir=archive_dir,
            include_detail=include_detail,
        )

    key = _run_records_cache_key(
        log_dir=log_dir,
        archive_dir=archive_dir,
        limit=limit,
        filters=filters,
        include_detail=include_detail,
    )
    now = time.monotonic()
    cached = _RUN_RECORD_CACHE.get(key)
    if cached is not None and now - cached[0] <= RUN_RECORD_CACHE_TTL_SECONDS:
        payload = deepcopy(cached[1])
        payload["cache_hit"] = True
        return payload

    payload = _read_recent_log_events(
        log_dir,
        limit=limit,
        filters=filters,
        archive_dir=archive_dir,
        include_detail=include_detail,
    )
    payload["cache_hit"] = False
    _RUN_RECORD_CACHE[key] = (now, deepcopy(payload))
    _prune_run_records_cache()
    return deepcopy(payload)


def _matches_run_filters(
    item: dict[str, Any],
    filters: dict[str, Any],
    *,
    trace_index: dict[str, list[dict[str, Any]]] | None = None,
) -> bool:
    record_key = filters.get("record_key")
    if record_key and record_key != _run_record_public_key(item):
        return False
    ticket_id = filters.get("ticket_id")
    if ticket_id and ticket_id not in {
        str(item.get("ticket_id") or ""),
        str(item.get("ticket") or ""),
    }:
        return False
    event = filters.get("event")
    module = filters.get("module")
    level = filters.get("level")
    trace_events = (
        _trace_events_for_record(item, trace_index) if event or module or level else []
    )
    if event and not any(
        event in str(value).lower()
        for value in [
            item.get("event"),
            *(trace.get("event") for trace in trace_events),
        ]
    ):
        return False
    if module and not any(
        module in str(value).lower()
        for value in [
            item.get("module"),
            *(trace.get("module") for trace in trace_events),
        ]
    ):
        return False
    if level and not any(
        level in str(value).lower()
        for value in [
            item.get("level"),
            *(trace.get("level") for trace in trace_events),
        ]
    ):
        return False
    if filters.get("errors_only") and not _record_has_error_with_trace(
        item, trace_index
    ):
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
    error_events: int | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    today_items = [
        item
        for item in all_items
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
        "error_events": error_events
        if error_events is not None
        else sum(1 for item in filtered if _record_has_error(item)),
        "latest_timestamp": latest_timestamp,
    }


def _read_recent_log_events(
    log_dir: Path,
    *,
    limit: int,
    filters: dict[str, Any] | None = None,
    archive_dir: Path | None = None,
    include_detail: bool = True,
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

    run_events: list[dict[str, Any]] = []
    trace_events: list[dict[str, Any]] = []
    raw_events_total = 0
    quick = bool(filters.get("quick")) and not any(
        filters.get(key)
        for key in (
            "record_key",
            "ticket_id",
            "event",
            "module",
            "level",
            "errors_only",
            "since",
            "until",
        )
    )
    fast_list = quick and not include_detail
    paths = sorted(log_dir.glob("events-*.jsonl"), reverse=fast_list)
    scanned_files = 0
    enough_fast_records = False
    scan_limit_hit = False
    for path in paths:
        scanned_files += 1
        try:
            lines = _iter_jsonl_lines(path, reverse=fast_list)
            for line in lines:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict):
                    continue
                raw_events_total += 1
                item["_source_file"] = path.name
                if _run_group_key(item):
                    trace_events.append(dict(item))
                record = _normalize_run_record_event(item)
                if record is None:
                    if fast_list and raw_events_total >= RUN_RECORD_FAST_MAX_SCAN_LINES:
                        scan_limit_hit = True
                        break
                    continue
                run_events.append(record)
                if (
                    fast_list
                    and len(run_events) >= limit
                    and (len(run_events) == limit or len(run_events) % 20 == 0)
                ):
                    if (
                        len(
                            _aggregate_run_records(
                                sorted(run_events, key=_run_record_sort_key)
                            ),
                        )
                        >= limit
                    ):
                        enough_fast_records = True
                        break
                if fast_list and raw_events_total >= RUN_RECORD_FAST_MAX_SCAN_LINES:
                    scan_limit_hit = True
                    break
        except OSError:
            continue
        if enough_fast_records or scan_limit_hit:
            break
    needs_trace_index = include_detail or any(
        filters.get(key) for key in ("event", "module", "level", "errors_only")
    )
    trace_index = _build_trace_index(trace_events) if needs_trace_index else None
    all_items = sorted(
        _aggregate_run_records(sorted(run_events, key=_run_record_sort_key)),
        key=_run_record_sort_key,
        reverse=True,
    )
    matched_items = [
        item
        for item in all_items
        if _matches_run_filters(item, filters, trace_index=trace_index)
    ]
    matched_error_events = sum(
        1
        for item in matched_items
        if (
            _record_has_error_with_trace(item, trace_index)
            if trace_index is not None
            else _record_has_error(item)
        )
    )
    archive_cache: dict[str, dict[str, Any] | None] = {}
    page_items = [
        _response_record(
            item,
            include_detail=include_detail,
            trace_index=trace_index,
            archive_dir=archive_dir,
            cache=archive_cache,
        )
        for item in matched_items[:limit]
    ]
    items = [_redact_value(item) for item in page_items]
    has_more = len(matched_items) > limit or (
        fast_list and (scanned_files < len(paths) or scan_limit_hit)
    )
    return {
        "items": items,
        "summary": _run_summary(
            total=len(all_items),
            filtered=matched_items,
            all_items=all_items,
            error_events=matched_error_events,
        ),
        "filters": _public_run_filters(filters),
        "log_dir": str(log_dir),
        "raw_events_total": raw_events_total,
        "partial": fast_list and (scanned_files < len(paths) or scan_limit_hit),
        "scanned_files": scanned_files,
        "scan_limit_hit": scan_limit_hit,
        "returned": len(items),
        "has_more": has_more,
        "detail": include_detail,
        "exists": True,
    }


def _mcp_endpoint(url: str) -> str:
    base = (url or "").strip().rstrip("/")
    if not base:
        return ""
    return base if base.endswith("/mcp") else f"{base}/mcp"


def _model_endpoint(base_url: str, provider: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        base = (
            "https://api.anthropic.com/v1"
            if provider == "anthropic"
            else "https://api.openai.com/v1"
        )
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("model_base_url_must_use_http_or_https")
    if parsed.username or parsed.password:
        raise ValueError("model_base_url_must_not_include_credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("model_base_url_must_not_include_query_or_fragment")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and (
        address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    ):
        raise ValueError("model_base_url_address_is_not_allowed")
    if base.endswith("/models"):
        return base
    if base.endswith("/v1"):
        return f"{base}/models"
    return f"{base}/v1/models"


def _admin_session_payload(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        payload = asdict(value)
    elif isinstance(value, dict):
        payload = dict(value)
    else:
        payload = {
            key: getattr(value, key)
            for key in (
                "ticket_id",
                "session_id",
                "status",
                "round_count",
                "total_tokens",
                "elapsed",
                "hint",
                "dry_run",
            )
            if hasattr(value, key)
        }
    status = payload.get("status")
    if hasattr(status, "value"):
        payload["status"] = status.value
    payload.pop("start_time", None)
    return _redact_value(payload)


async def _probe_integration(
    *,
    name: str,
    cfg: AppConfig,
) -> dict[str, Any]:
    spec = _integration_spec(name, cfg)
    checked_at = datetime.now(timezone.utc).isoformat()
    if spec is None:
        raise ValueError("unsupported_integration")

    enabled = bool(spec.get("enabled", True))
    url = str(spec["url"]).strip().rstrip("/")
    api_key = str(spec.get("api_key") or "").strip()
    timeout_seconds = float(spec.get("timeout_seconds") or 10)
    configured = bool(enabled and url)
    base = {
        "ok": True,
        "integration": name,
        "label": spec["label"],
        "configured": configured,
        "reachable": False,
        "status": "missing_config" if not configured else "unknown",
        "detail": "Service disabled or URL is not configured" if not configured else "",
        "checked_at": checked_at,
    }
    if not configured:
        return base

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
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
    effective = admin_config(request.app).effective_config(app_config(request.app))[
        "values"
    ]
    if not _truthy(effective.get("admin_auth.password_login_enabled", True)):
        return _json_error("password_login_disabled", status=403)
    try:
        payload = await request.json()
    except Exception:
        return _json_error("bad_request", status=400)
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    result = admin_auth(request.app).verify_login(
        username=username,
        password=password,
        remote_ip=_login_rate_limit_ip(request),
    )
    if result.rate_limited:
        response = _json_error("too_many_attempts", status=429)
        response.headers["Retry-After"] = str(result.retry_after_seconds)
        return response
    if not result.ok:
        return _json_error("invalid_credentials", status=401)
    token = admin_auth(request.app).create_session(username)
    response = web.json_response(
        {
            "ok": True,
            "username": username,
            "force_password_change": result.force_password_change,
        }
    )
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
    return web.json_response(
        {
            "ok": True,
            "username": username,
            "force_password_change": admin_auth(request.app).requires_password_change(
                username
            ),
        }
    )


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
    token = admin_auth(request.app).create_session(username or "")
    response = web.json_response({"ok": True})
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="Lax",
        secure=_request_is_secure(request),
        path="/",
        max_age=24 * 60 * 60,
    )
    return response


async def handle_admin_config_schema(request: web.Request) -> web.Response:
    _username, unauthorized = _require_admin(request)
    if unauthorized is not None:
        return unauthorized
    return web.json_response(admin_config(request.app).schema())


async def handle_admin_config_effective(request: web.Request) -> web.Response:
    _username, unauthorized = _require_admin(request)
    if unauthorized is not None:
        return unauthorized
    return web.json_response(
        admin_config(request.app).effective_config(app_config(request.app))
    )


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
    payload = _read_recent_log_events_cached(
        Path(cfg.logging.log_dir),
        limit=_query_limit(request),
        filters=filters,
        archive_dir=Path(cfg.archive.archive_dir),
        include_detail=_truthy(request.query.get("detail", "true")),  # type: ignore[attr-defined]
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
        diff=[
            {
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
            }
        ],
    )
    return web.json_response(result)


async def handle_admin_operations_status(request: web.Request) -> web.Response:
    """Expose the live work surface needed by the production command center."""
    _username, unauthorized = _require_admin(request)
    if unauthorized is not None:
        return unauthorized

    queue = trigger_queue(request.app)
    manager = session_manager(request.app)
    try:
        overview = manager.get_all_sessions()
    except (AttributeError, TypeError):
        overview = {}
    if not isinstance(overview, dict):
        overview = {}
    active_sessions = [
        _admin_session_payload(item)
        for item in overview.get("active_sessions", [])
    ]
    recent_completed = [
        _admin_session_payload(item)
        for item in overview.get("recent_completed", [])
    ]

    from zd_agent.services.saas_routes import _outbox_health, _task_status

    background_tasks = {
        BACKGROUND_TASK_NAMES[key]: _task_status(request.app.get(key))
        for key in BACKGROUND_TASK_KEYS
    }
    queue_path = getattr(queue, "path", None)
    state_dir = (
        Path(queue_path).parent
        if isinstance(queue_path, (str, os.PathLike))
        else Path(app_config(request.app).resources.base_dir)
        / app_config(request.app).resources.state_dir
    )
    return web.json_response(
        {
            "ok": True,
            "ready": all(value == "running" for value in background_tasks.values()),
            "active": int(overview.get("active", len(active_sessions)) or 0),
            "max_concurrent": int(
                overview.get(
                    "max_concurrent",
                    app_config(request.app).agent.max_concurrent_sessions,
                )
                or 0
            ),
            "active_sessions": active_sessions,
            "recent_completed": recent_completed[:8],
            "queue": {
                "pending": await queue.pending_count(),
                "dead_letter": await queue.dead_letter_count(),
            },
            "outbox": _outbox_health(state_dir),
            "background_tasks": background_tasks,
        }
    )


async def handle_admin_model_discovery(request: web.Request) -> web.Response:
    """Discover model IDs through a server-side provider adapter."""
    username, unauthorized = _require_admin(request)
    if unauthorized is not None:
        return unauthorized
    try:
        payload = await request.json()
    except Exception:
        return _json_error("bad_request", status=400)

    provider = str(payload.get("provider") or "openai").strip().lower()
    if provider not in {"openai", "anthropic"}:
        return _json_error("unsupported_model_provider", status=400)
    base_url = str(payload.get("base_url") or "").strip()
    try:
        endpoint = _model_endpoint(base_url, provider)
    except ValueError as exc:
        return _json_error(str(exc), status=400)

    env = EnvSettings()
    api_key = str(payload.get("api_key") or "").strip()
    if not api_key:
        api_key = (
            env.anthropic_api_key if provider == "anthropic" else env.openai_api_key
        )
    if not api_key:
        return _json_error("model_api_key_is_required", status=400)

    headers = (
        {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        if provider == "anthropic"
        else {"Authorization": f"Bearer {api_key}"}
    )
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            response = await client.get(endpoint, headers=headers)
    except httpx.HTTPError as exc:
        return _json_error(f"model_discovery_unreachable:{exc.__class__.__name__}", status=502)
    if response.status_code < 200 or response.status_code >= 300:
        return _json_error(
            f"model_discovery_http_{response.status_code}",
            status=502,
        )
    try:
        body = response.json()
    except ValueError:
        return _json_error("model_discovery_invalid_json", status=502)
    raw_models = body.get("data", []) if isinstance(body, dict) else []
    models: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        models.append(
            {
                "id": model_id,
                "owned_by": str(item.get("owned_by") or item.get("display_name") or ""),
            }
        )
        if len(models) >= 500:
            break
    models.sort(key=lambda item: item["id"].casefold())
    admin_config(request.app).audit_event(
        actor=username or "",
        ip=_remote_ip(request),
        action="model_discovery",
        reason=provider,
        diff=[
            {
                "path": "llm.base_url",
                "label": "模型发现",
                "group": "模型与运行",
                "old": "",
                "new": {"provider": provider, "model_count": len(models)},
                "secret": False,
                "restart_required": False,
            }
        ],
    )
    return web.json_response(
        {
            "ok": True,
            "provider": provider,
            "endpoint": endpoint,
            "models": models,
            "count": len(models),
            "latency_ms": round((time.monotonic() - started) * 1000),
        }
    )


async def handle_admin_deploy_status(request: web.Request) -> web.Response:
    _username, unauthorized = _require_admin(request)
    if unauthorized is not None:
        return unauthorized
    cfg = app_config(request.app)
    effective = admin_config(request.app).effective_config(cfg)["values"]
    google_secret = effective.get("admin_auth.google_client_secret") or {}
    feishu_secret = effective.get("admin_auth.feishu_app_secret") or {}
    return web.json_response(
        {
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
                    "enabled": bool(
                        effective.get("admin_auth.password_login_enabled", True)
                    ),
                    "default_username": "admin",
                },
                "google_sso": {
                    "enabled": bool(
                        effective.get("admin_auth.google_sso_enabled", False)
                    ),
                    "configured": bool(effective.get("admin_auth.google_client_id"))
                    and bool(google_secret.get("configured")),
                },
                "feishu_sso": {
                    "enabled": bool(
                        effective.get("admin_auth.feishu_sso_enabled", False)
                    ),
                    "configured": bool(effective.get("admin_auth.feishu_app_id"))
                    and bool(feishu_secret.get("configured")),
                },
            },
            "storage": {
                "base_dir": cfg.resources.base_dir,
                "state_dir": cfg.resources.state_dir,
                "log_dir": cfg.logging.log_dir,
            },
        }
    )


def register_admin_routes(
    *,
    app: web.Application,
    cfg: AppConfig,
    state_dir: Path,
    initial_admin_password: str | None = None,
) -> None:
    """Register admin UI and JSON API routes."""
    state_dir.mkdir(parents=True, exist_ok=True)
    env_password = EnvSettings().zd_admin_initial_password.strip()
    auth_store = AdminAuthStore(
        state_dir / "admin_auth.json",
        initial_password=(
            initial_admin_password
            if initial_admin_password is not None
            else env_password or None
        ),
    )
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
        "operations_status": handle_admin_operations_status,
        "model_discovery": handle_admin_model_discovery,
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
    app.router.add_get("/admin/api/operations/status", handle_admin_operations_status)
    app.router.add_post("/admin/api/models/discover", handle_admin_model_discovery)
    app.router.add_get("/admin/api/deploy/status", handle_admin_deploy_status)

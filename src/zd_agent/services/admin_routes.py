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

    input_tokens = _number_from_keys(record, ("input_tokens", "prompt_tokens", "tokens_in"))
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
    return str(item.get("ticket_id") or item.get("ticket") or item.get("session_id") or "")


def _is_missing(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _merge_numeric_max(merged: dict[str, Any], update: dict[str, Any], key: str) -> None:
    if key not in update or _is_missing(update.get(key)):
        return
    current = _number_from_keys(merged, (key,))
    incoming = _number_from_keys(update, (key,))
    if incoming >= current:
        merged[key] = update[key]


def _merge_latest_time(merged: dict[str, Any], update: dict[str, Any], key: str) -> None:
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
            if _is_missing(merged.get(key)) or str(merged.get(key)).lower() in RUN_RECORD_EVENTS:
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
        input_tokens = _number_from_keys(merged, ("input_tokens", "prompt_tokens", "tokens_in"))
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

    for record in records:
        key = _run_group_key(record)
        if not key:
            completed.append(record)
            continue

        event = str(record.get("event") or "").lower()
        current = open_records.get(key)
        current_event = str((current or {}).get("event") or "").lower()
        if (
            event in RUNNING_RECORD_EVENTS
            and current is not None
            and current_event not in RUNNING_RECORD_EVENTS
        ):
            completed.append(current)
            current = None

        merged = _merge_run_records(current, record)
        if event in SESSION_END_RECORD_EVENTS:
            completed.append(merged)
            open_records.pop(key, None)
        else:
            open_records[key] = merged

    completed.extend(open_records.values())
    return completed


def _run_record_sort_key(item: dict[str, Any]) -> tuple[datetime, int]:
    return (_event_datetime(item) or datetime.min.replace(tzinfo=timezone.utc), _event_priority(item))


def _ticket_id_for_archive(item: dict[str, Any]) -> str:
    raw = str(item.get("ticket_id") or item.get("ticket") or "").strip()
    return raw if TICKET_ID_RE.fullmatch(raw) else ""


def _read_archive_meta(
    archive_dir: Path | None,
    ticket_id: str,
    cache: dict[str, dict[str, Any] | None],
) -> dict[str, Any] | None:
    if archive_dir is None or not ticket_id:
        return None
    if ticket_id in cache:
        return cache[ticket_id]

    meta_path = archive_dir / ticket_id / "meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cache[ticket_id] = None
        return None
    if not isinstance(meta, dict):
        cache[ticket_id] = None
        return None
    meta["_archive_meta_path"] = str(meta_path)
    cache[ticket_id] = meta
    return meta


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


def _archive_rounds(meta: dict[str, Any], record: dict[str, Any]) -> list[dict[str, Any]]:
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


def _enrich_with_archive_meta(
    record: dict[str, Any],
    *,
    archive_dir: Path | None,
    cache: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    ticket_id = _ticket_id_for_archive(record)
    meta = _read_archive_meta(archive_dir, ticket_id, cache)
    if meta is None:
        return record

    enriched = dict(record)
    enriched["archive_available"] = True
    enriched["archive_path"] = meta.get("_archive_meta_path")

    for key in (
        "session_id",
        "ticket_type",
        "fix_status",
        "reply_sent",
        "failed_tool_count",
        "tool_call_count",
        "failed_tools",
        "customer_reply_available",
        "reviewer_checklist_available",
    ):
        if key in meta and not _is_missing(meta.get(key)):
            enriched[key] = meta[key]

    for key in ("rounds", "round_count", "total_tokens"):
        _merge_numeric_max(enriched, meta, key)

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
    if isinstance(raw_tool_calls, list):
        enriched["tool_calls"] = [
            _archive_tool_to_ui_tool(call)
            for call in raw_tool_calls
            if isinstance(call, dict)
        ]

    rounds = _archive_rounds(meta, enriched)
    if rounds:
        enriched["llm_rounds"] = rounds
    return enriched


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
    archive_dir: Path | None = None,
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
    raw_events_total = 0
    paths = sorted(log_dir.glob("events-*.jsonl"))
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            raw_events_total += 1
            item["_source_file"] = path.name
            record = _normalize_run_record_event(item)
            if record is None:
                continue
            run_events.append(record)
    archive_cache: dict[str, dict[str, Any] | None] = {}
    enriched_items = [
        _enrich_with_archive_meta(item, archive_dir=archive_dir, cache=archive_cache)
        for item in _aggregate_run_records(run_events)
    ]
    all_items = sorted(
        enriched_items,
        key=_run_record_sort_key,
        reverse=True,
    )
    matched_items = [item for item in all_items if _matches_run_filters(item, filters)]
    items = [_redact_value(item) for item in matched_items[:limit]]
    return {
        "items": items,
        "summary": _run_summary(
            total=len(all_items),
            filtered=matched_items,
            all_items=all_items,
        ),
        "filters": _public_run_filters(filters),
        "log_dir": str(log_dir),
        "raw_events_total": raw_events_total,
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
        archive_dir=Path(cfg.knowledge.archive_dir),
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

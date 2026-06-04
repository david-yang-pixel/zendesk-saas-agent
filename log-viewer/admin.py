"""Admin backend for the zd-agent management UI.

The public surface deliberately returns only masked secret state. Full API keys
are accepted only in write requests and are never emitted back to callers.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from fastapi import HTTPException, Request
from ruamel.yaml import YAML

MODEL_RE = re.compile(r"^[A-Za-z0-9._:/+-]{1,120}$")
PROVIDER_VALUES = {"openai", "anthropic"}


@dataclass(frozen=True)
class AdminPaths:
    config_path: Path
    env_path: Path
    control_path: Path
    audit_path: Path
    admin_state_path: Path
    service_name: str
    log_dir: Path


def get_paths() -> AdminPaths:
    control_default = "/opt/zd-agent/resources/.state/control.json"
    audit_default = "/opt/zd-agent/logs/admin-audit.jsonl"
    control_path = Path(os.environ.get("ZD_AGENT_CONTROL_PATH", control_default))
    admin_state_default = str(control_path.with_name("admin-state.json"))
    return AdminPaths(
        config_path=Path(os.environ.get("ZD_AGENT_CONFIG_PATH", "/opt/zd-agent/code/config.yaml")),
        env_path=Path(os.environ.get("ZD_AGENT_ENV_PATH", "/opt/zd-agent/.env")),
        control_path=control_path,
        audit_path=Path(os.environ.get("ZD_AGENT_AUDIT_PATH", audit_default)),
        admin_state_path=Path(os.environ.get("ZD_AGENT_ADMIN_STATE_PATH", admin_state_default)),
        service_name=os.environ.get("ZD_AGENT_SERVICE", "zd-agent"),
        log_dir=Path(os.environ.get("ZD_LOG_DIR", "/opt/zd-agent/logs")),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, text: str, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as fh:
        fh.write(text)
        tmp = Path(fh.name)
    if mode is not None:
        tmp.chmod(mode)
    elif path.exists():
        tmp.chmod(path.stat().st_mode & 0o777)
    tmp.replace(path)


def _load_yaml(path: Path) -> tuple[YAML, Any]:
    yaml = YAML()
    yaml.preserve_quotes = True
    if not path.exists():
        return yaml, {}
    with path.open(encoding="utf-8") as fh:
        return yaml, (yaml.load(fh) or {})


def _dump_yaml(path: Path, yaml: YAML, data: Any) -> None:
    from io import StringIO

    buf = StringIO()
    yaml.dump(data, buf)
    _atomic_write(path, buf.getvalue())


def _read_env_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def _parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in _read_env_lines(path):
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _quote_env(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise HTTPException(status_code=422, detail="Invalid secret value")
    if any(ch.isspace() for ch in value) or any(ch in value for ch in "\"'#$"):
        return json.dumps(value, ensure_ascii=False)
    return value


def _update_env(path: Path, updates: dict[str, str]) -> None:
    lines = _read_env_lines(path)
    seen: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            new_lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            new_lines.append(f"{key}={_quote_env(updates[key])}")
            seen.add(key)
        else:
            new_lines.append(line)
    for key, value in updates.items():
        if key not in seen:
            new_lines.append(f"{key}={_quote_env(value)}")
    _atomic_write(path, "\n".join(new_lines).rstrip() + "\n", mode=0o600)


def _mask_secret(value: str) -> dict[str, Any]:
    if not value:
        return {"configured": False, "masked": ""}
    tail = value[-4:] if len(value) >= 4 else "***"
    return {"configured": True, "masked": f"****{tail}"}


def _validate_model(value: Any, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail=f"Invalid {field}")
    value = value.strip()
    if value and not MODEL_RE.match(value):
        raise HTTPException(status_code=422, detail=f"Invalid {field}")
    return value


def _validate_provider(value: Any) -> str:
    if not isinstance(value, str) or value.strip() not in PROVIDER_VALUES:
        raise HTTPException(status_code=422, detail="Invalid provider")
    return value.strip()


def _validate_base_url(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail="Invalid base_url")
    value = value.strip()
    if not value:
        return None
    if len(value) > 300 or "\n" in value or "\r" in value:
        raise HTTPException(status_code=422, detail="Invalid base_url")
    if not (value.startswith("https://") or value.startswith("http://")):
        raise HTTPException(status_code=422, detail="Invalid base_url")
    return value


def _safe_secret(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail="Invalid secret value")
    value = value.strip()
    if "\n" in value or "\r" in value or len(value) > 4096:
        raise HTTPException(status_code=422, detail="Invalid secret value")
    return value


def _actor(request: Request) -> dict[str, str]:
    email = (
        request.headers.get("X-Auth-Request-Email")
        or request.headers.get("X-Forwarded-Email")
        or request.headers.get("X-Auth-Request-User")
        or "unknown"
    )
    xff = request.headers.get("X-Forwarded-For", "")
    ip = (xff.split(",", 1)[0].strip() if xff else "") or request.headers.get("X-Real-IP", "")
    if not ip and request.client:
        ip = request.client.host
    return {"actor_email": email, "actor_ip": ip or "unknown"}


def _sanitize_changes(changes: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in changes.items():
        if "key" in key.lower() or "secret" in key.lower():
            sanitized[key] = bool(value)
        else:
            sanitized[key] = value
    return sanitized


def audit(action: str, request: Request, *, result: str = "ok", changes: dict[str, Any] | None = None) -> None:
    paths = get_paths()
    record = {
        "timestamp": _now(),
        "action": action,
        **_actor(request),
        "result": result,
        "changes": _sanitize_changes(changes or {}),
    }
    paths.audit_path.parent.mkdir(parents=True, exist_ok=True)
    with paths.audit_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def _read_admin_state(paths: AdminPaths) -> dict[str, Any]:
    if not paths.admin_state_path.exists():
        return {"restart_required": False}
    try:
        return json.loads(paths.admin_state_path.read_text(encoding="utf-8"))
    except Exception:
        return {"restart_required": False}


def _write_admin_state(paths: AdminPaths, data: dict[str, Any]) -> None:
    _atomic_write(
        paths.admin_state_path,
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    )


def mark_restart_required(required: bool) -> None:
    paths = get_paths()
    state = _read_admin_state(paths)
    state["restart_required"] = required
    state["updated_at"] = _now()
    _write_admin_state(paths, state)


def read_config() -> dict[str, Any]:
    paths = get_paths()
    _, data = _load_yaml(paths.config_path)
    env = _parse_env(paths.env_path)
    llm = data.get("llm") or {}
    trigger = data.get("trigger_decider") or {}
    state = _read_admin_state(paths)
    return {
        "llm": {
            "provider": llm.get("provider", "openai"),
            "model": llm.get("model", ""),
            "subagent_model": llm.get("subagent_model", ""),
            "base_url": llm.get("base_url") or "",
        },
        "trigger_decider": {"llm_model": trigger.get("llm_model", "")},
        "api_keys": {
            "openai": _mask_secret(env.get("OPENAI_API_KEY", "")),
            "anthropic": _mask_secret(env.get("ANTHROPIC_API_KEY", "")),
        },
        "restart_required": bool(state.get("restart_required")),
    }


def update_config(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    paths = get_paths()
    yaml, data = _load_yaml(paths.config_path)
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="Invalid config file")
    data.setdefault("llm", {})
    data.setdefault("trigger_decider", {})

    changes: dict[str, Any] = {}
    llm_in = payload.get("llm") or {}
    if llm_in:
        provider = _validate_provider(llm_in.get("provider", data["llm"].get("provider", "openai")))
        model = _validate_model(llm_in.get("model", data["llm"].get("model", "")), "llm.model")
        subagent_model = _validate_model(
            llm_in.get("subagent_model", data["llm"].get("subagent_model", "")),
            "llm.subagent_model",
        )
        base_url = _validate_base_url(llm_in.get("base_url", data["llm"].get("base_url")))
        for key, value in (
            ("provider", provider),
            ("model", model),
            ("subagent_model", subagent_model),
            ("base_url", base_url),
        ):
            if data["llm"].get(key) != value:
                data["llm"][key] = value
                changes[f"llm.{key}"] = value

    trigger_in = payload.get("trigger_decider") or {}
    if trigger_in:
        llm_model = _validate_model(
            trigger_in.get("llm_model", data["trigger_decider"].get("llm_model", "")),
            "trigger_decider.llm_model",
        )
        if data["trigger_decider"].get("llm_model") != llm_model:
            data["trigger_decider"]["llm_model"] = llm_model
            changes["trigger_decider.llm_model"] = llm_model

    env_updates: dict[str, str] = {}
    api_keys = payload.get("api_keys") or {}
    openai_key = _safe_secret(api_keys.get("openai"))
    anthropic_key = _safe_secret(api_keys.get("anthropic"))
    if openai_key:
        env_updates["OPENAI_API_KEY"] = openai_key
        changes["api_keys.openai"] = True
    if anthropic_key:
        env_updates["ANTHROPIC_API_KEY"] = anthropic_key
        changes["api_keys.anthropic"] = True

    if changes:
        if any(not key.startswith("api_keys.") for key in changes):
            _dump_yaml(paths.config_path, yaml, data)
        if env_updates:
            _update_env(paths.env_path, env_updates)
        mark_restart_required(True)
    audit("config.update", request, changes=changes)
    return {"ok": True, "restart_required": bool(changes), "config": read_config()}


def read_control() -> dict[str, Any]:
    paths = get_paths()
    if not paths.control_path.exists():
        return {"paused": False}
    try:
        data = json.loads(paths.control_path.read_text(encoding="utf-8"))
    except Exception:
        return {"paused": False}
    return {
        "paused": bool(data.get("paused")),
        "updated_at": data.get("updated_at", ""),
        "updated_by": data.get("updated_by", ""),
    }


def write_control(paused: bool, request: Request) -> dict[str, Any]:
    paths = get_paths()
    actor = _actor(request)
    data = {
        "paused": paused,
        "updated_at": _now(),
        "updated_by": actor["actor_email"],
    }
    _atomic_write(paths.control_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    audit("control.pause" if paused else "control.resume", request, changes={"paused": paused})
    return {"ok": True, "control": read_control()}


def _service_status(name: str) -> dict[str, Any]:
    if not re.match(r"^[A-Za-z0-9_.@-]+$", name):
        return {"name": name, "active": "invalid", "ok": False}
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {"name": name, "active": "unknown", "ok": False, "error": type(exc).__name__}
    active = proc.stdout.strip() or proc.stderr.strip() or "unknown"
    return {"name": name, "active": active, "ok": proc.returncode == 0}


def status() -> dict[str, Any]:
    paths = get_paths()
    state = _read_admin_state(paths)
    def _mtime(path: Path) -> str:
        if not path.exists():
            return ""
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()

    return {
        "control": read_control(),
        "restart_required": bool(state.get("restart_required")),
        "services": {
            "agent": _service_status(paths.service_name),
            "log_viewer": _service_status("zd-log-viewer"),
        },
        "paths": {
            "config_mtime": _mtime(paths.config_path),
            "env_mtime": _mtime(paths.env_path),
            "log_dir": str(paths.log_dir),
            "audit_path": str(paths.audit_path),
            "control_path": str(paths.control_path),
        },
    }


def restart_service(service: str) -> dict[str, Any]:
    if not re.match(r"^[A-Za-z0-9_.@-]+$", service):
        raise HTTPException(status_code=422, detail="Invalid service")
    proc = subprocess.run(
        ["systemctl", "restart", service],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return {"ok": proc.returncode == 0, "returncode": proc.returncode}


def restart(request: Request) -> dict[str, Any]:
    paths = get_paths()
    result = restart_service(paths.service_name)
    if result.get("ok"):
        mark_restart_required(False)
    audit("service.restart", request, result="ok" if result.get("ok") else "failed")
    return result


def read_audit(limit: int = 100) -> dict[str, Any]:
    paths = get_paths()
    if not paths.audit_path.exists():
        return {"items": []}
    lines = paths.audit_path.read_text(encoding="utf-8").splitlines()[-limit:]
    items = []
    for line in reversed(lines):
        try:
            items.append(json.loads(line))
        except Exception:
            continue
    return {"items": items}

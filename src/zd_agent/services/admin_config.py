"""Schema-driven admin configuration and audit storage."""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from zd_agent.config import AppConfig
from zd_agent.services.atomic_write import atomic_write_text


@dataclass(frozen=True)
class FieldSpec:
    path: str
    label: str
    group: str
    input: str = "text"
    editable: bool = True
    secret: bool = False
    restart_required: bool = False
    protected: bool = False


FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec("llm.provider", "Provider", "模型与运行"),
    FieldSpec("llm.base_url", "Base URL", "模型与运行"),
    FieldSpec("llm.model", "主模型", "模型与运行"),
    FieldSpec("llm.subagent_model", "Subagent 模型", "模型与运行"),
    FieldSpec("llm.temperature", "Temperature", "模型与运行", input="number"),
    FieldSpec("llm.max_tokens", "Max tokens", "模型与运行", input="number"),
    FieldSpec("openai.api_key", "OpenAI API Key", "模型与运行", secret=True),
    FieldSpec("anthropic.api_key", "Anthropic API Key", "模型与运行", secret=True),
    FieldSpec("agent.max_concurrent_sessions", "并发上限", "模型与运行", input="number"),
    FieldSpec("agent.guardrails.max_rounds", "最大轮次", "模型与运行", input="number"),
    FieldSpec("agent.guardrails.timeout_seconds", "会话超时", "模型与运行", input="number"),
    FieldSpec("agent.guardrails.max_tokens_per_session", "最大上下文 token", "模型与运行", input="number"),
    FieldSpec("agent.guardrails.context_compaction_trigger_tokens", "压缩触发 token", "模型与运行", input="number"),
    FieldSpec("agent.guardrails.context_compaction_target_tokens", "压缩目标 token", "模型与运行", input="number"),
    FieldSpec("zendesk.search_query", "新工单搜索条件", "触发配置"),
    FieldSpec("zendesk.poll_interval_seconds", "新工单轮询间隔", "触发配置", input="number", restart_required=True),
    FieldSpec("zendesk.mention_trigger", "@mention 触发词", "触发配置"),
    FieldSpec("zendesk.mention_poll_interval_seconds", "@mention 扫描间隔", "触发配置", input="number", restart_required=True),
    FieldSpec("zendesk.min_ticket_id", "灰度最小工单 ID", "触发配置", input="number"),
    FieldSpec("trigger_decider.llm_model", "触发判定模型", "触发配置"),
    FieldSpec("trigger_decider.rapid_burst_window_seconds", "客户连发窗口", "触发配置", input="number"),
    FieldSpec("trigger_decider.rapid_burst_threshold", "客户连发阈值", "触发配置", input="number"),
    FieldSpec("trigger_decider.insufficient_timeout_minutes", "信息不足超时", "触发配置", input="number"),
    FieldSpec("trigger_decider.abandoned_hours", "放弃等待小时数", "触发配置", input="number"),
    FieldSpec("trigger_decider.enable_public_nudge", "主动公开询问", "触发配置", input="boolean"),
    FieldSpec("public_reply.enabled", "自动 public reply", "回复策略", input="boolean"),
    FieldSpec("public_reply.max_ask_count", "最大 ask 次数", "回复策略", input="number"),
    FieldSpec("public_reply.silent_initial_wait_seconds", "初始等待窗口", "回复策略", input="number"),
    FieldSpec("public_reply.silence_window_seconds", "客户消息合并窗口", "回复策略", input="number"),
    FieldSpec("public_reply.ask_followup_alert_minutes", "ask 后告警分钟", "回复策略", input="number"),
    FieldSpec("public_reply.abandoned_hours", "放弃等待小时数", "回复策略", input="number"),
    FieldSpec("public_reply.browser_relay_url", "Browser relay URL", "回复策略"),
    FieldSpec("public_reply.browser_relay_api_key", "Browser relay API Key", "回复策略", secret=True),
    FieldSpec("zendesk_mcp.mcp_url", "Zendesk MCP URL", "工具与集成"),
    FieldSpec("zendesk_mcp.mcp_api_key", "Zendesk MCP API Key", "工具与集成", secret=True),
    FieldSpec("db_gateway.url", "DB Gateway URL", "工具与集成"),
    FieldSpec("db_gateway.api_key", "DB Gateway API Key", "工具与集成", secret=True),
    FieldSpec("signoz.mcp_url", "SigNoz MCP URL", "工具与集成"),
    FieldSpec("signoz.mcp_api_key", "SigNoz MCP API Key", "工具与集成", secret=True),
    FieldSpec("source_mcp.mcp_url", "Source MCP URL", "工具与集成"),
    FieldSpec("source_mcp.mcp_api_key", "Source MCP API Key", "工具与集成", secret=True),
    FieldSpec("github_mcp.mcp_url", "GitHub MCP URL", "工具与集成"),
    FieldSpec("github_mcp.mcp_api_key", "GitHub MCP API Key", "工具与集成", secret=True),
    FieldSpec("docs_tool_service.mcp_url", "Docs service URL", "工具与集成"),
    FieldSpec("docs_tool_service.mcp_api_key", "Docs service API Key", "工具与集成", secret=True),
    FieldSpec("notification_service.mcp_url", "Notification service URL", "工具与集成"),
    FieldSpec("notification_service.mcp_api_key", "Notification service API Key", "工具与集成", secret=True),
    FieldSpec("sandbox_worker.mcp_url", "Sandbox worker URL", "工具与集成"),
    FieldSpec("sandbox_worker.mcp_api_key", "Sandbox worker API Key", "工具与集成", secret=True),
    FieldSpec("resources.base_dir", "资源根目录", "存储配置", restart_required=True, protected=True),
    FieldSpec("resources.attachments_dir", "附件目录", "存储配置"),
    FieldSpec("resources.state_dir", "状态目录", "存储配置", restart_required=True, protected=True),
    FieldSpec("sandbox.base_dir", "Sandbox 目录", "存储配置"),
    FieldSpec("sandbox.cleanup_on_success", "Sandbox 成功后清理", "存储配置", input="boolean"),
    FieldSpec("sandbox.cleanup_on_failure", "Sandbox 失败后清理", "存储配置", input="boolean"),
    FieldSpec("admin_auth.password_login_enabled", "账号密码登录", "部署状态", input="boolean"),
    FieldSpec("admin_auth.google_sso_enabled", "Google SSO", "部署状态", input="boolean"),
    FieldSpec("admin_auth.google_client_id", "Google Client ID", "部署状态"),
    FieldSpec("admin_auth.google_client_secret", "Google Client Secret", "部署状态", secret=True),
    FieldSpec("admin_auth.google_allowed_domains", "Google 允许域名", "部署状态"),
    FieldSpec("admin_auth.feishu_sso_enabled", "飞书 SSO", "部署状态", input="boolean"),
    FieldSpec("admin_auth.feishu_app_id", "飞书 App ID", "部署状态"),
    FieldSpec("admin_auth.feishu_app_secret", "飞书 App Secret", "部署状态", secret=True),
)

SECRET_ENV_MAP = {
    "openai.api_key": "OPENAI_API_KEY",
    "anthropic.api_key": "ANTHROPIC_API_KEY",
}

ADMIN_AUTH_DEFAULTS: dict[str, Any] = {
    "admin_auth.password_login_enabled": True,
    "admin_auth.google_sso_enabled": False,
    "admin_auth.google_client_id": "",
    "admin_auth.google_allowed_domains": "",
    "admin_auth.feishu_sso_enabled": False,
    "admin_auth.feishu_app_id": "",
}


def _fingerprint(value: str) -> str:
    if not value:
        return ""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    prefix = value[:3] if len(value) >= 3 else "***"
    return f"{prefix}-***{digest}"


def _get_attr(root: Any, path: str) -> Any:
    cur = root
    for part in path.split("."):
        cur = getattr(cur, part)
    return cur


def _set_attr(root: Any, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = root
    for part in parts[:-1]:
        cur = getattr(cur, part)
    setattr(cur, parts[-1], value)


def _coerce_value(spec: FieldSpec, value: Any) -> Any:
    if spec.input == "boolean":
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "开启"}
        return bool(value)
    if spec.input == "number":
        if isinstance(value, bool):
            raise ValueError(f"{spec.path} must be a number")
        if isinstance(value, float):
            return value
        if isinstance(value, int):
            return value
        raw = str(value).strip()
        return float(raw) if "." in raw else int(raw)
    if value is None:
        return ""
    raw = str(value)
    if spec.path.endswith(("base_url", "mcp_url", "url")):
        raw = raw.rstrip("/")
        if raw:
            parsed = urlparse(raw)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"{spec.path} must be an http(s) URL")
        return raw
    return raw


def _validate_normalized_value(spec: FieldSpec, value: Any) -> None:
    if spec.input == "number":
        if not isinstance(value, int | float):
            raise ValueError(f"{spec.path} must be a number")
        if spec.path.endswith(("seconds", "minutes", "hours")) and value < 0:
            raise ValueError(f"{spec.path} must be greater than or equal to 0")
        if any(token in spec.path for token in ("max_", "threshold", "limit")) and value < 0:
            raise ValueError(f"{spec.path} must be greater than or equal to 0")
        if "concurrent" in spec.path and value < 1:
            raise ValueError(f"{spec.path} must be greater than or equal to 1")


def _deep_set(data: dict[str, Any], path: str, value: Any) -> None:
    cur = data
    parts = path.split(".")
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_flatten(value, path))
        else:
            out[path] = value
    return out


class AdminConfigStore:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.overrides_path = state_dir / "admin_overrides.yaml"
        self.secrets_path = state_dir / "admin_secrets.yaml"
        self.audit_path = state_dir / "admin_audit.jsonl"
        self._specs = {spec.path: spec for spec in FIELD_SPECS}

    def schema(self) -> dict[str, Any]:
        groups: list[str] = []
        fields: list[dict[str, Any]] = []
        for spec in FIELD_SPECS:
            if spec.group not in groups:
                groups.append(spec.group)
            fields.append({
                "path": spec.path,
                "label": spec.label,
                "group": spec.group,
                "input": spec.input,
                "editable": spec.editable,
                "secret": spec.secret,
                "restart_required": spec.restart_required,
                "protected": spec.protected,
            })
        return {"groups": groups, "fields": fields}

    def _read_yaml(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        data = yaml.safe_load(path.read_text()) or {}
        return data if isinstance(data, dict) else {}

    def _write_yaml(self, path: Path, data: dict[str, Any], *, secret: bool = False) -> None:
        atomic_write_text(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=True))
        if secret:
            try:
                path.chmod(0o600)
            except OSError:
                pass

    def _secret_status(self, path: str, cfg: AppConfig | None = None) -> dict[str, Any]:
        secrets_data = self._read_yaml(self.secrets_path)
        flat = _flatten(secrets_data)
        value = flat.get(path) or os.environ.get(SECRET_ENV_MAP.get(path, ""), "")
        if not value and cfg is not None and not path.startswith("admin_auth."):
            try:
                value = _get_attr(cfg, path)
            except AttributeError:
                value = ""
        return {
            "configured": bool(value),
            "fingerprint": _fingerprint(str(value)) if value else "",
        }

    def _override_value(self, path: str) -> Any:
        return _flatten(self._read_yaml(self.overrides_path)).get(path)

    def _virtual_value(self, path: str) -> Any:
        value = self._override_value(path)
        if value is not None:
            return value
        return ADMIN_AUTH_DEFAULTS.get(path)

    def effective_config(self, cfg: AppConfig) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for spec in FIELD_SPECS:
            if spec.secret:
                values[spec.path] = self._secret_status(spec.path, cfg)
            elif spec.path.startswith("admin_auth."):
                values[spec.path] = self._virtual_value(spec.path)
            elif spec.path.startswith(("openai.", "anthropic.")):
                values[spec.path] = None
            else:
                override = self._override_value(spec.path)
                values[spec.path] = (
                    override if override is not None else _get_attr(cfg, spec.path)
                )
        return {"values": values}

    def _normalized_changes(self, payload: dict[str, Any]) -> dict[str, Any]:
        changes = payload.get("changes", {})
        if not isinstance(changes, dict):
            raise ValueError("changes must be an object")
        normalized: dict[str, Any] = {}
        for path, value in changes.items():
            spec = self._specs.get(str(path))
            if spec is None or not spec.editable:
                continue
            if spec.secret and (value is None or str(value) == ""):
                continue
            normalized_value = _coerce_value(spec, value)
            _validate_normalized_value(spec, normalized_value)
            normalized[spec.path] = normalized_value
        return normalized

    def preview(self, cfg: AppConfig, payload: dict[str, Any]) -> dict[str, Any]:
        changes = self._normalized_changes(payload)
        diff: list[dict[str, Any]] = []
        restart_required = False
        for path, new_value in changes.items():
            spec = self._specs[path]
            restart_required = restart_required or spec.restart_required
            if spec.secret:
                old_value = self._secret_status(path, cfg)
                shown_new = {
                    "configured": True,
                    "fingerprint": _fingerprint(str(new_value)),
                }
            elif path.startswith("admin_auth."):
                old_value = self._virtual_value(path)
                shown_new = new_value
            else:
                old_value = _get_attr(cfg, path)
                shown_new = new_value
            if old_value != shown_new:
                diff.append({
                    "path": path,
                    "label": spec.label,
                    "group": spec.group,
                    "secret": spec.secret,
                    "restart_required": spec.restart_required,
                    "old": old_value,
                    "new": shown_new,
                })
        return {
            "ok": True,
            "diff": diff,
            "restart_required": restart_required,
            "reason": str(payload.get("reason") or ""),
        }

    def apply(
        self,
        cfg: AppConfig,
        payload: dict[str, Any],
        *,
        actor: str,
        ip: str,
    ) -> dict[str, Any]:
        if not str(payload.get("reason") or "").strip():
            raise ValueError("reason is required")
        preview = self.preview(cfg, payload)
        changes = self._normalized_changes(payload)
        overrides = self._read_yaml(self.overrides_path)
        secrets_data = self._read_yaml(self.secrets_path)

        for path, value in changes.items():
            spec = self._specs[path]
            if spec.secret:
                _deep_set(secrets_data, path, value)
                env_name = SECRET_ENV_MAP.get(path)
                if env_name:
                    os.environ[env_name] = str(value)
                elif not path.startswith(("openai.", "anthropic.", "admin_auth.")):
                    _set_attr(cfg, path, value)
            else:
                _deep_set(overrides, path, value)
                if not path.startswith("admin_auth.") and not spec.restart_required:
                    _set_attr(cfg, path, value)

        self._write_yaml(self.overrides_path, overrides)
        self._write_yaml(self.secrets_path, secrets_data, secret=True)
        audit = self._write_audit(
            actor=actor,
            ip=ip,
            action="config_apply",
            reason=str(payload.get("reason") or ""),
            diff=preview["diff"],
            restart_required=preview["restart_required"],
        )
        return {**preview, "audit_id": audit["id"]}

    def apply_saved(self, cfg: AppConfig) -> None:
        for path, value in _flatten(self._read_yaml(self.overrides_path)).items():
            spec = self._specs.get(path)
            if spec and not spec.secret and not path.startswith("admin_auth."):
                _set_attr(cfg, path, _coerce_value(spec, value))
        for path, value in _flatten(self._read_yaml(self.secrets_path)).items():
            spec = self._specs.get(path)
            if not spec or not spec.secret:
                continue
            env_name = SECRET_ENV_MAP.get(path)
            if env_name:
                os.environ[env_name] = str(value)
            elif not path.startswith(("openai.", "anthropic.", "admin_auth.")):
                _set_attr(cfg, path, value)

    def _write_audit(
        self,
        *,
        actor: str,
        ip: str,
        action: str,
        reason: str,
        diff: list[dict[str, Any]],
        restart_required: bool = False,
    ) -> dict[str, Any]:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        item = {
            "id": f"audit_{int(time.time())}_{uuid.uuid4().hex[:8]}",
            "ts": int(time.time()),
            "actor": actor,
            "ip": ip,
            "action": action,
            "reason": reason,
            "diff": diff,
            "restart_required": restart_required,
            "status": "success",
        }
        with self.audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
        return item

    def audit_event(
        self,
        *,
        actor: str,
        ip: str,
        action: str,
        reason: str = "",
        diff: list[dict[str, Any]] | None = None,
        restart_required: bool = False,
    ) -> dict[str, Any]:
        return self._write_audit(
            actor=actor,
            ip=ip,
            action=action,
            reason=reason,
            diff=diff or [],
            restart_required=restart_required,
        )

    def pending_restart(self, cfg: AppConfig) -> bool:
        flat = _flatten(self._read_yaml(self.overrides_path))
        for path, value in flat.items():
            spec = self._specs.get(path)
            if not spec or not spec.restart_required:
                continue
            try:
                if _get_attr(cfg, path) != _coerce_value(spec, value):
                    return True
            except (AttributeError, ValueError):
                return True
        return False

    def list_audit_logs(
        self,
        *,
        limit: int = 100,
        action: str = "",
        actor: str = "",
    ) -> dict[str, Any]:
        if not self.audit_path.exists():
            return {"items": [], "summary": {"total": 0, "filtered": 0}}
        limit = max(1, min(int(limit), 500))
        lines = self.audit_path.read_text().splitlines()
        total = 0
        matched_count = 0
        filtered: list[dict[str, Any]] = []
        action_filter = action.strip().lower()
        actor_filter = actor.strip().lower()
        for line in reversed(lines):
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            total += 1
            if action_filter and action_filter not in str(item.get("action", "")).lower():
                continue
            if actor_filter and actor_filter not in str(item.get("actor", "")).lower():
                continue
            matched_count += 1
            if len(filtered) < limit:
                filtered.append(_redact_audit_value(item))
        return {
            "items": filtered,
            "summary": {"total": total, "filtered": matched_count},
        }


def _redact_audit_value(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {
            str(entry_key): _redact_audit_value(entry_value, str(entry_key))
            for entry_key, entry_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_audit_value(item, key) for item in value]
    if any(token in key.lower() for token in ("password", "token", "secret", "api_key", "apikey", "authorization")):
        return "[REDACTED]" if value else value
    return value

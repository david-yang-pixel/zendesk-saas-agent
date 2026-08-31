"""Schema-driven admin configuration and audit storage."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ValidationError

from zd_agent.config import AppConfig, CustomMcpServiceConfig
from zd_agent.runtime.openai_reasoning import (
    reasoning_capabilities_manifest,
    validate_reasoning_effort,
)
from zd_agent.services.atomic_write import atomic_write_text


logger = logging.getLogger(__name__)


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
    model_path: str | None = None


FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec("llm.provider", "模型供应商", "模型与运行"),
    FieldSpec("llm.base_url", "模型 Base URL", "模型与运行"),
    FieldSpec("llm.model", "主模型", "模型与运行", input="model_picker"),
    FieldSpec(
        "llm.reasoning_effort",
        "主模型推理强度",
        "模型与运行",
        input="reasoning_effort",
        model_path="llm.model",
    ),
    FieldSpec(
        "llm.enable_vision_input", "视觉能力", "模型与运行", input="vision_capability"
    ),
    FieldSpec("llm.temperature", "温度", "模型与运行", input="number"),
    FieldSpec("llm.timeout", "模型请求超时秒数", "模型与运行", input="number"),
    FieldSpec("llm.max_tokens", "单次最大输出 token", "模型与运行", input="number"),
    FieldSpec(
        "llm.context_window_tokens",
        "活跃上下文窗口 token",
        "模型与运行",
        input="number",
    ),
    FieldSpec("openai.api_key", "OpenAI API Key", "模型与运行", secret=True),
    FieldSpec("anthropic.api_key", "Anthropic API Key", "模型与运行", secret=True),
    FieldSpec(
        "agent.max_concurrent_sessions",
        "并发上限",
        "模型与运行",
        input="number",
        restart_required=True,
    ),
    FieldSpec(
        "agent.session_timeout_seconds",
        "单工单最长运行秒数",
        "模型与运行",
        input="number",
        restart_required=True,
    ),
    FieldSpec(
        "casework.context_compaction_soft_ratio",
        "上下文压缩软阈值比例",
        "模型与运行",
        input="number",
    ),
    FieldSpec(
        "casework.context_compaction_hard_ratio",
        "上下文硬限制比例",
        "模型与运行",
        input="number",
    ),
    FieldSpec(
        "casework.context_checkpoint_max_tokens",
        "检查点最大 token",
        "模型与运行",
        input="number",
    ),
    FieldSpec(
        "casework.context_retain_recent_rounds",
        "压缩后保留最近工具轮次",
        "模型与运行",
        input="number",
    ),
    FieldSpec("retry.max_attempts", "全局重试次数", "模型与运行", input="number"),
    FieldSpec(
        "retry.base_delay_seconds", "全局重试初始延迟秒数", "模型与运行", input="number"
    ),
    FieldSpec(
        "retry.max_delay_seconds", "全局重试最大延迟秒数", "模型与运行", input="number"
    ),
    FieldSpec("retry.jitter", "重试启用 jitter", "模型与运行", input="boolean"),
    FieldSpec(
        "retry.git.max_attempts", "源码/Git 重试次数覆盖", "模型与运行", input="number"
    ),
    FieldSpec("zendesk.subdomain", "Zendesk 子域名", "触发配置", restart_required=True),
    FieldSpec("zendesk.email", "Zendesk 账号邮箱", "触发配置", restart_required=True),
    FieldSpec("zendesk.api_token", "Zendesk API Token", "触发配置", secret=True),
    FieldSpec("zendesk.search_query", "新工单搜索条件", "触发配置"),
    FieldSpec("zendesk.poll_query", "轮询基础查询", "触发配置"),
    FieldSpec(
        "zendesk.poll_interval_seconds",
        "新工单轮询间隔",
        "触发配置",
        input="number",
        restart_required=True,
    ),
    FieldSpec("zendesk.mention_trigger", "主 @mention 触发词", "触发配置"),
    FieldSpec(
        "zendesk.mention_aliases", "@mention 别名触发词", "触发配置", input="textarea"
    ),
    FieldSpec(
        "zendesk.mention_poll_interval_seconds",
        "@mention 扫描间隔",
        "触发配置",
        input="number",
        restart_required=True,
    ),
    FieldSpec(
        "zendesk.agent_user_id",
        "Agent Zendesk 用户 ID",
        "触发配置",
        input="number",
        restart_required=True,
    ),
    FieldSpec("zendesk.min_ticket_id", "灰度最小工单 ID", "触发配置", input="number"),
    FieldSpec(
        "webhook.signing_secret",
        "Tickets webhook 签名密钥",
        "触发配置",
        secret=True,
        restart_required=True,
    ),
    FieldSpec(
        "webhook.messaging_signing_secret",
        "Messaging webhook 签名密钥",
        "触发配置",
        secret=True,
        restart_required=True,
    ),
    FieldSpec(
        "webhook.listen_host",
        "Webhook 监听地址",
        "触发配置",
        restart_required=True,
        protected=True,
    ),
    FieldSpec(
        "webhook.listen_port",
        "Webhook 监听端口",
        "触发配置",
        input="number",
        restart_required=True,
        protected=True,
    ),
    FieldSpec(
        "webhook.zendesk_path",
        "Tickets webhook 路径",
        "触发配置",
        restart_required=True,
    ),
    FieldSpec(
        "webhook.zendesk_messaging_path",
        "Messaging webhook 路径",
        "触发配置",
        restart_required=True,
    ),
    FieldSpec(
        "intake.silence_minutes_default",
        "Intake 静默分钟数",
        "触发配置",
        input="number",
    ),
    FieldSpec(
        "intake.post_agent_cooldown_seconds",
        "处理后冷却秒数",
        "触发配置",
        input="number",
    ),
    FieldSpec(
        "silence_waker.scan_interval_seconds",
        "静默唤醒扫描间隔",
        "触发配置",
        input="number",
        restart_required=True,
    ),
    FieldSpec(
        "waiting_poller.scan_interval_seconds",
        "等待工单扫描间隔",
        "触发配置",
        input="number",
        restart_required=True,
    ),
    FieldSpec(
        "waiting_poller.customer_wait_timeout_hours",
        "客户回应等待上限（小时）",
        "触发配置",
        input="number",
        restart_required=True,
    ),
    FieldSpec(
        "waiting_poller.poll_new_messages",
        "等待工单兜底拉取新消息",
        "触发配置",
        input="boolean",
    ),
    FieldSpec("public_reply.enabled", "自动 public reply", "回复策略", input="boolean"),
    FieldSpec(
        "public_reply.internal_audit_note_enabled",
        "Zendesk 内部审计备注",
        "回复策略",
        input="boolean",
        restart_required=True,
    ),
    FieldSpec(
        "public_reply.silent_initial_wait_seconds",
        "初始等待窗口",
        "回复策略",
        input="number",
    ),
    FieldSpec(
        "public_reply.silence_window_seconds",
        "客户消息合并窗口",
        "回复策略",
        input="number",
    ),
    FieldSpec(
        "public_reply.browser_relay_timeout_seconds",
        "Browser relay 超时秒数",
        "回复策略",
        input="number",
    ),
    FieldSpec("public_reply.browser_relay_url", "Browser relay URL", "工具与集成"),
    FieldSpec(
        "public_reply.browser_relay_readiness_url",
        "Browser relay readiness URL",
        "工具与集成",
        restart_required=True,
    ),
    FieldSpec(
        "public_reply.browser_relay_readiness_interval_seconds",
        "Browser relay readiness 检查间隔秒数",
        "工具与集成",
        input="number",
        restart_required=True,
    ),
    FieldSpec(
        "public_reply.freshness_callback_url", "Freshness callback URL", "工具与集成"
    ),
    FieldSpec(
        "public_reply.browser_relay_api_key",
        "Browser relay API Key",
        "工具与集成",
        secret=True,
    ),
    FieldSpec("zendesk_mcp.mcp_url", "Zendesk MCP URL", "工具与集成"),
    FieldSpec(
        "zendesk_mcp.mcp_api_key", "Zendesk MCP API Key", "工具与集成", secret=True
    ),
    FieldSpec(
        "zendesk_mcp.request_timeout_seconds",
        "Zendesk MCP 超时秒数",
        "工具与集成",
        input="number",
    ),
    FieldSpec("db_gateway.url", "DB Gateway URL", "工具与集成"),
    FieldSpec("db_gateway.api_key", "DB Gateway API Key", "工具与集成", secret=True),
    FieldSpec(
        "db_gateway.request_timeout_seconds",
        "DB Gateway 超时秒数",
        "工具与集成",
        input="number",
    ),
    FieldSpec("signoz.mcp_url", "SigNoz MCP URL", "工具与集成"),
    FieldSpec("signoz.mcp_api_key", "SigNoz MCP API Key", "工具与集成", secret=True),
    FieldSpec(
        "signoz.request_timeout_seconds",
        "SigNoz MCP 超时秒数",
        "工具与集成",
        input="number",
    ),
    FieldSpec("source_mcp.mcp_url", "Source MCP URL", "工具与集成"),
    FieldSpec(
        "source_mcp.mcp_api_key", "Source MCP API Key", "工具与集成", secret=True
    ),
    FieldSpec(
        "source_mcp.request_timeout_seconds",
        "Source MCP 超时秒数",
        "工具与集成",
        input="number",
    ),
    FieldSpec("github_mcp.mcp_url", "GitHub MCP URL", "工具与集成"),
    FieldSpec(
        "github_mcp.mcp_api_key", "GitHub MCP API Key", "工具与集成", secret=True
    ),
    FieldSpec(
        "github_mcp.request_timeout_seconds",
        "GitHub MCP 超时秒数",
        "工具与集成",
        input="number",
    ),
    FieldSpec("docs_tool_service.mcp_url", "Docs service URL", "工具与集成"),
    FieldSpec(
        "docs_tool_service.mcp_api_key",
        "Docs service API Key",
        "工具与集成",
        secret=True,
    ),
    FieldSpec(
        "docs_tool_service.request_timeout_seconds",
        "Docs service 超时秒数",
        "工具与集成",
        input="number",
    ),
    FieldSpec("notification_service.mcp_url", "Notification service URL", "工具与集成"),
    FieldSpec(
        "notification_service.mcp_api_key",
        "Notification service API Key",
        "工具与集成",
        secret=True,
    ),
    FieldSpec(
        "notification_service.request_timeout_seconds",
        "Notification service 超时秒数",
        "工具与集成",
        input="number",
    ),
    FieldSpec("mcp_services.custom", "自定义 MCP 服务", "工具与集成", input="json"),
    FieldSpec(
        "inspector.binary",
        "Inspector binary",
        "工具与集成",
        restart_required=True,
        protected=True,
    ),
    FieldSpec(
        "inspector.config_file",
        "Inspector config file",
        "工具与集成",
        restart_required=True,
        protected=True,
    ),
    FieldSpec(
        "inspector.timeout_seconds", "Inspector 超时秒数", "工具与集成", input="number"
    ),
    FieldSpec(
        "resources.base_dir",
        "资源根目录",
        "存储配置",
        restart_required=True,
        protected=True,
    ),
    FieldSpec(
        "resources.saas_codebase_dir",
        "SaaS 源码目录名",
        "存储配置",
        restart_required=True,
    ),
    FieldSpec(
        "resources.ticket_records_dir",
        "工单记录目录名",
        "存储配置",
        restart_required=True,
    ),
    FieldSpec("resources.attachments_dir", "附件目录", "存储配置"),
    FieldSpec(
        "resources.state_dir",
        "状态目录",
        "存储配置",
        restart_required=True,
        protected=True,
    ),
    FieldSpec(
        "archive.archive_dir", "Agent 归档目录", "存储配置", restart_required=True
    ),
    FieldSpec(
        "logging.jsonl_enabled",
        "JSONL 运行日志",
        "存储配置",
        input="boolean",
        restart_required=True,
    ),
    FieldSpec("logging.log_dir", "JSONL 日志目录", "存储配置", restart_required=True),
    FieldSpec("sandbox.base_dir", "历史 Sandbox 取证目录", "存储配置"),
    FieldSpec(
        "admin_auth.password_login_enabled", "账号密码登录", "部署状态", input="boolean"
    ),
    FieldSpec(
        "admin_auth.google_sso_enabled", "Google SSO", "部署状态", input="boolean"
    ),
    FieldSpec("admin_auth.google_client_id", "Google Client ID", "部署状态"),
    FieldSpec(
        "admin_auth.google_client_secret",
        "Google Client Secret",
        "部署状态",
        secret=True,
    ),
    FieldSpec("admin_auth.google_allowed_domains", "Google 允许域名", "部署状态"),
    FieldSpec("admin_auth.feishu_sso_enabled", "飞书 SSO", "部署状态", input="boolean"),
    FieldSpec("admin_auth.feishu_app_id", "飞书 App ID", "部署状态"),
    FieldSpec(
        "admin_auth.feishu_app_secret", "飞书 App Secret", "部署状态", secret=True
    ),
)

SECRET_ENV_MAP = {
    "openai.api_key": "OPENAI_API_KEY",
    "anthropic.api_key": "ANTHROPIC_API_KEY",
    "zendesk.api_token": "ZENDESK_API_TOKEN",
    "webhook.signing_secret": "ZENDESK_WEBHOOK_SECRET",
    "webhook.messaging_signing_secret": "ZENDESK_MESSAGING_WEBHOOK_SECRET",
    "public_reply.browser_relay_api_key": "ZENDESK_BROWSER_RELAY_API_KEY",
    "zendesk_mcp.mcp_api_key": "ZENDESK_MCP_API_KEY",
    "db_gateway.api_key": "DB_GATEWAY_API_KEY",
    "signoz.mcp_api_key": "SIGNOZ_MCP_API_KEY",
    "source_mcp.mcp_api_key": "SOURCE_MCP_API_KEY",
    "github_mcp.mcp_api_key": "GITHUB_MCP_API_KEY",
    "docs_tool_service.mcp_api_key": "DOCS_TOOL_SERVICE_API_KEY",
    "notification_service.mcp_api_key": "NOTIFICATION_SERVICE_API_KEY",
}

ADMIN_AUTH_DEFAULTS: dict[str, Any] = {
    "admin_auth.password_login_enabled": True,
    "admin_auth.google_sso_enabled": False,
    "admin_auth.google_client_id": "",
    "admin_auth.google_allowed_domains": "",
    "admin_auth.feishu_sso_enabled": False,
    "admin_auth.feishu_app_id": "",
}

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,63}$")
CUSTOM_MCP_PATH = "mcp_services.custom"
CUSTOM_MCP_SECRET_PATH = "mcp_services.custom_api_keys"

ADMIN_OVERRIDE_PATH_ALIASES = {
    "trigger_decider.silence_minutes_default": "intake.silence_minutes_default",
    "trigger_decider.post_agent_cooldown_seconds": (
        "intake.post_agent_cooldown_seconds"
    ),
    "waiting_poller.abandoned_hours": ("waiting_poller.customer_wait_timeout_hours"),
}

REMOVED_ADMIN_OVERRIDE_PATHS = frozenset(
    {
        "casework.max_investigation_rounds",
        "casework.max_investigation_calls",
        "casework.max_investigation_tokens",
        "casework.context_max_compactions",
    }
)


def _fingerprint(value: str) -> str:
    if not value:
        return ""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    prefix = value[:3] if len(value) >= 3 else "***"
    return f"{prefix}-***{digest}"


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


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
    if spec.path == "zendesk.mention_aliases":
        return _coerce_mention_aliases(value)
    if spec.path == "mcp_services.custom":
        return _coerce_custom_mcp_services(value)
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
    if spec.path == "llm.model":
        return raw.strip()
    if spec.path.endswith(("base_url", "mcp_url", "url")):
        raw = raw.rstrip("/")
        if raw:
            parsed = urlparse(raw)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"{spec.path} must be an http(s) URL")
        return raw
    return raw


def _json_payload(value: Any, *, path: str) -> Any:
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} must be valid JSON") from exc
    return value


def _coerce_custom_mcp_services(value: Any) -> list[CustomMcpServiceConfig]:
    payload = _json_payload(value, path="mcp_services.custom")
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise ValueError("mcp_services.custom must be a JSON array")
    services: list[CustomMcpServiceConfig] = []
    seen: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("mcp_services.custom items must be JSON objects")
        service = CustomMcpServiceConfig(**item)
        if "\0" in service.mcp_api_key:
            raise ValueError("mcp_services.custom API keys must not contain NUL bytes")
        name = service.name.strip()
        if not NAME_RE.match(name):
            raise ValueError(f"invalid custom MCP service name: {name or '<empty>'}")
        if name in seen:
            raise ValueError(f"duplicate custom MCP service name: {name}")
        url = service.mcp_url.strip().rstrip("/")
        if service.enabled and not url:
            raise ValueError(f"custom MCP URL is required: {name}")
        if url:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"custom MCP URL must be http(s): {name}")
        timeout = max(3, int(service.request_timeout_seconds or 35))
        services.append(
            service.model_copy(
                update={
                    "name": name,
                    "mcp_url": url,
                    "request_timeout_seconds": timeout,
                }
            )
        )
        seen.add(name)
    return services


def _public_custom_mcp_services(
    services: Any,
    *,
    api_keys: dict[str, str] | None = None,
    include_secret_status: bool = True,
) -> list[dict[str, Any]]:
    public: list[dict[str, Any]] = []
    for service in services or []:
        data = _jsonable(service)
        if not isinstance(data, dict):
            continue
        secret = str(data.pop("mcp_api_key", "") or "")
        name = str(data.get("name") or "")
        if include_secret_status:
            data["api_key_configured"] = bool(secret or (api_keys or {}).get(name, ""))
        public.append(data)
    return public


def _redact_legacy_custom_mcp_audit_value(
    value: Any,
    *,
    secrets: tuple[str, ...],
) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, nested in value.items():
            if key == "mcp_api_key":
                redacted["api_key_configured"] = bool(nested)
                continue
            redacted[key] = _redact_legacy_custom_mcp_audit_value(
                nested,
                secrets=secrets,
            )
        return redacted
    if isinstance(value, list):
        return [
            _redact_legacy_custom_mcp_audit_value(item, secrets=secrets)
            for item in value
        ]
    if isinstance(value, str):
        redacted_text = value
        for secret in secrets:
            redacted_text = redacted_text.replace(secret, "[REDACTED]")
        return redacted_text
    return value


def _coerce_mention_aliases(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_items = [str(item) for item in value]
    else:
        raw_items = str(value).replace(",", "\n").splitlines()
    aliases: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        alias = item.strip()
        if not alias:
            continue
        key = alias.lower()
        if key in seen:
            continue
        seen.add(key)
        aliases.append(alias)
    return aliases


def _validate_normalized_value(spec: FieldSpec, value: Any) -> None:
    if spec.secret and isinstance(value, str) and "\0" in value:
        raise ValueError(f"{spec.path} must not contain NUL bytes")
    if spec.input == "number":
        if not isinstance(value, int | float):
            raise ValueError(f"{spec.path} must be a number")
        if spec.path.endswith(("seconds", "minutes", "hours")) and value < 0:
            raise ValueError(f"{spec.path} must be greater than or equal to 0")
        if (
            any(token in spec.path for token in ("max_", "threshold", "limit"))
            and value < 0
        ):
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


def _canonicalize_admin_overrides(
    data: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    flat = _flatten(data)
    migrations: list[dict[str, str]] = []
    for legacy_path, canonical_path in ADMIN_OVERRIDE_PATH_ALIASES.items():
        if legacy_path not in flat:
            continue
        legacy_value = flat.pop(legacy_path)
        resolution = "canonical_preserved"
        if canonical_path not in flat:
            flat[canonical_path] = legacy_value
            resolution = "migrated"
        migrations.append(
            {
                "from": legacy_path,
                "to": canonical_path,
                "resolution": resolution,
            },
        )
    for removed_path in sorted(REMOVED_ADMIN_OVERRIDE_PATHS):
        if removed_path not in flat:
            continue
        flat.pop(removed_path)
        migrations.append(
            {
                "from": removed_path,
                "to": "(removed)",
                "resolution": "removed",
            }
        )
    timeout_path = "agent.session_timeout_seconds"
    timeout_value = flat.get(timeout_path)
    if isinstance(timeout_value, int | float) and timeout_value > 1_440:
        flat[timeout_path] = 1_440
        migrations.append(
            {
                "from": timeout_path,
                "to": timeout_path,
                "resolution": "clamped_to_1440",
            }
        )
    normalized: dict[str, Any] = {}
    for path, value in flat.items():
        _deep_set(normalized, path, value)
    return normalized, migrations


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
            fields.append(
                {
                    "path": spec.path,
                    "label": spec.label,
                    "group": spec.group,
                    "input": spec.input,
                    "editable": spec.editable,
                    "secret": spec.secret,
                    "restart_required": spec.restart_required,
                    "protected": spec.protected,
                    "model_path": spec.model_path,
                }
            )
        return {
            "groups": groups,
            "fields": fields,
            "reasoning_capabilities": reasoning_capabilities_manifest(),
        }

    def _read_yaml(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        data = yaml.safe_load(path.read_text()) or {}
        return data if isinstance(data, dict) else {}

    def _write_yaml(
        self, path: Path, data: dict[str, Any], *, secret: bool = False
    ) -> None:
        atomic_write_text(
            path, yaml.safe_dump(data, allow_unicode=True, sort_keys=True)
        )
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

    def _custom_mcp_api_keys(self, cfg: AppConfig | None = None) -> dict[str, str]:
        secrets_data = self._read_yaml(self.secrets_path)
        mcp_secrets = secrets_data.get("mcp_services")
        stored = (
            mcp_secrets.get("custom_api_keys") if isinstance(mcp_secrets, dict) else {}
        )
        keys = {
            str(name): str(value)
            for name, value in (stored.items() if isinstance(stored, dict) else [])
            if value
        }
        if cfg is not None:
            for service in cfg.mcp_services.custom:
                if service.mcp_api_key:
                    keys.setdefault(service.name, service.mcp_api_key)
        return keys

    def _redact_legacy_custom_mcp_audit_history(
        self,
        api_keys: dict[str, str],
    ) -> None:
        if not self.audit_path.exists():
            return
        original = self.audit_path.read_text(encoding="utf-8")
        secrets = tuple(value for value in api_keys.values() if value)
        lines: list[str] = []
        for raw_line in original.splitlines():
            try:
                item = json.loads(raw_line)
            except json.JSONDecodeError:
                lines.append(raw_line)
                continue
            redacted = _redact_legacy_custom_mcp_audit_value(
                item,
                secrets=secrets,
            )
            lines.append(json.dumps(redacted, ensure_ascii=False, sort_keys=True))
        sanitized = "\n".join(lines) + ("\n" if lines else "")
        if sanitized != original:
            atomic_write_text(self.audit_path, sanitized)

    def _virtual_value(self, path: str) -> Any:
        if path == "admin_auth.password_login_enabled":
            return True
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
            elif spec.path == CUSTOM_MCP_PATH:
                override = self._override_value(spec.path)
                source = override if override is not None else cfg.mcp_services.custom
                values[spec.path] = _public_custom_mcp_services(
                    source,
                    api_keys=self._custom_mcp_api_keys(cfg),
                )
            else:
                override = self._override_value(spec.path)
                values[spec.path] = _jsonable(
                    override if override is not None else _get_attr(cfg, spec.path)
                )
        return {"values": values}

    def _normalized_changes(
        self,
        payload: dict[str, Any],
        cfg: AppConfig | None = None,
    ) -> dict[str, Any]:
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
            if spec.path == CUSTOM_MCP_PATH:
                raw_services = _json_payload(value, path=CUSTOM_MCP_PATH)
                if not isinstance(raw_services, list):
                    raise ValueError("mcp_services.custom must be a JSON array")
                existing_keys = self._custom_mcp_api_keys(cfg)
                merged_services: list[Any] = []
                for item in raw_services:
                    if not isinstance(item, dict):
                        merged_services.append(item)
                        continue
                    merged = dict(item)
                    original_name = str(
                        merged.pop("_original_name", "") or merged.get("name") or ""
                    )
                    if not str(merged.get("mcp_api_key") or ""):
                        merged["mcp_api_key"] = existing_keys.get(original_name, "")
                    merged_services.append(merged)
                normalized_value = _coerce_custom_mcp_services(merged_services)
            else:
                normalized_value = _coerce_value(spec, value)
            _validate_normalized_value(spec, normalized_value)
            if (
                spec.path == "admin_auth.password_login_enabled"
                and normalized_value is not True
            ):
                raise ValueError("password_login_required")
            normalized[spec.path] = normalized_value
        if cfg is not None:
            main_model = str(normalized.get("llm.model", cfg.llm.model))
            main_effort = validate_reasoning_effort(
                main_model,
                str(normalized.get("llm.reasoning_effort", cfg.llm.reasoning_effort)),
            )
            if "llm.reasoning_effort" in normalized:
                normalized["llm.reasoning_effort"] = main_effort
        return normalized

    def _friendly_validation_error(self, error: ValidationError) -> str:
        raw = str(error)
        if "context compaction soft ratio must be lower than hard ratio" in raw:
            return "上下文压缩软阈值比例必须小于硬限制比例"
        first = error.errors()[0] if error.errors() else {}
        path = ".".join(str(part) for part in first.get("loc", ()))
        label = self._specs.get(path).label if path in self._specs else path or "配置"
        context = first.get("ctx") or {}
        error_type = str(first.get("type") or "")
        if error_type in {"less_than_equal", "less_than"}:
            bound = context.get("le", context.get("lt"))
            return f"{label}不能大于 {bound}"
        if error_type in {"greater_than_equal", "greater_than"}:
            bound = context.get("ge", context.get("gt"))
            return f"{label}不能小于 {bound}"
        return f"{label}无效：{first.get('msg') or '请检查输入值'}"

    def _validated_candidate(
        self,
        cfg: AppConfig,
        changes: dict[str, Any],
        *,
        validate_transport: bool = True,
    ) -> AppConfig:
        payload = cfg.model_dump(mode="python")
        for path, value in changes.items():
            if path.startswith(("openai.", "anthropic.", "admin_auth.")):
                continue
            _deep_set(payload, path, _jsonable(value))
        try:
            candidate = AppConfig.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(self._friendly_validation_error(exc)) from None

        if validate_transport and any(
            path.startswith("public_reply.") for path in changes
        ):
            from zd_agent.tools.public_reply_delivery import (
                PublicReplyTransportError,
                validate_public_reply_transport_config,
            )

            try:
                validate_public_reply_transport_config(candidate)
            except PublicReplyTransportError as exc:
                raise ValueError(str(exc)) from None
        return candidate

    def preview(self, cfg: AppConfig, payload: dict[str, Any]) -> dict[str, Any]:
        changes = self._normalized_changes(payload, cfg)
        self._validated_candidate(cfg, changes)
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
            elif path == CUSTOM_MCP_PATH:
                old_value = _public_custom_mcp_services(
                    _get_attr(cfg, path),
                    api_keys=self._custom_mcp_api_keys(cfg),
                )
                shown_new = _public_custom_mcp_services(new_value)
            else:
                old_value = _jsonable(_get_attr(cfg, path))
                shown_new = _jsonable(new_value)
            if old_value != shown_new:
                diff.append(
                    {
                        "path": path,
                        "label": spec.label,
                        "group": spec.group,
                        "secret": spec.secret,
                        "restart_required": spec.restart_required,
                        "old": old_value,
                        "new": shown_new,
                    }
                )
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
        changes = self._normalized_changes(payload, cfg)
        runtime_changes = {
            path: value
            for path, value in changes.items()
            if not self._specs[path].restart_required
            and not path.startswith(("openai.", "anthropic.", "admin_auth."))
        }
        runtime_candidate = self._validated_candidate(cfg, runtime_changes)
        overrides = self._read_yaml(self.overrides_path)
        secrets_data = self._read_yaml(self.secrets_path)

        for path, value in changes.items():
            spec = self._specs[path]
            if spec.secret:
                _deep_set(secrets_data, path, value)
            elif path == CUSTOM_MCP_PATH:
                _deep_set(
                    overrides,
                    path,
                    _public_custom_mcp_services(
                        value,
                        include_secret_status=False,
                    ),
                )
                _deep_set(
                    secrets_data,
                    CUSTOM_MCP_SECRET_PATH,
                    {
                        service.name: service.mcp_api_key
                        for service in value
                        if service.mcp_api_key
                    },
                )
            else:
                _deep_set(overrides, path, _jsonable(value))

        persisted_paths = (self.overrides_path, self.secrets_path)
        snapshots = {
            path: path.read_bytes() if path.exists() else None
            for path in persisted_paths
        }
        audit_existed = self.audit_path.exists()
        audit_size = self.audit_path.stat().st_size if audit_existed else 0
        try:
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
        except Exception:
            for path, content in snapshots.items():
                if content is None:
                    path.unlink(missing_ok=True)
                    continue
                atomic_write_text(path, content.decode("utf-8"))
                if path == self.secrets_path:
                    path.chmod(0o600)
            if audit_existed and self.audit_path.exists():
                with self.audit_path.open("r+b") as audit_file:
                    audit_file.truncate(audit_size)
            elif not audit_existed:
                self.audit_path.unlink(missing_ok=True)
            raise

        for path, value in changes.items():
            spec = self._specs[path]
            if spec.secret:
                env_name = SECRET_ENV_MAP.get(path)
                if env_name:
                    os.environ[env_name] = str(value)
        for root in sorted({path.split(".", 1)[0] for path in runtime_changes}):
            setattr(cfg, root, getattr(runtime_candidate, root))
        return {**preview, "audit_id": audit["id"]}

    def apply_saved(self, cfg: AppConfig) -> None:
        nonsecret_values: dict[str, Any] = {}
        reasoning_paths = {
            "llm.model",
            "llm.reasoning_effort",
        }
        raw_overrides = self._read_yaml(self.overrides_path)
        normalized_overrides, migrations = _canonicalize_admin_overrides(
            raw_overrides,
        )
        raw_custom_services = _flatten(normalized_overrides).get(CUSTOM_MCP_PATH)
        legacy_custom_services = (
            _coerce_custom_mcp_services(raw_custom_services)
            if isinstance(raw_custom_services, list)
            else []
        )
        legacy_custom_keys = {
            service.name: service.mcp_api_key
            for service in legacy_custom_services
            if service.mcp_api_key
        }
        if legacy_custom_keys:
            secrets_data = self._read_yaml(self.secrets_path)
            current_keys = self._custom_mcp_api_keys()
            for name, value in legacy_custom_keys.items():
                current_keys.setdefault(name, value)
            _deep_set(secrets_data, CUSTOM_MCP_SECRET_PATH, current_keys)
            _deep_set(
                normalized_overrides,
                CUSTOM_MCP_PATH,
                _public_custom_mcp_services(
                    legacy_custom_services,
                    include_secret_status=False,
                ),
            )
            persisted_paths = (self.overrides_path, self.secrets_path)
            snapshots = {
                path: path.read_bytes() if path.exists() else None
                for path in persisted_paths
            }
            audit_snapshot = (
                self.audit_path.read_bytes() if self.audit_path.exists() else None
            )
            try:
                self._write_yaml(self.overrides_path, normalized_overrides)
                self._write_yaml(self.secrets_path, secrets_data, secret=True)
                self._write_audit(
                    actor="system",
                    ip="local",
                    action="custom_mcp_secret_migration",
                    reason="move custom MCP API keys to secret storage",
                    diff=[
                        {
                            "path": CUSTOM_MCP_PATH,
                            "label": "自定义 MCP 服务",
                            "group": "工具与集成",
                            "old": {"secret_storage": "legacy_override"},
                            "new": {
                                "secret_storage": "admin_secrets",
                                "migrated_count": len(legacy_custom_keys),
                            },
                            "secret": True,
                            "restart_required": False,
                        }
                    ],
                )
            except Exception:
                for path, content in snapshots.items():
                    if content is None:
                        path.unlink(missing_ok=True)
                        continue
                    atomic_write_text(path, content.decode("utf-8"))
                    if path == self.secrets_path:
                        path.chmod(0o600)
                if audit_snapshot is None:
                    self.audit_path.unlink(missing_ok=True)
                else:
                    atomic_write_text(
                        self.audit_path,
                        audit_snapshot.decode("utf-8"),
                    )
                raise
        self._redact_legacy_custom_mcp_audit_history(legacy_custom_keys)
        flattened_overrides = _flatten(normalized_overrides)
        unknown_paths = sorted(
            path for path in flattened_overrides if path not in self._specs
        )
        if unknown_paths:
            logger.warning(
                "ignored unknown admin override paths: %s",
                ", ".join(unknown_paths),
            )
        for path, value in flattened_overrides.items():
            spec = self._specs.get(path)
            if spec and not spec.secret and not path.startswith("admin_auth."):
                coerced = _coerce_value(spec, value)
                if path == CUSTOM_MCP_PATH:
                    api_keys = self._custom_mcp_api_keys(cfg)
                    coerced = [
                        service.model_copy(
                            update={"mcp_api_key": api_keys.get(service.name, "")}
                        )
                        for service in coerced
                    ]
                nonsecret_values[path] = coerced

        candidate = (
            self._validated_candidate(
                cfg,
                nonsecret_values,
                validate_transport=False,
            )
            if nonsecret_values
            else cfg
        )

        if any(path in reasoning_paths for path in nonsecret_values):
            validate_reasoning_effort(
                candidate.llm.model,
                candidate.llm.reasoning_effort,
            )
        if migrations:
            previous_overrides = self.overrides_path.read_bytes()
            audit_existed = self.audit_path.exists()
            audit_size = self.audit_path.stat().st_size if audit_existed else 0
            try:
                self._write_yaml(self.overrides_path, normalized_overrides)
                self._write_audit(
                    actor="system",
                    ip="local",
                    action="config_override_migration",
                    reason="migrate legacy admin override paths",
                    diff=migrations,
                )
            except Exception:
                atomic_write_text(
                    self.overrides_path,
                    previous_overrides.decode("utf-8"),
                )
                if audit_existed and self.audit_path.exists():
                    with self.audit_path.open("r+b") as audit_file:
                        audit_file.truncate(audit_size)
                elif not audit_existed:
                    self.audit_path.unlink(missing_ok=True)
                raise
            logger.warning(
                "migrated legacy admin override paths: %s",
                ", ".join(
                    f"{item['from']}->{item['to']} ({item['resolution']})"
                    for item in migrations
                ),
            )
        for root in sorted({path.split(".", 1)[0] for path in nonsecret_values}):
            setattr(cfg, root, getattr(candidate, root))

        for path, value in _flatten(self._read_yaml(self.secrets_path)).items():
            spec = self._specs.get(path)
            if not spec or not spec.secret:
                continue
            env_name = SECRET_ENV_MAP.get(path)
            if env_name:
                os.environ[env_name] = str(value)
            if not path.startswith(("openai.", "anthropic.", "admin_auth.")):
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
            if (
                action_filter
                and action_filter not in str(item.get("action", "")).lower()
            ):
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
    if any(
        token in key.lower()
        for token in (
            "password",
            "token",
            "secret",
            "api_key",
            "apikey",
            "authorization",
        )
    ):
        return "[REDACTED]" if value else value
    return value

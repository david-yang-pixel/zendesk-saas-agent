"""Internal management manifests for hc-client.

These endpoints intentionally expose only redacted runtime state. Secrets stay
in the agent environment and are represented as *_configured booleans.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Awaitable, Callable

import httpx

from zd_agent.config import AppConfig, EnvSettings
from zd_agent.tools.registry import create_tool_registry

ModelFetcher = Callable[[str | None, str], Awaitable[list[str]]]

_DEFAULT_OPENAI_COMPATIBLE_MODELS = (
    "gpt-5.5",
    "gpt-5.5-mini",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5",
    "gpt-5-mini",
    "gpt-4.1",
    "gpt-4.1-mini",
)


def _configured(value: str | None) -> bool:
    return bool((value or "").strip())


def _get_str(obj: Any, *names: str) -> str:
    for name in names:
        value = getattr(obj, name, "")
        if value:
            return str(value)
    return ""


def _get_int(obj: Any, *names: str, default: int) -> int:
    for name in names:
        value = getattr(obj, name, None)
        if value is None or isinstance(value, bool):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return default


def _guardrails_manifest(cfg: AppConfig) -> dict[str, Any]:
    guardrails = cfg.agent.guardrails
    return {
        "max_rounds": guardrails.max_rounds,
        "timeout_seconds": guardrails.timeout_seconds,
        "max_context_tokens": guardrails.max_tokens_per_session,
        "compaction_threshold": guardrails.compaction_threshold,
    }


def build_agent_config_manifest(
    cfg: AppConfig,
    *,
    env: EnvSettings | None = None,
) -> dict[str, Any]:
    """Return the current runtime config in hc-client's expected shape."""
    subagent_model = cfg.llm.subagent_model or None
    effective_subagent_model = cfg.llm.subagent_model or cfg.llm.model
    openai_key_configured = _configured(env.openai_api_key) if env else False
    anthropic_key_configured = _configured(env.anthropic_api_key) if env else False
    llm_key_configured = openai_key_configured or anthropic_key_configured

    return {
        "llm": {
            "provider": cfg.llm.provider,
            "model": cfg.llm.model,
            "base_url": cfg.llm.base_url,
            "api_key_configured": llm_key_configured,
            "temperature": cfg.llm.temperature,
            "timeout": cfg.llm.timeout,
            "max_tokens": cfg.llm.max_tokens,
            "reasoning_effort": None,
        },
        "agent": {
            "max_concurrent_sessions": cfg.agent.max_concurrent_sessions,
            "guardrails": _guardrails_manifest(cfg),
        },
        "subagent": {
            "model": subagent_model,
            "effective_model": effective_subagent_model,
            "base_url": cfg.llm.base_url,
            "effective_base_url": cfg.llm.base_url,
            "api_key_configured": llm_key_configured,
            "reasoning_effort": None,
            "effective_reasoning_effort": None,
            "guardrails": _guardrails_manifest(cfg),
        },
        "features": {
            "auto_public_reply": {
                "enabled": cfg.public_reply.enabled,
            },
            "customer_reply_loop": {
                "enabled": cfg.waiting_poller.poll_new_messages,
                "debounce_seconds": cfg.public_reply.silence_window_seconds,
                "l1_group_id": 0,
                "max_auto_replies": cfg.public_reply.max_ask_count,
            },
        },
        "hindsight": {
            "enabled": False,
            "base_url": "",
            "bank_id": "",
            "recall_budget": "",
            "api_key_configured": False,
        },
        "zendesk": {
            "subdomain": cfg.zendesk.subdomain,
            "poll_interval_seconds": cfg.zendesk.poll_interval_seconds,
            "poll_query": cfg.zendesk.poll_query,
            "mention_trigger": cfg.zendesk.mention_trigger,
            "mention_poll_interval_seconds": cfg.zendesk.mention_poll_interval_seconds,
            "email_configured": _configured(cfg.zendesk.email),
            "api_token_configured": _configured(cfg.zendesk.api_token),
        },
        "webhook": {
            "signing_secret_configured": _configured(cfg.webhook.signing_secret),
            "messaging_signing_secret_configured": _configured(
                cfg.webhook.messaging_signing_secret
            ),
            "internal_bearer_configured": _configured(env.hc_client_api_key) if env else False,
        },
        "public_reply": {
            "enabled": cfg.public_reply.enabled,
            "max_ask_count": cfg.public_reply.max_ask_count,
            "silent_initial_wait_seconds": cfg.public_reply.silent_initial_wait_seconds,
            "silence_window_seconds": cfg.public_reply.silence_window_seconds,
            "ask_followup_alert_minutes": cfg.public_reply.ask_followup_alert_minutes,
            "abandoned_hours": cfg.public_reply.abandoned_hours,
            "browser_relay_url": cfg.public_reply.browser_relay_url,
            "browser_relay_api_key_configured": _configured(
                cfg.public_reply.browser_relay_api_key
            ),
            "browser_relay_timeout_seconds": cfg.public_reply.browser_relay_timeout_seconds,
        },
        "db_gateway": {
            "url": cfg.db_gateway.url,
            "api_key_configured": _configured(cfg.db_gateway.api_key),
            "request_timeout_seconds": _get_int(
                cfg.db_gateway,
                "request_timeout_seconds",
                "timeout_seconds",
                default=35,
            ),
        },
        "signoz": {
            "mcp_url": _get_str(cfg.signoz, "mcp_url", "url"),
            "api_key_configured": _configured(
                _get_str(cfg.signoz, "mcp_api_key", "api_key")
            ),
            "request_timeout_seconds": _get_int(
                cfg.signoz,
                "request_timeout_seconds",
                "timeout_seconds",
                default=35,
            ),
        },
    }


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _set_if_present(target: Any, payload: dict[str, Any], attr: str) -> None:
    if attr in payload and payload[attr] is not None:
        setattr(target, attr, payload[attr])


def _set_positive_int_if_present(target: Any, payload: dict[str, Any], attr: str) -> None:
    value = payload.get(attr)
    if value is None:
        return
    if isinstance(value, bool):
        return
    try:
        value_int = int(value)
    except (TypeError, ValueError):
        return
    if value_int > 0:
        setattr(target, attr, value_int)


def _set_float_if_present(target: Any, payload: dict[str, Any], attr: str) -> None:
    value = payload.get(attr)
    if value is None or isinstance(value, bool):
        return
    try:
        value_float = float(value)
    except (TypeError, ValueError):
        return
    setattr(target, attr, value_float)


def update_agent_config(
    cfg: AppConfig,
    payload: dict[str, Any],
    *,
    env: EnvSettings | None = None,
) -> dict[str, Any]:
    """Apply a conservative in-memory runtime config patch."""
    update = deepcopy(payload)

    llm = _as_mapping(update.get("llm"))
    _set_if_present(cfg.llm, llm, "provider")
    _set_if_present(cfg.llm, llm, "model")
    _set_if_present(cfg.llm, llm, "base_url")
    _set_float_if_present(cfg.llm, llm, "temperature")
    _set_positive_int_if_present(cfg.llm, llm, "timeout")
    if "max_tokens" in llm:
        try:
            max_tokens = int(llm["max_tokens"])
        except (TypeError, ValueError):
            max_tokens = cfg.llm.max_tokens
        if max_tokens >= 0:
            cfg.llm.max_tokens = max_tokens

    subagent = _as_mapping(update.get("subagent"))
    if "model" in subagent:
        cfg.llm.subagent_model = str(subagent.get("model") or "")
    if "base_url" in subagent:
        cfg.llm.base_url = subagent.get("base_url") or None

    agent = _as_mapping(update.get("agent"))
    _set_positive_int_if_present(cfg.agent, agent, "max_concurrent_sessions")
    guardrails = _as_mapping(agent.get("guardrails"))
    _set_positive_int_if_present(cfg.agent.guardrails, guardrails, "max_rounds")
    _set_positive_int_if_present(cfg.agent.guardrails, guardrails, "timeout_seconds")
    if "max_context_tokens" in guardrails:
        _set_positive_int_if_present(
            cfg.agent.guardrails,
            {"max_tokens_per_session": guardrails["max_context_tokens"]},
            "max_tokens_per_session",
        )
    _set_float_if_present(cfg.agent.guardrails, guardrails, "compaction_threshold")

    features = _as_mapping(update.get("features"))
    auto_public_reply = _as_mapping(features.get("auto_public_reply"))
    if "enabled" in auto_public_reply:
        cfg.public_reply.enabled = bool(auto_public_reply["enabled"])
    customer_reply_loop = _as_mapping(features.get("customer_reply_loop"))
    if "enabled" in customer_reply_loop:
        cfg.waiting_poller.poll_new_messages = bool(customer_reply_loop["enabled"])
    if "debounce_seconds" in customer_reply_loop:
        _set_positive_int_if_present(
            cfg.public_reply,
            {"silence_window_seconds": customer_reply_loop["debounce_seconds"]},
            "silence_window_seconds",
        )
    if "max_auto_replies" in customer_reply_loop:
        _set_positive_int_if_present(
            cfg.public_reply,
            {"max_ask_count": customer_reply_loop["max_auto_replies"]},
            "max_ask_count",
        )

    zendesk = _as_mapping(update.get("zendesk"))
    _set_positive_int_if_present(cfg.zendesk, zendesk, "poll_interval_seconds")
    _set_if_present(cfg.zendesk, zendesk, "poll_query")
    _set_if_present(cfg.zendesk, zendesk, "mention_trigger")
    _set_positive_int_if_present(
        cfg.zendesk,
        zendesk,
        "mention_poll_interval_seconds",
    )

    public_reply = _as_mapping(update.get("public_reply"))
    for attr in (
        "max_ask_count",
        "silent_initial_wait_seconds",
        "silence_window_seconds",
        "ask_followup_alert_minutes",
        "abandoned_hours",
        "browser_relay_timeout_seconds",
    ):
        _set_positive_int_if_present(cfg.public_reply, public_reply, attr)
    if "enabled" in public_reply:
        cfg.public_reply.enabled = bool(public_reply["enabled"])
    _set_if_present(cfg.public_reply, public_reply, "browser_relay_url")

    return build_agent_config_manifest(cfg, env=env)


def _dedupe_model_ids(*groups: list[str | None] | tuple[str | None, ...]) -> list[str]:
    seen: set[str] = set()
    model_ids: list[str] = []
    for group in groups:
        for model_id in group:
            candidate = (model_id or "").strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            model_ids.append(candidate)
    return model_ids


def _is_openai_compatible_provider(provider: str) -> bool:
    normalized = provider.strip().lower()
    return normalized in {"openai", "openai-compatible", "openai_compatible"}


def _models_endpoint(base_url: str | None) -> str:
    normalized = (base_url or "https://api.openai.com/v1").rstrip("/")
    return f"{normalized}/models"


async def _fetch_openai_compatible_models(
    base_url: str | None,
    api_key: str,
) -> list[str]:
    if not api_key.strip():
        return []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                _models_endpoint(base_url),
                headers={"Authorization": f"Bearer {api_key.strip()}"},
            )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return []

    raw_models = data.get("data") if isinstance(data, dict) else None
    if not isinstance(raw_models, list):
        return []
    model_ids: list[str] = []
    for item in raw_models:
        model_id = item.get("id") if isinstance(item, dict) else None
        if isinstance(model_id, str) and model_id.strip():
            model_ids.append(model_id.strip())
    return model_ids


async def list_agent_models(
    cfg: AppConfig,
    *,
    target: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    env: EnvSettings | None = None,
    model_fetcher: ModelFetcher = _fetch_openai_compatible_models,
) -> dict[str, Any]:
    """Return model options for hc-client, with safe local fallbacks."""
    if target == "subagent":
        model_id = cfg.llm.subagent_model or cfg.llm.model
    else:
        model_id = cfg.llm.model
    effective_base_url = base_url or cfg.llm.base_url or (env.openai_base_url if env else None)
    effective_api_key = (api_key or (env.openai_api_key if env else "") or "").strip()
    provider_models: list[str] = []
    default_models: tuple[str | None, ...] = ()
    if _is_openai_compatible_provider(cfg.llm.provider):
        provider_models = await model_fetcher(effective_base_url, effective_api_key)
        default_models = _DEFAULT_OPENAI_COMPATIBLE_MODELS
    model_ids = _dedupe_model_ids(
        (model_id, cfg.llm.model, cfg.llm.subagent_model),
        provider_models,
        default_models,
    )
    return {
        "provider": cfg.llm.provider,
        "target": target,
        "base_url": effective_base_url,
        "count": len(model_ids),
        "models": [{"id": model_id} for model_id in model_ids],
    }


def _risk_level(tool: Any) -> str:
    if getattr(tool, "is_write_operation", False):
        return "write"
    if getattr(tool, "requires_sandbox", False):
        return "sandbox"
    return "read"


def build_agent_tools_manifest() -> dict[str, Any]:
    """Return registered tool schemas plus metadata for hc-client display."""
    registry = create_tool_registry()
    tools = []
    for tool in registry.list_tools():
        schema = tool.to_openai_schema().get("function", {})
        tools.append(
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": schema.get("parameters", {}),
                "metadata": {
                    "group": getattr(tool, "group", "default"),
                    "concurrency_safe": bool(getattr(tool, "concurrency_safe", True)),
                    "max_output_tokens": int(getattr(tool, "max_output_tokens", 0) or 0),
                    "requires_sandbox": bool(getattr(tool, "requires_sandbox", False)),
                    "is_write_operation": bool(getattr(tool, "is_write_operation", False)),
                    "risk_level": _risk_level(tool),
                    "source_path": f"{tool.__class__.__module__}.{tool.__class__.__name__}",
                },
            }
        )
    return {"count": len(tools), "tools": tools}

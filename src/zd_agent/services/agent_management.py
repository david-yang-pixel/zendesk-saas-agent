"""Internal management manifests for hc-client.

These endpoints intentionally expose only redacted runtime state. Secrets stay
in the agent environment and are represented as *_configured booleans.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Awaitable, Callable

import httpx

from zd_agent.casework.adapters.tool_registry import create_casework_tool_registry
from zd_agent.config import AppConfig, EnvSettings
from zd_agent.runtime.openai_reasoning import (
    supported_reasoning_efforts,
    validate_reasoning_effort,
)
from zd_agent.tools.service_boundary import (
    get_tool_service_boundary,
    summarize_tool_service_boundaries,
)

ModelFetcher = Callable[[str | None, str], Awaitable[list[str]]]

_DEFAULT_OPENAI_COMPATIBLE_MODELS = (
    "gpt-5.6-terra",
    "gpt-5.6-sol",
    "gpt-5.6-luna",
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


def build_agent_config_manifest(
    cfg: AppConfig,
    *,
    env: EnvSettings | None = None,
) -> dict[str, Any]:
    """Return the current runtime config in hc-client's expected shape."""
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
            "reasoning_effort": cfg.llm.reasoning_effort or None,
        },
        "agent": {
            "max_concurrent_sessions": cfg.agent.max_concurrent_sessions,
        },
        "subagents": {
            "enabled": cfg.subagents.enabled,
            "max_parallel": cfg.subagents.max_parallel,
            "max_tasks_per_run": cfg.subagents.max_tasks_per_run,
            "task_timeout_seconds": cfg.subagents.task_timeout_seconds,
            "slots": [
                {
                    "id": slot.id,
                    "label": slot.label,
                    "enabled": slot.enabled,
                    "provider": slot.llm.provider,
                    "model": slot.llm.model,
                    "base_url": slot.llm.base_url,
                    "reasoning_effort": slot.llm.reasoning_effort or None,
                    "api_key_configured": _configured(slot.llm.api_key),
                }
                for slot in cfg.subagents.slots
            ],
        },
        "casework": {
            "mode": cfg.casework.mode.value,
        },
        "features": {
            "auto_public_reply": {
                "enabled": cfg.public_reply.enabled,
            },
            "customer_event_recovery": {
                "enabled": cfg.waiting_poller.poll_new_messages,
                "debounce_seconds": cfg.public_reply.silence_window_seconds,
                "customer_wait_timeout_hours": (
                    cfg.waiting_poller.customer_wait_timeout_hours
                ),
            },
        },
        "memory": {
            "enabled": True,
            "working_memory": "shared_per_root_session",
            "long_term_memory": "review_gated",
            "authority": "historical_guidance",
            "may_support_current_claims": False,
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
            "internal_bearer_configured": _configured(env.hc_client_api_key)
            if env
            else False,
        },
        "public_reply": {
            "enabled": cfg.public_reply.enabled,
            "silent_initial_wait_seconds": cfg.public_reply.silent_initial_wait_seconds,
            "silence_window_seconds": cfg.public_reply.silence_window_seconds,
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


def _set_positive_int_if_present(
    target: Any, payload: dict[str, Any], attr: str
) -> None:
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


def _set_bool_if_present(
    target: Any,
    payload: dict[str, Any],
    attr: str,
    *,
    path: str,
) -> None:
    if attr not in payload:
        return
    value = payload[attr]
    if not isinstance(value, bool):
        raise ValueError(f"{path} must be a boolean")
    setattr(target, attr, value)


def update_agent_config(
    cfg: AppConfig,
    payload: dict[str, Any],
    *,
    env: EnvSettings | None = None,
) -> dict[str, Any]:
    """Apply a conservative in-memory runtime config patch."""
    original_cfg = cfg
    cfg = cfg.model_copy(deep=True)
    update = deepcopy(payload)

    llm = _as_mapping(update.get("llm"))
    if "model" in llm:
        llm["model"] = str(llm.get("model") or "").strip()
    main_model = str(llm.get("model", cfg.llm.model))
    main_reasoning_effort = validate_reasoning_effort(
        main_model,
        str(llm.get("reasoning_effort", cfg.llm.reasoning_effort)),
    )
    _set_if_present(cfg.llm, llm, "provider")
    _set_if_present(cfg.llm, llm, "model")
    _set_if_present(cfg.llm, llm, "base_url")
    if "reasoning_effort" in llm:
        cfg.llm.reasoning_effort = main_reasoning_effort
    _set_float_if_present(cfg.llm, llm, "temperature")
    _set_positive_int_if_present(cfg.llm, llm, "timeout")
    if "max_tokens" in llm:
        try:
            max_tokens = int(llm["max_tokens"])
        except (TypeError, ValueError):
            max_tokens = cfg.llm.max_tokens
        if max_tokens >= 0:
            cfg.llm.max_tokens = max_tokens

    agent = _as_mapping(update.get("agent"))
    _set_positive_int_if_present(cfg.agent, agent, "max_concurrent_sessions")
    features = _as_mapping(update.get("features"))
    auto_public_reply = _as_mapping(features.get("auto_public_reply"))
    _set_bool_if_present(
        cfg.public_reply,
        auto_public_reply,
        "enabled",
        path="features.auto_public_reply.enabled",
    )
    customer_event_recovery = _as_mapping(features.get("customer_event_recovery"))
    if "enabled" in customer_event_recovery:
        if not isinstance(customer_event_recovery["enabled"], bool):
            raise ValueError(
                "features.customer_event_recovery.enabled must be a boolean"
            )
        cfg.waiting_poller.poll_new_messages = customer_event_recovery["enabled"]
    if "debounce_seconds" in customer_event_recovery:
        _set_positive_int_if_present(
            cfg.public_reply,
            {"silence_window_seconds": customer_event_recovery["debounce_seconds"]},
            "silence_window_seconds",
        )
    if "customer_wait_timeout_hours" in customer_event_recovery:
        _set_positive_int_if_present(
            cfg.waiting_poller,
            {
                "customer_wait_timeout_hours": customer_event_recovery[
                    "customer_wait_timeout_hours"
                ]
            },
            "customer_wait_timeout_hours",
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
        "silent_initial_wait_seconds",
        "silence_window_seconds",
        "browser_relay_timeout_seconds",
    ):
        _set_positive_int_if_present(cfg.public_reply, public_reply, attr)
    _set_bool_if_present(
        cfg.public_reply,
        public_reply,
        "enabled",
        path="public_reply.enabled",
    )
    _set_if_present(cfg.public_reply, public_reply, "browser_relay_url")

    from zd_agent.tools.public_reply_delivery import (
        PublicReplyTransportError,
        validate_public_reply_transport_config,
    )

    try:
        validate_public_reply_transport_config(cfg)
    except PublicReplyTransportError as exc:
        raise ValueError(str(exc)) from None

    for field_name in type(original_cfg).model_fields:
        setattr(original_cfg, field_name, getattr(cfg, field_name))
    return build_agent_config_manifest(original_cfg, env=env)


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
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
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
    model_id = cfg.llm.model
    effective_base_url = (
        base_url or cfg.llm.base_url or (env.openai_base_url if env else None)
    )
    effective_api_key = (api_key or (env.openai_api_key if env else "") or "").strip()
    provider_models: list[str] = []
    default_models: tuple[str | None, ...] = ()
    if _is_openai_compatible_provider(cfg.llm.provider):
        provider_models = await model_fetcher(effective_base_url, effective_api_key)
        default_models = _DEFAULT_OPENAI_COMPATIBLE_MODELS
    model_ids = _dedupe_model_ids(
        (model_id,),
        provider_models,
        default_models,
    )
    return {
        "provider": cfg.llm.provider,
        "target": "agent",
        "base_url": effective_base_url,
        "count": len(model_ids),
        "models": [
            {
                "id": model_id,
                "reasoning_efforts": list(supported_reasoning_efforts(model_id)),
            }
            for model_id in model_ids
        ],
    }


def _risk_level(tool: Any) -> str:
    if getattr(tool, "is_write_operation", False):
        return "write"
    if getattr(tool, "requires_sandbox", False):
        return "sandbox"
    return "read"


def build_agent_tools_manifest() -> dict[str, Any]:
    """Return registered tool schemas plus metadata for hc-client display."""
    registry = create_casework_tool_registry()
    tools = []
    for tool in registry.list_tools():
        schema = tool.to_openai_schema().get("function", {})
        tool_service = get_tool_service_boundary(tool.name).to_metadata()
        tools.append(
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": schema.get("parameters", {}),
                "metadata": {
                    "group": getattr(tool, "group", "default"),
                    "concurrency_safe": bool(getattr(tool, "concurrency_safe", True)),
                    "max_output_tokens": int(
                        getattr(tool, "max_output_tokens", 0) or 0
                    ),
                    "requires_sandbox": bool(getattr(tool, "requires_sandbox", False)),
                    "is_write_operation": bool(
                        getattr(tool, "is_write_operation", False)
                    ),
                    "risk_level": _risk_level(tool),
                    "source_path": f"{tool.__class__.__module__}.{tool.__class__.__name__}",
                    "tool_service": tool_service,
                },
            }
        )
    return {
        "count": len(tools),
        "tool_service_summary": summarize_tool_service_boundaries(
            registry.list_tools()
        ),
        "tools": tools,
    }

from __future__ import annotations

from types import SimpleNamespace

import pytest

from zd_agent.config import AppConfig
from zd_agent.services.agent_management import (
    _fetch_openai_compatible_models,
    build_agent_config_manifest,
    build_agent_tools_manifest,
    list_agent_models,
    update_agent_config,
)


def test_build_agent_config_manifest_redacts_secrets() -> None:
    cfg = AppConfig()
    cfg.llm.model = "gpt-5.5"
    cfg.llm.reasoning_effort = "high"
    cfg.llm.subagent_model = "gpt-5.4-mini"
    cfg.llm.subagent_reasoning_effort = "low"
    cfg.llm.base_url = "https://llm.example.test/v1"
    cfg.zendesk.subdomain = "langgenius"
    cfg.zendesk.email = "agent@example.test"
    cfg.zendesk.api_token = "zendesk-secret"
    cfg.webhook.signing_secret = "ticket-secret"
    cfg.webhook.messaging_signing_secret = "messaging-secret"
    cfg.public_reply.browser_relay_api_key = "relay-secret"
    cfg.db_gateway.api_key = "db-secret"
    cfg.signoz.mcp_api_key = "signoz-secret"

    manifest = build_agent_config_manifest(cfg)

    assert manifest["llm"]["model"] == "gpt-5.5"
    assert manifest["llm"]["reasoning_effort"] == "high"
    assert manifest["subagent"]["model"] == "gpt-5.4-mini"
    assert manifest["subagent"]["reasoning_effort"] == "low"
    assert manifest["subagent"]["effective_reasoning_effort"] == "low"
    assert manifest["llm"]["base_url"] == "https://llm.example.test/v1"
    assert manifest["llm"]["api_key_configured"] is False
    assert manifest["zendesk"]["subdomain"] == "langgenius"
    assert manifest["zendesk"]["email_configured"] is True
    assert manifest["zendesk"]["api_token_configured"] is True
    assert manifest["webhook"]["signing_secret_configured"] is True
    assert manifest["webhook"]["messaging_signing_secret_configured"] is True
    assert manifest["public_reply"]["browser_relay_api_key_configured"] is True
    assert "zendesk-secret" not in str(manifest)
    assert "relay-secret" not in str(manifest)
    assert "db-secret" not in str(manifest)


def test_update_agent_config_applies_only_whitelisted_runtime_fields() -> None:
    cfg = AppConfig()
    cfg.public_reply.browser_relay_url = "http://127.0.0.1:8097/mcp"
    cfg.public_reply.browser_relay_api_key = "relay-secret"

    manifest = update_agent_config(
        cfg,
        {
            "llm": {
                "model": "gpt-5.4",
                "reasoning_effort": "high",
                "temperature": 0.3,
                "api_key": "must-not-be-applied",
            },
            "subagent": {
                "model": "gpt-5.4-mini",
                "reasoning_effort": "low",
            },
            "agent": {
                "max_concurrent_sessions": 3,
                "guardrails": {
                    "max_rounds": 18,
                    "timeout_seconds": 480,
                    "max_context_tokens": 800000,
                    "compaction_threshold": 0.4,
                    "context_compaction_trigger_tokens": 42000,
                    "context_compaction_target_tokens": 28000,
                },
            },
            "features": {
                "auto_public_reply": {"enabled": True},
                "customer_reply_loop": {
                    "enabled": True,
                    "debounce_seconds": 45,
                    "max_auto_replies": 8,
                },
            },
            "zendesk": {
                "poll_interval_seconds": 180,
                "mention_trigger": "@saas-agent",
                "api_token": "must-not-be-applied",
            },
            "unknown": {"field": "ignored"},
        },
    )

    assert cfg.llm.model == "gpt-5.4"
    assert cfg.llm.reasoning_effort == "high"
    assert cfg.llm.subagent_model == "gpt-5.4-mini"
    assert cfg.llm.subagent_reasoning_effort == "low"
    assert cfg.llm.temperature == 0.3
    assert cfg.agent.max_concurrent_sessions == 3
    assert cfg.agent.guardrails.max_rounds == 18
    assert cfg.agent.guardrails.timeout_seconds == 480
    assert cfg.agent.guardrails.max_tokens_per_session == 800000
    assert cfg.agent.guardrails.compaction_threshold == 0.4
    assert cfg.agent.guardrails.context_compaction_trigger_tokens == 42000
    assert cfg.agent.guardrails.context_compaction_target_tokens == 28000
    assert cfg.public_reply.enabled is True
    assert cfg.public_reply.silence_window_seconds == 45
    assert cfg.public_reply.max_ask_count == 8
    assert cfg.zendesk.poll_interval_seconds == 180
    assert cfg.zendesk.mention_trigger == "@saas-agent"
    assert cfg.zendesk.api_token == ""
    assert manifest["agent"]["guardrails"]["max_context_tokens"] == 800000
    assert manifest["agent"]["guardrails"]["context_compaction_target_tokens"] == 28000


def test_update_agent_config_rejects_invalid_public_reply_atomically() -> None:
    cfg = AppConfig()
    cfg.llm.model = "gpt-5.4"
    cfg.public_reply.browser_relay_url = "http://127.0.0.1:8097/mcp"
    cfg.public_reply.browser_relay_api_key = "relay-secret"
    cfg.public_reply.freshness_callback_url = ""

    with pytest.raises(ValueError, match="HTTPS.*non-loopback"):
        update_agent_config(
            cfg,
            {
                "llm": {"model": "gpt-5.6-terra"},
                "features": {"auto_public_reply": {"enabled": True}},
                "public_reply": {
                    "browser_relay_url": (
                        "https://relay.example.test/zendesk-reply-mcp"
                    )
                },
            },
        )

    assert cfg.llm.model == "gpt-5.4"
    assert cfg.public_reply.enabled is False
    assert cfg.public_reply.browser_relay_url == "http://127.0.0.1:8097/mcp"


def test_update_agent_config_trims_model_ids() -> None:
    cfg = AppConfig()

    update_agent_config(
        cfg,
        {
            "llm": {
                "model": "  gpt-5.6-terra  ",
                "reasoning_effort": "max",
            },
            "subagent": {"model": "  gpt-5.4-mini  "},
        },
    )

    assert cfg.llm.model == "gpt-5.6-terra"
    assert cfg.llm.subagent_model == "gpt-5.4-mini"


def test_build_agent_tools_manifest_includes_metadata() -> None:
    manifest = build_agent_tools_manifest()

    assert manifest["count"] > 0
    assert manifest["tool_service_summary"]["total_tools"] == manifest["count"]
    first = manifest["tools"][0]
    assert {"name", "description", "parameters", "metadata"} <= set(first)
    assert {
        "group",
        "concurrency_safe",
        "max_output_tokens",
        "requires_sandbox",
        "is_write_operation",
        "risk_level",
        "source_path",
        "tool_service",
    } <= set(first["metadata"])
    assert {
        "execution_boundary",
        "service",
        "service_backed",
        "fully_isolated",
        "migration_status",
        "migration_target",
    } <= set(first["metadata"]["tool_service"])
    assert all(
        tool["metadata"]["risk_level"] == "read"
        and tool["metadata"]["is_write_operation"] is False
        and tool["metadata"]["requires_sandbox"] is False
        for tool in manifest["tools"]
    )


@pytest.mark.asyncio
async def test_list_agent_models_includes_default_candidates_without_provider_fetch() -> None:
    cfg = AppConfig()
    cfg.llm.model = "gpt-5.5"
    cfg.llm.subagent_model = "gpt-5.5-mini"

    manifest = await list_agent_models(cfg)

    model_ids = [model["id"] for model in manifest["models"]]
    assert model_ids[0] == "gpt-5.5"
    assert "gpt-5.5-mini" in model_ids
    assert "gpt-5.4-mini" in model_ids
    assert manifest["count"] == len(model_ids)
    assert manifest["count"] > 1
    by_id = {model["id"]: model for model in manifest["models"]}
    assert by_id["gpt-5.5"]["reasoning_efforts"] == [
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
    ]
    assert by_id["gpt-4.1"]["reasoning_efforts"] == []


@pytest.mark.asyncio
async def test_list_agent_models_merges_provider_models_with_defaults() -> None:
    cfg = AppConfig()
    cfg.llm.model = "gpt-5.5"
    cfg.llm.base_url = "https://llm.example.test/v1"
    env = SimpleNamespace(openai_api_key="env-key", openai_base_url=None)

    async def fake_fetcher(base_url: str | None, api_key: str) -> list[str]:
        assert base_url == "https://llm.example.test/v1"
        assert api_key == "env-key"
        return ["provider-model", "gpt-5.5"]

    manifest = await list_agent_models(
        cfg,
        env=env,
        model_fetcher=fake_fetcher,
    )

    model_ids = [model["id"] for model in manifest["models"]]
    assert model_ids[:2] == ["gpt-5.5", "provider-model"]
    assert model_ids.count("gpt-5.5") == 1
    assert "gpt-5.4-mini" in model_ids


@pytest.mark.asyncio
async def test_provider_model_fetch_disables_environment_proxy_trust(monkeypatch) -> None:
    observed: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"data": []}

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            observed.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def get(self, *_args, **_kwargs) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(
        "zd_agent.services.agent_management.httpx.AsyncClient",
        FakeClient,
    )

    assert await _fetch_openai_compatible_models("https://example.test/v1", "key") == []
    assert observed["trust_env"] is False

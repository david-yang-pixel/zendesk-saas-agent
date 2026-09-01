from __future__ import annotations

import json

import httpx
import pytest

from casework_sandbox.composition import (
    SandboxConfigurationError,
    SandboxRuntimeConfig,
    create_production_commands,
)


def test_production_config_fails_closed_when_an_external_adapter_is_missing() -> None:
    with pytest.raises(SandboxConfigurationError, match="ZENDESK_BASE_URL"):
        SandboxRuntimeConfig.from_env({})


def test_production_config_requires_at_least_one_registered_investigation_tool(
) -> None:
    env = {
        "ZENDESK_BASE_URL": "https://tenant.zendesk.com",
        "ZENDESK_EMAIL": "support@example.com",
        "ZENDESK_API_TOKEN": "token",
        "CASEWORK_MODEL_BASE_URL": "https://model.example.com",
        "CASEWORK_MODEL_API_KEY": "model-key",
        "CASEWORK_MODEL_NAME": "casework-model",
        "CASEWORK_TOOL_CONFIG": "[]",
    }

    with pytest.raises(SandboxConfigurationError, match="tool"):
        SandboxRuntimeConfig.from_env(env)


def test_tool_config_is_parsed_as_typed_read_only_adapter_contract() -> None:
    env = {
        "ZENDESK_BASE_URL": "https://tenant.zendesk.com",
        "ZENDESK_EMAIL": "support@example.com",
        "ZENDESK_API_TOKEN": "token",
        "CASEWORK_MODEL_BASE_URL": "https://model.example.com",
        "CASEWORK_MODEL_API_KEY": "model-key",
        "CASEWORK_MODEL_NAME": "casework-model",
        "CASEWORK_TOOL_CONFIG": json.dumps(
            [
                {
                    "name": "workspace_lookup",
                    "description": "Find a workspace",
                    "input_schema": {"type": "object"},
                    "endpoint_url": "https://tools.example.com/workspace",
                    "health_url": "https://tools.example.com/healthz",
                }
            ]
        ),
    }

    config = SandboxRuntimeConfig.from_env(env)

    assert config.tool_configs[0].name == "workspace_lookup"
    assert config.tool_configs[0].read_only is True


@pytest.mark.asyncio
async def test_factory_wires_configured_external_adapters_without_database_or_docker(
) -> None:
    env = {
        "ZENDESK_BASE_URL": "https://tenant.zendesk.com",
        "ZENDESK_EMAIL": "support@example.com",
        "ZENDESK_API_TOKEN": "token",
        "CASEWORK_MODEL_BASE_URL": "https://model.example.com",
        "CASEWORK_MODEL_API_KEY": "model-key",
        "CASEWORK_MODEL_NAME": "casework-model",
        "CASEWORK_TOOL_CONFIG": json.dumps(
            [
                {
                    "name": "workspace_lookup",
                    "description": "Find a workspace",
                    "input_schema": {"type": "object"},
                    "endpoint_url": "https://tools.example.com/workspace",
                    "health_url": "https://tools.example.com/healthz",
                }
            ]
        ),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path in {"/healthz", "/api/v2/users/me.json"}
        return httpx.Response(200, json={"status": "ready"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    commands = create_production_commands(environ=env, http_client=client)

    assert await commands.is_ready() is True
    assert commands.tool_names == ("workspace_lookup",)
    await commands.aclose()

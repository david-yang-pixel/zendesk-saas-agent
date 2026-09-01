from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest

from casework_agent_core.runtime_config import MappingSecretResolver
from casework_agent_core.subagents import ChildSession, SubagentTask
from casework_sandbox.composition import (
    HttpChildAgentRunner,
    SandboxConfigurationError,
    SandboxRuntimeConfig,
    create_production_commands,
)
from casework_tool_sdk.registry import ToolExecutionContext, ToolRegistry
from contracts.tool import ToolDefinition, ToolResult, ToolResultStatus


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


@pytest.mark.asyncio
async def test_child_adapter_resolves_only_child_credential_and_model() -> None:
    observed: list[bytes] = []
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        assert request.headers["authorization"] == "Bearer child-secret"
        observed.append(request.read())
        if requests == 1:
            return httpx.Response(
                200,
                json={
                    "tool_call": {
                        "name": "logs_lookup",
                        "arguments": {"query": "error"},
                    }
                },
            )
        return httpx.Response(
            200,
            json={"summary": "Child found evidence", "evidence_ids": ["ev-child"]},
        )

    class _Tool:
        definition = ToolDefinition(
            name="logs_lookup",
            description="Inspect logs",
            input_schema={
                "type": "object",
                "required": ["query"],
                "properties": {"query": {"type": "string"}},
            },
        )

        async def __call__(
            self,
            arguments: dict[str, object],
            context: ToolExecutionContext,
        ) -> ToolResult:
            assert arguments == {"query": "error"}
            assert context.session_id == "sub-1"
            assert context.root_session_id == "root-1"
            return ToolResult(
                status=ToolResultStatus.SUCCESS,
                summary="one matching log",
                evidence_id="ev-child",
                observed_at=datetime.now(UTC),
            )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    runner = HttpChildAgentRunner(
        client=client,
        base_url="https://model.example.com",
        resolver=MappingSecretResolver(
            {"secret/root": "root-secret", "secret/child": "child-secret"}
        ),
    )
    session = ChildSession(
        session_id="sub-1",
        root_session_id="root-1",
        parent_session_id="root-1",
        slot_id="logs",
        provider="child-provider",
        model="child-model",
        credential_ref="secret/child",
        context_window=1_000,
        max_tool_rounds=2,
        workdir="/tmp/sub-1",
        ticket_id=3726,
    )

    registry = ToolRegistry()
    tool = _Tool()
    registry.register(tool.definition, tool)
    report = await runner.run(
        session=session,
        task=SubagentTask(task_id="t-1", objective="inspect logs"),
        tools=registry,
        cancelled=asyncio.Event(),
    )

    assert report.evidence_ids == ("ev-child",)
    assert len(observed) == 2
    assert b'"model":"child-model"' in observed[0]
    assert b'"provider":"child-provider"' in observed[0]
    assert b'"evidence_id":"ev-child"' in observed[1]
    await client.aclose()


@pytest.mark.asyncio
async def test_child_adapter_rejects_unbacked_model_evidence() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200, json={"summary": "unsupported", "evidence_ids": ["made-up"]}
            )
        )
    )
    runner = HttpChildAgentRunner(
        client=client,
        base_url="https://model.example.com",
        resolver=MappingSecretResolver({"secret/child": "child-secret"}),
    )
    session = ChildSession(
        session_id="sub-1",
        root_session_id="root-1",
        parent_session_id="root-1",
        slot_id="logs",
        provider="child-provider",
        model="child-model",
        credential_ref="secret/child",
        context_window=1_000,
        max_tool_rounds=2,
        workdir="/tmp/sub-1",
        ticket_id=3726,
    )

    with pytest.raises(RuntimeError, match="not returned by a tool"):
        await runner.run(
            session=session,
            task=SubagentTask(task_id="t-1", objective="inspect logs"),
            tools=ToolRegistry(),
            cancelled=asyncio.Event(),
        )
    await client.aclose()

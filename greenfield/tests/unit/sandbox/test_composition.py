from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest

from casework_agent_core.domain import ActiveQuestion, TicketSnapshot
from casework_agent_core.runtime_config import MappingSecretResolver
from casework_agent_core.skills import LoadedSkill, SkillManifest
from casework_agent_core.subagents import ChildSession, SubagentTask
from casework_sandbox.composition import (
    HttpCaseworkModel,
    HttpChildAgentRunner,
    SandboxConfigurationError,
    SandboxRuntimeConfig,
    create_production_commands,
)
from casework_tool_sdk.registry import ToolExecutionContext, ToolRegistry
from contracts.ticket import TicketMessage
from contracts.tool import ToolDefinition, ToolResult, ToolResultStatus


def _model_inputs() -> tuple[TicketSnapshot, ActiveQuestion, LoadedSkill]:
    now = datetime.now(UTC)
    snapshot = TicketSnapshot(
        ticket_id=3726,
        status="open",
        requester_id=1,
        messages=(
            TicketMessage(
                event_id="evt-1",
                author_origin="customer",
                public=True,
                body="Find the affected workspace",
                created_at=now,
            ),
        ),
        locale="en-US",
        latest_customer_cursor="evt-1",
        complete=True,
        fetched_at=now,
    )
    question = ActiveQuestion(
        original_event_id="evt-1",
        original_request="Find the affected workspace",
    )
    skill = LoadedSkill(
        manifest=SkillManifest(
            name="core-casework",
            version="1",
            description="casework",
        ),
        content="Investigate with evidence.",
    )
    return snapshot, question, skill


def _response_text(value: object) -> dict[str, str]:
    return {"output_text": json.dumps(value)}


@pytest.mark.asyncio
async def test_root_model_uses_openai_responses_and_typed_plan_and_answer() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/v1/responses"
        assert request.headers["authorization"] == "Bearer root-key"
        payload = json.loads(request.content)
        assert b"root-key" not in request.content
        assert payload["model"] == "root-model"
        assert payload["metadata"] == {"casework_provider": "openai"}
        assert payload["text"]["format"]["type"] == "json_schema"
        assert payload["text"]["format"]["strict"] is True
        if calls == 1:
            return httpx.Response(
                200,
                json=_response_text(
                    {
                        "todos": [
                            {
                                "task_id": "inspect",
                                "goal": "Find evidence",
                                "status": "pending",
                                "assigned_to": "root",
                                "dependencies": [],
                                "skill": None,
                                "suggested_tools": ["workspace_lookup"],
                                "required_evidence": ["workspace_id"],
                                "attempts": 0,
                                "completion_condition": "evidence found",
                            }
                        ]
                    }
                ),
            )
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps({"answer": "Dify"}),
                            }
                        ],
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = HttpCaseworkModel(
        client=client,
        base_url="https://api.openai.com",
        provider="openai",
        api_key="root-key",
        model_name="root-model",
    )
    snapshot, question, skill = _model_inputs()

    plan = await model.plan(snapshot, question, skill, ("workspace_lookup",))
    answer = await model.compose_answer(snapshot, question, ("ev-1",))

    assert plan.items[0].suggested_tools == ("workspace_lookup",)
    assert answer == "Dify"
    assert calls == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_root_model_fails_closed_on_non_json_responses_output() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"output_text": "not-json"})
        )
    )
    model = HttpCaseworkModel(
        client=client,
        base_url="https://api.openai.com",
        provider="openai",
        api_key="root-key",
        model_name="root-model",
    )
    snapshot, question, skill = _model_inputs()

    with pytest.raises(RuntimeError, match="JSON"):
        await model.plan(snapshot, question, skill, ("workspace_lookup",))
    await client.aclose()


@pytest.mark.asyncio
async def test_root_model_reports_sanitized_http_status_and_error_code() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                401,
                json={
                    "error": {
                        "type": "invalid_request_error",
                        "code": "invalid_api_key",
                        "message": "secret provider detail",
                    }
                },
            )
        )
    )
    model = HttpCaseworkModel(
        client=client,
        base_url="https://api.openai.com",
        provider="openai",
        api_key="root-key",
        model_name="root-model",
    )
    snapshot, question, skill = _model_inputs()

    with pytest.raises(
        RuntimeError,
        match=r"HTTP 401 invalid_api_key$",
    ) as captured:
        await model.plan(snapshot, question, skill, ("workspace_lookup",))
    assert "secret provider detail" not in str(captured.value)
    assert "root-key" not in str(captured.value)
    await client.aclose()


@pytest.mark.asyncio
async def test_root_model_rejects_response_fields_outside_strict_schema() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json=_response_text({"todos": [], "unexpected": "ignored"}),
            )
        )
    )
    model = HttpCaseworkModel(
        client=client,
        base_url="https://api.openai.com",
        provider="openai",
        api_key="root-key",
        model_name="root-model",
    )
    snapshot, question, skill = _model_inputs()

    with pytest.raises(RuntimeError, match="unexpected fields"):
        await model.plan(snapshot, question, skill, ("workspace_lookup",))
    await client.aclose()


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


def test_mcp_tool_config_requires_remote_name_and_credential_reference() -> None:
    env = {
        "ZENDESK_BASE_URL": "https://tenant.zendesk.com",
        "ZENDESK_EMAIL": "support@example.com",
        "ZENDESK_API_TOKEN": "token",
        "CASEWORK_MODEL_BASE_URL": "https://model.example.com",
        "CASEWORK_TOOL_CONFIG": json.dumps(
            [
                {
                    "name": "workspace_lookup",
                    "description": "Find a workspace",
                    "input_schema": {"type": "object"},
                    "endpoint_url": "https://mcp.example.com/mcp",
                    "remote_tool_name": "workspace_lookup",
                }
            ]
        ),
    }

    with pytest.raises(SandboxConfigurationError, match="credential_ref"):
        SandboxRuntimeConfig.from_env(env)


def test_mcp_tool_config_is_parsed_with_remote_name_and_credential_reference() -> None:
    env = {
        "ZENDESK_BASE_URL": "https://tenant.zendesk.com",
        "ZENDESK_EMAIL": "support@example.com",
        "ZENDESK_API_TOKEN": "token",
        "CASEWORK_MODEL_BASE_URL": "https://model.example.com",
        "CASEWORK_TOOL_CONFIG": json.dumps(
            [
                {
                    "name": "workspace_lookup",
                    "description": "Find a workspace",
                    "input_schema": {"type": "object"},
                    "endpoint_url": "https://mcp.example.com/mcp",
                    "remote_tool_name": "workspace_lookup",
                    "credential_ref": "mcp/tools",
                }
            ]
        ),
    }

    config = SandboxRuntimeConfig.from_env(env)

    assert config.tool_configs[0].remote_tool_name == "workspace_lookup"
    assert config.tool_configs[0].credential_ref == "mcp/tools"


@pytest.mark.asyncio
async def test_factory_wires_mcp_streamable_http_adapter_and_bearer_credential(
) -> None:
    env = {
        "ZENDESK_BASE_URL": "https://tenant.zendesk.com",
        "ZENDESK_EMAIL": "support@example.com",
        "ZENDESK_API_TOKEN": "token",
        "CASEWORK_MODEL_BASE_URL": "https://model.example.com",
        "CASEWORK_MODEL_API_KEY": "model-key",
        "CASEWORK_MODEL_NAME": "casework-model",
        "CASEWORK_CREDENTIAL_MCP_TOOLS": "mcp-secret",
        "CASEWORK_TOOL_CONFIG": json.dumps(
            [
                {
                    "name": "workspace_lookup",
                    "description": "Find a workspace",
                    "input_schema": {"type": "object"},
                    "endpoint_url": "https://mcp.example.com/mcp",
                    "remote_tool_name": "workspace_lookup",
                    "credential_ref": "mcp/tools",
                }
            ]
        ),
    }
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/users/me.json":
            return httpx.Response(200, json={"user": {"id": 1}})
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ready"})
        assert request.url.path == "/mcp"
        payload = json.loads(request.content)
        methods.append(payload["method"])
        assert request.headers["authorization"] == "Bearer mcp-secret"
        return httpx.Response(
            200,
            headers={"Mcp-Session-Id": "mcp-session"},
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    commands = create_production_commands(environ=env, http_client=client)

    assert await commands.is_ready() is True
    assert commands.tool_names == ("workspace_lookup",)
    assert methods == ["initialize"]
    await commands.aclose()


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
        assert b"child-secret" not in observed[-1]
        assert request.url.path == "/v1/responses"
        payload = json.loads(observed[-1])
        assert payload["model"] == "child-model"
        assert payload["metadata"] == {"casework_provider": "child-provider"}
        if requests == 1:
            return httpx.Response(
                200,
                json=_response_text(
                    {
                        "action": "tool_call",
                        "tool_call": {
                            "name": "logs_lookup",
                            "arguments": json.dumps({"query": "error"}),
                        },
                        "summary": None,
                        "evidence_ids": [],
                    }
                ),
            )
        return httpx.Response(
            200,
            json=_response_text(
                {
                    "action": "report",
                    "tool_call": None,
                    "summary": "Child found evidence",
                    "evidence_ids": ["ev-child"],
                }
            ),
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
    assert b'"casework_provider":"child-provider"' in observed[0]
    await client.aclose()


@pytest.mark.asyncio
async def test_child_adapter_rejects_unbacked_model_evidence() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json=_response_text(
                    {
                        "action": "report",
                        "tool_call": None,
                        "summary": "unsupported",
                        "evidence_ids": ["made-up"],
                    }
                ),
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

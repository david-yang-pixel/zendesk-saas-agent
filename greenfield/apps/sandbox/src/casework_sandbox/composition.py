"""Production composition for the ephemeral sandbox process.

This module is deliberately self-contained: the sandbox talks to Zendesk and
configured model/tool HTTP adapters only. It never imports persistence or the
Docker SDK and cannot reach the Harness queue or Docker socket.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, cast

import httpx
from pydantic import Field, JsonValue, model_validator

from casework_agent_core.domain import ActiveQuestion, TicketSnapshot
from casework_agent_core.skills import LoadedSkill
from casework_agent_core.todo import TodoPlan
from casework_sandbox.runtime import RuntimeOutcome, SandboxAgent
from casework_sandbox.zendesk import ZendeskApiReader
from casework_tool_sdk.registry import ToolExecutionContext, ToolRegistry
from contracts._base import ContractModel
from contracts.outcome import Outcome, OutcomeKind
from contracts.sandbox import SandboxCancelRequest, SandboxStartRequest
from contracts.todo import Todo
from contracts.tool import ToolDefinition, ToolResult


class SandboxConfigurationError(ValueError):
    """Required external adapter configuration is absent or invalid."""


class ToolEndpointConfig(ContractModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    input_schema: dict[str, JsonValue]
    endpoint_url: str = Field(min_length=1)
    health_url: str | None = Field(default=None, min_length=1)
    timeout_seconds: float = Field(default=20.0, gt=0)
    read_only: bool = True

    @model_validator(mode="after")
    def _validate_adapter(self) -> ToolEndpointConfig:
        if not self.endpoint_url.startswith("https://"):
            raise ValueError("tool endpoint_url must use HTTPS")
        if self.health_url is not None and not self.health_url.startswith("https://"):
            raise ValueError("tool health_url must use HTTPS")
        if not self.read_only:
            raise ValueError("sandbox tool adapters must be read-only")
        return self


@dataclass(frozen=True, slots=True)
class SandboxRuntimeConfig:
    zendesk_base_url: str
    zendesk_email: str
    zendesk_api_token: str
    model_base_url: str
    model_api_key: str
    model_name: str
    tool_configs: tuple[ToolEndpointConfig, ...]

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> SandboxRuntimeConfig:
        values = os.environ if environ is None else environ
        required = (
            "ZENDESK_BASE_URL",
            "ZENDESK_EMAIL",
            "ZENDESK_API_TOKEN",
            "CASEWORK_MODEL_BASE_URL",
            "CASEWORK_MODEL_API_KEY",
            "CASEWORK_MODEL_NAME",
            "CASEWORK_TOOL_CONFIG",
        )
        missing = [name for name in required if not values.get(name, "").strip()]
        if missing:
            raise SandboxConfigurationError(
                "missing sandbox adapter configuration: " + ", ".join(missing)
            )
        try:
            raw_tools = json.loads(values["CASEWORK_TOOL_CONFIG"])
        except (json.JSONDecodeError, TypeError) as error:
            raise SandboxConfigurationError(
                "CASEWORK_TOOL_CONFIG must be a JSON array"
            ) from error
        if not isinstance(raw_tools, list) or not raw_tools:
            raise SandboxConfigurationError(
                "CASEWORK_TOOL_CONFIG must contain at least one tool"
            )
        try:
            tool_configs = tuple(
                ToolEndpointConfig.model_validate(item)
                for item in cast(list[object], raw_tools)
            )
        except Exception as error:
            raise SandboxConfigurationError(
                "CASEWORK_TOOL_CONFIG contains an invalid tool"
            ) from error
        return cls(
            zendesk_base_url=values["ZENDESK_BASE_URL"].strip(),
            zendesk_email=values["ZENDESK_EMAIL"].strip(),
            zendesk_api_token=values["ZENDESK_API_TOKEN"].strip(),
            model_base_url=values["CASEWORK_MODEL_BASE_URL"].strip(),
            model_api_key=values["CASEWORK_MODEL_API_KEY"].strip(),
            model_name=values["CASEWORK_MODEL_NAME"].strip(),
            tool_configs=tool_configs,
        )


class HttpInvestigationTool:
    """Read-only adapter that accepts only a typed endpoint result."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        config: ToolEndpointConfig,
    ) -> None:
        self._client = client
        self._config = config
        self.definition = ToolDefinition(
            name=config.name,
            description=config.description,
            input_schema=config.input_schema,
            read_only=True,
        )

    async def ready(self) -> bool:
        if self._config.health_url is None:
            return False
        try:
            response = await self._client.get(
                self._config.health_url, timeout=3.0
            )
            return response.is_success
        except httpx.HTTPError:
            return False

    async def __call__(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        response = await self._client.post(
            self._config.endpoint_url,
            json={
                "ticket_id": context.ticket_id,
                "session_id": context.session_id,
                "arguments": arguments,
            },
            timeout=self._config.timeout_seconds,
        )
        response.raise_for_status()
        return ToolResult.model_validate(response.json())


class HttpCaseworkModel:
    """OpenAI-compatible casework model adapter with typed plan/answer responses."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        model_name: str,
    ) -> None:
        if not base_url.startswith("https://"):
            raise SandboxConfigurationError("CASEWORK_MODEL_BASE_URL must use HTTPS")
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._headers = {"authorization": f"Bearer {api_key}"}
        self._model_name = model_name

    async def plan(
        self,
        snapshot: TicketSnapshot,
        question: ActiveQuestion,
        skill: LoadedSkill,
        available_tools: tuple[str, ...],
    ) -> TodoPlan:
        payload = await self._post(
            "/v1/casework/plan",
            {
                "model": self._model_name,
                "ticket": snapshot.model_dump(mode="json"),
                "question": question.model_dump(mode="json"),
                "skill": skill.model_dump(mode="json"),
                "available_tools": available_tools,
            },
        )
        todos = payload.get("todos")
        if not isinstance(todos, list):
            raise RuntimeError("model plan response is missing todos")
        try:
            return TodoPlan(
                items=[
                    Todo.model_validate(todo) for todo in cast(list[object], todos)
                ]
            )
        except Exception as error:
            raise RuntimeError("model plan response contains invalid Todo") from error

    async def compose_answer(
        self,
        snapshot: TicketSnapshot,
        question: ActiveQuestion,
        evidence_ids: tuple[str, ...],
    ) -> str:
        payload = await self._post(
            "/v1/casework/answer",
            {
                "model": self._model_name,
                "ticket": snapshot.model_dump(mode="json"),
                "question": question.model_dump(mode="json"),
                "evidence_ids": evidence_ids,
            },
        )
        answer = payload.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise RuntimeError("model answer response is missing answer")
        return answer.strip()

    async def ready(self) -> bool:
        try:
            response = await self._client.get(
                f"{self._base_url}/healthz", headers=self._headers, timeout=3.0
            )
            return response.is_success
        except httpx.HTTPError:
            return False

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(
                f"{self._base_url}{path}",
                headers=self._headers,
                json=payload,
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise RuntimeError("configured model adapter request failed") from error
        if not isinstance(data, dict):
            raise RuntimeError("configured model adapter returned a non-object")
        return cast(dict[str, Any], data)


class ProductionSandboxCommands:
    """Owns the injected runtime and translates versioned sandbox messages."""

    def __init__(
        self,
        *,
        agent: SandboxAgent,
        reader: ZendeskApiReader,
        model: HttpCaseworkModel,
        tools: ToolRegistry,
        adapters: tuple[HttpInvestigationTool, ...],
        http_client: httpx.AsyncClient,
    ) -> None:
        self._agent = agent
        self._reader = reader
        self._model = model
        self._tools = tools
        self._adapters = adapters
        self._http_client = http_client
        self._tasks: dict[str, asyncio.Task[RuntimeOutcome]] = {}

    async def is_ready(self) -> bool:
        if not self._tools.names():
            return False
        if not await self._reader.ready() or not await self._model.ready():
            return False
        for adapter in self._adapters:
            if not await adapter.ready():
                return False
        return True

    @property
    def tool_names(self) -> tuple[str, ...]:
        return self._tools.names()

    async def run(self, request: SandboxStartRequest) -> Outcome:
        task = asyncio.create_task(
            self._agent.run(ticket_id=request.ticket_id, session_id=request.session_id)
        )
        self._tasks[request.session_id] = task
        try:
            return await task
        except asyncio.CancelledError:
            return Outcome(
                outcome_id=f"outcome-{request.ticket_id}-cancelled",
                session_id=request.session_id,
                ticket_id=request.ticket_id,
                kind=OutcomeKind.CANCELLED,
                reason="sandbox run cancelled",
            )
        finally:
            self._tasks.pop(request.session_id, None)

    async def cancel(self, request: SandboxCancelRequest) -> None:
        task = self._tasks.get(request.session_id)
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def aclose(self) -> None:
        for task in tuple(self._tasks.values()):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        await self._http_client.aclose()


def create_production_commands(
    *,
    environ: Mapping[str, str] | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> ProductionSandboxCommands:
    config = SandboxRuntimeConfig.from_env(environ)
    client = http_client or httpx.AsyncClient()
    reader = ZendeskApiReader(
        client=client,
        base_url=config.zendesk_base_url,
        email=config.zendesk_email,
        api_token=config.zendesk_api_token,
    )
    model = HttpCaseworkModel(
        client=client,
        base_url=config.model_base_url,
        api_key=config.model_api_key,
        model_name=config.model_name,
    )
    registry = ToolRegistry()
    adapters: list[HttpInvestigationTool] = []
    for tool_config in config.tool_configs:
        adapter = HttpInvestigationTool(client=client, config=tool_config)
        registry.register(adapter.definition, adapter)
        adapters.append(adapter)
    agent = SandboxAgent(zendesk=reader, model=model, tools=registry)
    return ProductionSandboxCommands(
        agent=agent,
        reader=reader,
        model=model,
        tools=registry,
        adapters=tuple(adapters),
        http_client=client,
    )


__all__ = [
    "HttpCaseworkModel",
    "HttpInvestigationTool",
    "ProductionSandboxCommands",
    "SandboxConfigurationError",
    "SandboxRuntimeConfig",
    "ToolEndpointConfig",
    "create_production_commands",
]

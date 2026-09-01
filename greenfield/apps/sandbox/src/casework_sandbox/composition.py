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
from pathlib import Path
from typing import Any, cast

import httpx
from pydantic import Field, JsonValue, ValidationError, model_validator

from casework_agent_core.domain import ActiveQuestion, TicketSnapshot
from casework_agent_core.runtime_config import (
    EnvironmentSecretResolver,
    SecretResolver,
    resolve_runtime_models,
)
from casework_agent_core.skills import LoadedSkill
from casework_agent_core.subagents import (
    ChildSession,
    SubagentOrchestrator,
    SubagentReport,
    SubagentSlot,
    SubagentTask,
)
from casework_agent_core.todo import TodoPlan
from casework_sandbox.mcp import McpStreamableHttpTool
from casework_sandbox.runtime import RuntimeOutcome, SandboxAgent
from casework_sandbox.zendesk import ZendeskApiReader, ZendeskMessagingLoader
from casework_tool_sdk.registry import ToolExecutionContext, ToolRegistry
from contracts._base import ContractModel
from contracts.outcome import Outcome, OutcomeKind
from contracts.sandbox import SandboxCancelRequest, SandboxStartRequest
from contracts.todo import Todo, TodoStatus
from contracts.tool import ToolDefinition, ToolResult


class SandboxConfigurationError(ValueError):
    """Required external adapter configuration is absent or invalid."""


_TODO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "task_id",
        "goal",
        "status",
        "assigned_to",
        "dependencies",
        "skill",
        "suggested_tools",
        "required_evidence",
        "attempts",
        "completion_condition",
    ],
    "properties": {
        "task_id": {"type": "string"},
        "goal": {"type": "string"},
        "status": {
            "type": "string",
            "enum": [
                "pending",
                "ready",
                "running",
                "completed",
                "failed",
                "blocked",
                "cancelled",
            ],
        },
        "assigned_to": {"type": "string"},
        "dependencies": {"type": "array", "items": {"type": "string"}},
        "skill": {"type": ["string", "null"]},
        "suggested_tools": {"type": "array", "items": {"type": "string"}},
        "required_evidence": {
            "type": "array",
            "items": {"type": "string"},
        },
        "attempts": {"type": "integer", "minimum": 0},
        "completion_condition": {"type": "string"},
    },
}

_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["todos"],
    "properties": {
        "todos": {
            "type": "array",
            "maxItems": 128,
            "items": _TODO_SCHEMA,
        }
    },
}

_ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer"],
    "properties": {"answer": {"type": "string"}},
}

_CHILD_STEP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "tool_call", "summary", "evidence_ids"],
    "properties": {
        "action": {"type": "string", "enum": ["tool_call", "report"]},
        "tool_call": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["name", "arguments"],
            "properties": {
                "name": {"type": "string"},
                # Responses strict JSON schemas require closed object
                # properties. Encode arbitrary tool arguments as JSON text and
                # validate/decode them immediately before registry execution.
                "arguments": {"type": "string"},
            },
        },
        "summary": {"type": ["string", "null"]},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
    },
}

_SENSITIVE_MARKERS = (
    "hidden_reasoning",
    "chain_of_thought",
    "api_key",
    "authorization:",
    "bearer ",
    "private_key",
    "-----begin ",
)


def _contains_sensitive_marker(value: str) -> bool:
    folded = value.casefold()
    return any(marker in folded for marker in _SENSITIVE_MARKERS)


def _safe_model_summary(value: str) -> str:
    if _contains_sensitive_marker(value):
        return "Tool result admitted as evidence."
    return value[:2048]


async def _post_responses(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    provider: str,
    model: str,
    api_key: str,
    instructions: str,
    payload: dict[str, Any],
    schema_name: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Call an OpenAI-compatible Responses endpoint and decode JSON text."""

    request_payload = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": instructions}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(
                            payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }
                ],
            },
        ],
        "metadata": {"casework_provider": provider},
        "store": False,
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": schema,
            }
        },
    }
    try:
        response = await client.post(
            f"{base_url.rstrip('/')}/v1/responses",
            headers={"authorization": f"Bearer {api_key}"},
            json=request_payload,
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as error:
        code = "unknown_error"
        try:
            error_payload = error.response.json()
            if isinstance(error_payload, dict):
                response_mapping = cast(dict[str, Any], error_payload)
                error_value = response_mapping.get("error")
                if isinstance(error_value, dict):
                    error_mapping = cast(dict[str, Any], error_value)
                    raw_code = error_mapping.get("code") or error_mapping.get("type")
                    if isinstance(raw_code, str) and raw_code.strip():
                        code = raw_code.strip()[:128]
        except ValueError:
            pass
        raise RuntimeError(
            "configured model adapter request failed: "
            f"HTTP {error.response.status_code} {code}"
        ) from error
    except (httpx.HTTPError, ValueError) as error:
        raise RuntimeError("configured model adapter request failed") from error
    if not isinstance(data, dict):
        raise RuntimeError("configured model adapter returned a non-object")
    response_data = cast(dict[str, Any], data)
    text = response_data.get("output_text")
    if not isinstance(text, str):
        output = response_data.get("output")
        if isinstance(output, list):
            for item in cast(list[object], output):
                if not isinstance(item, dict):
                    continue
                output_item = cast(dict[str, Any], item)
                content = output_item.get("content")
                if not isinstance(content, list):
                    continue
                for part in cast(list[object], content):
                    if not isinstance(part, dict):
                        continue
                    output_part = cast(dict[str, Any], part)
                    if (
                        output_part.get("type") == "output_text"
                        and isinstance(output_part.get("text"), str)
                    ):
                        text = cast(str, output_part["text"])
                        break
                if text is not None:
                    break
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("model response is missing output text")
    try:
        decoded = json.loads(text)
    except (TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("model response output is not valid JSON") from error
    if not isinstance(decoded, dict):
        raise RuntimeError("model response JSON must be an object")
    decoded_object = cast(dict[str, Any], decoded)
    properties = schema.get("properties")
    if isinstance(properties, dict) and set(decoded_object) != set(
        cast(dict[str, Any], properties)
    ):
        raise RuntimeError("model response JSON contains unexpected fields")
    return decoded_object


def _validate_todo_plan(
    plan: TodoPlan,
    *,
    available_tools: tuple[str, ...],
    available_child_slots: tuple[str, ...],
) -> None:
    """Reject model plans that cannot be safely executed by the runtime."""

    task_ids = tuple(item.task_id for item in plan.items)
    if len(set(task_ids)) != len(task_ids):
        raise ValueError("model plan contains duplicate Todo task IDs")
    task_id_set = set(task_ids)
    available_tool_set = set(available_tools)
    available_slot_set = set(available_child_slots)
    for item in plan.items:
        if item.status is not TodoStatus.PENDING or item.attempts != 0:
            raise ValueError("model plan Todo must start pending with zero attempts")
        if any(not value.strip() for value in item.dependencies):
            raise ValueError("model plan contains a blank dependency")
        if any(dependency not in task_id_set for dependency in item.dependencies):
            raise ValueError("model plan contains an unknown dependency")
        if any(not value.strip() for value in item.suggested_tools):
            raise ValueError("model plan contains a blank tool name")
        if any(
            tool_name not in available_tool_set for tool_name in item.suggested_tools
        ):
            raise ValueError("model plan selected an unavailable tool")
        if item.assigned_to != "root" and item.assigned_to not in available_slot_set:
            raise ValueError("model plan selected an unavailable child slot")

    # A dependency cycle would leave the runtime with no ready Todo forever.
    remaining = {item.task_id: set(item.dependencies) for item in plan.items}
    completed: set[str] = set()
    while remaining:
        ready = {
            task_id
            for task_id, dependencies in remaining.items()
            if dependencies.issubset(completed)
        }
        if not ready:
            raise ValueError("model plan contains a dependency cycle")
        completed.update(ready)
        for task_id in ready:
            del remaining[task_id]


class ToolEndpointConfig(ContractModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    input_schema: dict[str, JsonValue]
    endpoint_url: str = Field(min_length=1)
    health_url: str | None = Field(default=None, min_length=1)
    timeout_seconds: float = Field(default=20.0, gt=0)
    read_only: bool = True
    remote_tool_name: str | None = Field(default=None, min_length=1)
    credential_ref: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _validate_adapter(self) -> ToolEndpointConfig:
        if not self.endpoint_url.startswith("https://"):
            raise ValueError("tool endpoint_url must use HTTPS")
        if self.health_url is not None and not self.health_url.startswith("https://"):
            raise ValueError("tool health_url must use HTTPS")
        if not self.read_only:
            raise ValueError("sandbox tool adapters must be read-only")
        if (self.remote_tool_name is None) != (self.credential_ref is None):
            raise ValueError(
                "MCP tool config requires remote_tool_name and credential_ref"
            )
        return self

    @property
    def is_mcp(self) -> bool:
        return self.remote_tool_name is not None


@dataclass(frozen=True, slots=True)
class SandboxRuntimeConfig:
    zendesk_base_url: str
    zendesk_email: str
    zendesk_api_token: str
    model_base_url: str
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
        except ValidationError as error:
            if any(
                item.get("msg")
                == (
                    "Value error, MCP tool config requires remote_tool_name "
                    "and credential_ref"
                )
                for item in error.errors()
            ):
                raise SandboxConfigurationError(
                    "CASEWORK_TOOL_CONFIG requires remote_tool_name and credential_ref"
                ) from error
            raise SandboxConfigurationError(
                "CASEWORK_TOOL_CONFIG contains an invalid tool"
            ) from error
        return cls(
            zendesk_base_url=values["ZENDESK_BASE_URL"].strip(),
            zendesk_email=values["ZENDESK_EMAIL"].strip(),
            zendesk_api_token=values["ZENDESK_API_TOKEN"].strip(),
            model_base_url=values["CASEWORK_MODEL_BASE_URL"].strip(),
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
    """OpenAI-compatible Responses adapter with typed plan/answer responses."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        provider: str,
        api_key: str,
        model_name: str,
        available_child_slots: tuple[str, ...] = (),
    ) -> None:
        if not base_url.startswith("https://"):
            raise SandboxConfigurationError("CASEWORK_MODEL_BASE_URL must use HTTPS")
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._provider = provider
        self._api_key = api_key
        self._headers = {"authorization": f"Bearer {api_key}"}
        self._model_name = model_name
        self._available_child_slots = available_child_slots

    async def plan(
        self,
        snapshot: TicketSnapshot,
        question: ActiveQuestion,
        skill: LoadedSkill,
        available_tools: tuple[str, ...],
    ) -> TodoPlan:
        payload = await _post_responses(
            client=self._client,
            base_url=self._base_url,
            provider=self._provider,
            model=self._model_name,
            api_key=self._api_key,
            instructions=(
                "Create a minimal evidence-driven Todo plan. Assign each Todo "
                "to root or an explicitly available child slot. suggested_tools "
                "must contain only names from available_tools. Return JSON only."
            ),
            payload={
                "ticket": snapshot.model_dump(mode="json"),
                "question": question.model_dump(mode="json"),
                "skill": skill.model_dump(mode="json"),
                "available_tools": available_tools,
                "available_child_slots": self._available_child_slots,
            },
            schema_name="casework_todo_plan",
            schema=_PLAN_SCHEMA,
        )
        todos = payload.get("todos")
        if not isinstance(todos, list):
            raise RuntimeError("model plan response is missing todos")
        try:
            plan = TodoPlan(
                items=[
                    Todo.model_validate(todo) for todo in cast(list[object], todos)
                ]
            )
            _validate_todo_plan(
                plan,
                available_tools=available_tools,
                available_child_slots=self._available_child_slots,
            )
            if any(
                _contains_sensitive_marker(text)
                for todo in plan.items
                for text in (todo.goal, todo.completion_condition)
            ):
                raise ValueError("model plan contains sensitive text")
            return plan
        except Exception as error:
            raise RuntimeError("model plan response contains invalid Todo") from error

    async def compose_answer(
        self,
        snapshot: TicketSnapshot,
        question: ActiveQuestion,
        evidence_ids: tuple[str, ...],
    ) -> str:
        payload = await _post_responses(
            client=self._client,
            base_url=self._base_url,
            provider=self._provider,
            model=self._model_name,
            api_key=self._api_key,
            instructions=(
                "Answer the customer's question using only the supplied evidence "
                "IDs. Return JSON only with a concise customer-facing answer."
            ),
            payload={
                "ticket": snapshot.model_dump(mode="json"),
                "question": question.model_dump(mode="json"),
                "evidence_ids": evidence_ids,
            },
            schema_name="casework_answer",
            schema=_ANSWER_SCHEMA,
        )
        answer = payload.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise RuntimeError("model answer response is missing answer")
        if len(answer) > 16_384:
            raise RuntimeError("model answer response exceeds 16384 characters")
        if _contains_sensitive_marker(answer):
            raise RuntimeError("model answer response contains sensitive text")
        return answer.strip()

    async def ready(self) -> bool:
        try:
            response = await self._client.get(
                f"{self._base_url}/v1/models", headers=self._headers, timeout=3.0
            )
            return response.is_success
        except httpx.HTTPError:
            return False

class HttpChildAgentRunner:
    """Independent child model adapter used by the depth-one orchestrator."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        resolver: SecretResolver,
    ) -> None:
        if not base_url.startswith("https://"):
            raise SandboxConfigurationError("CASEWORK_MODEL_BASE_URL must use HTTPS")
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._resolver = resolver

    async def run(
        self,
        *,
        session: ChildSession,
        task: SubagentTask,
        tools: ToolRegistry,
        cancelled: asyncio.Event,
    ) -> SubagentReport:
        if cancelled.is_set():
            raise asyncio.CancelledError
        from casework_agent_core.runtime_config import resolve_model_credentials
        from contracts.runtime import ModelBinding

        binding = ModelBinding(
            provider=session.provider,
            model=session.model,
            credential_ref=session.credential_ref,
        )
        resolved = resolve_model_credentials(binding, resolver=self._resolver)
        observations: list[dict[str, object]] = []
        admitted_evidence: set[str] = set()
        artifact_refs: list[str] = []
        for round_number in range(1, session.max_tool_rounds + 1):
            if cancelled.is_set():
                raise asyncio.CancelledError
            child_payload = await self._step(
                session=session,
                task=task,
                api_key=resolved.api_key,
                available_tools=tools.names(),
                observations=observations,
                round_number=round_number,
            )
            action = child_payload.get("action")
            tool_call = child_payload.get("tool_call")
            if action == "tool_call":
                if not isinstance(tool_call, dict):
                    raise RuntimeError("child model tool_call must be an object")
                call = cast(dict[str, Any], tool_call)
                if set(call) != {"name", "arguments"}:
                    raise RuntimeError(
                        "child model tool_call contains unexpected fields"
                    )
                tool_name = call.get("name")
                arguments_value = call.get("arguments", "{}")
                if not isinstance(tool_name, str) or not tool_name.strip():
                    raise RuntimeError("child model tool_call is missing name")
                if child_payload.get("summary") is not None:
                    raise RuntimeError(
                        "child model tool_call must not include a summary"
                    )
                if child_payload.get("evidence_ids") != []:
                    raise RuntimeError(
                        "child model tool_call must not include evidence IDs"
                    )
                if isinstance(arguments_value, str):
                    try:
                        arguments = json.loads(arguments_value)
                    except json.JSONDecodeError as error:
                        raise RuntimeError(
                            "child model tool arguments are not valid JSON"
                        ) from error
                else:
                    # Keep compatibility with existing OpenAI-compatible
                    # gateways that return an object despite the schema.
                    arguments = arguments_value
                if not isinstance(arguments, dict):
                    raise RuntimeError("child model tool arguments must be an object")
                result = await tools.execute(
                    tool_name,
                    cast(dict[str, Any], arguments),
                    context=ToolExecutionContext(
                        ticket_id=session.ticket_id,
                        session_id=session.session_id,
                        root_session_id=session.root_session_id,
                    ),
                )
                if result.evidence_id:
                    admitted_evidence.add(result.evidence_id)
                artifact_refs.extend(result.artifacts)
                observations.append(
                    {
                        "tool_name": tool_name,
                        "status": result.status.value,
                        "summary": _safe_model_summary(result.summary),
                        "evidence_id": result.evidence_id,
                        "artifact_refs": list(result.artifacts),
                    }
                )
                continue
            if action != "report" or tool_call is not None:
                raise RuntimeError("child model returned an invalid action")
            return self._report(
                task=task,
                payload=child_payload,
                admitted_evidence=admitted_evidence,
                artifact_refs=artifact_refs,
            )
        raise RuntimeError("child model exceeded max_tool_rounds without a report")

    async def _step(
        self,
        *,
        session: ChildSession,
        task: SubagentTask,
        api_key: str,
        available_tools: tuple[str, ...],
        observations: list[dict[str, object]],
        round_number: int,
    ) -> dict[str, Any]:
        return await _post_responses(
            client=self._client,
            base_url=self._base_url,
            provider=session.provider,
            model=session.model,
            api_key=api_key,
            instructions=(
                "Investigate only the assigned task. Choose tool_call to use "
                "one available read-only tool, or report when evidence is ready. "
                "Never invent evidence IDs. Return JSON only."
            ),
            payload={
                "ticket_id": session.ticket_id,
                "context_window_tokens": session.context_window,
                "round": round_number,
                "task": {
                    "task_id": task.task_id,
                    "objective": task.objective,
                    "required_evidence": task.required_evidence,
                },
                "available_tools": available_tools,
                "observations": observations,
            },
            schema_name="casework_child_step",
            schema=_CHILD_STEP_SCHEMA,
        )

    @staticmethod
    def _report(
        *,
        task: SubagentTask,
        payload: dict[str, Any],
        admitted_evidence: set[str],
        artifact_refs: list[str],
    ) -> SubagentReport:
        summary = payload.get("summary")
        evidence_ids = payload.get("evidence_ids", [])
        if not isinstance(summary, str) or not summary.strip():
            raise RuntimeError("child model response is missing summary")
        if _contains_sensitive_marker(summary):
            raise RuntimeError("child model response contains sensitive text")
        if not isinstance(evidence_ids, list) or not all(
            isinstance(value, str) and value.strip()
            for value in cast(list[object], evidence_ids)
        ):
            raise RuntimeError("child model response contains invalid evidence_ids")
        raw_evidence = cast(list[object], evidence_ids)
        evidence_values = tuple(cast(str, value) for value in raw_evidence)
        if any(value not in admitted_evidence for value in evidence_values):
            raise RuntimeError("child model reported evidence not returned by a tool")
        return SubagentReport(
            task_id=task.task_id,
            summary=summary.strip(),
            evidence_ids=evidence_values,
            artifact_refs=tuple(dict.fromkeys(artifact_refs)),
        )


class ProductionSandboxCommands:
    """Owns the injected runtime and translates versioned sandbox messages."""

    def __init__(
        self,
        *,
        reader: ZendeskApiReader,
        tools: ToolRegistry,
        adapters: tuple[HttpInvestigationTool | McpStreamableHttpTool, ...],
        http_client: httpx.AsyncClient,
        model_base_url: str,
        secret_resolver: SecretResolver,
        sandbox_workdir: Path | None = None,
    ) -> None:
        self._reader = reader
        self._tools = tools
        self._adapters = adapters
        self._http_client = http_client
        self._model_base_url = model_base_url.rstrip("/")
        self._secret_resolver = secret_resolver
        self._sandbox_workdir = sandbox_workdir or Path("/tmp/casework-sandbox")
        self._tasks: dict[str, asyncio.Task[RuntimeOutcome]] = {}

    async def is_ready(self) -> bool:
        if not self._tools.names():
            return False
        if not await self._reader.ready() or not await self._model_ready():
            return False
        for adapter in self._adapters:
            if not await adapter.ready():
                return False
        return True

    @property
    def tool_names(self) -> tuple[str, ...]:
        return self._tools.names()

    async def run(self, request: SandboxStartRequest) -> Outcome:
        try:
            agent = self._agent_for(request)
        except Exception as error:
            return Outcome(
                outcome_id=f"outcome-{request.ticket_id}-bootstrap-failed",
                session_id=request.session_id,
                ticket_id=request.ticket_id,
                kind=OutcomeKind.FAILED,
                reason=str(error),
            )
        task = asyncio.create_task(
            agent.run(ticket_id=request.ticket_id, session_id=request.session_id)
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

    async def _model_ready(self) -> bool:
        # The immutable per-job bootstrap supplies the model credential, so a
        # Harness readiness probe cannot safely make an authenticated model
        # request. Validate the direct Responses base URL here; the first
        # per-job request remains fail-closed on auth or model errors.
        return self._model_base_url.startswith("https://")

    def _agent_for(self, request: SandboxStartRequest) -> SandboxAgent:
        bootstrap = request.bootstrap
        models = resolve_runtime_models(
            bootstrap, resolver=self._secret_resolver
        )
        runtime_tools = self._tools.allowlist(bootstrap.enabled_tools)
        root = HttpCaseworkModel(
            client=self._http_client,
            base_url=self._model_base_url,
            provider=models.root.provider,
            api_key=models.root.api_key,
            model_name=models.root.model,
            available_child_slots=tuple(
                child.slot_id for child in bootstrap.enabled_child_slots
            ),
        )
        slots = tuple(
            SubagentSlot(
                slot_id=child.slot_id,
                provider=child.provider,
                model=child.model,
                credential_ref=child.credential_ref,
                context_window=child.context_window_tokens,
                max_tool_rounds=child.max_tool_rounds,
                max_parallel=child.max_parallel,
            )
            for child in bootstrap.enabled_child_slots
        )
        runner = HttpChildAgentRunner(
            client=self._http_client,
            base_url=self._model_base_url,
            resolver=self._secret_resolver,
        )
        orchestrator = SubagentOrchestrator(
            root_session_id=request.session_id,
            sandbox_workdir=self._sandbox_workdir,
            slots=slots,
            tools=runtime_tools,
            runner=runner,
            ticket_id=request.ticket_id,
        )
        return SandboxAgent(
            zendesk=self._reader,
            model=root,
            tools=runtime_tools,
            subagent_orchestrator=orchestrator if slots else None,
            sandbox_workdir=self._sandbox_workdir,
            runtime_bootstrap=bootstrap,
        )


def create_production_commands(
    *,
    environ: Mapping[str, str] | None = None,
    http_client: httpx.AsyncClient | None = None,
    sandbox_workdir: Path | None = None,
) -> ProductionSandboxCommands:
    config = SandboxRuntimeConfig.from_env(environ)
    client = http_client or httpx.AsyncClient()
    values = os.environ if environ is None else environ
    resolver = EnvironmentSecretResolver(values)
    registry = ToolRegistry()
    adapters: list[HttpInvestigationTool | McpStreamableHttpTool] = []
    mcp_adapters: dict[str, McpStreamableHttpTool] = {}
    for tool_config in config.tool_configs:
        if tool_config.is_mcp:
            remote_tool_name = tool_config.remote_tool_name
            credential_ref = tool_config.credential_ref
            if remote_tool_name is None or credential_ref is None:
                raise SandboxConfigurationError(
                    "MCP tool config requires remote_tool_name and credential_ref"
                )
            adapter = McpStreamableHttpTool(
                client=client,
                name=tool_config.name,
                description=tool_config.description,
                input_schema=tool_config.input_schema,
                endpoint_url=tool_config.endpoint_url,
                remote_tool_name=remote_tool_name,
                credential_ref=credential_ref,
                resolver=resolver,
                timeout_seconds=tool_config.timeout_seconds,
            )
        else:
            adapter = HttpInvestigationTool(client=client, config=tool_config)
        registry.register(adapter.definition, adapter)
        adapters.append(adapter)
        if isinstance(adapter, McpStreamableHttpTool):
            mcp_adapters[tool_config.name] = adapter
    ticket_tool = mcp_adapters.get("get_ticket")
    conversation_tool = mcp_adapters.get("get_ticket_conversation_log")
    messaging_loader = (
        ZendeskMessagingLoader(
            ticket_tool=ticket_tool,
            conversation_tool=conversation_tool,
        )
        if ticket_tool is not None and conversation_tool is not None
        else None
    )
    reader = ZendeskApiReader(
        client=client,
        base_url=config.zendesk_base_url,
        email=config.zendesk_email,
        api_token=config.zendesk_api_token,
        messaging_loader=messaging_loader,
    )
    return ProductionSandboxCommands(
        reader=reader,
        tools=registry,
        adapters=tuple(adapters),
        http_client=client,
        model_base_url=config.model_base_url,
        secret_resolver=resolver,
        sandbox_workdir=sandbox_workdir,
    )


__all__ = [
    "HttpCaseworkModel",
    "HttpInvestigationTool",
    "McpStreamableHttpTool",
    "ProductionSandboxCommands",
    "SandboxConfigurationError",
    "SandboxRuntimeConfig",
    "ToolEndpointConfig",
    "create_production_commands",
]

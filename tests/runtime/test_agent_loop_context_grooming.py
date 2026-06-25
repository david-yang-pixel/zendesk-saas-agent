from __future__ import annotations

import copy
from typing import Any

import pytest

import zd_agent.runtime.agent_loop as agent_loop_module
from zd_agent.config import GuardrailsConfig
from zd_agent.runtime.agent_loop import agent_loop
from zd_agent.runtime.guardrails import Guardrails
from zd_agent.runtime.llm.types import LLMResponse, ToolCallRequest, ToolSchema
from zd_agent.runtime.messages import MessageStore
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolRegistry, ToolResult


class _RecordingProvider:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.fail_first = fail_first
        self.calls: list[list[dict[str, Any]]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        self.calls.append(copy.deepcopy(messages))
        if self.fail_first and len(self.calls) == 1:
            raise TimeoutError("Request timed out.")
        return LLMResponse(
            content="done",
            tool_calls=[],
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=2,
            raw={"role": "assistant", "content": "done"},
        )

    def format_tool_result(
        self, tool_call_id: str, name: str, content: str,
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": content,
        }


class _ScriptedProvider(_RecordingProvider):
    def __init__(self, responses: list[LLMResponse]) -> None:
        super().__init__()
        self._responses = responses

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        self.calls.append(copy.deepcopy(messages))
        return self._responses.pop(0)


class _NoopTool(Tool):
    name = "run_subagents_parallel"
    description = "fake subagent batch"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        return ToolResult(output="subagent evidence summary")


class _FailingSubagentTool(Tool):
    name = "run_subagents_parallel"
    description = "fake failed subagent batch"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        return ToolResult(success=False, error="cumulative subagent limit exceeded")


def _subagent_response(call_id: str) -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[
            ToolCallRequest(
                id=call_id,
                name="run_subagents_parallel",
                arguments='{"tasks":[]}',
            )
        ],
        finish_reason="tool_calls",
        prompt_tokens=10,
        completion_tokens=2,
        raw={
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": "run_subagents_parallel",
                        "arguments": '{"tasks":[]}',
                    },
                }
            ],
        },
    )


def _guardrails() -> Guardrails:
    return Guardrails(
        GuardrailsConfig(
            max_rounds=3,
            timeout_seconds=600,
            max_tokens_per_session=200_000,
            compaction_threshold=0.75,
        )
    )


async def _drain_loop(
    store: MessageStore,
    provider: _RecordingProvider,
    *,
    registry: ToolRegistry | None = None,
) -> None:
    events = [
        event
        async for event in agent_loop(
            AgentSession(ticket_id=2931),
            provider,
            store,
            registry or ToolRegistry(),
            _guardrails(),
            [],
        )
    ]
    assert events[-1].status == "completed"


@pytest.mark.asyncio
async def test_agent_loop_proactively_snips_large_tool_outputs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_loop_module,
        "_PROACTIVE_CONTEXT_SNIP_TOKEN_THRESHOLD",
        1,
        raising=False,
    )
    provider = _RecordingProvider()
    store = MessageStore(provider)
    store.append_system("system")
    store.append_user("hello")
    store.raw.append(
        {
            "role": "tool",
            "tool_call_id": "tool-1",
            "name": "knowledge_search",
            "content": "x" * 1000,
        }
    )

    await _drain_loop(store, provider)

    first_call_tool = next(
        msg for msg in provider.calls[0] if msg.get("name") == "knowledge_search"
    )
    assert len(first_call_tool["content"]) < 300
    assert "[... 输出已精简 ...]" in first_call_tool["content"]


@pytest.mark.asyncio
async def test_agent_loop_grooms_context_before_timeout_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(agent_loop_module.asyncio, "sleep", _no_sleep)
    provider = _RecordingProvider(fail_first=True)
    store = MessageStore(provider)
    store.append_system("system")
    store.append_user(
        [
            {"type": "text", "text": "please inspect this screenshot"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,abc", "detail": "low"},
            },
        ]
    )
    store.raw.append(
        {
            "role": "tool",
            "tool_call_id": "tool-1",
            "name": "knowledge_search",
            "content": "y" * 1000,
        }
    )

    await _drain_loop(store, provider)

    assert len(provider.calls) == 2
    retry_messages = provider.calls[1]
    assert not any(
        isinstance(msg.get("content"), list)
        and any(
            isinstance(part, dict) and part.get("type") == "image_url"
            for part in msg["content"]
        )
        for msg in retry_messages
    )
    retry_tool = next(
        msg for msg in retry_messages if msg.get("name") == "knowledge_search"
    )
    assert "[... 输出已精简 ...]" in retry_tool["content"]


@pytest.mark.asyncio
async def test_agent_loop_uses_context_compaction_target_for_large_message_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, int | None] = {}

    monkeypatch.setattr(
        agent_loop_module,
        "count_tokens",
        lambda messages: 90_000,
    )
    monkeypatch.setattr(
        agent_loop_module,
        "_groom_context_proactively",
        lambda session, message_store, *, message_store_tokens: message_store_tokens,
    )

    async def fake_multi_level_compact(
        messages: list[dict[str, Any]],
        llm_or_provider: _RecordingProvider,
        target_tokens: int | None = None,
    ) -> list[dict[str, Any]]:
        seen["target_tokens"] = target_tokens
        return messages

    monkeypatch.setattr(
        agent_loop_module,
        "multi_level_compact",
        fake_multi_level_compact,
    )

    provider = _RecordingProvider()
    store = MessageStore(provider)
    store.append_system("system")
    store.append_user("hello")

    await _drain_loop(store, provider)

    assert seen["target_tokens"] == 30_000


@pytest.mark.asyncio
async def test_agent_loop_injects_convergence_reminder_after_subagent_batch() -> None:
    provider = _ScriptedProvider([
        _subagent_response("call-subagents"),
        LLMResponse(
            content="done",
            tool_calls=[],
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=2,
            raw={"role": "assistant", "content": "done"},
        ),
    ])
    store = MessageStore(provider)
    store.append_system("system")
    store.append_user("please investigate multiple sources")
    registry = ToolRegistry()
    registry.register(_NoopTool())

    await _drain_loop(store, provider, registry=registry)

    assert len(provider.calls) == 2
    assert not any(
        "并行证据收集已完成" in str(msg.get("content", ""))
        for msg in provider.calls[0]
    )
    assert any(
        "并行证据收集已完成" in str(msg.get("content", ""))
        for msg in provider.calls[1]
    )


@pytest.mark.asyncio
async def test_agent_loop_injects_subagent_convergence_reminder_once() -> None:
    provider = _ScriptedProvider([
        _subagent_response("call-subagents-1"),
        _subagent_response("call-subagents-2"),
        LLMResponse(
            content="done",
            tool_calls=[],
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=2,
            raw={"role": "assistant", "content": "done"},
        ),
    ])
    store = MessageStore(provider)
    store.append_system("system")
    store.append_user("please investigate multiple sources")
    registry = ToolRegistry()
    registry.register(_NoopTool())

    await _drain_loop(store, provider, registry=registry)

    reminder_count = sum(
        "并行证据收集已完成" in str(msg.get("content", ""))
        for msg in store.raw
    )
    assert reminder_count == 1


@pytest.mark.asyncio
async def test_agent_loop_does_not_inject_convergence_reminder_after_failed_subagent_batch() -> None:
    provider = _ScriptedProvider([
        _subagent_response("call-subagents"),
        LLMResponse(
            content="done",
            tool_calls=[],
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=2,
            raw={"role": "assistant", "content": "done"},
        ),
    ])
    store = MessageStore(provider)
    store.append_system("system")
    store.append_user("please investigate multiple sources")
    registry = ToolRegistry()
    registry.register(_FailingSubagentTool())

    await _drain_loop(store, provider, registry=registry)

    assert len(provider.calls) == 2
    assert not any(
        "并行证据收集已完成" in str(msg.get("content", ""))
        for msg in provider.calls[1]
    )

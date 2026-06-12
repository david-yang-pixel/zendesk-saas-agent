from __future__ import annotations

import copy
from typing import Any

import pytest

import zd_agent.runtime.agent_loop as agent_loop_module
from zd_agent.config import GuardrailsConfig
from zd_agent.runtime.agent_loop import agent_loop
from zd_agent.runtime.guardrails import Guardrails
from zd_agent.runtime.llm.types import LLMResponse, ToolSchema
from zd_agent.runtime.messages import MessageStore
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import ToolRegistry


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
) -> None:
    events = [
        event
        async for event in agent_loop(
            AgentSession(ticket_id=2931),
            provider,
            store,
            ToolRegistry(),
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

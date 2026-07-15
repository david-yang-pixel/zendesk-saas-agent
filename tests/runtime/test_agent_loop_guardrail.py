from __future__ import annotations

from typing import Any

import pytest

from zd_agent.config import GuardrailsConfig
from zd_agent.runtime.agent_loop import agent_loop
from zd_agent.runtime.events import LoopFinished
from zd_agent.runtime.guardrails import Guardrails
from zd_agent.runtime.llm.types import (
    LLMProvider,
    LLMResponse,
    ToolCallRequest,
    ToolSchema,
)
from zd_agent.runtime.messages import MessageStore
from zd_agent.session.agent_session import AgentSession, SessionStatus
from zd_agent.tools.base import Tool, ToolRegistry, ToolResult


class _ForcedSummaryProvider(LLMProvider):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="reply-1",
                    name="zendesk_public_reply",
                    arguments='{"ticket_id": 42, "body": "Reply", "reply_type": "ask"}',
                ),
            ],
            finish_reason="tool_calls",
            prompt_tokens=10,
            completion_tokens=2,
            raw={
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "reply-1",
                        "type": "function",
                        "function": {
                            "name": "zendesk_public_reply",
                            "arguments": (
                                '{"ticket_id": 42, "body": "Reply", '
                                '"reply_type": "ask"}'
                            ),
                        },
                    },
                ],
            },
        )

    def format_tool_result(
        self,
        tool_call_id: str,
        name: str,
        content: str,
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": content,
        }


class _FailingPublicReply(Tool):
    name = "zendesk_public_reply"
    description = "fake public reply"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    async def execute(
        self,
        args: dict[str, Any],
        session: AgentSession,
    ) -> ToolResult:
        return ToolResult(success=False, error="browser relay unavailable")


class _MalformedForcedSummaryProvider(_ForcedSummaryProvider):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        response = await super().chat(messages, tools, temperature)
        response.tool_calls[0] = ToolCallRequest(
            id="reply-1",
            name="zendesk_public_reply",
            arguments='{"ticket_id": 42, "body": "truncated',
        )
        return response


class _SuccessfulPublicReply(Tool):
    name = "zendesk_public_reply"
    description = "fake public reply"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(
        self,
        args: dict[str, Any],
        session: AgentSession,
    ) -> ToolResult:
        self.calls.append(args)
        session.reply_sent = True
        return ToolResult(success=True, output="sent")


class _CircuitSummaryProvider(_ForcedSummaryProvider):
    def __init__(self) -> None:
        self.saw_circuit_prompt = False

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        combined = "\n".join(str(message.get("content") or "") for message in messages)
        if "SQL slow-query circuit" not in combined:
            raise AssertionError("circuit must force a summary before another normal round")
        self.saw_circuit_prompt = True
        return await super().chat(messages, tools, temperature)


@pytest.mark.asyncio
async def test_forced_summary_reply_failure_does_not_complete_session() -> None:
    provider = _ForcedSummaryProvider()
    store = MessageStore(provider)
    store.append_system("system")
    store.append_user("investigate ticket")
    registry = ToolRegistry()
    registry.register(_FailingPublicReply())
    session = AgentSession(ticket_id=42)
    guardrails = Guardrails(GuardrailsConfig(max_rounds=0))
    schemas = [
        ToolSchema(
            name="zendesk_public_reply",
            description="fake public reply",
            parameters={"type": "object", "properties": {}},
        ),
    ]

    events = [
        event
        async for event in agent_loop(
            session,
            provider,
            store,
            registry,
            guardrails,
            schemas,
        )
    ]

    assert session.status == SessionStatus.FAILED
    assert session.reply_sent is False
    assert isinstance(events[-1], LoopFinished)
    assert events[-1].status == "guardrail"
    assert "browser relay unavailable" in session.error_message
    combined_messages = "\n".join(
        str(message.get("content") or "") for message in store.raw
    )
    assert (
        '如需 SE/SRE/人工兜底 → 必须调 zendesk_public_reply(reply_type="handoff")'
        in combined_messages
    )


@pytest.mark.asyncio
async def test_malformed_forced_summary_reply_uses_deterministic_handoff() -> None:
    provider = _MalformedForcedSummaryProvider()
    store = MessageStore(provider)
    store.append_system("system")
    store.append_user("investigate ticket")
    registry = ToolRegistry()
    reply_tool = _SuccessfulPublicReply()
    registry.register(reply_tool)
    session = AgentSession(ticket_id=42)
    guardrails = Guardrails(GuardrailsConfig(max_rounds=0))
    schemas = [
        ToolSchema(
            name="zendesk_public_reply",
            description="fake public reply",
            parameters={"type": "object", "properties": {}},
        ),
    ]

    events = [
        event
        async for event in agent_loop(
            session,
            provider,
            store,
            registry,
            guardrails,
            schemas,
        )
    ]

    assert session.status == SessionStatus.COMPLETED
    assert session.reply_sent is True
    assert isinstance(events[-1], LoopFinished)
    assert events[-1].status == "completed"
    assert len(reply_tool.calls) == 1
    [fallback] = reply_tool.calls
    assert fallback["ticket_id"] == 42
    assert fallback["reply_type"] == "handoff"
    assert "technical support" in fallback["body"]
    assert "automated investigation reached its processing limit" in fallback["internal_analysis"]


@pytest.mark.asyncio
async def test_open_slow_sql_circuit_forces_handoff_summary() -> None:
    provider = _CircuitSummaryProvider()
    store = MessageStore(provider)
    store.append_system("system")
    store.append_user("investigate ticket")
    registry = ToolRegistry()
    reply_tool = _SuccessfulPublicReply()
    registry.register(reply_tool)
    session = AgentSession(ticket_id=42)
    session.slow_sql_failure_count = 3
    session.slow_sql_circuit_open = True
    guardrails = Guardrails(GuardrailsConfig(max_rounds=30))
    schemas = [
        ToolSchema(
            name="zendesk_public_reply",
            description="fake public reply",
            parameters={"type": "object", "properties": {}},
        ),
    ]

    events = [
        event
        async for event in agent_loop(
            session,
            provider,
            store,
            registry,
            guardrails,
            schemas,
        )
    ]

    assert provider.saw_circuit_prompt is True
    assert session.status == SessionStatus.COMPLETED
    assert session.reply_sent is True
    assert isinstance(events[-1], LoopFinished)
    assert events[-1].status == "completed"

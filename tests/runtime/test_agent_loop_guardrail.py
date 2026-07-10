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

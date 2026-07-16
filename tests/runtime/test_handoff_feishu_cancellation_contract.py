"""RED contract for terminal ownership in a mixed LLM tool-call response."""

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


class _MixedHandoffAndFeishuProvider(LLMProvider):
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
                    id="handoff-1",
                    name="zendesk_public_reply",
                    arguments=(
                        '{"ticket_id": 42, "body": "A human will follow up.", '
                        '"reply_type": "handoff"}'
                    ),
                ),
                ToolCallRequest(
                    id="feishu-1",
                    name="feishu_push",
                    arguments='{"card_type": "ticket_escalate", "content": "duplicate"}',
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
                        "id": "handoff-1",
                        "type": "function",
                        "function": {
                            "name": "zendesk_public_reply",
                            "arguments": (
                                '{"ticket_id": 42, "body": "A human will follow up.", '
                                '"reply_type": "handoff"}'
                            ),
                        },
                    },
                    {
                        "id": "feishu-1",
                        "type": "function",
                        "function": {
                            "name": "feishu_push",
                            "arguments": (
                                '{"card_type": "ticket_escalate", "content": "duplicate"}'
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


class _DeliveredHandoffReply(Tool):
    name = "zendesk_public_reply"
    description = "fake public reply"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    async def execute(
        self,
        args: dict[str, Any],
        session: AgentSession,
    ) -> ToolResult:
        assert args["reply_type"] == "handoff"
        session.reply_sent = True
        session.public_reply_type = "handoff"
        return ToolResult(success=True, output="handoff delivered")


class _RecordingFeishuPush(Tool):
    name = "feishu_push"
    description = "fake Feishu push"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(
        self,
        args: dict[str, Any],
        session: AgentSession,
    ) -> ToolResult:
        self.calls.append(args)
        return ToolResult(success=True, output="Feishu push sent")


@pytest.mark.asyncio
async def test_handoff_cancels_same_response_feishu_push_and_keeps_transcript_valid() -> None:
    """A handoff owns escalation, so the model's extra Feishu call is cancelled."""
    provider = _MixedHandoffAndFeishuProvider()
    store = MessageStore(provider)
    store.append_system("system")
    store.append_user("investigate ticket")
    registry = ToolRegistry()
    registry.register(_DeliveredHandoffReply())
    feishu_push = _RecordingFeishuPush()
    registry.register(feishu_push)
    session = AgentSession(ticket_id=42)
    guardrails = Guardrails(GuardrailsConfig(max_rounds=3))
    schemas = [
        ToolSchema(
            name="zendesk_public_reply",
            description="fake public reply",
            parameters={"type": "object", "properties": {}},
        ),
        ToolSchema(
            name="feishu_push",
            description="fake Feishu push",
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

    assert session.status is SessionStatus.COMPLETED
    assert feishu_push.calls == []
    assert isinstance(events[-1], LoopFinished)
    assert events[-1].reason == "customer-visible reply delivered"
    cancelled_result = next(
        message
        for message in store.raw
        if message.get("role") == "tool"
        and message.get("tool_call_id") == "feishu-1"
    )
    assert cancelled_result["name"] == "feishu_push"
    assert "Cancelled" in cancelled_result["content"]
    assert "handoff" in cancelled_result["content"]

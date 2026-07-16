"""RED contracts for public-reply terminal behavior.

A customer-visible write is the runtime's terminal side effect.  Freshness
rebases must invalidate every later write proposed from the same assistant
response, while a successfully delivered reply must end the loop and win over
any later in-process failure status when trigger state is finalized.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from zd_agent.config import GuardrailsConfig
from zd_agent.runtime.agent_loop import agent_loop
from zd_agent.runtime.events import LoopFinished, ToolExecuted
from zd_agent.runtime.guardrails import Guardrails
from zd_agent.runtime.llm.types import (
    LLMProvider,
    LLMResponse,
    ToolCallRequest,
    ToolSchema,
)
from zd_agent.runtime.messages import MessageStore
from zd_agent.runtime.pre_send_check import (
    FreshnessRecovery,
    FreshnessRecoveryAction,
)
from zd_agent.services.trigger_state_machine import apply_session_finished
from zd_agent.session.agent_session import AgentSession, SessionStatus
from zd_agent.tools.base import Tool, ToolRegistry, ToolResult
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


def _tool_response(*calls: tuple[str, str]) -> LLMResponse:
    requests = [
        ToolCallRequest(
            id=call_id,
            name="zendesk_public_reply",
            arguments=json.dumps(
                {
                    "ticket_id": 3461,
                    "body": body,
                    "reply_type": "solution",
                },
            ),
        )
        for call_id, body in calls
    ]
    return LLMResponse(
        content=None,
        tool_calls=requests,
        finish_reason="tool_calls",
        prompt_tokens=10,
        completion_tokens=2,
        raw={
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": request.id,
                    "type": "function",
                    "function": {
                        "name": request.name,
                        "arguments": request.arguments,
                    },
                }
                for request in requests
            ],
        },
    )


def _final_response() -> LLMResponse:
    return LLMResponse(
        content="Done.",
        tool_calls=[],
        finish_reason="stop",
        prompt_tokens=2,
        completion_tokens=1,
        raw={"role": "assistant", "content": "Done."},
    )


class _ScriptedProvider(LLMProvider):
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = responses
        self.chat_count = 0

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        del messages, tools, temperature
        self.chat_count += 1
        if self.chat_count > len(self.responses):
            raise AssertionError("agent loop requested an unexpected extra LLM round")
        return self.responses[self.chat_count - 1]

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


class _RebaseOnFirstAttemptThenSend(Tool):
    name = "zendesk_public_reply"
    description = "fake customer-visible reply"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    concurrency_safe = False

    def __init__(self) -> None:
        self.attempted_bodies: list[str] = []
        self.sent_bodies: list[str] = []

    async def execute(
        self,
        args: dict[str, Any],
        session: AgentSession,
    ) -> ToolResult:
        body = str(args["body"])
        self.attempted_bodies.append(body)
        if len(self.attempted_bodies) == 1:
            return ToolResult(
                success=False,
                error="stale customer context",
                metadata={
                    "freshness_recovery": FreshnessRecovery(
                        action=FreshnessRecoveryAction.REFRESH_CONTEXT,
                        new_messages=(
                            {
                                "id": "m2",
                                "created_at": "2026-07-16T05:01:00Z",
                                "body": "Customer supplied new reproduction details.",
                                "public": True,
                                "image_urls": [],
                                "content_type": "text",
                            },
                        ),
                        next_snapshot_count=2,
                        next_snapshot_last_id="m2",
                        reason="new substantive customer message",
                    ),
                },
            )

        self.sent_bodies.append(body)
        session.reply_sent = True
        return ToolResult(success=True, output="public reply sent")


class _SuccessfulPublicReply(Tool):
    name = "zendesk_public_reply"
    description = "fake customer-visible reply"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    concurrency_safe = False

    def __init__(self) -> None:
        self.calls = 0

    async def execute(
        self,
        args: dict[str, Any],
        session: AgentSession,
    ) -> ToolResult:
        del args
        self.calls += 1
        session.reply_sent = True
        return ToolResult(success=True, output="public reply sent")


class _DeliveredReplyWithBookkeepingFailure(Tool):
    name = "zendesk_public_reply"
    description = "fake delivered reply with failed bookkeeping"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    concurrency_safe = False

    async def execute(
        self,
        args: dict[str, Any],
        session: AgentSession,
    ) -> ToolResult:
        del args
        # The customer-visible side effect already happened. A later internal
        # note/outbox failure must not make the runtime ask the model again.
        session.reply_sent = True
        return ToolResult(
            success=False,
            error="post-send internal-note bookkeeping failed",
        )


def _runtime(
    provider: LLMProvider,
    reply_tool: Tool,
) -> tuple[MessageStore, ToolRegistry, list[ToolSchema], Guardrails]:
    message_store = MessageStore(provider)
    message_store.append_system("system")
    message_store.append_user("investigate ticket")
    registry = ToolRegistry()
    registry.register(reply_tool)
    schemas = [
        ToolSchema(
            name=reply_tool.name,
            description=reply_tool.description,
            parameters=reply_tool.parameters,
        ),
    ]
    guardrails = Guardrails(
        GuardrailsConfig(
            max_rounds=5,
            timeout_seconds=30,
            max_tokens_per_session=10_000,
        ),
    )
    return message_store, registry, schemas, guardrails


@pytest.mark.asyncio
async def test_freshness_rebase_cancels_remaining_writes_from_same_response() -> None:
    """A second public write proposed from stale context must never execute."""
    provider = _ScriptedProvider(
        [
            _tool_response(
                ("reply-stale-1", "draft-before-update-A"),
                ("reply-stale-2", "draft-before-update-B"),
            ),
            _tool_response(("reply-fresh", "regenerated-after-update")),
            _final_response(),
        ],
    )
    reply_tool = _RebaseOnFirstAttemptThenSend()
    message_store, registry, schemas, guardrails = _runtime(provider, reply_tool)
    session = AgentSession(ticket_id=3461, trigger_type="production")
    session.customer_msgs_snapshot_captured = True
    session.customer_msgs_snapshot_count = 1
    session.customer_msgs_snapshot_last_id = "m1"

    events = [
        event
        async for event in agent_loop(
            session,
            provider,
            message_store,
            registry,
            guardrails,
            schemas,
        )
    ]

    assert reply_tool.attempted_bodies == [
        "draft-before-update-A",
        "regenerated-after-update",
    ]
    assert reply_tool.sent_bodies == ["regenerated-after-update"]
    assert session.customer_msgs_snapshot_last_id == "m2"
    assert session.reply_sent is True
    assert provider.chat_count == 2
    assert isinstance(events[-1], LoopFinished)
    assert events[-1].status == "completed"

    first_result_index = next(
        index
        for index, message in enumerate(message_store.raw)
        if message.get("tool_call_id") == "reply-stale-1"
    )
    cancelled_result_index = next(
        index
        for index, message in enumerate(message_store.raw)
        if message.get("tool_call_id") == "reply-stale-2"
    )
    refreshed_context_index = next(
        index
        for index, message in enumerate(message_store.raw)
        if message.get("role") == "user"
        and "Customer supplied new reproduction details."
        in str(message.get("content"))
    )
    assert first_result_index < cancelled_result_index < refreshed_context_index, (
        "all results for one assistant tool-call batch must remain contiguous "
        "before freshness context is injected"
    )


@pytest.mark.asyncio
async def test_successful_public_reply_ends_loop_before_another_llm_round() -> None:
    """Nothing after a successful customer-visible reply may fail the session."""
    provider = _ScriptedProvider(
        [_tool_response(("reply-success", "verified customer answer"))],
    )
    reply_tool = _SuccessfulPublicReply()
    message_store, registry, schemas, guardrails = _runtime(provider, reply_tool)
    session = AgentSession(ticket_id=3461, trigger_type="production")

    events = [
        event
        async for event in agent_loop(
            session,
            provider,
            message_store,
            registry,
            guardrails,
            schemas,
        )
    ]

    assert provider.chat_count == 1
    assert reply_tool.calls == 1
    assert session.reply_sent is True
    assert session.status is SessionStatus.COMPLETED
    assert isinstance(events[-1], LoopFinished)
    assert events[-1].status == "completed"
    reply_events = [
        event
        for event in events
        if isinstance(event, ToolExecuted)
        and event.tool_name == "zendesk_public_reply"
    ]
    assert len(reply_events) == 1
    assert reply_events[0].success is True


@pytest.mark.asyncio
async def test_delivered_public_reply_ends_loop_even_when_tool_reports_failure() -> None:
    """Confirmed customer delivery wins over post-send bookkeeping failure."""
    provider = _ScriptedProvider(
        [_tool_response(("reply-delivered", "verified customer answer"))],
    )
    message_store, registry, schemas, guardrails = _runtime(
        provider,
        _DeliveredReplyWithBookkeepingFailure(),
    )
    session = AgentSession(ticket_id=3461, trigger_type="production")

    events = [
        event
        async for event in agent_loop(
            session,
            provider,
            message_store,
            registry,
            guardrails,
            schemas,
        )
    ]

    assert provider.chat_count == 1
    assert session.reply_sent is True
    assert session.status is SessionStatus.COMPLETED
    assert isinstance(events[-1], LoopFinished)
    assert events[-1].status == "completed"
    reply_events = [
        event
        for event in events
        if isinstance(event, ToolExecuted)
        and event.tool_name == "zendesk_public_reply"
    ]
    assert len(reply_events) == 1
    assert reply_events[0].success is False


@pytest.mark.parametrize("terminal_status", [SessionStatus.FAILED, SessionStatus.TIMEOUT])
def test_delivered_reply_marks_processing_ticket_processed_even_if_run_fails_later(
    tmp_path,
    terminal_status: SessionStatus,
) -> None:
    """Durable delivery wins over a later runtime failure or timeout status."""
    now = datetime(2026, 7, 16, 5, 30, tzinfo=timezone.utc)
    tracker = TicketStateTracker(tmp_path / "tickets.json")
    session = AgentSession(ticket_id=3461, trigger_type="production")
    tracker.set(
        3461,
        TicketState(
            state="processing",
            first_seen=now - timedelta(minutes=5),
            active_session_id=session.session_id,
        ),
    )
    session.reply_sent = True
    session.customer_msgs_snapshot_captured = True
    session.customer_msgs_snapshot_count = 2
    session.customer_msgs_snapshot_last_id = "customer-message-2"
    session.status = terminal_status

    changed = apply_session_finished(tracker, session, now=now)

    persisted = TicketStateTracker(tmp_path / "tickets.json").get(3461)
    assert changed is True
    assert persisted is not None
    assert persisted.state == "processed"
    assert persisted.active_session_id == ""
    assert persisted.last_agent_finish_ts == now
    assert persisted.last_customer_comment_id == "customer-message-2"

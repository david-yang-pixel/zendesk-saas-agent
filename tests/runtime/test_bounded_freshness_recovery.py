"""RED specifications for bounding mid-session freshness rebases.

The agent may absorb at most two distinct customer updates while one production
run is active.  If a third update arrives, the current run must stop as a typed
deferred outcome and let the trigger state machine schedule a fresh run.  It
must not keep regenerating replies or fall through to a failed/completed state.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from zd_agent.config import GuardrailsConfig
from zd_agent.runtime.agent_loop import agent_loop
from zd_agent.runtime.events import LoopFinished, ToolExecuted
from zd_agent.runtime.guardrails import Guardrails
from zd_agent.runtime.llm.types import LLMResponse, ToolCallRequest, ToolSchema
from zd_agent.runtime.messages import MessageStore
from zd_agent.runtime.pre_send_check import pre_send_check_or_abort
from zd_agent.services.trigger_state_machine import (
    apply_session_finished,
    apply_silence_window_expired,
)
from zd_agent.session.agent_session import AgentSession, SessionStatus
from zd_agent.tools.base import Tool, ToolRegistry, ToolResult
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


_WORKSPACE_ID = "86628345-731d-4475-b681-5006f48a2287"


def _customer_message(number: int) -> dict[str, Any]:
    return {
        "id": f"m{number}",
        "created_at": f"2026-07-16T04:{14 + number:02d}:00Z",
        "body": (
            "Initial customer report."
            if number == 1
            else f"Customer update {number}: workspace ID {_WORKSPACE_ID}"
        ),
        "public": True,
        "image_urls": [],
        "content_type": "text",
    }


class _OneNewCustomerUpdatePerAttempt(Tool):
    """Freshness-aware public-reply fake with no external side effects."""

    name = "zendesk_public_reply"
    description = "send a customer-visible reply"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    concurrency_safe = False

    def __init__(self) -> None:
        self.current_messages = [_customer_message(1)]
        self.attempt_count = 0
        self.actual_send_count = 0
        self.snapshot_ids_seen: list[str] = []

    async def execute(
        self,
        args: dict[str, Any],
        session: AgentSession,
    ) -> ToolResult:
        self.attempt_count += 1
        self.snapshot_ids_seen.append(session.customer_msgs_snapshot_last_id)
        self.current_messages.append(
            _customer_message(len(self.current_messages) + 1),
        )

        async def fetch_messages() -> list[dict[str, Any]]:
            return copy.deepcopy(self.current_messages)

        check = await pre_send_check_or_abort(
            ticket_id=session.ticket_id,
            snapshot_count=session.customer_msgs_snapshot_count,
            snapshot_last_id=session.customer_msgs_snapshot_last_id or None,
            fetch_messages_fn=fetch_messages,
            skip_llm=True,
        )
        if not check.continue_send:
            return ToolResult(
                success=False,
                error="stale customer context",
                metadata={"freshness_recovery": check.recovery},
            )

        self.actual_send_count += 1
        session.reply_sent = True
        return ToolResult(success=True, output="public reply sent")


class _AlwaysRetryPublicReplyProvider:
    """Requests one reply per round and records if the loop asks a fourth time."""

    def __init__(self) -> None:
        self.chat_count = 0

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        self.chat_count += 1
        if self.chat_count > 3:
            return LLMResponse(
                content="The loop should have deferred before this fourth round.",
                tool_calls=[],
                finish_reason="stop",
                prompt_tokens=5,
                completion_tokens=1,
                raw={
                    "role": "assistant",
                    "content": "The loop should have deferred before this fourth round.",
                },
            )

        call_id = f"reply-{self.chat_count}"
        arguments = (
            '{"ticket_id": 3461, "body": "Current answer", '
            '"reply_type": "solution"}'
        )
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id=call_id,
                    name="zendesk_public_reply",
                    arguments=arguments,
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
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": "zendesk_public_reply",
                            "arguments": arguments,
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


@pytest.mark.asyncio
async def test_third_distinct_freshness_update_defers_current_run() -> None:
    """Two rebases are allowed; a third stale cursor must end the run."""
    session = AgentSession(ticket_id=3461, trigger_type="production")
    session.customer_msgs_snapshot_captured = True
    session.customer_msgs_snapshot_count = 1
    session.customer_msgs_snapshot_last_id = "m1"

    provider = _AlwaysRetryPublicReplyProvider()
    message_store = MessageStore(provider)
    message_store.append_system("system")
    message_store.append_user(_customer_message(1)["body"])

    reply_tool = _OneNewCustomerUpdatePerAttempt()
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
            max_rounds=6,
            timeout_seconds=30,
            max_tokens_per_session=10_000,
        ),
    )

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

    assert session.status.value == "deferred"
    assert SessionStatus.__members__["DEFERRED"] is session.status
    assert session.reply_sent is False
    assert getattr(session, "freshness_rebase_count", None) == 2

    # The third update (m4) belongs to the next run; this run must keep m3 as
    # its committed cursor and must not ask the model/tool for another reply.
    assert session.customer_msgs_snapshot_count == 3
    assert session.customer_msgs_snapshot_last_id == "m3"
    assert provider.chat_count == 3
    assert reply_tool.attempt_count == 3
    assert reply_tool.actual_send_count == 0
    assert reply_tool.snapshot_ids_seen == ["m1", "m2", "m3"]

    reply_events = [
        event
        for event in events
        if isinstance(event, ToolExecuted)
        and event.tool_name == "zendesk_public_reply"
    ]
    assert len(reply_events) == 3
    assert all(event.success is False for event in reply_events)

    assert isinstance(events[-1], LoopFinished)
    assert events[-1].status == "deferred"
    assert events[-1].reason == "freshness_rebase_limit"


def test_freshness_deferred_session_schedules_retry_after_60_seconds(
    tmp_path,
) -> None:
    """A deferred production run must not be recorded as failed or completed."""
    deferred_status = SessionStatus.__members__.get("DEFERRED")
    assert deferred_status is not None, (
        "freshness exhaustion needs a typed SessionStatus.DEFERRED outcome"
    )

    now = datetime(2026, 7, 16, 4, 30, tzinfo=timezone.utc)
    tracker = TicketStateTracker(tmp_path / "tickets.json")
    tracker.set(
        3461,
        TicketState(
            state="processing",
            first_seen=now - timedelta(minutes=10),
            last_customer_comment_id="m3",
            last_comment_ts=now - timedelta(seconds=5),
            last_verdict="substantial_issue",
        ),
    )
    session = AgentSession(ticket_id=3461, trigger_type="production")
    session.status = deferred_status
    session.reply_sent = False

    changed = apply_session_finished(tracker, session, now=now)

    record = tracker.get(3461)
    assert changed is True
    assert record is not None
    assert record.state == "in_silence_window"
    assert record.silence_window_end == now + timedelta(seconds=60)
    assert record.last_agent_finish_ts == now
    assert record.last_customer_comment_id == "m3"
    assert record.last_verdict == "substantial_issue"

    # The silence waker must not be able to re-dispatch immediately and create
    # another overlapping run for the same burst of customer messages.
    transition = apply_silence_window_expired(
        tracker,
        3461,
        now=now + timedelta(seconds=59),
    )
    assert transition is None
    assert tracker.get(3461).state == "in_silence_window"

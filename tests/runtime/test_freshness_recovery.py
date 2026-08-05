"""Regression specifications for stale-context recovery (#3461).

These tests intentionally describe the recovery contract that is missing today:

1. A late Zendesk Messaging ``formResponse`` remains a typed customer message.
2. The freshness guard returns the exact new messages and the cursor that may be
   committed after those messages enter the agent context.
3. The agent loop injects that context before advancing the cursor, so the same
   stale cursor cannot abort the same reply indefinitely.
"""
from __future__ import annotations

import asyncio
import copy
from dataclasses import is_dataclass
from typing import Any

import pytest

from zd_agent.config import AppConfig, GuardrailsConfig, PublicReplyConfig
from zd_agent.runtime.agent_loop import agent_loop
from zd_agent.runtime.guardrails import Guardrails
from zd_agent.runtime.llm.types import LLMResponse, ToolCallRequest, ToolSchema
from zd_agent.runtime.messages import MessageStore
from zd_agent.runtime.pre_send_check import pre_send_check_or_abort
from zd_agent.services.ticket_message_parsing import _messaging_customer_messages
from zd_agent.session.agent_session import AgentSession, SessionStatus
from zd_agent.tools.base import Tool, ToolRegistry, ToolResult
from zd_agent.tools.zendesk_public_reply import ZendeskPublicReply


_WORKSPACE_ID = "86628345-731d-4475-b681-5006f48a2287"


def _form_response_event() -> dict[str, Any]:
    return {
        "id": "m2",
        "created_at": "2026-07-16T04:18:53Z",
        "author": {
            "type": "end-user",
            "zen:support:user_id": 24680,
        },
        "content": {
            "type": "formResponse",
            "fields": [
                {
                    "type": "TEXT",
                    "name": "dataCapture.systemField.requester.name",
                    "label": "Name",
                    "text": "Ellie",
                },
                {
                    "type": "EMAIL",
                    "name": "dataCapture.systemField.requester.email",
                    "label": "Email",
                    "email": "ellie@example.test",
                },
                {
                    "type": "TEXT",
                    "name": "dataCapture.ticketField.41021162892948",
                    "label": "Workspace ID",
                    "text": _WORKSPACE_ID,
                },
            ],
        },
    }


def _customer_messages_with_late_form() -> list[dict[str, Any]]:
    initial = {
        "id": "m1",
        "created_at": "2026-07-16T04:15:22Z",
        "body": "I cannot view or update our knowledge documents.",
        "public": True,
        "image_urls": [],
        "content_type": "text",
    }
    [form_response] = _messaging_customer_messages([_form_response_event()])
    return [initial, form_response]


def _action_value(action: object) -> object:
    """Accept an enum-backed action while keeping the assertion implementation-agnostic."""
    return getattr(action, "value", action)


def test_messaging_form_response_retains_its_content_type() -> None:
    """Recovery context must distinguish a form completion from free-form text."""
    [message] = _messaging_customer_messages([_form_response_event()])

    assert message["content_type"] == "formResponse"
    assert "ellie@example.test" in message["body"]
    assert _WORKSPACE_ID in message["body"]


@pytest.mark.asyncio
async def test_stale_form_response_returns_typed_recovery_with_next_cursor() -> None:
    """A stale check must carry everything needed for one deterministic recovery."""
    current = _customer_messages_with_late_form()

    async def fetch_messages() -> list[dict[str, Any]]:
        return copy.deepcopy(current)

    result = await pre_send_check_or_abort(
        ticket_id=3461,
        snapshot_count=1,
        snapshot_last_id="m1",
        fetch_messages_fn=fetch_messages,
        skip_llm=True,
    )

    assert result.continue_send is False
    recovery = getattr(result, "recovery", None)
    assert recovery is not None, (
        "a stale result must expose typed recovery data, not only a prose error"
    )
    assert is_dataclass(recovery), "freshness recovery must be a typed value object"
    assert _action_value(recovery.action) == "refresh_context"
    assert list(recovery.new_messages) == [current[1]]
    assert recovery.next_snapshot_count == 2
    assert recovery.next_snapshot_last_id == "m2"


@pytest.mark.asyncio
async def test_public_reply_propagates_freshness_recovery_to_agent_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real public-reply tool must not collapse recovery into prose."""
    current = _customer_messages_with_late_form()
    config = AppConfig(
        public_reply=PublicReplyConfig(
            enabled=True,
            browser_relay_url="http://relay.test",
        )
    )
    monkeypatch.setattr(
        "zd_agent.tools.zendesk_public_reply.get_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "zd_agent.tools.zendesk_public_reply.public_reply_privacy_blockers",
        lambda _body: [],
    )
    monkeypatch.setattr(
        "zd_agent.tools.zendesk_public_reply.public_reply_evidence_blockers",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        "zd_agent.tools.zendesk_public_reply.refund_owner_gate_block_reason",
        lambda **_kwargs: None,
    )

    async def fetch_messages(
        _ticket_id: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return copy.deepcopy(current), {"_zd_agent_conversation_events": []}

    monkeypatch.setattr(
        "zd_agent.services.ticket_messages._fetch_customer_messages",
        fetch_messages,
    )

    session = AgentSession(ticket_id=3461)
    session.customer_msgs_snapshot_captured = True
    session.customer_msgs_snapshot_count = 1
    session.customer_msgs_snapshot_last_id = "m1"
    result = await ZendeskPublicReply().execute(
        {
            "ticket_id": 3461,
            "body": "Thank you. I will re-evaluate the new details.",
            "reply_type": "ask",
            "internal_analysis": "Exercise freshness recovery after a new customer message.",
            "conversation_id": "conversation-3461",
        },
        session,
    )

    assert result.success is False
    recovery = result.metadata.get("freshness_recovery")
    assert recovery is not None
    assert is_dataclass(recovery)
    assert _action_value(recovery.action) == "refresh_context"
    assert list(recovery.new_messages) == [current[1]]


class _FreshnessAwareReply(Tool):
    """Small real freshness adapter used to exercise the agent-loop boundary."""

    name = "zendesk_public_reply"
    description = "send a customer-visible reply"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    concurrency_safe = False

    def __init__(self, current_messages: list[dict[str, Any]]) -> None:
        self.current_messages = current_messages
        self.calls = 0

    async def execute(
        self,
        args: dict[str, Any],
        session: AgentSession,
    ) -> ToolResult:
        self.calls += 1

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
                metadata={"freshness_recovery": getattr(check, "recovery", None)},
            )

        session.reply_sent = True
        return ToolResult(success=True, output="public reply sent")


class _RetryReplyAfterRecoveryProvider:
    """Retry once, but only after the late form is visible to the model."""

    def __init__(self, session: AgentSession) -> None:
        self.session = session
        self.calls: list[list[dict[str, Any]]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        self.calls.append(copy.deepcopy(messages))
        call_number = len(self.calls)

        if call_number == 2:
            combined_context = "\n".join(
                str(message.get("content") or "") for message in messages
            )
            assert self.session.customer_msgs_snapshot_last_id == "m2"
            assert self.session.customer_msgs_snapshot_count == 2
            assert _WORKSPACE_ID in combined_context, (
                "the cursor must not advance before the new formResponse is injected"
            )
            assert "formResponse" in combined_context
            assert self.session.customer_form_data == {
                "name": "Ellie",
                "email": "ellie@example.test",
                "workspace_id": _WORKSPACE_ID,
            }

        if call_number <= 2:
            call_id = f"reply-{call_number}"
            arguments = (
                '{"ticket_id": 3461, "body": "Updated reply", '
                '"reply_type": "solution"}'
            )
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id=call_id,
                        name="zendesk_public_reply",
                        arguments=arguments,
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
                                "name": "zendesk_public_reply",
                                "arguments": arguments,
                            },
                        }
                    ],
                },
            )

        return LLMResponse(
            content="done",
            tool_calls=[],
            finish_reason="stop",
            prompt_tokens=5,
            completion_tokens=1,
            raw={"role": "assistant", "content": "done"},
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
async def test_agent_loop_injects_then_advances_cursor_and_aborts_only_once() -> None:
    """The #3461 cursor may abort once, then the same reply path must make progress."""
    current = _customer_messages_with_late_form()
    session = AgentSession(ticket_id=3461)
    session.customer_msgs_snapshot_captured = True
    session.customer_msgs_snapshot_count = 1
    session.customer_msgs_snapshot_last_id = "m1"

    provider = _RetryReplyAfterRecoveryProvider(session)
    message_store = MessageStore(provider)
    message_store.append_system("system")
    message_store.append_user(current[0]["body"])

    reply_tool = _FreshnessAwareReply(current)
    registry = ToolRegistry()
    registry.register(reply_tool)
    schemas = [
        ToolSchema(
            name=reply_tool.name,
            description=reply_tool.description,
            parameters=reply_tool.parameters,
        )
    ]
    guardrails = Guardrails(
        GuardrailsConfig(
            max_rounds=4,
            timeout_seconds=30,
            max_tokens_per_session=10_000,
        )
    )

    async def drain_loop() -> list[Any]:
        return [
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

    # A regression must fail quickly instead of consuming all 30 production rounds.
    events = await asyncio.wait_for(drain_loop(), timeout=5)

    assert session.status is SessionStatus.COMPLETED
    assert session.reply_sent is True
    assert reply_tool.calls == 2, "the same stale cursor must not abort repeatedly"
    assert events[-1].status == "completed"

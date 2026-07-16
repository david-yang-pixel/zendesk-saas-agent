"""RED specifications for non-recoverable freshness edges and late images.

These cases deliberately cross the public-reply/agent-loop boundary.  A
freshness failure must not be left as prose for the model to retry until a
guardrail fires, and a late customer image must become real multimodal model
input before its cursor is committed.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Awaitable, Callable

import pytest

from zd_agent.config import AppConfig, GuardrailsConfig, PublicReplyConfig
from zd_agent.runtime.agent_loop import (
    MAX_IMAGES,
    _apply_freshness_recovery,
    agent_loop,
)
from zd_agent.runtime.events import LoopFinished, ToolExecuted
from zd_agent.runtime.guardrails import Guardrails
from zd_agent.runtime.llm.types import LLMResponse, ToolCallRequest, ToolSchema
from zd_agent.runtime.messages import MessageStore
from zd_agent.runtime.pre_send_check import (
    FreshnessRecovery,
    FreshnessRecoveryAction,
    pre_send_check_or_abort,
)
from zd_agent.session.agent_session import AgentSession, SessionStatus
from zd_agent.tools.base import Tool, ToolRegistry, ToolResult
from zd_agent.tools.zendesk_public_reply import ZendeskPublicReply


_REMOTE_IMAGE_URL = "https://example.zendesk.com/attachments/late-error.png"
_IMAGE_DATA_URL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB"


class _AlwaysRequestSolutionProvider:
    """Keep asking for the same public reply until runtime terminates the run."""

    def __init__(self, ticket_id: int) -> None:
        self.ticket_id = ticket_id
        self.chat_count = 0

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        self.chat_count += 1
        call_id = f"reply-{self.chat_count}"
        arguments = json.dumps(
            {
                "ticket_id": self.ticket_id,
                "body": "Thank you. I reviewed the available details.",
                "reply_type": "solution",
                "conversation_id": f"conversation-{self.ticket_id}",
            }
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


def _register(tool: Tool) -> tuple[ToolRegistry, list[ToolSchema]]:
    registry = ToolRegistry()
    registry.register(tool)
    schemas = [
        ToolSchema(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
        )
    ]
    return registry, schemas


def _patch_public_reply_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
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


async def _run_repeating_public_reply(
    *,
    monkeypatch: pytest.MonkeyPatch,
    fetch_messages: Callable[[], Awaitable[list[dict[str, Any]]]],
) -> tuple[AgentSession, _AlwaysRequestSolutionProvider, list[Any], int]:
    """Run the real public-reply tool while replacing only Zendesk reads."""
    _patch_public_reply_boundaries(monkeypatch)
    fetch_count = 0

    async def fetch_for_ticket(
        _ticket_id: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        nonlocal fetch_count
        fetch_count += 1
        return (
            await fetch_messages(),
            {"_zd_agent_conversation_events": []},
        )

    monkeypatch.setattr(
        "zd_agent.services.ticket_messages._fetch_customer_messages",
        fetch_for_ticket,
    )

    session = AgentSession(ticket_id=3461, trigger_type="production")
    session.customer_msgs_snapshot_captured = True
    session.customer_msgs_snapshot_count = 1
    session.customer_msgs_snapshot_last_id = "m1"

    provider = _AlwaysRequestSolutionProvider(session.ticket_id)
    message_store = MessageStore(provider)
    message_store.append_system("system")
    message_store.append_user("Initial customer report")
    registry, schemas = _register(ZendeskPublicReply())
    guardrails = Guardrails(
        GuardrailsConfig(
            max_rounds=4,
            timeout_seconds=30,
            max_tokens_per_session=10_000,
        )
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
    return session, provider, events, fetch_count


@pytest.mark.asyncio
async def test_missing_snapshot_anchor_defers_without_entering_guardrail_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absent immutable anchor cannot be repaired by regenerating a reply."""

    async def fetch_messages() -> list[dict[str, Any]]:
        return [
            {
                "id": "m2",
                "body": "The current Zendesk history no longer contains m1.",
                "image_urls": [],
            }
        ]

    session, provider, events, fetch_count = await _run_repeating_public_reply(
        monkeypatch=monkeypatch,
        fetch_messages=fetch_messages,
    )

    assert session.status is SessionStatus.DEFERRED
    assert provider.chat_count == 1
    assert fetch_count == 1
    tool_events = [event for event in events if isinstance(event, ToolExecuted)]
    assert len(tool_events) == 1
    assert isinstance(events[-1], LoopFinished)
    assert events[-1].status == "deferred"
    assert "snapshot_last_id_missing" in events[-1].reason
    assert "guardrail" not in events[-1].reason.lower()


@pytest.mark.asyncio
async def test_repeated_freshness_fetch_error_defers_after_one_bounded_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second unavailable read is a typed defer, not an LLM retry prompt."""

    async def fetch_messages() -> list[dict[str, Any]]:
        raise RuntimeError("Zendesk MCP temporarily unavailable")

    session, provider, events, fetch_count = await _run_repeating_public_reply(
        monkeypatch=monkeypatch,
        fetch_messages=fetch_messages,
    )

    assert session.status is SessionStatus.DEFERRED
    assert provider.chat_count == 2
    assert fetch_count == 2
    tool_events = [event for event in events if isinstance(event, ToolExecuted)]
    assert len(tool_events) == 2
    assert isinstance(events[-1], LoopFinished)
    assert events[-1].status == "deferred"
    assert "fetch_error" in events[-1].reason
    assert "guardrail" not in events[-1].reason.lower()


class _LateImageFreshnessReply(Tool):
    """Use the real freshness checker but avoid any customer-visible write."""

    name = "zendesk_public_reply"
    description = "send a customer-visible reply"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    concurrency_safe = False

    def __init__(self) -> None:
        self.calls = 0
        self.current_messages = [
            {
                "id": "m1",
                "body": "Initial customer report",
                "image_urls": [],
                "content_type": "text",
            },
            {
                "id": "m2",
                "body": "This screenshot shows the exact error.",
                "image_urls": [_REMOTE_IMAGE_URL],
                "content_type": "image",
            },
        ]

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
                error=check.reason,
                metadata={"freshness_recovery": check.recovery},
            )

        session.reply_sent = True
        return ToolResult(success=True, output="public reply sent")


class _InspectLateImageProvider(_AlwaysRequestSolutionProvider):
    def __init__(self, session: AgentSession) -> None:
        super().__init__(session.ticket_id)
        self.session = session
        self.messages_by_call: list[list[dict[str, Any]]] = []
        self.cursor_by_call: list[str] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        self.messages_by_call.append(copy.deepcopy(messages))
        self.cursor_by_call.append(self.session.customer_msgs_snapshot_last_id)
        if len(self.messages_by_call) <= 2:
            return await super().chat(messages, tools, temperature)

        self.chat_count += 1
        return LLMResponse(
            content="done",
            tool_calls=[],
            finish_reason="stop",
            prompt_tokens=5,
            completion_tokens=1,
            raw={"role": "assistant", "content": "done"},
        )


@pytest.mark.asyncio
async def test_late_freshness_image_is_downloaded_and_injected_before_cursor_advance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The model must receive image bytes, not a private Zendesk URL in JSON."""
    session = AgentSession(ticket_id=3461, trigger_type="production")
    session.customer_msgs_snapshot_captured = True
    session.customer_msgs_snapshot_count = 1
    session.customer_msgs_snapshot_last_id = "m1"

    cursor_during_download: list[tuple[int, str]] = []
    downloaded_urls: list[str] = []

    async def download_image(url: str) -> str:
        downloaded_urls.append(url)
        cursor_during_download.append(
            (
                session.customer_msgs_snapshot_count,
                session.customer_msgs_snapshot_last_id,
            )
        )
        return _IMAGE_DATA_URL

    monkeypatch.setattr(
        "zd_agent.services.ticket_messages._download_image_data_url",
        download_image,
    )

    provider = _InspectLateImageProvider(session)
    message_store = MessageStore(provider)
    message_store.append_system("system")
    message_store.append_user("Initial customer report")
    reply_tool = _LateImageFreshnessReply()
    registry, schemas = _register(reply_tool)
    guardrails = Guardrails(
        GuardrailsConfig(
            max_rounds=4,
            timeout_seconds=30,
            max_tokens_per_session=10_000,
        )
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
            enable_vision_input=True,
        )
    ]

    assert downloaded_urls == [_REMOTE_IMAGE_URL]
    assert cursor_during_download == [(1, "m1")], (
        "the recovery cursor must advance only after the image enters MessageStore"
    )
    assert provider.cursor_by_call[:2] == ["m1", "m2"]

    second_call_messages = provider.messages_by_call[1]
    image_parts = [
        part
        for message in second_call_messages
        if message.get("role") == "user" and isinstance(message.get("content"), list)
        for part in message["content"]
        if part.get("type") == "image_url"
    ]
    assert image_parts == [
        {
            "type": "image_url",
            "image_url": {"url": _IMAGE_DATA_URL, "detail": "low"},
        }
    ]
    text_parts = [
        part.get("text", "")
        for message in second_call_messages
        if message.get("role") == "user" and isinstance(message.get("content"), list)
        for part in message["content"]
        if part.get("type") == "text"
    ]
    assert any("This screenshot shows the exact error." in text for text in text_parts)
    assert session.customer_msgs_snapshot_count == 2
    assert session.customer_msgs_snapshot_last_id == "m2"
    assert session.status is SessionStatus.COMPLETED
    assert session.reply_sent is True
    assert isinstance(events[-1], LoopFinished)
    assert events[-1].status == "completed"


@pytest.mark.asyncio
async def test_late_freshness_images_are_deduplicated_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One customer update cannot launch an unbounded image download fan-out."""
    session = AgentSession(ticket_id=3461, trigger_type="production")
    session.customer_msgs_snapshot_captured = True
    session.customer_msgs_snapshot_count = 1
    session.customer_msgs_snapshot_last_id = "m1"
    provider = _AlwaysRequestSolutionProvider(session.ticket_id)
    message_store = MessageStore(provider)

    unique_urls = [
        f"https://example.zendesk.com/attachments/late-{index}.png"
        for index in range(MAX_IMAGES + 3)
    ]
    requested_urls: list[str] = []

    async def download_image(url: str) -> str:
        requested_urls.append(url)
        return _IMAGE_DATA_URL

    monkeypatch.setattr(
        "zd_agent.services.ticket_messages._download_image_data_url",
        download_image,
    )
    result = ToolResult(
        success=False,
        error="stale",
        metadata={
            "freshness_recovery": FreshnessRecovery(
                action=FreshnessRecoveryAction.REFRESH_CONTEXT,
                new_messages=(
                    {
                        "id": "m2",
                        "body": "Customer attached several screenshots.",
                        "content_type": "image",
                        "image_urls": [unique_urls[0], *unique_urls, unique_urls[0]],
                    },
                ),
                next_snapshot_count=2,
                next_snapshot_last_id="m2",
            ),
        },
    )

    await _apply_freshness_recovery(
        result=result,
        session=session,
        message_store=message_store,
        enable_vision_input=True,
    )

    assert requested_urls == unique_urls[:MAX_IMAGES]
    image_parts = [
        part
        for message in message_store.raw
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if part.get("type") == "image_url"
    ]
    assert len(image_parts) == MAX_IMAGES


@pytest.mark.asyncio
async def test_late_image_with_vision_disabled_is_explicitly_marked_unread() -> None:
    """Advancing the cursor must not imply that the model saw a skipped image."""
    session = AgentSession(ticket_id=3461, trigger_type="production")
    session.customer_msgs_snapshot_captured = True
    session.customer_msgs_snapshot_count = 1
    session.customer_msgs_snapshot_last_id = "m1"
    provider = _AlwaysRequestSolutionProvider(session.ticket_id)
    message_store = MessageStore(provider)
    result = ToolResult(
        success=False,
        error="stale",
        metadata={
            "freshness_recovery": FreshnessRecovery(
                action=FreshnessRecoveryAction.REFRESH_CONTEXT,
                new_messages=(
                    {
                        "id": "m2",
                        "body": "This screenshot contains the exact error.",
                        "content_type": "image",
                        "image_urls": [_REMOTE_IMAGE_URL],
                    },
                ),
                next_snapshot_count=2,
                next_snapshot_last_id="m2",
            ),
        },
    )

    await _apply_freshness_recovery(
        result=result,
        session=session,
        message_store=message_store,
        enable_vision_input=False,
    )

    injected = str(message_store.raw[-1]["content"])
    assert "主模型视觉输入已关闭" in injected
    assert "图片内容尚未读取" in injected
    assert session.customer_msgs_snapshot_last_id == "m2"

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


class _UnsupportedImageProvider(_RecordingProvider):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        self.calls.append(copy.deepcopy(messages))
        if len(self.calls) == 1:
            raise ValueError(
                "model does not support image input "
                "data:image/png;base64,SECRET-CUSTOMER-CONTENT"
            )
        return LLMResponse(
            content="done",
            tool_calls=[],
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=2,
            raw={"role": "assistant", "content": "done"},
        )


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


@pytest.mark.asyncio
async def test_inject_images_reuses_preloaded_data_url_without_network(monkeypatch) -> None:
    provider = _RecordingProvider()
    store = MessageStore(provider)
    session = AgentSession(ticket_id=3485, dry_run=True)
    data_url = "data:image/png;base64,iVBORw0KGgoAAAAAAAAAAAAAAAAAAAAA"
    session.image_urls = [{"url": data_url, "file_name": "screenshot.png"}]

    async def fail_download(_url: str) -> str:
        raise AssertionError("preloaded image must not be downloaded again")

    monkeypatch.setattr(
        "zd_agent.services.ticket_messages._download_image_data_url",
        fail_download,
    )

    await agent_loop_module._inject_images(session, store)

    assert data_url in str(store.raw)


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
    session: AgentSession | None = None,
) -> None:
    events = [
        event
        async for event in agent_loop(
            session or AgentSession(ticket_id=2931, dry_run=True),
            provider,
            store,
            registry or ToolRegistry(),
            _guardrails(),
            [],
        )
    ]
    assert events[-1].status == "completed"


@pytest.mark.asyncio
async def test_connection_retry_logs_safe_session_and_subagent_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SensitiveTimeoutProvider(_RecordingProvider):
        async def chat(
            self,
            messages: list[dict[str, Any]],
            tools: list[ToolSchema] | None = None,
            temperature: float = 0.2,
        ) -> LLMResponse:
            self.calls.append(copy.deepcopy(messages))
            if len(self.calls) == 1:
                raise TimeoutError(
                    "connection error calling https://api.example.test/v1 "
                    "token=customer-secret"
                )
            return LLMResponse(
                content="done",
                tool_calls=[],
                finish_reason="stop",
                prompt_tokens=10,
                completion_tokens=2,
                raw={"role": "assistant", "content": "done"},
            )

    async def _no_sleep(delay: float) -> None:
        return None

    warnings: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(agent_loop_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(
        agent_loop_module.logger,
        "warning",
        lambda event, **fields: warnings.append((event, fields)),
    )
    provider = SensitiveTimeoutProvider()
    store = MessageStore(provider)
    store.append_system("system")
    store.append_user("hello")
    session = AgentSession(ticket_id=3485, dry_run=True)
    session.parent_session_id = "parent-session"
    session.subagent_batch_id = "batch-7"
    session.subagent_index = 2

    await _drain_loop(store, provider, session=session)

    retry = next(fields for event, fields in warnings if event == "connection_error_retry")
    assert retry["session_id"] == session.session_id
    assert retry["parent_session_id"] == "parent-session"
    assert retry["subagent_batch_id"] == "batch-7"
    assert retry["subagent_index"] == 2
    assert retry["round"] == 1
    assert retry["provider_type"] == "SensitiveTimeoutProvider"
    assert retry["error_type"] == "TimeoutError"
    assert "[URL]" in retry["error_detail"]
    assert "customer-secret" not in str(retry)


@pytest.mark.asyncio
async def test_new_customer_context_cancels_stale_tools_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AgentSession(ticket_id=3485, dry_run=True)
    session.customer_msgs_snapshot_count = 1
    session.customer_msgs_snapshot_last_id = "message-before"
    session.customer_msgs_snapshot_captured = True

    class DirtyDuringFirstCallProvider(_RecordingProvider):
        async def chat(
            self,
            messages: list[dict[str, Any]],
            tools: list[ToolSchema] | None = None,
            temperature: float = 0.2,
        ) -> LLMResponse:
            self.calls.append(copy.deepcopy(messages))
            if len(self.calls) == 1:
                session.customer_context_dirty = True
                session.customer_context_generation = 1
                session.customer_context_source = "messaging_webhook"
                session.customer_context_event_id = "evt-new-screenshot"
                return LLMResponse(
                    content=None,
                    tool_calls=[ToolCallRequest(
                        id="stale-tool-1",
                        name="expensive_probe",
                        arguments="{}",
                    )],
                    finish_reason="tool_calls",
                    prompt_tokens=10,
                    completion_tokens=2,
                    raw={
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "stale-tool-1",
                            "type": "function",
                            "function": {
                                "name": "expensive_probe",
                                "arguments": "{}",
                            },
                        }],
                    },
                )
            return LLMResponse(
                content="done with the new screenshot",
                tool_calls=[],
                finish_reason="stop",
                prompt_tokens=10,
                completion_tokens=2,
                raw={"role": "assistant", "content": "done with the new screenshot"},
            )

    class ExpensiveProbe(Tool):
        name = "expensive_probe"
        description = "must not run against stale context"
        parameters: dict[str, Any] = {"type": "object", "properties": {}}

        def __init__(self) -> None:
            self.calls = 0

        async def execute(
            self,
            args: dict[str, Any],
            current_session: AgentSession,
        ) -> ToolResult:
            self.calls += 1
            return ToolResult(output="stale result")

    async def fetch_messages(_ticket_id: int) -> list[dict[str, Any]]:
        return [
            {"id": "message-before", "body": "Initial report"},
            {
                "id": "message-screenshot",
                "body": "I attached another screenshot with the full error.",
                "image_urls": ["https://example.invalid/private.png"],
            },
        ]

    monkeypatch.setattr(
        "zd_agent.services.ticket_messages.fetch_customer_messages_for_freshness",
        fetch_messages,
    )
    provider = DirtyDuringFirstCallProvider()
    store = MessageStore(provider)
    store.append_system("system")
    store.append_user("investigate")
    tool = ExpensiveProbe()
    registry = ToolRegistry()
    registry.register(tool)

    await _drain_loop(store, provider, registry=registry, session=session)

    assert tool.calls == 0
    assert len(provider.calls) == 2
    retry_context = str(provider.calls[1])
    assert "another screenshot" in retry_context
    assert "Cancelled: customer context changed" in retry_context
    assert session.customer_msgs_snapshot_last_id == "message-screenshot"
    assert session.customer_context_dirty is False


@pytest.mark.asyncio
async def test_newer_webhook_generation_is_checked_before_another_llm_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AgentSession(ticket_id=3485, dry_run=True)
    session.customer_msgs_snapshot_count = 1
    session.customer_msgs_snapshot_last_id = "message-before"
    session.customer_msgs_snapshot_captured = True
    session.customer_context_dirty = True
    session.customer_context_generation = 1
    fetch_count = 0

    async def fetch_messages(_ticket_id: int) -> list[dict[str, Any]]:
        nonlocal fetch_count
        fetch_count += 1
        if fetch_count == 1:
            # A second webhook lands after this fetch generation was captured.
            session.customer_context_generation = 2
            session.customer_context_event_id = "evt-newer"
            return [{"id": "message-before", "body": "Initial report"}]
        return [
            {"id": "message-before", "body": "Initial report"},
            {
                "id": "message-newer",
                "body": "The newer webhook contains a screenshot.",
                "image_urls": ["https://example.invalid/newer.png"],
            },
        ]

    monkeypatch.setattr(
        "zd_agent.services.ticket_messages.fetch_customer_messages_for_freshness",
        fetch_messages,
    )
    provider = _RecordingProvider()
    store = MessageStore(provider)
    store.append_system("system")
    store.append_user("investigate")

    await _drain_loop(store, provider, session=session)

    assert fetch_count == 2
    assert len(provider.calls) == 1
    assert "newer webhook" in str(provider.calls[0])


@pytest.mark.asyncio
async def test_successful_early_context_check_resets_fetch_error_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AgentSession(ticket_id=3485, dry_run=True)
    session.customer_msgs_snapshot_count = 1
    session.customer_msgs_snapshot_last_id = "message-before"
    session.customer_msgs_snapshot_captured = True
    session.customer_context_dirty = True
    session.customer_context_generation = 1
    session.freshness_fetch_error_count = 1

    async def fetch_messages(_ticket_id: int) -> list[dict[str, Any]]:
        return [{"id": "message-before", "body": "Initial report"}]

    monkeypatch.setattr(
        "zd_agent.services.ticket_messages.fetch_customer_messages_for_freshness",
        fetch_messages,
    )
    provider = _RecordingProvider()
    store = MessageStore(provider)
    store.append_system("system")
    store.append_user("investigate")

    await _drain_loop(store, provider, session=session)

    assert session.freshness_fetch_error_count == 0


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
async def test_agent_loop_does_not_snip_active_responses_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    store.raw.append({
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "tool-1",
            "type": "function",
            "function": {"name": "knowledge_search", "arguments": "{}"},
        }],
        "_zd_responses_output": [{
            "type": "reasoning",
            "encrypted_content": "encrypted",
        }],
    })
    store.raw.append({
        "role": "tool",
        "tool_call_id": "tool-1",
        "name": "knowledge_search",
        "content": "x" * 1000,
    })

    await _drain_loop(store, provider)

    first_call_tool = next(
        msg for msg in provider.calls[0] if msg.get("name") == "knowledge_search"
    )
    assert first_call_tool["content"] == "x" * 1000


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
async def test_timeout_retry_does_not_mutate_active_responses_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(agent_loop_module.asyncio, "sleep", _no_sleep)
    provider = _RecordingProvider(fail_first=True)
    store = MessageStore(provider)
    store.append_system("system")
    store.append_user([
        {"type": "text", "text": "inspect"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,abc", "detail": "low"},
        },
    ])
    store.raw.append({
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "tool-1",
            "type": "function",
            "function": {"name": "knowledge_search", "arguments": "{}"},
        }],
        "_zd_responses_output": [{
            "type": "reasoning",
            "encrypted_content": "encrypted",
        }],
    })
    store.raw.append({
        "role": "tool",
        "tool_call_id": "tool-1",
        "name": "knowledge_search",
        "content": "y" * 1000,
    })

    await _drain_loop(store, provider)

    retry_messages = provider.calls[1]
    assert "image_url" in str(retry_messages)
    retry_tool = next(
        msg for msg in retry_messages if msg.get("name") == "knowledge_search"
    )
    assert retry_tool["content"] == "y" * 1000


@pytest.mark.asyncio
async def test_agent_loop_retries_without_images_when_model_rejects_image_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings: list[tuple[str, dict[str, Any]]] = []

    def capture_warning(event: str, **fields: Any) -> None:
        warnings.append((event, fields))

    monkeypatch.setattr(agent_loop_module.logger, "warning", capture_warning)
    provider = _UnsupportedImageProvider()
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
    assert any(
        "无法被当前模型读取" in str(msg.get("content", ""))
        for msg in retry_messages
    )
    fallback_warning = next(
        fields for event, fields in warnings if event == "image_url_fallback"
    )
    assert "SECRET-CUSTOMER-CONTENT" not in str(fallback_warning)


@pytest.mark.asyncio
async def test_image_error_does_not_mutate_or_replay_active_responses_history() -> None:
    provider = _UnsupportedImageProvider()
    store = MessageStore(provider)
    store.append_system("system")
    store.append_user([
        {"type": "text", "text": "inspect"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,abc", "detail": "low"},
        },
    ])
    store.raw.append({
        "role": "assistant",
        "content": "prior",
        "_zd_responses_output": [{
            "type": "reasoning",
            "encrypted_content": "encrypted",
        }],
    })

    events = [
        event
        async for event in agent_loop(
            AgentSession(ticket_id=2931, dry_run=True),
            provider,
            store,
            ToolRegistry(),
            _guardrails(),
            [],
        )
    ]

    assert len(provider.calls) == 1
    assert events[-1].status == "error"
    assert "image_url" in str(store.raw)


@pytest.mark.asyncio
async def test_agent_loop_skips_image_injection_when_vision_is_disabled() -> None:
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
    store.append_user("please inspect the attached screenshot")
    registry = ToolRegistry()
    registry.register(_NoopTool())
    session = AgentSession(ticket_id=2931, dry_run=True)
    session.image_urls = [{"url": "https://example.com/screenshot.png"}]

    events = [
        event
        async for event in agent_loop(
            session,
            provider,
            store,
            registry,
            _guardrails(),
            [],
            enable_vision_input=False,
        )
    ]

    assert events[-1].status == "completed"
    assert session.images_injected is True
    assert len(provider.calls) == 2
    assert not any(
        "image_url" in str(message)
        for call in provider.calls
        for message in call
    )
    assert any(
        "视觉输入已关闭" in str(message.get("content", ""))
        for message in provider.calls[1]
    )


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

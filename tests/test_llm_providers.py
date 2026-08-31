"""Phase 1 LLM 抽象层单元测试（不发起真实 API 请求）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from zd_agent.config import EnvSettings, LLMConfig
from zd_agent.runtime.llm.anthropic_provider import (
    _extract_system,
    _map_stop_reason,
    _openai_messages_to_anthropic,
    _tool_schemas_to_anthropic,
)
from zd_agent.runtime.llm.factory import create_llm_provider
from zd_agent.runtime.llm.openai_provider import (
    OpenAIProvider,
    _messages_to_responses_input,
    _parse_completion,
    _tool_schemas_to_openai,
)
from zd_agent.runtime.llm.types import (
    LLMResponse,
    ToolCallRequest,
    ToolSchema,
)


# --- types.py ---


def test_tool_call_request_construction_and_fields() -> None:
    """ToolCallRequest 基本构造与属性可读。"""
    tc = ToolCallRequest(id="c1", name="foo", arguments='{"a":1}')
    assert tc.id == "c1"
    assert tc.name == "foo"
    assert tc.arguments == '{"a":1}'


def test_tool_schema_construction_and_fields() -> None:
    """ToolSchema 基本构造与 parameters 字典。"""
    schema = ToolSchema(
        name="get_weather",
        description="查询天气",
        parameters={"type": "object", "properties": {"city": {"type": "string"}}},
    )
    assert schema.name == "get_weather"
    assert schema.description == "查询天气"
    assert schema.parameters["type"] == "object"


def test_llm_response_construction_and_has_tool_calls_false() -> None:
    """无 tool_calls 时 has_tool_calls 为 False。"""
    resp = LLMResponse(
        content="hi",
        tool_calls=[],
        finish_reason="stop",
        prompt_tokens=1,
        completion_tokens=2,
    )
    assert resp.content == "hi"
    assert resp.tool_calls == []
    assert resp.finish_reason == "stop"
    assert resp.prompt_tokens == 1
    assert resp.completion_tokens == 2
    assert resp.has_tool_calls is False


def test_llm_response_has_tool_calls_true() -> None:
    """存在 tool_calls 时 has_tool_calls 为 True。"""
    resp = LLMResponse(
        content=None,
        tool_calls=[ToolCallRequest(id="x", name="n", arguments="{}")],
        finish_reason="tool_calls",
        prompt_tokens=0,
        completion_tokens=0,
    )
    assert resp.has_tool_calls is True


# --- openai_provider.py ---


def test_tool_schemas_to_openai_format() -> None:
    """_tool_schemas_to_openai 将 ToolSchema 列表转为 OpenAI tools 格式。"""
    tools = [
        ToolSchema(name="f1", description="d1", parameters={"type": "object"}),
        ToolSchema(
            name="f2", description="d2", parameters={"type": "object", "properties": {}}
        ),
    ]
    out = _tool_schemas_to_openai(tools)
    assert out == [
        {
            "type": "function",
            "function": {
                "name": "f1",
                "description": "d1",
                "parameters": {"type": "object"},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "f2",
                "description": "d2",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]


def test_messages_to_responses_input_converts_multimodal_chat_content() -> None:
    converted = _messages_to_responses_input(
        [
            {"role": "system", "content": "You are support."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "See screenshot"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,abc",
                            "detail": "low",
                        },
                    },
                ],
            },
        ]
    )

    assert converted == [
        {"type": "message", "role": "system", "content": "You are support."},
        {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": "See screenshot"},
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64,abc",
                    "detail": "low",
                },
            ],
        },
    ]


def test_parse_completion_without_tool_calls_and_with_usage() -> None:
    """_parse_completion：无 tool_calls、有 usage 时解析正确。"""
    message = MagicMock()
    message.content = "回答正文"
    message.tool_calls = None
    message.model_dump.return_value = {"role": "assistant", "content": "回答正文"}
    choice = SimpleNamespace(message=message, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=50)
    response = MagicMock()
    response.choices = [choice]
    response.usage = usage

    result = _parse_completion(response)

    assert isinstance(result, LLMResponse)
    assert result.content == "回答正文"
    assert result.tool_calls == []
    assert result.finish_reason == "stop"
    assert result.prompt_tokens == 100
    assert result.completion_tokens == 50
    assert result.raw == {"role": "assistant", "content": "回答正文"}


def test_parse_completion_with_tool_calls_and_no_usage() -> None:
    """_parse_completion：含 tool_calls、usage 为 None 时 token 为 0。"""
    fn = SimpleNamespace(name="search", arguments='{"q":"a"}')
    tc = SimpleNamespace(id="call_abc", function=fn)
    message = MagicMock()
    message.content = None
    message.tool_calls = [tc]
    message.model_dump.return_value = {"role": "assistant", "tool_calls": []}
    choice = SimpleNamespace(message=message, finish_reason=None)
    response = MagicMock()
    response.choices = [choice]
    response.usage = None

    result = _parse_completion(response)

    assert result.content is None
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "call_abc"
    assert result.tool_calls[0].name == "search"
    assert result.tool_calls[0].arguments == '{"q":"a"}'
    assert result.finish_reason == "stop"
    assert result.prompt_tokens == 0
    assert result.completion_tokens == 0


async def test_gpt_56_tool_request_disables_reasoning_for_chat_completions() -> None:
    """GPT-5.6 的 Chat Completions 工具调用必须显式关闭 reasoning。"""
    message = MagicMock()
    message.content = None
    message.tool_calls = []
    message.model_dump.return_value = {"role": "assistant", "content": None}
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=None,
    )
    create = AsyncMock(return_value=response)
    provider = OpenAIProvider(api_key="sk-test", model="gpt-5.6-terra")
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    await provider.chat(
        [{"role": "user", "content": "Check the ticket"}],
        tools=[
            ToolSchema(
                name="get_ticket",
                description="Fetch a Zendesk ticket",
                parameters={"type": "object", "properties": {}},
            )
        ],
    )

    assert create.await_args.kwargs["reasoning_effort"] == "none"


async def test_gpt_56_reasoning_tool_request_uses_responses_api() -> None:
    """GPT-5.6 的非 none 工具轮使用 Responses API 保留推理能力。"""
    reasoning_item = SimpleNamespace(
        type="reasoning",
        model_dump=lambda **_kwargs: {
            "id": "rs_1",
            "type": "reasoning",
            "encrypted_content": "encrypted",
            "summary": [],
        },
    )
    function_call = SimpleNamespace(
        type="function_call",
        call_id="call_1",
        name="get_ticket",
        arguments='{"ticket_id":3461}',
        status="completed",
        model_dump=lambda **_kwargs: {
            "id": "fc_1",
            "type": "function_call",
            "call_id": "call_1",
            "name": "get_ticket",
            "arguments": '{"ticket_id":3461}',
            "status": "completed",
        },
    )
    response = SimpleNamespace(
        status="completed",
        output=[reasoning_item, function_call],
        output_text="",
        usage=SimpleNamespace(
            input_tokens=120,
            output_tokens=24,
            input_tokens_details=SimpleNamespace(cached_tokens=32),
            output_tokens_details=SimpleNamespace(reasoning_tokens=20),
        ),
        incomplete_details=None,
        error=None,
    )
    responses_create = AsyncMock(return_value=response)
    chat_create = AsyncMock()
    provider = OpenAIProvider(
        api_key="sk-test",
        model="gpt-5.6-terra",
        reasoning_effort="low",
    )
    provider._client = SimpleNamespace(
        responses=SimpleNamespace(create=responses_create),
        chat=SimpleNamespace(completions=SimpleNamespace(create=chat_create)),
    )

    result = await provider.chat(
        [{"role": "user", "content": "Check ticket 3461"}],
        tools=[
            ToolSchema(
                name="get_ticket",
                description="Fetch a Zendesk ticket",
                parameters={
                    "type": "object",
                    "properties": {"ticket_id": {"type": "integer"}},
                },
            )
        ],
    )

    chat_create.assert_not_awaited()
    request = responses_create.await_args.kwargs
    assert request["reasoning"] == {"effort": "low"}
    assert request["store"] is False
    assert request["include"] == ["reasoning.encrypted_content"]
    assert request["tools"] == [
        {
            "type": "function",
            "name": "get_ticket",
            "description": "Fetch a Zendesk ticket",
            "parameters": {
                "type": "object",
                "properties": {"ticket_id": {"type": "integer"}},
            },
            "strict": False,
        }
    ]
    assert result.finish_reason == "tool_calls"
    assert result.tool_calls[0].id == "call_1"
    assert result.prompt_tokens == 120
    assert result.completion_tokens == 24
    assert result.raw["_zd_responses_output"][0]["type"] == "reasoning"


async def test_namespaced_gpt_56_reasoning_tool_request_uses_responses_api() -> None:
    response = SimpleNamespace(
        status="completed",
        output=[],
        output_text="Done",
        usage=None,
        incomplete_details=None,
        error=None,
    )
    responses_create = AsyncMock(return_value=response)
    chat_create = AsyncMock()
    provider = OpenAIProvider(
        api_key="sk-test",
        model="openai/gpt-5.6-terra",
        reasoning_effort="low",
    )
    provider._client = SimpleNamespace(
        responses=SimpleNamespace(create=responses_create),
        chat=SimpleNamespace(completions=SimpleNamespace(create=chat_create)),
    )

    await provider.chat(
        [{"role": "user", "content": "Check the ticket"}],
        tools=[
            ToolSchema(
                name="get_ticket",
                description="Fetch a Zendesk ticket",
                parameters={"type": "object", "properties": {}},
            )
        ],
    )

    responses_create.assert_awaited_once()
    chat_create.assert_not_awaited()


async def test_pro_model_always_uses_responses_api_and_model_default_effort() -> None:
    response = SimpleNamespace(
        status="completed",
        output=[],
        output_text="Done",
        usage=None,
        incomplete_details=None,
        error=None,
    )
    responses_create = AsyncMock(return_value=response)
    chat_create = AsyncMock()
    provider = OpenAIProvider(api_key="sk-test", model="gpt-5.5-pro")
    provider._client = SimpleNamespace(
        responses=SimpleNamespace(create=responses_create),
        chat=SimpleNamespace(completions=SimpleNamespace(create=chat_create)),
    )

    result = await provider.chat([{"role": "user", "content": "Analyze this"}])

    assert result.content == "Done"
    assert "reasoning" not in responses_create.await_args.kwargs
    chat_create.assert_not_awaited()


@pytest.mark.parametrize(
    "model",
    [
        "gpt-5.3-codex",
        "gpt-5.2-codex",
        "gpt-5.1-codex",
        "gpt-5-codex",
    ],
)
async def test_codex_model_always_uses_responses_api(model: str) -> None:
    response = SimpleNamespace(
        status="completed",
        output=[],
        output_text="Done",
        usage=None,
        incomplete_details=None,
        error=None,
    )
    responses_create = AsyncMock(return_value=response)
    chat_create = AsyncMock()
    provider = OpenAIProvider(api_key="sk-test", model=model)
    provider._client = SimpleNamespace(
        responses=SimpleNamespace(create=responses_create),
        chat=SimpleNamespace(completions=SimpleNamespace(create=chat_create)),
    )

    await provider.chat([{"role": "user", "content": "Analyze this"}])

    responses_create.assert_awaited_once()
    chat_create.assert_not_awaited()


async def test_responses_reasoning_raises_minimum_configured_output_budget() -> None:
    response = SimpleNamespace(
        status="completed",
        output=[],
        output_text="Done",
        usage=None,
        incomplete_details=None,
        error=None,
    )
    create = AsyncMock(return_value=response)
    provider = OpenAIProvider(
        api_key="sk-test",
        model="gpt-5.6-terra",
        reasoning_effort="high",
        max_tokens=8192,
    )
    provider._client = SimpleNamespace(
        responses=SimpleNamespace(create=create),
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock())),
    )

    await provider.chat(
        [{"role": "user", "content": "Analyze"}],
        tools=[ToolSchema(name="lookup", description="Lookup", parameters={})],
    )

    assert create.await_args.kwargs["max_output_tokens"] == 25000


async def test_ultra_reasoning_scales_responses_budget_and_timeout() -> None:
    response = SimpleNamespace(
        status="completed",
        output=[],
        output_text="Done",
        usage=None,
        incomplete_details=None,
        error=None,
    )
    create = AsyncMock(return_value=response)
    provider = OpenAIProvider(
        api_key="sk-test",
        model="gpt-5.6-terra",
        reasoning_effort="ultra",
        max_tokens=8192,
        timeout=120,
    )
    provider._client = SimpleNamespace(
        responses=SimpleNamespace(create=create),
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock())),
    )

    await provider.chat(
        [{"role": "user", "content": "Analyze"}],
        tools=[ToolSchema(name="lookup", description="Lookup", parameters={})],
    )

    assert create.await_args.kwargs["max_output_tokens"] == 32768
    assert create.await_args.kwargs["timeout"] == 600.0


async def test_ultra_reasoning_scales_chat_completion_budget_and_timeout() -> None:
    message = MagicMock()
    message.content = "Done"
    message.tool_calls = []
    message.model_dump.return_value = {"role": "assistant", "content": "Done"}
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=None,
    )
    create = AsyncMock(return_value=response)
    provider = OpenAIProvider(
        api_key="sk-test",
        model="gpt-5.6-sol",
        reasoning_effort="ultra",
        max_tokens=8192,
        timeout=120,
    )
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    await provider.chat([{"role": "user", "content": "Analyze"}])

    assert create.await_args.kwargs["max_completion_tokens"] == 32768
    assert create.await_args.kwargs["timeout"] == 600.0


async def test_incomplete_responses_result_raises_instead_of_silent_stop() -> None:
    response = SimpleNamespace(
        status="incomplete",
        output=[],
        output_text="",
        usage=None,
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        error=None,
    )
    create = AsyncMock(return_value=response)
    provider = OpenAIProvider(
        api_key="sk-test",
        model="gpt-5.6-terra",
        reasoning_effort="low",
    )
    provider._client = SimpleNamespace(
        responses=SimpleNamespace(create=create),
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock())),
    )

    with pytest.raises(RuntimeError, match="incomplete.*max_output_tokens"):
        await provider.chat(
            [{"role": "user", "content": "Analyze"}],
            tools=[ToolSchema(name="lookup", description="Lookup", parameters={})],
        )


async def test_responses_history_replays_reasoning_and_function_output() -> None:
    """后续工具轮完整回放 reasoning/function_call，并用 call_id 关联结果。"""
    message = SimpleNamespace(
        type="message",
        content=[SimpleNamespace(type="output_text", text="Done")],
        model_dump=lambda **_kwargs: {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "phase": "final_answer",
            "content": [{"type": "output_text", "text": "Done", "annotations": []}],
        },
    )
    response = SimpleNamespace(
        status="completed",
        output=[message],
        output_text="Done",
        usage=SimpleNamespace(
            input_tokens=40,
            output_tokens=5,
            input_tokens_details=SimpleNamespace(cached_tokens=0),
            output_tokens_details=SimpleNamespace(reasoning_tokens=0),
        ),
        incomplete_details=None,
        error=None,
    )
    responses_create = AsyncMock(return_value=response)
    provider = OpenAIProvider(
        api_key="sk-test",
        model="gpt-5.6-terra",
        reasoning_effort="medium",
    )
    provider._client = SimpleNamespace(
        responses=SimpleNamespace(create=responses_create),
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock())),
    )
    prior_output = [
        {
            "id": "rs_1",
            "type": "reasoning",
            "encrypted_content": "encrypted",
            "summary": [],
        },
        {
            "id": "fc_1",
            "type": "function_call",
            "call_id": "call_1",
            "name": "get_ticket",
            "arguments": '{"ticket_id":3461}',
            "status": "completed",
        },
    ]

    await provider.chat(
        [
            {"role": "system", "content": "You are support."},
            {"role": "user", "content": "Check ticket 3461"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "get_ticket",
                            "arguments": '{"ticket_id":3461}',
                        },
                    }
                ],
                "_zd_responses_output": prior_output,
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "get_ticket",
                "content": '{"status":"open"}',
            },
        ],
        tools=[
            ToolSchema(
                name="get_ticket",
                description="Fetch a Zendesk ticket",
                parameters={"type": "object", "properties": {}},
            )
        ],
    )

    request_input = responses_create.await_args.kwargs["input"]
    assert request_input[2:4] == prior_output
    assert request_input[4] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": '{"status":"open"}',
    }


@pytest.mark.parametrize(
    ("model", "tools"),
    [
        ("gpt-5.6-terra", None),
        (
            "gpt-5.5",
            [
                ToolSchema(
                    name="get_ticket",
                    description="Fetch a Zendesk ticket",
                    parameters={"type": "object", "properties": {}},
                )
            ],
        ),
    ],
)
async def test_reasoning_override_is_limited_to_gpt_56_tool_requests(
    model: str,
    tools: list[ToolSchema] | None,
) -> None:
    """纯文本 GPT-5.6 与旧模型工具调用保持原有推理参数行为。"""
    message = MagicMock()
    message.content = "ok"
    message.tool_calls = []
    message.model_dump.return_value = {"role": "assistant", "content": "ok"}
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=None,
    )
    create = AsyncMock(return_value=response)
    provider = OpenAIProvider(api_key="sk-test", model=model)
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    await provider.chat(
        [{"role": "user", "content": "Check the ticket"}],
        tools=tools,
    )

    assert "reasoning_effort" not in create.await_args.kwargs


async def test_supported_legacy_model_sends_configured_reasoning_effort() -> None:
    message = MagicMock()
    message.content = "ok"
    message.tool_calls = []
    message.model_dump.return_value = {"role": "assistant", "content": "ok"}
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=None,
    )
    create = AsyncMock(return_value=response)
    provider = OpenAIProvider(
        api_key="sk-test",
        model="gpt-5.4-mini",
        reasoning_effort="xhigh",
    )
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    await provider.chat(
        [{"role": "user", "content": "Check the ticket"}],
        tools=[
            ToolSchema(
                name="get_ticket",
                description="Fetch a Zendesk ticket",
                parameters={"type": "object", "properties": {}},
            )
        ],
    )

    assert create.await_args.kwargs["reasoning_effort"] == "xhigh"


def test_openai_provider_rejects_reasoning_effort_unsupported_by_model() -> None:
    with pytest.raises(ValueError, match="does not support reasoning effort 'low'"):
        OpenAIProvider(
            api_key="sk-test",
            model="gpt-4.1",
            reasoning_effort="low",
        )


# --- anthropic_provider.py ---


def test_extract_system_string_and_merge_multiple() -> None:
    """_extract_system：字符串 system 多条合并为 \\n\\n，其余消息保留顺序。"""
    messages = [
        {"role": "system", "content": "A"},
        {"role": "user", "content": "u1"},
        {"role": "system", "content": "B"},
    ]
    system, rest = _extract_system(messages)
    assert system == "A\n\nB"
    assert rest == [{"role": "user", "content": "u1"}]


def test_extract_system_list_text_blocks() -> None:
    """_extract_system：system 为多块 text 时拼接。"""
    messages = [
        {
            "role": "system",
            "content": [{"type": "text", "text": "p1"}, {"type": "text", "text": "p2"}],
        },
        {"role": "assistant", "content": "ok"},
    ]
    system, rest = _extract_system(messages)
    assert system == "p1\np2"
    assert len(rest) == 1


def test_extract_system_empty_returns_none() -> None:
    """_extract_system：无有效 system 文本时返回 (None, 原列表非 system 部分)。"""
    messages = [{"role": "user", "content": "hi"}]
    system, rest = _extract_system(messages)
    assert system is None
    assert rest == messages


def test_openai_messages_to_anthropic_tool_and_assistant_tool_calls() -> None:
    """_openai_messages_to_anthropic：tool 转 user+tool_result；assistant tool_calls 转 tool_use。"""
    rest = [
        {
            "role": "assistant",
            "content": "调用工具",
            "tool_calls": [
                {
                    "id": "tu1",
                    "function": {"name": "calc", "arguments": '{"x": 1}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "tu1", "name": "calc", "content": "2"},
    ]
    out = _openai_messages_to_anthropic(rest)
    assert len(out) == 2
    assert out[0]["role"] == "assistant"
    blocks = out[0]["content"]
    assert any(b.get("type") == "text" for b in blocks)
    tool_use = next(b for b in blocks if b.get("type") == "tool_use")
    assert tool_use["id"] == "tu1"
    assert tool_use["name"] == "calc"
    assert tool_use["input"] == {"x": 1}
    assert out[1]["role"] == "user"
    assert out[1]["content"][0]["type"] == "tool_result"
    assert out[1]["content"][0]["tool_use_id"] == "tu1"
    assert out[1]["content"][0]["content"] == "2"


def test_openai_messages_to_anthropic_user_and_skip_unknown_role() -> None:
    """_openai_messages_to_anthropic：user 保留；未知 role 跳过。"""
    rest = [
        {"role": "user", "content": "hello"},
        {"role": "system", "content": "should_skip"},
    ]
    out = _openai_messages_to_anthropic(rest)
    assert out == [{"role": "user", "content": "hello"}]


def test_openai_image_data_url_converts_to_anthropic_image_source() -> None:
    out = _openai_messages_to_anthropic(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Inspect this screenshot"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,iVBORw0KGgo=",
                            "detail": "high",
                        },
                    },
                ],
            }
        ]
    )

    assert out == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Inspect this screenshot"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "iVBORw0KGgo=",
                    },
                },
            ],
        }
    ]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/image.png",
        "data:text/plain;base64,aGVsbG8=",
        "data:image/png;base64,not-valid-base64!",
    ],
)
def test_anthropic_image_conversion_fails_closed_for_unsupported_sources(
    url: str,
) -> None:
    with pytest.raises(ValueError, match="Anthropic image"):
        _openai_messages_to_anthropic(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": url}},
                    ],
                }
            ]
        )


def test_map_stop_reason_known_and_unknown() -> None:
    """_map_stop_reason：内置映射与未知值透传。"""
    assert _map_stop_reason(None) == "stop"
    assert _map_stop_reason("end_turn") == "stop"
    assert _map_stop_reason("tool_use") == "tool_calls"
    assert _map_stop_reason("max_tokens") == "length"
    assert _map_stop_reason("other") == "other"


def test_tool_schemas_to_anthropic_format() -> None:
    """_tool_schemas_to_anthropic 转为 name/description/input_schema。"""
    tools = [ToolSchema(name="t", description="desc", parameters={"type": "object"})]
    assert _tool_schemas_to_anthropic(tools) == [
        {"name": "t", "description": "desc", "input_schema": {"type": "object"}}
    ]


# --- factory.py ---


def test_create_llm_provider_openai_branch() -> None:
    """create_llm_provider：provider=openai 返回 OpenAIProvider。"""
    from zd_agent.runtime.llm.openai_provider import OpenAIProvider

    cfg = MagicMock(spec=LLMConfig)
    cfg.provider = "openai"
    cfg.model = "gpt-5.4-mini"
    cfg.reasoning_effort = "low"
    cfg.base_url = None
    cfg.timeout = 30
    cfg.max_tokens = 8192
    env = MagicMock(spec=EnvSettings)
    env.openai_api_key = "sk-mock"

    provider = create_llm_provider(cfg, env)
    assert isinstance(provider, OpenAIProvider)
    assert provider._reasoning_effort == "low"


def test_create_llm_provider_anthropic_branch() -> None:
    """create_llm_provider：provider=anthropic 返回 AnthropicProvider。"""
    from zd_agent.runtime.llm.anthropic_provider import AnthropicProvider

    cfg = MagicMock(spec=LLMConfig)
    cfg.provider = "anthropic"
    cfg.model = "claude-3-5-sonnet-20241022"
    cfg.timeout = 45
    cfg.max_tokens = 8192
    env = MagicMock(spec=EnvSettings)
    env.anthropic_api_key = "sk-ant-mock"

    provider = create_llm_provider(cfg, env)
    assert isinstance(provider, AnthropicProvider)


def test_create_llm_provider_unknown_raises() -> None:
    """create_llm_provider：未知 provider 抛出 ValueError。"""
    cfg = MagicMock(spec=LLMConfig)
    cfg.provider = "unknown_vendor"
    env = MagicMock(spec=EnvSettings)

    with pytest.raises(ValueError, match="不支持的 LLM provider"):
        create_llm_provider(cfg, env)

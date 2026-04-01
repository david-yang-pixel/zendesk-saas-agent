"""Phase 1 LLM 抽象层单元测试（不发起真实 API 请求）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from zd_agent.config import EnvSettings, LLMConfig
from zd_agent.runtime.llm.anthropic_provider import (
    _extract_system,
    _map_stop_reason,
    _openai_messages_to_anthropic,
    _tool_schemas_to_anthropic,
)
from zd_agent.runtime.llm.factory import create_llm_provider
from zd_agent.runtime.llm.openai_provider import _parse_completion, _tool_schemas_to_openai
from zd_agent.runtime.llm.types import LLMProvider, LLMResponse, ToolCallRequest, ToolSchema
from zd_agent.runtime.messages import MessageStore


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
        ToolSchema(name="f2", description="d2", parameters={"type": "object", "properties": {}}),
    ]
    out = _tool_schemas_to_openai(tools)
    assert out == [
        {"type": "function", "function": {"name": "f1", "description": "d1", "parameters": {"type": "object"}}},
        {
            "type": "function",
            "function": {"name": "f2", "description": "d2", "parameters": {"type": "object", "properties": {}}},
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
    cfg.model = "gpt-4o-mini"
    cfg.base_url = None
    cfg.timeout = 30
    cfg.max_tokens = 8192
    env = MagicMock(spec=EnvSettings)
    env.openai_api_key = "sk-mock"

    provider = create_llm_provider(cfg, env)
    assert isinstance(provider, OpenAIProvider)


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


# --- messages.py ---


class _FakeProvider(LLMProvider):
    """测试用最小 LLMProvider：仅实现 format_tool_result。"""

    async def chat(self, messages, tools=None, temperature=0.2):
        raise NotImplementedError

    def format_tool_result(self, tool_call_id: str, name: str, content: str) -> dict:
        return {"role": "tool", "tool_call_id": tool_call_id, "name": name, "content": content}


def test_message_store_append_and_to_api_messages_same_reference() -> None:
    """MessageStore：append 系列与 to_api_messages 返回同一内部列表引用。"""
    store = MessageStore(_FakeProvider())
    store.append_system("sys")
    store.append_user("u")
    store.append_assistant({"role": "assistant", "content": "a"})
    store.append_tool_result("id1", "n", "out")
    api = store.to_api_messages()
    assert api is store.raw
    assert len(store) == 4
    assert api[0] == {"role": "system", "content": "sys"}
    assert api[1] == {"role": "user", "content": "u"}
    assert api[2]["role"] == "assistant"
    assert api[3]["role"] == "tool" and api[3]["tool_call_id"] == "id1"


def test_message_store_append_assistant_non_dict() -> None:
    """MessageStore：append_assistant 非 dict 时包装为字符串 content。"""
    store = MessageStore(_FakeProvider())
    store.append_assistant("plain")
    assert store.to_api_messages()[0] == {"role": "assistant", "content": "plain"}


def test_message_store_raw_setter_replaces_list() -> None:
    """MessageStore：raw setter 替换内部消息列表。"""
    store = MessageStore(_FakeProvider())
    store.append_user("x")
    new_list = [{"role": "user", "content": "y"}]
    store.raw = new_list
    assert len(store) == 1
    assert store.to_api_messages()[0]["content"] == "y"
    # 应持有同一引用
    assert store.raw is new_list

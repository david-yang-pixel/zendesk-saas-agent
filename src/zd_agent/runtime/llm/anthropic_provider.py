"""Anthropic Messages API 的 LLMProvider 实现。"""

from __future__ import annotations

import json
from typing import Any

try:
    from anthropic import AsyncAnthropic
except ImportError as e:
    raise ImportError("请安装 anthropic: uv add anthropic") from e

from anthropic.types.message import Message

from zd_agent.log import get_logger
from zd_agent.runtime.llm.types import LLMProvider, LLMResponse, ToolCallRequest, ToolSchema

logger = get_logger(module="llm.anthropic")


def _tool_schemas_to_anthropic(tools: list[ToolSchema]) -> list[dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description, "input_schema": t.parameters}
        for t in tools
    ]


def _collect_system_text(content: Any) -> str | None:
    if isinstance(content, str):
        return content if content else None
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(parts) if parts else None
    return None


def _extract_system(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    system_chunks: list[str] = []
    rest: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "system":
            text = _collect_system_text(msg.get("content"))
            if text:
                system_chunks.append(text)
        else:
            rest.append(msg)
    if not system_chunks:
        return None, rest
    return "\n\n".join(system_chunks), rest


def _user_content_from_openai(content: Any) -> str | list[dict[str, Any]]:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return json.dumps(content, ensure_ascii=False) if isinstance(content, (dict, list)) else str(content)
    blocks: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            blocks.append({"type": "text", "text": str(part.get("text", ""))})
        else:
            blocks.append(part)
    return blocks if blocks else ""


def _assistant_blocks_from_openai(msg: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    raw_content = msg.get("content")
    if raw_content:
        if isinstance(raw_content, str):
            blocks.append({"type": "text", "text": raw_content})
        elif isinstance(raw_content, list):
            for part in raw_content:
                if isinstance(part, dict) and part.get("type") == "text":
                    blocks.append({"type": "text", "text": str(part.get("text", ""))})
    for tc in msg.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        name = str(fn.get("name", ""))
        raw_args = fn.get("arguments")
        if isinstance(raw_args, str):
            try:
                inp: dict[str, Any] = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError:
                inp = {}
        elif isinstance(raw_args, dict):
            inp = raw_args
        else:
            inp = {}
        blocks.append(
            {
                "type": "tool_use",
                "id": str(tc.get("id", "")),
                "name": name,
                "input": inp,
            }
        )
    return blocks


def _openai_messages_to_anthropic(rest: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in rest:
        role = msg.get("role")
        if role == "tool":
            tc_id = str(msg.get("tool_call_id", ""))
            body = msg.get("content", "")
            if not isinstance(body, str):
                body = json.dumps(body, ensure_ascii=False) if body is not None else ""
            out.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": tc_id, "content": body}
                    ],
                }
            )
        elif role == "assistant":
            blocks = _assistant_blocks_from_openai(msg)
            if blocks:
                out.append({"role": "assistant", "content": blocks})
        elif role == "user":
            out.append({"role": "user", "content": _user_content_from_openai(msg.get("content"))})
        else:
            continue
    return out


def _map_stop_reason(reason: str | None) -> str:
    if reason is None:
        return "stop"
    mapping = {"end_turn": "stop", "tool_use": "tool_calls", "max_tokens": "length"}
    return mapping.get(reason, reason)


def _parse_message_response(response: Message) -> LLMResponse:
    text_parts: list[str] = []
    tool_calls: list[ToolCallRequest] = []
    for block in response.content:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(getattr(block, "text", "") or "")
        elif btype == "tool_use":
            inp = getattr(block, "input", None)
            if not isinstance(inp, dict):
                inp = {}
            tool_calls.append(
                ToolCallRequest(
                    id=str(getattr(block, "id", "")),
                    name=str(getattr(block, "name", "")),
                    arguments=json.dumps(inp, ensure_ascii=False),
                )
            )
    content = "".join(text_parts) if text_parts else None
    if content == "":
        content = None
    usage = response.usage

    # Prompt caching 观测：Anthropic 返回 cache_read_input_tokens（命中）+
    # cache_creation_input_tokens（首次写）。Log 一行供 SigNoz 算命中率。
    if usage:
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
        if cache_read > 0 or cache_create > 0:
            logger.info(
                "metric.anthropic_prompt_cache",
                input_tokens=usage.input_tokens,
                cache_read_tokens=cache_read,
                cache_create_tokens=cache_create,
                cache_hit_rate=round(
                    cache_read / max(usage.input_tokens + cache_read, 1), 3
                ),
            )

    return LLMResponse(
        content=content,
        tool_calls=tool_calls,
        finish_reason=_map_stop_reason(response.stop_reason),
        prompt_tokens=usage.input_tokens if usage else 0,
        completion_tokens=usage.output_tokens if usage else 0,
        raw=response,
    )


class AnthropicProvider(LLMProvider):
    """Anthropic Messages API，负责 OpenAI 风格 messages 与 API 格式的互转。"""

    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int = 8192,
        timeout: int = 120,
    ) -> None:
        self._client = AsyncAnthropic(
            api_key=api_key,
            timeout=float(timeout),
            max_retries=0,
        )
        self._model = model
        self._max_tokens = max_tokens if max_tokens > 0 else 8192

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        system, rest = _extract_system(messages)
        anthropic_messages = _openai_messages_to_anthropic(rest)
        req: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": anthropic_messages,
            "temperature": temperature,
        }
        if system is not None:
            # Prompt caching：system prompt 通常稳定（SOUL+AGENTS+TOOLS+templates
            # ~12K tokens），把它包成 list[block] 加 cache_control=ephemeral 后
            # Anthropic 会 cache 整个 prefix，下次同 prompt 来 cache hit 价格 1/10。
            # 5 分钟 TTL，足够同工单多轮 LLM 调用复用。
            req["system"] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        if tools:
            req["tools"] = _tool_schemas_to_anthropic(tools)
        response = await self._client.messages.create(**req)
        return _parse_message_response(response)

    def format_tool_result(
        self, tool_call_id: str, name: str, content: str
    ) -> dict[str, Any]:
        _ = name
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": content,
                }
            ],
        }

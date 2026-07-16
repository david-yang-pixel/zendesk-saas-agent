"""OpenAI LLM provider with Chat Completions and GPT-5.6 Responses support."""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion

from zd_agent.log import get_logger
from zd_agent.runtime.llm.types import LLMProvider, LLMResponse, ToolCallRequest, ToolSchema
from zd_agent.runtime.openai_reasoning import (
    model_requires_responses_api,
    normalized_model_id,
    validate_reasoning_effort,
)

logger = get_logger(module="llm.openai")

_RESPONSES_OUTPUT_KEY = "_zd_responses_output"


def _tool_schemas_to_openai(tools: list[ToolSchema]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


def _tool_schemas_to_responses(tools: list[ToolSchema]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
            # Existing Zendesk tool schemas are permissive and are not all
            # valid OpenAI strict-mode schemas.
            "strict": False,
        }
        for tool in tools
    ]


def _responses_message_content(content: Any) -> Any:
    if not isinstance(content, list):
        return content
    converted: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            converted.append({"type": "input_text", "text": str(part.get("text", ""))})
            continue
        if part.get("type") == "image_url":
            image = part.get("image_url") or {}
            if not isinstance(image, dict) or not image.get("url"):
                continue
            converted.append(
                {
                    "type": "input_image",
                    "image_url": str(image["url"]),
                    "detail": str(image.get("detail") or "auto"),
                }
            )
    return converted


def _messages_to_responses_input(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for message in messages:
        preserved_output = message.get(_RESPONSES_OUTPUT_KEY)
        if message.get("role") == "assistant" and isinstance(preserved_output, list):
            converted.extend(preserved_output)
            continue

        role = str(message.get("role") or "")
        if role == "tool":
            converted.append(
                {
                    "type": "function_call_output",
                    "call_id": str(message.get("tool_call_id") or ""),
                    "output": str(message.get("content") or ""),
                }
            )
            continue

        content = message.get("content")
        if role in {"system", "developer", "user", "assistant"} and content is not None:
            converted.append(
                {
                    "type": "message",
                    "role": role,
                    "content": _responses_message_content(content),
                }
            )

        if role == "assistant":
            for tool_call in message.get("tool_calls") or []:
                function = tool_call.get("function") if isinstance(tool_call, dict) else None
                if not isinstance(function, dict):
                    continue
                converted.append(
                    {
                        "type": "function_call",
                        "call_id": str(tool_call.get("id") or ""),
                        "name": str(function.get("name") or ""),
                        "arguments": str(function.get("arguments") or "{}"),
                    }
                )
    return converted


def _parse_completion(response: ChatCompletion) -> LLMResponse:
    choice = response.choices[0]
    message = choice.message
    usage = response.usage
    tool_calls: list[ToolCallRequest] = []
    for tc in message.tool_calls or []:
        fn = tc.function
        tool_calls.append(
            ToolCallRequest(id=tc.id, name=fn.name, arguments=fn.arguments)
        )

    # OpenAI gpt-4o / gpt-5.x 自动 prompt caching：> 1024 tokens 的稳定 prefix
    # 自动 cache，cache hit 后 input token 价格 ÷10。日志记 cached_tokens 方便
    # SigNoz 看 cache hit rate（低 → system prompt 不稳定 / 经常变）。
    cached_tokens = 0
    if usage and getattr(usage, "prompt_tokens_details", None):
        cached_tokens = getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0
    if cached_tokens > 0 and usage:
        logger.info(
            "metric.openai_prompt_cache_hit",
            prompt_tokens=usage.prompt_tokens,
            cached_tokens=cached_tokens,
            cache_hit_rate=round(cached_tokens / max(usage.prompt_tokens, 1), 3),
        )

    return LLMResponse(
        content=message.content,
        tool_calls=tool_calls,
        finish_reason=choice.finish_reason or "stop",
        prompt_tokens=usage.prompt_tokens if usage else 0,
        completion_tokens=usage.completion_tokens if usage else 0,
        raw=message.model_dump(),
    )


def _parse_response(response: Any) -> LLMResponse:
    status = str(getattr(response, "status", "") or "")
    if status in {"failed", "cancelled", "queued", "in_progress"}:
        raise RuntimeError(f"OpenAI Responses request ended with status {status}")
    if status == "incomplete":
        details = getattr(response, "incomplete_details", None)
        reason = str(getattr(details, "reason", "") or "unknown")
        raise RuntimeError(
            f"OpenAI Responses request ended incomplete: {reason}"
        )

    output_items = list(getattr(response, "output", None) or [])
    completed = status == "completed"
    tool_calls: list[ToolCallRequest] = []
    if completed:
        for item in output_items:
            if getattr(item, "type", None) != "function_call":
                continue
            tool_calls.append(
                ToolCallRequest(
                    id=str(getattr(item, "call_id", "")),
                    name=str(getattr(item, "name", "")),
                    arguments=str(getattr(item, "arguments", "{}")),
                )
            )

    content = str(getattr(response, "output_text", "") or "") or None
    usage = getattr(response, "usage", None)
    prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
    completion_tokens = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
    input_details = getattr(usage, "input_tokens_details", None) if usage else None
    output_details = getattr(usage, "output_tokens_details", None) if usage else None
    cached_tokens = int(getattr(input_details, "cached_tokens", 0) or 0)
    reasoning_tokens = int(getattr(output_details, "reasoning_tokens", 0) or 0)
    if cached_tokens > 0:
        logger.info(
            "metric.openai_prompt_cache_hit",
            prompt_tokens=prompt_tokens,
            cached_tokens=cached_tokens,
            cache_hit_rate=round(cached_tokens / max(prompt_tokens, 1), 3),
        )

    serialized_output = [
        item.model_dump(exclude_none=True)
        for item in output_items
    ]
    raw: dict[str, Any] = {
        "role": "assistant",
        "content": content,
        _RESPONSES_OUTPUT_KEY: serialized_output,
        "_zd_responses_reasoning_tokens": reasoning_tokens,
    }
    if tool_calls:
        raw["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in tool_calls
        ]

    finish_reason = "tool_calls" if tool_calls else "stop"

    return LLMResponse(
        content=content,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        raw=raw,
    )


class OpenAIProvider(LLMProvider):
    """OpenAI 兼容 Chat Completions 端点（含自建 base_url）。"""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: int = 120,
        max_tokens: int = 0,
        reasoning_effort: str = "",
    ) -> None:
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": float(timeout),
            "max_retries": 0,
        }
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**kwargs)
        self._model = model
        self._max_tokens = max_tokens
        self._reasoning_effort = validate_reasoning_effort(model, reasoning_effort)

    def _uses_responses_api(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None,
    ) -> bool:
        if model_requires_responses_api(self._model):
            return True
        if not normalized_model_id(self._model).startswith("gpt-5.6"):
            return False
        has_responses_history = any(
            isinstance(message.get(_RESPONSES_OUTPUT_KEY), list)
            for message in messages
        )
        return has_responses_history or bool(
            tools
            and self._reasoning_effort
            and self._reasoning_effort != "none"
        )

    async def _responses_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None,
    ) -> LLMResponse:
        request: dict[str, Any] = {
            "model": self._model,
            "input": _messages_to_responses_input(messages),
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        if self._reasoning_effort:
            request["reasoning"] = {"effort": self._reasoning_effort}
        if self._max_tokens > 0:
            has_reasoning = bool(self._reasoning_effort) or model_requires_responses_api(
                self._model
            )
            request["max_output_tokens"] = max(
                self._max_tokens,
                25_000 if has_reasoning else self._max_tokens,
            )
        if tools:
            request["tools"] = _tool_schemas_to_responses(tools)
            request["tool_choice"] = "auto"
            request["parallel_tool_calls"] = True
        response = await self._client.responses.create(**request)
        return _parse_response(response)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        if self._uses_responses_api(messages, tools):
            return await self._responses_chat(messages, tools)

        req: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
        }
        # gpt-5 系列**只支持 default temperature (1.0)**，传 0.2 会 400
        # "Unsupported value: 'temperature' does not support 0.2 with this model"
        # → 检测 gpt-5.x 模型时整个跳过 temperature 字段。其它模型（gpt-4o /
        # gpt-3.5 等）正常传。
        # 观察发现：服务器上线 gpt-5.5 后 #2643 工单 silence_waker dispatch agent
        # 立刻 400 挂掉 → state 卡 processing → 客户永远收不到回复。
        if not normalized_model_id(self._model).startswith("gpt-5"):
            req["temperature"] = temperature
        if self._max_tokens > 0:
            req["max_completion_tokens"] = self._max_tokens
        if self._reasoning_effort:
            req["reasoning_effort"] = self._reasoning_effort
        if tools:
            req["tools"] = _tool_schemas_to_openai(tools)
            req["tool_choice"] = "auto"
            # GPT-5.6 Chat Completions rejects function tools when reasoning is
            # enabled. Configured non-none effort routes through Responses;
            # blank/default stays on Chat Completions with reasoning disabled.
            if (
                normalized_model_id(self._model).startswith("gpt-5.6")
                and not self._reasoning_effort
            ):
                req["reasoning_effort"] = "none"

        response = await self._client.chat.completions.create(**req)
        return _parse_completion(response)

    def format_tool_result(
        self, tool_call_id: str, name: str, content: str
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": content,
        }

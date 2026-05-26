"""OpenAI Chat Completions 的 LLMProvider 实现。"""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion

from zd_agent.log import get_logger
from zd_agent.runtime.llm.types import LLMProvider, LLMResponse, ToolCallRequest, ToolSchema

logger = get_logger(module="llm.openai")


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


class OpenAIProvider(LLMProvider):
    """OpenAI 兼容 Chat Completions 端点（含自建 base_url）。"""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: int = 120,
        max_tokens: int = 0,
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

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
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
        if not self._model.lower().startswith("gpt-5"):
            req["temperature"] = temperature
        if self._max_tokens > 0:
            req["max_completion_tokens"] = self._max_tokens
        if tools:
            req["tools"] = _tool_schemas_to_openai(tools)
            req["tool_choice"] = "auto"

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

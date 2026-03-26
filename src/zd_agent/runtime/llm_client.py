"""OpenAI API 封装：支持 function calling、重试、超时控制"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, RateLimitError
from openai.types.chat import ChatCompletion

from zd_agent.config import EnvSettings, get_config
from zd_agent.log import get_logger
from zd_agent.runtime.retry import get_retry_policy, retry_async

logger = get_logger(module="llm_client")


@dataclass
class LLMResponse:
    """LLM 单次调用结果"""

    content: str | None
    tool_calls: Sequence[Any]
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int
    raw_message: dict[str, Any]

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class LLMClient:
    """OpenAI Chat Completions 客户端，内置重试和超时处理"""

    def __init__(self) -> None:
        cfg = get_config()
        env = EnvSettings()

        client_kwargs: dict[str, Any] = {
            "api_key": env.openai_api_key,
            "timeout": float(cfg.llm.timeout),
            "max_retries": 0,
        }
        if cfg.llm.base_url:
            client_kwargs["base_url"] = cfg.llm.base_url

        self._client = AsyncOpenAI(**client_kwargs)
        self._model = cfg.llm.model
        self._temperature = cfg.llm.temperature

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """发送一次 chat completion 请求，内置指数退避重试"""
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
        }

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        logger.debug(
            "llm_request",
            model=self._model,
            message_count=len(messages),
            tool_count=len(tools) if tools else 0,
        )

        async def _call() -> LLMResponse:
            response: ChatCompletion = await self._client.chat.completions.create(**kwargs)

            choice = response.choices[0]
            message = choice.message
            usage = response.usage

            result = LLMResponse(
                content=message.content,
                tool_calls=list(message.tool_calls or []),
                finish_reason=choice.finish_reason or "stop",
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                raw_message=message.model_dump(),
            )

            logger.debug(
                "llm_response",
                finish_reason=result.finish_reason,
                has_tool_calls=result.has_tool_calls,
                tool_call_count=len(result.tool_calls),
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
            )
            return result

        return await retry_async(
            operation="openai_chat_completion",
            policy=get_retry_policy("llm"),
            func=_call,
            should_retry=lambda e: isinstance(
                e, (APITimeoutError, APIConnectionError, RateLimitError)
            ),
            log_context={"model": self._model},
        )

"""LLM 客户端 facade：保持向后兼容的接口，内部委托给 LLMProvider。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from openai import APIConnectionError, APITimeoutError, RateLimitError

from zd_agent.config import EnvSettings, get_config
from zd_agent.log import get_logger
from zd_agent.runtime.llm.factory import create_llm_provider
from zd_agent.runtime.llm.types import LLMProvider, LLMResponse as ProviderLLMResponse, ToolSchema
from zd_agent.runtime.retry import get_retry_policy, retry_async

logger = get_logger(module="llm_client")


@dataclass
class LLMResponse:
    """向后兼容的 LLM 响应（tool_loop.py 等模块仍在使用）"""

    content: str | None
    tool_calls: Sequence[Any]
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int
    raw_message: dict[str, Any]

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


def _provider_response_to_legacy(resp: ProviderLLMResponse) -> LLMResponse:
    """将新的 ProviderLLMResponse 转为旧的 LLMResponse 格式"""
    return LLMResponse(
        content=resp.content,
        tool_calls=resp.tool_calls,
        finish_reason=resp.finish_reason,
        prompt_tokens=resp.prompt_tokens,
        completion_tokens=resp.completion_tokens,
        raw_message=resp.raw if isinstance(resp.raw, dict) else {},
    )


class LLMClient:
    """向后兼容的 LLM 客户端，内部委托给 LLMProvider。

    tool_loop.py 和 compaction.py 中的调用方式不需要任何改动。
    同时暴露 _provider 属性供新代码（agent_loop 等）直接使用 Provider。
    """

    def __init__(self) -> None:
        cfg = get_config()
        env = EnvSettings()
        self._provider: LLMProvider = create_llm_provider(cfg.llm, env)
        self._model = cfg.llm.model
        self._temperature = cfg.llm.temperature

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """向后兼容入口：接受 OpenAI 格式的 tools dict，返回旧版 LLMResponse"""
        tool_schemas: list[ToolSchema] | None = None
        if tools:
            tool_schemas = [
                ToolSchema(
                    name=t["function"]["name"],
                    description=t["function"]["description"],
                    parameters=t["function"]["parameters"],
                )
                for t in tools
            ]

        logger.debug(
            "llm_request",
            model=self._model,
            message_count=len(messages),
            tool_count=len(tools) if tools else 0,
        )

        async def _call() -> LLMResponse:
            resp = await self._provider.chat(
                messages, tools=tool_schemas, temperature=self._temperature,
            )
            logger.debug(
                "llm_response",
                finish_reason=resp.finish_reason,
                has_tool_calls=resp.has_tool_calls,
                tool_call_count=len(resp.tool_calls),
                prompt_tokens=resp.prompt_tokens,
                completion_tokens=resp.completion_tokens,
            )
            return _provider_response_to_legacy(resp)

        return await retry_async(
            operation="llm_chat",
            policy=get_retry_policy("llm"),
            func=_call,
            should_retry=lambda e: isinstance(
                e, (APITimeoutError, APIConnectionError, RateLimitError)
            ),
            log_context={"model": self._model},
        )

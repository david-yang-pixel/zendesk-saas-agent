"""根据配置创建 LLMProvider 实例。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zd_agent.config import EnvSettings, LLMConfig

from zd_agent.runtime.llm.types import LLMProvider


def create_llm_provider(cfg: LLMConfig, env: EnvSettings) -> LLMProvider:
    """根据 cfg.provider 字段选择并实例化对应 Provider。"""
    if cfg.provider == "openai":
        from zd_agent.runtime.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(
            api_key=env.openai_api_key,
            model=cfg.model,
            base_url=cfg.base_url,
            timeout=cfg.timeout,
            max_tokens=cfg.max_tokens,
            reasoning_effort=cfg.reasoning_effort,
        )
    elif cfg.provider == "anthropic":
        from zd_agent.runtime.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            api_key=env.anthropic_api_key,
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            timeout=cfg.timeout,
        )
    else:
        raise ValueError(f"不支持的 LLM provider: {cfg.provider}")

"""根据配置创建 LLMProvider 实例。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zd_agent.config import EnvSettings, LLMConfig
    from zd_agent.runtime.config_snapshot import LLMRuntimeSnapshot

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


def create_llm_provider_from_snapshot(
    snapshot: LLMRuntimeSnapshot,
) -> LLMProvider:
    """Create a child provider from its frozen slot without environment fallback."""

    if snapshot.provider == "openai":
        from zd_agent.runtime.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(
            api_key=snapshot.api_key,
            model=snapshot.model,
            base_url=snapshot.base_url,
            timeout=snapshot.timeout,
            max_tokens=snapshot.max_tokens,
            reasoning_effort=snapshot.reasoning_effort,
        )
    if snapshot.provider == "anthropic":
        from zd_agent.runtime.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            api_key=snapshot.api_key,
            model=snapshot.model,
            max_tokens=snapshot.max_tokens,
            timeout=snapshot.timeout,
        )
    raise ValueError(f"不支持的 Subagent LLM provider: {snapshot.provider}")

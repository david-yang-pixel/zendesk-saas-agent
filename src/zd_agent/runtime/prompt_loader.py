"""Prompt 加载器：读取 prompt 文件 + 委托给 PromptBuilder 组装。"""

from __future__ import annotations

from importlib import resources
from typing import Any


def load_prompt(filename: str) -> str:
    """从 prompts 包中加载 prompt 文件"""
    ref = resources.files("zd_agent.prompts").joinpath(filename)
    return ref.read_text(encoding="utf-8")


def build_system_prompt(**kwargs: Any) -> str:
    """组装完整的 system prompt。

    无参数调用时行为与旧版完全一致（向后兼容）。
    传入关键字参数可注入动态上下文（provider、ticket_type、strategy_content 等）。
    """
    from zd_agent.runtime.prompt.builder import PromptBuilder
    from zd_agent.runtime.prompt.defaults import register_default_sections
    from zd_agent.runtime.prompt.section import PromptContext

    builder = PromptBuilder()
    register_default_sections(builder)
    ctx = PromptContext(
        current_date=kwargs.pop("current_date", ""),
        **kwargs,
    )
    return builder.build(ctx)


def build_static_system_prompt() -> str:
    """招法 4: 只装静态段（不含 ticket-specific 内容）的 system prompt。

    用于实现 prompt cache 拉满：system prompt 跨 ticket byte-identical →
    OpenAI / Anthropic prefix cache 命中率 ~95%，input token 价格 ÷10。

    动态段（date / strategy / language / followup / customer_profile）通过
    ``build_dynamic_user_context()`` 单独生成，由 caller 拼到首条 user
    message 头部。
    """
    from zd_agent.runtime.prompt.builder import PromptBuilder
    from zd_agent.runtime.prompt.defaults import register_static_sections
    from zd_agent.runtime.prompt.section import PromptContext

    builder = PromptBuilder()
    register_static_sections(builder)
    ctx = PromptContext()  # 空 context；静态段不依赖 ctx 数据
    return builder.build(ctx)


def build_dynamic_user_context(**kwargs: Any) -> str:
    """招法 4 配套：把 ticket-specific 段生成为 user-message 前缀文本。

    所有"每 ticket 不同 / 每天变化"的 prompt 内容（date / strategy /
    language hint / followup record / customer_profile）这里聚合，跟
    static system prompt 分开。

    返回空串表示无任何动态上下文（向后兼容）。
    """
    from zd_agent.runtime.prompt.builder import PromptBuilder
    from zd_agent.runtime.prompt.defaults import register_dynamic_sections
    from zd_agent.runtime.prompt.section import PromptContext

    builder = PromptBuilder()
    register_dynamic_sections(builder)
    ctx = PromptContext(
        current_date=kwargs.pop("current_date", ""),
        **kwargs,
    )
    return builder.build(ctx)

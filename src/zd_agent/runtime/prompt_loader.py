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

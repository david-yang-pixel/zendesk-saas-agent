"""默认 prompt 段注册，等价于当前 build_system_prompt() 的无参行为。"""

from __future__ import annotations

from zd_agent.runtime.prompt.builder import PromptBuilder
from zd_agent.runtime.prompt.section import PromptContext, PromptSection
from zd_agent.runtime.prompt_loader import load_prompt


def register_default_sections(builder: PromptBuilder) -> None:
    """注册默认 prompt 段（SOUL / AGENTS / TOOLS + 可选动态段）。"""
    builder.register(
        PromptSection(
            name="soul",
            content=load_prompt("SOUL.md"),
            order=10,
        )
    )
    builder.register(
        PromptSection(
            name="agents",
            content=load_prompt("AGENTS.md"),
            order=20,
        )
    )
    builder.register(
        PromptSection(
            name="tools",
            content=load_prompt("TOOLS.md"),
            order=30,
        )
    )
    builder.register(
        PromptSection(
            name="strategy",
            content=lambda ctx: f"## 当前工单处理策略\n\n{ctx.strategy_content}",
            order=25,
            condition=lambda ctx: bool(ctx.strategy_content),
        )
    )
    builder.register(
        PromptSection(
            name="date_context",
            content=lambda ctx: f"当前日期: {ctx.current_date}",
            order=5,
            condition=lambda ctx: bool(ctx.current_date),
        )
    )

    _LANG_NAMES = {"zh": "中文", "en": "English", "ja": "日本語"}

    builder.register(
        PromptSection(
            name="reply_language_override",
            content=lambda ctx: (
                f"⚠️ 重要：本工单客户使用 **{_LANG_NAMES.get(ctx.customer_language, ctx.customer_language)}** 沟通。"
                f"你的 Internal Note 全文（内部分析、客户回复建议、人工复核指引）都必须使用"
                f" **{_LANG_NAMES.get(ctx.customer_language, ctx.customer_language)}** 撰写。"
                f"代码块和命令行内容不受此限制。"
            ),
            order=40,
            condition=lambda ctx: bool(ctx.customer_language),
        )
    )

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
                f"初步检测客户可能使用 **{_LANG_NAMES.get(ctx.customer_language, ctx.customer_language)}** 沟通"
                f"（基于字符统计，仅供参考）。"
                f"请你结合对话全文独立判断客户实际使用的语言，"
                f"并使用客户的语言撰写 Internal Note 正文内容"
                f"（Suggested Reply、Internal Analysis、Review Guide 各段内容）。"
                f"代码块和命令行内容不受此限制。"
            ),
            order=40,
            condition=lambda ctx: bool(ctx.customer_language),
        )
    )

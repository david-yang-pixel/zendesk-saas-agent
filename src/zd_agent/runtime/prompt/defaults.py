"""默认 prompt 段注册，等价于当前 build_system_prompt() 的无参行为。"""

from __future__ import annotations

from pathlib import Path

from zd_agent.runtime.prompt.builder import PromptBuilder
from zd_agent.runtime.prompt.section import PromptContext, PromptSection
from zd_agent.runtime.prompt_loader import load_prompt


def _db_schema_path() -> Path:
    """运行时生成的数据库 schema cheat sheet。

    由 ``scripts/dump_db_schema.sh`` 周期性 introspect prod 库写入。
    不存在时（开发环境）section 自动跳过。
    """
    # 延迟 import 避免顶层 config 副作用
    from zd_agent.config import get_config

    cfg = get_config()
    return (
        Path(cfg.resources.base_dir)
        / cfg.resources.knowledge_base_dir
        / "database_schema.md"
    )


def _load_db_schema() -> str:
    p = _db_schema_path()
    try:
        return p.read_text(encoding="utf-8") if p.exists() else ""
    except Exception:
        return ""


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

    # COMMON_PLAYBOOK — always loaded (after AGENTS, before strategy)
    builder.register(
        PromptSection(
            name="common_playbook",
            content=load_prompt("playbook/COMMON_PLAYBOOK.md"),
            order=22,
        )
    )

    # SQL_TEMPLATES — 每个工单都加载（不再按 category gate）
    # 之前按 ticket_type ∈ {platform_issue,...} 条件加载，但 runner.py 调用时
    # 没传 ticket_type → ctx.ticket_type 永远是空 → 这段 prompt 实际从未生效。
    # 现按用户要求："就按照 prompt 里面的流程处理"，全部段落始终加载。
    builder.register(
        PromptSection(
            name="sql_templates",
            content=load_prompt("sql/SQL_TEMPLATES.md"),
            order=26,
        )
    )

    # DB_SCHEMA — 运行时从生产库 introspect 出来的真实 schema cheat sheet。
    # 由 scripts/dump_db_schema.sh 周期生成到
    # /opt/zd-agent/resources/saas版知识库/database_schema.md。
    # 文件不存在（开发机 / 还没跑过脚本）则 section 自动跳过。
    builder.register(
        PromptSection(
            name="db_schema",
            content=lambda ctx: _load_db_schema(),
            order=27,
            condition=lambda ctx: bool(_load_db_schema()),
        )
    )

    # REFUND_FLOW — 每个工单都加载
    builder.register(
        PromptSection(
            name="refund_flow",
            content=load_prompt("refund/REFUND_FLOW.md"),
            order=27,
        )
    )

    # REPLY_TEMPLATES — always loaded (reply drafting is on every ticket)
    builder.register(
        PromptSection(
            name="reply_templates",
            content=load_prompt("templates/REPLY_TEMPLATES.md"),
            order=35,
        )
    )

    # DEV_REPORT — 每个工单都加载
    builder.register(
        PromptSection(
            name="dev_report",
            content=load_prompt("templates/DEV_REPORT.md"),
            order=36,
        )
    )

    # followup_context — when session.mode == "followup", inject previous record
    builder.register(
        PromptSection(
            name="followup_context",
            content=lambda ctx: (
                f"## 跟进模式 · 历史分析记录\n\n"
                f"本工单已有历史分析，**不要重复已有分析**。只处理客户新增对话。\n\n"
                f"### 上次分析记录\n\n{ctx.previous_record}\n\n"
                f"### 本次新增客户对话\n\n{ctx.new_comments_diff or '(无)'}"
            ),
            order=15,  # right after SOUL, before AGENTS
            condition=lambda ctx: ctx.mode == "followup" and bool(ctx.previous_record),
        )
    )

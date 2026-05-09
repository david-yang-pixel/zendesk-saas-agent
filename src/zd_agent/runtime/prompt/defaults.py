"""默认 prompt 段注册，等价于当前 build_system_prompt() 的无参行为。"""

from __future__ import annotations

from pathlib import Path

from zd_agent.runtime.prompt.builder import PromptBuilder
from zd_agent.runtime.prompt.section import PromptContext, PromptSection
from zd_agent.runtime.prompt_loader import load_prompt


def _db_schema_path() -> Path:
    """运行时生成的数据库 schema cheat sheet。

    由 ``scripts/dump_db_schema.sh`` 周期性 introspect prod 库写入。
    不存在时（开发环境）此文件可能为空。

    注：此函数现也被 ``tools/classify.py`` 调用（lazy load DB schema 到 tool
    result），所以保留 module 级 helper。
    """
    # 延迟 import 避免顶层 config 副作用
    from zd_agent.config import get_config

    cfg = get_config()
    return (
        Path(cfg.resources.base_dir)
        / cfg.resources.knowledge_base_dir
        / "database_schema.md"
    )


def load_db_schema() -> str:
    """读取生产库 schema cheat sheet；不存在或读失败返回空串。

    Public API：classify.py 在 ticket_type 需要 SQL 时调用此函数注入。
    """
    p = _db_schema_path()
    try:
        return p.read_text(encoding="utf-8") if p.exists() else ""
    except Exception:
        return ""


# 向后兼容旧 section 引用（已弃用，新代码请用 load_db_schema）
_load_db_schema = load_db_schema


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

    def _reply_language_section(ctx: PromptContext) -> str:
        detected = _LANG_NAMES.get(ctx.customer_language, ctx.customer_language)
        # 二选一规则：客户中文 → 中文；其他一律 English（不强行说葡/日等小语种）
        is_zh = ctx.customer_language == "zh"
        target = "中文" if is_zh else "English"
        return (
            f"初步检测客户可能使用 **{detected}** 沟通（基于字符统计，仅供参考）。"
            f"请结合对话全文独立判断客户实际使用的语言。\n\n"
            f"**客户回复输出语言（二选一）**：客户用中文 → 用中文回复；"
            f"客户用任何其他语言（English / 日本語 / Português / Español / 한국어 / ...）→ "
            f"一律用 **English** 回复。**绝不 EN + 中文双语对照**，**绝不让 agent 输出日语 / 葡语等非中英文**（质量不可控）。\n\n"
            f"基于上述检测，本工单**Suggested Reply 段应使用 {target}**。\n"
            f"Internal Analysis / Review Guide 段如客户是中文则用中文，否则用中文（运维同事母语）。\n"
            f"代码块和命令行内容不受语言限制。"
        )

    builder.register(
        PromptSection(
            name="reply_language_override",
            content=_reply_language_section,
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

    # SQL_TEMPLATES + DB_SCHEMA + REFUND_FLOW —— 不再 always-load 进 system
    # prompt，改为 classify_ticket 工具返回时按 ticket_type 选择性附加到 tool
    # result（见 tools/classify.py）。
    #
    # 为什么不直接做"按 ctx.ticket_type 条件 load 进 system prompt"：
    #   - system prompt 第一次构造时（runner.py:88）ticket_type 还没有值，
    #     classify 在第一轮 tool call 才出来
    #   - 如果 classify 后重写 system prompt，prompt cache 会失效（OpenAI/
    #     Anthropic 都按 system prefix 哈希），第二轮起 cache miss 反而更贵
    #   - 通过 tool result 注入：system prompt 永远固定 → cache 命中
    #     ticket-specific 内容随 tool message 进入 history，下一轮 cache 仍续命
    #
    # 收益：refund / product_usage / insufficient_info 等不需要 SQL 的工单，
    # system prompt 省 ~9-10k tokens × 100% 工单首次调用。

    # ⚠️ 历史遗留 section 已被 classify.py 接管，此处保留 stub 占位避免
    # 别的地方意外引用——实际不再注册任何内容。

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

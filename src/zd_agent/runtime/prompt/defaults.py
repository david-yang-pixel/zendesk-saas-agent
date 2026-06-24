"""默认 prompt 段注册，等价于当前 build_system_prompt() 的无参行为。"""

from __future__ import annotations

from pathlib import Path

from zd_agent.runtime.prompt.builder import PromptBuilder
from zd_agent.runtime.prompt.section import PromptContext, PromptSection
from zd_agent.runtime.prompt_loader import load_prompt


def _db_schema_path() -> Path:
    """运行时生成的数据库 schema cheat sheet。

    由同级 ``db-gateway/scripts/dump_db_schema.sh`` 周期性 introspect prod 库写入。
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


def register_static_sections(builder: PromptBuilder) -> None:
    """注册**只 byte-identical 跨工单**的静态 prompt 段。

    招法 4: 这些段进 system prompt，让 OpenAI/Anthropic prefix cache 拉满。
    动态段（date / strategy / language / followup / customer_profile）请
    用 register_dynamic_sections。
    """
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
            name="common_playbook",
            content=load_prompt("playbook/COMMON_PLAYBOOK.md"),
            order=22,
        )
    )
    builder.register(
        PromptSection(
            name="reply_templates",
            content=load_prompt("templates/REPLY_TEMPLATES.md"),
            order=35,
        )
    )
    builder.register(
        PromptSection(
            name="dev_report",
            content=load_prompt("templates/DEV_REPORT.md"),
            order=36,
        )
    )


def register_dynamic_sections(builder: PromptBuilder) -> None:
    """注册**每 ticket / 每天可能变**的动态 prompt 段。

    招法 4: 这些段单独生成为 user-message 前缀文本，不进 system prompt，
    保证 system prompt 跨工单 byte-identical → cache 拉满。
    """
    builder.register(
        PromptSection(
            name="date_context",
            content=lambda ctx: f"当前日期: {ctx.current_date}",
            order=5,
            condition=lambda ctx: bool(ctx.current_date),
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

    _LANG_NAMES = {"zh": "中文", "en": "English", "ja": "日本語"}

    def _reply_language_section(ctx: PromptContext) -> str:
        detected = _LANG_NAMES.get(ctx.customer_language, ctx.customer_language)
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
    builder.register(
        PromptSection(
            name="followup_context",
            content=lambda ctx: (
                f"## 跟进模式 · 最新 Zendesk 对话优先\n\n"
                f"Zendesk 最新对话是唯一真相源；本地工单记录只作为参考，"
                f"不能覆盖或代替最新客户消息。只处理下面确认过的新增客户对话，"
                f"不要重复已有分析。\n\n"
                f"### 上次分析记录\n\n{ctx.previous_record}\n\n"
                f"### 本次新增客户对话\n\n{ctx.new_comments_diff or '(无)'}"
            ),
            order=15,
            condition=lambda ctx: (
                ctx.mode == "followup"
                and bool(ctx.previous_record)
                and bool(ctx.new_comments_diff)
            ),
        )
    )

    def _customer_profile_section(ctx: PromptContext) -> str:
        p = ctx.customer_profile or {}
        email = p.get("requester_email", "")
        recent_ids = p.get("recent_ticket_ids") or []
        count = p.get("recent_ticket_count", 0)
        lines = [
            "## 客户画像（dispatch 前预拉，供 agent 决策参考）",
            "",
            f"- requester: {email or '(未知)'}",
        ]
        if count > 0:
            lines.append(
                f"- 过去 30 天同 requester SaaS 工单 **{count}** 个："
                f"{', '.join('#' + str(t) for t in recent_ids)}"
            )
            lines.append(
                "- **强烈建议**：在你写 Internal Note 前，至少 `ticket_record_read` "
                "或 `zendesk_get_ticket` 看 1-2 个历史工单 — 该 requester 已多次"
                "联系，可能在追同一个问题或有相关上下文。**不要重复 ask 客户**"
                "已经在历史工单里回答过的信息。"
            )
        else:
            lines.append("- 过去 30 天无历史 SaaS 工单 → 新客户，按常规流程处理")
        return "\n".join(lines)

    builder.register(
        PromptSection(
            name="customer_profile",
            content=_customer_profile_section,
            order=90,
            condition=lambda ctx: bool(ctx.customer_profile),
        )
    )


def register_default_sections(builder: PromptBuilder) -> None:
    """注册默认 prompt 段（向后兼容包装：static + dynamic 一起）。

    新代码应使用 register_static_sections + register_dynamic_sections
    分离静态/动态以实现 cache 优化（招法 4）。
    """
    register_static_sections(builder)
    register_dynamic_sections(builder)

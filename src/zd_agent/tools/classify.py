"""工单分类工具：SaaS 版分类（对齐 skill 01-08 知识库）。"""
from __future__ import annotations

from importlib import resources
from typing import Any

from zd_agent.log import get_logger
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolResult

logger = get_logger(module="classify")

# SaaS categories (aligned with skill 01-08 KB):
VALID_TYPES = {
    "platform_issue",
    "knowledge_base",
    "workflow",
    "api",
    "permissions",
    "product_usage",
    "billing",
    "refund",
    "insufficient_info",
    "mention",
    "general",
}
DEFAULT_TYPE = "general"

# Lazy-load 矩阵：每个 ticket_type 在 classify 后需要附加哪些"重型 prompt 段"
# 到 tool result（不进 system prompt 以保 prompt cache）。
#
# - SQL_TEMPLATES + DB_SCHEMA: 需要查 PG/billing 库的类目
# - REFUND_FLOW: 只有 refund 工单需要
# - 其他类目（product_usage / insufficient_info / mention / general / api）
#   完全不附加，节省 ~9-10k tokens
NEEDS_SQL = {"platform_issue", "billing", "workflow", "knowledge_base", "permissions", "refund"}
NEEDS_REFUND_FLOW = {"refund"}


def _load_strategy(ticket_type: str) -> str:
    filename = f"{ticket_type}.md"
    ref = resources.files("zd_agent.prompts.strategies").joinpath(filename)
    return ref.read_text(encoding="utf-8")


def _load_prompt_file(package: str, filename: str) -> str:
    """从 prompts 子包读 .md 文件；失败返回空串。"""
    try:
        ref = resources.files(package).joinpath(filename)
        return ref.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("prompt_file_load_failed", package=package, filename=filename, error=str(e))
        return ""


def _build_lazy_sections(ticket_type: str) -> str:
    """根据 ticket_type 拼装本次工单需要的"重型 prompt 段"（lazy load）。

    返回拼好的 markdown，调用方（classify tool）会把它附加到 tool result，
    LLM 在下一轮就能看到。
    """
    parts: list[str] = []

    if ticket_type in NEEDS_SQL:
        sql_md = _load_prompt_file("zd_agent.prompts.sql", "SQL_TEMPLATES.md")
        if sql_md:
            parts.append(f"## SQL 查询模板（lazy-loaded）\n\n{sql_md}")

        # DB schema 是运行时生成的，路径不在 package 内
        from zd_agent.runtime.prompt.defaults import load_db_schema
        db_schema = load_db_schema()
        if db_schema:
            parts.append(f"## 生产库 Schema 速查（lazy-loaded）\n\n{db_schema}")

    if ticket_type in NEEDS_REFUND_FLOW:
        refund_md = _load_prompt_file("zd_agent.prompts.refund", "REFUND_FLOW.md")
        if refund_md:
            parts.append(f"## 退款流程（lazy-loaded）\n\n{refund_md}")

    return "\n\n---\n\n".join(parts)


class ClassifyTicket(Tool):
    name = "classify_ticket"
    description = (
        "信息收集和版本确认完成后，对工单进行 SaaS 场景分类。分类结果决定后续"
        "加载哪些针对性策略 prompt（如 refund → 退款流程）。"
        "必须在 First-Pass Intake 完成后尽早调用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "ticket_type": {
                "type": "string",
                "enum": sorted(VALID_TYPES),
                "description": (
                    "工单类型（SaaS 场景）：\n"
                    "- platform_issue: 平台故障 / 稳定性 / 5xx / 超时 / WAF\n"
                    "- knowledge_base: 数据集 / 文档 / 检索 / top_k / 向量\n"
                    "- workflow: 工作流 / 节点 / 触发 / 调度\n"
                    "- api: API / endpoint / SDK / 调用错误\n"
                    "- permissions: 权限 / 成员 / 组织 / SSO\n"
                    "- product_usage: 产品使用 / 功能咨询 / how-to\n"
                    "- billing: 计费 / 订阅 / 配额\n"
                    "- refund: 退款 / 取消订阅（特殊流程）\n"
                    "- insufficient_info: 客户信息不全，需补充\n"
                    "- mention: support engineer @saas-bot 触发的工单，聚焦 hint\n"
                    "- general: 无法明确归类 / 复合问题"
                ),
            },
            "reasoning": {
                "type": "string",
                "description": "分类依据：简要说明为什么选择这个类型",
            },
        },
        "required": ["ticket_type", "reasoning"],
    }

    group = "workflow"

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        ticket_type = args.get("ticket_type", DEFAULT_TYPE)
        reasoning = args.get("reasoning", "")

        if ticket_type not in VALID_TYPES:
            logger.warning(
                "invalid_ticket_type",
                ticket_id=session.ticket_id,
                provided=ticket_type,
                fallback=DEFAULT_TYPE,
            )
            ticket_type = DEFAULT_TYPE

        session.ticket_type = ticket_type
        session.classification_reasoning = reasoning

        logger.info(
            "ticket_classified",
            ticket_id=session.ticket_id,
            ticket_type=ticket_type,
            reasoning=reasoning,
        )

        try:
            strategy = _load_strategy(ticket_type)
        except Exception as e:
            logger.warning(
                "strategy_load_failed",
                ticket_id=session.ticket_id,
                ticket_type=ticket_type,
                error=str(e),
            )
            return ToolResult(
                output=f"工单已分类为 [{ticket_type}]，但策略文件加载失败；按默认 SaaS 流程继续。",
            )

        # Lazy-load 重型段：SQL_TEMPLATES + DB_SCHEMA + REFUND_FLOW 不再
        # always-load 进 system prompt（per改动 3），改为按 ticket_type 选择性
        # 附加到 tool result。这样：
        #   - refund/product_usage/insufficient_info/mention/api/general 类工单
        #     完全不加载这些段 → 节省 ~9-10k tokens
        #   - 需要查 SQL 的类（platform_issue/billing/workflow/...）按需注入
        #   - system prompt 永远固定 → prompt cache 不失效
        lazy_sections = _build_lazy_sections(ticket_type)
        lazy_section_count = lazy_sections.count("## ") if lazy_sections else 0
        logger.info(
            "classify_lazy_sections_attached",
            ticket_id=session.ticket_id,
            ticket_type=ticket_type,
            lazy_section_count=lazy_section_count,
            lazy_chars=len(lazy_sections),
        )

        output_parts = [
            f"工单已分类为 **{ticket_type}**",
            f"分类依据：{reasoning}",
            "---",
            f"## 针对性处理策略\n\n{strategy}",
        ]
        if lazy_sections:
            output_parts.append("---")
            output_parts.append(lazy_sections)

        return ToolResult(output="\n\n".join(output_parts))

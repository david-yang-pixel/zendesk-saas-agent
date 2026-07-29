"""工单分类工具：SaaS 版分类（对齐 skill 01-08 知识库）。"""
from __future__ import annotations

from typing import Any

from zd_agent.log import get_logger
from zd_agent.runtime.prompt.skill_context import (
    DEFAULT_TICKET_TYPE,
    TICKET_TYPE_SET,
    build_ticket_skill_context,
    load_ticket_strategy,
    normalize_ticket_type,
)
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolResult

logger = get_logger(module="classify")

VALID_TYPES = TICKET_TYPE_SET
DEFAULT_TYPE = DEFAULT_TICKET_TYPE

class ClassifyTicket(Tool):
    name = "classify_ticket"
    description = (
        "仅在 zendesk_get_ticket 已成功读取最新工单后进行 SaaS 场景分类，"
        "并加载该类别的精简策略。基础 Skill 已在 system prompt 中，不会重复注入。"
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
        if not isinstance(ticket_type, str):
            return ToolResult(success=False, error="ticket_type must be a string")
        if not isinstance(reasoning, str):
            return ToolResult(success=False, error="reasoning must be a string")
        provided_ticket_type = ticket_type.strip().lower()
        ticket_type = normalize_ticket_type(provided_ticket_type)
        reasoning = reasoning.strip()
        if not reasoning:
            return ToolResult(success=False, error="reasoning is empty")
        if session.context is None:
            return ToolResult(
                success=False,
                error=(
                    "Fresh Zendesk context is required before classification. "
                    "Call zendesk_get_ticket successfully, then call "
                    "classify_ticket in the next tool round."
                ),
            )

        if ticket_type != provided_ticket_type:
            logger.warning(
                "invalid_ticket_type",
                ticket_id=session.ticket_id,
                provided=provided_ticket_type,
                fallback=DEFAULT_TYPE,
            )

        if (
            session.ticket_type == ticket_type
            and bool(session.classification_reasoning)
        ):
            logger.info(
                "ticket_classification_reused",
                ticket_id=session.ticket_id,
                ticket_type=ticket_type,
            )
            return ToolResult(
                output=(
                    f"工单已分类为 **{ticket_type}**，对应类型策略已注入；"
                    "无需重复加载。只有最新 Zendesk 内容改变了场景时才重新分类。"
                )
            )

        session.ticket_type = ticket_type
        session.classification_reasoning = reasoning

        logger.info(
            "ticket_classified",
            ticket_id=session.ticket_id,
            ticket_type=ticket_type,
            reasoning=reasoning,
        )

        try:
            strategy = load_ticket_strategy(ticket_type)
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

        skill_context = build_ticket_skill_context(
            ticket_type=ticket_type,
            strategy=strategy,
        )
        output_parts = [
            f"工单已分类为 **{ticket_type}**",
            f"分类依据：{reasoning}",
            "---",
            skill_context,
        ]

        return ToolResult(output="\n\n".join(output_parts))

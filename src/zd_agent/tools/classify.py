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


def _load_strategy(ticket_type: str) -> str:
    filename = f"{ticket_type}.md"
    ref = resources.files("zd_agent.prompts.strategies").joinpath(filename)
    return ref.read_text(encoding="utf-8")


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

        return ToolResult(
            output=(
                f"工单已分类为 **{ticket_type}**\n"
                f"分类依据：{reasoning}\n\n"
                f"---\n\n"
                f"## 针对性处理策略\n\n{strategy}"
            ),
        )

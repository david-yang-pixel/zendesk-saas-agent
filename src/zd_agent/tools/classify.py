"""工单分类工具：根据信息收集结果对工单分类，返回针对性处理策略"""

from __future__ import annotations

from importlib import resources
from typing import Any

from zd_agent.log import get_logger
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolResult

logger = get_logger(module="classify")

VALID_TYPES = {"config-deploy", "code-bug", "usage-howto", "general", "mention"}
DEFAULT_TYPE = "general"


def _load_strategy(ticket_type: str) -> str:
    """从 prompts/strategies/ 加载策略文件"""
    filename = f"{ticket_type}.md"
    ref = resources.files("zd_agent.prompts.strategies").joinpath(filename)
    return ref.read_text(encoding="utf-8")


class ClassifyTicket(Tool):
    name = "classify_ticket"
    description = (
        "信息收集和版本确认完成后，对工单进行分类并获取针对性的处理策略。"
        "必须在 Step 2 完成后、Step 3 开始前调用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "ticket_type": {
                "type": "string",
                "enum": sorted(VALID_TYPES),
                "description": (
                    "工单类型：config-deploy（配置/部署/环境）、"
                    "code-bug（代码缺陷/回归）、"
                    "usage-howto（使用方法/功能咨询）、"
                    "mention（@mention 触发，聚焦回答 hint 中的问题）、"
                    "general（无法明确归类/复合问题）"
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

        # 记录到 session
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
                output=f"工单已分类为 [{ticket_type}]，但策略文件加载失败，请按默认流程继续。",
            )

        return ToolResult(
            output=(
                f"工单已分类为 **{ticket_type}**\n"
                f"分类依据：{reasoning}\n\n"
                f"---\n\n"
                f"## 针对性处理策略\n\n{strategy}"
            ),
        )

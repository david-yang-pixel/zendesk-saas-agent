"""FeishuPush tool: let the agent proactively push cards to Feishu."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from zd_agent.integrations.notification_service import push_notification_card
from zd_agent.log import get_logger
from zd_agent.runtime.image_evidence import guard_against_stale_no_image_claim
from zd_agent.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from zd_agent.session.agent_session import AgentSession

logger = get_logger(module="tools.feishu")


class FeishuPush(Tool):
    name = "feishu_push"
    description = (
        "主动推送消息到飞书群。card_type 选项："
        "ticket_done（工单处理完成摘要卡片，content 用固定字段：处理结果/关键事实/数据库核查/代码路径/日志核查/客户回复/后续动作/注意事项）/"
        "ticket_escalate（需人工介入，需 zendesk_url）/"
        "alert（通用告警，不附 zendesk 链接，urgent=true 时 @ 所有人）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "card_type": {
                "type": "string",
                "enum": ["ticket_done", "ticket_escalate", "alert"],
            },
            "title": {
                "type": "string",
                "description": "告警标题（alert 类型使用）",
            },
            "content": {
                "type": "string",
                "description": (
                    "卡片正文，支持 lark_md。ticket_done 必须用多行固定字段："
                    "处理结果：...\n关键事实：...\n数据库核查：...\n代码路径：...\n日志核查：...\n"
                    "客户回复：...\n后续动作：...\n注意事项：...\n"
                    "数据库核查按 **表名** + bullet 字段块写，字段行形如 - `字段`: 值。"
                    "；ticket_escalate 填原因，alert 填消息"
                ),
            },
            "zendesk_url": {
                "type": "string",
                "description": "工单链接（ticket_done / ticket_escalate 可选）",
            },
            "urgent": {
                "type": "boolean",
                "description": "alert 类型使用。true 时模板红色 @ 所有人",
                "default": False,
            },
        },
        "required": ["card_type", "content"],
    }
    group = "always"
    concurrency_safe = True

    async def execute(
        self, args: dict[str, Any], session: "AgentSession",
    ) -> ToolResult:
        if "card_type" not in args:
            return ToolResult(success=False, error="card_type is required")
        card_type = args["card_type"]
        if not isinstance(card_type, str):
            return ToolResult(success=False, error="card_type must be a string")
        card_type = card_type.strip()
        if not card_type:
            return ToolResult(success=False, error="card_type is empty")

        if "content" not in args:
            return ToolResult(success=False, error="content is required")
        content = args["content"]
        if not isinstance(content, str):
            return ToolResult(success=False, error="content must be a string")
        content = content.strip()
        if not content:
            return ToolResult(success=False, error="content is empty")
        content = guard_against_stale_no_image_claim(content, session)

        title = args.get("title", "Alert")
        if not isinstance(title, str):
            return ToolResult(success=False, error="title must be a string")
        zendesk_url = args.get("zendesk_url", "")
        if not isinstance(zendesk_url, str):
            return ToolResult(success=False, error="zendesk_url must be a string")
        urgent = args.get("urgent", False)
        if not isinstance(urgent, bool):
            return ToolResult(success=False, error="urgent must be a boolean")

        logger.info(
            "feishu_push_start",
            ticket_id=session.ticket_id,
            card_type=card_type,
        )
        payload = await push_notification_card(
            card_type=card_type,
            ticket_id=str(session.ticket_id),
            content=content,
            title=title,
            zendesk_url=zendesk_url,
            urgent=urgent,
        )
        if not payload.get("success"):
            error = payload.get("error") or "unknown error"
            logger.warning(
                "feishu_push_failed",
                ticket_id=session.ticket_id,
                card_type=card_type,
                error=str(error),
                code=payload.get("code"),
            )
            return ToolResult(success=False, error=f"Feishu push failed: {error}")
        logger.info(
            "feishu_push_pushed",
            ticket_id=session.ticket_id,
            card_type=card_type,
        )
        output = payload.get("output")
        return ToolResult(output=output if isinstance(output, str) else f"pushed ({card_type})")

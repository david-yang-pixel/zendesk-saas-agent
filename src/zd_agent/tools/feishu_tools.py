"""FeishuPush tool: let the agent proactively push cards to Feishu."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from zd_agent.config import get_config
from zd_agent.integrations.feishu.client import FeishuClient
from zd_agent.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from zd_agent.session.agent_session import AgentSession


class FeishuPush(Tool):
    name = "feishu_push"
    description = (
        "主动推送消息到飞书群。card_type 选项："
        "ticket_done（工单处理完成摘要卡片，需 zendesk_url）/"
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
                "description": "卡片正文，支持 lark_md。ticket_done 填摘要，ticket_escalate 填原因，alert 填消息",
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
        cfg = get_config().feishu
        if not cfg.app_id or not cfg.app_secret or not cfg.default_chat_id:
            return ToolResult(
                success=False,
                error="Feishu not configured (app_id / app_secret / chat_id missing)",
            )

        client = FeishuClient(app_id=cfg.app_id, app_secret=cfg.app_secret)
        card_type = args["card_type"]
        content = args.get("content", "")
        try:
            if card_type == "ticket_done":
                await client.send_ticket_done(
                    chat_id=cfg.default_chat_id,
                    ticket_id=str(session.ticket_id),
                    summary=content,
                    zendesk_url=args.get("zendesk_url", ""),
                )
            elif card_type == "ticket_escalate":
                await client.send_ticket_escalate(
                    chat_id=cfg.default_chat_id,
                    ticket_id=str(session.ticket_id),
                    reason=content,
                    zendesk_url=args.get("zendesk_url", ""),
                )
            elif card_type == "alert":
                await client.send_alert(
                    chat_id=cfg.default_chat_id,
                    title=args.get("title", "Alert"),
                    message=content,
                    urgent=args.get("urgent", False),
                )
            else:
                return ToolResult(
                    success=False, error=f"unknown card_type: {card_type}",
                )
        except Exception as e:
            return ToolResult(success=False, error=f"Feishu push failed: {e}")
        return ToolResult(output=f"pushed ({card_type})")

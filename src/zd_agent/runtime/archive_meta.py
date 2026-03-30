"""归档元数据构建工具。"""

from __future__ import annotations

from typing import Any

from zd_agent.session.agent_session import AgentSession


def build_archive_meta(
    *,
    session: AgentSession,
    archived_at: str,
    elapsed_seconds: float | None = None,
    solution_summary: str | None = None,
) -> dict[str, Any]:
    """基于会话状态生成统一的 meta.json 内容。"""

    tool_calls = [_serialize_tool_call(item) for item in session.tool_call_log]
    failed_tools = [
        {
            "round": item["round"],
            "tool": item["tool"],
            "args": item["args"],
            "error": item["error"],
        }
        for item in tool_calls
        if not item["success"]
    ]

    meta: dict[str, Any] = {
        "ticket_id": session.ticket_id,
        "session_id": session.session_id,
        "status": session.status.value,
        "ticket_type": session.ticket_type or None,
        "created_at": archived_at,
        "archived_at": archived_at,
        "rounds": session.round_count,
        "total_tokens": session.token_usage.total_tokens,
        "token_usage": {
            "prompt": session.token_usage.prompt_tokens,
            "completion": session.token_usage.completion_tokens,
            "total": session.token_usage.total_tokens,
        },
        "tool_call_count": len(tool_calls),
        "tool_calls": tool_calls,
        "failed_tool_count": len(failed_tools),
        "failed_tools": failed_tools,
        "error": session.error_message or None,
        "fix_status": session.fix_status or None,
        "customer_reply_available": bool(
            session.customer_reply_draft or "## 客户回复建议" in session.reply_draft
        ),
        "reviewer_checklist_available": bool(
            session.reviewer_checklist or "## 人工复核指引" in session.reply_draft
        ),
        "reply_sent": session.reply_sent,
    }

    if elapsed_seconds is not None:
        meta["elapsed_seconds"] = round(elapsed_seconds, 1)

    if solution_summary is not None:
        meta["solution_summary"] = solution_summary

    return meta


def _serialize_tool_call(item: dict[str, Any]) -> dict[str, Any]:
    """整理单个工具调用的归档结构。"""

    args = {
        key: (str(value)[:100] if len(str(value)) > 100 else value)
        for key, value in item.get("args", {}).items()
    }
    return {
        "round": item.get("round"),
        "tool": item.get("tool", "unknown"),
        "args": args,
        "success": bool(item.get("success", False)),
        "error": item.get("error"),
        "result_preview": item.get("result_preview", ""),
    }

"""Build auditable metadata for one v2 casework run."""

from __future__ import annotations

from typing import Any

from zd_agent.session.agent_session import AgentSession, SessionStatus


def _current_casework_phase(session: AgentSession) -> str:
    """Infer the archived v2 phase from typed session facts."""
    if session.status is SessionStatus.COMPLETED:
        return "complete"
    if session.status is SessionStatus.DEFERRED:
        return "deferred"
    if session.status in {SessionStatus.FAILED, SessionStatus.TIMEOUT}:
        return "failed"
    if session.casework_disposition:
        return "effects"
    if session.tool_call_log or session.round_count:
        return "investigate"
    return "intake"


def build_archive_meta(
    *,
    session: AgentSession,
    archived_at: str,
    elapsed_seconds: float | None = None,
    solution_summary: str | None = None,
) -> dict[str, Any]:
    """Serialize the current v2 run without legacy classifier/prompt state."""
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
        "casework_phase": _current_casework_phase(session),
        "orchestration": {
            "root_session_id": session.root_session_id or session.session_id,
            "parent_session_id": session.parent_session_id or None,
            "session_kind": session.session_kind,
            "subagent_task_id": session.subagent_task_id or None,
            "subagent_slot_id": session.subagent_slot_id or None,
            "runtime_config_digest": session.runtime_config_digest or None,
        },
        "casework": {
            "disposition": session.casework_disposition or None,
            "reply_kind": session.casework_reply_kind or None,
            "ownership_action": session.casework_ownership_action or None,
            "reason_code": session.casework_reason_code or None,
            "delivery_state": session.casework_delivery_state or None,
            "ownership_state": session.casework_ownership_state or None,
            "effect_reference": session.casework_effect_reference or None,
            "attempt": session.casework_attempt,
        },
        "created_at": archived_at,
        "archived_at": archived_at,
        "rounds": session.round_count,
        "total_tokens": session.token_usage.total_tokens,
        "active_context_tokens": session.active_context_tokens,
        "context_compaction_count": session.context_compaction_count,
        "context_compactions": [
            dict(item) for item in session.context_compaction_log
        ],
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
        "customer_reply_available": bool(session.reply_draft),
        "reply_sent": session.reply_sent,
    }

    if elapsed_seconds is not None:
        meta["elapsed_seconds"] = round(elapsed_seconds, 1)
    if solution_summary is not None:
        meta["solution_summary"] = solution_summary
    return meta


def _serialize_tool_call(item: dict[str, Any]) -> dict[str, Any]:
    """Serialize one direct v2 evidence-tool call."""
    args = {
        key: (str(value)[:100] if len(str(value)) > 100 else value)
        for key, value in item.get("args", {}).items()
    }
    serialized: dict[str, Any] = {
        "call_id": str(item.get("call_id") or ""),
        "round": item.get("round"),
        "tool": item.get("tool", "unknown"),
        "args": args,
        "success": bool(item.get("success", False)),
        "error": item.get("error"),
        "result_preview": item.get("result_preview", ""),
    }
    metadata = item.get("metadata")
    if isinstance(metadata, dict) and metadata:
        serialized["metadata"] = dict(metadata)
    return serialized

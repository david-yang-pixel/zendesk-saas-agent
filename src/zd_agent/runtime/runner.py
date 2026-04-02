"""Agent 运行器：组装所有组件，执行单个工单的完整处理"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from zd_agent.config import get_config
from zd_agent.log import get_logger
from zd_agent.runtime.archive_meta import build_archive_meta
from zd_agent.runtime.guardrails import Guardrails
from zd_agent.runtime.llm_client import LLMClient
from zd_agent.runtime.prompt_loader import build_system_prompt
from zd_agent.runtime.tool_loop import run_tool_loop
from zd_agent.session.agent_session import AgentSession, SessionStatus
from zd_agent.session.sandbox import create_sandbox
from zd_agent.tools.registry import create_tool_registry
from zd_agent.tools.zendesk import _call_with_rate_limit_retry, _get_client

logger = get_logger(module="runner")

_ACK_NOTE = (
    "🤖 **AI Agent is now processing this ticket.** "
    "Please wait while the agent analyzes and prepares a response."
)
_FAILURE_NOTE_TEMPLATE = (
    "⚠️ **AI Agent encountered an error while processing this ticket.**\n\n"
    "**Reason:** {reason}\n\n"
    "Please handle this ticket manually or retry by mentioning @support-agent."
)


async def _send_ack_note(ticket_id: int) -> None:
    """发送 ACK 内部备注，告知客服 AI 已开始处理。"""
    try:
        client = _get_client()
        await _call_with_rate_limit_retry(client.reply_ticket, ticket_id, _ACK_NOTE, False)
        logger.info("ack_note_sent", ticket_id=ticket_id)
    except Exception as e:
        logger.warning("ack_note_failed", ticket_id=ticket_id, error=str(e))


async def _send_error_note(ticket_id: int, reason: str) -> None:
    """发送错误内部备注，告知客服 AI 处理失败及原因。"""
    body = _FAILURE_NOTE_TEMPLATE.format(reason=reason)
    try:
        client = _get_client()
        await _call_with_rate_limit_retry(client.reply_ticket, ticket_id, body, False)
        logger.info("error_note_sent", ticket_id=ticket_id)
    except Exception as e:
        logger.warning("error_note_failed", ticket_id=ticket_id, error=str(e))


async def run_agent(session: AgentSession) -> None:
    """运行 Agent 处理单个工单的完整流程

    1. 创建 sandbox
    2. 加载 prompt + 构造初始 messages
    3. 创建 ToolLoop 运行
    4. 清理 sandbox
    """
    cfg = get_config()

    # 创建 sandbox（根据配置自动选择 Local 或 Docker）
    sandbox = create_sandbox()
    session.sandbox_dir = await sandbox.create(session.ticket_id)

    # 创建工具注册中心
    registry = create_tool_registry()

    # 创建 LLM 客户端
    llm = LLMClient()

    # 创建护栏
    guardrails = Guardrails(config=cfg.agent.guardrails)

    # 构造初始 messages
    system_prompt = build_system_prompt()
    session.messages = [
        {"role": "system", "content": system_prompt},
    ]

    # 构造 user message
    user_content = f"请处理工单 #{session.ticket_id}。"
    if session.hint:
        user_content += f"\n\n额外提示：{session.hint}"
    if session.dry_run:
        user_content += "\n\n注意：当前为 Dry Run 模式，分析完成后生成回复草稿即可，不要实际发送回复。"

    session.messages.append({"role": "user", "content": user_content})

    logger.info(
        "agent_start",
        ticket_id=session.ticket_id,
        session_id=session.session_id,
        dry_run=session.dry_run,
        hint=session.hint or "(无)",
    )

    # 发送 ACK 内部备注（dry_run 模式跳过）
    if not session.dry_run:
        await _send_ack_note(session.ticket_id)

    try:
        # 运行工具循环
        await run_tool_loop(session, llm, registry, guardrails, trigger_type=session.trigger_type)

    except Exception as e:
        logger.error(
            "agent_error",
            ticket_id=session.ticket_id,
            error=str(e),
            exc_info=True,
        )
        session.status = SessionStatus.FAILED
        session.error_message = str(e)
        # 发送错误内部备注（dry_run 模式跳过）
        if not session.dry_run:
            await _send_error_note(session.ticket_id, str(e))

    finally:
        # 输出处理摘要
        logger.info(
            "agent_done",
            ticket_id=session.ticket_id,
            status=session.status.value,
            rounds=session.round_count,
            prompt_tokens=session.token_usage.prompt_tokens,
            completion_tokens=session.token_usage.completion_tokens,
            total_tokens=session.token_usage.total_tokens,
            elapsed=f"{guardrails.elapsed_seconds:.1f}s",
            reply_sent=session.reply_sent,
            archived=session.archived,
        )

        # 自动归档保底：如果 LLM 没有调用 archive_ticket，runner 层补充写入 meta.json
        _ensure_archive(session, guardrails)

        # sandbox 清理策略
        should_clean = (
            (session.status == SessionStatus.COMPLETED and cfg.sandbox.cleanup_on_success)
            or (session.status != SessionStatus.COMPLETED and cfg.sandbox.cleanup_on_failure)
        )

        if should_clean:
            await sandbox.cleanup()
        else:
            logger.info(
                "sandbox_preserved",
                ticket_id=session.ticket_id,
                status=session.status.value,
                path=str(session.sandbox_dir),
            )


def _ensure_archive(session: AgentSession, guardrails: Guardrails) -> None:
    """保底归档：确保每次处理都有 meta.json（即使 LLM 没有调用 archive_ticket）"""
    cfg = get_config()
    archive_dir = Path(cfg.knowledge.archive_dir) / str(session.ticket_id)
    meta_path = archive_dir / "meta.json"

    if meta_path.exists():
        return

    archive_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()

    meta = build_archive_meta(
        session=session,
        archived_at=now,
        elapsed_seconds=guardrails.elapsed_seconds,
    )

    try:
        meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8",
        )
        # 同时保存分析和回复草稿
        if session.analysis:
            (archive_dir / "analysis.md").write_text(session.analysis, encoding="utf-8")
        if session.reply_draft:
            (archive_dir / "reply.md").write_text(session.reply_draft, encoding="utf-8")

        logger.info("auto_archive_created", ticket_id=session.ticket_id, path=str(archive_dir))
    except Exception as e:
        logger.warning("auto_archive_failed", ticket_id=session.ticket_id, error=str(e))

"""Agent 运行器：组装所有组件，执行单个工单的完整处理"""

from __future__ import annotations

from pathlib import Path

from zd_agent.casework.domain.enums import ExecutionMode
from zd_agent.casework.adapters.tool_registry import create_casework_tool_registry
from zd_agent.casework.policies.rollout import casework_controls_ticket
from zd_agent.config import AppConfig, get_config
from zd_agent.integrations.zendesk_mcp import call_zendesk_tool
from zd_agent.log import get_logger
from zd_agent.runtime.guardrails import Guardrails
from zd_agent.runtime.casework_shadow import maybe_run_casework_shadow
from zd_agent.runtime.casework_v2 import (
    apply_casework_execution_to_session,
    execute_casework_v2,
)
from zd_agent.runtime.llm_client import LLMClient
from zd_agent.runtime.runner_archive import ensure_archive
from zd_agent.runtime.runner_reporting import report_preflight_failure
from zd_agent.services.escalation_notifications import (
    send_feishu_escalation_best_effort,
)
from zd_agent.services.feishu_notifications import notify_ticket_done_best_effort
from zd_agent.runtime.tool_loop import run_tool_loop
from zd_agent.session.agent_session import AgentSession, SessionStatus
from zd_agent.session.sandbox import create_sandbox
from zd_agent.tools.registry import create_tool_registry

logger = get_logger(module="runner")

_ACK_NOTE = (
    "🤖 **AI Agent is now processing this ticket.** "
    "Please wait while the agent analyzes and prepares a response."
)
_FAILURE_NOTE_TEMPLATE = (
    "⚠️ **AI Agent encountered an error while processing this ticket.**\n\n"
    "**Reason:** {reason}\n\n"
    "Please handle this ticket manually or retry by mentioning {mention}."
)


async def _capture_customer_message_snapshot(session: AgentSession) -> None:
    from zd_agent.services.ticket_messages import (
        _fetch_customer_messages,
        extract_form_data_from_msgs,
    )

    customer_messages, ticket = await _fetch_customer_messages(session.ticket_id)
    if ticket is None:
        logger.warning(
            "customer_message_snapshot_unavailable",
            ticket_id=session.ticket_id,
        )
        raise RuntimeError(
            f"customer-message snapshot unavailable for #{session.ticket_id}"
        )

    session.customer_msgs_snapshot_count = len(customer_messages)
    session.customer_msgs_snapshot_last_id = (
        str(customer_messages[-1].get("id") or "")
        if customer_messages
        else ""
    )
    session.customer_msgs_snapshot_captured = True
    session.customer_form_data = extract_form_data_from_msgs(customer_messages)
    logger.info(
        "customer_message_snapshot_captured",
        ticket_id=session.ticket_id,
        customer_message_count=session.customer_msgs_snapshot_count,
        customer_last_message_id=session.customer_msgs_snapshot_last_id or None,
        has_form_email=bool(session.customer_form_data.get("email")),
        has_form_workspace_id=bool(session.customer_form_data.get("workspace_id")),
    )


async def _send_ack_note(ticket_id: int) -> None:
    """发送 ACK 内部备注，告知客服 AI 已开始处理。"""
    try:
        payload = await call_zendesk_tool(
            "add_internal_note",
            {
                "ticket_id": ticket_id,
                "body": _ACK_NOTE,
                "explicit_user_internal_note_request": "agent processing ack",
                "dry_run": False,
                "confirm": True,
                "actor": "zd-agent",
            },
        )
        if not payload.get("success"):
            raise RuntimeError(f"{payload.get('code')}: {payload.get('error')}")
        logger.info("ack_note_sent", ticket_id=ticket_id)
    except Exception as e:
        logger.warning("ack_note_failed", ticket_id=ticket_id, error=str(e))


async def _send_error_note(ticket_id: int, reason: str) -> None:
    """发送错误内部备注，告知客服 AI 处理失败及原因。"""
    from zd_agent.config import get_config
    mention = get_config().zendesk.mention_trigger or "@agent"
    body = _FAILURE_NOTE_TEMPLATE.format(reason=reason, mention=mention)
    try:
        payload = await call_zendesk_tool(
            "add_internal_note",
            {
                "ticket_id": ticket_id,
                "body": body,
                "explicit_user_internal_note_request": "agent failure notice",
                "dry_run": False,
                "confirm": True,
                "actor": "zd-agent",
            },
        )
        if not payload.get("success"):
            raise RuntimeError(f"{payload.get('code')}: {payload.get('error')}")
        logger.info("error_note_sent", ticket_id=ticket_id)
    except Exception as e:
        logger.warning("error_note_failed", ticket_id=ticket_id, error=str(e))


async def _report_agent_failure(session: AgentSession, reason: str) -> None:
    """Leave deterministic Zendesk and Feishu traces for failed sessions."""
    safe_reason = (reason or f"agent session ended with status {session.status.value}")[:1000]
    await _send_error_note(session.ticket_id, safe_reason)
    await send_feishu_escalation_best_effort(
        ticket_id=session.ticket_id,
        reason=safe_reason,
        source="agent_session_failure",
        handoff_reply_sent=bool(session.reply_sent),
    )


async def _run_casework_agent(session: AgentSession, cfg: AppConfig) -> None:
    """Run the v2-only decision and effect path without the legacy tool loop."""
    guardrails = Guardrails(config=cfg.agent.guardrails)
    registry = create_casework_tool_registry()
    llm = LLMClient()
    if not session.dry_run:
        await _send_ack_note(session.ticket_id)
    logger.info(
        "casework_v2_start",
        ticket_id=session.ticket_id,
        session_id=session.session_id,
        execution_mode=cfg.casework.mode.value,
        dry_run=session.dry_run,
    )
    try:
        execution = await execute_casework_v2(
            session=session,
            cfg=cfg,
            provider=llm._provider,
            registry=registry,
        )
        apply_casework_execution_to_session(session, execution)
    except Exception as error:
        logger.error(
            "casework_v2_error",
            ticket_id=session.ticket_id,
            session_id=session.session_id,
            error=str(error),
            exc_info=True,
        )
        session.status = SessionStatus.FAILED
        session.error_message = str(error)
        if not session.dry_run:
            await _report_agent_failure(session, str(error))
    if (
        session.status is SessionStatus.COMPLETED
        and session.reply_sent
        and session.casework_ownership_action in {"", "none"}
    ):
        await notify_ticket_done_best_effort(session)
    ensure_archive(session, guardrails)
    logger.info(
        "casework_v2_done",
        ticket_id=session.ticket_id,
        session_id=session.session_id,
        status=session.status.value,
        rounds=session.round_count,
        total_tokens=session.token_usage.total_tokens,
        reply_sent=session.reply_sent,
        disposition=session.casework_disposition,
        ownership_action=session.casework_ownership_action,
        delivery_state=session.casework_delivery_state,
    )


async def run_agent(session: AgentSession) -> None:
    """运行 Agent 处理单个工单的完整流程

    1. 创建 sandbox
    2. 加载 prompt + 构造初始 messages
    3. 创建 ToolLoop 运行
    4. 清理 sandbox
    """
    cfg = get_config()
    # Production and dry-run ticket sessions use the same deterministic
    # intake -> classify -> plan -> investigate -> reply controller.
    session.investigation_policy_enabled = True
    guardrails = Guardrails(config=cfg.agent.guardrails)

    try:
        await _capture_customer_message_snapshot(session)
    except Exception as e:
        logger.error(
            "agent_error",
            ticket_id=session.ticket_id,
            session_id=session.session_id,
            stage="customer_message_snapshot",
            error=str(e),
            exc_info=True,
        )
        session.status = SessionStatus.FAILED
        session.error_message = str(e)
        await report_preflight_failure(
            session,
            guardrails,
            model=cfg.llm.model,
            reason=str(e),
        )
        ensure_archive(session, guardrails)
        if not session.dry_run:
            await _report_agent_failure(session, str(e))
        logger.info(
            "agent_done",
            ticket_id=session.ticket_id,
            session_id=session.session_id,
            status=session.status.value,
            rounds=session.round_count,
            prompt_tokens=session.token_usage.prompt_tokens,
            completion_tokens=session.token_usage.completion_tokens,
            total_tokens=session.token_usage.total_tokens,
            elapsed=f"{guardrails.elapsed_seconds:.1f}s",
            reply_sent=session.reply_sent,
            model=cfg.llm.model,
        )
        return

    if casework_controls_ticket(
        mode=cfg.casework.mode,
        canary_ticket_ids=cfg.casework.canary_ticket_ids,
        ticket_id=session.ticket_id,
    ):
        await _run_casework_agent(session, cfg)
        return

    # 创建 sandbox（根据配置自动选择 Local 或 Docker）
    sandbox = create_sandbox()
    session.sandbox_dir = await sandbox.create(session.ticket_id)

    # 创建工具注册中心
    registry = create_tool_registry()

    # 创建 LLM 客户端
    llm = LLMClient()

    # P2-8: 预拉客户画像（best-effort，超时/失败不影响主流程）
    from zd_agent.runtime.customer_profile import prefetch_customer_profile
    customer_profile = await prefetch_customer_profile(session.ticket_id)
    session.customer_profile = customer_profile

    # 招法 4 (cache 拉满)：system prompt 保持完全静态，不再注入 ticket-specific
    # 内容（customer_profile / current_date / hint / etc）。这些内容改放 user
    # message。OpenAI/Anthropic prompt cache 按 prefix 哈希命中，system 稳定
    # 后跨 ticket cache hit > 90% → input token 价格 ÷10。
    from zd_agent.runtime.prompt_loader import build_static_system_prompt
    system_prompt = build_static_system_prompt()
    session.messages = [
        {"role": "system", "content": system_prompt},
    ]

    # 构造 user message（所有 ticket-specific 上下文都放这里，不污染 system prompt）
    user_content_parts = [f"请处理工单 #{session.ticket_id}。"]

    # 当前日期（agent 可能引用，但不能放 system prompt 否则每天 cache miss）
    from datetime import date as _date
    user_content_parts.append(f"\n\n_当前日期: {_date.today().isoformat()}_")

    # Zendesk Messaging formResponse is the primary identity source for SaaS
    # workspace lookup.  Keep requester metadata separate because the two can
    # differ after forwarded/shared conversations.
    if session.customer_form_data:
        form_email = " ".join(
            str(session.customer_form_data.get("email") or "").split()
        )[:320]
        form_workspace_id = " ".join(
            str(session.customer_form_data.get("workspace_id") or "").split()
        )[:160]
        user_content_parts.append(
            "\n\n## 用户表单身份（工作空间定位的首选证据）\n"
            f"- 表单 Email: {form_email or '(未提供)'}\n"
            f"- 表单 Workspace ID: {form_workspace_id or '(未提供)'}\n"
            "- 涉及套餐、配额或 workspace 状态时，先核对两个字段。若 "
            "Workspace ID 是邮箱格式，它是客户提供的显式目标：必须分别用该 "
            "邮箱和表单 Email 调 `inspector_exec section=tenants`，目标状态查询 "
            "绑定 Workspace ID 邮箱解析出的 tenant_id，不能让联系邮箱覆盖它。"
            "普通 tenant UUID 形式的 Workspace ID 仅用于多个 workspace 消歧；"
            "其他非 UUID 文本只是显示名/提示，不能覆盖邮箱唯一映射。邮箱"
            "唯一映射时禁止再向客户索取 Workspace ID 或 workspace name；"
            "内部查询未执行、失败或未验证时先支持侧重查，只有成功查询证明"
            "真实歧义、无映射或 canonical ID 冲突时才向客户澄清。"
        )

    # customer_profile 注入到 user message
    if customer_profile:
        email = customer_profile.get("requester_email", "")
        recent_ids = customer_profile.get("recent_ticket_ids") or []
        count = customer_profile.get("recent_ticket_count", 0)
        if count > 0:
            user_content_parts.append(
                f"\n\n## 客户画像\n"
                f"- requester: {email}\n"
                f"- 过去 30 天同 requester SaaS 工单 **{count}** 个："
                f"{', '.join('#' + str(t) for t in recent_ids)}\n"
                f"- **强烈建议**在写 Internal Note 前用 `ticket_record_read` "
                f"看 1-2 个历史工单，避免重复 ask 已答过的信息。"
            )
        else:
            user_content_parts.append(
                f"\n\n## 客户画像\n"
                f"- requester: {email}\n"
                f"- 过去 30 天无历史 SaaS 工单 → 新客户，按常规流程处理"
            )

    # 线上部署版本注入（cloud.dify.ai 生产 dify-api 实际运行版本）。
    # best-effort：解析失败返回 None → 整段不注入，不影响工单处理。
    try:
        from zd_agent.tools.saas_version import (
            resolve_deployed_version,
            render_deployed_version_block,
        )
        _dv = await resolve_deployed_version()
        if _dv is not None:
            user_content_parts.append("\n\n" + render_deployed_version_block(_dv))
    except Exception as e:
        logger.warning("deployed_version_inject_failed", error=str(e))

    user_content = "".join(user_content_parts)
    if session.hint:
        user_content += (
            "\n\n---\n"
            "【辅助提示，仅做参考，不应改变或限制诊断范围 / 主导分析方向】\n"
            f"{session.hint}\n"
            "---\n"
            "提醒：即使有上述辅助提示，你仍必须先按核心 router 调用 `classify_ticket`，"
            "再遵循工具返回的 Skills 和类型策略完成诊断，不能因为辅助提示跳过证据门槛。"
        )
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
        if (
            not session.dry_run
            and session.status in (SessionStatus.FAILED, SessionStatus.TIMEOUT)
        ):
            await _report_agent_failure(
                session,
                session.error_message or "agent session failed without an error message",
            )

    except Exception as e:
        logger.error(
            "agent_error",
            ticket_id=session.ticket_id,
            session_id=session.session_id,
            error=str(e),
            exc_info=True,
        )
        session.status = SessionStatus.FAILED
        session.error_message = str(e)
        # 发送错误内部备注（dry_run 模式跳过）
        if not session.dry_run:
            await _report_agent_failure(session, str(e))

    finally:
        if cfg.casework.mode is ExecutionMode.SHADOW:
            try:
                await maybe_run_casework_shadow(
                    mode=cfg.casework.mode,
                    session=session,
                    provider=llm._provider,
                    registry=registry,
                    archive_dir=Path(cfg.casework.shadow_archive_dir),
                    enable_vision=cfg.llm.enable_vision_input,
                )
            except Exception as error:
                logger.warning(
                    "casework_shadow_boundary_failed",
                    ticket_id=session.ticket_id,
                    error_type=type(error).__name__,
                )

        # 输出处理摘要
        logger.info(
            "agent_done",
            ticket_id=session.ticket_id,
            session_id=session.session_id,
            status=session.status.value,
            rounds=session.round_count,
            prompt_tokens=session.token_usage.prompt_tokens,
            completion_tokens=session.token_usage.completion_tokens,
            total_tokens=session.token_usage.total_tokens,
            elapsed=f"{guardrails.elapsed_seconds:.1f}s",
            reply_sent=session.reply_sent,
            model=cfg.llm.model,
        )

        if (
            session.status == SessionStatus.COMPLETED
            and session.reply_sent
            and session.public_reply_type != "handoff"
        ):
            await notify_ticket_done_best_effort(session)

        ensure_archive(session, guardrails)

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

# noqa: SIZ001 - Ordered public-reply gates stay atomic; helpers live in focused modules.
"""ZendeskPublicReply tool: send a customer-visible native messaging reply.

Why a separate tool from :class:`zendesk.ZendeskInternalReply`
------------------------------------------------------------
Internal Note 与 Public Reply 是两类**完全不同**的对外动作：

- **Internal Note** 给 Support Engineer 看，agent 写"草稿 + 分析 + 引导"三段式
  HTML 卡片，可以随便写、SE 审完发不发是 SE 的事；**风险低**。
- **Public Reply** 客户**直接看到**，agent 说错话不能撤回，**风险高**。

为防止 agent 把两个语境混淆（用 internal 风格写 public reply 给客户读到内部摘要
卡片是灾难），把 public reply 做成**完全独立的工具**，参数 schema、措辞约束、
footer 处理都不一样。

设计决策（来自跟用户的 2026-05-13 设计讨论）
---------------------------------------------
1. **永远附 footer**：硬编码追加，LLM 不能跳过；明示 AI 身份 + 给客户"回复 human"
   逃生口。
2. **统一英文**：所有 SaaS messaging 工单的公开回复一律 English，不跟随客户语言。
   原因：模板一致性 + 国际化默认 + LLM 英文质量最稳。
3. **reply_type 区分场景**：ask / solution / handoff 统一使用标准 AI 声明；
   closed 使用收尾声明。
4. **不做 HTML 渲染**：public reply 是给客户读的，纯文本 / Markdown 已经够，不要
   学 Internal Note 那样塞复杂 HTML 卡片。
5. **kill switch**：``cfg.public_reply.enabled = false`` 时工具直接 return 失败，
   出问题一秒下线无需 redeploy。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

from zd_agent.config import get_config
from zd_agent.integrations.zendesk_browser_relay import send_browser_relay_reply
from zd_agent.integrations.zendesk_mcp import call_zendesk_tool
from zd_agent.log import get_logger
from zd_agent.runtime.pre_send_check import FreshnessResult, pre_send_check_or_abort
from zd_agent.runtime.refund_gate import refund_owner_gate_block_reason
from zd_agent.runtime.reply_audit import (
    public_reply_evidence_blockers,
    public_reply_privacy_blockers,
)
from zd_agent.services.messaging_context import get_messaging_context
from zd_agent.services.public_reply_delivery_store import (
    PublicReplyDeliveryStore,
)
from zd_agent.tools.base import Tool, ToolResult
from zd_agent.tools.public_reply_format import (
    _FOOTER_BY_TYPE,
    _FOOTER_STANDARD,
    _VALID_REPLY_TYPES,
    _append_footer,
    _format_reply_body,
)
from zd_agent.tools.public_reply_delivery import (
    recent_duplicate_public_reply_exists,
    send_public_reply_safely,
)

if TYPE_CHECKING:
    from zd_agent.session.agent_session import AgentSession

__all__ = [
    "ZendeskPublicReply",
    "_FOOTER_BY_TYPE",
    "_FOOTER_STANDARD",
    "_VALID_REPLY_TYPES",
    "_append_footer",
    "_check_freshness_before_relay",
    "_format_reply_body",
    "_send_public_reply_safely",
    "send_public_reply",
]

logger = get_logger(module="tools.zendesk_public_reply")


async def _recent_duplicate_public_reply_exists(
    *,
    ticket_id: int,
    body_with_footer: str,
    not_before: float,
) -> bool:
    return await recent_duplicate_public_reply_exists(
        ticket_id=ticket_id,
        body_with_footer=body_with_footer,
        not_before=not_before,
        zendesk_tool_caller=call_zendesk_tool,
    )


def _delivery_store_for_config(cfg: Any) -> PublicReplyDeliveryStore:
    state_dir = Path(cfg.resources.base_dir) / cfg.resources.state_dir
    return PublicReplyDeliveryStore(state_dir / "public_reply_outbox.sqlite3")


async def _send_public_reply_safely(
    *,
    ticket_id: int,
    body_with_footer: str,
    reply_type: str,
    conversation_id: str | None = None,
    messaging_app_id: str | None = None,
    snapshot_customer_message_count: int | None = None,
    snapshot_customer_last_message_id: str | None = None,
    delivery_attempt_id: str | None = None,
    delivery_store: PublicReplyDeliveryStore | None = None,
) -> bool:
    cfg = get_config()
    return await send_public_reply_safely(
        cfg=cfg,
        ticket_id=ticket_id,
        body_with_footer=body_with_footer,
        reply_type=reply_type,
        conversation_id=conversation_id,
        snapshot_customer_message_count=snapshot_customer_message_count,
        snapshot_customer_last_message_id=snapshot_customer_last_message_id,
        delivery_attempt_id=delivery_attempt_id,
        delivery_store=delivery_store,
        delivery_store_factory=_delivery_store_for_config,
        zendesk_tool_caller=call_zendesk_tool,
        relay_sender=send_browser_relay_reply,
    )


async def _check_freshness_before_relay(
    *,
    ticket_id: int,
    snapshot_count: int | None,
    snapshot_last_id: str | None,
) -> FreshnessResult | None:
    """Best-effort first freshness guard before handing off to browser relay."""
    if snapshot_count is None:
        return None

    from zd_agent.config import EnvSettings

    cfg = get_config()

    async def _fetch() -> list[dict]:
        from zd_agent.services.ticket_messages import (
            fetch_customer_messages_for_freshness,
        )

        return await fetch_customer_messages_for_freshness(ticket_id)

    env = EnvSettings()
    return await pre_send_check_or_abort(
        ticket_id=ticket_id,
        snapshot_count=snapshot_count,
        snapshot_last_id=snapshot_last_id,
        fetch_messages_fn=_fetch,
        model=cfg.trigger_decider.llm_model,
        api_key=env.openai_api_key,
        base_url=cfg.llm.base_url,
        skip_llm=True,
    )
async def send_public_reply(
    *,
    ticket_id: int,
    body: str,
    reply_type: str,
    dry_run: bool = False,
    conversation_id: str | None = None,
    messaging_app_id: str | None = None,
    snapshot_customer_message_count: int | None = None,
    snapshot_customer_last_message_id: str | None = None,
    delivery_attempt_id: str | None = None,
) -> tuple[bool, str]:
    """直接发 public reply 的公共入口（不需要 AgentSession）。

    给 trigger_consumer / 集成测试 / CLI 用。和 ZendeskPublicReply Tool 共享
    底层送达逻辑（footer 追加 + kill switch + browser relay），并在调用
    relay 前用客户消息快照做一次轻量 freshness check。

    Returns:
        (success, message_or_error)
    """
    if not isinstance(ticket_id, int) or isinstance(ticket_id, bool) or ticket_id <= 0:
        return False, "ticket_id must be a positive integer"

    # Kill switch
    cfg = get_config()
    if not getattr(cfg, "public_reply", None) or not cfg.public_reply.enabled:
        logger.warning(
            "public_reply_disabled_direct",
            ticket_id=ticket_id,
            reason="cfg.public_reply.enabled=false",
        )
        return (
            False,
            "Public reply is currently disabled (kill switch).",
        )

    # 参数校验
    body = (body or "").strip()
    if not body:
        return False, "body is empty"
    if reply_type not in _VALID_REPLY_TYPES:
        return False, f"invalid reply_type: {reply_type!r}"
    privacy_blockers = public_reply_privacy_blockers(body)
    if privacy_blockers:
        logger.warning(
            "public_reply_privacy_hard_gate_blocked_direct",
            ticket_id=ticket_id,
            reply_type=reply_type,
            warning_count=len(privacy_blockers),
            warnings=privacy_blockers[:5],
        )
        return (
            False,
            "Public reply privacy hard gate blocked this reply. "
            "Remove internal source paths before replying: "
            + "; ".join(privacy_blockers[:5]),
        )

    # Footer 追加
    body_with_footer = _append_footer(body, reply_type)
    if not conversation_id:
        context = get_messaging_context(ticket_id)
        conversation_id = context.get("conversation_id") or None
        messaging_app_id = messaging_app_id or context.get("app_id") or None

    if dry_run:
        logger.info(
            "public_reply_dry_run_direct",
            ticket_id=ticket_id, reply_type=reply_type,
            body_len=len(body_with_footer),
        )
        return True, "[Dry Run] Not sent."
    if not conversation_id:
        logger.warning(
            "public_reply_missing_conversation_id_direct",
            ticket_id=ticket_id,
            reply_type=reply_type,
        )
        return (
            False,
            "Zendesk Messaging conversation_id is missing; "
            "Support API public-comment fallback is disabled.",
        )
    if (
        reply_type in ("ask", "solution")
        and snapshot_customer_message_count is None
    ):
        return (
            False,
            "Public reply blocked because the customer-message snapshot is unavailable.",
        )

    try:
        check = await _check_freshness_before_relay(
            ticket_id=ticket_id,
            snapshot_count=snapshot_customer_message_count,
            snapshot_last_id=snapshot_customer_last_message_id,
        )
    except Exception as e:  # noqa: BLE001 - freshness failures must fail closed.
        logger.warning(
            "public_reply_pre_relay_freshness_error",
            ticket_id=ticket_id,
            error=str(e)[:200],
        )
        return (
            False,
            "Pre-relay freshness check unavailable; public reply was blocked.",
        )
    if check is not None and not check.continue_send:
        logger.info(
            "public_reply_aborted_pre_relay_freshness",
            ticket_id=ticket_id,
            reply_type=reply_type,
            reason=check.reason,
            new_msg_count=check.new_messages_count,
        )
        if check.reason.startswith("fetch_error"):
            return (
                False,
                "Pre-relay freshness check unavailable; public reply was blocked.",
            )
        return (
            False,
            "Pre-relay freshness check abort: customer added "
            f"{check.new_messages_count} new message(s) ({check.reason}).",
        )

    logger.info(
        "public_reply_send_start_direct",
        ticket_id=ticket_id, reply_type=reply_type,
        body_len=len(body_with_footer),
    )
    try:
        sent_now = await _send_public_reply_safely(
            ticket_id=ticket_id,
            body_with_footer=body_with_footer,
            reply_type=reply_type,
            conversation_id=conversation_id,
            messaging_app_id=messaging_app_id,
            snapshot_customer_message_count=snapshot_customer_message_count,
            snapshot_customer_last_message_id=snapshot_customer_last_message_id,
            delivery_attempt_id=delivery_attempt_id,
        )
        logger.info(
            "public_reply_sent_direct" if sent_now else "public_reply_already_delivered_direct",
            ticket_id=ticket_id, reply_type=reply_type,
        )
        # M8 metric: 成功发出一条公共回复
        try:
            from zd_agent.observability.metrics import (
                emit_agent_public_reply_sent,
            )
            if sent_now:
                emit_agent_public_reply_sent(
                    ticket_id=ticket_id,
                    reply_type=reply_type,
                    body_chars=len(body),  # 不含 footer
                )
        except Exception as metric_error:  # noqa: BLE001 - metrics are best effort.
            logger.warning(
                "public_reply_metric_failed",
                ticket_id=ticket_id,
                error_type=type(metric_error).__name__,
            )
        if sent_now:
            return True, f"已发送 Public Reply 到工单 #{ticket_id}"
        return True, f"Public Reply already delivered to ticket #{ticket_id}"
    except Exception as e:  # noqa: BLE001 - public boundary returns a typed result.
        logger.error(
            "public_reply_direct_send_failed",
            ticket_id=ticket_id, error=str(e)[:200],
            exc_info=True,
        )
        return False, f"{type(e).__name__}: {e}"


class ZendeskPublicReply(Tool):
    """发送公开回复（客户可见）。**只用于 SaaS messaging 工单**。

    与 ``zendesk_internal_reply`` 互斥：

    - Internal Reply 给同事看，不会触发客户邮件
    - Public Reply 给**客户**看 + Zendesk 会发邮件通知客户 / 在 messaging widget 显示

    工具自动追加 footer（明示 AI 身份 + 提供"回复 human" 逃生口）。LLM
    在 ``body`` 里**不需要**也**不应该**自己写 footer，写了会被检测到不重复加。
    """

    name = "zendesk_public_reply"
    description = (
        "向 SaaS messaging 工单发送 Public Reply（客户可见的公开评论）。"
        "正文应该用 English（统一语言）。系统会自动追加 footer 注明 AI 身份并提示"
        "客户可回复 'human' 转人工，**不要在 body 里自己写 footer**。"
        "**不要包含源码路径、内部仓库路径、文件名+行号或内部代码实现位置**。"
        "reply_type 决定 footer 措辞变体："
        "ask（向客户问问题，模板 A/B/C/D）/ "
        "solution（给出解决方案，模板 F）/ "
        "handoff（已转人工，模板 E）/ "
        "closed（工单收尾，模板 G）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "ticket_id": {
                "type": "integer",
                "description": "工单 ID",
            },
            "body": {
                "type": "string",
                "description": (
                    "公开回复正文，**English only**，Markdown 格式。"
                    "不要包含 footer——系统会自动追加。不要包含源码路径、"
                    "内部仓库路径、文件名+行号或内部代码实现位置。"
                ),
            },
            "reply_type": {
                "type": "string",
                "enum": sorted(_VALID_REPLY_TYPES),
                "description": (
                    "决定 footer 措辞变体："
                    "ask（向客户索取更多信息）/ "
                    "solution（agent 给出解决方案）/ "
                    "handoff（已转人工）/ "
                    "closed（客户已解决，收尾）"
                ),
            },
            "conversation_id": {
                "type": "string",
                "description": "Zendesk Messaging conversation id（系统通常自动填充）",
            },
            "messaging_app_id": {
                "type": "string",
                "description": "Zendesk Messaging app id（系统通常自动填充）",
            },
        },
        "required": ["ticket_id", "body", "reply_type"],
    }
    group = "zendesk"
    concurrency_safe = False
    is_write_operation = True

    async def execute(
        self, args: dict[str, Any], session: "AgentSession",
    ) -> ToolResult:
        ticket_id = args.get("ticket_id")
        raw_body = args.get("body")
        reply_type = args.get("reply_type")
        raw_conversation_id = args.get("conversation_id")
        raw_messaging_app_id = args.get("messaging_app_id")

        # 先做类型校验，再 strip，避免非法工具参数变成 AttributeError。
        if "ticket_id" not in args:
            return ToolResult(success=False, error="ticket_id is required")
        if not isinstance(ticket_id, int) or isinstance(ticket_id, bool) or ticket_id <= 0:
            return ToolResult(
                success=False,
                error=f"invalid ticket_id: {ticket_id!r}",
            )
        if "body" not in args:
            return ToolResult(success=False, error="body is required")
        if not isinstance(raw_body, str):
            return ToolResult(success=False, error="body must be a string")
        if "reply_type" not in args:
            return ToolResult(success=False, error="reply_type is required")
        if raw_conversation_id is not None and not isinstance(raw_conversation_id, str):
            return ToolResult(success=False, error="conversation_id must be a string")
        if raw_messaging_app_id is not None and not isinstance(raw_messaging_app_id, str):
            return ToolResult(success=False, error="messaging_app_id must be a string")

        body = raw_body.strip()
        conversation_id = (raw_conversation_id or "").strip() or None
        messaging_app_id = (raw_messaging_app_id or "").strip() or None

        # 1. Kill switch（出问题一秒下线）
        cfg = get_config()
        if not getattr(cfg, "public_reply", None) or not cfg.public_reply.enabled:
            logger.warning(
                "public_reply_disabled",
                ticket_id=ticket_id,
                reason="cfg.public_reply.enabled=false",
            )
            return ToolResult(
                success=False,
                error=(
                    "Public reply is currently disabled (kill switch). "
                    "Do not use zendesk_internal_reply as a customer-visible "
                    "fallback; escalate or retry after the public reply path is healthy."
                ),
            )

        # 2. 参数校验
        if not body:
            return ToolResult(success=False, error="body is empty")
        if reply_type not in _VALID_REPLY_TYPES:
            return ToolResult(
                success=False,
                error=(
                    f"invalid reply_type: {reply_type!r}; "
                    f"must be one of {sorted(_VALID_REPLY_TYPES)}"
                ),
            )
        if getattr(session, "reply_sent", False):
            logger.warning(
                "public_reply_duplicate_blocked",
                ticket_id=ticket_id,
                reply_type=reply_type,
            )
            return ToolResult(
                success=False,
                error=(
                    "Public reply already sent in this agent session; "
                    "do not send another customer-visible reply. Use archive_ticket "
                    "for the processing summary, or re-fetch the ticket before retrying."
                ),
            )

        privacy_blockers = public_reply_privacy_blockers(body)
        if privacy_blockers:
            logger.warning(
                "public_reply_privacy_hard_gate_blocked",
                ticket_id=ticket_id,
                reply_type=reply_type,
                warning_count=len(privacy_blockers),
                warnings=privacy_blockers[:5],
            )
            return ToolResult(
                success=False,
                error=(
                    "Public reply privacy hard gate blocked this reply. "
                    "Remove internal source paths before replying: "
                    + "; ".join(privacy_blockers[:5])
                ),
            )

        blockers = public_reply_evidence_blockers(
            body=body,
            tool_call_log=getattr(session, "tool_call_log", []) or [],
            current_ticket_id=ticket_id,
        )
        if blockers:
            logger.warning(
                "public_reply_evidence_hard_gate_blocked",
                ticket_id=ticket_id,
                reply_type=reply_type,
                warning_count=len(blockers),
                warnings=blockers[:5],
            )
            return ToolResult(
                success=False,
                error=(
                    "Public reply evidence hard gate blocked this reply. "
                    "Verify the concrete claim with tools before replying: "
                    + "; ".join(blockers[:5])
                ),
            )

        refund_block = refund_owner_gate_block_reason(body=body, session=session)
        if refund_block:
            logger.warning(
                "public_reply_refund_owner_gate_blocked",
                ticket_id=ticket_id,
                reply_type=reply_type,
            )
            return ToolResult(success=False, error=refund_block)

        # 3. Footer 强制追加（幂等）
        body_with_footer = _append_footer(body, reply_type)
        if not conversation_id:
            context = get_messaging_context(ticket_id)
            conversation_id = context.get("conversation_id") or None
            messaging_app_id = messaging_app_id or context.get("app_id") or None

        # 4. dry_run 模式：只存草稿不发
        session.reply_draft = body_with_footer
        if session.dry_run:
            logger.info(
                "public_reply_dry_run",
                ticket_id=ticket_id, reply_type=reply_type,
                body_len=len(body_with_footer),
            )
            return ToolResult(
                output=(
                    f"[Dry Run] Public reply 已存草稿（未发送）。\n\n"
                    f"--- 草稿内容 ---\n{body_with_footer}"
                ),
            )
        if not conversation_id:
            logger.warning(
                "public_reply_missing_conversation_id",
                ticket_id=ticket_id,
                reply_type=reply_type,
            )
            return ToolResult(
                success=False,
                error=(
                    "Zendesk Messaging conversation_id is missing; "
                    "Support API public-comment fallback is disabled."
                ),
            )

        # 4b. Pre-send freshness check（M5）
        # handoff / closed 类回复跳过 freshness check：
        #   - handoff 是"已转人工"通知，发就发，不需等更多客户消息
        #   - closed 是客户已说解决，发就发
        if reply_type in ("ask", "solution"):
            if not session.customer_msgs_snapshot_captured:
                return ToolResult(
                    success=False,
                    error=(
                        "Public reply blocked because the customer-message "
                        "snapshot is unavailable. Re-fetch the ticket before replying."
                    ),
                )
            from zd_agent.config import EnvSettings
            from zd_agent.runtime.pre_send_check import pre_send_check_or_abort

            async def _fetch():
                from zd_agent.services.ticket_messages import (
                    fetch_customer_messages_for_freshness,
                )

                return await fetch_customer_messages_for_freshness(ticket_id)

            env = EnvSettings()
            try:
                check = await pre_send_check_or_abort(
                    ticket_id=ticket_id,
                    snapshot_count=session.customer_msgs_snapshot_count,
                    snapshot_last_id=session.customer_msgs_snapshot_last_id or None,
                    fetch_messages_fn=_fetch,
                    model=cfg.trigger_decider.llm_model,
                    api_key=env.openai_api_key,
                    base_url=cfg.llm.base_url,
                )
            except Exception as e:  # noqa: BLE001 - freshness failures must fail closed.
                logger.warning(
                    "public_reply_pre_send_check_error",
                    ticket_id=ticket_id, error=str(e)[:200],
                )
                return ToolResult(
                    success=False,
                    error=(
                        "Pre-send freshness check unavailable; public reply was "
                        "blocked to avoid sending a stale response."
                    ),
                )

            if not check.continue_send:
                logger.info(
                    "public_reply_aborted_pre_send_check",
                    ticket_id=ticket_id, reply_type=reply_type,
                    reason=check.reason,
                    new_msg_count=check.new_messages_count,
                )
                if check.reason.startswith("fetch_error"):
                    return ToolResult(
                        success=False,
                        error=(
                            "Pre-send freshness check unavailable; public reply was "
                            "blocked to avoid sending a stale response."
                        ),
                    )
                return ToolResult(
                    success=False,
                    error=(
                        f"Pre-send check abort: customer added "
                        f"{check.new_messages_count} new substantive message(s) "
                        f"({check.reason}). Re-analyze with fresh context."
                    ),
                )

        # 5. 真发送：只走 native messaging browser relay，不回退 Support API。
        logger.info(
            "public_reply_send_start",
            ticket_id=ticket_id, reply_type=reply_type,
            body_len=len(body_with_footer),
        )
        try:
            sent_now = await _send_public_reply_safely(
                ticket_id=ticket_id,
                body_with_footer=body_with_footer,
                reply_type=reply_type,
                conversation_id=conversation_id,
                messaging_app_id=messaging_app_id,
                snapshot_customer_message_count=session.customer_msgs_snapshot_count
                if session.customer_msgs_snapshot_captured
                else None,
                snapshot_customer_last_message_id=(
                    session.customer_msgs_snapshot_last_id or None
                ),
                delivery_attempt_id=(
                    session.delivery_attempt_id or session.session_id
                ),
            )
            session.reply_sent = True
            logger.info(
                "public_reply_sent" if sent_now else "public_reply_already_delivered",
                ticket_id=ticket_id, reply_type=reply_type,
            )
            # M8 metric: agent 通过 Tool 路径成功发出公共回复
            try:
                from zd_agent.observability.metrics import (
                    emit_agent_public_reply_sent,
                )
                if sent_now:
                    emit_agent_public_reply_sent(
                        ticket_id=ticket_id,
                        reply_type=reply_type,
                        body_chars=len(body),  # 不含 footer
                    )
            except Exception as metric_error:  # noqa: BLE001 - metrics are best effort.
                logger.warning(
                    "public_reply_metric_failed",
                    ticket_id=ticket_id,
                    error_type=type(metric_error).__name__,
                )
            if sent_now:
                return ToolResult(
                    output=(
                        f"已发送 Public Reply 到工单 #{ticket_id}"
                        f"（reply_type={reply_type}）"
                    ),
                )
            return ToolResult(
                output=(
                    f"Public Reply already delivered to ticket #{ticket_id}"
                    f"（reply_type={reply_type}）"
                ),
            )
        except Exception as e:  # noqa: BLE001 - tool boundary returns ToolResult.
            logger.error(
                "public_reply_send_failed",
                ticket_id=ticket_id, error=str(e)[:200],
                exc_info=True,
            )
            return ToolResult(
                success=False, error=f"{type(e).__name__}: {e}",
            )

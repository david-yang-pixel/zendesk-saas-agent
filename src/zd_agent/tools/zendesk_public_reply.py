# noqa: SIZ001 - Ordered public-reply gates stay atomic; helpers live in focused modules.
"""ZendeskPublicReply tool: send a customer-visible native messaging reply.

Why a separate tool from :class:`zendesk.ZendeskInternalReply`
------------------------------------------------------------
Internal Note 与 Public Reply 是两类**完全不同**的内容：

- **Internal Note** 给 Support Engineer 看，agent 写"草稿 + 分析 + 引导"三段式
  HTML 卡片，可以随便写、SE 审完发不发是 SE 的事；**风险低**。
- **Public Reply** 客户**直接看到**，agent 说错话不能撤回，**风险高**。每次确认
  公开回复送达后，系统会另外写入一条幂等的 Internal Analysis，供工程师追踪。

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
4. **使用易读纯文本**：public reply 不使用 Markdown 标题、粗体、反引号、emoji
   或装饰性分隔线；只保留短段落和必要列表。
5. **kill switch**：``cfg.public_reply.enabled = false`` 时工具直接 return 失败，
   出问题一秒下线无需 redeploy。
"""
from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from zd_agent.config import get_config
from zd_agent.integrations.zendesk_browser_relay import (
    BrowserRelayStaleContextError,
    send_browser_relay_reply,
)
from zd_agent.integrations.zendesk_mcp import call_zendesk_tool
from zd_agent.log import get_logger
from zd_agent.runtime.pre_send_check import (
    FreshnessRecovery,
    FreshnessRecoveryAction,
    FreshnessResult,
)
from zd_agent.runtime.refund_gate import refund_owner_gate_block_reason
from zd_agent.runtime.reply_audit import (
    public_reply_evidence_blockers,
    public_reply_privacy_blockers,
)
from zd_agent.services.messaging_context import get_messaging_context
from zd_agent.services.public_reply_internal_note import (
    PostPublicInternalNoteStore,
    ensure_post_public_internal_note,
)
from zd_agent.services.public_reply_delivery_store import (
    PublicReplyDeliveryStore,
)
from zd_agent.services.public_reply_handoff_effects import (
    PostPublicHandoffStore,
    ensure_post_public_handoff_effects,
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


@dataclass(frozen=True)
class DirectPublicReplyResult:
    """Outcome for the deterministic public-reply path.

    ``accepted_snapshot_*`` is populated only after a successful delivery.  It
    lets trigger-state callers consume harmless customer follow-ups that the
    final freshness gate deliberately allowed through.
    """

    success: bool
    message: str
    accepted_snapshot_count: int | None = None
    accepted_snapshot_last_id: str | None = None


def _advance_session_snapshot_from_accepted_freshness(
    session: AgentSession,
    check: FreshnessResult,
) -> None:
    """Persist a cursor accepted by the freshness gate after delivery.

    A non-substantive customer message does not require a model rebase, but it
    is still consumed by the reply that follows.  Keep the durable state cursor
    in sync so its delayed webhook cannot dispatch that same message again.
    """
    recovery = check.recovery
    if (
        not check.continue_send
        or recovery is None
        or not recovery.next_snapshot_last_id
    ):
        return
    session.customer_msgs_snapshot_count = recovery.next_snapshot_count
    session.customer_msgs_snapshot_last_id = recovery.next_snapshot_last_id
    session.customer_msgs_snapshot_captured = True


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


def _handoff_store_for_config(cfg: Any) -> PostPublicHandoffStore:
    state_dir = Path(cfg.resources.base_dir) / cfg.resources.state_dir
    return PostPublicHandoffStore(
        state_dir / "post_public_handoff_outbox.sqlite3"
    )


def _prepare_durable_delivery_payload(
    *,
    cfg: Any,
    ticket_id: int,
    reply_type: str,
    delivery_attempt_id: str | None,
    body_with_footer: str,
    internal_analysis: str,
    snapshot_customer_message_count: int | None = None,
    snapshot_customer_last_message_id: str | None = None,
) -> tuple[
    str,
    str,
    str,
    PublicReplyDeliveryStore | None,
    PostPublicInternalNoteStore | None,
]:
    """Freeze public body and analysis under one logical delivery attempt."""
    if not delivery_attempt_id:
        return body_with_footer, internal_analysis, reply_type, None, None
    delivery_store = _delivery_store_for_config(cfg)
    attempt = delivery_store.prepare(
        delivery_attempt_id=delivery_attempt_id,
        ticket_id=ticket_id,
        reply_type=reply_type,
        body=body_with_footer,
        internal_analysis=internal_analysis,
        snapshot_customer_message_count=snapshot_customer_message_count,
        snapshot_customer_last_message_id=snapshot_customer_last_message_id,
    )
    note_store = PostPublicInternalNoteStore(
        delivery_store.path.parent / "post_public_internal_note_outbox.sqlite3"
    )
    note_store.prepare(
        ticket_id=ticket_id,
        delivery_attempt_id=delivery_attempt_id,
        reply_type=attempt.reply_type,
        body_with_footer=attempt.body,
        internal_analysis=attempt.internal_analysis,
        public_delivery_confirmed=attempt.delivered,
        accept_legacy_reference=attempt.migrated_from_v1,
    )
    return (
        attempt.body,
        attempt.internal_analysis,
        attempt.reply_type,
        delivery_store,
        note_store,
    )


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
    internal_analysis: str = "",
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
        internal_analysis=internal_analysis,
    )


async def _check_freshness_before_relay(
    *,
    ticket_id: int,
    snapshot_count: int | None,
    snapshot_last_id: str | None,
) -> FreshnessResult:
    """Fail-closed freshness and Answer Bot form check before relay."""
    return await _check_public_reply_readiness(
        ticket_id=ticket_id,
        snapshot_count=snapshot_count,
        snapshot_last_id=snapshot_last_id,
        skip_llm=True,
    )


async def _check_public_reply_readiness(
    *,
    ticket_id: int,
    snapshot_count: int | None,
    snapshot_last_id: str | None,
    skip_llm: bool,
    cfg: Any | None = None,
) -> FreshnessResult:
    """Fetch once, then verify customer freshness and blocking-form readiness."""
    from zd_agent.config import EnvSettings
    from zd_agent.runtime.pre_send_check import pre_send_check_or_abort
    from zd_agent.services.ticket_message_parsing import (
        conversation_has_pending_form,
    )
    from zd_agent.services.ticket_messages import _fetch_customer_messages

    latest_ticket: dict[str, Any] | None = None

    async def _fetch() -> list[dict]:
        nonlocal latest_ticket
        messages, ticket = await _fetch_customer_messages(ticket_id)
        if ticket is None:
            raise RuntimeError(f"ticket fetch failed for #{ticket_id}")
        latest_ticket = ticket
        return messages

    if snapshot_count is None:
        try:
            await _fetch()
        except Exception as error:  # noqa: BLE001 - public writes fail closed.
            reason = f"fetch_error: {type(error).__name__}"
            return FreshnessResult(
                continue_send=False,
                reason=reason,
                recovery=FreshnessRecovery(
                    action=FreshnessRecoveryAction.RETRY_FETCH,
                    reason=reason,
                ),
            )
        check = FreshnessResult(continue_send=True, reason="no_snapshot_required")
    else:
        cfg = cfg or get_config()
        env = EnvSettings()
        check = await pre_send_check_or_abort(
            ticket_id=ticket_id,
            snapshot_count=snapshot_count,
            snapshot_last_id=snapshot_last_id,
            fetch_messages_fn=_fetch,
            model=cfg.trigger_decider.llm_model,
            api_key=env.openai_api_key,
            reasoning_effort=cfg.trigger_decider.reasoning_effort,
            base_url=cfg.llm.base_url,
            skip_llm=skip_llm,
        )
    if not check.continue_send:
        return check

    conversation_events = (
        latest_ticket.get("_zd_agent_conversation_events")
        if latest_ticket
        else []
    )
    if conversation_has_pending_form(conversation_events or []):
        return FreshnessResult(
            continue_send=False,
            reason="pending_blocking_form",
            recovery=FreshnessRecovery(
                action=FreshnessRecoveryAction.DEFER,
                reason="pending_blocking_form",
            ),
        )
    return check


async def send_public_reply_with_freshness(
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
    internal_analysis: str = "",
    handoff_reason: str = "agent_handoff",
    handoff_source: str = "direct_public_reply",
    handoff_customer_phrase: str = "",
) -> DirectPublicReplyResult:
    """直接发 public reply 的公共入口（不需要 AgentSession）。

    给 trigger_consumer / 集成测试 / CLI 用。和 ZendeskPublicReply Tool 共享
    底层送达逻辑（footer 追加 + kill switch + browser relay），并在调用
    relay 前用客户消息快照做一次轻量 freshness check。

    Returns:
        (success, message_or_error)
    """
    if not isinstance(ticket_id, int) or isinstance(ticket_id, bool) or ticket_id <= 0:
        return DirectPublicReplyResult(False, "ticket_id must be a positive integer")

    # Kill switch
    cfg = get_config()
    if not getattr(cfg, "public_reply", None) or not cfg.public_reply.enabled:
        logger.warning(
            "public_reply_disabled_direct",
            ticket_id=ticket_id,
            reason="cfg.public_reply.enabled=false",
        )
        return DirectPublicReplyResult(
            False, "Public reply is currently disabled (kill switch)."
        )

    # 参数校验
    body = (body or "").strip()
    if not body:
        return DirectPublicReplyResult(False, "body is empty")
    if reply_type not in _VALID_REPLY_TYPES:
        return DirectPublicReplyResult(False, f"invalid reply_type: {reply_type!r}")
    privacy_blockers = public_reply_privacy_blockers(body)
    if privacy_blockers:
        logger.warning(
            "public_reply_privacy_hard_gate_blocked_direct",
            ticket_id=ticket_id,
            reply_type=reply_type,
            warning_count=len(privacy_blockers),
            warnings=privacy_blockers[:5],
        )
        return DirectPublicReplyResult(
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
        return DirectPublicReplyResult(True, "[Dry Run] Not sent.")
    if not conversation_id:
        logger.warning(
            "public_reply_missing_conversation_id_direct",
            ticket_id=ticket_id,
            reply_type=reply_type,
        )
        return DirectPublicReplyResult(
            False,
            "Zendesk Messaging conversation_id is missing; "
            "Support API public-comment fallback is disabled.",
        )
    if (
        reply_type in ("ask", "solution", "closed")
        and snapshot_customer_message_count is None
    ):
        return DirectPublicReplyResult(
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
        return DirectPublicReplyResult(
            False, "Pre-relay freshness check unavailable; public reply was blocked."
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
            return DirectPublicReplyResult(
                False, "Pre-relay freshness check unavailable; public reply was blocked."
            )
        if check.reason == "pending_blocking_form":
            return DirectPublicReplyResult(
                False, "Public reply deferred because a blocking customer form is pending."
            )
        return DirectPublicReplyResult(
            False,
            "Pre-relay freshness check abort: customer added "
            f"{check.new_messages_count} new message(s) ({check.reason}).",
        )

    try:
        logical_delivery_attempt_id = (
            delivery_attempt_id or f"direct:{ticket_id}:{uuid4().hex}"
        )
        (
            body_with_footer,
            internal_analysis,
            reply_type,
            delivery_store,
            note_store,
        ) = _prepare_durable_delivery_payload(
            cfg=cfg,
            ticket_id=ticket_id,
            reply_type=reply_type,
            delivery_attempt_id=logical_delivery_attempt_id,
            body_with_footer=body_with_footer,
            internal_analysis=internal_analysis,
            snapshot_customer_message_count=snapshot_customer_message_count,
            snapshot_customer_last_message_id=snapshot_customer_last_message_id,
        )
        handoff_store: PostPublicHandoffStore | None = None
        if reply_type == "handoff":
            handoff_store = _handoff_store_for_config(cfg)
            handoff_store.prepare(
                ticket_id=ticket_id,
                delivery_attempt_id=logical_delivery_attempt_id,
                body_with_footer=body_with_footer,
                public_delivery_confirmed=False,
                reason=handoff_reason,
                source=handoff_source,
                customer_phrase=handoff_customer_phrase,
            )
        logger.info(
            "public_reply_send_start_direct",
            ticket_id=ticket_id,
            reply_type=reply_type,
            body_len=len(body_with_footer),
        )
        sent_now = await _send_public_reply_safely(
            ticket_id=ticket_id,
            body_with_footer=body_with_footer,
            reply_type=reply_type,
            conversation_id=conversation_id,
            messaging_app_id=messaging_app_id,
            snapshot_customer_message_count=snapshot_customer_message_count,
            snapshot_customer_last_message_id=snapshot_customer_last_message_id,
            delivery_attempt_id=logical_delivery_attempt_id,
            delivery_store=delivery_store,
            internal_analysis=internal_analysis,
        )
        logger.info(
            "public_reply_sent_direct" if sent_now else "public_reply_already_delivered_direct",
            ticket_id=ticket_id, reply_type=reply_type,
        )
        internal_note_sent = await ensure_post_public_internal_note(
            ticket_id=ticket_id,
            delivery_attempt_id=logical_delivery_attempt_id,
            reply_type=reply_type,
            body_with_footer=body_with_footer,
            internal_analysis=internal_analysis,
            zendesk_tool_caller=call_zendesk_tool,
            note_store=note_store,
            public_delivery_confirmed=True,
        )
        if reply_type == "handoff" and handoff_store is not None:
            await ensure_post_public_handoff_effects(
                ticket_id=ticket_id,
                delivery_attempt_id=logical_delivery_attempt_id,
                body_with_footer=body_with_footer,
                store=handoff_store,
                public_delivery_confirmed=True,
                reason=handoff_reason,
                source=handoff_source,
                customer_phrase=handoff_customer_phrase,
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
            message = f"已发送 Public Reply 到工单 #{ticket_id}"
        else:
            message = f"Public Reply already delivered to ticket #{ticket_id}"
        if not internal_note_sent:
            message += "; internal analysis note delivery failed"
        recovery = check.recovery
        return DirectPublicReplyResult(
            True,
            message,
            accepted_snapshot_count=(
                recovery.next_snapshot_count
                if recovery is not None and recovery.next_snapshot_last_id
                else snapshot_customer_message_count
            ),
            accepted_snapshot_last_id=(
                recovery.next_snapshot_last_id
                if recovery is not None and recovery.next_snapshot_last_id
                else snapshot_customer_last_message_id
            ),
        )
    except Exception as e:  # noqa: BLE001 - public boundary returns a typed result.
        logger.error(
            "public_reply_direct_send_failed",
            ticket_id=ticket_id, error=str(e)[:200],
            exc_info=True,
        )
        return DirectPublicReplyResult(False, f"{type(e).__name__}: {e}")


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
    internal_analysis: str = "",
    handoff_reason: str = "agent_handoff",
    handoff_source: str = "direct_public_reply",
    handoff_customer_phrase: str = "",
) -> tuple[bool, str]:
    """Compatibility wrapper for callers that only need send success."""
    result = await send_public_reply_with_freshness(
        ticket_id=ticket_id,
        body=body,
        reply_type=reply_type,
        dry_run=dry_run,
        conversation_id=conversation_id,
        messaging_app_id=messaging_app_id,
        snapshot_customer_message_count=snapshot_customer_message_count,
        snapshot_customer_last_message_id=snapshot_customer_last_message_id,
        delivery_attempt_id=delivery_attempt_id,
        internal_analysis=internal_analysis,
        handoff_reason=handoff_reason,
        handoff_source=handoff_source,
        handoff_customer_phrase=handoff_customer_phrase,
    )
    return result.success, result.message


class ZendeskPublicReply(Tool):
    """发送公开回复（客户可见）。**只用于 SaaS messaging 工单**。

    客户回复和内部分析由本工具一次完成：

    - Public Reply 给**客户**看 + Zendesk 会发邮件通知客户 / 在 messaging widget 显示
    - 确认送达后自动写一条 Internal Analysis 给 Support Engineer 看

    工具自动追加 footer。ask/solution 提供"回复 human"逃生口；handoff 根据上海
    时间自动追加人工支持时段状态，并使用人工跟进专用 footer。LLM 在 ``body``
    里**不需要**也**不应该**自己写 footer，写了会被检测到不重复加。
    """

    name = "zendesk_public_reply"
    description = (
        "向 SaaS messaging 工单发送 Public Reply（客户可见的公开评论）。"
        "正文应该用 English（统一语言）。系统会自动追加 footer；ask/solution 会提示"
        "客户可回复 'human' 转人工，handoff 会使用人工跟进专用 footer。"
        "人工支持时间为上海时间 09:00（含）到 18:00（不含）；handoff 会自动追加"
        "当前是否处于支持时段和排队说明，**不要在 body 里重复写工作时间或 footer**。"
        "使用易读纯文本和短段落；不要使用 Markdown 标题、粗体、反引号、emoji"
        "或装饰性分隔线。"
        "**不要包含源码路径、内部仓库路径、文件名+行号或内部代码实现位置**。"
        "公开回复确认送达后，系统会自动写入 internal_analysis 作为内部备注。"
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
                    "公开回复正文，**English only**，易读纯文本格式。先给结论，"
                    "再用短段落和必要的 '- ' 列表说明步骤；不要使用 Markdown 标题、"
                    "粗体、反引号、emoji 或装饰性分隔线。"
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
            "internal_analysis": {
                "type": "string",
                "description": (
                    "客户不可见的内部分析。用中文简明记录分类、已确认事实、"
                    "关键证据、仍未知内容和后续动作。公开回复成功后系统会自动"
                    "把它写入 Internal Note；不要把这些内部信息写进 body。"
                ),
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
        raw_internal_analysis = args.get("internal_analysis", "")

        # 先做类型校验，再 strip，避免非法工具参数变成 AttributeError。
        if "ticket_id" not in args:
            return ToolResult(success=False, error="ticket_id is required")
        if not isinstance(ticket_id, int) or isinstance(ticket_id, bool) or ticket_id <= 0:
            return ToolResult(
                success=False,
                error=f"invalid ticket_id: {ticket_id!r}",
            )
        if ticket_id != session.ticket_id:
            return ToolResult(
                success=False,
                error="ticket_id does not match the active agent session",
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
        if not isinstance(raw_internal_analysis, str):
            return ToolResult(success=False, error="internal_analysis must be a string")

        body = raw_body.strip()
        conversation_id = (raw_conversation_id or "").strip() or None
        messaging_app_id = (raw_messaging_app_id or "").strip() or None
        internal_analysis = raw_internal_analysis.strip()

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
        pending_images = getattr(session, "image_urls", None)
        if (
            reply_type in {"ask", "solution", "closed"}
            and isinstance(pending_images, list)
            and pending_images
            and getattr(session, "images_injected", False) is not True
        ):
            logger.warning(
                "public_reply_blocked_pending_image_injection",
                ticket_id=ticket_id,
                reply_type=reply_type,
                pending_image_count=len(pending_images),
            )
            return ToolResult(
                success=False,
                error=(
                    "Public reply blocked because queued ticket images have not "
                    "been injected into the model context yet. Finish this tool "
                    "batch, inspect the injected images in the next round, then "
                    "generate the reply again."
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

        # 4b. Pre-send freshness + Answer Bot form readiness check（M5）。
        # handoff 不比较 customer snapshot，但仍须读取最新完整 conversation，
        # 防止 dispatch 后出现的 blocking form 被 system-event 盲区漏掉。
        customer_resolvable_reply = reply_type in ("ask", "solution", "closed")
        if (
            customer_resolvable_reply
            and not session.customer_msgs_snapshot_captured
        ):
            return ToolResult(
                success=False,
                error=(
                    "Public reply blocked because the customer-message "
                    "snapshot is unavailable. Re-fetch the ticket before replying."
                ),
            )
        try:
            check = await _check_public_reply_readiness(
                ticket_id=ticket_id,
                snapshot_count=(
                    session.customer_msgs_snapshot_count
                    if customer_resolvable_reply
                    else None
                ),
                snapshot_last_id=(
                    (session.customer_msgs_snapshot_last_id or None)
                    if customer_resolvable_reply
                    else None
                ),
                skip_llm=False,
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
                error = (
                    "Pre-send freshness check unavailable; public reply was "
                    "blocked to avoid sending a stale response."
                )
            elif check.reason == "pending_blocking_form":
                error = (
                    "Public reply deferred because a blocking customer form "
                    "is pending. Wait for the matching form response."
                )
            else:
                error = (
                    f"Pre-send check abort: customer added "
                    f"{check.new_messages_count} new substantive message(s) "
                    f"({check.reason}). Re-analyze with fresh context."
                )
            return ToolResult(
                success=False,
                error=error,
                metadata={"freshness_recovery": check.recovery},
            )

        # 5. 真发送：只走 native messaging browser relay，不回退 Support API。
        logical_delivery_attempt_id = (
            session.delivery_attempt_id or session.session_id
        )
        try:
            (
                body_with_footer,
                internal_analysis,
                reply_type,
                delivery_store,
                note_store,
            ) = _prepare_durable_delivery_payload(
                cfg=cfg,
                ticket_id=ticket_id,
                reply_type=reply_type,
                delivery_attempt_id=logical_delivery_attempt_id,
                body_with_footer=body_with_footer,
                internal_analysis=internal_analysis,
                snapshot_customer_message_count=(
                    session.customer_msgs_snapshot_count
                    if session.customer_msgs_snapshot_captured
                    else None
                ),
                snapshot_customer_last_message_id=(
                    session.customer_msgs_snapshot_last_id or None
                ),
            )
            session.reply_draft = body_with_footer
            session.public_reply_type = reply_type
            handoff_store: PostPublicHandoffStore | None = None
            if reply_type == "handoff":
                handoff_store = _handoff_store_for_config(cfg)
                handoff_store.prepare(
                    ticket_id=ticket_id,
                    delivery_attempt_id=logical_delivery_attempt_id,
                    body_with_footer=body_with_footer,
                    public_delivery_confirmed=False,
                )
            logger.info(
                "public_reply_send_start",
                ticket_id=ticket_id,
                reply_type=reply_type,
                body_len=len(body_with_footer),
            )
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
                delivery_attempt_id=logical_delivery_attempt_id,
                delivery_store=delivery_store,
                internal_analysis=internal_analysis,
            )
            # Customer delivery is now confirmed (or reconciled from the
            # outbox).  Block any alternative second reply in this session even
            # if the mandatory internal-note bookkeeping below needs recovery.
            session.reply_sent = True
            _advance_session_snapshot_from_accepted_freshness(session, check)
            logger.info(
                "public_reply_sent" if sent_now else "public_reply_already_delivered",
                ticket_id=ticket_id, reply_type=reply_type,
            )
            internal_note_sent = await ensure_post_public_internal_note(
                ticket_id=ticket_id,
                delivery_attempt_id=logical_delivery_attempt_id,
                reply_type=reply_type,
                body_with_footer=body_with_footer,
                internal_analysis=internal_analysis,
                zendesk_tool_caller=call_zendesk_tool,
                note_store=note_store,
                public_delivery_confirmed=True,
            )
            if reply_type == "handoff" and handoff_store is not None:
                handoff_effects_sent = await ensure_post_public_handoff_effects(
                    ticket_id=ticket_id,
                    delivery_attempt_id=logical_delivery_attempt_id,
                    body_with_footer=body_with_footer,
                    store=handoff_store,
                    public_delivery_confirmed=True,
                )
                logger.info(
                    "handoff_operator_effects_completed"
                    if handoff_effects_sent
                    else "handoff_operator_effects_pending",
                    ticket_id=ticket_id,
                    delivery_attempt_id=logical_delivery_attempt_id,
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
                output = (
                    f"已发送 Public Reply 到工单 #{ticket_id}"
                    f"（reply_type={reply_type}）"
                )
            else:
                output = (
                    f"Public Reply already delivered to ticket #{ticket_id}"
                    f"（reply_type={reply_type}）"
                )
            if not internal_note_sent:
                output += "；公开回复已送达，但内部分析备注写入失败"
            return ToolResult(output=output)
        except BrowserRelayStaleContextError as e:
            try:
                late_check = await _check_freshness_before_relay(
                    ticket_id=ticket_id,
                    snapshot_count=(
                        session.customer_msgs_snapshot_count
                        if session.customer_msgs_snapshot_captured else None
                    ),
                    snapshot_last_id=(
                        session.customer_msgs_snapshot_last_id or None
                    ),
                )
            except Exception as refresh_error:  # noqa: BLE001
                logger.warning(
                    "public_reply_late_stale_refresh_failed",
                    ticket_id=ticket_id,
                    error=str(refresh_error)[:200],
                )
                return ToolResult(
                    success=False,
                    error=f"{type(e).__name__}: {e}; refresh failed",
                )
            logger.info(
                "public_reply_late_stale_recovered",
                ticket_id=ticket_id,
                reason=late_check.reason if late_check else "no_snapshot",
                new_msg_count=(
                    late_check.new_messages_count if late_check else 0
                ),
            )
            return ToolResult(
                success=False,
                error=(
                    "Browser relay aborted stale customer context; "
                    "re-analyze the newly fetched messages."
                ),
                metadata={
                    "freshness_recovery": (
                        late_check.recovery if late_check else None
                    )
                },
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

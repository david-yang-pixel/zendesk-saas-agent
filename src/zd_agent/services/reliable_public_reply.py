"""Reliable public delivery boundary owned by casework v2.

This module deliberately owns only customer-resolvable replies.  Human
ownership is a separate typed casework effect, so a requested ``handoff`` can
never create legacy handoff intent here.  A migrated outbox row may still carry
``handoff`` as immutable transport metadata; that frozen row is reconciled
without importing or invoking the legacy handoff-effect service.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from zd_agent.casework.adapters.language import public_reply_english_blockers
from zd_agent.config import AppConfig, EnvSettings, get_config
from zd_agent.integrations.zendesk_browser_relay import (
    BrowserRelayDefiniteUnsentError,
    send_browser_relay_reply,
)
from zd_agent.integrations.zendesk_mcp import call_zendesk_tool
from zd_agent.log import get_logger
from zd_agent.runtime.pre_send_check import (
    FreshnessRecovery,
    FreshnessRecoveryAction,
    FreshnessResult,
)
from zd_agent.runtime.reply_audit import public_reply_privacy_blockers
from zd_agent.services.assignee import is_human_assignee
from zd_agent.services.messaging_context import get_messaging_context
from zd_agent.services.public_reply_delivery_store import (
    PublicReplyDeliveryStore,
)
from zd_agent.services.public_reply_internal_note import (
    PostPublicInternalNoteStore,
    ensure_post_public_internal_note,
)
from zd_agent.tools.public_reply_delivery import (
    recent_duplicate_public_reply_exists,
    send_public_reply_safely,
)
from zd_agent.tools.public_reply_format import _append_footer


ReliablePublicReplyType = Literal["ask", "solution", "closed"]
EffectivePublicReplyType = Literal["ask", "solution", "handoff", "closed"]
_REQUESTED_REPLY_TYPES = frozenset({"ask", "solution", "closed"})
_EFFECTIVE_REPLY_TYPES = frozenset({"ask", "solution", "handoff", "closed"})
_AUTOMATION_PUBLIC_REPLY_MARKERS = (
    "This reply was sent by Dify AI Agent.",
    "This reply was sent by Dify AI Support.",
    "Dify SaaS Agent",
)

logger = get_logger(module="services.reliable_public_reply")

MessagingContextGetter = Callable[[int], dict[str, str]]
ReadinessChecker = Callable[..., Awaitable[FreshnessResult]]
DeliveryStoreFactory = Callable[[AppConfig], PublicReplyDeliveryStore]
PreparedTransportSender = Callable[..., Awaitable[bool]]
ZendeskToolCaller = Callable[..., Awaitable[dict[str, Any]]]
RelaySender = Callable[..., Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class ReliablePublicReplyResult:
    success: bool
    message: str
    accepted_snapshot_count: int | None = None
    accepted_snapshot_last_id: str | None = None
    historical_delivery_confirmed: bool = False
    covers_requested_snapshot: bool = True
    paused: bool = False
    definitely_unsent: bool = False


@dataclass(frozen=True, slots=True)
class PreparedPublicReplyDelivery:
    """Frozen payload and durable stores for one logical public delivery."""

    cfg: AppConfig
    ticket_id: int
    delivery_attempt_id: str
    requested_reply_type: ReliablePublicReplyType
    effective_reply_type: EffectivePublicReplyType
    body_with_footer: str
    internal_analysis: str
    conversation_id: str
    messaging_app_id: str | None
    snapshot_customer_message_count: int | None
    snapshot_customer_last_message_id: str | None
    accepted_snapshot_count: int | None
    accepted_snapshot_last_id: str | None
    delivery_store: PublicReplyDeliveryStore
    note_store: PostPublicInternalNoteStore | None
    delivery_key: str
    migrated_from_v1: bool
    already_delivered: bool
    covers_requested_snapshot: bool
    body_chars: int


class ReliablePublicReplyRejected(ValueError):
    """A fail-closed preparation rejection before customer delivery."""


class ReliablePublicReplyDefinitelyUnsent(ReliablePublicReplyRejected):
    """Preparation stopped before any remote customer-visible write."""


class ReliablePublicReplyPaused(ReliablePublicReplyRejected):
    """The operator kill switch paused this effect without failing it."""


def _delivery_store_for_config(cfg: AppConfig) -> PublicReplyDeliveryStore:
    return PublicReplyDeliveryStore(_delivery_store_path(cfg))


def _delivery_store_path(cfg: AppConfig) -> Path:
    state_dir = Path(cfg.resources.base_dir) / cfg.resources.state_dir
    return state_dir / "public_reply_outbox.sqlite3"


def _validated_request(
    *,
    ticket_id: int,
    body: str,
    reply_type: str,
    snapshot_customer_message_count: int | None,
    snapshot_customer_last_message_id: str | None,
    delivery_attempt_id: str,
    internal_analysis: str,
) -> tuple[str, ReliablePublicReplyType, str, str | None, str, str]:
    if not isinstance(ticket_id, int) or isinstance(ticket_id, bool) or ticket_id <= 0:
        raise ReliablePublicReplyRejected("ticket_id must be a positive integer")
    if reply_type not in _REQUESTED_REPLY_TYPES:
        raise ReliablePublicReplyRejected(
            "invalid casework reply_type: "
            f"{reply_type!r}; must be ask, solution, or closed"
        )
    if not isinstance(body, str) or not body.strip():
        raise ReliablePublicReplyRejected("body is empty")
    language_blockers = public_reply_english_blockers(body.strip())
    if language_blockers:
        raise ReliablePublicReplyRejected(
            "Public reply English hard gate blocked this reply. "
            "Regenerate the customer response in English before delivery."
        )
    blockers = public_reply_privacy_blockers(body.strip())
    if blockers:
        raise ReliablePublicReplyRejected(
            "Public reply privacy hard gate blocked this reply. "
            "Remove internal source paths before replying: " + "; ".join(blockers[:5])
        )
    if (
        not isinstance(snapshot_customer_message_count, int)
        or isinstance(snapshot_customer_message_count, bool)
        or snapshot_customer_message_count < 0
    ):
        raise ReliablePublicReplyRejected(
            "Public reply blocked because the customer-message snapshot is unavailable."
        )
    if snapshot_customer_last_message_id is not None and not isinstance(
        snapshot_customer_last_message_id,
        str,
    ):
        raise ReliablePublicReplyRejected(
            "snapshot_customer_last_message_id must be a string"
        )
    if not isinstance(delivery_attempt_id, str) or not delivery_attempt_id.strip():
        raise ReliablePublicReplyRejected("delivery_attempt_id is required")
    if not isinstance(internal_analysis, str):
        raise ReliablePublicReplyRejected("internal_analysis must be a string")
    return (
        body.strip(),
        cast(ReliablePublicReplyType, reply_type),
        delivery_attempt_id.strip(),
        (snapshot_customer_last_message_id or "").strip() or None,
        internal_analysis.strip(),
        _append_footer(body.strip(), reply_type),
    )


def _accepted_snapshot(
    check: FreshnessResult,
    *,
    snapshot_count: int,
    snapshot_last_id: str | None,
) -> tuple[int | None, str | None]:
    recovery = check.recovery
    if recovery is not None and recovery.next_snapshot_last_id:
        return recovery.next_snapshot_count, recovery.next_snapshot_last_id
    return snapshot_count, snapshot_last_id


def _delivered_attempt_covers_snapshot(
    attempt_snapshot_count: int | None,
    attempt_snapshot_last_id: str,
    *,
    requested_snapshot_count: int,
    requested_snapshot_last_id: str | None,
) -> bool:
    """Prove that an old confirmed send covered the caller's current cursor."""
    if attempt_snapshot_count is None:
        return False
    if attempt_snapshot_count != requested_snapshot_count:
        return False
    return (attempt_snapshot_last_id or None) == requested_snapshot_last_id


async def prepare_reliable_public_reply(
    *,
    ticket_id: int,
    body: str,
    reply_type: str,
    snapshot_customer_message_count: int | None,
    snapshot_customer_last_message_id: str | None,
    delivery_attempt_id: str,
    internal_analysis: str,
    conversation_id: str | None = None,
    messaging_app_id: str | None = None,
    current_snapshot_customer_message_count: int | None = None,
    current_snapshot_customer_last_message_id: str | None = None,
    cfg: AppConfig | None = None,
    context_getter: MessagingContextGetter | None = None,
    readiness_checker: ReadinessChecker | None = None,
    delivery_store_factory: DeliveryStoreFactory | None = None,
    zendesk_tool_caller: ZendeskToolCaller | None = None,
) -> PreparedPublicReplyDelivery:
    """Validate freshness, then freeze public and internal-note intent."""
    (
        normalized_body,
        requested_reply_type,
        delivery_attempt_id,
        snapshot_last_id,
        internal_analysis,
        body_with_footer,
    ) = _validated_request(
        ticket_id=ticket_id,
        body=body,
        reply_type=reply_type,
        snapshot_customer_message_count=snapshot_customer_message_count,
        snapshot_customer_last_message_id=snapshot_customer_last_message_id,
        delivery_attempt_id=delivery_attempt_id,
        internal_analysis=internal_analysis,
    )
    snapshot_count = cast(int, snapshot_customer_message_count)
    if current_snapshot_customer_message_count is not None and (
        not isinstance(current_snapshot_customer_message_count, int)
        or isinstance(current_snapshot_customer_message_count, bool)
        or current_snapshot_customer_message_count < 0
    ):
        raise ReliablePublicReplyRejected(
            "current_snapshot_customer_message_count must be a non-negative integer"
        )
    if current_snapshot_customer_last_message_id is not None and not isinstance(
        current_snapshot_customer_last_message_id,
        str,
    ):
        raise ReliablePublicReplyRejected(
            "current_snapshot_customer_last_message_id must be a string"
        )
    current_snapshot_last_id = (
        current_snapshot_customer_last_message_id or ""
    ).strip() or None
    cfg = cfg or get_config()
    if not getattr(cfg, "public_reply", None) or not cfg.public_reply.enabled:
        raise ReliablePublicReplyPaused(
            "Public reply is currently disabled (kill switch)."
        )
    if conversation_id is not None and not isinstance(conversation_id, str):
        raise ReliablePublicReplyRejected("conversation_id must be a string")
    if messaging_app_id is not None and not isinstance(messaging_app_id, str):
        raise ReliablePublicReplyRejected("messaging_app_id must be a string")
    conversation_id = (conversation_id or "").strip() or None
    messaging_app_id = (messaging_app_id or "").strip() or None
    if not conversation_id:
        context = (context_getter or get_messaging_context)(ticket_id)
        conversation_id = str(context.get("conversation_id") or "").strip() or None
        messaging_app_id = (
            messaging_app_id or str(context.get("app_id") or "").strip() or None
        )
    if not conversation_id:
        raise ReliablePublicReplyRejected(
            "Zendesk Messaging conversation_id is missing; "
            "Support API public-comment fallback is disabled."
        )

    # A crash may leave the outer casework effect unconfirmed even though the
    # inner public-delivery outbox is already delivered.  Read that exact
    # frozen attempt before freshness so a newer customer cursor can promote
    # the old outer effect and requeue a distinct logical generation.  Do not
    # create a new outbox before freshness for a brand-new attempt.
    delivery_store: PublicReplyDeliveryStore | None = None
    existing_attempt = None
    if _delivery_store_path(cfg).exists():
        delivery_store = (delivery_store_factory or _delivery_store_for_config)(cfg)
        existing_attempt = delivery_store.get(
            delivery_attempt_id=delivery_attempt_id,
            ticket_id=ticket_id,
        )
    if (
        existing_attempt is not None
        and existing_attempt.reply_type == "handoff"
        and not existing_attempt.delivered
    ):
        raise ReliablePublicReplyRejected(
            "Undelivered legacy handoff is quarantined; automatic delivery is blocked."
        )
    if (
        existing_attempt is not None
        and existing_attempt.ambiguous
        and not existing_attempt.delivered
    ):
        assert delivery_store is not None
        caller = zendesk_tool_caller or call_zendesk_tool
        try:
            remote_match = await recent_duplicate_public_reply_exists(
                ticket_id=ticket_id,
                body_with_footer=existing_attempt.body,
                not_before=existing_attempt.created_at,
                zendesk_tool_caller=caller,
            )
        except Exception as error:
            raise ReliablePublicReplyRejected(
                "Ambiguous public reply lookup failed; manual reconciliation "
                "is required and automatic resend remains blocked."
            ) from error
        if not remote_match:
            raise ReliablePublicReplyRejected(
                "Public reply delivery is ambiguous and requires manual "
                "reconciliation; automatic resend is blocked."
            )
        if not delivery_store.confirm_ambiguous_delivered(
            existing_attempt.delivery_key,
        ):
            refreshed = delivery_store.get(
                delivery_attempt_id=delivery_attempt_id,
                ticket_id=ticket_id,
            )
            if refreshed is None or not refreshed.delivered:
                raise ReliablePublicReplyRejected(
                    "Ambiguous public reply could not be confirmed locally; "
                    "manual reconciliation is required."
                )
        existing_attempt = delivery_store.get(
            delivery_attempt_id=delivery_attempt_id,
            ticket_id=ticket_id,
        )
    confirmed_before_check = bool(
        existing_attempt is not None and existing_attempt.delivered
    )

    checker = readiness_checker or _check_freshness_before_relay
    try:
        check = await checker(
            ticket_id=ticket_id,
            snapshot_count=snapshot_count,
            snapshot_last_id=snapshot_last_id,
        )
    except Exception as error:
        logger.warning(
            "reliable_public_reply_freshness_error",
            ticket_id=ticket_id,
            error=str(error)[:200],
        )
        if not confirmed_before_check:
            raise ReliablePublicReplyDefinitelyUnsent(
                "Pre-relay freshness check unavailable; public reply was blocked."
            ) from error
        check = FreshnessResult(
            continue_send=False,
            reason=f"fetch_error: {type(error).__name__}",
            recovery=FreshnessRecovery(
                action=FreshnessRecoveryAction.RETRY_FETCH,
                reason=f"fetch_error: {type(error).__name__}",
            ),
        )
    if not check.continue_send and not confirmed_before_check:
        if check.reason.startswith("fetch_error"):
            message = "Pre-relay freshness check unavailable; public reply was blocked."
        elif check.reason == "pending_blocking_form":
            message = (
                "Public reply deferred because a blocking customer form is pending."
            )
        else:
            message = (
                "Pre-relay freshness check abort: customer added "
                f"{check.new_messages_count} new message(s) ({check.reason})."
            )
        raise ReliablePublicReplyDefinitelyUnsent(message)

    if delivery_store is None:
        delivery_store = (delivery_store_factory or _delivery_store_for_config)(cfg)
    attempt = delivery_store.prepare(
        delivery_attempt_id=delivery_attempt_id,
        ticket_id=ticket_id,
        reply_type=requested_reply_type,
        body=body_with_footer,
        internal_analysis=internal_analysis,
        snapshot_customer_message_count=snapshot_count,
        snapshot_customer_last_message_id=snapshot_last_id,
    )
    if attempt.reply_type not in _EFFECTIVE_REPLY_TYPES:
        raise ReliablePublicReplyRejected(
            "Frozen public reply has an unsupported effective reply_type."
        )
    effective_reply_type = cast(EffectivePublicReplyType, attempt.reply_type)
    if effective_reply_type == "handoff" and not attempt.delivered:
        raise ReliablePublicReplyRejected(
            "Undelivered legacy handoff is quarantined; automatic delivery is blocked."
        )
    if (
        not attempt.delivered
        and effective_reply_type in _REQUESTED_REPLY_TYPES
        and attempt.snapshot_customer_message_count is None
    ):
        raise ReliablePublicReplyRejected(
            "Frozen public reply lacks a customer-message snapshot; delivery is blocked."
        )
    note_store: PostPublicInternalNoteStore | None = None
    if cfg.public_reply.internal_audit_note_enabled:
        note_store = PostPublicInternalNoteStore(
            delivery_store.path.parent / "post_public_internal_note_outbox.sqlite3"
        )
        note_store.prepare(
            ticket_id=ticket_id,
            delivery_attempt_id=delivery_attempt_id,
            reply_type=effective_reply_type,
            body_with_footer=attempt.body,
            internal_analysis=attempt.internal_analysis,
            public_delivery_confirmed=attempt.delivered,
            accept_legacy_reference=attempt.migrated_from_v1,
        )
    checked_count, checked_last_id = _accepted_snapshot(
        check,
        snapshot_count=snapshot_count,
        snapshot_last_id=snapshot_last_id,
    )
    coverage_count = (
        current_snapshot_customer_message_count
        if current_snapshot_customer_message_count is not None
        else checked_count
    )
    coverage_last_id = (
        current_snapshot_last_id
        if current_snapshot_customer_message_count is not None
        or current_snapshot_customer_last_message_id is not None
        else checked_last_id
    )
    covers_requested_snapshot = not attempt.delivered or (
        check.continue_send
        and coverage_count is not None
        and _delivered_attempt_covers_snapshot(
            attempt.snapshot_customer_message_count,
            attempt.snapshot_customer_last_message_id,
            requested_snapshot_count=coverage_count,
            requested_snapshot_last_id=coverage_last_id,
        )
    )
    if attempt.delivered and not covers_requested_snapshot:
        accepted_count = attempt.snapshot_customer_message_count
        accepted_last_id = attempt.snapshot_customer_last_message_id or None
    else:
        accepted_count, accepted_last_id = checked_count, checked_last_id
    return PreparedPublicReplyDelivery(
        cfg=cfg,
        ticket_id=ticket_id,
        delivery_attempt_id=delivery_attempt_id,
        requested_reply_type=requested_reply_type,
        effective_reply_type=effective_reply_type,
        body_with_footer=attempt.body,
        internal_analysis=attempt.internal_analysis,
        conversation_id=conversation_id,
        messaging_app_id=messaging_app_id,
        snapshot_customer_message_count=(attempt.snapshot_customer_message_count),
        snapshot_customer_last_message_id=(
            attempt.snapshot_customer_last_message_id or None
        ),
        accepted_snapshot_count=accepted_count,
        accepted_snapshot_last_id=accepted_last_id,
        delivery_store=delivery_store,
        note_store=note_store,
        delivery_key=attempt.delivery_key,
        migrated_from_v1=attempt.migrated_from_v1,
        already_delivered=attempt.delivered,
        covers_requested_snapshot=covers_requested_snapshot,
        body_chars=len(normalized_body),
    )


async def _check_freshness_before_relay(
    *,
    ticket_id: int,
    snapshot_count: int | None,
    snapshot_last_id: str | None,
) -> FreshnessResult:
    return await _check_public_reply_readiness(
        ticket_id=ticket_id,
        snapshot_count=snapshot_count,
        snapshot_last_id=snapshot_last_id,
        skip_llm=True,
    )


def _newer_human_public_comment_exists(
    *,
    ticket: dict[str, Any],
    customer_messages: list[dict[str, Any]],
    cfg: AppConfig,
) -> bool:
    comments = ticket.get("_zd_agent_comments") or []
    if not isinstance(comments, list):
        return True
    requester_id = ticket.get("requester_id")
    agent_user_id = cfg.zendesk.agent_user_id
    latest_customer = customer_messages[-1] if customer_messages else {}
    latest_customer_id = str(latest_customer.get("id") or "")
    latest_customer_at = str(latest_customer.get("created_at") or "")
    customer_comment_index = next(
        (
            index
            for index, comment in enumerate(comments)
            if isinstance(comment, dict)
            and str(comment.get("id") or "") == latest_customer_id
        ),
        None,
    )
    for index, comment in enumerate(comments):
        if not isinstance(comment, dict) or not comment.get("public"):
            continue
        author_id = comment.get("author_id")
        if str(author_id or "") == str(requester_id or ""):
            continue
        body = str(
            comment.get("plain_body")
            or comment.get("body")
            or comment.get("html_body")
            or ""
        )
        if (
            agent_user_id
            and str(author_id or "") == str(agent_user_id)
            and any(marker in body for marker in _AUTOMATION_PUBLIC_REPLY_MARKERS)
        ):
            continue
        if customer_comment_index is not None and index > customer_comment_index:
            return True
        comment_created_at = str(comment.get("created_at") or "")
        if latest_customer_at and comment_created_at:
            if comment_created_at > latest_customer_at:
                return True
            continue
        # Without a cross-channel timestamp/order proof, a public comment by a
        # non-customer identity is unsafe to race with an automated send.
        return True
    return False


def _ticket_readiness_blocker(
    *,
    ticket: dict[str, Any],
    customer_messages: list[dict[str, Any]],
    cfg: AppConfig,
) -> str:
    status = str(ticket.get("status") or "").strip().casefold()
    if status in {"solved", "closed"}:
        return f"ticket_status_{status}"
    if is_human_assignee(
        ticket.get("assignee_id"),
        agent_user_id=cfg.zendesk.agent_user_id,
        non_human_assignee_ids=cfg.zendesk.non_human_assignee_ids,
    ):
        return "human_assignee"
    if _newer_human_public_comment_exists(
        ticket=ticket,
        customer_messages=customer_messages,
        cfg=cfg,
    ):
        return "newer_human_public_comment"
    return ""


async def _check_public_reply_readiness(
    *,
    ticket_id: int,
    snapshot_count: int | None,
    snapshot_last_id: str | None,
    skip_llm: bool,
    cfg: AppConfig | None = None,
) -> FreshnessResult:
    """Fetch once, then verify customer freshness and blocking-form readiness."""
    from zd_agent.runtime.pre_send_check import pre_send_check_or_abort
    from zd_agent.services.ticket_message_parsing import (
        conversation_has_pending_form,
    )
    from zd_agent.services.ticket_messages import _fetch_customer_messages

    latest_ticket: dict[str, Any] | None = None
    latest_customer_messages: list[dict[str, Any]] = []
    cfg = cfg or get_config()

    async def _fetch() -> list[dict[str, Any]]:
        nonlocal latest_customer_messages, latest_ticket
        messages, ticket = await _fetch_customer_messages(ticket_id)
        if ticket is None:
            raise RuntimeError(f"ticket fetch failed for #{ticket_id}")
        latest_ticket = ticket
        latest_customer_messages = messages
        return messages

    if snapshot_count is None:
        try:
            await _fetch()
        except Exception as error:
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
        env = EnvSettings()
        llm_provider = cfg.llm.provider.strip().casefold()
        llm_api_key = (
            env.anthropic_api_key if llm_provider == "anthropic" else env.openai_api_key
        )
        check = await pre_send_check_or_abort(
            ticket_id=ticket_id,
            snapshot_count=snapshot_count,
            snapshot_last_id=snapshot_last_id,
            fetch_messages_fn=_fetch,
            provider=llm_provider,
            model=cfg.llm.model,
            api_key=llm_api_key,
            reasoning_effort=cfg.llm.reasoning_effort,
            base_url=cfg.llm.base_url if llm_provider == "openai" else None,
            skip_llm=skip_llm,
        )
    if not check.continue_send:
        return check
    if latest_ticket is None:
        return FreshnessResult(
            continue_send=False,
            reason="fetch_error: missing_ticket",
            recovery=FreshnessRecovery(
                action=FreshnessRecoveryAction.RETRY_FETCH,
                reason="fetch_error: missing_ticket",
            ),
        )
    readiness_blocker = _ticket_readiness_blocker(
        ticket=latest_ticket,
        customer_messages=latest_customer_messages,
        cfg=cfg,
    )
    if readiness_blocker:
        return FreshnessResult(
            continue_send=False,
            reason=readiness_blocker,
            recovery=FreshnessRecovery(
                action=FreshnessRecoveryAction.DEFER,
                reason=readiness_blocker,
            ),
        )
    conversation_events = (
        latest_ticket.get("_zd_agent_conversation_events") if latest_ticket else []
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


async def _deliver_transport(
    prepared: PreparedPublicReplyDelivery,
    *,
    transport_sender: PreparedTransportSender | None,
    zendesk_tool_caller: ZendeskToolCaller,
    relay_sender: RelaySender,
) -> bool:
    if transport_sender is not None:
        return await transport_sender(
            ticket_id=prepared.ticket_id,
            body_with_footer=prepared.body_with_footer,
            reply_type=prepared.effective_reply_type,
            conversation_id=prepared.conversation_id,
            messaging_app_id=prepared.messaging_app_id,
            snapshot_customer_message_count=(prepared.snapshot_customer_message_count),
            snapshot_customer_last_message_id=(
                prepared.snapshot_customer_last_message_id
            ),
            delivery_attempt_id=prepared.delivery_attempt_id,
            delivery_store=prepared.delivery_store,
            internal_analysis=prepared.internal_analysis,
        )
    return await send_public_reply_safely(
        cfg=prepared.cfg,
        ticket_id=prepared.ticket_id,
        body_with_footer=prepared.body_with_footer,
        reply_type=prepared.effective_reply_type,
        conversation_id=prepared.conversation_id,
        snapshot_customer_message_count=(prepared.snapshot_customer_message_count),
        snapshot_customer_last_message_id=(prepared.snapshot_customer_last_message_id),
        delivery_attempt_id=prepared.delivery_attempt_id,
        delivery_store=prepared.delivery_store,
        delivery_store_factory=_delivery_store_for_config,
        zendesk_tool_caller=zendesk_tool_caller,
        relay_sender=relay_sender,
        internal_analysis=prepared.internal_analysis,
    )


async def deliver_prepared_public_reply(
    prepared: PreparedPublicReplyDelivery,
    *,
    transport_sender: PreparedTransportSender | None = None,
    zendesk_tool_caller: ZendeskToolCaller | None = None,
    relay_sender: RelaySender | None = None,
) -> ReliablePublicReplyResult:
    """Send or reconcile one frozen delivery, then drain its note intent."""
    caller = zendesk_tool_caller or call_zendesk_tool
    relay = relay_sender or send_browser_relay_reply
    try:
        sent_now = await _deliver_transport(
            prepared,
            transport_sender=transport_sender,
            zendesk_tool_caller=caller,
            relay_sender=relay,
        )
    except Exception as error:
        logger.error(
            "reliable_public_reply_delivery_failed",
            ticket_id=prepared.ticket_id,
            error=str(error)[:200],
            exc_info=True,
        )
        return ReliablePublicReplyResult(
            False,
            f"{type(error).__name__}: {error}",
            definitely_unsent=isinstance(error, BrowserRelayDefiniteUnsentError),
        )

    internal_note_sent = True
    if prepared.note_store is not None:
        try:
            internal_note_sent = await ensure_post_public_internal_note(
                ticket_id=prepared.ticket_id,
                delivery_attempt_id=prepared.delivery_attempt_id,
                reply_type=prepared.effective_reply_type,
                body_with_footer=prepared.body_with_footer,
                internal_analysis=prepared.internal_analysis,
                zendesk_tool_caller=caller,
                note_store=prepared.note_store,
                public_delivery_confirmed=True,
                accept_legacy_reference=prepared.migrated_from_v1,
            )
        except Exception as error:
            internal_note_sent = False
            logger.error(
                "reliable_public_reply_internal_note_failed",
                ticket_id=prepared.ticket_id,
                error=str(error)[:200],
                exc_info=True,
            )

    try:
        from zd_agent.observability.metrics import emit_agent_public_reply_sent

        if sent_now:
            emit_agent_public_reply_sent(
                ticket_id=prepared.ticket_id,
                reply_type=prepared.effective_reply_type,
                body_chars=prepared.body_chars,
            )
    except Exception as error:
        logger.warning(
            "reliable_public_reply_metric_failed",
            ticket_id=prepared.ticket_id,
            error_type=type(error).__name__,
        )

    if not prepared.covers_requested_snapshot:
        message = (
            f"Historical public reply delivery for ticket #{prepared.ticket_id} "
            "was reconciled, but it does not cover the current customer "
            "snapshot; the newer customer message remains unanswered."
        )
        if not internal_note_sent:
            message += "; internal analysis note delivery failed"
        return ReliablePublicReplyResult(
            False,
            message,
            accepted_snapshot_count=prepared.accepted_snapshot_count,
            accepted_snapshot_last_id=prepared.accepted_snapshot_last_id,
            historical_delivery_confirmed=True,
            covers_requested_snapshot=False,
        )

    message = (
        f"Public reply sent to ticket #{prepared.ticket_id}"
        if sent_now
        else f"Public reply already delivered to ticket #{prepared.ticket_id}"
    )
    if not internal_note_sent:
        message += "; internal analysis note delivery failed"
    return ReliablePublicReplyResult(
        True,
        message,
        accepted_snapshot_count=prepared.accepted_snapshot_count,
        accepted_snapshot_last_id=prepared.accepted_snapshot_last_id,
    )


async def send_casework_public_reply_with_freshness(
    *,
    ticket_id: int,
    body: str,
    reply_type: ReliablePublicReplyType,
    snapshot_customer_message_count: int | None,
    snapshot_customer_last_message_id: str | None,
    delivery_attempt_id: str,
    internal_analysis: str,
    conversation_id: str | None = None,
    messaging_app_id: str | None = None,
    current_snapshot_customer_message_count: int | None = None,
    current_snapshot_customer_last_message_id: str | None = None,
    zendesk_tool_caller: ZendeskToolCaller | None = None,
    relay_sender: RelaySender | None = None,
) -> ReliablePublicReplyResult:
    """Prepare and deliver one ask/solution/closed casework reply."""
    try:
        prepared = await prepare_reliable_public_reply(
            ticket_id=ticket_id,
            body=body,
            reply_type=reply_type,
            conversation_id=conversation_id,
            messaging_app_id=messaging_app_id,
            snapshot_customer_message_count=snapshot_customer_message_count,
            snapshot_customer_last_message_id=snapshot_customer_last_message_id,
            current_snapshot_customer_message_count=(
                current_snapshot_customer_message_count
            ),
            current_snapshot_customer_last_message_id=(
                current_snapshot_customer_last_message_id
            ),
            delivery_attempt_id=delivery_attempt_id,
            internal_analysis=internal_analysis,
            zendesk_tool_caller=zendesk_tool_caller,
        )
    except ReliablePublicReplyPaused as error:
        return ReliablePublicReplyResult(
            False,
            str(error),
            paused=True,
        )
    except Exception as error:
        if not isinstance(error, ReliablePublicReplyRejected):
            logger.error(
                "reliable_public_reply_prepare_failed",
                ticket_id=ticket_id,
                error=str(error)[:200],
                exc_info=True,
            )
        return ReliablePublicReplyResult(
            False,
            str(error)
            if isinstance(error, ReliablePublicReplyRejected)
            else (f"{type(error).__name__}: {error}"),
            definitely_unsent=isinstance(
                error,
                ReliablePublicReplyDefinitelyUnsent,
            ),
        )
    return await deliver_prepared_public_reply(
        prepared,
        zendesk_tool_caller=zendesk_tool_caller,
        relay_sender=relay_sender,
    )


__all__ = [
    "PreparedPublicReplyDelivery",
    "ReliablePublicReplyResult",
    "ReliablePublicReplyType",
    "deliver_prepared_public_reply",
    "prepare_reliable_public_reply",
    "send_casework_public_reply_with_freshness",
]

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal, Protocol, cast

from zd_agent.casework.adapters.audit import (
    render_casework_audit,
    render_ownership_packet,
)
from zd_agent.casework.domain.enums import (
    DeliveryState,
    OwnershipAction,
    OwnershipState,
    ReplyKind,
)
from zd_agent.casework.domain.models import (
    CaseworkEffectReceipt,
    CaseworkRun,
    EffectAuthorityProjection,
)
from zd_agent.services.casework_effect_store import (
    CaseworkEffectStore,
    PendingCaseworkEffect,
)
from zd_agent.services.casework_ownership_effects import (
    ensure_casework_ownership_effects,
)
from zd_agent.services.reliable_public_reply import (
    ReliablePublicReplyResult,
    send_casework_public_reply_with_freshness,
)


CaseworkPublicReplyType = Literal["ask", "solution", "closed"]


class PublicReplySender(Protocol):
    def __call__(
        self,
        *,
        ticket_id: int,
        body: str,
        reply_type: CaseworkPublicReplyType,
        snapshot_customer_message_count: int | None,
        snapshot_customer_last_message_id: str | None,
        current_snapshot_customer_message_count: int | None,
        current_snapshot_customer_last_message_id: str | None,
        delivery_attempt_id: str,
        internal_analysis: str,
    ) -> Awaitable[ReliablePublicReplyResult]: ...


OwnershipSender = Callable[..., Awaitable[bool]]
_AI_DISCLOSURES = (
    "This reply was sent by Dify AI Agent.",
    "This reply was sent by Dify AI Support.",
    "この返信はDify AI Agentによって送信されました。",
)


class CaseworkEffectStorePort(Protocol):
    def prepare(
        self,
        *,
        ticket_id: int,
        delivery_attempt_id: str,
        generation: int,
        source_event_id: str,
        reply_type: str,
        body: str,
        internal_analysis: str,
        snapshot_customer_message_count: int | None,
        snapshot_customer_last_message_id: str,
        ownership_action: str,
        reason_code: str,
        ownership_packet: str,
        authority_projection_json: str = "",
        delivery_required: bool = True,
    ) -> PendingCaseworkEffect: ...

    def confirm_public_delivery(self, reference: str) -> bool: ...

    def discard_definitely_unsent(self, reference: str) -> bool: ...


_REPLY_TYPE: dict[ReplyKind, CaseworkPublicReplyType] = {
    ReplyKind.ASK: "ask",
    ReplyKind.ANSWER: "solution",
    ReplyKind.PROGRESS: "solution",
    ReplyKind.CLOSE: "closed",
}


def _transport_body(body: str) -> str:
    normalized = body.strip()
    for disclosure in _AI_DISCLOSURES:
        if normalized.endswith(disclosure):
            normalized = normalized[: -len(disclosure)].rstrip()
            break
    return normalized


def _failed_delivery_state(result: ReliablePublicReplyResult) -> DeliveryState:
    if result.paused:
        return DeliveryState.PAUSED
    lowered = result.message.casefold()
    if any(
        value in lowered
        for value in ("freshness", "stale", "pending form", "form is pending")
    ):
        return DeliveryState.DEFERRED
    if any(value in lowered for value in ("ambiguous", "unknown delivery")):
        return DeliveryState.AMBIGUOUS
    return DeliveryState.FAILED


class ZendeskCaseworkEffects:
    def __init__(
        self,
        *,
        store: CaseworkEffectStorePort,
        snapshot_customer_message_count: int | None,
        snapshot_customer_last_message_id: str,
        public_reply_sender: PublicReplySender = (
            send_casework_public_reply_with_freshness
        ),
        ownership_sender: OwnershipSender = ensure_casework_ownership_effects,
        public_reply_enabled: bool = True,
    ) -> None:
        self._store = store
        self._snapshot_count = snapshot_customer_message_count
        self._snapshot_last_id = snapshot_customer_last_message_id
        self._public_reply_sender = public_reply_sender
        self._ownership_sender = ownership_sender
        self._public_reply_enabled = public_reply_enabled

    @staticmethod
    def _dry_run_receipt(run: CaseworkRun) -> CaseworkEffectReceipt:
        ownership_state = (
            OwnershipState.NOT_REQUIRED
            if run.decision.ownership_action is OwnershipAction.NONE
            else OwnershipState.PENDING
        )
        return CaseworkEffectReceipt(
            delivery_state=DeliveryState.DRAFTED,
            ownership_state=ownership_state,
        )

    def _prepare(
        self,
        *,
        run: CaseworkRun,
        delivery_attempt_id: str,
        delivery_required: bool = True,
    ) -> PendingCaseworkEffect:
        draft = run.reply_draft
        if draft is None:
            raise ValueError("customer-visible v2 decision requires a reply draft")
        if draft.reply_kind is ReplyKind.NONE:
            raise ValueError("no-reply draft cannot enter the effect adapter")
        action = run.assessment_build.assessment.human_action
        if run.decision.ownership_action is not OwnershipAction.NONE and action is None:
            raise ValueError("ownership decision requires an admitted action packet")
        authority = EffectAuthorityProjection.from_run(
            run,
            snapshot_customer_message_count=self._snapshot_count,
            snapshot_customer_last_message_id=self._snapshot_last_id,
        )
        return self._store.prepare(
            ticket_id=run.context.ticket.ticket_id,
            delivery_attempt_id=delivery_attempt_id,
            generation=run.context.ticket.generation,
            source_event_id=run.context.ticket.latest_event_id,
            reply_type=_REPLY_TYPE[draft.reply_kind],
            body=_transport_body(draft.body),
            internal_analysis=render_casework_audit(run),
            snapshot_customer_message_count=self._snapshot_count,
            snapshot_customer_last_message_id=self._snapshot_last_id,
            ownership_action=run.decision.ownership_action.value,
            reason_code=run.decision.reason_code.value,
            ownership_packet=render_ownership_packet(action),
            authority_projection_json=authority.model_dump_json(),
            delivery_required=delivery_required,
        )

    @staticmethod
    def _authority_projection(
        effect: PendingCaseworkEffect,
        run: CaseworkRun,
    ) -> EffectAuthorityProjection:
        if effect.authority_projection_json:
            projection = EffectAuthorityProjection.model_validate_json(
                effect.authority_projection_json,
            )
            if (
                projection.generation != effect.generation
                or projection.source_event_id != effect.source_event_id
                or projection.snapshot_customer_message_count
                != effect.snapshot_customer_message_count
                or projection.snapshot_customer_last_message_id
                != effect.snapshot_customer_last_message_id
                or projection.ownership_action.value != effect.ownership_action
                or projection.decision.reason_code.value != effect.reason_code
                or _REPLY_TYPE.get(projection.reply.reply_kind) != effect.reply_type
                or _transport_body(projection.reply.body) != effect.body
            ):
                raise RuntimeError(
                    "frozen casework authority projection does not match its effect"
                )
            return projection
        draft = run.reply_draft
        if (
            draft is None
            or run.context.ticket.generation != effect.generation
            or run.context.ticket.latest_event_id != effect.source_event_id
            or _REPLY_TYPE.get(draft.reply_kind) != effect.reply_type
            or _transport_body(draft.body) != effect.body
            or run.decision.ownership_action.value != effect.ownership_action
            or run.decision.reason_code.value != effect.reason_code
        ):
            raise RuntimeError(
                "frozen casework effect lacks a valid authoritative run projection"
            )
        return EffectAuthorityProjection.from_run(
            run,
            snapshot_customer_message_count=effect.snapshot_customer_message_count,
            snapshot_customer_last_message_id=effect.snapshot_customer_last_message_id,
        )

    async def _deliver(
        self,
        effect: PendingCaseworkEffect,
    ) -> ReliablePublicReplyResult:
        if effect.reply_type not in {"ask", "solution", "closed"}:
            return ReliablePublicReplyResult(
                False,
                f"invalid casework reply_type: {effect.reply_type!r}",
            )
        return await self._public_reply_sender(
            ticket_id=effect.ticket_id,
            body=effect.body,
            reply_type=cast(CaseworkPublicReplyType, effect.reply_type),
            snapshot_customer_message_count=(effect.snapshot_customer_message_count),
            snapshot_customer_last_message_id=(
                effect.snapshot_customer_last_message_id
            ),
            current_snapshot_customer_message_count=self._snapshot_count,
            current_snapshot_customer_last_message_id=self._snapshot_last_id,
            delivery_attempt_id=effect.delivery_attempt_id,
            internal_analysis=effect.internal_analysis,
        )

    async def apply(
        self,
        *,
        run: CaseworkRun,
        delivery_attempt_id: str,
        dry_run: bool,
    ) -> CaseworkEffectReceipt:
        if dry_run:
            return self._dry_run_receipt(run)
        if not self._public_reply_enabled:
            if run.decision.ownership_action is OwnershipAction.NONE:
                return CaseworkEffectReceipt(
                    delivery_state=DeliveryState.PAUSED,
                    ownership_state=OwnershipState.NOT_REQUIRED,
                    detail="Public reply is currently disabled (kill switch).",
                )
            effect = self._prepare(
                run=run,
                delivery_attempt_id=delivery_attempt_id,
                delivery_required=False,
            )
            authority_projection = self._authority_projection(effect, run)
            completed = await self._ownership_sender(
                effect=effect,
                store=self._store,
            )
            return CaseworkEffectReceipt(
                delivery_state=DeliveryState.PAUSED,
                ownership_state=(
                    OwnershipState.CONFIRMED if completed else OwnershipState.PENDING
                ),
                ownership_reference=effect.reference,
                detail="Public reply is currently disabled (kill switch).",
                authoritative_audit=effect.internal_analysis,
                authority_projection=authority_projection,
            )
        effect = self._prepare(run=run, delivery_attempt_id=delivery_attempt_id)
        authority_projection = self._authority_projection(effect, run)
        result = await self._deliver(effect)
        ownership_state = (
            OwnershipState.NOT_REQUIRED
            if not effect.ownership_required
            else OwnershipState.PENDING
        )
        if result.historical_delivery_confirmed:
            if not self._store.confirm_public_delivery(effect.reference):
                raise RuntimeError(
                    "historical v2 public delivery could not be confirmed locally"
                )
            return CaseworkEffectReceipt(
                delivery_state=DeliveryState.DEFERRED,
                ownership_state=ownership_state,
                delivery_reference=effect.reference,
                ownership_reference=(
                    effect.reference
                    if ownership_state is not OwnershipState.NOT_REQUIRED
                    else ""
                ),
                accepted_snapshot_count=result.accepted_snapshot_count,
                accepted_snapshot_last_id=(result.accepted_snapshot_last_id or ""),
                detail=result.message,
                authoritative_audit=effect.internal_analysis,
                authority_projection=authority_projection,
            )
        if not result.success:
            if result.definitely_unsent and self._store.discard_definitely_unsent(
                effect.reference,
            ):
                return CaseworkEffectReceipt(
                    delivery_state=_failed_delivery_state(result),
                    ownership_state=ownership_state,
                    detail=result.message,
                )
            return CaseworkEffectReceipt(
                delivery_state=_failed_delivery_state(result),
                ownership_state=ownership_state,
                delivery_reference=effect.reference,
                ownership_reference=(
                    effect.reference
                    if ownership_state is not OwnershipState.NOT_REQUIRED
                    else ""
                ),
                detail=result.message,
                authoritative_audit=effect.internal_analysis,
                authority_projection=authority_projection,
            )
        if not self._store.confirm_public_delivery(effect.reference):
            raise RuntimeError("v2 public delivery could not be confirmed locally")
        if effect.ownership_required:
            completed = await self._ownership_sender(
                effect=effect,
                store=self._store,
            )
            ownership_state = (
                OwnershipState.CONFIRMED if completed else OwnershipState.PENDING
            )
        return CaseworkEffectReceipt(
            delivery_state=DeliveryState.CONFIRMED,
            ownership_state=ownership_state,
            delivery_reference=effect.reference,
            ownership_reference=(
                effect.reference
                if ownership_state is not OwnershipState.NOT_REQUIRED
                else ""
            ),
            accepted_snapshot_count=result.accepted_snapshot_count,
            accepted_snapshot_last_id=result.accepted_snapshot_last_id or "",
            detail=result.message,
            authoritative_audit=effect.internal_analysis,
            authority_projection=authority_projection,
        )


def configured_casework_effects(
    *,
    state_dir: Path,
    snapshot_customer_message_count: int | None,
    snapshot_customer_last_message_id: str,
    public_reply_enabled: bool,
) -> ZendeskCaseworkEffects:
    return ZendeskCaseworkEffects(
        store=CaseworkEffectStore(state_dir / "casework_effects.sqlite3"),
        snapshot_customer_message_count=snapshot_customer_message_count,
        snapshot_customer_last_message_id=snapshot_customer_last_message_id,
        public_reply_enabled=public_reply_enabled,
    )

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

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
from zd_agent.casework.domain.models import CaseworkEffectReceipt, CaseworkRun
from zd_agent.services.casework_effect_store import (
    CaseworkEffectStore,
    PendingCaseworkEffect,
)
from zd_agent.services.casework_ownership_effects import (
    ensure_casework_ownership_effects,
)
from zd_agent.tools.zendesk_public_reply import (
    DirectPublicReplyResult,
    send_public_reply_with_freshness,
)


PublicReplySender = Callable[..., Awaitable[DirectPublicReplyResult]]
OwnershipSender = Callable[..., Awaitable[bool]]
_AI_DISCLOSURE = "This reply was sent by Dify AI Support."


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
        delivery_required: bool = True,
    ) -> PendingCaseworkEffect: ...

    def confirm_public_delivery(self, reference: str) -> bool: ...


_REPLY_TYPE = {
    ReplyKind.ASK: "ask",
    ReplyKind.ANSWER: "solution",
    ReplyKind.PROGRESS: "solution",
    ReplyKind.CLOSE: "closed",
}


def _transport_body(body: str) -> str:
    normalized = body.strip()
    if normalized.endswith(_AI_DISCLOSURE):
        normalized = normalized[: -len(_AI_DISCLOSURE)].rstrip()
    return normalized


def _failed_delivery_state(message: str) -> DeliveryState:
    lowered = message.casefold()
    if any(value in lowered for value in ("freshness", "stale", "pending form")):
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
        public_reply_sender: PublicReplySender = send_public_reply_with_freshness,
        ownership_sender: OwnershipSender = ensure_casework_ownership_effects,
    ) -> None:
        self._store = store
        self._snapshot_count = snapshot_customer_message_count
        self._snapshot_last_id = snapshot_customer_last_message_id
        self._public_reply_sender = public_reply_sender
        self._ownership_sender = ownership_sender

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
    ) -> PendingCaseworkEffect:
        draft = run.reply_draft
        if draft is None:
            raise ValueError("customer-visible v2 decision requires a reply draft")
        if draft.reply_kind is ReplyKind.NONE:
            raise ValueError("no-reply draft cannot enter the effect adapter")
        action = run.assessment_build.assessment.human_action
        if run.decision.ownership_action is not OwnershipAction.NONE and action is None:
            raise ValueError("ownership decision requires an admitted action packet")
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
        )

    async def _deliver(
        self,
        effect: PendingCaseworkEffect,
    ) -> DirectPublicReplyResult:
        return await self._public_reply_sender(
            ticket_id=effect.ticket_id,
            body=effect.body,
            reply_type=effect.reply_type,
            snapshot_customer_message_count=(
                effect.snapshot_customer_message_count
            ),
            snapshot_customer_last_message_id=(
                effect.snapshot_customer_last_message_id
            ),
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
        effect = self._prepare(run=run, delivery_attempt_id=delivery_attempt_id)
        result = await self._deliver(effect)
        ownership_state = (
            OwnershipState.NOT_REQUIRED
            if run.decision.ownership_action is OwnershipAction.NONE
            else OwnershipState.PENDING
        )
        if not result.success:
            return CaseworkEffectReceipt(
                delivery_state=_failed_delivery_state(result.message),
                ownership_state=ownership_state,
                delivery_reference=effect.reference,
                ownership_reference=(
                    effect.reference
                    if ownership_state is not OwnershipState.NOT_REQUIRED
                    else ""
                ),
                detail=result.message,
            )
        if not self._store.confirm_public_delivery(effect.reference):
            raise RuntimeError("v2 public delivery could not be confirmed locally")
        if run.decision.ownership_action is not OwnershipAction.NONE:
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
        )


def configured_casework_effects(
    *,
    state_dir: Path,
    snapshot_customer_message_count: int | None,
    snapshot_customer_last_message_id: str,
) -> ZendeskCaseworkEffects:
    return ZendeskCaseworkEffects(
        store=CaseworkEffectStore(state_dir / "casework_effects.sqlite3"),
        snapshot_customer_message_count=snapshot_customer_message_count,
        snapshot_customer_last_message_id=snapshot_customer_last_message_id,
    )

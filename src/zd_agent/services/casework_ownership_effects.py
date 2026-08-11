"""Crash-safe typed ownership effects for delivered v2 replies."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress

from zd_agent.log import get_logger
from zd_agent.services.casework_effect_store import (
    CaseworkEffectStore,
    PendingCaseworkEffect,
)
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


logger = get_logger(module="services.casework_ownership_effects")
EffectSender = Callable[..., Awaitable[bool]]


async def _renew_claim(
    store: CaseworkEffectStore,
    reference: str,
    token: str,
) -> None:
    interval = max(0.1, min(30.0, store.claim_lease_seconds / 3))
    while True:
        await asyncio.sleep(interval)
        if not store.renew(reference, token):
            return


async def ensure_casework_ownership_effects(
    *,
    effect: PendingCaseworkEffect,
    store: CaseworkEffectStore,
    notification_sender: EffectSender | None = None,
    tag_sender: EffectSender | None = None,
) -> bool:
    current = store.get(effect.reference)
    if current is None:
        raise RuntimeError("casework ownership command is missing")
    if not current.ownership_required:
        return True
    if not current.ownership_ready:
        return False
    if current.ownership_complete:
        return True
    token = store.claim_ownership(current.reference)
    if token is None:
        return False
    if notification_sender is None or tag_sender is None:
        from zd_agent.services import casework_notifications

        notification_sender = (
            notification_sender
            or casework_notifications.send_casework_ownership_notification_best_effort
        )
        tag_sender = (
            tag_sender or casework_notifications.add_casework_ownership_tags_best_effort
        )
    heartbeat = asyncio.create_task(_renew_claim(store, current.reference, token))
    notification_sent = current.notification_sent
    tag_sent = current.zendesk_tag_sent
    try:
        if not notification_sent and current.notification_ambiguous:
            logger.warning(
                "casework_ownership_notification_ambiguous",
                ticket_id=current.ticket_id,
                reference=current.reference,
            )
        elif not notification_sent:
            definitely_unsent = False
            try:
                if not store.begin_notification(current.reference, token):
                    return False
                notification_sent = await notification_sender(
                    ticket_id=current.ticket_id,
                    ownership_action=current.ownership_action,
                    reason_code=current.reason_code,
                    ownership_packet=current.ownership_packet,
                    customer_reply=current.body,
                    source=(
                        f"casework_v2:{current.ownership_action}:{current.reason_code}"
                    ),
                    idempotency_reference=current.reference,
                )
                definitely_unsent = notification_sent is False
            except Exception as error:  # noqa: BLE001 - durable row retries.
                logger.warning(
                    "casework_ownership_notification_failed",
                    ticket_id=current.ticket_id,
                    error=str(error)[:200],
                )
            if notification_sent:
                notification_sent = store.mark_ownership_effect(
                    current.reference,
                    token,
                    "notification_sent",
                )
            elif definitely_unsent:
                store.clear_notification_ambiguous(current.reference, token)
        if not tag_sent:
            try:
                tag_sent = await tag_sender(
                    ticket_id=current.ticket_id,
                    ownership_action=current.ownership_action,
                    reason_code=current.reason_code,
                )
            except Exception as error:  # noqa: BLE001 - durable row retries.
                logger.warning(
                    "casework_ownership_tag_failed",
                    ticket_id=current.ticket_id,
                    error=str(error)[:200],
                )
            if tag_sent:
                tag_sent = store.mark_ownership_effect(
                    current.reference,
                    token,
                    "zendesk_tag_sent",
                )
        return bool(notification_sent and tag_sent)
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat
        store.release(current.reference, token)


async def reconcile_casework_ownership_effects(
    *,
    store: CaseworkEffectStore,
    notification_sender: EffectSender | None = None,
    tag_sender: EffectSender | None = None,
    state: TicketStateTracker | None = None,
    limit: int = 100,
) -> int:
    reconciled_references: set[str] = set()
    for effect in store.pending_ownership(limit=limit):
        if await ensure_casework_ownership_effects(
            effect=effect,
            store=store,
            notification_sender=notification_sender,
            tag_sender=tag_sender,
        ):
            reconciled_references.add(effect.reference)
    if state is not None:
        reconciled_references.update(
            _promote_completed_ownership(
                store=store,
                state=state,
                limit=limit,
            )
        )
    return len(reconciled_references)


def _promote_completed_ownership(
    *,
    store: CaseworkEffectStore,
    state: TicketStateTracker,
    limit: int,
) -> set[str]:
    """CAS any exact completed typed owner command from pending to owned."""
    promoted: set[str] = set()
    for ticket_key in state.list_by_state("ownership_pending")[: max(1, limit)]:
        pending = state.get(ticket_key)
        if pending is None or pending.state != "ownership_pending":
            continue
        reference = pending.ownership_effect_reference
        owner_token = pending.active_session_id
        if not reference or not owner_token:
            continue
        try:
            ticket_id = int(ticket_key)
        except ValueError:
            continue
        effect = store.get(reference)
        if (
            effect is None
            or effect.ticket_id != ticket_id
            or not effect.ownership_required
            or not effect.complete
            or pending.ownership_action != effect.ownership_action
            or pending.ownership_reason != effect.reason_code
        ):
            continue
        effect_action = effect.ownership_action
        effect_reason = effect.reason_code

        def _mark_owned(current: TicketState | None) -> TicketState:
            if current is None:
                raise RuntimeError("runtime ownership state disappeared")
            if (
                current.ownership_effect_reference != reference
                or current.ownership_action != effect_action
                or current.ownership_reason != effect_reason
            ):
                raise RuntimeError("runtime ownership authority changed")
            current.state = "owned"
            current.active_session_id = ""
            current.silence_window_end = None
            current.last_verdict = effect_reason
            return current

        commit = state.update_if_revision(
            ticket_id,
            expected_revision=pending.state_revision,
            expected_state="ownership_pending",
            expected_active_session_id=owner_token,
            mutate=_mark_owned,
        )
        if commit is not None:
            promoted.add(reference)
            logger.info(
                "casework_ownership_state_reconciled",
                ticket_id=ticket_id,
                reference=reference,
            )
    return promoted

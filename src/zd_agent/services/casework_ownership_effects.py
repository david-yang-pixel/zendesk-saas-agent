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
            tag_sender
            or casework_notifications.add_casework_ownership_tags_best_effort
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
                        f"casework_v2:{current.ownership_action}:"
                        f"{current.reason_code}"
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
    limit: int = 100,
) -> int:
    reconciled = 0
    for effect in store.pending_ownership(limit=limit):
        if await ensure_casework_ownership_effects(
            effect=effect,
            store=store,
            notification_sender=notification_sender,
            tag_sender=tag_sender,
        ):
            reconciled += 1
    return reconciled

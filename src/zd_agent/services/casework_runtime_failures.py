"""Durable ownership for v2 runtime failures after bounded automatic retries."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from zd_agent.casework.domain.enums import (
    OwnershipAction,
    OwnershipState,
    ReasonCode,
)
from zd_agent.services.casework_effect_store import CaseworkEffectStore
from zd_agent.services.casework_ownership_effects import (
    ensure_casework_ownership_effects,
)


EffectSender = Callable[..., Awaitable[bool]]
_MAX_AUTOMATION_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class RuntimeFailureOwnershipReceipt:
    reference: str
    ownership_action: OwnershipAction
    reason_code: ReasonCode
    ownership_state: OwnershipState


def _safe_failure_type(value: str) -> str:
    normalized = "".join(
        character
        for character in value.strip()
        if character.isalnum() or character in {"_", "-"}
    )
    return normalized[:80] or "RuntimeError"


def _ownership_packet(*, attempt: int, failure_type: str) -> str:
    return "\n".join(
        (
            "Target: support",
            f"Reason: {ReasonCode.AUTOMATION_RETRY_EXHAUSTED.value}",
            (
                "Summary: The v2 runtime did not complete the ticket after "
                f"{attempt} automatic attempts ({failure_type})."
            ),
            "Customer impact: The latest question has not received an answer.",
            "Completed checks: The bounded automatic retry budget was exhausted.",
            (
                "Required action: Review the v2 audit, complete the unresolved "
                "investigation, and answer the latest customer question."
            ),
            (
                "Acceptance: Verify all material facts from authoritative sources "
                "before replying."
            ),
        ),
    )


async def persist_runtime_failure_ownership(
    *,
    state_dir: Path,
    ticket_id: int,
    delivery_attempt_id: str,
    generation: int,
    source_event_id: str,
    snapshot_customer_message_count: int | None,
    snapshot_customer_last_message_id: str,
    automation_attempt: int,
    failure_type: str,
    notification_sender: EffectSender | None = None,
    tag_sender: EffectSender | None = None,
) -> RuntimeFailureOwnershipReceipt:
    """Persist and execute one typed owner command after the retry budget."""
    if automation_attempt < _MAX_AUTOMATION_ATTEMPTS:
        raise ValueError("runtime ownership requires an exhausted retry budget")
    safe_failure_type = _safe_failure_type(failure_type)
    fallback_cursor = f"runtime-failure:{delivery_attempt_id}"
    source_event_id = source_event_id.strip() or fallback_cursor
    snapshot_customer_last_message_id = (
        snapshot_customer_last_message_id.strip() or fallback_cursor
    )
    store = CaseworkEffectStore(state_dir / "casework_effects.sqlite3")
    effect = store.prepare(
        ticket_id=ticket_id,
        delivery_attempt_id=f"{delivery_attempt_id}:runtime-failure",
        generation=generation,
        source_event_id=source_event_id,
        reply_type="none",
        body="",
        internal_analysis=(
            "v2 runtime retry exhausted; no automatic customer reply was sent; "
            f"failure_type={safe_failure_type}"
        ),
        snapshot_customer_message_count=snapshot_customer_message_count,
        snapshot_customer_last_message_id=snapshot_customer_last_message_id,
        ownership_action=OwnershipAction.SUPPORT.value,
        reason_code=ReasonCode.AUTOMATION_RETRY_EXHAUSTED.value,
        ownership_packet=_ownership_packet(
            attempt=automation_attempt,
            failure_type=safe_failure_type,
        ),
        delivery_required=False,
    )
    completed = await ensure_casework_ownership_effects(
        effect=effect,
        store=store,
        notification_sender=notification_sender,
        tag_sender=tag_sender,
    )
    return RuntimeFailureOwnershipReceipt(
        reference=effect.reference,
        ownership_action=OwnershipAction.SUPPORT,
        reason_code=ReasonCode.AUTOMATION_RETRY_EXHAUSTED,
        ownership_state=(
            OwnershipState.CONFIRMED if completed else OwnershipState.PENDING
        ),
    )

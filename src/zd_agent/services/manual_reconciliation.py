"""Audited operator recovery for ambiguous historical public deliveries."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from zd_agent.services.public_reply_delivery_store import PublicReplyDeliveryStore
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


def reconcile_manual_delivery(
    *,
    state_dir: Path,
    ticket_id: int,
    delivery_attempt_id: str,
    outcome: str,
    reason: str,
    actor: str = "cli",
    now: datetime | None = None,
) -> TicketState:
    """Resolve one exact manual fence only after an operator verifies Zendesk."""
    reason = reason.strip()
    if not reason:
        raise ValueError("manual reconciliation reason is required")
    if not delivery_attempt_id.strip():
        raise ValueError("delivery attempt id is required")
    tracker = TicketStateTracker(state_dir / "tickets.json")
    current = tracker.get(ticket_id)
    if current is None or current.state != "manual_reconciliation":
        raise ValueError("ticket is not awaiting manual reconciliation")
    if current.delivery_attempt_id != delivery_attempt_id:
        raise ValueError("delivery attempt id does not match the quarantined ticket")

    resolved_at = now or datetime.now(timezone.utc)
    outbox = PublicReplyDeliveryStore(state_dir / "public_reply_outbox.sqlite3")
    existing_delivery = outbox.inspect_logical_attempt(
        delivery_attempt_id=delivery_attempt_id,
        ticket_id=ticket_id,
    )
    if outcome == "delivered" and existing_delivery.reply_type != "ask":
        raise ValueError("only a historical ask can be confirmed as delivered")
    delivery = outbox.resolve_manually(
        delivery_attempt_id=delivery_attempt_id,
        ticket_id=ticket_id,
        outcome=outcome,
    )

    def _apply(record: TicketState | None) -> TicketState:
        if record is None:
            raise RuntimeError("manual reconciliation ticket disappeared")
        record.active_session_id = ""
        record.ownership_action = ""
        record.ownership_reason = ""
        record.ownership_effect_reference = ""
        if outcome == "delivered":
            if delivery.reply_type != "ask":
                raise ValueError(
                    "only a confirmed historical ask can resume customer wait",
                )
            record.state = "awaiting_customer"
            record.last_verdict = "manual_delivery_confirmed"
            record.last_agent_finish_ts = resolved_at
            record.silence_window_end = None
        else:
            record.state = "in_silence_window"
            record.last_verdict = "casework_ready"
            record.last_agent_finish_ts = None
            record.silence_window_end = resolved_at
            record.delivery_attempt_id = (
                f"manual-reconcile:{ticket_id}:{record.state_revision + 1}"
            )
        return record

    committed = tracker.update_if_revision(
        ticket_id,
        expected_revision=current.state_revision,
        expected_state="manual_reconciliation",
        expected_active_session_id=current.active_session_id,
        mutate=_apply,
    )
    if committed is None:
        raise RuntimeError("manual reconciliation lost the ticket generation")

    audit_path = state_dir / "manual_reconciliation.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with audit_path.open("a", encoding="utf-8") as audit_file:
        audit_file.write(
            json.dumps(
                {
                    "ts": resolved_at.isoformat(),
                    "actor": actor,
                    "ticket_id": ticket_id,
                    "delivery_attempt_id": delivery_attempt_id,
                    "outcome": outcome,
                    "reason": reason,
                    "state_revision": committed.state_revision,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
        )
        audit_file.flush()
        os.fsync(audit_file.fileno())
    audit_path.chmod(0o600)
    return committed.after

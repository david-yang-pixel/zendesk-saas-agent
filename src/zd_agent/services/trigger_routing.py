"""Pure routing helpers for the trigger consumer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from zd_agent.trigger.state_store import TicketState


@dataclass(frozen=True)
class CooldownDecision:
    should_skip: bool
    elapsed_seconds: float | None = None
    cooldown_seconds: int = 0


@dataclass(frozen=True)
class CustomerReopenMetric:
    prev_state: str
    source: str
    hours_since_processed: float | None


def _parse_created_at(value: str | None, *, now: datetime) -> datetime:
    if not value:
        return now
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return now


def latest_customer_message_context(
    customer_msgs: list[dict],
    *,
    now: datetime,
) -> tuple[dict, object, datetime]:
    """Return latest message, id, and parsed timestamp using ``now`` fallback."""
    latest = customer_msgs[-1] if customer_msgs else {}
    latest_cid = latest.get("id", "")
    latest_ts = _parse_created_at(latest.get("created_at"), now=now)
    return latest, latest_cid, latest_ts


def direct_trigger_cooldown(
    *,
    existing: TicketState | None,
    source: str,
    hint: str,
    full_cooldown_seconds: int,
    now: datetime,
) -> CooldownDecision:
    """Cooldown for direct dispatch sources such as mention and silence_waker."""
    if existing is None or existing.last_agent_finish_ts is None:
        return CooldownDecision(should_skip=False)

    cooldown_seconds = 30 if (source == "mention" and hint) else full_cooldown_seconds
    elapsed_seconds = (now - _aware(existing.last_agent_finish_ts)).total_seconds()
    return CooldownDecision(
        should_skip=elapsed_seconds < cooldown_seconds,
        elapsed_seconds=elapsed_seconds,
        cooldown_seconds=cooldown_seconds,
    )


def post_agent_cooldown(
    *,
    existing: TicketState | None,
    cooldown_seconds: int,
    now: datetime,
) -> CooldownDecision:
    """Cooldown for customer messages that arrive right after agent completion."""
    if existing is None or existing.last_agent_finish_ts is None:
        return CooldownDecision(should_skip=False)

    elapsed_seconds = (now - _aware(existing.last_agent_finish_ts)).total_seconds()
    return CooldownDecision(
        should_skip=elapsed_seconds < cooldown_seconds,
        elapsed_seconds=elapsed_seconds,
        cooldown_seconds=cooldown_seconds,
    )


def is_duplicate_customer_comment(
    existing: TicketState | None,
    latest_cid: object,
) -> bool:
    """Return whether the newest customer comment was already evaluated."""
    return bool(existing and existing.last_customer_comment_id == latest_cid)


def customer_reopen_metric(
    *,
    existing: TicketState | None,
    source: str,
    now: datetime,
) -> CustomerReopenMetric | None:
    """Return metric context when a processed ticket receives a new comment."""
    if existing is None or existing.state != "processed":
        return None

    hours_since = None
    if existing.last_eval_at is not None:
        hours_since = (now - _aware(existing.last_eval_at)).total_seconds() / 3600
    return CustomerReopenMetric(
        prev_state=existing.state,
        source=source,
        hours_since_processed=hours_since,
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value

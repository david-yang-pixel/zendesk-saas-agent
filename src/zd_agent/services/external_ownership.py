"""Infrastructure-only ownership gates outside the v2 decision core."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zd_agent.services.trigger_state_machine import apply_human_takeover
from zd_agent.trigger.decision_log import log_decision


async def record_human_takeover(
    *,
    ticket_id: int,
    existing: Any,
    state: Any,
    log_path: Path,
    source: str,
    now: Any,
    latest_cid: Any,
    latest_ts: Any,
    detection_reason: str,
) -> None:
    """Stop automation after a real human reply without external side effects."""
    apply_human_takeover(
        state,
        ticket_id,
        existing=existing,
        latest_cid=latest_cid,
        latest_ts=latest_ts,
        now=now,
        reason="human_takeover_detected",
    )
    log_decision(
        log_path=log_path,
        ticket_id=ticket_id,
        verdict="human_takeover",
        action="externally_owned",
        extra={
            "source": source,
            "reason": "human_takeover_detected",
            "takeover_reason": detection_reason,
        },
    )

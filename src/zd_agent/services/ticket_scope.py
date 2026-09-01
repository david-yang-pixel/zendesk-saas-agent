"""Authoritative eligibility rules for automatic SaaS casework intake."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_ACTIVE_STATUSES = {"new", "open"}
_SAAS_TAG = "saas"


@dataclass(frozen=True)
class TicketScopeResult:
    eligible: bool
    reason: str = "eligible"


def evaluate_saas_ticket_scope(
    ticket: dict[str, Any],
    *,
    saas_group_id: int,
) -> TicketScopeResult:
    """Require SaaS tag, SaaS group, and an active Zendesk status.

    ``saas_group_id=0`` preserves compatibility for installations that have not
    configured a target group yet. Their existing search query remains the
    intake boundary until an explicit group ID is saved.
    """
    status = str(ticket.get("status") or "").strip().casefold()
    if status and status not in _ACTIVE_STATUSES:
        return TicketScopeResult(False, "status_out_of_scope")

    if not saas_group_id:
        return TicketScopeResult(True)

    tags = {
        str(tag).strip().casefold()
        for tag in (ticket.get("tags") or [])
        if str(tag).strip()
    }
    if _SAAS_TAG not in tags:
        return TicketScopeResult(False, "saas_tag_missing")

    try:
        group_id = int(ticket.get("group_id") or 0)
    except (TypeError, ValueError):
        group_id = 0
    if group_id != saas_group_id:
        return TicketScopeResult(False, "saas_group_mismatch")

    return TicketScopeResult(True)

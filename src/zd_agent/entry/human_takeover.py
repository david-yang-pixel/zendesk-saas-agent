"""Shared helpers for deciding whether Zendesk already has ticket ownership."""
from __future__ import annotations


def _to_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def is_human_assignee(
    assignee_id: object,
    *,
    agent_user_id: int,
) -> bool:
    """Return True when the ticket has any non-empty assignee.

    Business rule: human/owner takeover is represented by Zendesk having an
    assignee on the ticket. The specific assignee identity does not matter.
    """
    assignee = _to_int(assignee_id)
    return bool(assignee)

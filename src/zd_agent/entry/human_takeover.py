"""Shared helpers for deciding whether Zendesk already has ticket ownership."""
from __future__ import annotations

from collections.abc import Iterable


def _to_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def is_human_assignee(
    assignee_id: object,
    *,
    agent_user_id: int,
    non_human_assignee_ids: Iterable[int] = (),
) -> bool:
    """Return True when the ticket has a real human assignee.

    Queue/group assignment such as "SaaS" is represented by group_id, not
    assignee_id, and must not count. Zendesk Messaging may also attach
    automation identities such as "AI Agent"; those are excluded by config.
    """
    assignee = _to_int(assignee_id)
    if not assignee:
        return False

    non_human_ids = {_to_int(v) for v in non_human_assignee_ids}
    return assignee not in {v for v in non_human_ids if v}

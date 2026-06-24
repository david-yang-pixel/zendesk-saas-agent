"""Follow-up helpers.

Zendesk's latest conversation is the source of truth for whether a ticket has a
new follow-up. Local ticket records are historical references only; their
existence must not force follow-up mode by default.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


def load_previous_record(
    *,
    resources_base_dir: str,
    ticket_records_dir: str,
    ticket_id: int | str,
) -> Optional[str]:
    """Read existing ticket record markdown if present. None if not found."""
    path = Path(resources_base_dir) / ticket_records_dir / f"{ticket_id}.md"
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def detect_followup_mode(
    *,
    resources_base_dir: str,
    ticket_records_dir: str,
    ticket_id: int | str,
    trust_local_record: bool = False,
) -> tuple[str, str]:
    """Return (mode, previous_record).

    By default this returns first_pass even if a local record exists. Callers
    should only pass trust_local_record=True after they have already verified
    from live Zendesk context that the customer added new follow-up content.
    """
    if not trust_local_record:
        return ("first_pass", "")

    record = load_previous_record(
        resources_base_dir=resources_base_dir,
        ticket_records_dir=ticket_records_dir,
        ticket_id=ticket_id,
    )
    if record is None:
        return ("first_pass", "")
    return ("followup", record)

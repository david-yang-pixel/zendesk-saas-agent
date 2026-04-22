"""Followup mode detection: checks 工单记录/<id>.md and diffs customer comments."""
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
) -> tuple[str, str]:
    """Return (mode, previous_record).

    mode == "followup" when a prior record exists, else "first_pass".
    """
    record = load_previous_record(
        resources_base_dir=resources_base_dir,
        ticket_records_dir=ticket_records_dir,
        ticket_id=ticket_id,
    )
    if record is None:
        return ("first_pass", "")
    return ("followup", record)

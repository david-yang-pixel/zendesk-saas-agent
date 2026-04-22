"""Decision logger: append JSONL records for every trigger decision."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def log_decision(
    *,
    log_path: Path | str,
    ticket_id: str | int,
    verdict: str,
    completeness: int = 0,
    silence_min: int = 0,
    action: str,
    extra: Optional[dict] = None,
) -> None:
    """Append one JSONL decision record."""
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "ticket": str(ticket_id),
        "verdict": verdict,
        "completeness": completeness,
        "silence_min": silence_min,
        "action": action,
    }
    if extra:
        entry.update(extra)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

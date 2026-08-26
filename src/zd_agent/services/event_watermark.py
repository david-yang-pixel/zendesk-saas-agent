"""Online-epoch watermark helpers used by every backfill source."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from zd_agent.services.agent_lifecycle import ACCEPT_AFTER_ENV


def accept_after_unix() -> float:
    try:
        return max(0.0, float(os.environ.get(ACCEPT_AFTER_ENV, "0") or 0))
    except ValueError:
        return 0.0


def timestamp_unix(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        result = float(value)
        return result / 1000 if result > 10_000_000_000 else result
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return timestamp_unix(float(raw))
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def is_after_online_watermark(value: Any, *, watermark: float | None = None) -> bool:
    boundary = accept_after_unix() if watermark is None else max(0.0, watermark)
    if boundary <= 0:
        return True
    observed = timestamp_unix(value)
    return observed is not None and observed > boundary

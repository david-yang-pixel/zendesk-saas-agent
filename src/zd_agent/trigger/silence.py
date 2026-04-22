"""Silence window duration calculation with rapid-burst detection.

Default silence = 3 min.
If N or more customer messages arrived within `burst_window_s` seconds of the
latest message, extend to `extend_min` (default 5 min) — catches clients who
are typing a multi-part problem statement.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any


def compute_silence_duration(
    customer_comments: list[Any],
    *,
    default_min: int,
    extend_min: int,
    burst_window_s: int,
    burst_threshold: int,
) -> timedelta:
    """Return silence duration for the given comment sequence.

    Each comment must have a timezone-aware `.created_at: datetime` attribute.
    """
    if len(customer_comments) < burst_threshold:
        return timedelta(minutes=default_min)

    sorted_c = sorted(customer_comments, key=lambda c: c.created_at)
    latest_ts = sorted_c[-1].created_at
    window = timedelta(seconds=burst_window_s)
    in_burst = sum(1 for c in sorted_c if (latest_ts - c.created_at) <= window)

    if in_burst >= burst_threshold:
        return timedelta(minutes=extend_min)
    return timedelta(minutes=default_min)

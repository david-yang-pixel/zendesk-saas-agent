from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

SleepFn = Callable[[float], Awaitable[None]]
NowFn = Callable[[], float]


def _delay_seconds(value: Any) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, seconds)


def with_trigger_delay(
    message: dict[str, Any],
    *,
    delay_seconds: Any,
    now_fn: NowFn = time.time,
) -> dict[str, Any]:
    seconds = _delay_seconds(delay_seconds)
    if seconds <= 0:
        return dict(message)
    delayed = dict(message)
    delayed["available_at"] = now_fn() + seconds
    return delayed


async def wait_for_trigger_availability(
    message: dict[str, Any],
    *,
    sleep_fn: SleepFn = asyncio.sleep,
    now_fn: NowFn = time.time,
) -> float:
    available_at = message.get("available_at")
    try:
        wait_seconds = float(available_at) - now_fn()
    except (TypeError, ValueError):
        return 0.0
    if wait_seconds <= 0:
        return 0.0
    await sleep_fn(wait_seconds)
    return wait_seconds

"""Cancellation-safe completion for already-started durable cleanup tasks."""

from __future__ import annotations

import asyncio
from typing import TypeVar


T = TypeVar("T")


def clear_current_task_cancellation() -> None:
    """Consume queued cancellation requests while a durable cleanup finishes."""
    current = asyncio.current_task()
    if current is None:
        return
    while current.cancelling():
        current.uncancel()


async def await_durable_cleanup(task: asyncio.Task[T]) -> T:
    """Wait for ``task`` despite repeated cancellation of its parent task.

    Callers must re-raise their original ``CancelledError`` after this returns.
    The cleanup task itself is still allowed to fail or be cancelled.
    """
    clear_current_task_cancellation()
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                return task.result()
            clear_current_task_cancellation()

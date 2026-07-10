from __future__ import annotations

import asyncio  # noqa: ANY001 - reusable clearable event coordinates many consumers.
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from zd_agent.services.durable_trigger_store import DurableTriggerStore


class DurableTriggerQueue:
    schedules_available_at = True

    def __init__(
        self,
        path: Path | str,
        *,
        max_attempts: int = 5,
        retry_base_seconds: float = 5.0,
        lease_seconds: float = 30 * 60,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path)
        self.max_attempts = max(1, max_attempts)
        self.retry_base_seconds = max(0.0, retry_base_seconds)
        self.lease_seconds = max(1.0, lease_seconds)
        self._now = now_fn
        self._wake = asyncio.Event()
        self._store = DurableTriggerStore(
            self.path,
            lease_seconds=self.lease_seconds,
            now_fn=self._now,
        )

    async def put(self, message: dict[str, Any]) -> bool:
        payload = dict(message)
        event_key = str(payload.get("_queue_key") or uuid.uuid4().hex)
        available_at = _float_or(payload.get("available_at"), self._now())
        inserted = await asyncio.to_thread(
            self._store.insert,
            event_key,
            payload,
            available_at,
        )
        if inserted:
            self._wake.set()
        return inserted

    def ensure_processing_recovery(self, ticket_id: int) -> bool:
        event_key = f"startup-recovery:{ticket_id}"
        payload = {
            "source": "startup_recovery",
            "ticket_id": ticket_id,
            "_queue_key": event_key,
        }
        available_at = self._now()
        inserted = self._store.insert(event_key, payload, available_at)
        if not inserted:
            inserted = self._store.revive_dead_letter(
                event_key,
                payload,
                available_at,
            )
        if inserted:
            self._wake.set()
        return inserted

    async def get(self) -> dict[str, Any]:
        while True:
            self._wake.clear()
            message, next_available_at = await asyncio.to_thread(
                self._store.claim_ready
            )
            if message is not None:
                return message

            timeout = None
            if next_available_at is not None:
                timeout = max(0.0, next_available_at - self._now())
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=timeout)
            except TimeoutError:
                continue

    async def ack(self, message: dict[str, Any]) -> None:
        event_id = _event_id(message)
        claim_token = _claim_token(message)
        await asyncio.to_thread(self._store.delete, event_id, claim_token)

    async def release(self, message: dict[str, Any]) -> None:
        event_id = _event_id(message)
        claim_token = _claim_token(message)
        await asyncio.to_thread(
            self._store.reschedule,
            event_id,
            claim_token,
            0.0,
            "consumer_cancelled",
        )
        self._wake.set()

    async def defer(
        self,
        message: dict[str, Any],
        delay_seconds: float,
        reason: str,
    ) -> None:
        event_id = _event_id(message)
        claim_token = _claim_token(message)
        await asyncio.to_thread(
            self._store.reschedule,
            event_id,
            claim_token,
            max(0.0, delay_seconds),
            reason,
        )
        self._wake.set()

    async def retry(self, message: dict[str, Any], error: str) -> bool:
        event_id = _event_id(message)
        claim_token = _claim_token(message)
        attempts = int(message.get("_queue_attempts") or 0) + 1
        pending = attempts < self.max_attempts
        delay = min(300.0, self.retry_base_seconds * (2 ** max(0, attempts - 1)))
        await asyncio.to_thread(
            self._store.mark_failed,
            event_id,
            claim_token,
            attempts,
            pending,
            delay,
            error,
        )
        if pending:
            self._wake.set()
        return pending

    async def pending_count(self) -> int:
        return await asyncio.to_thread(self._store.count, "pending")

    async def list_dead_letters(self, limit: int = 100) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._store.dead_letters,
            max(1, min(limit, 500)),
        )


def _event_id(message: dict[str, Any]) -> int:
    value = message.get("_queue_event_id")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DurableTriggerMessageError(
            "durable trigger message is missing _queue_event_id"
        )
    return value


def _claim_token(message: dict[str, Any]) -> str:
    value = message.get("_queue_claim_token")
    if not isinstance(value, str) or not value:
        raise DurableTriggerMessageError(
            "durable trigger message is missing _queue_claim_token"
        )
    return value


def _float_or(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


class DurableTriggerMessageError(ValueError):
    pass

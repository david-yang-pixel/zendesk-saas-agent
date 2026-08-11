from __future__ import annotations

import asyncio  # noqa: ANY001 - reusable clearable event coordinates many consumers.
from hashlib import sha256
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from zd_agent.services.cancellation import await_durable_cleanup
from zd_agent.services.durable_trigger_store import DurableTriggerStore


class DurableTriggerQueue:
    schedules_available_at = True

    def __init__(
        self,
        path: Path | str,
        *,
        max_attempts: int = 5,
        max_materialization_defers: int = 20,
        retry_base_seconds: float = 5.0,
        lease_seconds: float = 30 * 60,
        poll_interval_seconds: float = 0.5,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path)
        self.max_attempts = max(1, max_attempts)
        self.max_materialization_defers = max(1, max_materialization_defers)
        self.retry_base_seconds = max(0.0, retry_base_seconds)
        self.lease_seconds = max(1.0, lease_seconds)
        self.poll_interval_seconds = max(0.01, poll_interval_seconds)
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

    async def put_recoverable(self, message: dict[str, Any]) -> bool:
        """Insert an event, reviving the same durable generation if dead-lettered.

        This is deliberately separate from normal webhook ``put`` semantics:
        only deterministic state-machine generations (such as a silence-window
        wakeup) are safe to revive after a crash between dead-lettering and its
        compensating state transition.
        """
        payload = dict(message)
        event_key = str(payload.get("_queue_key") or uuid.uuid4().hex)
        payload["_queue_key"] = event_key
        available_at = _float_or(payload.get("available_at"), self._now())
        inserted = await asyncio.to_thread(
            self._store.insert,
            event_key,
            payload,
            available_at,
        )
        if not inserted:
            inserted = await asyncio.to_thread(
                self._store.revive_dead_letter,
                event_key,
                payload,
                available_at,
            )
        if inserted:
            self._wake.set()
        return inserted

    def ensure_processing_recovery(
        self,
        ticket_id: int,
        *,
        delivery_attempt_id: str = "",
    ) -> bool:
        # Queue identity and customer-delivery identity serve different jobs.
        # The pre-crash trigger may still hold a live queue lease under the
        # delivery id, so startup recovery needs its own independent key.
        event_key = _processing_recovery_key(ticket_id, delivery_attempt_id)
        payload = {
            "source": "startup_recovery",
            "ticket_id": ticket_id,
            "_queue_key": event_key,
            "delivery_attempt_id": delivery_attempt_id or event_key,
        }
        available_at = self._now()
        inserted = self._store.insert(event_key, payload, available_at)
        if not inserted:
            inserted = self._store.revive_recovery(
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
            claim_task = asyncio.create_task(
                asyncio.to_thread(self._store.claim_ready),
            )
            try:
                message, next_available_at = await asyncio.shield(claim_task)
            except asyncio.CancelledError:
                # ``to_thread`` cannot be cancelled after SQLite has committed
                # the processing lease. Repeated lifecycle cancellation must
                # not interrupt the claim/release cleanup and strand the row.
                message, _next_available_at = await await_durable_cleanup(claim_task)
                if message is not None:
                    release_task = asyncio.create_task(self.release(message))
                    await await_durable_cleanup(release_task)
                raise
            if message is not None:
                return message

            # The event is process-local.  A bounded poll is also required so a
            # consumer observes inserts committed by another queue instance.
            timeout = self.poll_interval_seconds
            if next_available_at is not None:
                timeout = min(
                    timeout,
                    max(0.0, next_available_at - self._now()),
                )
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=timeout)
            except TimeoutError:
                continue

    async def ack(self, message: dict[str, Any]) -> bool:
        event_id = _event_id(message)
        claim_token = _claim_token(message)
        return await asyncio.to_thread(self._store.delete, event_id, claim_token)

    async def release(self, message: dict[str, Any]) -> bool:
        event_id = _event_id(message)
        claim_token = _claim_token(message)
        released = await asyncio.to_thread(
            self._store.reschedule,
            event_id,
            claim_token,
            0.0,
            "consumer_cancelled",
            payload=message,
        )
        if released:
            self._wake.set()
        return released

    async def defer(
        self,
        message: dict[str, Any],
        delay_seconds: float,
        reason: str,
    ) -> bool | None:
        event_id = _event_id(message)
        claim_token = _claim_token(message)
        is_materialization_wait = reason == "ticket_not_materialized"
        deferrals = int(message.get("_queue_deferrals") or 0)
        if is_materialization_wait and deferrals + 1 >= self.max_materialization_defers:
            attempts = int(message.get("_queue_attempts") or 0) + 1
            failed = await asyncio.to_thread(
                self._store.mark_failed,
                event_id,
                claim_token,
                attempts,
                False,
                0.0,
                f"{reason}: deferral budget exhausted",
                message,
            )
            return False if failed else None
        deferred = await asyncio.to_thread(
            self._store.reschedule,
            event_id,
            claim_token,
            max(0.0, delay_seconds),
            reason,
            increment_deferrals=is_materialization_wait,
            payload=message,
        )
        if deferred:
            self._wake.set()
            return True
        return None

    async def retry(self, message: dict[str, Any], error: str) -> bool | None:
        event_id = _event_id(message)
        claim_token = _claim_token(message)
        attempts = int(message.get("_queue_attempts") or 0) + 1
        pending = attempts < self.max_attempts
        delay = min(300.0, self.retry_base_seconds * (2 ** max(0, attempts - 1)))
        failed = await asyncio.to_thread(
            self._store.mark_failed,
            event_id,
            claim_token,
            attempts,
            pending,
            delay,
            error,
            message,
        )
        if not failed:
            return None
        if pending:
            self._wake.set()
        return pending

    async def pending_count(self) -> int:
        return await asyncio.to_thread(self._store.count, "pending")

    async def dead_letter_count(self) -> int:
        return await asyncio.to_thread(self._store.count, "dead_letter")

    async def list_dead_letters(self, limit: int = 100) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._store.dead_letters,
            max(1, min(limit, 500)),
        )

    async def resolve_dead_letter(self, message: dict[str, Any]) -> bool:
        return await asyncio.to_thread(
            self._store.resolve_dead_letter,
            _event_id(message),
        )


def _event_id(message: dict[str, Any]) -> int:
    value = message.get("_queue_event_id")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DurableTriggerMessageError(
            "durable trigger message is missing _queue_event_id"
        )
    return value


def _processing_recovery_key(ticket_id: int, delivery_attempt_id: str) -> str:
    if not delivery_attempt_id:
        return f"startup-recovery:{ticket_id}"
    generation = sha256(delivery_attempt_id.encode("utf-8")).hexdigest()[:16]
    return f"startup-recovery:{ticket_id}:{generation}"


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

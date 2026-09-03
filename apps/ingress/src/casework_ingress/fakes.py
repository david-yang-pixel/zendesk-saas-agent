"""In-memory sink implementations for ingress tests and local development."""

from __future__ import annotations

from dataclasses import dataclass, field


def _new_job_submissions() -> list[JobSubmission]:
    return []


def _new_cancellations() -> list[Cancellation]:
    return []


def _new_event_ids() -> set[str]:
    return set()


@dataclass(frozen=True, slots=True)
class JobSubmission:
    """Infrastructure record for one accepted queue submission."""

    ticket_id: int
    event_id: str


@dataclass(frozen=True, slots=True)
class Cancellation:
    """Infrastructure record for one accepted cancellation signal."""

    ticket_id: int
    event_id: str


@dataclass(slots=True)
class InMemoryJobSink:
    """A deduplicating fake that records only queue metadata and ticket input."""

    submitted: list[JobSubmission] = field(default_factory=_new_job_submissions)
    _seen_event_ids: set[str] = field(
        default_factory=_new_event_ids, init=False, repr=False
    )

    async def submit_ticket(self, *, ticket_id: int, event_id: str) -> bool:
        if event_id in self._seen_event_ids:
            return False
        self._seen_event_ids.add(event_id)
        self.submitted.append(JobSubmission(ticket_id=ticket_id, event_id=event_id))
        return True


@dataclass(slots=True)
class InMemoryCancelSink:
    """A deduplicating fake for active-run cancellation signals."""

    cancelled: list[Cancellation] = field(default_factory=_new_cancellations)
    _seen_event_ids: set[str] = field(
        default_factory=_new_event_ids, init=False, repr=False
    )

    async def cancel_ticket(self, *, ticket_id: int, event_id: str) -> bool:
        if event_id in self._seen_event_ids:
            return False
        self._seen_event_ids.add(event_id)
        self.cancelled.append(Cancellation(ticket_id=ticket_id, event_id=event_id))
        return True


@dataclass(slots=True)
class InMemoryIdempotency:
    """A process-local atomic event-id claim fake."""

    seen_event_ids: set[str] = field(default_factory=_new_event_ids)

    async def claim(self, event_id: str) -> bool:
        if event_id in self.seen_event_ids:
            return False
        self.seen_event_ids.add(event_id)
        return True


# Short aliases are useful for callers that refer to these as memory fakes.
MemoryJobSink = InMemoryJobSink
MemoryCancelSink = InMemoryCancelSink
InMemoryIdempotencyStore = InMemoryIdempotency

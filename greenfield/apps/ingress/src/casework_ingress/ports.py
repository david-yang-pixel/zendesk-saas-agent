"""Side-effect ports used by the webhook ingress boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from casework_ingress.processing_rules import NormalizedZendeskEvent

AdmissionAction = Literal["enqueue", "ignore", "cancel", "duplicate"]


@dataclass(frozen=True, slots=True)
class AdmissionEvent:
    event_id: str
    received_at: datetime
    normalized: NormalizedZendeskEvent
    raw_payload: dict[str, object]

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id must not be blank")
        if self.received_at.tzinfo is None or self.received_at.utcoffset() is None:
            raise ValueError("received_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    action: AdmissionAction
    reason: str
    ticket_id: int


class AdmissionPort(Protocol):
    """Atomic durable admission for one complete normalized webhook."""

    async def admit(self, event: AdmissionEvent) -> AdmissionResult: ...


class JobSink(Protocol):
    """Durable destination for a matching ticket's minimal business input."""

    async def submit_ticket(self, *, ticket_id: int, event_id: str) -> bool:
        """Submit a ticket and return ``False`` when the event was a duplicate."""

        ...


class CancelSink(Protocol):
    """Destination for deterministic human-takeover cancellation signals."""

    async def cancel_ticket(self, *, ticket_id: int, event_id: str) -> bool:
        """Cancel an active ticket run and return ``False`` for a duplicate event."""

        ...


class IdempotencyPort(Protocol):
    """Atomic event-id claim used before evaluating a webhook."""

    async def claim(self, event_id: str) -> bool:
        """Claim an event id, returning ``False`` when it was already claimed."""

        ...


# The store name is useful to adapters while the port name describes its role.
IdempotencyStore = IdempotencyPort

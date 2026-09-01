"""Side-effect ports used by the webhook ingress boundary."""

from __future__ import annotations

from typing import Protocol


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

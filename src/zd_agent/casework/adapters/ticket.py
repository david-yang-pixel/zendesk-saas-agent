from __future__ import annotations

from typing import Any, Protocol, cast

from zd_agent.casework.domain.models import TicketSnapshot, VisualObservation


class CurrentTicketLoader(Protocol):
    async def load(
        self,
        ticket_id: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]: ...


class VisualAnalyzer(Protocol):
    async def analyze(
        self,
        ticket: TicketSnapshot,
    ) -> tuple[VisualObservation, ...]: ...


class ZendeskCurrentTicketLoader:
    async def load(
        self,
        ticket_id: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        from zd_agent.services.ticket_messages import _fetch_customer_messages

        messages, ticket = await _fetch_customer_messages(ticket_id)
        if ticket is None:
            raise RuntimeError("current Zendesk ticket snapshot is unavailable")
        return cast(list[dict[str, Any]], messages), cast(dict[str, Any], ticket)


class NoVisualAnalyzer:
    async def analyze(
        self,
        ticket: TicketSnapshot,
    ) -> tuple[VisualObservation, ...]:
        _ = ticket
        return ()

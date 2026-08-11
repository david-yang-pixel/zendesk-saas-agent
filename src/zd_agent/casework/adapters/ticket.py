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
        from zd_agent.services.ticket_messages import fetch_current_customer_messages

        raw_messages, ticket = await fetch_current_customer_messages(ticket_id)
        if ticket is None:
            raise RuntimeError("current Zendesk ticket snapshot is unavailable")
        messages = raw_messages
        current_ticket = dict(ticket)
        requester = current_ticket.get("requester")
        requester_email = (
            cast(dict[str, Any], requester).get("email")
            if isinstance(requester, dict)
            else ""
        )
        if isinstance(requester_email, str) and requester_email.strip():
            return messages, current_ticket

        requester_id = current_ticket.get("requester_id")
        if (
            isinstance(requester_id, bool)
            or not isinstance(requester_id, int)
            or requester_id <= 0
        ):
            return messages, current_ticket

        from zd_agent.integrations.zendesk_mcp import call_zendesk_tool

        payload = await call_zendesk_tool("get_user", {"user_id": requester_id})
        if not payload.get("success"):
            raise RuntimeError("current Zendesk requester lookup is unavailable")
        raw_data: Any = payload.get("data") or {}
        if not isinstance(raw_data, dict):
            raise RuntimeError("Zendesk MCP get_user returned invalid data")
        typed_data = cast(dict[str, Any], raw_data)
        user: Any = typed_data.get("user", typed_data)
        if not isinstance(user, dict):
            raise RuntimeError("Zendesk MCP get_user returned invalid user")
        typed_user = cast(dict[str, Any], user)
        email = typed_user.get("email")
        if not isinstance(email, str) or not email.strip():
            # Messaging requesters can be anonymous Zendesk users while the
            # submitted support form still contains the authoritative contact
            # email.  Missing requester email is therefore an expected absence,
            # not a transport failure; context construction will prefer the form
            # email and fall back to requester data only when it exists.
            return messages, current_ticket
        current_ticket["requester"] = dict(typed_user)
        return messages, current_ticket


class NoVisualAnalyzer:
    async def analyze(
        self,
        ticket: TicketSnapshot,
    ) -> tuple[VisualObservation, ...]:
        _ = ticket
        return ()

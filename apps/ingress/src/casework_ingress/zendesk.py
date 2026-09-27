"""Read-only Zendesk Tickets API adapter used by raw Messaging ingress."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast

import httpx


class ZendeskTicketError(RuntimeError):
    """Raised when a ticket cannot be safely projected for rule evaluation."""


@dataclass(frozen=True, slots=True)
class ZendeskTicket:
    """The only ticket fields needed by deterministic ingress rules."""

    ticket_id: int
    tags: tuple[str, ...]
    group_id: str | int | None
    status: str
    assignee_id: int | None = None
    created_at: datetime | None = None


class ZendeskTicketReader(Protocol):
    """Read-only boundary for fetching current ticket rule attributes."""

    async def fetch_ticket(self, ticket_id: int) -> ZendeskTicket: ...


class ZendeskApiTicketReader:
    """Fetch and strictly project one ticket from the Zendesk REST API."""

    def __init__(
        self,
        *,
        base_url: str,
        email: str,
        api_token: str,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        origin = httpx.URL(base_url)
        if origin.scheme != "https" or not origin.host:
            raise ValueError("Zendesk base URL must be an HTTPS origin")
        if origin.path not in {"", "/"} or origin.query or origin.fragment:
            raise ValueError("Zendesk base URL must not include a path or query")
        if not email.strip() or not api_token.strip():
            raise ValueError("Zendesk email and API token are required")
        if timeout_seconds <= 0:
            raise ValueError("Zendesk timeout must be positive")

        self._origin = origin.copy_with(path="/", query=None, fragment=None)
        self._auth = httpx.BasicAuth(f"{email.strip()}/token", api_token)
        self._timeout = timeout_seconds
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None

    @classmethod
    def from_environment(cls) -> ZendeskApiTicketReader:
        base_url = os.environ.get("ZENDESK_BASE_URL", "").strip()
        if not base_url:
            subdomain = os.environ.get("ZENDESK_SUBDOMAIN", "").strip()
            if subdomain:
                base_url = f"https://{subdomain}.zendesk.com"
        email = (
            os.environ.get("ZENDESK_EMAIL", "").strip()
            or os.environ.get("ZENDESK_ACCOUNT_EMAIL", "").strip()
        )
        api_token = os.environ.get("ZENDESK_API_TOKEN", "").strip()
        timeout_value = os.environ.get("ZENDESK_TIMEOUT_SECONDS", "10").strip()
        try:
            timeout_seconds = float(timeout_value)
        except ValueError as error:
            raise RuntimeError("ZENDESK_TIMEOUT_SECONDS must be numeric") from error
        return cls(
            base_url=base_url,
            email=email,
            api_token=api_token,
            timeout_seconds=timeout_seconds,
        )

    async def fetch_ticket(self, ticket_id: int) -> ZendeskTicket:
        if ticket_id <= 0:
            raise ValueError("ticket_id must be positive")
        url = self._origin.join(f"/api/v2/tickets/{ticket_id}.json")
        try:
            response = await self._client.get(
                url,
                auth=self._auth,
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise ZendeskTicketError("Zendesk ticket request failed") from error

        if not isinstance(payload, dict):
            raise ZendeskTicketError("Zendesk ticket response was not an object")
        payload_object = cast(dict[str, object], payload)
        raw_ticket_value = payload_object.get("ticket")
        if not isinstance(raw_ticket_value, dict):
            raise ZendeskTicketError("Zendesk response did not contain a ticket")
        raw_ticket = cast(dict[str, object], raw_ticket_value)
        returned_id = self._positive_integer(raw_ticket.get("id"))
        if returned_id != ticket_id:
            raise ZendeskTicketError("Zendesk returned a different ticket")
        raw_tags_value = raw_ticket.get("tags")
        raw_tags = (
            cast(list[object], raw_tags_value)
            if isinstance(raw_tags_value, list)
            else None
        )
        if not isinstance(raw_tags, list) or any(
            not isinstance(tag, str) for tag in raw_tags
        ):
            raise ZendeskTicketError("Zendesk ticket tags were invalid")
        tags = cast(list[str], raw_tags)
        status = raw_ticket.get("status")
        if not isinstance(status, str) or not status.strip():
            raise ZendeskTicketError("Zendesk ticket status was invalid")
        raw_group_id = raw_ticket.get("group_id")
        group_id = (
            None
            if raw_group_id is None
            else self._positive_integer(raw_group_id)
        )
        raw_assignee_id = raw_ticket.get("assignee_id")
        assignee_id = (
            None
            if raw_assignee_id is None
            else self._positive_integer(raw_assignee_id)
        )
        raw_created_at = raw_ticket.get("created_at")
        created_at = None
        if raw_created_at is not None:
            if not isinstance(raw_created_at, str):
                raise ZendeskTicketError("Zendesk ticket creation time was invalid")
            try:
                created_at = datetime.fromisoformat(
                    raw_created_at.replace("Z", "+00:00")
                )
            except ValueError as error:
                raise ZendeskTicketError(
                    "Zendesk ticket creation time was invalid"
                ) from error
            if created_at.tzinfo is None or created_at.utcoffset() is None:
                raise ZendeskTicketError("Zendesk ticket creation time was invalid")
            created_at = created_at.astimezone(UTC)
        return ZendeskTicket(
            ticket_id=ticket_id,
            tags=tuple(tags),
            group_id=group_id,
            status=status,
            assignee_id=assignee_id,
            created_at=created_at,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def _positive_integer(value: object) -> int:
        if type(value) is int and value > 0:
            return value
        if isinstance(value, str) and value.isdecimal():
            parsed = int(value)
            if parsed > 0:
                return parsed
        raise ZendeskTicketError("Zendesk ticket identifier was invalid")


# Explicit aliases keep adapter naming close to the domain port.
HttpxZendeskTicketReader = ZendeskApiTicketReader
TicketReader = ZendeskTicketReader
ZendeskTicketApi = ZendeskApiTicketReader
ZendeskTicketClient = ZendeskApiTicketReader
ZendeskTicketPort = ZendeskTicketReader


__all__ = [
    "HttpxZendeskTicketReader",
    "TicketReader",
    "ZendeskApiTicketReader",
    "ZendeskTicket",
    "ZendeskTicketApi",
    "ZendeskTicketClient",
    "ZendeskTicketError",
    "ZendeskTicketPort",
    "ZendeskTicketReader",
]

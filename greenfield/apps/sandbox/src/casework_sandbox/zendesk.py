"""Read-only Zendesk API adapter that builds a complete ticket snapshot."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import httpx
from pydantic import JsonValue

from contracts.ticket import TicketAttachment, TicketMessage, TicketSnapshot


class ZendeskSnapshotError(RuntimeError):
    """The ticket could not be fetched completely and must not be answered."""


class ZendeskApiReader:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        email: str,
        api_token: str,
        timeout_seconds: float = 20.0,
        max_pages: int = 100,
    ) -> None:
        origin = httpx.URL(base_url)
        if origin.scheme != "https" or not origin.host:
            raise ValueError("Zendesk base URL must be an HTTPS origin")
        if origin.path not in {"", "/"} or origin.query:
            raise ValueError("Zendesk base URL must not include a path or query")
        if not email.strip() or not api_token.strip():
            raise ValueError("Zendesk email and API token are required")
        if timeout_seconds <= 0 or max_pages < 1:
            raise ValueError("Zendesk timeout and max_pages must be positive")
        self._client = client
        self._origin = origin.copy_with(path="/", query=None, fragment=None)
        self._auth = httpx.BasicAuth(f"{email.strip()}/token", api_token)
        self._timeout = timeout_seconds
        self._max_pages = max_pages

    async def fetch_ticket(self, ticket_id: int) -> TicketSnapshot:
        if ticket_id <= 0:
            raise ValueError("ticket_id must be positive")
        ticket_payload = await self._get_json(f"/api/v2/tickets/{ticket_id}.json")
        ticket = self._object(ticket_payload.get("ticket"), field="ticket")
        if self._integer(ticket.get("id"), field="ticket.id") != ticket_id:
            raise ZendeskSnapshotError("Zendesk returned a different ticket")
        requester_id = self._integer(
            ticket.get("requester_id"), field="ticket.requester_id"
        )
        messages = await self._fetch_comments(ticket_id, requester_id=requester_id)
        customer_messages = tuple(
            message
            for message in messages
            if message.public and message.author_origin == "customer"
        )
        if not customer_messages:
            raise ZendeskSnapshotError("ticket has no complete public customer event")
        locale = await self._fetch_requester_locale(requester_id)
        return TicketSnapshot(
            ticket_id=ticket_id,
            status=self._string(ticket.get("status"), field="ticket.status"),
            requester_id=requester_id,
            assignee_id=self._optional_integer(
                ticket.get("assignee_id"), field="ticket.assignee_id"
            ),
            tags=tuple(
                self._string(tag, field="ticket.tags[]")
                for tag in self._array(ticket.get("tags"), field="ticket.tags")
            ),
            form_fields=self._form_fields(ticket.get("custom_fields")),
            messages=messages,
            locale=locale,
            latest_customer_cursor=customer_messages[-1].event_id,
            complete=True,
            fetched_at=datetime.now(UTC),
        )

    async def _fetch_comments(
        self, ticket_id: int, *, requester_id: int
    ) -> tuple[TicketMessage, ...]:
        next_url: str | None = f"/api/v2/tickets/{ticket_id}/comments.json"
        seen: set[str] = set()
        messages: list[TicketMessage] = []
        for _ in range(self._max_pages):
            if next_url is None:
                return tuple(messages)
            absolute = self._safe_url(next_url)
            normalized = str(absolute)
            if normalized in seen:
                raise ZendeskSnapshotError("Zendesk comment pagination loop")
            seen.add(normalized)
            payload = await self._get_json(absolute)
            comments = self._array(payload.get("comments"), field="comments")
            messages.extend(
                self._message(comment, requester_id=requester_id)
                for comment in comments
            )
            raw_next = payload.get("next_page")
            if raw_next is None:
                next_url = None
            elif isinstance(raw_next, str) and raw_next.strip():
                next_url = raw_next
            else:
                raise ZendeskSnapshotError("invalid Zendesk next_page")
        raise ZendeskSnapshotError("Zendesk comment pagination limit exceeded")

    async def _fetch_requester_locale(self, requester_id: int) -> str:
        payload = await self._get_json(f"/api/v2/users/{requester_id}.json")
        user = self._object(payload.get("user"), field="user")
        locale = user.get("locale") or user.get("locale_id")
        if not isinstance(locale, str) or len(locale.strip()) < 2:
            return "en-US"
        return locale.strip()

    async def _get_json(self, url: str | httpx.URL) -> dict[str, Any]:
        safe_url = self._safe_url(url)
        try:
            response = await self._client.get(
                safe_url,
                auth=self._auth,
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise ZendeskSnapshotError("Zendesk snapshot request failed") from error
        return self._object(payload, field="response")

    def _safe_url(self, value: str | httpx.URL) -> httpx.URL:
        url = value if isinstance(value, httpx.URL) else httpx.URL(value)
        if not url.is_absolute_url:
            url = self._origin.join(url)
        if (
            url.scheme != self._origin.scheme
            or url.host != self._origin.host
            or url.port != self._origin.port
        ):
            raise ZendeskSnapshotError("cross-origin Zendesk pagination rejected")
        if not url.path.startswith("/api/v2/"):
            raise ZendeskSnapshotError("Zendesk API path rejected")
        return url

    @classmethod
    def _message(cls, value: object, *, requester_id: int) -> TicketMessage:
        comment = cls._object(value, field="comment")
        author_id = cls._integer(comment.get("author_id"), field="comment.author_id")
        attachments = tuple(
            cls._attachment(item)
            for item in cls._array(
                comment.get("attachments", []), field="comment.attachments"
            )
        )
        return TicketMessage(
            event_id=str(cls._integer(comment.get("id"), field="comment.id")),
            author_origin="customer" if author_id == requester_id else "agent",
            public=cls._boolean(comment.get("public"), field="comment.public"),
            body=cls._string(comment.get("body"), field="comment.body"),
            created_at=cls._datetime(
                comment.get("created_at"), field="comment.created_at"
            ),
            attachments=attachments,
        )

    @classmethod
    def _attachment(cls, value: object) -> TicketAttachment:
        attachment = cls._object(value, field="attachment")
        return TicketAttachment(
            attachment_id=str(
                cls._integer(attachment.get("id"), field="attachment.id")
            ),
            filename=cls._string(
                attachment.get("file_name"), field="attachment.file_name"
            ),
            content_type=cls._string(
                attachment.get("content_type"), field="attachment.content_type"
            ),
            size_bytes=cls._integer(
                attachment.get("size"), field="attachment.size", allow_zero=True
            ),
        )

    @classmethod
    def _form_fields(cls, value: object) -> dict[str, JsonValue]:
        if value is None:
            return {}
        result: dict[str, JsonValue] = {}
        for item in cls._array(value, field="ticket.custom_fields"):
            field = cls._object(item, field="ticket.custom_fields[]")
            field_id = cls._integer(field.get("id"), field="custom_field.id")
            result[str(field_id)] = cast(JsonValue, field.get("value"))
        return result

    @staticmethod
    def _object(value: object, *, field: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ZendeskSnapshotError(f"Zendesk {field} must be an object")
        return cast(dict[str, Any], value)

    @staticmethod
    def _array(value: object, *, field: str) -> list[object]:
        if not isinstance(value, list):
            raise ZendeskSnapshotError(f"Zendesk {field} must be an array")
        return cast(list[object], value)

    @staticmethod
    def _string(value: object, *, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ZendeskSnapshotError(f"Zendesk {field} must be a string")
        return value

    @staticmethod
    def _integer(value: object, *, field: str, allow_zero: bool = False) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ZendeskSnapshotError(f"Zendesk {field} must be an integer")
        if value < 0 or (value == 0 and not allow_zero):
            raise ZendeskSnapshotError(f"Zendesk {field} must be positive")
        return value

    @classmethod
    def _optional_integer(cls, value: object, *, field: str) -> int | None:
        return None if value is None else cls._integer(value, field=field)

    @staticmethod
    def _boolean(value: object, *, field: str) -> bool:
        if not isinstance(value, bool):
            raise ZendeskSnapshotError(f"Zendesk {field} must be a boolean")
        return value

    @staticmethod
    def _datetime(value: object, *, field: str) -> datetime:
        if not isinstance(value, str):
            raise ZendeskSnapshotError(f"Zendesk {field} must be a timestamp")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ZendeskSnapshotError(
                f"Zendesk {field} must be a timestamp"
            ) from error
        if parsed.tzinfo is None:
            raise ZendeskSnapshotError(f"Zendesk {field} must include a timezone")
        return parsed

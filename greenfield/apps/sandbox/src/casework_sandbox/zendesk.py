"""Read-only Zendesk API adapter that builds a complete ticket snapshot."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast

import httpx
from pydantic import JsonValue

from casework_agent_core.domain import TicketSnapshotNotReadyError
from casework_tool_sdk.registry import ToolExecutionContext
from contracts.ticket import TicketAttachment, TicketMessage, TicketSnapshot
from contracts.tool import ToolResult, ToolResultStatus


class ZendeskSnapshotError(RuntimeError):
    """The ticket could not be fetched completely and must not be answered."""


_CROCKFORD_BASE32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_MESSAGING_EVENT_CLOCK_SKEW = timedelta(seconds=10)


def canonical_messaging_event_id(event_id: str) -> str:
    """Normalize webhook and snapshot identifiers to one comparison format."""

    value = event_id.strip()
    if not value:
        raise ValueError("expected_event_id must not be blank")
    if value.startswith("messaging:") or value.isdecimal():
        return value
    return f"messaging:{value}"


def _messaging_event_time(event_id: str) -> datetime | None:
    """Decode the timestamp carried by a Zendesk Messaging webhook ULID.

    Zendesk uses different ULIDs for the webhook envelope and the conversation
    event.  Exact equality is therefore insufficient, but the envelope ULID's
    millisecond timestamp still provides a bounded freshness watermark.
    """

    value = event_id.removeprefix("messaging:").strip().upper()
    if len(value) != 26 or value[0] not in "01234567":
        return None
    timestamp = 0
    for character in value[:10]:
        try:
            digit = _CROCKFORD_BASE32.index(character)
        except ValueError:
            return None
        timestamp = timestamp * 32 + digit
    try:
        return datetime.fromtimestamp(timestamp / 1_000, UTC)
    except (OverflowError, OSError, ValueError):
        return None


class StructuredZendeskTool(Protocol):
    """One configured read-only Zendesk MCP tool."""

    async def __call__(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult: ...


class ZendeskMessagingLoader:
    """Load every bounded Messaging page through the configured Zendesk MCP."""

    def __init__(
        self,
        *,
        ticket_tool: StructuredZendeskTool,
        conversation_tool: StructuredZendeskTool,
        page_size: int = 100,
        max_pages: int = 10,
        max_events: int = 1_000,
    ) -> None:
        if page_size < 1 or max_pages < 1 or max_events < 1:
            raise ValueError("Messaging pagination limits must be positive")
        self._ticket_tool = ticket_tool
        self._conversation_tool = conversation_tool
        self._page_size = page_size
        self._max_pages = max_pages
        self._max_events = max_events

    async def fetch(self, ticket_id: int) -> tuple[TicketMessage, ...]:
        context = ToolExecutionContext(
            ticket_id=ticket_id,
            session_id=f"snapshot-{ticket_id}",
            root_session_id=f"snapshot-{ticket_id}",
        )
        first = await self._ticket_tool(
            {
                "ticket_id": ticket_id,
                "include_comments": True,
                "include_conversation_log": True,
                "conversation_page_size": self._page_size,
                "force_refresh": True,
            },
            context,
        )
        payload = self._payload(first, tool_name="get_ticket")
        events, meta = self._conversation_parts(payload)
        page_count = 1
        seen_cursors: set[str] = set()
        while bool(meta.get("has_more")):
            if len(events) >= self._max_events:
                raise ZendeskSnapshotError("Messaging event limit exceeded")
            if page_count >= self._max_pages:
                raise ZendeskSnapshotError("Messaging page limit exceeded")
            cursor = meta.get("after_cursor")
            if not isinstance(cursor, str) or not cursor.strip():
                raise ZendeskSnapshotError(
                    "Messaging pagination is missing after_cursor"
                )
            if cursor in seen_cursors:
                raise ZendeskSnapshotError("Messaging pagination cursor repeated")
            seen_cursors.add(cursor)
            result = await self._conversation_tool(
                {
                    "ticket_id": ticket_id,
                    "conversation_after": cursor,
                    "conversation_page_size": self._page_size,
                    "force_refresh": True,
                },
                context,
            )
            page_payload = self._payload(
                result, tool_name="get_ticket_conversation_log"
            )
            page_events, meta = self._conversation_parts(page_payload)
            events.extend(page_events)
            page_count += 1
            if len(events) > self._max_events:
                raise ZendeskSnapshotError("Messaging event limit exceeded")

        messages = tuple(
            message for event in events if (message := self._message(event)) is not None
        )
        return tuple(
            sorted(messages, key=lambda item: (item.created_at, item.event_id))
        )

    @staticmethod
    def _payload(result: ToolResult, *, tool_name: str) -> dict[str, Any]:
        if result.status is not ToolResultStatus.SUCCESS:
            raise ZendeskSnapshotError(f"{tool_name} did not return a complete result")
        data = result.data
        if not isinstance(data, dict):
            raise ZendeskSnapshotError(f"{tool_name} returned no structured data")
        data_mapping = cast(dict[str, Any], data)
        structured = data_mapping.get("structured_content")
        if not isinstance(structured, dict):
            content = data_mapping.get("content")
            structured = ZendeskMessagingLoader._structured_from_content(content)
        if not isinstance(structured, dict):
            raise ZendeskSnapshotError(f"{tool_name} returned no structured data")
        structured_mapping = cast(dict[str, Any], structured)
        if structured_mapping.get("success") is not True:
            raise ZendeskSnapshotError(f"{tool_name} returned an unsuccessful payload")
        payload = structured_mapping.get("data")
        if not isinstance(payload, dict):
            raise ZendeskSnapshotError(f"{tool_name} payload is missing data")
        return cast(dict[str, Any], payload)

    @staticmethod
    def _structured_from_content(value: object) -> dict[str, Any] | None:
        if not isinstance(value, list):
            return None
        for raw in cast(list[object], value):
            if not isinstance(raw, dict):
                continue
            block = cast(dict[str, Any], raw)
            if block.get("type") != "text":
                continue
            text = block.get("text")
            if not isinstance(text, str):
                continue
            try:
                decoded = json.loads(text)
            except ValueError:
                continue
            if isinstance(decoded, dict):
                return cast(dict[str, Any], decoded)
        return None

    @staticmethod
    def _conversation_parts(
        payload: dict[str, Any],
    ) -> tuple[list[object], dict[str, Any]]:
        conversation = payload.get("conversation_log")
        if isinstance(conversation, dict):
            nested = conversation.get("conversation_log")
            source = (
                cast(dict[str, Any], nested)
                if isinstance(nested, dict)
                else conversation
            )
        elif "events" in payload:
            source = payload
        else:
            return [], {}
        events = source.get("events")
        meta = source.get("meta")
        if not isinstance(events, list):
            raise ZendeskSnapshotError("Messaging events must be an array")
        if not isinstance(meta, dict):
            meta = {}
        return cast(list[object], events), cast(dict[str, Any], meta)

    @classmethod
    def _message(cls, value: object) -> TicketMessage | None:
        if not isinstance(value, dict):
            raise ZendeskSnapshotError("Messaging event must be an object")
        event = cast(dict[str, Any], value)
        author = event.get("author")
        content = event.get("content")
        if not isinstance(author, dict) or not isinstance(content, dict):
            # Conversation logs also contain non-message lifecycle events.
            return None
        event_id = event.get("id")
        if not isinstance(event_id, (str, int)) or not str(event_id).strip():
            raise ZendeskSnapshotError("Messaging event is missing id")
        author_mapping = cast(dict[str, Any], author)
        content_mapping = cast(dict[str, Any], content)
        author_type = str(author_mapping.get("type") or "").strip().casefold()
        if not author_type:
            raise ZendeskSnapshotError("Messaging event is missing author type")
        metadata = event.get("metadata")
        metadata_public = (
            cast(dict[str, Any], metadata).get("is_public")
            if isinstance(metadata, dict)
            else None
        )
        public = event.get("public") is not False and metadata_public is not False
        customer = author_type in {"end-user", "customer", "requester"}

        msg_id = str(event_id).strip()
        reference = str(event.get("reference") or "").strip()
        alias_ids: list[str] = [f"messaging:{msg_id}", msg_id]
        if reference.startswith("zen:sunco:conversation_message:"):
            sunco_id = reference.split(":")[-1].strip()
            if sunco_id:
                msg_id = sunco_id
                alias_ids.extend([f"messaging:{sunco_id}", sunco_id])

        return TicketMessage(
            event_id=f"messaging:{msg_id}",
            author_origin="customer" if customer else "agent",
            public=public,
            body=cls._body(content_mapping),
            created_at=cls._datetime(
                event.get("created_at"), field="messaging.created_at"
            ),
            attachments=cls._attachments(
                event, content_mapping, event_id=str(event_id)
            ),
            alias_ids=tuple(dict.fromkeys(alias_ids)),
        )

    @staticmethod
    def _body(content: dict[str, Any]) -> str:
        content_type = str(content.get("type") or "").casefold()
        if content_type == "text":
            return str(content.get("text") or "").strip()
        if content_type == "html":
            raw = str(content.get("body") or content.get("text") or "")
            return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()
        fallback = str(content.get("text_fallback") or "").strip()
        if fallback:
            return fallback
        fields = content.get("fields")
        if isinstance(fields, list):
            lines: list[str] = []
            for raw in cast(list[object], fields):
                if not isinstance(raw, dict):
                    continue
                field = cast(dict[str, Any], raw)
                label = field.get("label") or field.get("name")
                field_value = (
                    field.get("value") or field.get("text") or field.get("email")
                )
                if label and field_value not in {None, ""}:
                    lines.append(f"{label}: {field_value}")
            return "\n".join(lines)
        return ""

    @classmethod
    def _attachments(
        cls,
        event: dict[str, Any],
        content: dict[str, Any],
        *,
        event_id: str,
    ) -> tuple[TicketAttachment, ...]:
        values = event.get("attachments")
        raw_attachments: list[object] = (
            list(cast(list[object], values)) if isinstance(values, list) else []
        )
        if content.get("media_url"):
            raw_attachments.append(
                {
                    "id": content.get("attachment_id") or f"{event_id}-media",
                    "file_name": content.get("alt_text") or "attachment",
                    "content_type": (
                        content.get("media_type") or "application/octet-stream"
                    ),
                    "size": content.get("media_size") or 0,
                }
            )
        attachments: list[TicketAttachment] = []
        for index, raw in enumerate(raw_attachments):
            if not isinstance(raw, dict):
                raise ZendeskSnapshotError("Messaging attachment must be an object")
            attachment = cast(dict[str, Any], raw)
            size = attachment.get("size", 0)
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                size = 0
            attachments.append(
                TicketAttachment(
                    attachment_id=str(attachment.get("id") or f"{event_id}-{index}"),
                    filename=str(
                        attachment.get("file_name")
                        or attachment.get("name")
                        or "attachment"
                    ),
                    content_type=str(
                        attachment.get("content_type") or "application/octet-stream"
                    ),
                    size_bytes=size,
                )
            )
        return tuple(attachments)

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


class ZendeskApiReader:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        email: str,
        api_token: str,
        messaging_loader: ZendeskMessagingLoader | None = None,
        timeout_seconds: float = 20.0,
        max_pages: int = 100,
        snapshot_retry_delays: tuple[float, ...] = (0.25, 0.75, 1.5),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
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
        if any(delay < 0 for delay in snapshot_retry_delays):
            raise ValueError("snapshot retry delays must not be negative")
        self._client = client
        self._origin = origin.copy_with(path="/", query=None, fragment=None)
        self._auth = httpx.BasicAuth(f"{email.strip()}/token", api_token)
        self._messaging_loader = messaging_loader
        self._timeout = timeout_seconds
        self._max_pages = max_pages
        self._snapshot_retry_delays = snapshot_retry_delays
        self._sleep = sleep

    async def fetch_ticket(
        self, ticket_id: int, *, expected_event_id: str | None = None
    ) -> TicketSnapshot:
        expected = (
            canonical_messaging_event_id(expected_event_id)
            if expected_event_id is not None
            else None
        )
        snapshot = await self._fetch_ticket_once(ticket_id)
        if expected is None or self._contains_event(snapshot, expected):
            return snapshot
        for delay in self._snapshot_retry_delays:
            await self._sleep(delay)
            snapshot = await self._fetch_ticket_once(ticket_id)
            if self._contains_event(snapshot, expected):
                return snapshot
        raise TicketSnapshotNotReadyError(
            expected_event_id=expected,
            observed_cursor=snapshot.latest_customer_cursor,
        )

    async def _fetch_ticket_once(self, ticket_id: int) -> TicketSnapshot:
        if ticket_id <= 0:
            raise ValueError("ticket_id must be positive")
        ticket_payload = await self._get_json(f"/api/v2/tickets/{ticket_id}.json")
        ticket = self._object(ticket_payload.get("ticket"), field="ticket")
        if self._integer(ticket.get("id"), field="ticket.id") != ticket_id:
            raise ZendeskSnapshotError("Zendesk returned a different ticket")
        requester_id = self._integer(
            ticket.get("requester_id"), field="ticket.requester_id"
        )
        comments = await self._fetch_comments(ticket_id, requester_id=requester_id)
        messaging = (
            await self._messaging_loader.fetch(ticket_id)
            if self._messaging_loader is not None
            else ()
        )
        messages = self._merge_messages(comments, messaging)
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

    @staticmethod
    def _contains_event(snapshot: TicketSnapshot, expected_event_id: str) -> bool:
        if any(
            message.event_id == expected_event_id
            or expected_event_id in getattr(message, "alias_ids", ())
            for message in snapshot.messages
        ):
            return True
        expected_at = _messaging_event_time(expected_event_id)
        if expected_at is None:
            return False
        return any(
            message.public
            and message.author_origin == "customer"
            and message.created_at >= expected_at - _MESSAGING_EVENT_CLOCK_SKEW
            for message in snapshot.messages
        )

    @staticmethod
    def _merge_messages(
        comments: tuple[TicketMessage, ...],
        messaging: tuple[TicketMessage, ...],
    ) -> tuple[TicketMessage, ...]:
        unique: dict[str, TicketMessage] = {}
        for message in (*comments, *messaging):
            if message.event_id in unique:
                raise ZendeskSnapshotError("Zendesk returned a duplicate message id")
            unique[message.event_id] = message
        return tuple(
            sorted(unique.values(), key=lambda item: (item.created_at, item.event_id))
        )

    async def ready(self) -> bool:
        """Verify that Zendesk credentials and the configured origin work."""

        try:
            response = await self._client.get(
                self._origin.join("/api/v2/users/me.json"),
                auth=self._auth,
                timeout=3.0,
            )
            return response.is_success
        except httpx.HTTPError:
            return False

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

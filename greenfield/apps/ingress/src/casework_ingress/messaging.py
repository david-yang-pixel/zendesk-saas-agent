"""Zendesk Messaging payload parsing and signature verification."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from .processing_rules import NormalizedZendeskEvent
from .zendesk import ZendeskTicket

_CUSTOMER_ACTOR_TYPES = frozenset({"user", "end_user", "end-user", "customer"})


class MessagingPayloadError(ValueError):
    """Raised for a malformed raw Zendesk Messaging payload."""


@dataclass(frozen=True, slots=True)
class MessagingEvent:
    """Sanitized event projection retained after raw payload parsing."""

    event_id: str
    ticket_id: int
    actor_type: str
    message_id: str | None
    message_text: str
    public: bool
    message_present: bool = True

    @property
    def is_customer_message(self) -> bool:
        return (
            self.actor_type in _CUSTOMER_ACTOR_TYPES
            and self.public
            and self.message_present
        )

    def to_normalized_event(self, ticket: ZendeskTicket) -> NormalizedZendeskEvent:
        if ticket.ticket_id != self.ticket_id:
            raise MessagingPayloadError("ticket lookup returned a different ticket")
        return NormalizedZendeskEvent(
            ticket_id=ticket.ticket_id,
            tags=ticket.tags,
            # ProcessingRuleConfig keeps the legacy ``saas_assignee`` storage
            # key, but routing is intentionally based on the Zendesk group.
            assignee=ticket.group_id,
            status=ticket.status,
            is_customer_message=self.is_customer_message,
            message_text=self.message_text,
        )

    def safe_payload(self) -> dict[str, object]:
        """Return metadata only; never retain the raw body or message content."""

        payload: dict[str, object] = {
            "event_id": self.event_id,
            "ticket_id": self.ticket_id,
            "actor_type": self.actor_type,
            "message_id": self.message_id,
            "public": self.public,
        }
        if self.message_id is not None:
            payload["snapshot_cursor"] = f"messaging:{self.message_id}"
        return payload


def verify_zendesk_signature(
    *,
    secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
    now: float | None = None,
    max_age_seconds: float = 300.0,
) -> bool:
    """Verify Zendesk's base64 HMAC-SHA256(timestamp + raw body) signature."""

    if not secret.strip() or not timestamp.strip() or not signature.strip():
        return False
    if max_age_seconds < 0:
        return False
    timestamp_value = timestamp.strip()
    try:
        occurred_at = float(int(timestamp_value))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(timestamp_value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return False
            occurred_at = parsed.timestamp()
        except (OverflowError, ValueError):
            return False
    current_time = time.time() if now is None else now
    if abs(current_time - occurred_at) > max_age_seconds:
        return False
    expected = base64.b64encode(
        hmac.new(
            secret.encode(), timestamp_value.encode() + body, hashlib.sha256
        ).digest()
    ).decode()
    return hmac.compare_digest(expected, signature.strip())


def parse_messaging_payload(value: object) -> MessagingEvent:
    """Extract only event/ticket/actor/message fields from a raw webhook."""

    payload = _object(value, "payload")
    event_id = _event_id(payload.get("id", payload.get("event_id")))
    event_body = _object(payload.get("event"), "event")
    detail = payload.get("detail")
    detail_body = {} if detail is None else _object(detail, "detail")
    ticket_id = _ticket_id(payload, event_body, detail_body)

    actor_value = event_body.get("actor")
    actor_object = (
        cast(dict[str, object], actor_value)
        if isinstance(actor_value, dict)
        else {}
    )
    actor_type_value = actor_object.get("type")
    actor_type = (
        actor_type_value.strip().casefold()
        if isinstance(actor_type_value, str)
        else ""
    )

    # Unknown actor shapes are safe no-ops.  Do not inspect their message
    # content or call Zendesk for an event that cannot be a customer message.
    if actor_type not in _CUSTOMER_ACTOR_TYPES:
        return MessagingEvent(
            event_id=event_id,
            ticket_id=ticket_id,
            actor_type=actor_type,
            message_id=None,
            message_text="",
            public=False,
            message_present=False,
        )

    message_value = event_body.get("message")
    if message_value is None:
        return MessagingEvent(
            event_id=event_id,
            ticket_id=ticket_id,
            actor_type=actor_type,
            message_id=None,
            message_text="",
            public=False,
            message_present=False,
        )
    message = _object(message_value, "event.message")
    message_id = _optional_identifier(message.get("id"))
    body_value = message.get("body", message.get("text"))
    if not isinstance(body_value, str):
        content = message.get("content")
        if isinstance(content, dict):
            content_object = cast(dict[str, object], content)
            body_value = content_object.get(
                "text", content_object.get("body")
            )
    message_text = body_value if isinstance(body_value, str) else ""
    public = _public_message(message, event_body)
    return MessagingEvent(
        event_id=event_id,
        ticket_id=ticket_id,
        actor_type=actor_type,
        message_id=message_id,
        message_text=message_text,
        public=public,
        message_present=isinstance(body_value, str) and bool(body_value.strip()),
    )


def _object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise MessagingPayloadError(f"{field} must be an object")
    return cast(dict[str, object], value)


def _event_id(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise MessagingPayloadError("missing event_id")


def _optional_identifier(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        result = str(value).strip()
        return result or None
    return None


def _ticket_id(
    payload: dict[str, object],
    event_body: dict[str, object],
    detail: dict[str, object],
) -> int:
    candidates = (
        detail.get("id"),
        payload.get("ticket_id"),
        event_body.get("ticket_id"),
    )
    for candidate in candidates:
        if candidate is not None:
            return _positive_ticket_id(candidate)
    subject = payload.get("subject")
    if isinstance(subject, str) and subject.startswith("zen:ticket:"):
        return _positive_ticket_id(subject.split(":", 2)[-1])
    raise MessagingPayloadError("missing ticket_id")


def _positive_ticket_id(value: object) -> int:
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and value.isdecimal():
        result = int(value)
        if result > 0:
            return result
    raise MessagingPayloadError("invalid ticket_id")


def _public_message(
    message: dict[str, object], event_body: dict[str, object]
) -> bool:
    explicit_public = message.get("public", message.get("is_public"))
    if explicit_public is None:
        explicit_public = event_body.get("public", event_body.get("is_public"))
    if explicit_public is not None:
        return explicit_public is True
    visibility = message.get("visibility")
    if visibility is None:
        visibility = event_body.get("visibility")
    if visibility is not None:
        return isinstance(visibility, str) and visibility.strip().casefold() == "public"
    return True


def decode_json_body(body: bytes) -> object:
    """Decode without logging or retaining the raw body on failure."""

    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MessagingPayloadError("invalid JSON") from error


__all__ = [
    "MessagingEvent",
    "MessagingPayloadError",
    "decode_json_body",
    "parse_messaging_payload",
    "verify_messaging_signature",
    "verify_zendesk_signature",
]


# Keep the transport-specific name available to callers that distinguish it
# from the classic Tickets webhook signature.
verify_messaging_signature = verify_zendesk_signature

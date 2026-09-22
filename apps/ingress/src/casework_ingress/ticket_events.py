"""Strict projection of signed Zendesk ticket comment events."""

from dataclasses import dataclass, field

from .messaging import MessagingPayloadError, _object, _positive_ticket_id
from .processing_rules import NormalizedZendeskEvent, has_operator_mention

COMMENT_ADDED = "zen:event-type:ticket.comment_added"


@dataclass(frozen=True, slots=True)
class TicketCommentEvent:
    event_id: str
    ticket_id: int
    comment_id: str
    author_id: str
    body: str = field(repr=False)
    is_public: bool
    is_staff: bool

    @property
    def eligible(self) -> bool:
        return not self.is_public and self.is_staff and has_operator_mention(self.body)

    def normalized(self) -> NormalizedZendeskEvent:
        return NormalizedZendeskEvent(ticket_id=self.ticket_id, message_text=self.body)

    def payload(self) -> dict[str, object]:
        # Retain only the instruction needed by the worker, never the raw
        # Zendesk payload (names, HTML, ticket description, etc.). Not for logs.
        return {
            "event_id": self.event_id,
            "ticket_id": self.ticket_id,
            "comment_id": self.comment_id,
            "author_id": self.author_id,
            "trigger": "operator_mention",
            "snapshot_cursor": f"mention:{self.comment_id}",
            "body": self.body,
        }


def parse_ticket_event(value: object) -> TicketCommentEvent | None:
    payload = _object(value, "payload")
    event_type = payload.get("type")
    if not isinstance(event_type, str) or not event_type.strip():
        raise MessagingPayloadError("missing type")
    if event_type != COMMENT_ADDED:
        return None
    event_id = payload.get("id")
    if not isinstance(event_id, str) or not event_id.strip():
        raise MessagingPayloadError("missing event id")
    detail = _object(payload.get("detail"), "detail")
    event = _object(payload.get("event"), "event")
    comment = _object(event.get("comment"), "comment")
    author = _object(comment.get("author"), "author")
    body = comment.get("body")
    public, staff = comment.get("is_public"), author.get("is_staff")
    if not isinstance(body, str) or type(public) is not bool or type(staff) is not bool:
        raise MessagingPayloadError("invalid comment fields")
    return TicketCommentEvent(
        event_id=event_id.strip(),
        ticket_id=_positive_ticket_id(detail.get("id")),
        comment_id=str(_positive_ticket_id(comment.get("id"))),
        author_id=str(_positive_ticket_id(author.get("id"))),
        body=body,
        is_public=public,
        is_staff=staff,
    )

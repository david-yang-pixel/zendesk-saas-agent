"""Immutable ticket inputs and active-question resolution for the sandbox Agent."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CoreModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class TicketComment(CoreModel):
    comment_id: str = Field(min_length=1)
    body: str = Field(min_length=1)
    public: bool
    author_origin: str = Field(min_length=1)
    created_at: datetime
    event_id: str = Field(min_length=1)

    @field_validator("created_at")
    @classmethod
    def _aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("comment created_at must be timezone-aware")
        return value


class TicketSnapshot(CoreModel):
    """Complete, immutable ticket read model fetched inside the sandbox."""

    ticket_id: int = Field(gt=0)
    subject: str = Field(min_length=1)
    requester_email: str = Field(min_length=1)
    locale: str = Field(default="en-US", min_length=2)
    status: str = Field(min_length=1)
    assignee: str = Field(min_length=1)
    tags: tuple[str, ...] = ()
    form_fields: dict[str, Any] = Field(default_factory=dict)
    comments: tuple[TicketComment, ...] = ()
    messaging_events: tuple[dict[str, Any], ...] = ()
    attachments: tuple[dict[str, Any], ...] = ()
    latest_customer_event_id: str = Field(min_length=1)

    @property
    def customer_comments(self) -> tuple[TicketComment, ...]:
        return tuple(
            comment
            for comment in self.comments
            if comment.public and comment.author_origin.casefold() == "customer"
        )


class ActiveQuestion(CoreModel):
    """Original customer question plus later clarifications, never replacement."""

    question_id: str = Field(min_length=1)
    original_text: str = Field(min_length=1)
    event_ids: tuple[str, ...] = ()
    clarifications: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()


def resolve_active_question(snapshot: TicketSnapshot) -> ActiveQuestion:
    comments = snapshot.customer_comments
    if not comments:
        raise ValueError("ticket has no public customer question")
    original = comments[0]
    clarification_comments = comments[1:]
    return ActiveQuestion(
        question_id=f"question-{snapshot.ticket_id}-{original.event_id}",
        original_text=original.body,
        event_ids=tuple(comment.event_id for comment in comments),
        clarifications=tuple(comment.body for comment in clarification_comments),
    )

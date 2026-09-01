"""Zendesk webhook event contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, StrictInt, field_validator

from contracts._base import ContractModel, require_aware, unique_strings


class EventType(StrEnum):
    TICKET_UPDATED = "ticket.updated"
    TICKET_CREATED = "ticket.created"
    HUMAN_CANCEL = "human.cancel"


class Event(ContractModel):
    """The immutable event snapshot used by deterministic ingress filtering."""

    event_id: str = Field(min_length=1)
    ticket_id: StrictInt = Field(gt=0)
    occurred_at: datetime
    source: str = Field(min_length=1)
    customer_origin: str = Field(min_length=1)
    event_type: EventType = EventType.TICKET_UPDATED
    status: str | None = None
    tags: tuple[str, ...] = ()
    assignee_id: StrictInt | None = Field(default=None, gt=0)
    author_id: StrictInt | None = Field(default=None, gt=0)
    message_id: str | None = None
    message: str | None = None

    @field_validator("occurred_at")
    @classmethod
    def _aware_occurred_at(cls, value: datetime) -> datetime:
        return require_aware(value, "event occurred_at")

    @field_validator("tags")
    @classmethod
    def _unique_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unique_strings(value, "event tags")
        return value

    @property
    def business_input(self) -> dict[str, int]:
        """The only event data allowed to cross into the Agent job."""

        return {"ticket_id": self.ticket_id}


TicketEvent = Event
WebhookEvent = Event
ZendeskEvent = Event


class HumanCancel(ContractModel):
    """Explicit human-takeover command; it carries no Agent job input."""

    event_id: str = Field(min_length=1)
    ticket_id: StrictInt = Field(gt=0)
    occurred_at: datetime
    reason: str = Field(min_length=1)
    source: str = Field(default="zendesk", min_length=1)

    @field_validator("occurred_at")
    @classmethod
    def _aware_occurred_at(cls, value: datetime) -> datetime:
        return require_aware(value, "human cancel occurred_at")


HumanCancelCommand = HumanCancel
HumanCancelEvent = HumanCancel
NormalizedWebhookEvent = Event

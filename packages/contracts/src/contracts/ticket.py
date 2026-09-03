"""Complete Zendesk ticket snapshot and active-question contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, JsonValue, StrictInt, field_validator, model_validator

from contracts._base import ContractModel, require_aware, unique_strings


class TicketAttachment(ContractModel):
    attachment_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    content_type: str = Field(min_length=1)
    size_bytes: StrictInt = Field(ge=0)
    artifact_ref: str | None = Field(default=None, min_length=1)


class TicketMessage(ContractModel):
    event_id: str = Field(min_length=1)
    author_origin: str = Field(min_length=1)
    public: bool
    body: str
    created_at: datetime
    attachments: tuple[TicketAttachment, ...] = ()
    alias_ids: tuple[str, ...] = ()

    @field_validator("created_at")
    @classmethod
    def _aware_created_at(cls, value: datetime) -> datetime:
        return require_aware(value, "ticket message created_at")


class TicketSnapshot(ContractModel):
    ticket_id: StrictInt = Field(gt=0)
    status: str = Field(min_length=1)
    requester_id: StrictInt = Field(gt=0)
    assignee_id: StrictInt | None = Field(default=None, gt=0)
    tags: tuple[str, ...] = ()
    form_fields: dict[str, JsonValue] = Field(default_factory=dict)
    messages: tuple[TicketMessage, ...]
    locale: str = Field(min_length=2)
    latest_customer_cursor: str = Field(min_length=1)
    complete: bool
    fetched_at: datetime

    @field_validator("fetched_at")
    @classmethod
    def _aware_fetched_at(cls, value: datetime) -> datetime:
        return require_aware(value, "ticket snapshot fetched_at")

    @field_validator("tags")
    @classmethod
    def _unique_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unique_strings(value, "ticket snapshot tags")
        return value

    @model_validator(mode="after")
    def _must_be_complete(self) -> TicketSnapshot:
        if not self.complete:
            raise ValueError("ticket snapshot must be complete")
        if not self.messages:
            raise ValueError("ticket snapshot requires messages")
        return self


class ActiveQuestion(ContractModel):
    original_event_id: str = Field(min_length=1)
    original_request: str = Field(min_length=1)
    clarification_event_ids: tuple[str, ...] = ()
    current_facts: tuple[str, ...] = ()

    @field_validator("clarification_event_ids")
    @classmethod
    def _unique_event_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unique_strings(value, "active question event ids")
        return value

    @model_validator(mode="after")
    def _original_is_not_a_clarification(self) -> ActiveQuestion:
        if self.original_event_id in self.clarification_event_ids:
            raise ValueError("original event cannot be a clarification")
        return self

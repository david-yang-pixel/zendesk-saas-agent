"""Structured terminal outcome returned by the sandbox Agent."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AliasChoices, Field, StrictInt, field_validator, model_validator

from contracts._base import ContractModel, unique_strings


class OutcomeKind(StrEnum):
    ANSWER = "answer"
    ASK_CUSTOMER = "ask_customer"
    ASK = "ask_customer"
    HUMAN_TAKEOVER = "human_takeover"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Outcome(ContractModel):
    outcome_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    ticket_id: StrictInt = Field(gt=0)
    kind: OutcomeKind = Field(
        validation_alias=AliasChoices("kind", "disposition")
    )
    answer: str | None = None
    question: str | None = None
    reason: str | None = None
    reply_locale: str | None = Field(default=None, min_length=2)
    snapshot_cursor: str | None = Field(default=None, min_length=1)
    delivery_eligible: bool = False
    todo_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    checkpoint_ref: str | None = Field(default=None, min_length=1)
    retry_classification: str | None = Field(default=None, min_length=1)

    @field_validator("todo_ids", "evidence_ids", "artifact_refs")
    @classmethod
    def _unique_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unique_strings(value, "outcome evidence_ids")
        return value

    @model_validator(mode="after")
    def _required_disposition_fields(self) -> Outcome:
        if self.kind is OutcomeKind.ANSWER:
            if not self.answer or not self.evidence_ids:
                raise ValueError("answer outcome requires answer text and evidence_ids")
        elif self.kind is OutcomeKind.ASK_CUSTOMER:
            if not self.question:
                raise ValueError("ask outcome requires a question")
        elif self.kind in {
            OutcomeKind.HUMAN_TAKEOVER,
            OutcomeKind.FAILED,
            OutcomeKind.CANCELLED,
        } and not self.reason:
            raise ValueError(f"{self.kind.value} outcome requires a reason")
        if self.delivery_eligible and (
            not self.reply_locale or not self.snapshot_cursor
        ):
            raise ValueError(
                "delivery-eligible outcome requires reply_locale and snapshot_cursor"
            )
        if self.delivery_eligible and self.kind not in {
            OutcomeKind.ANSWER,
            OutcomeKind.ASK_CUSTOMER,
        }:
            raise ValueError("outcome kind is not eligible for delivery")
        return self


CaseworkOutcome = Outcome
RunOutcome = Outcome
OutcomeDisposition = OutcomeKind

"""Structured terminal outcome returned by the sandbox Agent."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AliasChoices, Field, StrictInt, field_validator, model_validator

from contracts._base import ContractModel, unique_strings


class OutcomeKind(StrEnum):
    ANSWER = "answer"
    ASK = "ask"
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
    evidence_ids: tuple[str, ...] = ()

    @field_validator("evidence_ids")
    @classmethod
    def _unique_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unique_strings(value, "outcome evidence_ids")
        return value

    @model_validator(mode="after")
    def _required_disposition_fields(self) -> Outcome:
        if self.kind is OutcomeKind.ANSWER:
            if not self.answer or not self.evidence_ids:
                raise ValueError("answer outcome requires answer text and evidence_ids")
        elif self.kind is OutcomeKind.ASK:
            if not self.question:
                raise ValueError("ask outcome requires a question")
        elif self.kind in {
            OutcomeKind.HUMAN_TAKEOVER,
            OutcomeKind.FAILED,
            OutcomeKind.CANCELLED,
        } and not self.reason:
            raise ValueError(f"{self.kind.value} outcome requires a reason")
        return self


CaseworkOutcome = Outcome
RunOutcome = Outcome
OutcomeDisposition = OutcomeKind

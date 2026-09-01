"""Evidence records binding tool observations to durable identifiers."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, JsonValue, field_validator, model_validator

from contracts._base import ContractModel, require_aware, unique_strings


class EvidenceStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"


class Evidence(ContractModel):
    evidence_id: str = Field(min_length=1)
    call_id: str | None = Field(default=None, min_length=1)
    source: str = Field(min_length=1)
    status: EvidenceStatus = EvidenceStatus.SUCCESS
    observed_at: datetime
    targets: tuple[str, ...] = ()
    payload: JsonValue | None = None
    payload_ref: str | None = Field(default=None, min_length=1)
    fresh_until: datetime | None = None
    redacted: bool = True

    @field_validator("observed_at", "fresh_until")
    @classmethod
    def _aware_timestamps(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_aware(value, "evidence timestamp")

    @field_validator("targets")
    @classmethod
    def _unique_targets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unique_strings(value, "evidence targets")
        return value

    @model_validator(mode="after")
    def _validate_payload(self) -> Evidence:
        if self.payload is None and not self.payload_ref:
            raise ValueError("evidence requires a payload or payload_ref")
        if self.status is EvidenceStatus.SUCCESS and not self.call_id:
            raise ValueError("successful evidence requires a call_id")
        return self


EvidenceItem = Evidence
EvidenceResultStatus = EvidenceStatus

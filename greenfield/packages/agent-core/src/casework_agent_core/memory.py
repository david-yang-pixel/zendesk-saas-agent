"""Ephemeral working memory and review-gated durable memory proposals."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class WorkingFact:
    key: str
    value: str
    evidence_ids: tuple[str, ...]


class WorkingMemory:
    """Session-scoped facts that disappear with the ticket sandbox."""

    def __init__(self) -> None:
        self._facts: dict[str, WorkingFact] = {}

    def record(
        self, *, key: str, value: str, evidence_ids: tuple[str, ...]
    ) -> WorkingFact:
        if not key.strip() or not value.strip():
            raise ValueError("working-memory key and value are required")
        if not evidence_ids or any(not item.strip() for item in evidence_ids):
            raise ValueError("working-memory fact requires evidence")
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("working-memory evidence IDs must be unique")
        fact = WorkingFact(
            key=key.strip(),
            value=value.strip(),
            evidence_ids=evidence_ids,
        )
        self._facts[fact.key] = fact
        return fact

    def snapshot(self) -> tuple[WorkingFact, ...]:
        return tuple(self._facts[key] for key in sorted(self._facts))


class MemoryProposalStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class MemoryProposal:
    proposal_id: str
    subject: str
    content: str
    evidence_ids: tuple[str, ...]
    source_session_id: str
    status: MemoryProposalStatus
    created_at: datetime
    reviewer: str | None = None
    reviewed_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        proposal_id: str,
        subject: str,
        content: str,
        evidence_ids: tuple[str, ...],
        source_session_id: str,
    ) -> MemoryProposal:
        required = (proposal_id, subject, content, source_session_id)
        if any(not value.strip() for value in required):
            raise ValueError("memory proposal fields must not be empty")
        if not evidence_ids or any(not item.strip() for item in evidence_ids):
            raise ValueError("memory proposal requires evidence")
        normalized = content.casefold().replace(" ", "_")
        if "hidden_reasoning" in normalized or "chain_of_thought" in normalized:
            raise ValueError("hidden reasoning cannot be persisted as memory")
        return cls(
            proposal_id=proposal_id.strip(),
            subject=subject.strip(),
            content=content.strip(),
            evidence_ids=evidence_ids,
            source_session_id=source_session_id.strip(),
            status=MemoryProposalStatus.PROPOSED,
            created_at=datetime.now(UTC),
        )

    def review(self, *, approved: bool, reviewer: str) -> MemoryProposal:
        if self.status is not MemoryProposalStatus.PROPOSED:
            raise ValueError("memory proposal is terminal after review")
        if not reviewer.strip():
            raise ValueError("memory proposal review requires a reviewer")
        return replace(
            self,
            status=(
                MemoryProposalStatus.APPROVED
                if approved
                else MemoryProposalStatus.REJECTED
            ),
            reviewer=reviewer.strip(),
            reviewed_at=datetime.now(UTC),
        )

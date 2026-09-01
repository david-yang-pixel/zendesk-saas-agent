from __future__ import annotations

import pytest

from casework_agent_core.memory import (
    MemoryProposal,
    MemoryProposalStatus,
    WorkingMemory,
)


def test_working_memory_keeps_evidence_bound_facts_only() -> None:
    memory = WorkingMemory()

    memory.record(
        key="affected_app",
        value="app-123",
        evidence_ids=("ev-app",),
    )

    snapshot = memory.snapshot()
    assert snapshot[0].key == "affected_app"
    assert snapshot[0].evidence_ids == ("ev-app",)
    with pytest.raises(ValueError, match="evidence"):
        memory.record(key="guess", value="maybe", evidence_ids=())


def test_long_term_memory_is_only_a_reviewable_proposal() -> None:
    proposal = MemoryProposal.create(
        proposal_id="memory-1",
        subject="workflow timeout diagnosis",
        content="Timeout evidence must include one exact run identifier.",
        evidence_ids=("ev-source",),
        source_session_id="session-1",
    )

    assert proposal.status is MemoryProposalStatus.PROPOSED
    approved = proposal.review(approved=True, reviewer="operator@example.com")
    assert approved.status is MemoryProposalStatus.APPROVED
    assert approved.reviewer == "operator@example.com"
    with pytest.raises(ValueError, match="terminal"):
        approved.review(approved=False, reviewer="other@example.com")


def test_memory_proposal_cannot_persist_hidden_reasoning() -> None:
    with pytest.raises(ValueError, match="hidden reasoning"):
        MemoryProposal.create(
            proposal_id="memory-2",
            subject="private chain of thought",
            content="hidden_reasoning: internal model deliberation",
            evidence_ids=("ev-1",),
            source_session_id="session-1",
        )


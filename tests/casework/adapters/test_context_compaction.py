from __future__ import annotations

from datetime import datetime, timezone

import pytest

from zd_agent.casework.adapters.context_compaction import (
    ActiveContextDecision,
    ActiveContextPolicy,
    CheckpointBody,
    CheckpointClaim,
    InvestigationCheckpoint,
    rebuild_messages_from_checkpoint,
    validate_checkpoint_evidence,
)
from zd_agent.casework.domain.enums import (
    ClaimRole,
    ClaimStatus,
    EvidenceAuthority,
    EvidenceResultStatus,
)
from zd_agent.casework.domain.models import EvidenceItem


NOW = datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc)


def _evidence(evidence_id: str = "ev-current") -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        call_id=evidence_id,
        source="signoz_query",
        authority=EvidenceAuthority.CURRENT_AUTHORITATIVE,
        result_status=EvidenceResultStatus.SUCCESS,
        observed_at=NOW,
        supports_roles=(ClaimRole.CURRENT_STATE,),
        payload="current state",
    )


def _checkpoint() -> InvestigationCheckpoint:
    return InvestigationCheckpoint(
        ticket_id=4001,
        generation=2,
        latest_customer_event_id="event-2",
        active_question="What is the current state?",
        created_at=NOW,
        body=CheckpointBody(
            proposed_claims=(
                CheckpointClaim(
                    statement="The run is currently waiting.",
                    role=ClaimRole.CURRENT_STATE,
                    status=ClaimStatus.CONFIRMED,
                    supporting_evidence_ids=("ev-current",),
                ),
            ),
            completed_checks=("Read current runtime state.",),
            next_actions=("Check the worker boundary.",),
        ),
    )


def test_active_context_policy_has_separate_soft_and_hard_limits() -> None:
    policy = ActiveContextPolicy(
        context_window_tokens=100_000,
        soft_ratio=0.70,
        hard_ratio=0.88,
    )

    assert policy.decision(69_999) is ActiveContextDecision.CONTINUE
    assert policy.decision(70_000) is ActiveContextDecision.COMPACT
    assert policy.decision(88_000) is ActiveContextDecision.HARD_LIMIT


def test_checkpoint_rejects_unknown_evidence_references() -> None:
    checkpoint = _checkpoint().model_copy(
        update={
            "body": _checkpoint().body.model_copy(
                update={
                    "proposed_claims": (
                        CheckpointClaim(
                            statement="Unsupported current state.",
                            role=ClaimRole.CURRENT_STATE,
                            status=ClaimStatus.CONFIRMED,
                            supporting_evidence_ids=("ev-missing",),
                        ),
                    ),
                },
            ),
        },
    )

    with pytest.raises(ValueError, match="unknown evidence ID"):
        validate_checkpoint_evidence(checkpoint, (_evidence(),))


def test_checkpoint_rejects_role_mismatched_evidence() -> None:
    checkpoint = _checkpoint().model_copy(
        update={
            "body": _checkpoint().body.model_copy(
                update={
                    "proposed_claims": (
                        CheckpointClaim(
                            statement="The root cause is confirmed.",
                            role=ClaimRole.ROOT_CAUSE,
                            status=ClaimStatus.CONFIRMED,
                            supporting_evidence_ids=("ev-current",),
                        ),
                    ),
                },
            ),
        },
    )

    with pytest.raises(ValueError, match="role-compatible"):
        validate_checkpoint_evidence(checkpoint, (_evidence(),))


def test_checkpoint_rejects_contradiction_without_role_matched_evidence() -> None:
    checkpoint = _checkpoint().model_copy(
        update={
            "body": _checkpoint().body.model_copy(
                update={
                    "proposed_claims": (
                        CheckpointClaim(
                            statement="The root cause hypothesis was disproved.",
                            role=ClaimRole.ROOT_CAUSE,
                            status=ClaimStatus.CONTRADICTED,
                            contradicting_evidence_ids=("ev-current",),
                        ),
                    ),
                },
            ),
        },
    )

    with pytest.raises(ValueError, match="role-compatible"):
        validate_checkpoint_evidence(checkpoint, (_evidence(),))


def test_rebuild_keeps_canonical_prefix_checkpoint_and_recent_tool_round() -> None:
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "case context"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "old", "type": "function"}],
        },
        {"role": "tool", "tool_call_id": "old", "content": "old evidence"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "recent", "type": "function"}],
        },
        {
            "role": "tool",
            "tool_call_id": "recent",
            "content": "recent evidence",
        },
    ]

    rebuilt = rebuild_messages_from_checkpoint(
        messages=messages,
        checkpoint=_checkpoint(),
        retain_recent_rounds=1,
    )

    assert [message["role"] for message in rebuilt] == [
        "system",
        "user",
        "user",
        "assistant",
        "tool",
    ]
    assert "INVESTIGATION_CHECKPOINT" in str(rebuilt[2]["content"])
    assert rebuilt[-1]["content"] == "recent evidence"
    assert "old evidence" not in str(rebuilt)

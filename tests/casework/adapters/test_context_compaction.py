from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from typing import Any
from unittest.mock import patch

import pytest
import tiktoken

from zd_agent.casework.adapters.context_compaction import (
    ActiveContextDecision,
    ActiveContextPolicy,
    CheckpointBody,
    CheckpointClaim,
    ContextCompactionError,
    InvestigationCheckpoint,
    create_structured_checkpoint,
    estimate_active_context_tokens,
    rebuild_messages_from_checkpoint,
    validate_checkpoint_evidence,
    _context_tokenizer,
)
from zd_agent.casework.domain.enums import (
    ClaimRole,
    ClaimStatus,
    EvidenceAuthority,
    EvidenceResultStatus,
    ResolutionStatus,
)
from zd_agent.casework.domain.models import (
    CaseContext,
    EvidenceItem,
    FormIdentity,
    IdentityResolution,
    TicketSnapshot,
)
from zd_agent.runtime.llm.types import LLMProvider, LLMResponse, ToolSchema


NOW = datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc)


class _CheckpointProvider(LLMProvider):
    def __init__(self, content: str) -> None:
        self.content = content

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        _ = (messages, tools, temperature)
        return LLMResponse(
            content=self.content,
            tool_calls=[],
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
        )

    def format_tool_result(
        self,
        tool_call_id: str,
        name: str,
        content: str,
    ) -> dict[str, Any]:
        _ = (tool_call_id, name, content)
        raise NotImplementedError


def _case_context() -> CaseContext:
    return CaseContext(
        ticket=TicketSnapshot(
            ticket_id=4001,
            generation=2,
            status="open",
            latest_event_id="event-2",
            latest_event_at=NOW,
            questions=("What is the current state?",),
            form_identity=FormIdentity(email="owner@example.test"),
        ),
        built_at=NOW,
        identity=IdentityResolution(
            contact_email="owner@example.test",
            target_identity="owner@example.test",
            status=ResolutionStatus.NO_MEMBERSHIP,
        ),
    )


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


@pytest.mark.parametrize("content", ["中" * 2_000, "😀" * 2_000])
def test_active_context_estimate_is_conservative_for_multilingual_text(
    content: str,
) -> None:
    messages = [{"role": "user", "content": content}]
    estimated = estimate_active_context_tokens(
        messages,
        tools=None,
    )
    serialized = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    tokenizer_tokens = len(
        tiktoken.get_encoding("cl100k_base").encode_ordinary(serialized)
    )

    assert estimated >= tokenizer_tokens + 512


def test_active_context_estimate_does_not_reject_compressible_ascii_early() -> None:
    content = "a" * 239_000
    estimated = estimate_active_context_tokens(
        [{"role": "user", "content": content}],
        tools=None,
    )
    policy = ActiveContextPolicy(context_window_tokens=272_000)

    assert policy.decision(estimated) is not ActiveContextDecision.HARD_LIMIT


def test_active_context_estimate_has_an_offline_tokenizer_fallback() -> None:
    _context_tokenizer.cache_clear()
    try:
        with patch(
            "zd_agent.casework.adapters.context_compaction.tiktoken.get_encoding",
            side_effect=OSError("offline"),
        ):
            ascii_estimate = estimate_active_context_tokens(
                [{"role": "user", "content": "a" * 239_000}],
                tools=None,
            )
            cjk_estimate = estimate_active_context_tokens(
                [{"role": "user", "content": "中" * 2_000}],
                tools=None,
            )
    finally:
        _context_tokenizer.cache_clear()

    policy = ActiveContextPolicy(context_window_tokens=272_000)
    assert policy.decision(ascii_estimate) is not ActiveContextDecision.HARD_LIMIT
    assert cjk_estimate >= len(("中" * 2_000).encode("utf-8"))


def test_checkpoint_budget_is_conservative_for_multilingual_text() -> None:
    provider = _CheckpointProvider(
        json.dumps({"unknowns": ["中" * 1_000]}, ensure_ascii=False),
    )

    with pytest.raises(ContextCompactionError, match="maximum token budget"):
        asyncio.run(
            create_structured_checkpoint(
                provider=provider,
                context=_case_context(),
                evidence=(),
                messages=[],
                created_at=NOW,
                max_checkpoint_tokens=600,
                on_response=lambda response: None,
            ),
        )


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

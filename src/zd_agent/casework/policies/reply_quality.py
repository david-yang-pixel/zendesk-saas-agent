from __future__ import annotations

import re

from zd_agent.casework.domain.enums import OwnershipAction, ReplyKind
from zd_agent.casework.domain.models import (
    CaseAssessment,
    DispositionDecision,
    ReplyCandidate,
    ReplyQualityResult,
)


_INTERNAL_EVIDENCE_RE = re.compile(
    r"\b(?:tenant_id|source_call_ids|sql_query|inspector_exec|signoz|mcp__|"
    r"evidence ledger)\b|/opt/zd-agent/|src/zd_agent/",
    re.IGNORECASE,
)
_HUMAN_FOLLOWUP_RE = re.compile(
    r"\b(?:human|support engineer|billing team|engineering team|SRE team)\b"
    r".{0,50}\b(?:follow up|take over|review|reply)\b",
    re.IGNORECASE,
)
_WORKSPACE_ID_REQUEST_RE = re.compile(
    r"\b(?:please|could you|can you|provide|share|send|confirm)\b"
    r".{0,45}\bworkspace\s+(?:id|name)\b",
    re.IGNORECASE,
)


def _claim_reasons(
    candidate: ReplyCandidate,
    assessment: CaseAssessment,
) -> tuple[str, ...]:
    admitted_ids = {claim.claim_id for claim in assessment.admitted_claims}
    used_ids = set(candidate.used_claim_ids)
    reasons: list[str] = []
    if unknown_ids := used_ids - admitted_ids:
        reasons.append(f"reply uses unknown claim IDs: {', '.join(sorted(unknown_ids))}")
    required_ids = {
        claim.claim_id
        for claim in assessment.admitted_claims
        if claim.material
    }
    if missing_ids := required_ids - used_ids:
        reasons.append(
            "reply omits required material claims: " + ", ".join(sorted(missing_ids)),
        )
    return tuple(reasons)


def _customer_field_reasons(
    candidate: ReplyCandidate,
    decision: DispositionDecision,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if set(candidate.requested_customer_fields) != set(decision.required_customer_fields):
        reasons.append("reply customer fields do not match the disposition decision")
    if candidate.reply_kind is not ReplyKind.ASK and candidate.requested_customer_fields:
        reasons.append("non-ask reply cannot request customer fields")
    if (
        _WORKSPACE_ID_REQUEST_RE.search(candidate.body)
        and not {"workspace_id", "workspace_name"}
        & set(decision.required_customer_fields)
    ):
        reasons.append("reply introduces an unapproved workspace identity request")
    return tuple(reasons)


def _safety_reasons(
    candidate: ReplyCandidate,
    decision: DispositionDecision,
) -> tuple[str, ...]:
    reasons: list[str] = []
    body_promises_human = bool(_HUMAN_FOLLOWUP_RE.search(candidate.body))
    if decision.ownership_action is OwnershipAction.NONE and (
        candidate.mentions_human_followup or body_promises_human
    ):
        reasons.append("reply promises human follow-up without durable ownership")
    if _INTERNAL_EVIDENCE_RE.search(candidate.body):
        reasons.append("reply exposes internal evidence or implementation details")
    return tuple(reasons)


def evaluate_reply_quality(
    *,
    candidate: ReplyCandidate,
    assessment: CaseAssessment,
    decision: DispositionDecision,
) -> ReplyQualityResult:
    reasons: list[str] = []
    if candidate.reply_kind is not decision.reply_kind:
        reasons.append("reply kind does not match the disposition decision")
    reasons.extend(_claim_reasons(candidate, assessment))
    reasons.extend(_customer_field_reasons(candidate, decision))
    reasons.extend(_safety_reasons(candidate, decision))
    return ReplyQualityResult(
        accepted=not reasons,
        reasons=tuple(reasons),
    )

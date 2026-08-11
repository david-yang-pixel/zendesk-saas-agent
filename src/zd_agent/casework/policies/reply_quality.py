from __future__ import annotations

import re
import unicodedata

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
    r"(?:\b(?:human|person|representative|support (?:engineer|specialist)|"
    r"billing (?:team|specialist)|engineering team|SRE team|site reliability team|"
    r"security team|our team|someone|colleague)\b.{0,70}\b"
    r"(?:follow up|take over|review|reply|contact|continue)\b|"
    r"\bwe\s+will\s+have\s+someone\s+(?:contact|reply\s+to)\b|"
    r"\b(?:escalated|forwarded|assigned)\b.{0,55}\b(?:human|person|"
    r"representative|specialist|engineers?|support|billing|engineering|sre|"
    r"site reliability|security|team|colleague)\b|"
    r"\b(?:engineers?|specialist|team|colleague)\b.{0,50}\b(?:are\s+)?"
    r"(?:looking into|assigned|reach out|get back|follow up|take over)\b|"
    r"\b(?:case|ticket)\b.{0,30}\b(?:is\s+now\s+with|has\s+been\s+assigned\s+to)\b"
    r".{0,35}\b(?:billing|engineering|support|security|sre|team|specialist)\b|"
    r"\bwe(?:'ll|\s+will)\s+(?:get back|reach out|follow up)\b|"
    r"\bexpect\b.{0,35}\b(?:response|reply)\b.{0,35}\b(?:team|specialist|"
    r"engineer|support)\b)",
    re.IGNORECASE,
)
_WORKSPACE_IDENTITY_REQUEST_RE = re.compile(
    r"(?:"
    r"\b(?:what\s+is(?:\s+your)?|which)\b.{0,35}\bworkspace\b|"
    r"\b(?:tell\s+(?:me|us)|let\s+us\s+know|provide|share|send|confirm|"
    r"name|identify)\b.{0,45}\b(?:workspace|account)\b|"
    r"\b(?:i|we)\s+need(?:\s+you\s+to)?\b.{0,45}\b(?:identify|name|"
    r"provide|share)\b.{0,30}\b(?:workspace|account)\b|"
    r"\b(?:workspace|account)\b.{0,45}\b(?:id|name|url|identity|"
    r"identified)\b"
    r")",
    re.IGNORECASE,
)


def public_reply_language_blockers(body: str) -> tuple[str, ...]:
    """Reject alphabetic scripts that cannot be part of an English reply."""
    for character in body:
        if character.isalpha() and "LATIN" not in unicodedata.name(character, ""):
            return ("public reply contains non-English alphabetic text",)
    return ()


def _claim_reasons(
    candidate: ReplyCandidate,
    assessment: CaseAssessment,
) -> tuple[str, ...]:
    admitted_ids = {claim.claim_id for claim in assessment.admitted_claims}
    used_ids = set(candidate.used_claim_ids)
    reasons: list[str] = []
    if unknown_ids := used_ids - admitted_ids:
        reasons.append(
            f"reply uses unknown claim IDs: {', '.join(sorted(unknown_ids))}"
        )
    required_ids = {
        claim.claim_id for claim in assessment.admitted_claims if claim.material
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
    if set(candidate.requested_customer_fields) != set(
        decision.required_customer_fields
    ):
        reasons.append("reply customer fields do not match the disposition decision")
    if (
        candidate.reply_kind is not ReplyKind.ASK
        and candidate.requested_customer_fields
    ):
        reasons.append("non-ask reply cannot request customer fields")
    if _WORKSPACE_IDENTITY_REQUEST_RE.search(candidate.body) and not {
        "target_workspace_identity",
        "target_workspace_choice",
        "workspace_id",
        "workspace_name",
    } & set(decision.required_customer_fields):
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
    reasons.extend(public_reply_language_blockers(candidate.body))
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

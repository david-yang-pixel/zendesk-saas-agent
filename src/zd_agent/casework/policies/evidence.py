from __future__ import annotations

from datetime import datetime

from zd_agent.casework.domain.enums import (
    ClaimRejectionCode,
    ClaimRole,
    ClaimStatus,
    EvidenceAuthority,
    EvidenceResultStatus,
)
from zd_agent.casework.domain.models import Claim, ClaimAdmission, EvidenceItem


def _reject(
    claim: Claim,
    code: ClaimRejectionCode,
    detail: str,
) -> ClaimAdmission:
    return ClaimAdmission(
        claim_id=claim.claim_id,
        accepted=False,
        rejection_code=code,
        detail=detail,
    )


def _claim_precheck(claim: Claim) -> ClaimAdmission | None:
    checks = (
        (
            not claim.public_allowed,
            ClaimRejectionCode.NOT_PUBLIC,
            "claim is internal-only",
        ),
        (
            claim.status is ClaimStatus.CONTRADICTED,
            ClaimRejectionCode.CONTRADICTED,
            "claim is contradicted by current evidence",
        ),
        (
            claim.status is ClaimStatus.UNKNOWN,
            ClaimRejectionCode.UNSUPPORTED_STATUS,
            "unknown claim cannot be stated as a customer fact",
        ),
        (
            claim.material and not claim.supporting_evidence_ids,
            ClaimRejectionCode.MISSING_EVIDENCE,
            "material claim has no supporting evidence",
        ),
    )
    return next(
        (_reject(claim, code, detail) for failed, code, detail in checks if failed),
        None,
    )


def _resolve_supporting_evidence(
    claim: Claim,
    evidence: tuple[EvidenceItem, ...],
) -> tuple[tuple[EvidenceItem, ...], ClaimAdmission | None]:
    evidence_by_id = {item.evidence_id: item for item in evidence}
    missing = tuple(
        evidence_id
        for evidence_id in claim.supporting_evidence_ids
        if evidence_id not in evidence_by_id
    )
    if missing:
        return (), _reject(
            claim,
            ClaimRejectionCode.UNKNOWN_EVIDENCE,
            f"unknown evidence IDs: {', '.join(missing)}",
        )
    return (
        tuple(evidence_by_id[item] for item in claim.supporting_evidence_ids),
        None,
    )


def _evidence_eligibility_error(
    supporting: tuple[EvidenceItem, ...],
    *,
    as_of: datetime,
) -> tuple[ClaimRejectionCode, str] | None:
    if any(item.result_status is not EvidenceResultStatus.SUCCESS for item in supporting):
        return (
            ClaimRejectionCode.FAILED_EVIDENCE,
            "failed or partial evidence cannot prove a material claim",
        )
    if any(item.authority is EvidenceAuthority.HISTORICAL_GUIDANCE for item in supporting):
        return (
            ClaimRejectionCode.HISTORICAL_EVIDENCE,
            "historical guidance cannot prove a current claim",
        )
    if any(item.fresh_until is not None and item.fresh_until < as_of for item in supporting):
        return (
            ClaimRejectionCode.STALE_EVIDENCE,
            "supporting evidence expired before claim admission",
        )
    return None


def _target_coverage_error(
    claim: Claim,
    supporting: tuple[EvidenceItem, ...],
) -> tuple[ClaimRejectionCode, str] | None:
    covered = {(target.kind, target.value) for item in supporting for target in item.targets}
    required = {(target.kind, target.value) for target in claim.targets}
    if required.issubset(covered):
        return None
    return (
        ClaimRejectionCode.TARGET_MISMATCH,
        "supporting evidence does not cover the claim target",
    )


def _role_coverage_error(
    claim: Claim,
    supporting: tuple[EvidenceItem, ...],
) -> tuple[ClaimRejectionCode, str] | None:
    supported = {role for item in supporting for role in item.supports_roles}
    if claim.role is ClaimRole.ROOT_CAUSE:
        required = {ClaimRole.FAILING_BOUNDARY, ClaimRole.ROOT_CAUSE}
        if not required.issubset(supported):
            return (
                ClaimRejectionCode.ROOT_CAUSE_INCOMPLETE,
                "root cause requires both failing-boundary and mechanism evidence",
            )
    elif claim.role not in supported:
        return (
            ClaimRejectionCode.ROLE_MISMATCH,
            "supporting evidence does not cover the claim role",
        )
    return None


def admit_claim(
    *,
    claim: Claim,
    evidence: tuple[EvidenceItem, ...],
    as_of: datetime,
) -> ClaimAdmission:
    """Admit one customer-visible claim only from current eligible evidence."""
    if as_of.tzinfo is None:
        raise ValueError("claim admission time must be timezone-aware")
    if rejection := _claim_precheck(claim):
        return rejection
    supporting, rejection = _resolve_supporting_evidence(claim, evidence)
    if rejection is not None:
        return rejection
    checks = (
        _evidence_eligibility_error(supporting, as_of=as_of),
        _target_coverage_error(claim, supporting),
        _role_coverage_error(claim, supporting),
    )
    if error := next((value for value in checks if value is not None), None):
        return _reject(claim, *error)
    return ClaimAdmission(
        claim_id=claim.claim_id,
        accepted=True,
        eligible_evidence_ids=claim.supporting_evidence_ids,
    )

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


def admit_claim(
    *,
    claim: Claim,
    evidence: tuple[EvidenceItem, ...],
    as_of: datetime,
) -> ClaimAdmission:
    """Admit one customer-visible claim only from current eligible evidence."""
    if as_of.tzinfo is None:
        raise ValueError("claim admission time must be timezone-aware")
    if not claim.public_allowed:
        return _reject(claim, ClaimRejectionCode.NOT_PUBLIC, "claim is internal-only")
    if claim.status is ClaimStatus.CONTRADICTED:
        return _reject(
            claim,
            ClaimRejectionCode.CONTRADICTED,
            "claim is contradicted by current evidence",
        )
    if claim.status is ClaimStatus.UNKNOWN:
        return _reject(
            claim,
            ClaimRejectionCode.UNSUPPORTED_STATUS,
            "unknown claim cannot be stated as a customer fact",
        )
    if claim.material and not claim.supporting_evidence_ids:
        return _reject(
            claim,
            ClaimRejectionCode.MISSING_EVIDENCE,
            "material claim has no supporting evidence",
        )

    evidence_by_id = {item.evidence_id: item for item in evidence}
    missing = tuple(
        evidence_id
        for evidence_id in claim.supporting_evidence_ids
        if evidence_id not in evidence_by_id
    )
    if missing:
        return _reject(
            claim,
            ClaimRejectionCode.UNKNOWN_EVIDENCE,
            f"unknown evidence IDs: {', '.join(missing)}",
        )
    supporting = tuple(
        evidence_by_id[evidence_id]
        for evidence_id in claim.supporting_evidence_ids
    )
    if any(item.result_status is not EvidenceResultStatus.SUCCESS for item in supporting):
        return _reject(
            claim,
            ClaimRejectionCode.FAILED_EVIDENCE,
            "failed or partial evidence cannot prove a material claim",
        )
    if any(item.authority is EvidenceAuthority.HISTORICAL_GUIDANCE for item in supporting):
        return _reject(
            claim,
            ClaimRejectionCode.HISTORICAL_EVIDENCE,
            "historical guidance cannot prove a current claim",
        )
    if any(item.fresh_until is not None and item.fresh_until < as_of for item in supporting):
        return _reject(
            claim,
            ClaimRejectionCode.STALE_EVIDENCE,
            "supporting evidence expired before claim admission",
        )

    covered_targets = {
        (target.kind, target.value)
        for item in supporting
        for target in item.targets
    }
    required_targets = {(target.kind, target.value) for target in claim.targets}
    if not required_targets.issubset(covered_targets):
        return _reject(
            claim,
            ClaimRejectionCode.TARGET_MISMATCH,
            "supporting evidence does not cover the claim target",
        )

    supported_roles = {
        role
        for item in supporting
        for role in item.supports_roles
    }
    if claim.role is ClaimRole.ROOT_CAUSE:
        required_roles = {ClaimRole.FAILING_BOUNDARY, ClaimRole.ROOT_CAUSE}
        if not required_roles.issubset(supported_roles):
            return _reject(
                claim,
                ClaimRejectionCode.ROOT_CAUSE_INCOMPLETE,
                "root cause requires both failing-boundary and mechanism evidence",
            )
    elif claim.role not in supported_roles:
        return _reject(
            claim,
            ClaimRejectionCode.ROLE_MISMATCH,
            "supporting evidence does not cover the claim role",
        )
    return ClaimAdmission(
        claim_id=claim.claim_id,
        accepted=True,
        eligible_evidence_ids=claim.supporting_evidence_ids,
    )

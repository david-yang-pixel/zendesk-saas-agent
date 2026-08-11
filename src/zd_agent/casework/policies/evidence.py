from __future__ import annotations

from datetime import datetime

from zd_agent.casework.domain.enums import (
    ClaimRejectionCode,
    ClaimRole,
    ClaimStatus,
    EvidenceAuthority,
    EvidenceResultStatus,
)
from zd_agent.casework.domain.models import (
    Claim,
    ClaimAdmission,
    EvidenceItem,
    TargetReference,
)


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
    if any(
        item.result_status is not EvidenceResultStatus.SUCCESS for item in supporting
    ):
        return (
            ClaimRejectionCode.FAILED_EVIDENCE,
            "failed or partial evidence cannot prove a material claim",
        )
    if any(
        item.authority is EvidenceAuthority.HISTORICAL_GUIDANCE for item in supporting
    ):
        return (
            ClaimRejectionCode.HISTORICAL_EVIDENCE,
            "historical guidance cannot prove a current claim",
        )
    if any(
        item.fresh_until is not None and item.fresh_until < as_of for item in supporting
    ):
        return (
            ClaimRejectionCode.STALE_EVIDENCE,
            "supporting evidence expired before claim admission",
        )
    return None


def _target_coverage_error(
    claim: Claim,
    supporting: tuple[EvidenceItem, ...],
) -> tuple[ClaimRejectionCode, str] | None:
    covered = {
        (target.kind, target.value) for item in supporting for target in item.targets
    }
    required = {(target.kind, target.value) for target in claim.targets}
    if required.issubset(covered):
        return None
    return (
        ClaimRejectionCode.TARGET_MISMATCH,
        "supporting evidence does not cover the claim target",
    )


def _target_pairs(targets: tuple[TargetReference, ...]) -> set[tuple[str, str]]:
    return {(target.kind, target.value) for target in targets}


def _item_covers_claim(item: EvidenceItem, claim: Claim) -> bool:
    required = {(target.kind, target.value) for target in claim.targets}
    covered = {(target.kind, target.value) for target in item.targets}
    return required.issubset(covered)


def _required_target_error(
    claim: Claim,
    required_targets: tuple[TargetReference, ...],
) -> tuple[ClaimRejectionCode, str] | None:
    if _target_pairs(required_targets).issubset(_target_pairs(claim.targets)):
        return None
    return (
        ClaimRejectionCode.TARGET_MISMATCH,
        "claim is not bound to the resolved case target",
    )


def _required_evidence_error(
    claim: Claim,
    required_groups: tuple[frozenset[str], ...],
) -> tuple[ClaimRejectionCode, str] | None:
    cited = set(claim.supporting_evidence_ids)
    if all(cited & group for group in required_groups):
        return None
    return (
        ClaimRejectionCode.MISSING_EVIDENCE,
        "claim omits a required case-context evidence class",
    )


_FACTUAL_AUTHORITIES = {
    EvidenceAuthority.CURRENT_AUTHORITATIVE,
    EvidenceAuthority.CURRENT_SUPPORTING,
}


def _role_coverage_error(
    claim: Claim,
    supporting: tuple[EvidenceItem, ...],
) -> tuple[ClaimRejectionCode, str] | None:
    if claim.role is ClaimRole.ROOT_CAUSE:
        boundary_covered = any(
            ClaimRole.FAILING_BOUNDARY in item.supports_roles
            and item.authority in _FACTUAL_AUTHORITIES
            and _item_covers_claim(item, claim)
            for item in supporting
        )
        mechanism_covered = any(
            ClaimRole.ROOT_CAUSE in item.supports_roles
            and item.authority in _FACTUAL_AUTHORITIES
            and (not item.targets or _item_covers_claim(item, claim))
            for item in supporting
        )
        if not boundary_covered or not mechanism_covered:
            return (
                ClaimRejectionCode.ROOT_CAUSE_INCOMPLETE,
                "root cause requires a target-bound failing boundary and an aligned "
                "mechanism",
            )
    elif not any(
        claim.role in item.supports_roles
        and item.authority in _FACTUAL_AUTHORITIES
        and _item_covers_claim(item, claim)
        for item in supporting
    ):
        return (
            ClaimRejectionCode.ROLE_MISMATCH,
            "one evidence item must cover both the claim role and target",
        )
    return None


_AUTHORITATIVE_ROLES = {
    ClaimRole.CURRENT_STATE,
    ClaimRole.FAILING_BOUNDARY,
    ClaimRole.IMPACT,
    ClaimRole.RECOVERY,
}


def _authority_error(
    claim: Claim,
    supporting: tuple[EvidenceItem, ...],
) -> tuple[ClaimRejectionCode, str] | None:
    if claim.status is not ClaimStatus.CONFIRMED:
        return None
    role = (
        ClaimRole.FAILING_BOUNDARY if claim.role is ClaimRole.ROOT_CAUSE else claim.role
    )
    if role not in _AUTHORITATIVE_ROLES:
        return None
    authoritative = any(
        item.authority is EvidenceAuthority.CURRENT_AUTHORITATIVE
        and role in item.supports_roles
        and _item_covers_claim(item, claim)
        for item in supporting
    )
    if authoritative:
        return None
    return (
        ClaimRejectionCode.INSUFFICIENT_AUTHORITY,
        "confirmed current-state claims require authoritative current evidence",
    )


def _contradiction_error(
    claim: Claim,
    evidence: tuple[EvidenceItem, ...],
    *,
    as_of: datetime,
) -> tuple[ClaimRejectionCode, str] | None:
    if not claim.contradicting_evidence_ids:
        return None
    by_id = {item.evidence_id: item for item in evidence}
    if any(value not in by_id for value in claim.contradicting_evidence_ids):
        return (
            ClaimRejectionCode.UNKNOWN_EVIDENCE,
            "claim cites unknown contradicting evidence",
        )
    current = tuple(by_id[value] for value in claim.contradicting_evidence_ids)
    eligible = any(
        item.result_status is EvidenceResultStatus.SUCCESS
        and item.authority is not EvidenceAuthority.HISTORICAL_GUIDANCE
        and (item.fresh_until is None or item.fresh_until >= as_of)
        for item in current
    )
    if not eligible:
        return None
    return (
        ClaimRejectionCode.CONTRADICTED,
        "current eligible evidence contradicts the claim",
    )


def admit_claim(
    *,
    claim: Claim,
    evidence: tuple[EvidenceItem, ...],
    as_of: datetime,
    required_targets: tuple[TargetReference, ...] = (),
    required_evidence_groups: tuple[frozenset[str], ...] = (),
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
        _contradiction_error(claim, evidence, as_of=as_of),
        _evidence_eligibility_error(supporting, as_of=as_of),
        _required_target_error(claim, required_targets),
        _required_evidence_error(claim, required_evidence_groups),
        _target_coverage_error(claim, supporting),
        _role_coverage_error(claim, supporting),
        _authority_error(claim, supporting),
    )
    if error := next((value for value in checks if value is not None), None):
        return _reject(claim, *error)
    return ClaimAdmission(
        claim_id=claim.claim_id,
        accepted=True,
        eligible_evidence_ids=claim.supporting_evidence_ids,
    )

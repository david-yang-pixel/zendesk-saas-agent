from datetime import datetime, timedelta, timezone

from zd_agent.casework.domain.enums import (
    ClaimRejectionCode,
    ClaimRole,
    ClaimStatus,
    EvidenceAuthority,
    EvidenceResultStatus,
)
from zd_agent.casework.domain.models import Claim, EvidenceItem, TargetReference
from zd_agent.casework.policies.evidence import admit_claim


NOW = datetime(2026, 8, 11, 4, 0, tzinfo=timezone.utc)
TENANT = TargetReference(kind="tenant_id", value="tenant-1")


def _evidence(
    evidence_id: str,
    *,
    authority: EvidenceAuthority = EvidenceAuthority.CURRENT_AUTHORITATIVE,
    result_status: EvidenceResultStatus = EvidenceResultStatus.SUCCESS,
    targets: tuple[TargetReference, ...] = (TENANT,),
    supports_roles: tuple[ClaimRole, ...] = (ClaimRole.CURRENT_STATE,),
    fresh_until: datetime | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        call_id=f"call-{evidence_id}",
        source="test-reader",
        authority=authority,
        result_status=result_status,
        targets=targets,
        observed_at=NOW,
        fresh_until=fresh_until,
        supports_roles=supports_roles,
        payload={"value": evidence_id},
    )


def _claim(
    *,
    role: ClaimRole = ClaimRole.CURRENT_STATE,
    status: ClaimStatus = ClaimStatus.CONFIRMED,
    supporting: tuple[str, ...] = ("ev-current",),
    contradicting: tuple[str, ...] = (),
    targets: tuple[TargetReference, ...] = (TENANT,),
) -> Claim:
    return Claim(
        claim_id="claim-1",
        statement="The workspace has an active Professional subscription.",
        role=role,
        status=status,
        supporting_evidence_ids=supporting,
        contradicting_evidence_ids=contradicting,
        targets=targets,
        public_allowed=True,
    )


def test_current_successful_target_bound_evidence_is_admitted() -> None:
    result = admit_claim(
        claim=_claim(),
        evidence=(_evidence("ev-current"),),
        as_of=NOW,
    )

    assert result.accepted is True
    assert result.eligible_evidence_ids == ("ev-current",)
    assert result.rejection_code is ClaimRejectionCode.NONE


def test_historical_guidance_cannot_prove_current_material_claim() -> None:
    result = admit_claim(
        claim=_claim(supporting=("ev-history",)),
        evidence=(
            _evidence(
                "ev-history",
                authority=EvidenceAuthority.HISTORICAL_GUIDANCE,
            ),
        ),
        as_of=NOW,
    )

    assert result.accepted is False
    assert result.rejection_code is ClaimRejectionCode.HISTORICAL_EVIDENCE


def test_failed_or_stale_evidence_is_rejected() -> None:
    failed = admit_claim(
        claim=_claim(supporting=("ev-failed",)),
        evidence=(
            _evidence("ev-failed", result_status=EvidenceResultStatus.FAILURE),
        ),
        as_of=NOW,
    )
    stale = admit_claim(
        claim=_claim(supporting=("ev-stale",)),
        evidence=(
            _evidence("ev-stale", fresh_until=NOW - timedelta(seconds=1)),
        ),
        as_of=NOW,
    )

    assert failed.rejection_code is ClaimRejectionCode.FAILED_EVIDENCE
    assert stale.rejection_code is ClaimRejectionCode.STALE_EVIDENCE


def test_claim_target_must_be_covered_by_supporting_evidence() -> None:
    result = admit_claim(
        claim=_claim(),
        evidence=(
            _evidence(
                "ev-current",
                targets=(TargetReference(kind="tenant_id", value="tenant-2"),),
            ),
        ),
        as_of=NOW,
    )

    assert result.rejection_code is ClaimRejectionCode.TARGET_MISMATCH


def test_confirmed_root_cause_requires_boundary_and_mechanism_evidence() -> None:
    incomplete = admit_claim(
        claim=_claim(
            role=ClaimRole.ROOT_CAUSE,
            supporting=("ev-mechanism",),
        ),
        evidence=(
            _evidence(
                "ev-mechanism",
                supports_roles=(ClaimRole.ROOT_CAUSE,),
            ),
        ),
        as_of=NOW,
    )

    complete = admit_claim(
        claim=_claim(
            role=ClaimRole.ROOT_CAUSE,
            supporting=("ev-boundary", "ev-mechanism"),
        ),
        evidence=(
            _evidence(
                "ev-boundary",
                supports_roles=(ClaimRole.FAILING_BOUNDARY,),
            ),
            _evidence(
                "ev-mechanism",
                supports_roles=(ClaimRole.ROOT_CAUSE,),
            ),
        ),
        as_of=NOW,
    )

    assert incomplete.rejection_code is ClaimRejectionCode.ROOT_CAUSE_INCOMPLETE
    assert complete.accepted is True


def test_contradicted_claim_is_never_customer_visible() -> None:
    result = admit_claim(
        claim=_claim(
            status=ClaimStatus.CONTRADICTED,
            contradicting=("ev-current",),
        ),
        evidence=(_evidence("ev-current"),),
        as_of=NOW,
    )

    assert result.rejection_code is ClaimRejectionCode.CONTRADICTED

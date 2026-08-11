from datetime import datetime, timedelta, timezone

from zd_agent.casework.adapters.evidence import tool_evidence
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
        evidence=(_evidence("ev-failed", result_status=EvidenceResultStatus.FAILURE),),
        as_of=NOW,
    )
    stale = admit_claim(
        claim=_claim(supporting=("ev-stale",)),
        evidence=(_evidence("ev-stale", fresh_until=NOW - timedelta(seconds=1)),),
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


def test_tenant_runtime_boundary_and_global_source_can_prove_root_cause() -> None:
    boundary = tool_evidence(
        call_id="ev-boundary",
        tool_name="signoz_search_logs",
        args={"tenant": TENANT.value, "searchText": "collection missing"},
        allowed_target_arguments=frozenset({"tenant", "searchText"}),
        observed_at=NOW,
        success=True,
        output="The current tenant request fails at vector collection lookup.",
        error="",
    )
    mechanism = tool_evidence(
        call_id="ev-mechanism",
        tool_name="git_show",
        args={"repo": "api", "filepath": "retrieval.py", "ref": "runtime"},
        allowed_target_arguments=frozenset({"repo", "filepath", "ref"}),
        observed_at=NOW,
        success=True,
        output="Aligned source resolves the configured collection before retrieval.",
        error="",
    )

    result = admit_claim(
        claim=_claim(
            role=ClaimRole.ROOT_CAUSE,
            supporting=("ev-boundary", "ev-mechanism"),
        ),
        evidence=(boundary, mechanism),
        as_of=NOW,
    )

    assert boundary.targets == (TENANT,)
    assert mechanism.targets == ()
    assert result.accepted is True


def test_unaligned_source_cannot_supply_global_root_cause_mechanism() -> None:
    boundary = tool_evidence(
        call_id="ev-boundary",
        tool_name="signoz_search_logs",
        args={"tenant": TENANT.value},
        allowed_target_arguments=frozenset({"tenant"}),
        observed_at=NOW,
        success=True,
        output="The current tenant request fails at vector collection lookup.",
        error="",
    )
    unaligned_source = tool_evidence(
        call_id="ev-mechanism",
        tool_name="git_show",
        args={"repo": "api", "filepath": "retrieval.py", "ref": "main"},
        allowed_target_arguments=frozenset({"repo", "filepath", "ref"}),
        observed_at=NOW,
        success=True,
        output="A branch contains a possible collection lookup mechanism.",
        error="",
    )

    result = admit_claim(
        claim=_claim(
            role=ClaimRole.ROOT_CAUSE,
            supporting=("ev-boundary", "ev-mechanism"),
        ),
        evidence=(boundary, unaligned_source),
        as_of=NOW,
    )

    assert ClaimRole.ROOT_CAUSE not in unaligned_source.supports_roles
    assert result.rejection_code is ClaimRejectionCode.ROOT_CAUSE_INCOMPLETE


def test_github_navigation_is_not_an_aligned_root_cause_mechanism() -> None:
    item = tool_evidence(
        call_id="ev-pr",
        tool_name="github_get_pr",
        args={"repo": "api", "number": 123},
        allowed_target_arguments=frozenset({"repo", "number"}),
        observed_at=NOW,
        success=True,
        output="A proposed change discusses a similar mechanism.",
        error="",
    )

    assert ClaimRole.PRODUCT_FACT in item.supports_roles
    assert ClaimRole.ROOT_CAUSE not in item.supports_roles


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


def test_required_case_target_cannot_be_omitted_from_material_claim() -> None:
    result = admit_claim(
        claim=_claim(targets=()),
        evidence=(_evidence("ev-current"),),
        as_of=NOW,
        required_targets=(TENANT,),
    )

    assert result.rejection_code is ClaimRejectionCode.TARGET_MISMATCH


def test_role_evidence_cannot_borrow_target_coverage_from_another_item() -> None:
    other_tenant = TargetReference(kind="tenant_id", value="tenant-2")
    result = admit_claim(
        claim=_claim(supporting=("ev-role", "ev-target")),
        evidence=(
            _evidence("ev-role", targets=(other_tenant,)),
            _evidence(
                "ev-target",
                targets=(TENANT,),
                supports_roles=(ClaimRole.PRODUCT_FACT,),
            ),
        ),
        as_of=NOW,
    )

    assert result.rejection_code is ClaimRejectionCode.ROLE_MISMATCH


def test_current_contradicting_evidence_rejects_otherwise_supported_claim() -> None:
    result = admit_claim(
        claim=_claim(contradicting=("ev-contradiction",)),
        evidence=(
            _evidence("ev-current"),
            _evidence("ev-contradiction"),
        ),
        as_of=NOW,
    )

    assert result.rejection_code is ClaimRejectionCode.CONTRADICTED


def test_confirmed_current_state_requires_authoritative_evidence() -> None:
    result = admit_claim(
        claim=_claim(),
        evidence=(
            _evidence(
                "ev-current",
                authority=EvidenceAuthority.CURRENT_SUPPORTING,
            ),
        ),
        as_of=NOW,
    )

    assert result.rejection_code is ClaimRejectionCode.INSUFFICIENT_AUTHORITY


def test_target_binding_cannot_supply_factual_role_coverage() -> None:
    result = admit_claim(
        claim=_claim(supporting=("ev-identity",)),
        evidence=(
            _evidence(
                "ev-identity",
                authority=EvidenceAuthority("target_binding"),
                supports_roles=(ClaimRole.CURRENT_STATE,),
            ),
        ),
        as_of=NOW,
    )

    assert result.rejection_code is ClaimRejectionCode.ROLE_MISMATCH


def _tool_item(
    tool_name: str,
    args: dict[str, object],
    *,
    allowed: frozenset[str],
) -> EvidenceItem:
    return tool_evidence(
        call_id="ev-tool",
        tool_name=tool_name,
        args=args,
        allowed_target_arguments=allowed,
        observed_at=NOW,
        success=True,
        output="current ticket data",
        error="",
    )


def test_tool_evidence_does_not_inherit_the_session_ticket_target() -> None:
    item = _tool_item(
        "official_docs_search",
        {"query": "ticket behavior"},
        allowed=frozenset({"query"}),
    )

    assert item.targets == ()


def test_zendesk_ticket_evidence_only_covers_its_argument_ticket() -> None:
    current_ticket = TargetReference(kind="ticket_id", value="4001")
    same_ticket = _tool_item(
        "zendesk_get_ticket",
        {"ticket_id": 4001},
        allowed=frozenset({"ticket_id"}),
    )
    other_ticket = _tool_item(
        "zendesk_get_ticket",
        {"ticket_id": 4002},
        allowed=frozenset({"ticket_id"}),
    )
    claim = _claim(targets=(current_ticket,), supporting=("ev-tool",))

    accepted = admit_claim(claim=claim, evidence=(same_ticket,), as_of=NOW)
    rejected = admit_claim(claim=claim, evidence=(other_ticket,), as_of=NOW)

    assert accepted.accepted is True
    assert rejected.rejection_code is ClaimRejectionCode.TARGET_MISMATCH


def test_zendesk_search_history_cannot_support_current_state() -> None:
    history = _tool_item(
        "zendesk_search",
        {"query": "similar incident"},
        allowed=frozenset({"query"}),
    )

    result = admit_claim(
        claim=_claim(targets=(), supporting=("ev-tool",)),
        evidence=(history,),
        as_of=NOW,
    )

    assert history.authority is EvidenceAuthority.HISTORICAL_GUIDANCE
    assert ClaimRole.CURRENT_STATE not in history.supports_roles
    assert result.rejection_code is ClaimRejectionCode.HISTORICAL_EVIDENCE

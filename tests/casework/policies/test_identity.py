from zd_agent.casework.domain.enums import (
    AdapterErrorCode,
    LookupStatus,
    ResolutionMethod,
    ResolutionStatus,
)
from zd_agent.casework.domain.models import (
    WorkspaceCandidate,
    WorkspaceMembershipLookup,
)
from zd_agent.casework.policies.identity import resolve_workspace_identity


TENANT_1 = "11111111-1111-4111-8111-111111111111"
TENANT_2 = "22222222-2222-4222-8222-222222222222"


def _lookup(
    email: str,
    *candidates: WorkspaceCandidate,
    status: LookupStatus = LookupStatus.SUCCESS,
    error_code: AdapterErrorCode | None = None,
) -> WorkspaceMembershipLookup:
    return WorkspaceMembershipLookup(
        email=email,
        status=status,
        candidates=candidates,
        evidence_id=f"ev-{email}",
        error_code=error_code,
    )


def _candidate(tenant_id: str, name: str, role: str = "owner") -> WorkspaceCandidate:
    return WorkspaceCandidate(
        tenant_id=tenant_id,
        workspace_name=name,
        role=role,
    )


def test_unique_form_email_membership_resolves_without_workspace_id() -> None:
    resolution = resolve_workspace_identity(
        form_email="Owner@Example.com",
        form_workspace_id="",
        requester_email="requester@example.com",
        lookups=(_lookup("owner@example.com", _candidate(TENANT_1, "Acme")),),
        form_evidence_id="ev-form",
    )

    assert resolution.status is ResolutionStatus.RESOLVED
    assert resolution.method is ResolutionMethod.UNIQUE_EMAIL_MEMBERSHIP
    assert resolution.selected_tenant_id == TENANT_1
    assert set(resolution.evidence_ids) == {"ev-form", "ev-owner@example.com"}


def test_noncanonical_workspace_label_does_not_override_unique_email_membership() -> (
    None
):
    resolution = resolve_workspace_identity(
        form_email="owner@example.com",
        form_workspace_id="A different display label",
        requester_email="",
        lookups=(_lookup("owner@example.com", _candidate(TENANT_1, "Acme")),),
        form_evidence_id="ev-form",
    )

    assert resolution.status is ResolutionStatus.RESOLVED
    assert resolution.selected_tenant_id == TENANT_1


def test_unique_exact_candidate_name_disambiguates_multiple_memberships() -> None:
    resolution = resolve_workspace_identity(
        form_email="owner@example.com",
        form_workspace_id="  ＡＣＭＥ   Support  ",
        requester_email="",
        lookups=(
            _lookup(
                "owner@example.com",
                _candidate(TENANT_1, "Other"),
                _candidate(TENANT_2, "acme support"),
            ),
        ),
        form_evidence_id="ev-form",
    )

    assert resolution.status is ResolutionStatus.RESOLVED
    assert resolution.method is ResolutionMethod.UNIQUE_EXACT_CANDIDATE_NAME
    assert resolution.selected_tenant_id == TENANT_2


def test_customer_reply_exact_name_disambiguates_email_memberships() -> None:
    resolution = resolve_workspace_identity(
        form_email="owner@example.com",
        form_workspace_id="",
        requester_email="",
        customer_workspace_hint="  ＡＣＭＥ   Support  ",
        lookups=(
            _lookup(
                "owner@example.com",
                _candidate(TENANT_1, "Other"),
                _candidate(TENANT_2, "acme support"),
            ),
        ),
        form_evidence_id="ev-form",
    )

    assert resolution.status is ResolutionStatus.RESOLVED
    assert resolution.method is ResolutionMethod.UNIQUE_EXACT_CANDIDATE_NAME
    assert resolution.selected_tenant_id == TENANT_2


def test_customer_reply_cannot_override_a_conflicting_form_workspace_name() -> None:
    resolution = resolve_workspace_identity(
        form_email="owner@example.com",
        form_workspace_id="Other",
        requester_email="",
        customer_workspace_hint="Acme Support",
        lookups=(
            _lookup(
                "owner@example.com",
                _candidate(TENANT_1, "Other"),
                _candidate(TENANT_2, "Acme Support"),
            ),
        ),
        form_evidence_id="ev-form",
    )

    assert resolution.status is ResolutionStatus.AMBIGUOUS
    assert resolution.selected_tenant_id == ""


def test_duplicate_candidate_name_remains_ambiguous() -> None:
    resolution = resolve_workspace_identity(
        form_email="owner@example.com",
        form_workspace_id="Acme",
        requester_email="",
        lookups=(
            _lookup(
                "owner@example.com",
                _candidate(TENANT_1, "Acme"),
                _candidate(TENANT_2, "ACME"),
            ),
        ),
        form_evidence_id="ev-form",
    )

    assert resolution.status is ResolutionStatus.AMBIGUOUS
    assert resolution.selected_tenant_id == ""


def test_canonical_workspace_id_must_belong_to_email_candidates() -> None:
    resolution = resolve_workspace_identity(
        form_email="owner@example.com",
        form_workspace_id=TENANT_2,
        requester_email="",
        lookups=(_lookup("owner@example.com", _candidate(TENANT_1, "Acme")),),
        form_evidence_id="ev-form",
    )

    assert resolution.status is ResolutionStatus.CONFLICT
    assert resolution.selected_tenant_id == ""


def test_customer_exact_name_resolves_a_bounded_canonical_id_conflict() -> None:
    resolution = resolve_workspace_identity(
        form_email="owner@example.com",
        form_workspace_id=TENANT_2,
        requester_email="",
        customer_workspace_hint="Acme",
        lookups=(_lookup("owner@example.com", _candidate(TENANT_1, "Acme")),),
        form_evidence_id="ev-form",
    )

    assert resolution.status is ResolutionStatus.RESOLVED
    assert resolution.method is ResolutionMethod.UNIQUE_EXACT_CANDIDATE_NAME
    assert resolution.selected_tenant_id == TENANT_1


def test_customer_email_replaces_a_membership_email_with_no_workspaces() -> None:
    resolution = resolve_workspace_identity(
        form_email="owner@example.com",
        form_workspace_id="",
        requester_email="",
        customer_workspace_hint="workspace-owner@example.com",
        lookups=(
            _lookup("owner@example.com"),
            _lookup(
                "workspace-owner@example.com",
                _candidate(TENANT_2, "Target"),
            ),
        ),
        form_evidence_id="ev-form",
    )

    assert resolution.status is ResolutionStatus.RESOLVED
    assert resolution.method is ResolutionMethod.UNIQUE_EMAIL_MEMBERSHIP
    assert resolution.selected_tenant_id == TENANT_2


def test_email_shaped_workspace_field_is_target_and_contact_is_cross_checked() -> None:
    resolution = resolve_workspace_identity(
        form_email="contact@example.com",
        form_workspace_id="target@example.com",
        requester_email="requester@example.com",
        lookups=(
            _lookup("contact@example.com", _candidate(TENANT_1, "Contact")),
            _lookup("target@example.com", _candidate(TENANT_2, "Target")),
        ),
        form_evidence_id="ev-form",
    )

    assert resolution.status is ResolutionStatus.RESOLVED
    assert resolution.target_identity == "target@example.com"
    assert resolution.selected_tenant_id == TENANT_2
    assert set(resolution.evidence_ids) == {
        "ev-form",
        "ev-contact@example.com",
        "ev-target@example.com",
    }


def test_missing_contact_cross_check_is_internal_lookup_pending() -> None:
    resolution = resolve_workspace_identity(
        form_email="contact@example.com",
        form_workspace_id="target@example.com",
        requester_email="",
        lookups=(_lookup("target@example.com", _candidate(TENANT_2, "Target")),),
        form_evidence_id="ev-form",
    )

    assert resolution.status is ResolutionStatus.LOOKUP_PENDING
    assert "contact" in resolution.detail


def test_failed_lookup_is_not_customer_ambiguity() -> None:
    resolution = resolve_workspace_identity(
        form_email="owner@example.com",
        form_workspace_id="",
        requester_email="",
        lookups=(
            _lookup(
                "owner@example.com",
                status=LookupStatus.FAILED,
                error_code=AdapterErrorCode.TIMEOUT,
            ),
        ),
        form_evidence_id="ev-form",
    )

    assert resolution.status is ResolutionStatus.LOOKUP_FAILED
    assert resolution.selected_tenant_id == ""


def test_canonical_workspace_id_without_membership_evidence_is_not_resolved() -> None:
    resolution = resolve_workspace_identity(
        form_email="",
        form_workspace_id=TENANT_1,
        requester_email="",
        lookups=(),
        form_evidence_id="ev-form",
    )

    assert resolution.status is ResolutionStatus.NO_MEMBERSHIP
    assert resolution.method is ResolutionMethod.NONE
    assert resolution.selected_tenant_id == ""

from datetime import datetime, timezone

from zd_agent.casework.domain.enums import (
    AdapterErrorCode,
    BillingBindingStatus,
    LookupStatus,
    ResolutionMethod,
    ResolutionStatus,
)
from zd_agent.casework.domain.models import (
    BillingLookup,
    IdentityResolution,
    WorkspaceCandidate,
)
from zd_agent.casework.policies.billing import bind_billing_snapshot


NOW = datetime(2026, 8, 11, 3, 0, tzinfo=timezone.utc)
TENANT_1 = "11111111-1111-4111-8111-111111111111"
TENANT_2 = "22222222-2222-4222-8222-222222222222"


def _resolved_identity(tenant_id: str = TENANT_1) -> IdentityResolution:
    candidate = WorkspaceCandidate(
        tenant_id=tenant_id,
        workspace_name="Acme",
        role="owner",
    )
    return IdentityResolution(
        contact_email="owner@example.com",
        target_identity="owner@example.com",
        status=ResolutionStatus.RESOLVED,
        method=ResolutionMethod.UNIQUE_EMAIL_MEMBERSHIP,
        candidates=(candidate,),
        selected_tenant_id=tenant_id,
        selected_workspace_name="Acme",
        selected_role="owner",
        evidence_ids=("ev-identity",),
    )


def test_successful_billing_lookup_is_bound_to_identity_evidence() -> None:
    binding = bind_billing_snapshot(
        identity=_resolved_identity(),
        lookup=BillingLookup(
            tenant_id=TENANT_1,
            status=LookupStatus.SUCCESS,
            observed_at=NOW,
            evidence_id="ev-billing",
            plan="professional",
            subscription_status="active",
            interval="year",
        ),
    )

    assert binding.status is BillingBindingStatus.RESOLVED
    assert binding.snapshot is not None
    assert binding.snapshot.plan == "professional"
    assert binding.snapshot.evidence_ids == ("ev-identity", "ev-billing")


def test_subscription_and_cancellation_states_remain_separate() -> None:
    binding = bind_billing_snapshot(
        identity=_resolved_identity(),
        lookup=BillingLookup(
            tenant_id=TENANT_1,
            status=LookupStatus.SUCCESS,
            observed_at=NOW,
            evidence_id="ev-billing",
            plan="professional",
            subscription_status="active",
            cancellation_status="scheduled_at_period_end",
            renewal_status="will_not_renew",
            entitlement_status="active",
        ),
    )

    assert binding.snapshot is not None
    assert binding.snapshot.subscription_status == "active"
    assert binding.snapshot.cancellation_status == "scheduled_at_period_end"
    assert binding.snapshot.renewal_status == "will_not_renew"
    assert binding.snapshot.entitlement_status == "active"


def test_billing_lookup_for_different_tenant_is_rejected() -> None:
    binding = bind_billing_snapshot(
        identity=_resolved_identity(TENANT_1),
        lookup=BillingLookup(
            tenant_id=TENANT_2,
            status=LookupStatus.SUCCESS,
            observed_at=NOW,
            evidence_id="ev-billing",
            plan="team",
        ),
    )

    assert binding.status is BillingBindingStatus.TARGET_MISMATCH
    assert binding.snapshot is None


def test_failed_billing_lookup_does_not_invent_a_plan() -> None:
    binding = bind_billing_snapshot(
        identity=_resolved_identity(),
        lookup=BillingLookup(
            tenant_id=TENANT_1,
            status=LookupStatus.FAILED,
            observed_at=NOW,
            evidence_id="ev-error",
            error_code=AdapterErrorCode.TIMEOUT,
        ),
    )

    assert binding.status is BillingBindingStatus.LOOKUP_FAILED
    assert binding.snapshot is None
    assert binding.error_code is AdapterErrorCode.TIMEOUT


def test_pending_billing_lookup_remains_internal_work() -> None:
    binding = bind_billing_snapshot(
        identity=_resolved_identity(),
        lookup=None,
    )

    assert binding.status is BillingBindingStatus.LOOKUP_PENDING
    assert binding.snapshot is None


def test_unresolved_identity_blocks_billing_binding_before_lookup() -> None:
    identity = IdentityResolution(
        contact_email="owner@example.com",
        target_identity="owner@example.com",
        status=ResolutionStatus.AMBIGUOUS,
        candidates=(
            WorkspaceCandidate(tenant_id=TENANT_1, workspace_name="One"),
            WorkspaceCandidate(tenant_id=TENANT_2, workspace_name="Two"),
        ),
        evidence_ids=("ev-identity",),
    )

    binding = bind_billing_snapshot(identity=identity, lookup=None)

    assert binding.status is BillingBindingStatus.IDENTITY_UNRESOLVED
    assert binding.snapshot is None

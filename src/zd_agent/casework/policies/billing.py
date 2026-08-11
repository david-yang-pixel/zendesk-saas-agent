from __future__ import annotations

from zd_agent.casework.domain.enums import (
    BillingBindingStatus,
    LookupStatus,
    ResolutionStatus,
)
from zd_agent.casework.domain.models import (
    BillingBinding,
    BillingLookup,
    BillingSnapshot,
    IdentityResolution,
)


def bind_billing_snapshot(
    *,
    identity: IdentityResolution,
    lookup: BillingLookup | None,
) -> BillingBinding:
    """Bind authoritative Billing facts to one resolved target tenant."""
    if identity.status is not ResolutionStatus.RESOLVED:
        return BillingBinding(
            status=BillingBindingStatus.IDENTITY_UNRESOLVED,
            evidence_ids=identity.evidence_ids,
            detail="workspace identity must resolve before Billing lookup",
        )
    if lookup is None or lookup.status is LookupStatus.PENDING:
        return BillingBinding(
            status=BillingBindingStatus.LOOKUP_PENDING,
            evidence_ids=identity.evidence_ids,
            detail="tenant-scoped Billing lookup pending",
        )
    evidence_ids = tuple(
        dict.fromkeys((*identity.evidence_ids, lookup.evidence_id)),
    )
    if lookup.tenant_id != identity.selected_tenant_id:
        return BillingBinding(
            status=BillingBindingStatus.TARGET_MISMATCH,
            evidence_ids=evidence_ids,
            detail="Billing result does not target the resolved workspace",
        )
    if lookup.status is LookupStatus.FAILED:
        return BillingBinding(
            status=BillingBindingStatus.LOOKUP_FAILED,
            error_code=lookup.error_code,
            evidence_ids=evidence_ids,
            detail="tenant-scoped Billing lookup failed",
        )
    snapshot = BillingSnapshot(
        tenant_id=lookup.tenant_id,
        identity_tenant_id=identity.selected_tenant_id,
        plan=lookup.plan,
        subscription_status=lookup.subscription_status,
        interval=lookup.interval,
        stripe_status=lookup.stripe_status,
        entitlement_status=lookup.entitlement_status,
        cancellation_status=lookup.cancellation_status,
        renewal_status=lookup.renewal_status,
        observed_at=lookup.observed_at,
        evidence_ids=evidence_ids,
    )
    return BillingBinding(
        status=BillingBindingStatus.RESOLVED,
        snapshot=snapshot,
        evidence_ids=evidence_ids,
    )

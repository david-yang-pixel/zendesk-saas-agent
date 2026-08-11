from __future__ import annotations

from pydantic import Field

from zd_agent.casework.domain.enums import (
    Disposition,
    OwnershipAction,
    ReasonCode,
    ReplyKind,
)
from zd_agent.casework.domain.models import (
    BillingLookup,
    CaseAssessment,
    FrozenModel,
    WorkspaceMembershipLookup,
)


class ReplayExpected(FrozenModel):
    tenant_id: str = ""
    billing_plan: str = ""
    disposition: Disposition
    reply_kind: ReplyKind
    ownership_action: OwnershipAction
    reason_code: ReasonCode
    required_customer_fields: tuple[str, ...] = ()


class ReplayScenario(FrozenModel):
    scenario_id: str = Field(min_length=1)
    form_email: str = ""
    form_workspace_id: str = ""
    requester_email: str = ""
    membership_lookups: tuple[WorkspaceMembershipLookup, ...] = ()
    billing_required: bool = False
    billing_lookup: BillingLookup | None = None
    assessment: CaseAssessment
    expected: ReplayExpected


class ReplayOutcome(FrozenModel):
    scenario_id: str = Field(min_length=1)
    tenant_id: str = ""
    billing_plan: str = ""
    disposition: Disposition
    reply_kind: ReplyKind
    ownership_action: OwnershipAction
    reason_code: ReasonCode
    required_customer_fields: tuple[str, ...] = ()


class ReplayComparison(FrozenModel):
    scenario_id: str = Field(min_length=1)
    matches: bool
    mismatches: tuple[str, ...] = ()

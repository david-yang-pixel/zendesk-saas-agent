from __future__ import annotations

from zd_agent.casework.domain.enums import ResolutionStatus
from zd_agent.casework.policies.billing import bind_billing_snapshot
from zd_agent.casework.policies.disposition import decide_disposition
from zd_agent.casework.policies.identity import resolve_workspace_identity
from zd_agent.casework.replay.models import ReplayOutcome, ReplayScenario


def run_replay(scenario: ReplayScenario) -> ReplayOutcome:
    """Run one sanitized scenario through identity, Billing, and disposition."""
    identity = resolve_workspace_identity(
        form_email=scenario.form_email,
        form_workspace_id=scenario.form_workspace_id,
        requester_email=scenario.requester_email,
        lookups=scenario.membership_lookups,
        form_evidence_id=f"replay:{scenario.scenario_id}:form",
    )
    billing_plan = ""
    if scenario.billing_required:
        billing = bind_billing_snapshot(
            identity=identity,
            lookup=scenario.billing_lookup,
        )
        if billing.snapshot is not None:
            billing_plan = billing.snapshot.plan
    decision = decide_disposition(scenario.assessment)
    return ReplayOutcome(
        scenario_id=scenario.scenario_id,
        tenant_id=(
            identity.selected_tenant_id
            if identity.status is ResolutionStatus.RESOLVED
            else ""
        ),
        billing_plan=billing_plan,
        disposition=decision.disposition,
        reply_kind=decision.reply_kind,
        ownership_action=decision.ownership_action,
        reason_code=decision.reason_code,
        required_customer_fields=decision.required_customer_fields,
    )

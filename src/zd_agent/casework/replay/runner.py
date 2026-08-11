from __future__ import annotations

from datetime import datetime

from zd_agent.casework.adapters.legacy_evidence import build_case_context_evidence
from zd_agent.casework.adapters.model import (
    NoOpReplyReviewer,
    PolicyReplyWriter,
)
from zd_agent.casework.application.build_context import CaseContextBuilder
from zd_agent.casework.application.compose_reply import ReplyComposer
from zd_agent.casework.application.handle_case import CaseworkOrchestrator
from zd_agent.casework.domain.enums import LookupStatus
from zd_agent.casework.domain.models import (
    BillingLookup,
    CaseContext,
    FormIdentity,
    InvestigationResult,
    TicketSnapshot,
    WorkspaceMembershipLookup,
)
from zd_agent.casework.replay.models import ReplayOutcome, ReplayScenario


class _ReplayClock:
    def __init__(self, scenario: ReplayScenario) -> None:
        self._now = scenario.as_of

    def now(self) -> datetime:
        return self._now


class _ReplayIdentityReader:
    def __init__(self, scenario: ReplayScenario) -> None:
        self._lookups = {item.email: item for item in scenario.membership_lookups}

    async def find_workspaces(self, email: str) -> WorkspaceMembershipLookup:
        lookup = self._lookups.get(email)
        if lookup is not None:
            return lookup
        return WorkspaceMembershipLookup(email=email, status=LookupStatus.PENDING)


class _ReplayBillingReader:
    def __init__(self, scenario: ReplayScenario) -> None:
        self._lookup = scenario.billing_lookup
        self._as_of = scenario.as_of

    async def read_billing(self, tenant_id: str) -> BillingLookup:
        if self._lookup is not None:
            return self._lookup
        return BillingLookup(
            tenant_id=tenant_id,
            status=LookupStatus.PENDING,
            observed_at=self._as_of,
        )


class _ReplayInvestigator:
    def __init__(self, scenario: ReplayScenario) -> None:
        self._scenario = scenario

    async def investigate(self, context: CaseContext) -> InvestigationResult:
        context_evidence = build_case_context_evidence(context)
        combined = {item.evidence_id: item for item in context_evidence}
        combined.update({item.evidence_id: item for item in self._scenario.evidence})
        return InvestigationResult(
            proposal=self._scenario.assessment,
            evidence=tuple(combined.values()),
        )


def _ticket(scenario: ReplayScenario) -> TicketSnapshot:
    return TicketSnapshot(
        ticket_id=9000,
        generation=1,
        status="open",
        channel="replay",
        latest_event_id=f"replay:{scenario.scenario_id}",
        latest_event_at=scenario.as_of,
        questions=(scenario.assessment.question,),
        form_identity=FormIdentity(
            email=scenario.form_email,
            workspace_id=scenario.form_workspace_id,
        ),
        requester_email=scenario.requester_email,
    )


async def run_replay(scenario: ReplayScenario) -> ReplayOutcome:
    """Run a sanitized scenario through the complete no-delivery core."""
    clock = _ReplayClock(scenario)
    orchestrator = CaseworkOrchestrator(
        context_builder=CaseContextBuilder(
            identity_reader=_ReplayIdentityReader(scenario),
            billing_reader=_ReplayBillingReader(scenario),
            clock=clock,
        ),
        investigator=_ReplayInvestigator(scenario),
        reply_composer=ReplyComposer(
            writer=PolicyReplyWriter(),
            reviewer=NoOpReplyReviewer(),
        ),
        clock=clock,
    )
    run = await orchestrator.run(
        ticket=_ticket(scenario),
        billing_required=scenario.billing_required,
    )
    billing = run.context.billing.snapshot if run.context.billing else None
    reply = run.reply_draft
    return ReplayOutcome(
        scenario_id=scenario.scenario_id,
        tenant_id=run.context.identity.selected_tenant_id,
        billing_plan=billing.plan if billing else "",
        disposition=run.decision.disposition,
        reply_kind=run.decision.reply_kind,
        ownership_action=run.decision.ownership_action,
        reason_code=run.decision.reason_code,
        required_customer_fields=run.decision.required_customer_fields,
        admitted_claim_ids=tuple(
            claim.claim_id for claim in run.assessment_build.assessment.admitted_claims
        ),
        rejected_customer_fields=run.assessment_build.rejected_customer_fields,
        reply_body=reply.body if reply else "",
    )

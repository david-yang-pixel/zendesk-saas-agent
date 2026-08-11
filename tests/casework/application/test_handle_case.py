from datetime import datetime, timezone

from zd_agent.casework.application.build_context import CaseContextBuilder
from zd_agent.casework.application.compose_reply import ReplyComposer
from zd_agent.casework.application.handle_case import CaseworkOrchestrator
from zd_agent.casework.domain.enums import (
    AdapterErrorCode,
    AnswerStatus,
    ClaimRole,
    ClaimStatus,
    Disposition,
    EvidenceAuthority,
    EvidenceResultStatus,
    ExecutionMode,
    LookupStatus,
    ReplyKind,
)
from zd_agent.casework.domain.models import (
    AssessmentProposal,
    BillingLookup,
    Claim,
    EvidenceItem,
    FormIdentity,
    InvestigationResult,
    ReplyCandidate,
    TargetReference,
    TicketSnapshot,
    WorkspaceCandidate,
    WorkspaceMembershipLookup,
)


NOW = datetime(2026, 8, 11, 7, 0, tzinfo=timezone.utc)
TENANT = "11111111-1111-4111-8111-111111111111"


class FakeClock:
    def now(self) -> datetime:
        return NOW


class FakeIdentityReader:
    def __init__(self, lookup: WorkspaceMembershipLookup) -> None:
        self._lookup = lookup

    async def find_workspaces(self, email: str) -> WorkspaceMembershipLookup:
        return self._lookup


class FakeBillingReader:
    def __init__(self, lookup: BillingLookup) -> None:
        self._lookup = lookup

    async def read_billing(self, tenant_id: str) -> BillingLookup:
        return self._lookup


class FakeInvestigator:
    def __init__(self, result: InvestigationResult) -> None:
        self._result = result

    async def investigate(self, context) -> InvestigationResult:
        return self._result


class FakeWriter:
    def __init__(self) -> None:
        self.calls = 0

    async def write_reply(self, *, assessment, decision) -> ReplyCandidate:
        self.calls += 1
        return ReplyCandidate(
            body="Your workspace has an active Professional subscription.",
            language="en",
            reply_kind=decision.reply_kind,
            used_claim_ids=tuple(
                claim.claim_id for claim in assessment.admitted_claims
            ),
            requested_customer_fields=decision.required_customer_fields,
        )


class FakeReviewer:
    async def revise_wording(self, *, body, assessment, decision) -> str:
        return body


def _ticket() -> TicketSnapshot:
    return TicketSnapshot(
        ticket_id=4002,
        generation=2,
        status="open",
        channel="messaging",
        locale="en-US",
        latest_event_id="event-2",
        latest_event_at=NOW,
        questions=("What subscription is active?",),
        form_identity=FormIdentity(email="owner@example.com"),
    )


def _context_builder(
    *,
    identity_lookup: WorkspaceMembershipLookup | None = None,
) -> CaseContextBuilder:
    identity_lookup = identity_lookup or WorkspaceMembershipLookup(
        email="owner@example.com",
        status=LookupStatus.SUCCESS,
        candidates=(
            WorkspaceCandidate(
                tenant_id=TENANT,
                workspace_name="Acme",
                role="owner",
            ),
        ),
        evidence_id="ev-identity",
    )
    return CaseContextBuilder(
        identity_reader=FakeIdentityReader(identity_lookup),
        billing_reader=FakeBillingReader(
            BillingLookup(
                tenant_id=TENANT,
                status=LookupStatus.SUCCESS,
                observed_at=NOW,
                evidence_id="ev-billing",
                plan="professional",
                subscription_status="active",
            ),
        ),
        clock=FakeClock(),
    )


async def test_orchestrator_defaults_to_casework_mode() -> None:
    target = TargetReference(kind="tenant_id", value=TENANT)
    claim = Claim(
        claim_id="claim-plan",
        statement="The workspace has an active Professional subscription.",
        role=ClaimRole.CURRENT_STATE,
        status=ClaimStatus.CONFIRMED,
        supporting_evidence_ids=("ev-identity", "ev-billing"),
        targets=(target,),
        public_allowed=True,
    )
    identity_evidence = EvidenceItem(
        evidence_id="ev-identity",
        call_id="call-identity",
        source="identity-reader",
        authority=EvidenceAuthority.CURRENT_AUTHORITATIVE,
        result_status=EvidenceResultStatus.SUCCESS,
        targets=(target,),
        observed_at=NOW,
        supports_roles=(ClaimRole.CURRENT_STATE,),
        payload={"resolved": True},
    )
    billing_evidence = EvidenceItem(
        evidence_id="ev-billing",
        call_id="call-billing",
        source="billing-reader",
        authority=EvidenceAuthority.CURRENT_AUTHORITATIVE,
        result_status=EvidenceResultStatus.SUCCESS,
        targets=(target,),
        observed_at=NOW,
        supports_roles=(ClaimRole.CURRENT_STATE,),
        payload={"plan": "professional", "status": "active"},
    )
    writer = FakeWriter()
    orchestrator = CaseworkOrchestrator(
        context_builder=_context_builder(),
        investigator=FakeInvestigator(
            InvestigationResult(
                evidence=(identity_evidence, billing_evidence),
                proposal=AssessmentProposal(
                    question="What subscription is active?",
                    answer_status=AnswerStatus.SUPPORTED,
                    proposed_claims=(claim,),
                ),
            ),
        ),
        reply_composer=ReplyComposer(writer=writer, reviewer=FakeReviewer()),
        clock=FakeClock(),
    )

    run = await orchestrator.run(
        ticket=_ticket(),
        billing_required=True,
    )

    assert run.execution_mode is ExecutionMode.CASEWORK
    assert run.context.identity.selected_tenant_id == TENANT
    assert run.decision.disposition is Disposition.RESOLVED
    assert run.reply_draft is not None
    assert run.reply_draft.body.startswith("Your workspace")
    assert run.delivery_attempted is False
    assert writer.calls == 1


async def test_retry_pending_run_does_not_compose_a_reply() -> None:
    writer = FakeWriter()
    failed_lookup = WorkspaceMembershipLookup(
        email="owner@example.com",
        status=LookupStatus.FAILED,
        evidence_id="ev-timeout",
        error_code=AdapterErrorCode.TIMEOUT,
    )
    orchestrator = CaseworkOrchestrator(
        context_builder=_context_builder(identity_lookup=failed_lookup),
        investigator=FakeInvestigator(
            InvestigationResult(
                proposal=AssessmentProposal(
                    question="What subscription is active?",
                    answer_status=AnswerStatus.INSUFFICIENT,
                ),
            ),
        ),
        reply_composer=ReplyComposer(writer=writer, reviewer=FakeReviewer()),
        clock=FakeClock(),
    )

    run = await orchestrator.run(
        ticket=_ticket(),
        billing_required=True,
    )

    assert run.decision.disposition is Disposition.RETRY_PENDING
    assert run.decision.reply_kind is ReplyKind.NONE
    assert run.reply_draft is None
    assert run.delivery_attempted is False
    assert writer.calls == 0

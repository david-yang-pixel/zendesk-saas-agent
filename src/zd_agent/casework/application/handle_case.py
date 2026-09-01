from __future__ import annotations

from datetime import datetime

from zd_agent.casework.application.assess_case import build_case_assessment
from zd_agent.casework.application.build_context import CaseContextBuilder
from zd_agent.casework.application.compose_reply import ReplyComposer
from zd_agent.casework.domain.enums import (
    ActionRequirement,
    AnswerStatus,
    ClaimRole,
    ClaimStatus,
    EvidenceAuthority,
    EvidenceResultStatus,
    ExecutionMode,
    OwnershipAction,
    ReasonCode,
    ResolutionStatus,
    ReplyKind,
)
from zd_agent.casework.domain.models import (
    ActionProposal,
    AssessmentBuild,
    AssessmentProposal,
    CaseAssessment,
    CaseContext,
    CaseworkRun,
    Claim,
    ClaimAdmission,
    CustomerMessageEvent,
    EvidenceItem,
    HumanActionRequest,
    IdentityResolution,
    InvestigationResult,
    TargetReference,
    TicketSnapshot,
    VisualObservation,
)
from zd_agent.casework.policies.disposition import decide_disposition
from zd_agent.casework.policies.ownership import active_human_request_index
from zd_agent.casework.policies.reply_language import (
    asks_whether_japanese_is_supported,
    japanese_support_confirmation,
    resolve_ticket_reply_language,
)
from zd_agent.casework.ports.investigation import CaseInvestigator
from zd_agent.casework.ports.readers import Clock


def _missing_issue_result(
    ticket: TicketSnapshot,
) -> tuple[InvestigationResult, AssessmentBuild]:
    question = "The customer has not yet described the issue."
    investigation = InvestigationResult(
        proposal=AssessmentProposal(
            question=question,
            answer_status=AnswerStatus.INSUFFICIENT,
            proposed_customer_fields=("issue_description",),
        ),
    )
    assessment = CaseAssessment(
        question=question,
        reply_language=resolve_ticket_reply_language(ticket),
        answer_status=AnswerStatus.INSUFFICIENT,
        customer_only_fields=("issue_description",),
    )
    return investigation, AssessmentBuild(assessment=assessment)


def _human_action(evidence_id: str) -> HumanActionRequest:
    return HumanActionRequest(
        ownership_action=OwnershipAction.SUPPORT,
        reason_code=ReasonCode.CUSTOMER_REQUESTED_HUMAN,
        summary="The customer explicitly requested human support.",
        customer_impact=(
            "The customer asked the automation to stop and a human to take over."
        ),
        completed_checks=(
            "Confirmed the latest customer message is an explicit human-support request.",
        ),
        unresolved_action="A human support engineer must continue this ticket.",
        acceptance_criteria=(
            "A human support engineer reviews the complete ticket conversation.",
            "The automated agent sends no further replies while human ownership is active.",
        ),
        evidence_ids=(evidence_id,),
    )


def _human_request_evidence(
    ticket: TicketSnapshot,
    event: CustomerMessageEvent,
    evidence_id: str,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        call_id=f"zendesk-event:{event.event_id}",
        source="zendesk-conversation",
        authority=EvidenceAuthority.CURRENT_AUTHORITATIVE,
        result_status=EvidenceResultStatus.SUCCESS,
        targets=(TargetReference(kind="ticket_id", value=str(ticket.ticket_id)),),
        observed_at=event.occurred_at,
        supports_roles=(),
        payload={"customer_requested_human": True},
    )


def _human_request_result(
    ticket: TicketSnapshot,
    event: CustomerMessageEvent,
) -> tuple[InvestigationResult, AssessmentBuild]:
    evidence_id = f"ticket:{ticket.ticket_id}:event:{event.event_id}:human-request"
    action = _human_action(evidence_id)
    action_proposal = ActionProposal(
        requirement=ActionRequirement.CUSTOMER_REQUESTED_HUMAN,
        summary=action.summary,
        customer_impact=action.customer_impact,
        completed_checks=action.completed_checks,
        unresolved_action=action.unresolved_action,
        acceptance_criteria=action.acceptance_criteria,
        evidence_ids=action.evidence_ids,
    )
    proposal = AssessmentProposal(
        question=event.body,
        answer_status=AnswerStatus.INSUFFICIENT,
        action_proposal=action_proposal,
        evidence_ids=(evidence_id,),
    )
    investigation = InvestigationResult(
        proposal=proposal,
        evidence=(_human_request_evidence(ticket, event, evidence_id),),
    )
    assessment = CaseAssessment(
        question=event.body,
        reply_language=resolve_ticket_reply_language(ticket),
        answer_status=AnswerStatus.INSUFFICIENT,
        human_action=action,
        evidence_ids=(evidence_id,),
    )
    return investigation, AssessmentBuild(assessment=assessment)


def _language_support_result(
    ticket: TicketSnapshot,
    event: CustomerMessageEvent,
) -> tuple[InvestigationResult, AssessmentBuild]:
    evidence_id = "policy:supported-customer-language:ja:v1"
    language = resolve_ticket_reply_language(ticket)
    claim = Claim(
        claim_id=f"ticket:{ticket.ticket_id}:japanese-support",
        statement=japanese_support_confirmation(language),
        role=ClaimRole.PRODUCT_FACT,
        status=ClaimStatus.CONFIRMED,
        supporting_evidence_ids=(evidence_id,),
        public_allowed=True,
    )
    evidence = EvidenceItem(
        evidence_id=evidence_id,
        call_id=evidence_id,
        source="casework-policy",
        authority=EvidenceAuthority.CURRENT_AUTHORITATIVE,
        result_status=EvidenceResultStatus.SUCCESS,
        observed_at=event.occurred_at,
        supports_roles=(ClaimRole.PRODUCT_FACT,),
        payload={"supported_customer_language": "ja"},
    )
    investigation = InvestigationResult(
        proposal=AssessmentProposal(
            question=event.body,
            answer_status=AnswerStatus.SUPPORTED,
            proposed_claims=(claim,),
            evidence_ids=(evidence_id,),
        ),
        evidence=(evidence,),
    )
    assessment = CaseAssessment(
        question=event.body,
        reply_language=language,
        answer_status=AnswerStatus.SUPPORTED,
        admitted_claims=(claim,),
        evidence_ids=(evidence_id,),
    )
    return investigation, AssessmentBuild(
        assessment=assessment,
        admissions=(
            ClaimAdmission(
                claim_id=claim.claim_id,
                accepted=True,
                eligible_evidence_ids=(evidence_id,),
            ),
        ),
    )


def _deterministic_result(
    ticket: TicketSnapshot,
) -> tuple[InvestigationResult, AssessmentBuild] | None:
    messages = ticket.customer_messages
    bodies = tuple(event.body for event in messages) if messages else ticket.questions
    human_index = active_human_request_index(bodies)
    if human_index is not None:
        event = (
            messages[human_index]
            if messages
            else CustomerMessageEvent(
                event_id=ticket.latest_event_id,
                occurred_at=ticket.latest_event_at,
                body=bodies[human_index],
            )
        )
        return _human_request_result(ticket, event)
    if bodies and asks_whether_japanese_is_supported(bodies[-1]):
        event = (
            messages[-1]
            if messages
            else CustomerMessageEvent(
                event_id=ticket.latest_event_id,
                occurred_at=ticket.latest_event_at,
                body=bodies[-1],
            )
        )
        return _language_support_result(ticket, event)
    if not ticket.questions:
        return _missing_issue_result(ticket)
    return None


def deterministic_intake_required(ticket: TicketSnapshot) -> bool:
    """Whether this ticket can be handled without evidence or visual reads."""
    return _deterministic_result(ticket) is not None


def _minimal_context(
    *,
    ticket: TicketSnapshot,
    built_at: datetime,
    automation_attempt: int,
    visual_observations: tuple[VisualObservation, ...],
) -> CaseContext:
    contact_email = ticket.form_identity.email or ticket.requester_email
    target_identity = ticket.form_identity.workspace_id or contact_email
    return CaseContext(
        ticket=ticket,
        built_at=built_at,
        identity=IdentityResolution(
            contact_email=contact_email,
            target_identity=target_identity,
            status=ResolutionStatus.LOOKUP_PENDING,
        ),
        automation_attempt=automation_attempt,
        visual_observations=visual_observations,
    )


class CaseworkOrchestrator:
    """Run the complete casework decision path without owning delivery."""

    def __init__(
        self,
        *,
        context_builder: CaseContextBuilder,
        investigator: CaseInvestigator,
        reply_composer: ReplyComposer,
        clock: Clock,
    ) -> None:
        self._context_builder = context_builder
        self._investigator = investigator
        self._reply_composer = reply_composer
        self._clock = clock

    async def run(
        self,
        *,
        ticket: TicketSnapshot,
        billing_required: bool,
        automation_attempt: int = 1,
        visual_observations: tuple[VisualObservation, ...] = (),
        execution_mode: ExecutionMode = ExecutionMode.CASEWORK,
    ) -> CaseworkRun:
        started_at = self._clock.now()
        deterministic = _deterministic_result(ticket)
        if deterministic is not None:
            context = _minimal_context(
                ticket=ticket,
                built_at=started_at,
                automation_attempt=automation_attempt,
                visual_observations=visual_observations,
            )
            investigation, assessment_build = deterministic
        else:
            context = await self._context_builder.build(
                ticket,
                billing_required=billing_required,
                automation_attempt=automation_attempt,
                visual_observations=visual_observations,
            )
            investigation, assessment_build = await self._assess(context)
        decision = decide_disposition(assessment_build.assessment)
        reply_draft = None
        if decision.reply_kind is not ReplyKind.NONE:
            reply_draft = await self._reply_composer.compose(
                assessment=assessment_build.assessment,
                decision=decision,
            )
        return CaseworkRun(
            execution_mode=execution_mode,
            started_at=started_at,
            finished_at=self._clock.now(),
            context=context,
            investigation=investigation,
            assessment_build=assessment_build,
            decision=decision,
            reply_draft=reply_draft,
            delivery_attempted=False,
        )

    async def _assess(
        self,
        context: CaseContext,
    ) -> tuple[InvestigationResult, AssessmentBuild]:
        investigation = await self._investigator.investigate(context)
        assessment = build_case_assessment(
            context=context,
            proposal=investigation.proposal,
            evidence=investigation.evidence,
            as_of=self._clock.now(),
        )
        return investigation, assessment

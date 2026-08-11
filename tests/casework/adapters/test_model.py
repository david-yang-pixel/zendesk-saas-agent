from datetime import datetime, timezone
import json

from zd_agent.casework.adapters.evidence import build_case_context_evidence
from zd_agent.casework.adapters.model import (
    NoOpReplyReviewer,
    PolicyReplyWriter,
    investigation_result_from_content,
)
from zd_agent.casework.domain.enums import (
    AnswerStatus,
    ClaimRole,
    ClaimStatus,
    Disposition,
    OwnershipAction,
    ReasonCode,
    ReplyKind,
    ResolutionMethod,
    ResolutionStatus,
)
from zd_agent.casework.domain.models import (
    CaseAssessment,
    CaseContext,
    Claim,
    DispositionDecision,
    FormIdentity,
    IdentityResolution,
    TargetReference,
    TicketSnapshot,
    WorkspaceCandidate,
)


NOW = datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc)
TENANT = "11111111-1111-4111-8111-111111111111"


def _resolved_context() -> CaseContext:
    return CaseContext(
        ticket=TicketSnapshot(
            ticket_id=4001,
            generation=1,
            status="open",
            latest_event_id="event-1",
            latest_event_at=NOW,
            questions=("Why is this workspace failing?",),
            form_identity=FormIdentity(email="owner@example.test"),
        ),
        built_at=NOW,
        identity=IdentityResolution(
            contact_email="owner@example.test",
            target_identity="owner@example.test",
            status=ResolutionStatus.RESOLVED,
            method=ResolutionMethod.UNIQUE_EMAIL_MEMBERSHIP,
            candidates=(
                WorkspaceCandidate(tenant_id=TENANT, workspace_name="Workspace"),
            ),
            selected_tenant_id=TENANT,
            selected_workspace_name="Workspace",
            evidence_ids=("ev-identity",),
        ),
    )


def test_model_identity_evidence_is_only_a_target_binding() -> None:
    context = _resolved_context()
    evidence = build_case_context_evidence(context)
    content = json.dumps(
        {
            "answer_status": "supported",
            "proposed_claims": [
                {
                    "statement": "The workspace is currently failing.",
                    "role": "current_state",
                    "status": "confirmed",
                    "supporting_evidence_ids": ["ev-identity"],
                    "targets": [],
                },
            ],
        },
    )

    result = investigation_result_from_content(
        context=context,
        evidence=evidence,
        content=content,
    )

    [claim] = result.proposal.proposed_claims
    [identity_evidence] = result.evidence
    assert claim.targets == (TargetReference(kind="tenant_id", value=TENANT),)
    assert claim.supporting_evidence_ids == ("ev-identity",)
    assert identity_evidence.authority.value == "target_binding"
    assert identity_evidence.supports_roles == ()


def test_model_keeps_original_issue_after_workspace_clarification() -> None:
    context = _resolved_context()
    context = context.model_copy(
        update={
            "ticket": context.ticket.model_copy(
                update={
                    "questions": (
                        "Please approve a refund for this renewal.",
                        "Workspace One",
                    ),
                    "active_question": "Please approve a refund for this renewal.",
                }
            )
        }
    )

    result = investigation_result_from_content(
        context=context,
        evidence=(),
        content=json.dumps({"answer_status": "insufficient"}),
    )

    assert result.proposal.question == "Please approve a refund for this renewal."


def test_model_binds_visual_observation_to_current_ticket() -> None:
    context = _resolved_context()
    content = json.dumps(
        {
            "answer_status": "supported",
            "proposed_claims": [
                {
                    "statement": "The screenshot displays a quota error.",
                    "role": "visual_observation",
                    "status": "evidence_supported",
                    "supporting_evidence_ids": ["visual:attachment-1"],
                    "targets": [],
                },
            ],
        },
    )

    result = investigation_result_from_content(
        context=context,
        evidence=(),
        content=content,
    )

    [claim] = result.proposal.proposed_claims
    assert claim.targets == (TargetReference(kind="ticket_id", value="4001"),)
    assert claim.supporting_evidence_ids == ("visual:attachment-1",)


def test_model_cannot_choose_case_targets() -> None:
    context = _resolved_context()
    content = json.dumps(
        {
            "answer_status": "supported",
            "proposed_claims": [
                {
                    "statement": "The current request is rejected.",
                    "role": "current_state",
                    "status": "confirmed",
                    "supporting_evidence_ids": ["ev-runtime"],
                    "targets": [
                        {
                            "kind": "tenant_id",
                            "value": "22222222-2222-4222-8222-222222222222",
                        },
                        {"kind": "trace_id", "value": "model-chosen-trace"},
                    ],
                },
                {
                    "statement": "The published limit is 5,000 requests.",
                    "role": "product_fact",
                    "status": "evidence_supported",
                    "supporting_evidence_ids": ["ev-docs"],
                    "targets": [
                        {
                            "kind": "tenant_id",
                            "value": "22222222-2222-4222-8222-222222222222",
                        },
                    ],
                },
            ],
        },
    )

    result = investigation_result_from_content(
        context=context,
        evidence=(),
        content=content,
    )

    current_state, product_fact = result.proposal.proposed_claims
    assert current_state.targets == (TargetReference(kind="tenant_id", value=TENANT),)
    assert product_fact.targets == ()


async def test_policy_reply_writer_cannot_add_facts_or_unapproved_questions() -> None:
    claim = Claim(
        claim_id="claim-current",
        statement="The current API request is rejected because its quota is exhausted.",
        role=ClaimRole.CURRENT_STATE,
        status=ClaimStatus.CONFIRMED,
        supporting_evidence_ids=("ev-runtime",),
        targets=(TargetReference(kind="tenant_id", value=TENANT),),
        public_allowed=True,
    )
    assessment = CaseAssessment(
        question="Why is the API request failing?",
        answer_status=AnswerStatus.SUPPORTED,
        admitted_claims=(claim,),
    )
    decision = DispositionDecision(
        disposition=Disposition.RESOLVED,
        reply_kind=ReplyKind.ANSWER,
        ownership_action=OwnershipAction.NONE,
        reason_code=ReasonCode.RESOLVED_WITH_EVIDENCE,
        evidence_ids=("ev-runtime",),
    )

    candidate = await PolicyReplyWriter().write_reply(
        assessment=assessment,
        decision=decision,
    )
    reviewed = await NoOpReplyReviewer().revise_wording(
        body=candidate.body,
        assessment=assessment,
        decision=decision,
    )

    assert candidate.body.startswith(claim.statement)
    assert candidate.body.endswith("This reply was sent by Dify AI Agent.")
    assert candidate.used_claim_ids == ("claim-current",)
    assert candidate.requested_customer_fields == ()
    assert "workspace" not in candidate.body.casefold()
    assert reviewed == candidate.body

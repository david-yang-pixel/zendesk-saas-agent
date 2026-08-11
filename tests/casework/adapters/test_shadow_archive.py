from __future__ import annotations

import json
from datetime import datetime, timezone

from zd_agent.casework.adapters.archive import JsonlShadowArchive
from zd_agent.casework.domain.enums import (
    AnswerStatus,
    BillingBindingStatus,
    ClaimRole,
    ClaimStatus,
    Disposition,
    EvidenceAuthority,
    EvidenceResultStatus,
    ExecutionMode,
    OwnershipAction,
    ReasonCode,
    ReplyKind,
    ResolutionMethod,
    ResolutionStatus,
)
from zd_agent.casework.domain.models import (
    AssessmentBuild,
    AssessmentProposal,
    BillingBinding,
    BillingSnapshot,
    CaseAssessment,
    CaseContext,
    CaseworkRun,
    Claim,
    DispositionDecision,
    EvidenceItem,
    FormIdentity,
    IdentityResolution,
    InvestigationResult,
    ReplyDraft,
    TargetReference,
    TicketSnapshot,
    WorkspaceCandidate,
)
from zd_agent.casework.domain.shadow import build_shadow_record


NOW = datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc)
TENANT = "11111111-1111-4111-8111-111111111111"


def _run() -> CaseworkRun:
    target = TargetReference(kind="tenant_id", value=TENANT)
    claim = Claim(
        claim_id="claim-plan",
        statement="The workspace has an active Professional subscription.",
        role=ClaimRole.CURRENT_STATE,
        status=ClaimStatus.CONFIRMED,
        supporting_evidence_ids=("ev-billing",),
        targets=(target,),
        public_allowed=True,
    )
    evidence = EvidenceItem(
        evidence_id="ev-billing",
        call_id="call-billing",
        source="billing-reader",
        authority=EvidenceAuthority.CURRENT_AUTHORITATIVE,
        result_status=EvidenceResultStatus.SUCCESS,
        targets=(target,),
        observed_at=NOW,
        supports_roles=(ClaimRole.CURRENT_STATE,),
        payload={"plan": "professional"},
    )
    candidate = WorkspaceCandidate(tenant_id=TENANT, workspace_name="Private Name")
    identity = IdentityResolution(
        contact_email="private-owner@example.test",
        target_identity="private-owner@example.test",
        status=ResolutionStatus.RESOLVED,
        method=ResolutionMethod.UNIQUE_EMAIL_MEMBERSHIP,
        candidates=(candidate,),
        selected_tenant_id=TENANT,
        selected_workspace_name="Private Name",
        evidence_ids=("ev-identity",),
    )
    snapshot = BillingSnapshot(
        tenant_id=TENANT,
        identity_tenant_id=TENANT,
        plan="professional",
        subscription_status="active",
        observed_at=NOW,
        evidence_ids=("ev-identity", "ev-billing"),
    )
    context = CaseContext(
        ticket=TicketSnapshot(
            ticket_id=4003,
            generation=4,
            status="open",
            channel="messaging",
            latest_event_id="private-event-id",
            latest_event_at=NOW,
            questions=("What plan is active?",),
            form_identity=FormIdentity(email="private-owner@example.test"),
        ),
        built_at=NOW,
        identity=identity,
        billing_required=True,
        billing=BillingBinding(
            status=BillingBindingStatus.RESOLVED,
            snapshot=snapshot,
            evidence_ids=snapshot.evidence_ids,
        ),
    )
    proposal = AssessmentProposal(
        question="What plan is active?",
        answer_status=AnswerStatus.SUPPORTED,
        proposed_claims=(claim,),
    )
    assessment = CaseAssessment(
        question=proposal.question,
        answer_status=AnswerStatus.SUPPORTED,
        admitted_claims=(claim,),
    )
    decision = DispositionDecision(
        disposition=Disposition.RESOLVED,
        reply_kind=ReplyKind.ANSWER,
        reason_code=ReasonCode.RESOLVED_WITH_EVIDENCE,
        evidence_ids=("ev-billing",),
    )
    return CaseworkRun(
        execution_mode=ExecutionMode.SHADOW,
        started_at=NOW,
        finished_at=NOW,
        context=context,
        investigation=InvestigationResult(proposal=proposal, evidence=(evidence,)),
        assessment_build=AssessmentBuild(assessment=assessment),
        decision=decision,
        reply_draft=ReplyDraft(
            body="Your workspace has an active Professional subscription.",
            reply_kind=ReplyKind.ANSWER,
            disposition=Disposition.RESOLVED,
            ownership_action=OwnershipAction.NONE,
            reason_code=ReasonCode.RESOLVED_WITH_EVIDENCE,
            used_claim_ids=("claim-plan",),
            reviewed=True,
        ),
    )


def test_shadow_record_contains_decision_but_no_raw_customer_content() -> None:
    record = build_shadow_record(_run(), recorded_at=NOW)
    payload = record.model_dump_json()

    assert record.ticket_id == 4003
    assert record.identity_status is ResolutionStatus.RESOLVED
    assert record.billing_plan == "professional"
    assert record.disposition is Disposition.RESOLVED
    assert record.target_fingerprint
    assert record.reply_fingerprint
    assert "private-owner@example.test" not in payload
    assert "Private Name" not in payload
    assert "Your workspace has" not in payload
    assert TENANT not in payload


def test_jsonl_shadow_archive_appends_complete_redacted_records(tmp_path) -> None:
    record = build_shadow_record(_run(), recorded_at=NOW)
    archive = JsonlShadowArchive(tmp_path)

    first_path = archive.append(record)
    second_path = archive.append(record)

    assert first_path == second_path
    lines = first_path.read_text().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert [item["ticket_id"] for item in parsed] == [4003, 4003]
    assert all("contact_email" not in item for item in parsed)
    assert all("reply_body" not in item for item in parsed)

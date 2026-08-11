import json
from datetime import datetime, timezone
from typing import Any

from zd_agent.casework.adapters.legacy_evidence import LegacyCaseMaterial
from zd_agent.casework.adapters.model import (
    LlmCaseInvestigator,
    NoOpReplyReviewer,
    PolicyReplyWriter,
)
from zd_agent.casework.domain.enums import (
    AnswerStatus,
    ClaimRole,
    ClaimStatus,
    Disposition,
    EvidenceAuthority,
    EvidenceResultStatus,
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
    EvidenceItem,
    FormIdentity,
    IdentityResolution,
    TargetReference,
    TicketSnapshot,
    WorkspaceCandidate,
)
from zd_agent.runtime.llm.types import LLMProvider, LLMResponse, ToolSchema


NOW = datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc)
TENANT = "11111111-1111-4111-8111-111111111111"
TARGET = TargetReference(kind="tenant_id", value=TENANT)


class FakeProvider(LLMProvider):
    def __init__(self, contents: list[str]) -> None:
        self.contents = contents
        self.calls: list[list[dict[str, Any]]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        self.calls.append(messages)
        return LLMResponse(
            content=self.contents.pop(0),
            tool_calls=[],
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=10,
        )

    def format_tool_result(
        self,
        tool_call_id: str,
        name: str,
        content: str,
    ) -> dict[str, Any]:
        return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


def _context() -> CaseContext:
    identity = IdentityResolution(
        contact_email="owner@example.test",
        target_identity="owner@example.test",
        status=ResolutionStatus.RESOLVED,
        method=ResolutionMethod.UNIQUE_EMAIL_MEMBERSHIP,
        candidates=(WorkspaceCandidate(tenant_id=TENANT, workspace_name="Workspace"),),
        selected_tenant_id=TENANT,
        selected_workspace_name="Workspace",
        evidence_ids=("ev-identity",),
    )
    return CaseContext(
        ticket=TicketSnapshot(
            ticket_id=4001,
            generation=1,
            status="open",
            latest_event_id="event-1",
            latest_event_at=NOW,
            questions=("Why is the API request failing?",),
            form_identity=FormIdentity(email="owner@example.test"),
        ),
        built_at=NOW,
        identity=identity,
    )


def _evidence() -> EvidenceItem:
    return EvidenceItem(
        evidence_id="ev-runtime",
        call_id="call-runtime",
        source="signoz_search_logs",
        authority=EvidenceAuthority.CURRENT_AUTHORITATIVE,
        result_status=EvidenceResultStatus.SUCCESS,
        targets=(TARGET,),
        observed_at=NOW,
        supports_roles=(ClaimRole.CURRENT_STATE, ClaimRole.FAILING_BOUNDARY),
        payload={"status": 429, "error": "quota exhausted"},
    )


def _model_result() -> str:
    return json.dumps(
        {
            "answer_status": "supported",
            "proposed_claims": [
                {
                    "statement": "The current API request is being rejected because its quota is exhausted.",
                    "role": "current_state",
                    "status": "confirmed",
                    "material": True,
                    "supporting_evidence_ids": ["ev-runtime"],
                    "targets": [{"kind": "tenant_id", "value": TENANT}],
                },
            ],
            "proposed_customer_fields": [],
            "action_proposal": None,
        },
    )


async def test_investigator_returns_typed_claims_over_the_frozen_evidence_set() -> None:
    provider = FakeProvider([_model_result()])
    evidence = (_evidence(),)
    investigator = LlmCaseInvestigator(
        provider=provider,
        material=LegacyCaseMaterial(
            evidence=evidence,
            legacy_reply_type="handoff",
            legacy_reply_body="A human will review this.",
            legacy_analysis="Legacy analysis is navigation only.",
        ),
    )

    result = await investigator.investigate(_context())

    assert result.evidence == evidence
    assert result.proposal.question == "Why is the API request failing?"
    assert result.proposal.answer_status is AnswerStatus.SUPPORTED
    assert result.proposal.proposed_claims[0].public_allowed is True
    assert result.proposal.evidence_ids == ("ev-runtime",)
    prompt = str(provider.calls[0][-1]["content"])
    assert "navigation only" in prompt
    assert "cannot prove a claim" in prompt


async def test_investigator_retries_one_malformed_model_response() -> None:
    provider = FakeProvider(["not-json", _model_result()])
    investigator = LlmCaseInvestigator(
        provider=provider,
        material=LegacyCaseMaterial(evidence=(_evidence(),)),
    )

    result = await investigator.investigate(_context())

    assert result.proposal.answer_status is AnswerStatus.SUPPORTED
    assert len(provider.calls) == 2


async def test_investigator_marks_internal_claim_wording_as_not_public() -> None:
    raw = json.loads(_model_result())
    raw["proposed_claims"][0]["statement"] = (
        "inspector_exec found tenant_id in an internal database table."
    )
    provider = FakeProvider([json.dumps(raw)])
    investigator = LlmCaseInvestigator(
        provider=provider,
        material=LegacyCaseMaterial(evidence=(_evidence(),)),
    )

    result = await investigator.investigate(_context())

    assert result.proposal.proposed_claims[0].public_allowed is False


async def test_policy_reply_writer_cannot_add_facts_or_unapproved_questions() -> None:
    claim = Claim(
        claim_id="claim-current",
        statement="The current API request is rejected because its quota is exhausted.",
        role=ClaimRole.CURRENT_STATE,
        status=ClaimStatus.CONFIRMED,
        supporting_evidence_ids=("ev-runtime",),
        targets=(TARGET,),
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

    assert candidate.body == claim.statement
    assert candidate.used_claim_ids == ("claim-current",)
    assert candidate.requested_customer_fields == ()
    assert "workspace" not in candidate.body.casefold()
    assert reviewed == candidate.body

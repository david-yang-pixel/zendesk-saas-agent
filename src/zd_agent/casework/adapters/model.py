from __future__ import annotations

from hashlib import sha256
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, Field, ValidationError

from zd_agent.casework.domain.base import FrozenModel, TargetReference
from zd_agent.casework.domain.enums import (
    AnswerStatus,
    BillingBindingStatus,
    ClaimRole,
    ClaimStatus,
    OwnershipAction,
    ReasonCode,
    ResolutionStatus,
)
from zd_agent.casework.domain.models import (
    ActionProposal,
    AssessmentProposal,
    CaseAssessment,
    CaseContext,
    Claim,
    DispositionDecision,
    EvidenceItem,
    InvestigationResult,
    ReplyCandidate,
)
from zd_agent.casework.policies.privacy import claim_is_customer_safe
from zd_agent.runtime.llm.types import LLMProvider, LLMResponse


class ModelOutputError(RuntimeError):
    pass


class _ModelClaim(FrozenModel):
    statement: str = Field(min_length=1)
    role: ClaimRole
    status: ClaimStatus
    material: bool = True
    supporting_evidence_ids: tuple[str, ...] = ()
    contradicting_evidence_ids: tuple[str, ...] = ()
    targets: tuple[TargetReference, ...] = ()

    def to_claim(self, index: int) -> Claim:
        material = f"{index}\0{self.statement}\0{self.role.value}"
        claim_id = f"claim-{sha256(material.encode('utf-8')).hexdigest()[:16]}"
        return Claim(
            claim_id=claim_id,
            statement=self.statement.strip(),
            role=self.role,
            status=self.status,
            material=self.material,
            supporting_evidence_ids=self.supporting_evidence_ids,
            contradicting_evidence_ids=self.contradicting_evidence_ids,
            targets=self.targets,
            public_allowed=claim_is_customer_safe(self.statement),
        )


class _ModelAssessment(FrozenModel):
    answer_status: AnswerStatus
    proposed_claims: tuple[_ModelClaim, ...] = ()
    proposed_customer_fields: tuple[str, ...] = ()
    action_proposal: ActionProposal | None = None


def investigation_output_schema() -> dict[str, Any]:
    return _ModelAssessment.model_json_schema()


_ModelType = TypeVar("_ModelType", bound=BaseModel)


def _json_object(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        stripped = stripped[first_newline + 1 :] if first_newline >= 0 else stripped
        if stripped.endswith("```"):
            stripped = stripped[:-3].rstrip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model response does not contain a JSON object")
    return stripped[start : end + 1]


async def read_structured_output(
    *,
    provider: LLMProvider,
    system: str,
    user: str | list[dict[str, Any]],
    model_type: type[_ModelType],
    on_response: Callable[[LLMResponse], None] | None = None,
) -> _ModelType:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    last_error = ""
    for attempt in range(2):
        response = await provider.chat(messages, tools=None, temperature=0.0)
        if on_response is not None:
            on_response(response)
        content = response.content or ""
        try:
            return model_type.model_validate_json(_json_object(content))
        except (ValueError, ValidationError) as error:
            last_error = str(error)
            if attempt == 0:
                messages.extend(
                    (
                        {"role": "assistant", "content": content},
                        {
                            "role": "user",
                            "content": (
                                "The response failed schema validation. Return only one "
                                f"corrected JSON object. Validation error: {last_error[:1000]}"
                            ),
                        },
                    ),
                )
    raise ModelOutputError(
        f"model returned invalid structured output: {last_error[:1000]}"
    )


def build_investigation_result(
    *,
    context: CaseContext,
    evidence: tuple[EvidenceItem, ...],
    model_result: _ModelAssessment,
) -> InvestigationResult:
    """Convert a validated model proposal without granting it policy authority."""
    claims = tuple(
        _bind_claim_to_context(context, claim.to_claim(index))
        for index, claim in enumerate(model_result.proposed_claims, start=1)
    )
    evidence_ids = tuple(
        dict.fromkeys(
            evidence_id
            for claim in claims
            for evidence_id in (
                *claim.supporting_evidence_ids,
                *claim.contradicting_evidence_ids,
            )
        ),
    )
    if model_result.action_proposal is not None:
        evidence_ids = tuple(
            dict.fromkeys((*evidence_ids, *model_result.action_proposal.evidence_ids)),
        )
    proposal = AssessmentProposal(
        question=context.ticket.questions[-1],
        answer_status=model_result.answer_status,
        proposed_claims=claims,
        proposed_customer_fields=model_result.proposed_customer_fields,
        action_proposal=model_result.action_proposal,
        evidence_ids=evidence_ids,
    )
    return InvestigationResult(proposal=proposal, evidence=evidence)


def investigation_result_from_content(
    *,
    context: CaseContext,
    evidence: tuple[EvidenceItem, ...],
    content: str,
) -> InvestigationResult:
    """Parse the terminal JSON emitted by the bounded investigation loop."""
    try:
        model_result = _ModelAssessment.model_validate_json(_json_object(content))
    except (ValueError, ValidationError) as error:
        raise ModelOutputError(
            f"model returned invalid structured output: {str(error)[:1000]}",
        ) from error
    return build_investigation_result(
        context=context,
        evidence=evidence,
        model_result=model_result,
    )


_TENANT_BOUND_CLAIM_ROLES = {
    ClaimRole.CURRENT_STATE,
    ClaimRole.FAILING_BOUNDARY,
    ClaimRole.ROOT_CAUSE,
    ClaimRole.IMPACT,
    ClaimRole.RECOVERY,
}


def _bind_claim_to_context(context: CaseContext, claim: Claim) -> Claim:
    """Replace model-chosen targets with the code-resolved case target."""
    if claim.role is ClaimRole.VISUAL_OBSERVATION:
        return claim.model_copy(
            update={
                "targets": (
                    TargetReference(
                        kind="ticket_id",
                        value=str(context.ticket.ticket_id),
                    ),
                ),
            },
        )
    if (
        context.identity.status is not ResolutionStatus.RESOLVED
        or claim.role not in _TENANT_BOUND_CLAIM_ROLES
    ):
        return claim.model_copy(update={"targets": ()})
    targets = (
        TargetReference(
            kind="tenant_id",
            value=context.identity.selected_tenant_id,
        ),
    )
    evidence_ids = [*claim.supporting_evidence_ids, *context.identity.evidence_ids]
    if (
        claim.role is ClaimRole.CURRENT_STATE
        and context.billing is not None
        and context.billing.status is BillingBindingStatus.RESOLVED
    ):
        evidence_ids.extend(context.billing.evidence_ids)
    return claim.model_copy(
        update={
            "targets": targets,
            "supporting_evidence_ids": tuple(dict.fromkeys(evidence_ids)),
        },
    )


_FIELD_QUESTIONS = {
    "issue_description": (
        "Hi, thanks for contacting Dify Support. Could you please describe the "
        "issue you\u2019re experiencing?"
    ),
    "target_workspace_choice": (
        "We found more than one workspace associated with the email. Please tell "
        "us the name of the affected workspace."
    ),
    "target_workspace_identity": "Please provide the email associated with the affected workspace.",
    "reproduction_steps": "Please share the exact steps that reproduce the issue.",
    "request_timestamp": "Please share the approximate time of the affected request, including your time zone.",
    "affected_object_id": "Please share the affected object ID shown in your account.",
    "expected_behavior": "Please tell us what result you expected.",
    "actual_behavior": "Please tell us what happened instead.",
    "attachment_resend": "Please attach the screenshot or file again so we can inspect it.",
    "customer_authorization": "Please confirm whether you authorize this account action.",
}


_OWNER_LABELS = {
    OwnershipAction.SUPPORT: "support specialist",
    OwnershipAction.BILLING: "billing specialist",
    OwnershipAction.ENGINEERING: "engineering team",
    OwnershipAction.SRE: "site reliability team",
    OwnershipAction.SECURITY: "security team",
}
_OWNERSHIP_PARAGRAPHS = {
    ReasonCode.CUSTOMER_REQUESTED_HUMAN: (
        "You asked to continue with a support specialist. They will follow up in "
        "this ticket."
    ),
    ReasonCode.BUSINESS_APPROVAL_REQUIRED: (
        "The refund decision requires review by our billing specialist. They will "
        "record the decision and follow up in this ticket."
    ),
    ReasonCode.SENSITIVE_ACCOUNT_MUTATION_REQUIRED: (
        "This request requires a protected account action. A support specialist "
        "will review it and follow up in this ticket."
    ),
    ReasonCode.SECURITY_OR_PRIVACY_REVIEW_REQUIRED: (
        "This request requires a security or privacy review. Our security team will "
        "follow up in this ticket."
    ),
    ReasonCode.POLICY_COMMITMENT_REQUIRED: (
        "The requested commitment requires review by a support specialist. They "
        "will follow up in this ticket."
    ),
    ReasonCode.AUTOMATION_RETRY_EXHAUSTED: (
        "We couldn't complete the verification needed to answer this safely after "
        "retrying. A support specialist will continue the review and follow up in "
        "this ticket."
    ),
}
_AI_DISCLOSURE = "This reply was sent by Dify AI Agent."


class PolicyReplyWriter:
    """Render only admitted claims and policy-approved next steps."""

    async def write_reply(
        self,
        *,
        assessment: CaseAssessment,
        decision: DispositionDecision,
    ) -> ReplyCandidate:
        paragraphs = [claim.statement.strip() for claim in assessment.admitted_claims]
        paragraphs.extend(
            _FIELD_QUESTIONS[field]
            for field in decision.required_customer_fields
            if field in _FIELD_QUESTIONS
        )
        if decision.ownership_action is not OwnershipAction.NONE:
            owner = _OWNER_LABELS[decision.ownership_action]
            paragraphs.append(
                _OWNERSHIP_PARAGRAPHS.get(
                    decision.reason_code,
                    f"The remaining action requires our {owner}. "
                    "They will follow up in this ticket.",
                ),
            )
        if not paragraphs:
            raise ValueError("reply decision has no admitted customer-safe content")
        paragraphs.append(_AI_DISCLOSURE)
        return ReplyCandidate(
            body="\n\n".join(paragraphs),
            reply_kind=decision.reply_kind,
            used_claim_ids=tuple(
                claim.claim_id for claim in assessment.admitted_claims
            ),
            requested_customer_fields=decision.required_customer_fields,
            mentions_human_followup=(
                decision.ownership_action is not OwnershipAction.NONE
            ),
        )


class NoOpReplyReviewer:
    """Keep admitted v2 wording unchanged until revision is mechanically provable."""

    async def revise_wording(
        self,
        *,
        body: str,
        assessment: CaseAssessment,
        decision: DispositionDecision,
    ) -> str:
        _ = assessment, decision
        return body

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, TypeVar

from pydantic import BaseModel, Field, ValidationError

from zd_agent.casework.adapters.legacy_evidence import LegacyCaseMaterial
from zd_agent.casework.domain.base import FrozenModel, TargetReference
from zd_agent.casework.domain.enums import (
    AnswerStatus,
    ClaimRole,
    ClaimStatus,
    OwnershipAction,
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
from zd_agent.runtime.llm.types import LLMProvider


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
) -> _ModelType:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    last_error = ""
    for attempt in range(2):
        response = await provider.chat(messages, tools=None, temperature=0.0)
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
    raise ModelOutputError(f"model returned invalid structured output: {last_error[:1000]}")


def _bounded_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n[truncated for model context]"


def _evidence_for_prompt(material: LegacyCaseMaterial) -> list[dict[str, object]]:
    rendered: list[dict[str, object]] = []
    remaining = 60_000
    for item in material.evidence:
        metadata = item.model_dump(
            mode="json",
            exclude={"payload", "payload_ref"},
        )
        payload = json.dumps(item.payload, ensure_ascii=False, separators=(",", ":"))
        allowance = min(8_000, remaining)
        if allowance <= 0:
            break
        metadata["payload"] = _bounded_text(payload, allowance)
        rendered.append(metadata)
        remaining -= min(len(payload), allowance)
    return rendered


def _context_for_prompt(context: CaseContext) -> dict[str, object]:
    billing = context.billing.snapshot if context.billing else None
    return {
        "question": context.ticket.questions[-1],
        "identity": {
            "status": context.identity.status.value,
            "method": context.identity.method.value,
            "selected_tenant_id": context.identity.selected_tenant_id,
            "workspace_name": context.identity.selected_workspace_name,
            "candidate_count": len(context.identity.candidates),
        },
        "billing": billing.model_dump(mode="json") if billing else None,
        "visual_observations": [
            observation.model_dump(mode="json")
            for observation in context.visual_observations
        ],
    }


def _investigation_prompt(
    context: CaseContext,
    material: LegacyCaseMaterial,
) -> str:
    payload = {
        "case_context": _context_for_prompt(context),
        "eligible_evidence": _evidence_for_prompt(material),
        "legacy_reference": {
            "warning": "Navigation only. It cannot prove a claim or select ownership.",
            "analysis": _bounded_text(material.legacy_analysis, 12_000),
            "reply_type": material.legacy_reply_type,
            "reply": _bounded_text(material.legacy_reply_body, 6_000),
        },
        "output_schema": _ModelAssessment.model_json_schema(),
    }
    return json.dumps(payload, ensure_ascii=False)


def build_investigation_result(
    *,
    context: CaseContext,
    evidence: tuple[EvidenceItem, ...],
    model_result: _ModelAssessment,
) -> InvestigationResult:
    """Convert a validated model proposal without granting it policy authority."""
    claims = tuple(
        claim.to_claim(index)
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


class LlmCaseInvestigator:
    def __init__(
        self,
        *,
        provider: LLMProvider,
        material: LegacyCaseMaterial,
    ) -> None:
        self._provider = provider
        self._material = material

    async def investigate(self, context: CaseContext) -> InvestigationResult:
        model_result = await read_structured_output(
            provider=self._provider,
            system=(
                "You are the evidence synthesis lane of a support system. Return "
                "only JSON matching the supplied schema. Cite only eligible evidence "
                "IDs. A legacy analysis or reply is navigation, never evidence. Use "
                "confirmed only for direct current evidence; use evidence_supported "
                "for a bounded inference. Do not invent a workspace, plan, recovery, "
                "root cause, customer field, action, or commitment. Claims must be "
                "concise customer-safe English. You cannot choose an owner or handoff."
                " A tenant Billing claim must cite both case_context.identity and "
                "case_context.billing evidence and target the selected tenant."
            ),
            user=_investigation_prompt(context, self._material),
            model_type=_ModelAssessment,
        )
        return build_investigation_result(
            context=context,
            evidence=self._material.evidence,
            model_result=model_result,
        )


_FIELD_QUESTIONS = {
    "target_workspace_choice": "Please tell us which of the listed workspaces is affected.",
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
_AI_DISCLOSURE = "This reply was sent by Dify AI Support."


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
                f"The remaining action requires our {owner}. They will follow up in this ticket.",
            )
        if not paragraphs:
            raise ValueError("reply decision has no admitted customer-safe content")
        paragraphs.append(_AI_DISCLOSURE)
        return ReplyCandidate(
            body="\n\n".join(paragraphs),
            reply_kind=decision.reply_kind,
            used_claim_ids=tuple(claim.claim_id for claim in assessment.admitted_claims),
            requested_customer_fields=decision.required_customer_fields,
            mentions_human_followup=(
                decision.ownership_action is not OwnershipAction.NONE
            ),
        )


class NoOpReplyReviewer:
    """The shadow path cannot introduce facts during a wording-only pass."""

    async def revise_wording(
        self,
        *,
        body: str,
        assessment: CaseAssessment,
        decision: DispositionDecision,
    ) -> str:
        _ = assessment, decision
        return body

from __future__ import annotations

from zd_agent.casework.domain.enums import AnswerStatus, ResolutionStatus
from zd_agent.casework.domain.models import (
    AdapterFailure,
    AssessmentProposal,
    CaseContext,
    HumanActionRequest,
)
from zd_agent.casework.policies.customer_input import admit_customer_fields


def _attachment_resend_allowed(context: CaseContext) -> bool:
    if not context.ticket.attachments:
        return False
    return any(
        "could not be retrieved" in observation.uncertainty.casefold()
        for observation in context.visual_observations
    )


def admit_case_customer_fields(
    *,
    context: CaseContext,
    proposal: AssessmentProposal,
    answer_status: AnswerStatus,
    human_action: HumanActionRequest | None,
    blocking_failure: AdapterFailure | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Admit model fields and add a code-owned identity clarification if needed."""
    fields, rejected = admit_customer_fields(
        proposed_fields=proposal.proposed_customer_fields,
        identity_status=context.identity.status,
        identity_has_candidates=bool(context.identity.candidates),
        attachment_resend_allowed=_attachment_resend_allowed(context),
    )
    if (
        answer_status is not AnswerStatus.INSUFFICIENT
        or human_action is not None
        or blocking_failure is not None
    ):
        return fields, rejected

    if context.identity.status is ResolutionStatus.AMBIGUOUS or (
        context.identity.status is ResolutionStatus.CONFLICT
        and context.identity.candidates
    ):
        fields = (*fields, "target_workspace_choice")
    elif context.identity.status in {
        ResolutionStatus.CONFLICT,
        ResolutionStatus.NO_MEMBERSHIP,
    }:
        fields = (*fields, "target_workspace_identity")
    return tuple(dict.fromkeys(fields)), rejected

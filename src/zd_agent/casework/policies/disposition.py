from __future__ import annotations

from zd_agent.casework.domain.enums import (
    AnswerStatus,
    Disposition,
    OwnershipAction,
    ReasonCode,
    ReplyKind,
)
from zd_agent.casework.domain.models import (
    AdapterFailure,
    CaseAssessment,
    DispositionDecision,
    HumanActionRequest,
    RetryDirective,
)


def _evidence_ids(assessment: CaseAssessment) -> tuple[str, ...]:
    values = list(assessment.evidence_ids)
    for claim in assessment.admitted_claims:
        values.extend(claim.supporting_evidence_ids)
    if assessment.blocking_failure and assessment.blocking_failure.evidence_id:
        values.append(assessment.blocking_failure.evidence_id)
    if assessment.human_action:
        values.extend(assessment.human_action.evidence_ids)
    return tuple(dict.fromkeys(value for value in values if value))


def _human_action_decision(
    action: HumanActionRequest,
    *,
    assessment: CaseAssessment,
    evidence_ids: tuple[str, ...],
) -> DispositionDecision:
    disposition_by_owner = {
        OwnershipAction.SUPPORT: Disposition.HUMAN_ACTION_REQUIRED,
        OwnershipAction.BILLING: Disposition.HUMAN_ACTION_REQUIRED,
        OwnershipAction.ENGINEERING: Disposition.ENGINEERING_ESCALATION,
        OwnershipAction.SRE: Disposition.SRE_ESCALATION,
        OwnershipAction.SECURITY: Disposition.SECURITY_REVIEW,
    }
    reply_kind = (
        ReplyKind.ANSWER
        if assessment.answer_status in {AnswerStatus.SUPPORTED, AnswerStatus.BOUNDED}
        else ReplyKind.PROGRESS
    )
    return DispositionDecision(
        disposition=disposition_by_owner[action.ownership_action],
        reply_kind=reply_kind,
        ownership_action=action.ownership_action,
        reason_code=action.reason_code,
        evidence_ids=evidence_ids,
    )


def _failure_decision(
    failure: AdapterFailure,
    *,
    evidence_ids: tuple[str, ...],
) -> DispositionDecision:
    if failure.can_retry():
        return DispositionDecision(
            disposition=Disposition.RETRY_PENDING,
            reply_kind=ReplyKind.NONE,
            reason_code=ReasonCode.INTERNAL_RETRY_PENDING,
            evidence_ids=evidence_ids,
            retry_directive=RetryDirective(
                operation=failure.operation,
                attempt=failure.attempt,
                max_attempts=failure.max_attempts,
                retry_after_seconds=failure.retry_after_seconds,
            ),
        )
    return DispositionDecision(
        disposition=Disposition.HUMAN_ACTION_REQUIRED,
        reply_kind=ReplyKind.PROGRESS,
        ownership_action=OwnershipAction.SUPPORT,
        reason_code=ReasonCode.AUTOMATION_RETRY_EXHAUSTED,
        evidence_ids=evidence_ids,
    )


def decide_disposition(assessment: CaseAssessment) -> DispositionDecision:
    """Choose disposition without accepting a model-authored handoff shortcut."""
    evidence_ids = _evidence_ids(assessment)
    if assessment.human_action is not None:
        return _human_action_decision(
            assessment.human_action,
            assessment=assessment,
            evidence_ids=evidence_ids,
        )
    if assessment.answer_status in {AnswerStatus.SUPPORTED, AnswerStatus.BOUNDED}:
        return DispositionDecision(
            disposition=Disposition.RESOLVED,
            reply_kind=(
                ReplyKind.CLOSE if assessment.close_after_reply else ReplyKind.ANSWER
            ),
            reason_code=(
                ReasonCode.BOUNDED_AUTHORITATIVE_ANSWER
                if assessment.answer_status is AnswerStatus.BOUNDED
                else ReasonCode.RESOLVED_WITH_EVIDENCE
            ),
            evidence_ids=evidence_ids,
        )
    if assessment.blocking_failure is not None:
        return _failure_decision(
            assessment.blocking_failure,
            evidence_ids=evidence_ids,
        )
    if assessment.customer_only_fields:
        return DispositionDecision(
            disposition=Disposition.AWAITING_CUSTOMER,
            reply_kind=ReplyKind.ASK,
            reason_code=ReasonCode.CUSTOMER_INPUT_REQUIRED,
            evidence_ids=evidence_ids,
            required_customer_fields=assessment.customer_only_fields,
        )
    raise ValueError(
        "assessment has no valid disposition: add admitted claims, a typed internal "
        "failure, exact customer-only fields, or a structured human action",
    )

from zd_agent.casework.adapters.model import PolicyReplyWriter
from zd_agent.casework.domain.enums import (
    AnswerStatus,
    Disposition,
    OwnershipAction,
    ReasonCode,
    ReplyKind,
)
from zd_agent.casework.domain.models import (
    CaseAssessment,
    DispositionDecision,
    HumanActionRequest,
)


def _action(
    owner: OwnershipAction,
    reason: ReasonCode,
) -> HumanActionRequest:
    return HumanActionRequest(
        ownership_action=owner,
        reason_code=reason,
        summary="A verified action remains.",
        customer_impact="The customer is waiting for the action.",
        completed_checks=("Verified the available account data",),
        unresolved_action="Complete the remaining action.",
        acceptance_criteria=("The action is recorded",),
        evidence_ids=("ev-current",),
    )


async def test_exhausted_automation_reply_explains_retry_without_requesting_identity() -> (
    None
):
    action = _action(
        OwnershipAction.SUPPORT,
        ReasonCode.AUTOMATION_RETRY_EXHAUSTED,
    )
    assessment = CaseAssessment(
        question="What plan is this workspace using?",
        answer_status=AnswerStatus.INSUFFICIENT,
        human_action=action,
    )
    decision = DispositionDecision(
        disposition=Disposition.HUMAN_ACTION_REQUIRED,
        reply_kind=ReplyKind.PROGRESS,
        ownership_action=OwnershipAction.SUPPORT,
        reason_code=ReasonCode.AUTOMATION_RETRY_EXHAUSTED,
        evidence_ids=("ev-current",),
    )

    candidate = await PolicyReplyWriter().write_reply(
        assessment=assessment,
        decision=decision,
    )

    assert candidate.body.startswith(
        "We couldn't complete the verification needed to answer this safely after retrying."
    )
    assert "support specialist" in candidate.body
    assert "workspace id" not in candidate.body.casefold()


async def test_exhausted_automation_reply_uses_japanese_customer_language() -> None:
    action = _action(
        OwnershipAction.SUPPORT,
        ReasonCode.AUTOMATION_RETRY_EXHAUSTED,
    )
    assessment = CaseAssessment(
        question="日本語で送っても大丈夫ですか？",
        reply_language="ja",
        answer_status=AnswerStatus.INSUFFICIENT,
        human_action=action,
    )
    decision = DispositionDecision(
        disposition=Disposition.HUMAN_ACTION_REQUIRED,
        reply_kind=ReplyKind.PROGRESS,
        ownership_action=OwnershipAction.SUPPORT,
        reason_code=ReasonCode.AUTOMATION_RETRY_EXHAUSTED,
        evidence_ids=("ev-current",),
    )

    candidate = await PolicyReplyWriter().write_reply(
        assessment=assessment,
        decision=decision,
    )

    assert candidate.language == "ja"
    assert candidate.body.startswith("必要な確認を再試行しましたが")
    assert "サポート担当者" in candidate.body
    assert "This reply was sent" not in candidate.body


async def test_business_approval_reply_names_the_decision_not_a_generic_handoff() -> (
    None
):
    action = _action(
        OwnershipAction.BILLING,
        ReasonCode.BUSINESS_APPROVAL_REQUIRED,
    )
    assessment = CaseAssessment(
        question="Please approve a refund for this annual subscription.",
        answer_status=AnswerStatus.INSUFFICIENT,
        human_action=action,
    )
    decision = DispositionDecision(
        disposition=Disposition.HUMAN_ACTION_REQUIRED,
        reply_kind=ReplyKind.PROGRESS,
        ownership_action=OwnershipAction.BILLING,
        reason_code=ReasonCode.BUSINESS_APPROVAL_REQUIRED,
        evidence_ids=("ev-current",),
    )

    candidate = await PolicyReplyWriter().write_reply(
        assessment=assessment,
        decision=decision,
    )

    assert "refund decision" in candidate.body.casefold()
    assert "billing specialist" in candidate.body.casefold()
    assert "handoff" not in candidate.body.casefold()

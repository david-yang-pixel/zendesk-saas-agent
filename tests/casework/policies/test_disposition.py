from zd_agent.casework.domain.enums import (
    AdapterErrorCode,
    AnswerStatus,
    ClaimRole,
    ClaimStatus,
    Disposition,
    OwnershipAction,
    ReasonCode,
    ReplyKind,
)
from zd_agent.casework.domain.models import (
    AdapterFailure,
    CaseAssessment,
    Claim,
    HumanActionRequest,
)
from zd_agent.casework.policies.disposition import decide_disposition


def _claim(role: ClaimRole = ClaimRole.CURRENT_STATE) -> Claim:
    return Claim(
        claim_id=f"claim-{role.value}",
        statement="Verified customer-safe fact.",
        role=role,
        status=ClaimStatus.CONFIRMED,
        supporting_evidence_ids=("ev-1",),
        public_allowed=True,
    )


def test_bounded_no_published_timeline_answer_resolves_without_ownership() -> None:
    decision = decide_disposition(
        CaseAssessment(
            question="When will this ship on Cloud?",
            answer_status=AnswerStatus.BOUNDED,
            admitted_claims=(_claim(ClaimRole.LIMITATION),),
        ),
    )

    assert decision.disposition is Disposition.RESOLVED
    assert decision.reply_kind is ReplyKind.ANSWER
    assert decision.ownership_action is OwnershipAction.NONE
    assert decision.reason_code is ReasonCode.BOUNDED_AUTHORITATIVE_ANSWER


def test_customer_question_contains_only_proven_customer_owned_fields() -> None:
    decision = decide_disposition(
        CaseAssessment(
            question="Which of two workspaces is affected?",
            answer_status=AnswerStatus.INSUFFICIENT,
            customer_only_fields=("target_workspace_choice",),
            evidence_ids=("ev-memberships",),
        ),
    )

    assert decision.disposition is Disposition.AWAITING_CUSTOMER
    assert decision.required_customer_fields == ("target_workspace_choice",)
    assert decision.ownership_action is OwnershipAction.NONE


def test_retryable_timeout_stays_internal_until_budget_is_exhausted() -> None:
    decision = decide_disposition(
        CaseAssessment(
            question="What is my subscription?",
            answer_status=AnswerStatus.INSUFFICIENT,
            blocking_failure=AdapterFailure(
                operation="billing.lookup",
                code=AdapterErrorCode.TIMEOUT,
                attempt=1,
                max_attempts=3,
                retry_after_seconds=2,
                evidence_id="ev-timeout",
            ),
        ),
    )

    assert decision.disposition is Disposition.RETRY_PENDING
    assert decision.reply_kind is ReplyKind.NONE
    assert decision.ownership_action is OwnershipAction.NONE
    assert decision.retry_directive is not None


def test_answerable_case_is_resolved_even_if_an_unneeded_tool_failed() -> None:
    decision = decide_disposition(
        CaseAssessment(
            question="Is a release date published?",
            answer_status=AnswerStatus.BOUNDED,
            admitted_claims=(_claim(ClaimRole.LIMITATION),),
            blocking_failure=AdapterFailure(
                operation="github.search",
                code=AdapterErrorCode.TIMEOUT,
                attempt=1,
                max_attempts=3,
                retry_after_seconds=2,
                evidence_id="ev-timeout",
            ),
        ),
    )

    assert decision.disposition is Disposition.RESOLVED
    assert decision.ownership_action is OwnershipAction.NONE


def test_exhausted_automation_failure_creates_specific_support_ownership() -> None:
    decision = decide_disposition(
        CaseAssessment(
            question="What is my subscription?",
            answer_status=AnswerStatus.INSUFFICIENT,
            blocking_failure=AdapterFailure(
                operation="billing.lookup",
                code=AdapterErrorCode.TIMEOUT,
                attempt=3,
                max_attempts=3,
                evidence_id="ev-timeout",
            ),
        ),
    )

    assert decision.disposition is Disposition.HUMAN_ACTION_REQUIRED
    assert decision.reply_kind is ReplyKind.PROGRESS
    assert decision.ownership_action is OwnershipAction.SUPPORT
    assert decision.reason_code is ReasonCode.AUTOMATION_RETRY_EXHAUSTED


def test_ambiguous_write_is_never_retried() -> None:
    decision = decide_disposition(
        CaseAssessment(
            question="Was the external action completed?",
            answer_status=AnswerStatus.INSUFFICIENT,
            blocking_failure=AdapterFailure(
                operation="external.write",
                code=AdapterErrorCode.AMBIGUOUS_WRITE,
                attempt=1,
                max_attempts=3,
                evidence_id="ev-write",
            ),
        ),
    )

    assert decision.disposition is Disposition.HUMAN_ACTION_REQUIRED
    assert decision.retry_directive is None


def test_confirmed_defect_answers_customer_and_routes_engineering_packet() -> None:
    decision = decide_disposition(
        CaseAssessment(
            question="Why is retrieval failing?",
            answer_status=AnswerStatus.SUPPORTED,
            admitted_claims=(
                _claim(ClaimRole.FAILING_BOUNDARY),
                _claim(ClaimRole.ROOT_CAUSE),
            ),
            human_action=HumanActionRequest(
                ownership_action=OwnershipAction.ENGINEERING,
                reason_code=ReasonCode.CONFIRMED_PLATFORM_DEFECT,
                summary="Confirmed defect in the current customer path.",
                customer_impact="Retrieval fails for the affected dataset.",
                completed_checks=("Verified current failure", "Aligned source"),
                unresolved_action="Repair the failing implementation.",
                acceptance_criteria=("Customer path succeeds",),
                evidence_ids=("ev-1",),
            ),
        ),
    )

    assert decision.disposition is Disposition.ENGINEERING_ESCALATION
    assert decision.reply_kind is ReplyKind.ANSWER
    assert decision.ownership_action is OwnershipAction.ENGINEERING


def test_business_approval_routes_billing_without_claiming_completion() -> None:
    decision = decide_disposition(
        CaseAssessment(
            question="Can you approve this refund?",
            answer_status=AnswerStatus.INSUFFICIENT,
            human_action=HumanActionRequest(
                ownership_action=OwnershipAction.BILLING,
                reason_code=ReasonCode.BUSINESS_APPROVAL_REQUIRED,
                summary="Refund eligibility is verified; approval remains.",
                customer_impact="Customer is waiting for a billing decision.",
                completed_checks=("Verified owner", "Verified subscription"),
                unresolved_action="Approve or reject the refund.",
                acceptance_criteria=("Decision recorded",),
                evidence_ids=("ev-owner", "ev-billing"),
            ),
        ),
    )

    assert decision.disposition is Disposition.HUMAN_ACTION_REQUIRED
    assert decision.ownership_action is OwnershipAction.BILLING
    assert decision.reason_code is ReasonCode.BUSINESS_APPROVAL_REQUIRED


def test_unstructured_uncertainty_cannot_become_generic_handoff() -> None:
    assessment = CaseAssessment(
        question="What happened?",
        answer_status=AnswerStatus.INSUFFICIENT,
    )

    try:
        decide_disposition(assessment)
    except ValueError as error:
        assert "no valid disposition" in str(error)
    else:
        raise AssertionError("unstructured uncertainty must fail closed")

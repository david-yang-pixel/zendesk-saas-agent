import pytest

from zd_agent.casework.application.compose_reply import ReplyComposer, ReplyQualityError
from zd_agent.casework.domain.enums import (
    AnswerStatus,
    ClaimRole,
    ClaimStatus,
    Disposition,
    OwnershipAction,
    ReasonCode,
    ReplyKind,
)
from zd_agent.casework.domain.models import (
    CaseAssessment,
    Claim,
    DispositionDecision,
    ReplyCandidate,
)


def _claim(claim_id: str = "claim-current") -> Claim:
    return Claim(
        claim_id=claim_id,
        statement="The subscription is active on the Professional plan.",
        role=ClaimRole.CURRENT_STATE,
        status=ClaimStatus.CONFIRMED,
        supporting_evidence_ids=("ev-billing",),
        public_allowed=True,
    )


def _assessment() -> CaseAssessment:
    return CaseAssessment(
        question="What subscription is this workspace using?",
        answer_status=AnswerStatus.SUPPORTED,
        admitted_claims=(_claim(),),
    )


def _decision() -> DispositionDecision:
    return DispositionDecision(
        disposition=Disposition.RESOLVED,
        reply_kind=ReplyKind.ANSWER,
        reason_code=ReasonCode.RESOLVED_WITH_EVIDENCE,
        evidence_ids=("ev-billing",),
    )


class FakeWriter:
    def __init__(self, candidate: ReplyCandidate) -> None:
        self._candidate = candidate

    async def write_reply(
        self,
        *,
        assessment: CaseAssessment,
        decision: DispositionDecision,
    ) -> ReplyCandidate:
        return self._candidate


class FakeReviewer:
    def __init__(self, revised_body: str) -> None:
        self._revised_body = revised_body

    async def revise_wording(
        self,
        *,
        body: str,
        assessment: CaseAssessment,
        decision: DispositionDecision,
    ) -> str:
        return self._revised_body


async def test_composer_rejects_unverifiable_wording_changes() -> None:
    candidate = ReplyCandidate(
        body="Your Professional subscription is active.",
        language="en",
        reply_kind=ReplyKind.ANSWER,
        used_claim_ids=("claim-current",),
    )
    composer = ReplyComposer(
        writer=FakeWriter(candidate),
        reviewer=FakeReviewer(
            "Your workspace currently has an active Professional subscription.",
        ),
    )

    try:
        await composer.compose(
            assessment=_assessment(),
            decision=_decision(),
        )
    except ReplyQualityError as error:
        assert "fact-preserving" in str(error)
    else:
        raise AssertionError("unverified wording changes must fail closed")


async def test_noop_review_preserves_claim_and_decision_metadata() -> None:
    candidate = ReplyCandidate(
        body="Your Professional subscription is active.",
        language="en",
        reply_kind=ReplyKind.ANSWER,
        used_claim_ids=("claim-current",),
    )
    composer = ReplyComposer(
        writer=FakeWriter(candidate),
        reviewer=FakeReviewer(candidate.body),
    )

    draft = await composer.compose(
        assessment=_assessment(),
        decision=_decision(),
    )

    assert draft.body == candidate.body
    assert draft.used_claim_ids == ("claim-current",)
    assert draft.reply_kind is ReplyKind.ANSWER
    assert draft.reviewed is True


async def test_missing_material_claim_is_rejected_before_review() -> None:
    candidate = ReplyCandidate(
        body="We checked the workspace.",
        language="en",
        reply_kind=ReplyKind.ANSWER,
        used_claim_ids=(),
    )
    composer = ReplyComposer(
        writer=FakeWriter(candidate),
        reviewer=FakeReviewer(candidate.body),
    )

    try:
        await composer.compose(assessment=_assessment(), decision=_decision())
    except ReplyQualityError as error:
        assert "required material claims" in str(error)
    else:
        raise AssertionError("reply without the admitted claim must fail")


async def test_invented_claim_id_is_rejected() -> None:
    candidate = ReplyCandidate(
        body="The issue is fixed.",
        language="en",
        reply_kind=ReplyKind.ANSWER,
        used_claim_ids=("invented-root-cause",),
    )
    composer = ReplyComposer(
        writer=FakeWriter(candidate),
        reviewer=FakeReviewer(candidate.body),
    )

    try:
        await composer.compose(assessment=_assessment(), decision=_decision())
    except ReplyQualityError as error:
        assert "unknown claim" in str(error)
    else:
        raise AssertionError("invented claim ID must fail")


async def test_reply_without_ownership_cannot_promise_human_followup() -> None:
    candidate = ReplyCandidate(
        body="A human support engineer will follow up in this ticket.",
        language="en",
        reply_kind=ReplyKind.ANSWER,
        used_claim_ids=("claim-current",),
        mentions_human_followup=True,
    )
    composer = ReplyComposer(
        writer=FakeWriter(candidate),
        reviewer=FakeReviewer(candidate.body),
    )

    try:
        await composer.compose(assessment=_assessment(), decision=_decision())
    except ReplyQualityError as error:
        assert "human follow-up" in str(error)
    else:
        raise AssertionError("human promise without ownership must fail")


@pytest.mark.parametrize(
    "body",
    (
        "A support specialist will follow up in this ticket.",
        "Our team will follow up in this ticket.",
        "A colleague will take over from here.",
        "We will have someone contact you.",
        "I've escalated this to our engineering team.",
        "Our engineers are looking into this.",
        "A specialist has been assigned to your case.",
        "Your case is now with our billing team.",
        "We'll get back to you after an engineer checks this.",
        "This has been forwarded to the SRE team.",
        "Expect a response from our team shortly.",
        "A colleague will reach out shortly.",
    ),
)
async def test_reply_body_cannot_hide_human_promise_without_ownership(body) -> None:
    candidate = ReplyCandidate(
        body=body,
        language="en",
        reply_kind=ReplyKind.ANSWER,
        used_claim_ids=("claim-current",),
        mentions_human_followup=False,
    )
    composer = ReplyComposer(
        writer=FakeWriter(candidate),
        reviewer=FakeReviewer(candidate.body),
    )

    with pytest.raises(ReplyQualityError, match="human follow-up"):
        await composer.compose(assessment=_assessment(), decision=_decision())


async def test_customer_ask_fields_must_exactly_match_decision() -> None:
    assessment = CaseAssessment(
        question="Which workspace is affected?",
        answer_status=AnswerStatus.INSUFFICIENT,
        customer_only_fields=("target_workspace_choice",),
        evidence_ids=("ev-memberships",),
    )
    decision = DispositionDecision(
        disposition=Disposition.AWAITING_CUSTOMER,
        reply_kind=ReplyKind.ASK,
        reason_code=ReasonCode.CUSTOMER_INPUT_REQUIRED,
        evidence_ids=("ev-memberships",),
        required_customer_fields=("target_workspace_choice",),
    )
    candidate = ReplyCandidate(
        body="Please confirm which of the two listed workspaces is affected.",
        language="en",
        reply_kind=ReplyKind.ASK,
        requested_customer_fields=("workspace_id",),
    )
    composer = ReplyComposer(
        writer=FakeWriter(candidate),
        reviewer=FakeReviewer(candidate.body),
    )

    try:
        await composer.compose(assessment=assessment, decision=decision)
    except ReplyQualityError as error:
        assert "customer fields" in str(error)
    else:
        raise AssertionError("extra customer field must fail")


async def test_internal_evidence_terms_are_rejected_after_review() -> None:
    candidate = ReplyCandidate(
        body="Your Professional subscription is active.",
        language="en",
        reply_kind=ReplyKind.ANSWER,
        used_claim_ids=("claim-current",),
    )
    composer = ReplyComposer(
        writer=FakeWriter(candidate),
        reviewer=FakeReviewer(
            "The sql_query for tenant_id tenant-1 proves your plan is active.",
        ),
    )

    try:
        await composer.compose(assessment=_assessment(), decision=_decision())
    except ReplyQualityError as error:
        assert "internal evidence" in str(error)
    else:
        raise AssertionError("internal implementation details must fail")


async def test_reviewer_cannot_introduce_unapproved_workspace_id_question() -> None:
    candidate = ReplyCandidate(
        body="Your Professional subscription is active.",
        language="en",
        reply_kind=ReplyKind.ANSWER,
        used_claim_ids=("claim-current",),
    )
    composer = ReplyComposer(
        writer=FakeWriter(candidate),
        reviewer=FakeReviewer("Please provide your Workspace ID."),
    )

    try:
        await composer.compose(assessment=_assessment(), decision=_decision())
    except ReplyQualityError as error:
        assert "workspace identity" in str(error)
    else:
        raise AssertionError("reviewer-introduced identity ask must fail")


@pytest.mark.parametrize(
    "body",
    (
        "What is your Workspace ID?",
        "Tell me your workspace name.",
        "I need your Workspace ID to continue.",
        "Let us know which workspace is affected.",
        "We need you to identify the affected workspace.",
        "Name the workspace where this happens.",
        "Share the workspace URL used for this request.",
        "The affected workspace still needs to be identified.",
    ),
)
async def test_resolved_reply_cannot_rephrase_workspace_identity_question(body) -> None:
    candidate = ReplyCandidate(
        body=body,
        language="en",
        reply_kind=ReplyKind.ANSWER,
        used_claim_ids=("claim-current",),
    )
    composer = ReplyComposer(
        writer=FakeWriter(candidate),
        reviewer=FakeReviewer(candidate.body),
    )

    with pytest.raises(ReplyQualityError, match="workspace identity"):
        await composer.compose(assessment=_assessment(), decision=_decision())


async def test_workspace_recovery_instruction_is_not_misread_as_identity_request() -> (
    None
):
    candidate = ReplyCandidate(
        body=(
            "Please run one request with the Dify-managed provider in this "
            "workspace; we can then verify that execution."
        ),
        language="en",
        reply_kind=ReplyKind.ANSWER,
        used_claim_ids=("claim-current",),
    )
    composer = ReplyComposer(
        writer=FakeWriter(candidate),
        reviewer=FakeReviewer(candidate.body),
    )

    result = await composer.compose(assessment=_assessment(), decision=_decision())

    assert result.body == candidate.body


async def test_engineering_ownership_may_be_described_without_changing_it() -> None:
    assessment = CaseAssessment(
        question="Why is retrieval failing?",
        answer_status=AnswerStatus.SUPPORTED,
        admitted_claims=(_claim(),),
    )
    decision = DispositionDecision(
        disposition=Disposition.ENGINEERING_ESCALATION,
        reply_kind=ReplyKind.ANSWER,
        ownership_action=OwnershipAction.ENGINEERING,
        reason_code=ReasonCode.CONFIRMED_PLATFORM_DEFECT,
        evidence_ids=("ev-billing",),
    )
    candidate = ReplyCandidate(
        body="We confirmed the issue and our engineering team will follow up.",
        language="en",
        reply_kind=ReplyKind.ANSWER,
        used_claim_ids=("claim-current",),
        mentions_human_followup=True,
    )
    composer = ReplyComposer(
        writer=FakeWriter(candidate),
        reviewer=FakeReviewer(candidate.body),
    )

    draft = await composer.compose(assessment=assessment, decision=decision)

    assert draft.ownership_action is OwnershipAction.ENGINEERING
    assert draft.mentions_human_followup is True

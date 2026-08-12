from zd_agent.casework.domain.enums import (
    AnswerStatus,
    ClaimRole,
    ClaimStatus,
    InvestigationScenario,
)
from zd_agent.casework.domain.models import Claim
from zd_agent.casework.policies.investigation_completion import (
    classify_investigation_scenario,
    enforce_investigation_completion,
    missing_completion_roles,
)


QUESTION = (
    "Tengo documentos atascados en la base de conocimiento. "
    "Ahora están en pausa y no sé qué falla."
)


def _claim(role: ClaimRole) -> Claim:
    return Claim(
        claim_id=f"claim-{role.value}",
        statement=f"Safe statement for {role.value}.",
        role=role,
        status=ClaimStatus.CONFIRMED,
        supporting_evidence_ids=("ev-current",),
        public_allowed=True,
    )


def test_stuck_document_route_requires_state_and_failing_boundary() -> None:
    assert classify_investigation_scenario(QUESTION) is (
        InvestigationScenario.ASYNC_PROCESSING
    )

    claims = (
        _claim(ClaimRole.CURRENT_STATE),
        _claim(ClaimRole.PRODUCT_FACT),
    )

    assert missing_completion_roles(QUESTION, claims) == (ClaimRole.FAILING_BOUNDARY,)
    assert (
        enforce_investigation_completion(
            question=QUESTION,
            answer_status=AnswerStatus.BOUNDED,
            admitted_claims=claims,
        )
        is AnswerStatus.INSUFFICIENT
    )


def test_stuck_document_route_can_finish_after_boundary_is_evidenced() -> None:
    claims = (
        _claim(ClaimRole.CURRENT_STATE),
        _claim(ClaimRole.FAILING_BOUNDARY),
    )

    assert (
        enforce_investigation_completion(
            question=QUESTION,
            answer_status=AnswerStatus.BOUNDED,
            admitted_claims=claims,
        )
        is AnswerStatus.BOUNDED
    )

from __future__ import annotations

from zd_agent.casework.domain.models import (
    CaseAssessment,
    DispositionDecision,
    ReplyCandidate,
    ReplyDraft,
)
from zd_agent.casework.policies.reply_quality import evaluate_reply_quality
from zd_agent.casework.ports.reply import ReplyReviewer, ReplyWriter


class ReplyQualityError(ValueError):
    pass


class ReplyComposer:
    def __init__(self, *, writer: ReplyWriter, reviewer: ReplyReviewer) -> None:
        self._writer = writer
        self._reviewer = reviewer

    @staticmethod
    def _require_quality(
        candidate: ReplyCandidate,
        *,
        assessment: CaseAssessment,
        decision: DispositionDecision,
    ) -> None:
        result = evaluate_reply_quality(
            candidate=candidate,
            assessment=assessment,
            decision=decision,
        )
        if not result.accepted:
            raise ReplyQualityError("; ".join(result.reasons))

    async def compose(
        self,
        *,
        assessment: CaseAssessment,
        decision: DispositionDecision,
    ) -> ReplyDraft:
        candidate = await self._writer.write_reply(
            assessment=assessment,
            decision=decision,
        )
        self._require_quality(
            candidate,
            assessment=assessment,
            decision=decision,
        )
        revised_body = await self._reviewer.revise_wording(
            body=candidate.body,
            assessment=assessment,
            decision=decision,
        )
        reviewed = candidate.model_copy(update={"body": revised_body.strip()})
        self._require_quality(
            reviewed,
            assessment=assessment,
            decision=decision,
        )
        return ReplyDraft(
            body=reviewed.body,
            language=reviewed.language,
            reply_kind=decision.reply_kind,
            disposition=decision.disposition,
            ownership_action=decision.ownership_action,
            reason_code=decision.reason_code,
            used_claim_ids=reviewed.used_claim_ids,
            requested_customer_fields=reviewed.requested_customer_fields,
            mentions_human_followup=reviewed.mentions_human_followup,
            reviewed=True,
        )

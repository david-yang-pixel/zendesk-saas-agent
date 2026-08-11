from __future__ import annotations

from typing import Protocol

from zd_agent.casework.domain.models import (
    CaseAssessment,
    DispositionDecision,
    ReplyCandidate,
)


class ReplyWriter(Protocol):
    async def write_reply(
        self,
        *,
        assessment: CaseAssessment,
        decision: DispositionDecision,
    ) -> ReplyCandidate: ...


class ReplyReviewer(Protocol):
    async def revise_wording(
        self,
        *,
        body: str,
        assessment: CaseAssessment,
        decision: DispositionDecision,
    ) -> str: ...

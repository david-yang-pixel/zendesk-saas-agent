from __future__ import annotations

from datetime import datetime

from pydantic import model_validator

from zd_agent.casework.domain.base import FrozenModel
from zd_agent.casework.domain.context import CaseContext
from zd_agent.casework.domain.decision import DispositionDecision, ReplyDraft
from zd_agent.casework.domain.enums import ExecutionMode, ReplyKind
from zd_agent.casework.domain.evidence import AssessmentBuild, InvestigationResult


class CaseworkRun(FrozenModel):
    execution_mode: ExecutionMode
    started_at: datetime
    finished_at: datetime
    context: CaseContext
    investigation: InvestigationResult
    assessment_build: AssessmentBuild
    decision: DispositionDecision
    reply_draft: ReplyDraft | None = None
    delivery_attempted: bool = False

    @model_validator(mode="after")
    def _validate_run(self) -> CaseworkRun:
        if self.started_at.tzinfo is None or self.finished_at.tzinfo is None:
            raise ValueError("casework run timestamps must be timezone-aware")
        if self.finished_at < self.started_at:
            raise ValueError("casework run cannot finish before it starts")
        if self.decision.reply_kind is ReplyKind.NONE and self.reply_draft is not None:
            raise ValueError("no-reply decision cannot carry a reply draft")
        if self.decision.reply_kind is not ReplyKind.NONE and self.reply_draft is None:
            raise ValueError("customer-visible decision requires a reply draft")
        return self

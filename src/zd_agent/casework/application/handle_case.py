from __future__ import annotations

from zd_agent.casework.application.assess_case import build_case_assessment
from zd_agent.casework.application.build_context import CaseContextBuilder
from zd_agent.casework.application.compose_reply import ReplyComposer
from zd_agent.casework.domain.enums import ExecutionMode, ReplyKind
from zd_agent.casework.domain.models import (
    CaseworkRun,
    TicketSnapshot,
    VisualObservation,
)
from zd_agent.casework.policies.disposition import decide_disposition
from zd_agent.casework.ports.investigation import CaseInvestigator
from zd_agent.casework.ports.readers import Clock


class CaseworkOrchestrator:
    """Run the complete casework decision path without owning delivery."""

    def __init__(
        self,
        *,
        context_builder: CaseContextBuilder,
        investigator: CaseInvestigator,
        reply_composer: ReplyComposer,
        clock: Clock,
    ) -> None:
        self._context_builder = context_builder
        self._investigator = investigator
        self._reply_composer = reply_composer
        self._clock = clock

    async def run(
        self,
        *,
        ticket: TicketSnapshot,
        billing_required: bool,
        automation_attempt: int = 1,
        visual_observations: tuple[VisualObservation, ...] = (),
        execution_mode: ExecutionMode = ExecutionMode.CASEWORK,
    ) -> CaseworkRun:
        started_at = self._clock.now()
        context = await self._context_builder.build(
            ticket,
            billing_required=billing_required,
            automation_attempt=automation_attempt,
            visual_observations=visual_observations,
        )
        investigation = await self._investigator.investigate(context)
        assessment_build = build_case_assessment(
            context=context,
            proposal=investigation.proposal,
            evidence=investigation.evidence,
            as_of=self._clock.now(),
        )
        decision = decide_disposition(assessment_build.assessment)
        reply_draft = None
        if decision.reply_kind is not ReplyKind.NONE:
            reply_draft = await self._reply_composer.compose(
                assessment=assessment_build.assessment,
                decision=decision,
            )
        return CaseworkRun(
            execution_mode=execution_mode,
            started_at=started_at,
            finished_at=self._clock.now(),
            context=context,
            investigation=investigation,
            assessment_build=assessment_build,
            decision=decision,
            reply_draft=reply_draft,
            delivery_attempted=False,
        )

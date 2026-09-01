"""Ticket-id driven Agent runtime executed inside one ephemeral sandbox."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from casework_agent_core.context import ContextCheckpoint, ContextGovernance
from casework_agent_core.domain import (
    ActiveQuestion,
    TicketSnapshot,
    resolve_active_question,
)
from casework_agent_core.evidence import EvidenceGate
from casework_agent_core.skills import LoadedSkill, SkillLoader
from casework_agent_core.subagents import SubagentReport, SubagentTask
from casework_agent_core.todo import TodoPlan
from casework_tool_sdk.loop import ToolLoop
from casework_tool_sdk.registry import ToolExecutionContext, ToolRegistry
from contracts.outcome import Outcome, OutcomeKind
from contracts.runtime import RuntimeBootstrap
from contracts.telemetry import (
    ActiveQuestionTelemetry,
    ArtifactReference,
    ContextCheckpointTelemetry,
    SandboxResultTelemetry,
    SubagentTaskTelemetry,
    TodoTelemetry,
    ToolCallTelemetry,
)


class ZendeskReader(Protocol):
    async def fetch_ticket(self, ticket_id: int) -> TicketSnapshot: ...


class CaseworkModel(Protocol):
    async def plan(
        self,
        snapshot: TicketSnapshot,
        question: ActiveQuestion,
        skill: LoadedSkill,
        available_tools: tuple[str, ...],
    ) -> TodoPlan: ...

    async def compose_answer(
        self,
        snapshot: TicketSnapshot,
        question: ActiveQuestion,
        evidence_ids: tuple[str, ...],
    ) -> str: ...


class SubagentDispatcher(Protocol):
    async def dispatch(self, slot_id: str, task: SubagentTask) -> SubagentReport: ...


@dataclass(frozen=True, slots=True)
class TodoSummary:
    completed: int
    pending: int
    failed: int


class RuntimeOutcome(Outcome):
    """Public terminal contract with compact execution summary."""

    todo_summary: TodoSummary
    checkpoint: ContextCheckpoint | None = None


class SandboxAgent:
    def __init__(
        self,
        *,
        zendesk: ZendeskReader,
        model: CaseworkModel,
        tools: ToolRegistry,
        skill_loader: SkillLoader | None = None,
        context_window_tokens: int = 100_000,
        max_tool_rounds: int = 8,
        subagent_orchestrator: SubagentDispatcher | None = None,
        sandbox_workdir: Path | None = None,
        runtime_bootstrap: RuntimeBootstrap | None = None,
    ) -> None:
        self._zendesk = zendesk
        self._model = model
        self._tools = tools
        self._subagent_orchestrator = subagent_orchestrator
        self._sandbox_workdir = sandbox_workdir
        self._runtime_bootstrap = runtime_bootstrap
        skills_root = Path(__file__).resolve().parents[4] / "skills"
        self._skills = skill_loader or SkillLoader(skills_root)
        self._context_window_tokens = context_window_tokens
        self._max_tool_rounds = max_tool_rounds

    async def run(self, *, ticket_id: int, session_id: str) -> RuntimeOutcome:
        bootstrap = self._runtime_bootstrap
        context_window_tokens = (
            bootstrap.context_window_tokens
            if bootstrap is not None
            else self._context_window_tokens
        )
        max_tool_rounds = (
            bootstrap.max_tool_rounds
            if bootstrap is not None
            else self._max_tool_rounds
        )
        context = ContextGovernance(window_tokens=context_window_tokens)
        try:
            snapshot = await self._zendesk.fetch_ticket(ticket_id)
            if snapshot.ticket_id != ticket_id:
                raise ValueError("Zendesk returned a different ticket snapshot")
            question = resolve_active_question(snapshot)
            context.add(
                "ticket "
                f"{snapshot.ticket_id} event {snapshot.latest_customer_cursor}",
                tokens=8,
            )
            context.add(
                question.original_request,
                tokens=max(1, len(question.original_request) // 4),
            )
            skill_name = (
                bootstrap.enabled_skills[0]
                if bootstrap is not None and bootstrap.enabled_skills
                else "core-casework"
            )
            skill = self._skills.load(skill_name)
            runtime_tools = self._tools
            if bootstrap is not None:
                runtime_tools = self._tools.allowlist(bootstrap.enabled_tools)
            plan = await self._model.plan(
                snapshot,
                question,
                skill,
                runtime_tools.names(),
            )
            gate = EvidenceGate()
            tool_rounds = 0
            tool_telemetry: list[ToolCallTelemetry] = []
            subagent_telemetry: list[SubagentTaskTelemetry] = []
            while tool_rounds < max_tool_rounds:
                task = plan.next_ready()
                if task is None:
                    break
                if task.assigned_to != "root":
                    if self._subagent_orchestrator is None:
                        return self._outcome(
                            session_id=session_id,
                            ticket_id=ticket_id,
                            kind=OutcomeKind.FAILED,
                            reason=(
                                "Todo requires child slot but no subagent "
                                "orchestrator is configured"
                            ),
                            plan=plan,
                            checkpoint=context.checkpoint(active_question=question),
                            telemetry=self._telemetry(
                                question=question,
                                plan=plan,
                                tool_calls=tool_telemetry,
                                subagent_tasks=subagent_telemetry,
                                evidence_ids=tuple(
                                    item.evidence_id for item in gate.records()
                                ),
                                checkpoint=context.checkpoint(
                                    active_question=question
                                ),
                            ),
                        )
                    child_task = SubagentTask(
                        task_id=task.task_id,
                        objective=task.goal,
                        required_evidence=task.required_evidence,
                    )
                    report = await self._subagent_orchestrator.dispatch(
                        task.assigned_to, child_task
                    )
                    gate.admit_subagent_report(task_id=task.task_id, report=report)
                    subagent_telemetry.append(
                        SubagentTaskTelemetry(
                            subagent_id=task.task_id,
                            objective=task.goal[:2048],
                            status="completed",
                            slot_id=task.assigned_to,
                            evidence_ids=report.evidence_ids,
                        )
                    )
                    plan.mark_completed(task.task_id)
                    context.add(report.summary, tokens=max(1, len(report.summary) // 4))
                    continue
                if not task.suggested_tools:
                    break
                loop = ToolLoop(
                    registry=runtime_tools,
                    max_rounds=max_tool_rounds - tool_rounds,
                )
                loop_result = await loop.run(
                    calls=[
                        (
                            task.suggested_tools[0],
                            {"query": question.original_request},
                        )
                    ],
                    context=ToolExecutionContext(
                        ticket_id=ticket_id,
                        session_id=session_id,
                        root_session_id=session_id,
                    ),
                )
                tool_rounds += loop_result.rounds
                if not loop_result.results:
                    break
                result = loop_result.results[0]
                call_id = f"call-{ticket_id}-{tool_rounds:02d}"
                gate.admit(
                    call_id=call_id,
                    tool_name=task.suggested_tools[0],
                    result=result,
                )
                tool_telemetry.append(
                    ToolCallTelemetry(
                        call_id=call_id,
                        tool_name=task.suggested_tools[0],
                        status=(
                            "failed"
                            if result.status.value == "failure"
                            else "succeeded"
                        ),
                        summary=result.summary[:2048],
                        evidence_ids=(
                            (result.evidence_id,) if result.evidence_id else ()
                        ),
                        artifact_refs=self._artifact_refs(result.artifacts),
                    )
                )
                plan.mark_completed(task.task_id)
                context.add(result.summary, tokens=max(1, len(result.summary) // 4))
            evidence_ids = tuple(item.evidence_id for item in gate.records())
            checkpoint = context.checkpoint(active_question=question)
            telemetry = self._telemetry(
                question=question,
                plan=plan,
                tool_calls=tool_telemetry,
                subagent_tasks=subagent_telemetry,
                evidence_ids=evidence_ids,
                checkpoint=checkpoint,
            )
            answer = await self._model.compose_answer(snapshot, question, evidence_ids)
            if not gate.can_answer(answer=answer, evidence_ids=evidence_ids):
                return self._outcome(
                    session_id=session_id,
                    ticket_id=ticket_id,
                    kind=OutcomeKind.FAILED,
                    reason="completion gate rejected an unsupported answer",
                    plan=plan,
                    checkpoint=checkpoint,
                    telemetry=telemetry,
                )
            return RuntimeOutcome(
                outcome_id=f"outcome-{ticket_id}",
                session_id=session_id,
                ticket_id=ticket_id,
                kind=OutcomeKind.ANSWER,
                answer=answer,
                reply_locale=snapshot.locale,
                snapshot_cursor=snapshot.latest_customer_cursor,
                delivery_eligible=True,
                evidence_ids=evidence_ids,
                todo_summary=self._summary(plan),
                checkpoint=checkpoint,
                telemetry=telemetry,
            )
        except Exception as error:
            return RuntimeOutcome(
                outcome_id=f"outcome-{ticket_id}",
                session_id=session_id,
                ticket_id=ticket_id,
                kind=OutcomeKind.FAILED,
                reason=str(error),
                todo_summary=TodoSummary(completed=0, pending=0, failed=1),
            )

    @staticmethod
    def _summary(plan: TodoPlan) -> TodoSummary:
        from contracts.todo import TodoStatus

        return TodoSummary(
            completed=sum(item.status is TodoStatus.COMPLETED for item in plan.items),
            pending=sum(
                item.status
                in {TodoStatus.PENDING, TodoStatus.READY, TodoStatus.RUNNING}
                for item in plan.items
            ),
            failed=sum(
                item.status in {TodoStatus.FAILED, TodoStatus.BLOCKED}
                for item in plan.items
            ),
        )

    @classmethod
    def _outcome(
        cls,
        *,
        session_id: str,
        ticket_id: int,
        kind: OutcomeKind,
        reason: str,
        plan: TodoPlan,
        checkpoint: ContextCheckpoint,
        telemetry: SandboxResultTelemetry | None = None,
    ) -> RuntimeOutcome:
        return RuntimeOutcome(
            outcome_id=f"outcome-{ticket_id}",
            session_id=session_id,
            ticket_id=ticket_id,
            kind=kind,
            reason=reason,
            todo_summary=cls._summary(plan),
            checkpoint=checkpoint,
            telemetry=telemetry,
        )

    @staticmethod
    def _artifact_refs(values: tuple[str, ...]) -> tuple[ArtifactReference, ...]:
        references: list[ArtifactReference] = []
        for value in values:
            try:
                references.append(ArtifactReference(ref=value))
            except ValueError:
                # Provider data is never copied into telemetry.  Keep only
                # well-formed controlled references.
                continue
        return tuple(references)

    @staticmethod
    def _telemetry(
        *,
        question: ActiveQuestion,
        plan: TodoPlan,
        tool_calls: list[ToolCallTelemetry],
        subagent_tasks: list[SubagentTaskTelemetry],
        evidence_ids: tuple[str, ...],
        checkpoint: ContextCheckpoint,
    ) -> SandboxResultTelemetry:
        return SandboxResultTelemetry(
            active_question=ActiveQuestionTelemetry(
                original_event_id=question.original_event_id,
                original_request=question.original_request[:4096],
                clarification_event_ids=question.clarification_event_ids,
                current_facts=tuple(fact[:2048] for fact in question.current_facts),
            ),
            todos=tuple(
                TodoTelemetry(
                    task_id=item.task_id,
                    goal=item.goal[:2048],
                    status=item.status.value,
                    assigned_to=item.assigned_to,
                )
                for item in plan.items
            ),
            tool_calls=tuple(tool_calls),
            subagent_tasks=tuple(subagent_tasks),
            evidence_ids=evidence_ids,
            context_checkpoint=ContextCheckpointTelemetry(
                checkpoint_ref=f"checkpoint-{question.original_event_id}",
                level=checkpoint.level.value,
                used_tokens=checkpoint.used_tokens,
                exact_identifiers=checkpoint.exact_identifiers,
                retained_rounds=tuple(
                    round_[:2048] for round_ in checkpoint.retained_rounds
                ),
                summary=checkpoint.summary[:4096] or None,
            ),
        )

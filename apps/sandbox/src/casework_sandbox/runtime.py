"""Ticket-id driven Agent runtime executed inside one ephemeral sandbox."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from casework_agent_core.context import ContextCheckpoint, ContextGovernance
from casework_agent_core.domain import (
    ActiveQuestion,
    TicketSnapshot,
    TicketSnapshotNotReadyError,
    resolve_active_question,
)
from casework_agent_core.evidence import EvidenceGate
from casework_agent_core.memory import WorkingMemory
from casework_agent_core.skills import LoadedSkill, SkillLoader
from casework_agent_core.subagents import SubagentReport, SubagentTask
from casework_agent_core.todo import TodoPlan
from casework_tool_sdk.loop import ToolLoop
from casework_tool_sdk.registry import ToolExecutionContext, ToolRegistry
from contracts.evidence import Evidence
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
from contracts.todo import Todo
from contracts.tool import ToolResultStatus


class ZendeskReader(Protocol):
    async def fetch_ticket(
        self, ticket_id: int, *, expected_event_id: str | None = None
    ) -> TicketSnapshot: ...


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
        evidence: tuple[Evidence, ...],
    ) -> str: ...

    async def repair_tool_call(
        self,
        snapshot: TicketSnapshot,
        question: ActiveQuestion,
        todo: Todo,
        failed_tool_name: str,
        failure_summary: str,
        available_tools: tuple[str, ...],
    ) -> tuple[str, dict[str, object]] | None: ...


class SubagentDispatcher(Protocol):
    async def dispatch(self, slot_id: str, task: SubagentTask) -> SubagentReport: ...

    async def dispatch_many(
        self, work: tuple[tuple[str, SubagentTask], ...]
    ) -> tuple[SubagentReport, ...]: ...


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

    async def run(
        self,
        *,
        ticket_id: int,
        session_id: str,
        expected_event_id: str | None = None,
    ) -> RuntimeOutcome:
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
        context = ContextGovernance(
            window_tokens=context_window_tokens,
            soft_compaction_percent=(
                bootstrap.soft_compaction_percent if bootstrap is not None else 60
            ),
            hard_checkpoint_percent=(
                bootstrap.hard_checkpoint_percent if bootstrap is not None else 80
            ),
        )
        # Working memory is deliberately scoped to this invocation.  It is
        # never shared between sessions and is reclaimed with the sandbox.
        working_memory = WorkingMemory()
        try:
            snapshot = await self._zendesk.fetch_ticket(
                ticket_id, expected_event_id=expected_event_id
            )
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
            context.compact_if_needed(active_question=question)
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
                            checkpoint=self._checkpoint(
                                context, question, working_memory
                            ),
                            telemetry=self._telemetry(
                                question=question,
                                plan=plan,
                                tool_calls=tool_telemetry,
                                subagent_tasks=subagent_telemetry,
                                evidence_ids=tuple(
                                    item.evidence_id for item in gate.records()
                                ),
                                checkpoint=self._checkpoint(
                                    context, question, working_memory
                                ),
                                working_memory=working_memory,
                            ),
                        )
                    batch = (
                        task,
                        *plan.take_ready_for(
                            assigned_to=task.assigned_to,
                            limit=7,
                        ),
                    )
                    child_work = tuple(
                        (
                            item.assigned_to,
                            SubagentTask(
                                task_id=item.task_id,
                                objective=item.goal,
                                required_evidence=item.required_evidence,
                            ),
                        )
                        for item in batch
                    )
                    reports = await self._subagent_orchestrator.dispatch_many(
                        child_work
                    )
                    for item, report in zip(batch, reports, strict=True):
                        gate.admit_subagent_report(
                            task_id=item.task_id, report=report
                        )
                        working_memory.record(
                            key=self._memory_key("subagent", item.task_id),
                            value=f"evidence:{report.evidence_ids[0]}",
                            evidence_ids=report.evidence_ids,
                        )
                        subagent_telemetry.append(
                            SubagentTaskTelemetry(
                                subagent_id=report.task_id,
                                objective=item.goal[:2048],
                                status="completed",
                                slot_id=item.assigned_to,
                                evidence_ids=report.evidence_ids,
                            )
                        )
                        plan.mark_completed(item.task_id)
                        context.add(
                            "subagent evidence " + ",".join(report.evidence_ids),
                            tokens=max(1, len(report.evidence_ids) * 2),
                        )
                    context.compact_if_needed(active_question=question)
                    continue
                if not task.suggested_tools:
                    break
                tool_name = task.suggested_tools[0]
                tool_arguments: dict[str, object] = dict(task.tool_arguments)
                repaired = False
                result = None
                while True:
                    loop = ToolLoop(
                        registry=runtime_tools,
                        max_rounds=max_tool_rounds - tool_rounds,
                    )
                    loop_result = await loop.run(
                        calls=[(tool_name, tool_arguments)],
                        context=ToolExecutionContext(
                            ticket_id=ticket_id,
                            session_id=session_id,
                            root_session_id=session_id,
                        ),
                    )
                    tool_rounds += loop_result.rounds
                    if not loop_result.results:
                        break
                    # A bounded retry returns every attempt for telemetry/budgeting;
                    # only the final attempt determines this Todo's evidence.
                    result = loop_result.results[-1]
                    call_id = f"call-{ticket_id}-{tool_rounds:02d}"
                    safe_summary = self._safe_summary(result.summary)
                    if (
                        result.status is ToolResultStatus.FAILURE
                        or result.evidence_id is None
                    ):
                        tool_telemetry.append(
                            ToolCallTelemetry(
                                call_id=call_id,
                                tool_name=tool_name,
                                status="failed",
                                summary=safe_summary,
                            )
                        )
                        context.add(
                            f"tool failure {tool_name}: {safe_summary}",
                            tokens=max(1, len(safe_summary) // 4),
                        )
                        context.compact_if_needed(active_question=question)
                        repair = None
                        if not repaired and tool_rounds < max_tool_rounds:
                            repair = await self._model.repair_tool_call(
                                snapshot,
                                question,
                                task,
                                tool_name,
                                safe_summary,
                                runtime_tools.names(),
                            )
                        if repair is not None:
                            tool_name, tool_arguments = repair
                            repaired = True
                            continue
                        plan.mark_failed(task.task_id)
                        checkpoint = self._checkpoint(
                            context, question, working_memory
                        )
                        return self._outcome(
                            session_id=session_id,
                            ticket_id=ticket_id,
                            kind=OutcomeKind.FAILED,
                            reason=f"tool {tool_name} failed: {safe_summary}",
                            plan=plan,
                            checkpoint=checkpoint,
                            telemetry=self._telemetry(
                                question=question,
                                plan=plan,
                                tool_calls=tool_telemetry,
                                subagent_tasks=subagent_telemetry,
                                evidence_ids=tuple(
                                    item.evidence_id for item in gate.records()
                                ),
                                checkpoint=checkpoint,
                                working_memory=working_memory,
                            ),
                        )
                    gate.admit(call_id=call_id, tool_name=tool_name, result=result)
                    evidence_id = result.evidence_id
                    assert evidence_id is not None
                    tool_telemetry.append(
                        ToolCallTelemetry(
                            call_id=call_id,
                            tool_name=tool_name,
                            status="succeeded",
                            summary=safe_summary,
                            evidence_ids=(evidence_id,),
                            artifact_refs=self._artifact_refs(result.artifacts),
                        )
                    )
                    break
                if result is None or result.evidence_id is None:
                    break
                evidence_id = result.evidence_id
                working_memory.record(
                    key=self._memory_key("tool", task.task_id),
                    value=f"evidence:{evidence_id}",
                    evidence_ids=(evidence_id,),
                )
                plan.mark_completed(task.task_id)
                context.add(
                    f"tool evidence {evidence_id}",
                    tokens=max(1, len(evidence_id) // 2),
                )
                context.compact_if_needed(active_question=question)
            evidence = gate.records()
            evidence_ids = tuple(item.evidence_id for item in evidence)
            checkpoint = self._checkpoint(context, question, working_memory)
            telemetry = self._telemetry(
                question=question,
                plan=plan,
                tool_calls=tool_telemetry,
                subagent_tasks=subagent_telemetry,
                evidence_ids=evidence_ids,
                checkpoint=checkpoint,
                working_memory=working_memory,
            )
            answer = await self._model.compose_answer(snapshot, question, evidence)
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
        except TicketSnapshotNotReadyError as error:
            return RuntimeOutcome(
                outcome_id=f"outcome-{ticket_id}",
                session_id=session_id,
                ticket_id=ticket_id,
                kind=OutcomeKind.FAILED,
                reason=str(error),
                snapshot_cursor=error.observed_cursor,
                retry_classification="snapshot_not_ready",
                todo_summary=TodoSummary(completed=0, pending=0, failed=1),
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

    @staticmethod
    def _memory_key(prefix: str, task_id: str) -> str:
        identifier = re.sub(r"[^a-z0-9_.-]+", "-", task_id.casefold()).strip("-")
        return f"{prefix}.{identifier or 'task'}"

    @staticmethod
    def _safe_summary(value: str) -> str:
        """Keep operator telemetry bounded and free of obvious secret text."""

        normalized = value.casefold()
        if any(
            marker in normalized
            for marker in (
                "hidden_reasoning",
                "chain_of_thought",
                "raw_secret",
                "api_key",
                "authorization:",
                "bearer ",
                "sk-",
            )
        ):
            return "Tool result admitted as evidence."
        return value[:2048]

    @staticmethod
    def _checkpoint(
        context: ContextGovernance,
        question: ActiveQuestion,
        working_memory: WorkingMemory,
    ) -> ContextCheckpoint:
        checkpoint = context.checkpoint(active_question=question)
        return replace(
            checkpoint,
            working_memory_fact_keys=working_memory.fact_keys(),
            working_memory_fact_count=working_memory.fact_count(),
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
        working_memory: WorkingMemory,
    ) -> SandboxResultTelemetry:
        fact_keys = working_memory.fact_keys()
        fact_count = working_memory.fact_count()
        return SandboxResultTelemetry(
            active_question=ActiveQuestionTelemetry(
                original_event_id=question.original_event_id,
                original_request=question.original_request[:4096],
                clarification_event_ids=question.clarification_event_ids[-128:],
                current_facts=tuple(
                    fact[:2048] for fact in question.current_facts[-128:]
                ),
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
                working_memory_fact_keys=fact_keys,
                working_memory_fact_count=fact_count,
            ),
            working_memory_fact_keys=fact_keys,
            working_memory_fact_count=fact_count,
        )

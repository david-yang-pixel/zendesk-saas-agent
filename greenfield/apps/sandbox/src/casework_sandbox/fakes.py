"""Small fakes used by the sandbox vertical tests."""

from __future__ import annotations

from casework_agent_core.domain import ActiveQuestion, TicketSnapshot
from casework_agent_core.skills import LoadedSkill
from casework_agent_core.todo import TodoPlan, TodoPlanner
from contracts.evidence import Evidence
from contracts.todo import Todo


class FakeZendesk:
    def __init__(self, snapshot: TicketSnapshot) -> None:
        self._snapshot = snapshot
        self.fetched_ticket_ids: list[int] = []

    async def fetch_ticket(self, ticket_id: int) -> TicketSnapshot:
        self.fetched_ticket_ids.append(ticket_id)
        if ticket_id != self._snapshot.ticket_id:
            raise KeyError(ticket_id)
        return self._snapshot


class FakeModel:
    def __init__(self, *, answer: str) -> None:
        self._answer = answer
        self.planned_ticket_ids: list[int] = []
        self.answer_calls = 0

    async def plan(
        self,
        snapshot: TicketSnapshot,
        question: ActiveQuestion,
        skill: LoadedSkill,
        available_tools: tuple[str, ...],
    ) -> TodoPlan:
        del skill
        self.planned_ticket_ids.append(snapshot.ticket_id)
        return TodoPlanner().build(
            snapshot,
            question,
            available_tools=available_tools,
        )

    async def compose_answer(
        self,
        snapshot: TicketSnapshot,
        question: ActiveQuestion,
        evidence: tuple[Evidence, ...],
    ) -> str:
        del snapshot, question, evidence
        self.answer_calls += 1
        return self._answer

    async def repair_tool_call(
        self,
        snapshot: TicketSnapshot,
        question: ActiveQuestion,
        todo: Todo,
        failed_tool_name: str,
        failure_summary: str,
        available_tools: tuple[str, ...],
    ) -> tuple[str, dict[str, object]] | None:
        del snapshot, question, todo, failed_tool_name, failure_summary, available_tools
        return None

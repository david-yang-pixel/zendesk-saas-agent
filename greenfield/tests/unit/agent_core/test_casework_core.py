from datetime import UTC, datetime

from casework_agent_core.context import ContextGovernance
from casework_agent_core.domain import (
    ActiveQuestion,
    TicketComment,
    TicketSnapshot,
    resolve_active_question,
)
from casework_agent_core.evidence import EvidenceGate
from casework_agent_core.todo import TodoPlan, TodoPlanner
from contracts.tool import ToolResult, ToolResultStatus

NOW = datetime.now(UTC)


def make_snapshot() -> TicketSnapshot:
    return TicketSnapshot(
        ticket_id=3726,
        subject="Cannot find my workspace",
        requester_email="customer@example.com",
        locale="en-US",
        status="open",
        assignee="SaaS",
        tags=("saas",),
        comments=(
            TicketComment(
                comment_id="c-01",
                body="Please find the workspace connected to my account.",
                public=True,
                author_origin="customer",
                created_at=NOW,
                event_id="evt-01",
            ),
        ),
        latest_customer_event_id="evt-01",
    )


def test_snapshot_resolves_original_question_and_plans_a_dynamic_todo() -> None:
    snapshot = make_snapshot()

    question = resolve_active_question(snapshot)
    assert question.original_text.startswith("Please find")
    assert question.event_ids == ("evt-01",)

    plan = TodoPlanner().build(
        snapshot, question, available_tools=("workspace_lookup",)
    )
    assert isinstance(plan, TodoPlan)
    ready = plan.next_ready()
    assert ready is not None
    assert ready.goal == "Investigate the customer's request"
    assert ready.suggested_tools == ("workspace_lookup",)


def test_evidence_gate_admits_success_and_rejects_answer_without_bound_evidence(
) -> None:
    gate = EvidenceGate()
    result = ToolResult(
        status=ToolResultStatus.SUCCESS,
        summary="Found one workspace",
        data={"workspace_id": "ws-01"},
        evidence_id="ev-01",
        observed_at=NOW,
        targets=("ws-01",),
    )

    evidence = gate.admit(
        call_id="call-01", tool_name="workspace_lookup", result=result
    )
    assert evidence.evidence_id == "ev-01"
    assert gate.can_answer(answer="The workspace is ws-01.", evidence_ids=("ev-01",))
    assert not gate.can_answer(answer="The workspace is ws-01.", evidence_ids=())


def test_context_governance_checkpoint_preserves_exact_ids_and_compacts() -> None:
    governance = ContextGovernance(window_tokens=100)
    governance.add("ticket 3726 event evt-01", tokens=70)
    assert governance.checkpoint(active_question=ActiveQuestion(
        question_id="q-01",
        original_text="Find my workspace",
        event_ids=("evt-01",),
    )).level == "soft"

    governance.add("large raw output", tokens=20)
    checkpoint = governance.checkpoint(active_question=ActiveQuestion(
        question_id="q-01",
        original_text="Find my workspace",
        event_ids=("evt-01",),
    ))
    assert checkpoint.level == "hard"
    assert checkpoint.exact_identifiers == ("3726", "evt-01")
    assert checkpoint.active_question.original_text == "Find my workspace"

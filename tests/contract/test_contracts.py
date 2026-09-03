from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from contracts.event import Event, HumanCancel
from contracts.evidence import Evidence
from contracts.job import CaseJob, JobStatus
from contracts.outcome import Outcome, OutcomeKind
from contracts.runtime import ModelBinding, RuntimeBootstrap
from contracts.sandbox import (
    Sandbox,
    SandboxCancelRequest,
    SandboxHeartbeat,
    SandboxResources,
    SandboxResultMessage,
    SandboxStartRequest,
    SandboxStatus,
)
from contracts.session import Session, SessionStatus
from contracts.subagent import Subagent, SubagentStatus
from contracts.ticket import ActiveQuestion, TicketMessage, TicketSnapshot
from contracts.todo import Todo, TodoStatus
from contracts.tool import ToolResult, ToolResultStatus

NOW = datetime(2026, 9, 1, tzinfo=UTC)
CONFIG = {
    "config_revision": "cfg-01",
    "model_digest": "model-sha256",
    "skill_digest": "skill-sha256",
    "tool_digest": "tool-sha256",
}
BOOTSTRAP = RuntimeBootstrap(
    config_revision="cfg-01",
    root_model=ModelBinding(
        provider="openai", model="root", credential_ref="secret/root"
    ),
    model_digest="model-sha256",
    skill_digest="skill-sha256",
    tool_digest="tool-sha256",
)


def test_event_and_job_keep_business_input_minimal_and_json_stable() -> None:
    event = Event(
        event_id="evt-01",
        ticket_id=3726,
        occurred_at=NOW,
        customer_origin="customer",
        source="zendesk",
        status="open",
        tags=("saas",),
    )
    job = CaseJob(
        job_id="job-01",
        event_id=event.event_id,
        expected_event_id="messaging:evt-01",
        ticket_id=event.ticket_id,
        config_revision=CONFIG["config_revision"],
        model_digest=CONFIG["model_digest"],
        skill_digest=CONFIG["skill_digest"],
        tool_digest=CONFIG["tool_digest"],
        created_at=NOW,
    )

    assert job.business_input == {"ticket_id": 3726}
    assert job.model_dump_json(exclude_none=True) == (
        '{"job_id":"job-01","event_id":"evt-01",'
        '"expected_event_id":"messaging:evt-01","ticket_id":3726,'
        '"config_revision":"cfg-01","model_digest":"model-sha256",'
        '"skill_digest":"skill-sha256","tool_digest":"tool-sha256",'
        '"status":"queued","attempts":0,'
        '"created_at":"2026-09-01T00:00:00Z"}'
    )


def test_job_state_transitions_are_fail_closed() -> None:
    job = CaseJob(
        job_id="job-01",
        event_id="evt-01",
        expected_event_id="messaging:evt-01",
        ticket_id=1,
        config_revision=CONFIG["config_revision"],
        model_digest=CONFIG["model_digest"],
        skill_digest=CONFIG["skill_digest"],
        tool_digest=CONFIG["tool_digest"],
    )
    running = job.transition(JobStatus.CLAIMED).transition(JobStatus.SANDBOX_CREATING)
    assert running.status is JobStatus.SANDBOX_CREATING
    with pytest.raises(ValueError, match="invalid job transition"):
        job.transition(JobStatus.RUNNING)
    with pytest.raises(ValueError, match="terminal"):
        job.transition(JobStatus.CANCELLED).transition(JobStatus.RUNNING)


def test_session_and_sandbox_lifecycle_transitions() -> None:
    session = Session(
        session_id="session-01",
        ticket_id=1,
        config_revision=CONFIG["config_revision"],
        model_digest=CONFIG["model_digest"],
        skill_digest=CONFIG["skill_digest"],
        tool_digest=CONFIG["tool_digest"],
    )
    session = session.transition(SessionStatus.RUNNING)
    assert session.status is SessionStatus.RUNNING
    sandbox = Sandbox(
        sandbox_id="sandbox-01",
        session_id=session.session_id,
        resources=SandboxResources(),
    )
    sandbox = sandbox.transition(SandboxStatus.READY).transition(SandboxStatus.RUNNING)
    assert sandbox.status is SandboxStatus.RUNNING
    assert sandbox.resources.cpu == 0.5
    assert sandbox.resources.memory_mb == 1024
    with pytest.raises(ValueError, match="invalid sandbox transition"):
        sandbox.transition(SandboxStatus.DESTROYED)


def test_sandbox_resource_retry_is_bounded() -> None:
    first = Sandbox(sandbox_id="sandbox-01", session_id="session-01")
    assert first.resource_attempt == 1
    retry = Sandbox(
        sandbox_id="sandbox-02",
        session_id="session-01",
        resource_attempt=2,
        resources=SandboxResources(cpu=1.0),
    )
    assert retry.resources.cpu == 1.0
    with pytest.raises(ValidationError):
        Sandbox(
            sandbox_id="sandbox-03",
            session_id="session-01",
            resource_attempt=3,
            resources=SandboxResources(cpu=1.0),
        )


def test_todo_readiness_requires_all_dependencies() -> None:
    todo = Todo(
        task_id="task-01",
        goal="Locate the affected app",
        assigned_to="root",
        dependencies=("task-00",),
        skill="app-run-investigation",
        suggested_tools=("sql_query",),
        required_evidence=("app_id", "workspace_id"),
        completion_condition="One exact app is found",
    )
    assert not todo.is_ready(set())
    ready = todo.transition(TodoStatus.READY, completed_task_ids={"task-00"})
    assert ready.status is TodoStatus.READY
    with pytest.raises(ValueError, match="dependencies"):
        Todo(
            task_id="task-02",
            goal="Bad dependency",
            dependencies=("task-02",),
            completion_condition="never",
        )


def test_tool_result_success_requires_evidence_and_serializes_targets() -> None:
    result = ToolResult(
        status=ToolResultStatus.SUCCESS,
        summary="Found one matching app",
        data={"app_id": "app-01"},
        evidence_id="ev-01",
        observed_at=NOW,
        targets=("app-01",),
    )
    assert result.model_dump(mode="json")["status"] == "success"
    with pytest.raises(ValidationError):
        ToolResult(
            status=ToolResultStatus.SUCCESS,
            summary="missing evidence",
            observed_at=NOW,
        )


def test_evidence_requires_timezone_and_payload_or_reference() -> None:
    evidence = Evidence(
        evidence_id="ev-01",
        call_id="call-01",
        source="sql_query",
        observed_at=NOW,
        payload={"app_id": "app-01"},
        targets=("app-01",),
    )
    assert evidence.model_dump(mode="json")["observed_at"].endswith("Z")
    with pytest.raises(ValidationError):
        Evidence(
            evidence_id="ev-02",
            call_id="call-02",
            source="sql_query",
            observed_at=datetime(2026, 9, 1),
        )


def test_event_requires_origin_and_source_and_human_cancel_is_not_a_job() -> None:
    with pytest.raises(ValidationError):
        Event.model_validate(
            {"event_id": "evt-02", "ticket_id": 1, "occurred_at": NOW}
        )
    cancel = HumanCancel(
        event_id="evt-human-01",
        ticket_id=1,
        occurred_at=NOW,
        reason="Human agent took over",
    )
    assert cancel.ticket_id == 1
    assert not hasattr(cancel, "business_input")


def test_harness_sandbox_protocol_messages_are_typed() -> None:
    start = SandboxStartRequest(
        sandbox_id="sandbox-01",
        job_id="job-01",
        session_id="session-01",
        event_id="evt-01",
        expected_event_id="messaging:evt-01",
        ticket_id=1,
        generation=3,
        resource_attempt=1,
        deadline=NOW,
        resources=SandboxResources(),
        config_revision=CONFIG["config_revision"],
        model_digest=CONFIG["model_digest"],
        skill_digest=CONFIG["skill_digest"],
        tool_digest=CONFIG["tool_digest"],
        bootstrap=BOOTSTRAP,
    )
    assert start.event_id == "evt-01"
    assert start.expected_event_id == "messaging:evt-01"
    cancel = SandboxCancelRequest(
        sandbox_id=start.sandbox_id,
        session_id=start.session_id,
        generation=start.generation,
        reason="human takeover",
    )
    heartbeat = SandboxHeartbeat(
        sandbox_id=start.sandbox_id,
        session_id=start.session_id,
        observed_at=NOW,
    )
    result = SandboxResultMessage(
        sandbox_id=start.sandbox_id,
        session_id=start.session_id,
        outcome=Outcome(
            outcome_id="out-01",
            session_id="session-01",
            ticket_id=1,
            kind=OutcomeKind.ASK,
            question="Which workspace is affected?",
            reply_locale="en",
            snapshot_cursor="cursor-01",
            delivery_eligible=True,
        ),
    )
    assert cancel.reason == "human takeover"
    assert heartbeat.observed_at == NOW
    assert result.outcome.kind is OutcomeKind.ASK


def test_ticket_snapshot_and_active_question_preserve_original_request() -> None:
    snapshot = TicketSnapshot(
        ticket_id=3726,
        status="open",
        requester_id=99,
        assignee_id=7,
        tags=("saas",),
        form_fields={"environment": "production"},
        messages=(
            TicketMessage(
                event_id="event-original",
                author_origin="customer",
                public=True,
                body="Why did my workflow fail?",
                created_at=NOW,
            ),
            TicketMessage(
                event_id="event-clarification",
                author_origin="customer",
                public=True,
                body="It is workspace A.",
                created_at=NOW,
            ),
        ),
        locale="en",
        latest_customer_cursor="cursor-02",
        complete=True,
        fetched_at=NOW,
    )
    question = ActiveQuestion(
        original_event_id="event-original",
        original_request="Why did my workflow fail?",
        clarification_event_ids=("event-clarification",),
        current_facts=("workspace=A",),
    )

    assert snapshot.messages[-1].body == "It is workspace A."
    assert question.original_request == "Why did my workflow fail?"
    repeated = ActiveQuestion(
        original_event_id="event-original",
        original_request="Why did my workflow fail?",
        clarification_event_ids=("event-a", "event-b"),
        current_facts=("same clarification", "same clarification"),
    )
    assert repeated.current_facts == ("same clarification", "same clarification")
    with pytest.raises(ValidationError, match="event ids"):
        ActiveQuestion(
            original_event_id="event-original",
            original_request="Why did my workflow fail?",
            clarification_event_ids=("event-a", "event-a"),
        )
    with pytest.raises(ValidationError, match="complete"):
        TicketSnapshot.model_validate({**snapshot.model_dump(), "complete": False})


def test_subagent_is_depth_one_and_has_independent_model_configuration() -> None:
    task = Subagent(
        subagent_id="sub-01",
        root_session_id="session-01",
        parent_session_id="session-01",
        ticket_id=1,
        slot_id="logs",
        objective="Inspect runtime logs",
        model_provider="openai",
        model_name="gpt-test",
    )
    assert task.status is SubagentStatus.QUEUED
    assert task.transition(SubagentStatus.RUNNING).status is SubagentStatus.RUNNING
    with pytest.raises(ValidationError):
        Subagent(
            subagent_id="sub-02",
            root_session_id="session-01",
            parent_session_id="sub-01",
            ticket_id=1,
            slot_id="nested",
            objective="Nested task",
            model_provider="openai",
            model_name="gpt-test",
        )


def test_outcome_requires_fields_for_each_disposition() -> None:
    answer = Outcome(
        outcome_id="out-01",
        session_id="session-01",
        ticket_id=1,
        kind=OutcomeKind.ANSWER,
        answer="The app is configured correctly.",
        reply_locale="en",
        snapshot_cursor="cursor-01",
        delivery_eligible=True,
        evidence_ids=("ev-01",),
    )
    assert answer.kind is OutcomeKind.ANSWER
    with pytest.raises(ValidationError):
        Outcome(
            outcome_id="out-02",
            session_id="session-01",
            ticket_id=1,
            kind=OutcomeKind.ASK,
        )

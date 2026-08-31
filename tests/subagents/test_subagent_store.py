from __future__ import annotations

from zd_agent.subagents.models import SubagentTaskStatus
from zd_agent.subagents.store import SubagentStore


def test_task_store_is_idempotent_per_root_and_task_key(tmp_path) -> None:
    store = SubagentStore(tmp_path / "subagents.sqlite3")

    first = store.create(
        root_session_id="root-1",
        parent_session_id="root-1",
        ticket_id=4001,
        slot_id="reader",
        objective="Inspect current logs",
        idempotency_key="logs",
        config_digest="digest-1",
    )
    second = store.create(
        root_session_id="root-1",
        parent_session_id="root-1",
        ticket_id=4001,
        slot_id="reader",
        objective="This duplicate must not replace the original",
        idempotency_key="logs",
        config_digest="digest-1",
    )

    assert second.id == first.id
    assert second.objective == "Inspect current logs"
    assert store.list_for_root("root-1") == (first,)


def test_task_store_persists_state_result_and_model_snapshot(tmp_path) -> None:
    path = tmp_path / "subagents.sqlite3"
    store = SubagentStore(path)
    task = store.create(
        root_session_id="root-1",
        parent_session_id="root-1",
        ticket_id=4001,
        slot_id="reader",
        objective="Inspect current logs",
        idempotency_key="logs",
        config_digest="digest-1",
        model_snapshot={"provider": "openai", "model": "child-model"},
    )
    store.mark_running(task.id, session_id="child-session-1")
    completed = store.complete(
        task.id,
        result={"summary": "Current logs show a timeout", "evidence": []},
    )

    reopened = SubagentStore(path).get(task.id)

    assert completed.status is SubagentTaskStatus.COMPLETED
    assert reopened == completed
    assert reopened is not None
    assert reopened.model_snapshot == {
        "provider": "openai",
        "model": "child-model",
    }
    assert reopened.result["summary"] == "Current logs show a timeout"


def test_recovery_marks_interrupted_running_tasks_failed(tmp_path) -> None:
    store = SubagentStore(tmp_path / "subagents.sqlite3")
    task = store.create(
        root_session_id="root-1",
        parent_session_id="root-1",
        ticket_id=4001,
        slot_id="reader",
        objective="Inspect current logs",
        idempotency_key="logs",
        config_digest="digest-1",
    )
    store.mark_running(task.id, session_id="child-session-1")
    queued = store.create(
        root_session_id="root-2",
        parent_session_id="root-2",
        ticket_id=4002,
        slot_id="reader",
        objective="Queued before process interruption",
        idempotency_key="queued",
        config_digest="digest-1",
    )

    recovered = store.recover_interrupted()

    assert recovered == 2
    failed = store.get(task.id)
    assert failed is not None
    assert failed.status is SubagentTaskStatus.FAILED
    assert failed.error_code == "process_interrupted"
    queued_failed = store.get(queued.id)
    assert queued_failed is not None
    assert queued_failed.status is SubagentTaskStatus.FAILED

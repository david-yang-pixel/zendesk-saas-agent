from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from zd_agent.services.agent_lifecycle import (
    AgentLifecycleConflict,
    AgentLifecycleStore,
    AgentWorkerSupervisor,
    ControlPlaneSessionView,
)
from zd_agent.services.durable_trigger_queue import DurableTriggerQueue
from zd_agent.trigger.state_store import TicketState, TicketStateTracker


def _lifecycle_state(
    *,
    desired_state: str,
    dispatch_enabled: bool,
    dispatch_generation: int = 0,
    generation: int = 0,
    accept_after: float = 0,
    **extra,
) -> dict:
    return {
        "desired_state": desired_state,
        "dispatch_enabled": dispatch_enabled,
        "dispatch_generation": dispatch_generation,
        "generation": generation,
        "accept_after": accept_after,
        **extra,
    }


@pytest.mark.asyncio
async def test_explicit_start_creates_new_epoch_and_discards_backlog(tmp_path) -> None:
    state_dir = tmp_path / ".state"
    queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    await queue.put({"source": "old", "ticket_id": 1, "_queue_key": "old:1"})
    store = AgentLifecycleStore(state_dir)
    store.write(
        _lifecycle_state(
            desired_state="stopped",
            dispatch_enabled=False,
            generation=4,
            stopped_at="2026-08-26T00:00:00+00:00",
        )
    )
    supervisor = AgentWorkerSupervisor(
        state_dir=state_dir,
        trigger_queue=queue,
        command=(sys.executable, "-c", "import time; time.sleep(60)"),
        stop_timeout_seconds=2,
    )

    status = await supervisor.start()
    try:
        assert status["desired_state"] == "running"
        assert status["generation"] == 5
        assert status["accept_after"] > 0
        assert await queue.pending_count() == 0
        with queue._store.connect() as connection:
            row = connection.execute(
                "SELECT status,last_error FROM trigger_events WHERE event_key = 'old:1'"
            ).fetchone()
        assert row["status"] == "discarded"
        assert row["last_error"] == "agent_start_watermark"
    finally:
        await supervisor.stop()
        generation = supervisor.store.read()["dispatch_generation"]
        supervisor.store.write_worker_status(
            {
                "status": "running",
                "pid": supervisor._process.pid,
                "heartbeat_unix": time.time(),
                "observed_dispatch_generation": generation,
                "dispatch_enabled": False,
                "background_tasks_quiesced": True,
                "active": 0,
            }
        )
        await supervisor.wait_for_operation(timeout=3)

    assert supervisor.status()["status"] == "stopped"
    assert supervisor.status()["process_running"] is False


def test_control_plane_session_view_reads_worker_heartbeat(tmp_path) -> None:
    store = AgentLifecycleStore(tmp_path / ".state")
    store.write_worker_status(
        {
            "active": 1,
            "max_concurrent": 3,
            "active_sessions": [{"ticket_id": 4051, "status": "running"}],
        }
    )

    view = ControlPlaneSessionView(store)

    assert view.active_count == 1
    assert view.get_all_sessions()["active_sessions"][0]["ticket_id"] == 4051


@pytest.mark.asyncio
async def test_stop_persists_stopped_and_prevents_auto_restart(tmp_path) -> None:
    state_dir = tmp_path / ".state"
    queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    supervisor = AgentWorkerSupervisor(
        state_dir=state_dir,
        trigger_queue=queue,
        command=(sys.executable, "-c", "import time; time.sleep(60)"),
        stop_timeout_seconds=2,
    )
    supervisor.store.write(
        _lifecycle_state(
            desired_state="running",
            dispatch_enabled=True,
            dispatch_generation=1,
            generation=1,
        )
    )
    await supervisor.initialize()

    status, discarded = await supervisor.stop()
    generation = supervisor.store.read()["dispatch_generation"]
    supervisor.store.write_worker_status(
        {
            "status": "running",
            "pid": supervisor._process.pid,
            "heartbeat_unix": time.time(),
            "observed_dispatch_generation": generation,
            "dispatch_enabled": False,
            "background_tasks_quiesced": True,
            "active": 0,
        }
    )
    await supervisor.wait_for_operation(timeout=3)
    status = supervisor.status()

    assert discarded == 0
    assert status["desired_state"] == "stopped"
    assert supervisor.store.read()["desired_state"] == "stopped"
    await supervisor.close()


def test_corrupt_lifecycle_state_fails_closed(tmp_path) -> None:
    store = AgentLifecycleStore(tmp_path / ".state")
    store.path.write_text("{broken", encoding="utf-8")

    lifecycle = store.read()

    assert lifecycle["desired_state"] == "stopped"
    assert lifecycle["dispatch_enabled"] is False
    assert lifecycle["state_error"] == "JSONDecodeError"


@pytest.mark.asyncio
async def test_running_ingress_does_not_depend_on_worker_heartbeat(tmp_path) -> None:
    state_dir = tmp_path / ".state"
    queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    supervisor = AgentWorkerSupervisor(state_dir=state_dir, trigger_queue=queue)
    supervisor.store.write(
        {
            "desired_state": "running",
            "dispatch_enabled": True,
            "dispatch_generation": 7,
            "generation": 2,
            "accept_after": 0,
        }
    )

    assert supervisor.accepting_events() is True
    assert supervisor.status()["process_running"] is False


@pytest.mark.asyncio
async def test_dependency_interlock_immediately_fences_ingress_and_blocks_start(
    tmp_path,
) -> None:
    state_dir = tmp_path / ".state"
    queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    supervisor = AgentWorkerSupervisor(state_dir=state_dir, trigger_queue=queue)
    supervisor.store.write(
        _lifecycle_state(
            desired_state="running",
            dispatch_enabled=True,
            dispatch_generation=3,
            generation=2,
        )
    )

    await supervisor.engage_dependency_interlock(
        dependency="zendesk_browser_relay",
        reason="login_required",
    )

    status = supervisor.status()
    assert status["accepting_events"] is False
    assert status["dispatch_enabled"] is False
    assert status["dependency_interlock"]["active"] is True
    with pytest.raises(AgentLifecycleConflict, match="dependency_interlock_active"):
        await supervisor.start()


@pytest.mark.asyncio
async def test_clearing_dependency_interlock_keeps_explicitly_stopped_agent_off(
    tmp_path,
) -> None:
    state_dir = tmp_path / ".state"
    queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    supervisor = AgentWorkerSupervisor(state_dir=state_dir, trigger_queue=queue)
    supervisor.store.write(
        _lifecycle_state(
            desired_state="stopped",
            dispatch_enabled=False,
            dependency_interlock_active=True,
            dependency_interlock_dependency="zendesk_browser_relay",
            dependency_interlock_reason="login_required",
        )
    )

    status = await supervisor.clear_dependency_interlock(
        dependency="zendesk_browser_relay"
    )

    assert status["dependency_interlock"]["active"] is False
    assert status["desired_state"] == "stopped"
    assert status["process_running"] is False


@pytest.mark.asyncio
async def test_dependency_recovery_auto_starts_agent_that_was_running(tmp_path) -> None:
    state_dir = tmp_path / ".state"
    queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    supervisor = AgentWorkerSupervisor(
        state_dir=state_dir,
        trigger_queue=queue,
        command=(sys.executable, "-c", "import time; time.sleep(60)"),
        stop_timeout_seconds=2,
    )
    supervisor.store.write(
        _lifecycle_state(
            desired_state="stopped",
            dispatch_enabled=False,
            generation=3,
            dependency_interlock_active=True,
            dependency_interlock_dependency="zendesk_browser_relay",
            dependency_interlock_reason="login_required",
            dependency_interlock_resume_requested=True,
        )
    )

    status = await supervisor.clear_dependency_interlock(
        dependency="zendesk_browser_relay"
    )

    assert status["dependency_interlock"]["active"] is False
    assert status["desired_state"] == "running"
    assert status["dispatch_enabled"] is True
    assert status["generation"] == 4
    assert status["process_running"] is True
    assert supervisor.store.read()["dependency_interlock_resume_requested"] is False
    await supervisor.close()


@pytest.mark.asyncio
async def test_legacy_dependency_state_recovers_before_supervisor_initialization(
    tmp_path,
) -> None:
    state_dir = tmp_path / ".state"
    queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    supervisor = AgentWorkerSupervisor(
        state_dir=state_dir,
        trigger_queue=queue,
        command=(sys.executable, "-c", "import time; time.sleep(60)"),
        stop_timeout_seconds=2,
    )
    supervisor.store.write(
        _lifecycle_state(
            desired_state="running",
            dispatch_enabled=False,
            generation=2,
            dependency_interlock_active=True,
            dependency_interlock_dependency="zendesk_browser_relay",
            dependency_interlock_reason="login_required",
        )
    )

    status = await supervisor.clear_dependency_interlock(
        dependency="zendesk_browser_relay"
    )

    assert status["desired_state"] == "running"
    assert status["dispatch_enabled"] is True
    assert status["generation"] == 3
    assert status["process_running"] is True
    await supervisor.close()


@pytest.mark.asyncio
async def test_restart_preserves_preexisting_dependency_resume_intent(tmp_path) -> None:
    state_dir = tmp_path / ".state"
    queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    supervisor = AgentWorkerSupervisor(state_dir=state_dir, trigger_queue=queue)
    supervisor.store.write(
        _lifecycle_state(
            desired_state="running",
            dispatch_enabled=False,
            generation=2,
            dependency_interlock_active=True,
            dependency_interlock_dependency="zendesk_browser_relay",
            dependency_interlock_reason="login_required",
        )
    )

    await supervisor.initialize()

    lifecycle = supervisor.store.read()
    assert lifecycle["desired_state"] == "stopped"
    assert lifecycle["dependency_interlock_resume_requested"] is True
    assert supervisor.status()["process_running"] is False


@pytest.mark.asyncio
async def test_manual_stop_cancels_dependency_auto_resume(tmp_path) -> None:
    state_dir = tmp_path / ".state"
    queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    supervisor = AgentWorkerSupervisor(state_dir=state_dir, trigger_queue=queue)
    supervisor.store.write(
        _lifecycle_state(
            desired_state="stopped",
            dispatch_enabled=False,
            dependency_interlock_active=True,
            dependency_interlock_dependency="zendesk_browser_relay",
            dependency_interlock_reason="login_required",
            dependency_interlock_resume_requested=True,
        )
    )

    await supervisor.stop(actor="admin", reason="operator requested stop")
    status = await supervisor.clear_dependency_interlock(
        dependency="zendesk_browser_relay"
    )

    assert status["desired_state"] == "stopped"
    assert status["process_running"] is False
    assert supervisor.store.read()["dependency_interlock_resume_requested"] is False


@pytest.mark.asyncio
async def test_recovery_during_live_dependency_stop_keeps_interlock_closed(
    tmp_path,
) -> None:
    state_dir = tmp_path / ".state"
    queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    supervisor = AgentWorkerSupervisor(
        state_dir=state_dir,
        trigger_queue=queue,
        command=(sys.executable, "-c", "import time; time.sleep(60)"),
    )
    supervisor.store.write(
        _lifecycle_state(
            desired_state="running",
            dispatch_enabled=False,
            dependency_interlock_active=True,
            dependency_interlock_dependency="zendesk_browser_relay",
            dependency_interlock_reason="login_required",
        )
    )
    supervisor._process = MagicMock(pid=4321, returncode=None)

    with pytest.raises(AgentLifecycleConflict, match="dependency_stop_in_progress"):
        await supervisor.clear_dependency_interlock(dependency="zendesk_browser_relay")

    assert supervisor.accepting_events() is False


@pytest.mark.asyncio
async def test_dependency_interlock_stops_worker_while_preserving_ambiguous_outbox(
    monkeypatch,
    tmp_path,
) -> None:
    state_dir = tmp_path / ".state"
    queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    supervisor = AgentWorkerSupervisor(
        state_dir=state_dir,
        trigger_queue=queue,
        command=(sys.executable, "-c", "import time; time.sleep(60)"),
        stop_timeout_seconds=2,
    )
    supervisor.store.write(
        _lifecycle_state(
            desired_state="running",
            dispatch_enabled=True,
            dispatch_generation=1,
            generation=1,
        )
    )
    await supervisor.initialize()
    monkeypatch.setattr(
        supervisor,
        "_outbox_blockers",
        lambda: {"public_reply_ambiguous": 1},
    )

    await supervisor.engage_dependency_interlock(
        dependency="zendesk_browser_relay",
        reason="login_required",
    )
    fence_generation = supervisor.store.read()["dispatch_generation"]
    supervisor.store.write_worker_status(
        {
            "status": "running",
            "pid": supervisor._process.pid,
            "heartbeat_unix": time.time(),
            "observed_dispatch_generation": fence_generation,
            "dispatch_enabled": False,
            "background_tasks_quiesced": True,
            "active": 0,
        }
    )
    await supervisor.wait_for_operation(timeout=3)

    status = supervisor.status()
    assert status["desired_state"] == "stopped"
    assert status["process_running"] is False
    assert status["transition_status"] == "completed"
    assert status["transition_blockers"] == {"public_reply_ambiguous": 1}
    await supervisor.close()


@pytest.mark.asyncio
async def test_explicit_stopped_state_keeps_worker_process_absent(tmp_path) -> None:
    state_dir = tmp_path / ".state"
    queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    store = AgentLifecycleStore(state_dir)
    store.write(
        {
            "desired_state": "stopped",
            "dispatch_enabled": False,
            "dispatch_generation": 4,
            "generation": 2,
            "accept_after": 100,
        }
    )
    spawn = AsyncMock()
    supervisor = AgentWorkerSupervisor(
        state_dir=state_dir,
        trigger_queue=queue,
        spawn_fn=spawn,
    )

    await supervisor.initialize()

    spawn.assert_not_awaited()
    assert supervisor.status()["status"] == "stopped"
    assert supervisor.status()["process_running"] is False


@pytest.mark.asyncio
async def test_first_boot_is_control_plane_only(tmp_path) -> None:
    state_dir = tmp_path / ".state"
    queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    spawn = AsyncMock()
    supervisor = AgentWorkerSupervisor(
        state_dir=state_dir,
        trigger_queue=queue,
        spawn_fn=spawn,
    )

    await supervisor.initialize()

    spawn.assert_not_awaited()
    assert supervisor.store.read()["desired_state"] == "stopped"
    assert supervisor.status()["process_running"] is False


@pytest.mark.asyncio
async def test_existing_lifecycle_is_upgraded_with_initialization_marker(
    tmp_path,
) -> None:
    state_dir = tmp_path / ".state"
    queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    store = AgentLifecycleStore(state_dir)
    store.path.write_text(
        '{"accept_after":0,"desired_state":"stopped",'
        '"dispatch_enabled":false,"dispatch_generation":1,"generation":1}',
        encoding="utf-8",
    )
    supervisor = AgentWorkerSupervisor(state_dir=state_dir, trigger_queue=queue)

    await supervisor.initialize()

    assert supervisor.store.initialized_path.exists()
    supervisor.store.path.unlink()
    assert supervisor.store.read()["state_error"] == "FileNotFoundError"


def test_partial_lifecycle_state_fails_closed(tmp_path) -> None:
    store = AgentLifecycleStore(tmp_path / ".state")
    store.path.write_text('{"generation": 1}', encoding="utf-8")

    lifecycle = store.read()

    assert lifecycle["desired_state"] == "stopped"
    assert lifecycle["dispatch_enabled"] is False
    assert lifecycle["state_error"] == "ValueError"


def test_initialized_lifecycle_file_loss_fails_closed(tmp_path) -> None:
    store = AgentLifecycleStore(tmp_path / ".state")
    store.write(
        _lifecycle_state(
            desired_state="stopped",
            dispatch_enabled=False,
        )
    )
    store.path.unlink()

    lifecycle = store.read()

    assert lifecycle["desired_state"] == "stopped"
    assert lifecycle["dispatch_enabled"] is False
    assert lifecycle["state_error"] == "FileNotFoundError"


@pytest.mark.asyncio
async def test_pending_stop_transition_resumes_after_control_plane_restart(
    tmp_path,
) -> None:
    state_dir = tmp_path / ".state"
    queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    supervisor = AgentWorkerSupervisor(
        state_dir=state_dir,
        trigger_queue=queue,
        command=(sys.executable, "-c", "import time; time.sleep(60)"),
        stop_timeout_seconds=2,
    )
    supervisor.store.write(
        _lifecycle_state(
            desired_state="running",
            dispatch_enabled=False,
            dispatch_generation=8,
            generation=3,
            transition_action="stop",
            transition_status="pending",
            transition_generation=8,
            transition_operation_id="recover-stop-1",
        )
    )

    await supervisor.initialize()
    generation = supervisor.store.read()["dispatch_generation"]
    supervisor.store.write_worker_status(
        {
            "status": "running",
            "pid": supervisor._process.pid,
            "heartbeat_unix": time.time(),
            "observed_dispatch_generation": generation,
            "dispatch_enabled": False,
            "background_tasks_quiesced": True,
            "active": 0,
        }
    )
    await supervisor.wait_for_operation(timeout=3)

    lifecycle = supervisor.store.read()
    assert lifecycle["desired_state"] == "stopped"
    assert lifecycle["transition_status"] == "completed"
    assert supervisor.status()["process_running"] is False
    await supervisor.close()


@pytest.mark.asyncio
async def test_recovered_drain_uses_persisted_absolute_deadline(tmp_path) -> None:
    state_dir = tmp_path / ".state"
    queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    supervisor = AgentWorkerSupervisor(
        state_dir=state_dir,
        trigger_queue=queue,
        command=(sys.executable, "-c", "import time; time.sleep(60)"),
        stop_timeout_seconds=2,
        drain_timeout_seconds=60,
    )
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    supervisor.store.write(
        _lifecycle_state(
            desired_state="running",
            dispatch_enabled=False,
            dispatch_generation=8,
            generation=3,
            transition_action="stop",
            transition_status="pending",
            transition_generation=8,
            transition_operation_id="expired-stop-1",
            transition_started_at=(past - timedelta(seconds=60)).isoformat(),
            transition_deadline_at=past.isoformat(),
        )
    )

    await supervisor.initialize()
    await supervisor.wait_for_operation(timeout=3)

    lifecycle = supervisor.store.read()
    assert lifecycle["transition_status"] == "blocked"
    assert lifecycle["transition_error"] == "drain_timeout"
    assert supervisor.status()["process_running"] is True
    await supervisor.close()


@pytest.mark.asyncio
async def test_terminal_transition_audit_is_redelivered_after_restart(tmp_path) -> None:
    state_dir = tmp_path / ".state"
    queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    supervisor = AgentWorkerSupervisor(state_dir=state_dir, trigger_queue=queue)
    supervisor.store.write(
        _lifecycle_state(
            desired_state="stopped",
            dispatch_enabled=False,
            transition_action="stop",
            transition_status="completed",
            transition_audit_status="pending",
            transition_operation_id="audit-stop-1",
        )
    )
    callback = MagicMock()
    supervisor.set_transition_callback(callback)

    await supervisor.initialize()

    callback.assert_called_once()
    assert callback.call_args.args[0]["operation_id"] == "audit-stop-1"
    assert supervisor.store.read()["transition_audit_status"] == "completed"


@pytest.mark.asyncio
async def test_new_action_cannot_overwrite_undelivered_terminal_audit(
    tmp_path,
) -> None:
    state_dir = tmp_path / ".state"
    queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    supervisor = AgentWorkerSupervisor(state_dir=state_dir, trigger_queue=queue)
    supervisor.store.write(
        _lifecycle_state(
            desired_state="stopped",
            dispatch_enabled=False,
            transition_action="stop",
            transition_status="completed",
            transition_audit_status="pending",
            transition_operation_id="undelivered-stop-1",
        )
    )
    supervisor.set_transition_callback(MagicMock(side_effect=OSError("audit disk")))

    with pytest.raises(AgentLifecycleConflict, match="audit_delivery_pending"):
        await supervisor.start(operation_id="new-start-1")

    lifecycle = supervisor.store.read()
    assert lifecycle["transition_operation_id"] == "undelivered-stop-1"
    assert lifecycle["transition_audit_status"] == "pending"
    assert lifecycle["desired_state"] == "stopped"


@pytest.mark.asyncio
async def test_noop_stop_persists_operation_as_synchronously_completed(
    tmp_path,
) -> None:
    state_dir = tmp_path / ".state"
    queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    supervisor = AgentWorkerSupervisor(state_dir=state_dir, trigger_queue=queue)
    await supervisor.initialize()

    status, discarded = await supervisor.stop(
        operation_id="noop-stop-1",
        actor="admin",
        reason="confirm already stopped",
    )

    lifecycle = supervisor.store.read()
    assert discarded == 0
    assert status["transition_status"] == "completed"
    assert lifecycle["last_operation_id"] == "noop-stop-1"
    assert lifecycle["transition_audit_status"] == "pending"


def test_new_online_epoch_retires_old_waiting_states(tmp_path) -> None:
    state_dir = tmp_path / ".state"
    tracker = TicketStateTracker(state_dir / "tickets.json")
    tracker.set(
        4051,
        TicketState(
            state="awaiting_customer",
            first_seen=datetime.now(timezone.utc),
            last_customer_comment_id="offline-message",
        ),
    )
    supervisor = AgentWorkerSupervisor(
        state_dir=state_dir,
        trigger_queue=DurableTriggerQueue(state_dir / "trigger_queue.sqlite3"),
    )

    supervisor._migrate_old_epoch_states()

    retired = tracker.get(4051)
    assert retired.state == "out_of_scope"
    assert retired.last_verdict == "offline_wait_state_discarded"


@pytest.mark.asyncio
async def test_stop_blocks_without_killing_worker_when_public_reply_is_ambiguous(
    tmp_path, monkeypatch
) -> None:
    state_dir = tmp_path / ".state"
    queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    supervisor = AgentWorkerSupervisor(
        state_dir=state_dir,
        trigger_queue=queue,
        command=(sys.executable, "-c", "import time; time.sleep(60)"),
        stop_timeout_seconds=2,
    )
    supervisor.store.write(
        _lifecycle_state(
            desired_state="running",
            dispatch_enabled=True,
            dispatch_generation=1,
            generation=1,
        )
    )
    await supervisor.initialize()
    checks = 0

    def _block_after_request() -> dict[str, int]:
        nonlocal checks
        checks += 1
        return {} if checks == 1 else {"public_reply_ambiguous": 1}

    monkeypatch.setattr(supervisor, "_outbox_blockers", _block_after_request)
    await supervisor.stop(operation_id="blocked-stop-1")
    generation = supervisor.store.read()["dispatch_generation"]
    supervisor.store.write_worker_status(
        {
            "status": "running",
            "pid": supervisor._process.pid,
            "heartbeat_unix": time.time(),
            "observed_dispatch_generation": generation,
            "dispatch_enabled": False,
            "background_tasks_quiesced": False,
            "active": 0,
        }
    )
    await supervisor.wait_for_operation(timeout=3)

    lifecycle = supervisor.store.read()
    assert lifecycle["desired_state"] == "running"
    assert lifecycle["transition_status"] == "blocked"
    assert lifecycle["transition_error"] == "unsafe_outbox"
    assert supervisor.status()["process_running"] is True
    with pytest.raises(
        AgentLifecycleConflict,
        match="incomplete_lifecycle_transition",
    ):
        await supervisor.start(operation_id="unsafe-start-1")
    unchanged = supervisor.store.read()
    assert unchanged["transition_action"] == "stop"
    assert unchanged["transition_status"] == "blocked"
    await supervisor.close()


@pytest.mark.asyncio
async def test_start_cannot_overwrite_pending_stop_transition(tmp_path) -> None:
    state_dir = tmp_path / ".state"
    queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    supervisor = AgentWorkerSupervisor(
        state_dir=state_dir,
        trigger_queue=queue,
        command=(sys.executable, "-c", "import time; time.sleep(60)"),
        stop_timeout_seconds=2,
    )
    supervisor.store.write(
        _lifecycle_state(
            desired_state="running",
            dispatch_enabled=True,
            dispatch_generation=1,
            generation=1,
        )
    )
    await supervisor.initialize()
    await supervisor.stop(operation_id="pending-stop-1")

    with pytest.raises(AgentLifecycleConflict, match="lifecycle_operation_in_progress"):
        await supervisor.start(operation_id="overwriting-start-1")

    lifecycle = supervisor.store.read()
    assert lifecycle["transition_action"] == "stop"
    assert lifecycle["transition_operation_id"] == "pending-stop-1"
    generation = lifecycle["dispatch_generation"]
    supervisor.store.write_worker_status(
        {
            "status": "running",
            "pid": supervisor._process.pid,
            "heartbeat_unix": time.time(),
            "observed_dispatch_generation": generation,
            "dispatch_enabled": False,
            "background_tasks_quiesced": True,
            "active": 0,
        }
    )
    await supervisor.wait_for_operation(timeout=3)
    await supervisor.close()


@pytest.mark.asyncio
async def test_stop_cannot_complete_when_crashed_worker_registry_is_corrupt(
    tmp_path, monkeypatch
) -> None:
    state_dir = tmp_path / ".state"
    queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    supervisor = AgentWorkerSupervisor(state_dir=state_dir, trigger_queue=queue)
    supervisor.store.write(
        _lifecycle_state(
            desired_state="running",
            dispatch_enabled=True,
            dispatch_generation=1,
            generation=1,
        )
    )
    supervisor._process = MagicMock(pid=54321, returncode=1)
    supervisor._child_groups.path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(
        "zd_agent.services.agent_lifecycle.os.killpg",
        MagicMock(side_effect=ProcessLookupError),
    )

    await supervisor.stop(operation_id="crashed-stop-1")
    await supervisor.wait_for_operation(timeout=3)

    lifecycle = supervisor.store.read()
    assert lifecycle["desired_state"] == "running"
    assert lifecycle["transition_status"] == "blocked"
    assert lifecycle["transition_error"] == "ChildProcessGroupRegistryError"

    supervisor._child_groups.path.write_text("[]", encoding="utf-8")
    await supervisor.stop(operation_id="crashed-stop-2")
    await supervisor.wait_for_operation(timeout=3)

    assert supervisor.store.read()["desired_state"] == "stopped"
    assert supervisor.status()["process_running"] is False


@pytest.mark.asyncio
async def test_restart_queues_events_until_worker_fence_ack(tmp_path) -> None:
    state_dir = tmp_path / ".state"
    queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    supervisor = AgentWorkerSupervisor(
        state_dir=state_dir,
        trigger_queue=queue,
        command=(sys.executable, "-c", "import time; time.sleep(60)"),
        stop_timeout_seconds=2,
    )
    supervisor.store.write(
        _lifecycle_state(
            desired_state="running",
            dispatch_enabled=True,
            dispatch_generation=1,
            generation=1,
        )
    )
    await supervisor.initialize()

    status = await supervisor.restart()
    assert status["status"] == "restarting"
    assert status["desired_state"] == "running"
    assert supervisor.accepting_events() is True
    await queue.put(
        {"source": "during_restart", "ticket_id": 8, "_queue_key": "restart:8"}
    )
    generation = supervisor.store.read()["dispatch_generation"]
    supervisor.store.write_worker_status(
        {
            "status": "running",
            "pid": supervisor._process.pid,
            "heartbeat_unix": time.time(),
            "observed_dispatch_generation": generation,
            "dispatch_enabled": False,
            "background_tasks_quiesced": True,
            "active": 0,
        }
    )

    await supervisor.wait_for_operation(timeout=3)

    assert supervisor.store.read()["desired_state"] == "running"
    assert supervisor.store.read()["dispatch_enabled"] is True
    assert await queue.pending_count() == 1
    await supervisor.close()

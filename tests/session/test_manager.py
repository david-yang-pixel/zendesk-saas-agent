from types import SimpleNamespace
import asyncio
from unittest.mock import MagicMock, patch

import pytest

from zd_agent.session.agent_session import SessionStatus
from zd_agent.session.manager import SessionManager


@pytest.mark.asyncio
async def test_completed_session_history_keeps_only_the_most_recent_entries() -> None:
    async def complete_session(session) -> None:
        session.status = SessionStatus.COMPLETED

    cfg = SimpleNamespace(agent=SimpleNamespace(max_concurrent_sessions=1))
    with (
        patch("zd_agent.session.manager.get_config", return_value=cfg),
        patch("zd_agent.session.manager.run_agent", side_effect=complete_session),
        patch("zd_agent.session.manager.MAX_COMPLETED_SESSIONS", 2),
    ):
        manager = SessionManager(max_concurrent=1)
        for ticket_id in (101, 102, 103):
            await manager.dispatch(ticket_id)
            await manager.wait_all()

    assert manager.completed_count == 2
    assert [item.ticket_id for item in manager.get_recent_completed()] == [102, 103]


@pytest.mark.asyncio
async def test_existing_active_session_does_not_run_created_hook() -> None:
    release = asyncio.Event()

    async def wait_for_release(_session) -> None:
        await release.wait()

    cfg = SimpleNamespace(agent=SimpleNamespace(max_concurrent_sessions=1))
    created_hook = MagicMock()
    with (
        patch("zd_agent.session.manager.get_config", return_value=cfg),
        patch("zd_agent.session.manager.run_agent", side_effect=wait_for_release),
    ):
        manager = SessionManager(max_concurrent=1)
        first = await manager.dispatch(3461)
        second = await manager.dispatch(3461, on_session_created=created_hook)
        release.set()
        await manager.wait_all()

    assert second is first
    created_hook.assert_not_called()


@pytest.mark.asyncio
async def test_customer_webhook_marks_only_the_owned_active_session_dirty() -> None:
    release = asyncio.Event()

    async def wait_for_release(_session) -> None:
        await release.wait()

    cfg = SimpleNamespace(agent=SimpleNamespace(max_concurrent_sessions=1))
    with (
        patch("zd_agent.session.manager.get_config", return_value=cfg),
        patch("zd_agent.session.manager.run_agent", side_effect=wait_for_release),
    ):
        manager = SessionManager(max_concurrent=1)
        session = await manager.dispatch(3485)

        assert manager.mark_customer_context_dirty(
            3485,
            expected_session_id="stale-session",
            source="messaging_webhook",
            event_id="evt-stale",
        ) is False
        assert session.customer_context_dirty is False

        assert manager.mark_customer_context_dirty(
            3485,
            expected_session_id=session.session_id,
            source="messaging_webhook",
            event_id="evt-1",
        ) is True
        assert manager.mark_customer_context_dirty(
            3485,
            expected_session_id=session.session_id,
            source="messaging_webhook",
            event_id="evt-2",
        ) is True
        assert session.customer_context_dirty is True
        assert session.customer_context_generation == 2
        assert session.customer_context_source == "messaging_webhook"
        assert session.customer_context_event_id == "evt-2"

        release.set()
        await manager.wait_all()


@pytest.mark.asyncio
async def test_created_hook_runs_before_fast_session_completion() -> None:
    events: list[str] = []

    def bind_owner(_session) -> None:
        events.append("owner-bound")

    async def complete_session(session) -> None:
        events.append("run-started")
        session.status = SessionStatus.COMPLETED

    async def finished(_session) -> None:
        events.append("finished")

    cfg = SimpleNamespace(agent=SimpleNamespace(max_concurrent_sessions=1))
    with (
        patch("zd_agent.session.manager.get_config", return_value=cfg),
        patch("zd_agent.session.manager.run_agent", side_effect=complete_session),
    ):
        manager = SessionManager(max_concurrent=1, on_finished=finished)
        await manager.dispatch(3461, on_session_created=bind_owner)
        await manager.wait_all()

    assert events == ["owner-bound", "run-started", "finished"]
    assert manager.active_count == 0


@pytest.mark.asyncio
async def test_create_task_failure_removes_provisional_active_session() -> None:
    cfg = SimpleNamespace(agent=SimpleNamespace(max_concurrent_sessions=1))
    created_hook = MagicMock()
    with (
        patch("zd_agent.session.manager.get_config", return_value=cfg),
        patch(
            "zd_agent.session.manager.asyncio.create_task",
            side_effect=RuntimeError("cannot create task"),
        ),
    ):
        manager = SessionManager(max_concurrent=1)
        with pytest.raises(RuntimeError, match="cannot create task"):
            await manager.dispatch(3461, on_session_created=created_hook)

    created_hook.assert_called_once()
    assert manager.active_count == 0

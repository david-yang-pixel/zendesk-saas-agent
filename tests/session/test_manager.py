from types import SimpleNamespace
from unittest.mock import patch

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

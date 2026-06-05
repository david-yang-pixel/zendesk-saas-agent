from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_run_ticket_session_creates_and_runs_agent():
    from zd_agent.services.cli_sessions import run_ticket_session

    fake_session = MagicMock()
    agent_session_cls = MagicMock(return_value=fake_session)
    run_agent = AsyncMock()

    with (
        patch("zd_agent.services.cli_sessions.AgentSession", agent_session_cls),
        patch("zd_agent.services.cli_sessions.run_agent", run_agent),
    ):
        session = await run_ticket_session(
            ticket_id=123,
            hint="extra",
            dry_run=True,
        )

    agent_session_cls.assert_called_once_with(
        ticket_id=123,
        hint="extra",
        dry_run=True,
    )
    run_agent.assert_awaited_once_with(fake_session)
    assert session is fake_session


@pytest.mark.asyncio
async def test_run_batch_sessions_returns_cli_summary():
    from zd_agent.services.cli_sessions import run_batch_sessions

    session_a = MagicMock()
    session_a.ticket_id = 1
    session_a.status.value = "completed"
    session_a.round_count = 2
    session_a.token_usage.total_tokens = 100
    session_a.reply_sent = True

    session_b = MagicMock()
    session_b.ticket_id = 2
    session_b.status.value = "failed"
    session_b.round_count = 1
    session_b.token_usage.total_tokens = 50
    session_b.reply_sent = False

    manager = MagicMock()
    manager.dispatch_batch = AsyncMock(return_value=[session_a, session_b])
    manager.wait_all = AsyncMock()
    manager_cls = MagicMock(return_value=manager)

    with patch("zd_agent.services.cli_sessions.SessionManager", manager_cls):
        results = await run_batch_sessions(
            ticket_ids=[1, 2],
            hint="h",
            dry_run=True,
        )

    manager.dispatch_batch.assert_awaited_once_with(
        [1, 2],
        hint="h",
        dry_run=True,
    )
    manager.wait_all.assert_awaited_once_with()
    assert results == [
        {
            "ticket_id": 1,
            "status": "completed",
            "rounds": 2,
            "tokens": 100,
            "reply_sent": True,
        },
        {
            "ticket_id": 2,
            "status": "failed",
            "rounds": 1,
            "tokens": 50,
            "reply_sent": False,
        },
    ]

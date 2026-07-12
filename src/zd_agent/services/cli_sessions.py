"""Session execution helpers for CLI commands."""
from __future__ import annotations

from typing import Any

from zd_agent.config import get_config
from zd_agent.runtime.runner import run_agent
from zd_agent.services.app_bootstrap import create_configured_session_manager
from zd_agent.session.agent_session import AgentSession


async def run_ticket_session(
    *,
    ticket_id: int,
    hint: str,
    dry_run: bool,
) -> AgentSession:
    """Create and run a single ticket session for the CLI."""
    session = AgentSession(ticket_id=ticket_id, hint=hint, dry_run=dry_run)
    await run_agent(session)
    return session


async def run_batch_sessions(
    *,
    ticket_ids: list[int],
    hint: str,
    dry_run: bool,
) -> list[dict[str, Any]]:
    """Run a batch of tickets and return CLI summary rows."""
    manager = create_configured_session_manager(get_config())
    sessions = await manager.dispatch_batch(
        ticket_ids,
        hint=hint,
        dry_run=dry_run,
    )
    await manager.wait_all()
    return [
        {
            "ticket_id": session.ticket_id,
            "status": session.status.value,
            "rounds": session.round_count,
            "tokens": session.token_usage.total_tokens,
            "reply_sent": session.reply_sent,
        }
        for session in sessions
    ]

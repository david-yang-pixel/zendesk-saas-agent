from __future__ import annotations

from pathlib import Path

from zd_agent.casework.domain.enums import ExecutionMode
from zd_agent.casework.domain.shadow import ShadowArchiveRecord
from zd_agent.runtime.llm.types import LLMProvider
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import ToolRegistry


async def maybe_run_casework_shadow(
    *,
    mode: ExecutionMode,
    session: AgentSession,
    provider: LLMProvider,
    registry: ToolRegistry,
    archive_dir: Path,
    enable_vision: bool,
) -> ShadowArchiveRecord | None:
    """Run the observational path only after an explicit shadow-mode opt-in."""
    if mode is not ExecutionMode.SHADOW:
        return None
    from zd_agent.casework.adapters.shadow import run_casework_shadow_best_effort

    return await run_casework_shadow_best_effort(
        session=session,
        provider=provider,
        registry=registry,
        archive_dir=archive_dir,
        enable_vision=enable_vision,
    )

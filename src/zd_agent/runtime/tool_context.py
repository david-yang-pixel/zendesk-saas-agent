"""工具执行上下文，携带 session 级别状态。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from zd_agent.session.agent_session import AgentSession


@dataclass
class ToolUseContext:
    """工具执行上下文。

    当前阶段作为 AgentSession 的薄包装。
    Phase 6 (Subagent) 时会扩展为支持隔离的子上下文。
    """
    session: AgentSession
    sandbox_dir: Path | None = None
    abort_signal: asyncio.Event | None = None
    is_subagent: bool = False
    parent_session_id: str | None = None

    @classmethod
    def from_session(cls, session: AgentSession) -> ToolUseContext:
        return cls(session=session, sandbox_dir=session.sandbox_dir)

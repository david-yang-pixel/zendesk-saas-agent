"""SessionManager：多工单并发控制与生命周期管理

设计要点：
- 信号量控制并发上限（max_concurrent）
- 防重入：同一工单不重复处理
- Session 完成后自动取队列中下一个
- 提供活跃 session 查询接口（供 CLI status 使用）
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from zd_agent.config import get_config
from zd_agent.log import get_logger
from zd_agent.runtime.runner import run_agent
from zd_agent.session.agent_session import AgentSession, SessionStatus

logger = get_logger(module="session_manager")


@dataclass
class SessionInfo:
    """Session 摘要信息（供外部查询）"""

    ticket_id: int
    session_id: str
    status: SessionStatus
    round_count: int
    total_tokens: int
    start_time: float
    elapsed: float
    hint: str
    dry_run: bool


@dataclass
class _TrackedSession:
    """内部追踪的 session 条目"""

    session: AgentSession
    task: asyncio.Task[None] | None = None
    start_time: float = field(default_factory=time.monotonic)


class SessionManager:
    """工单会话并发管理器"""

    def __init__(self, max_concurrent: int | None = None) -> None:
        cfg = get_config()
        self._max_concurrent = max_concurrent or cfg.agent.max_concurrent_sessions
        self._semaphore = asyncio.Semaphore(self._max_concurrent)

        # ticket_id → _TrackedSession
        self._active: dict[int, _TrackedSession] = {}
        # 已完成的 session 记录（供 status 查看最近结果）
        self._completed: list[_TrackedSession] = []
        self._lock = asyncio.Lock()

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def completed_count(self) -> int:
        return len(self._completed)

    async def dispatch(
        self,
        ticket_id: int,
        hint: str = "",
        dry_run: bool = False,
        trigger_type: str = "auto",
    ) -> AgentSession:
        """派发工单处理任务

        - 如果同一工单正在处理，返回已有 session
        - 否则创建新 session，受信号量控制并发
        """
        async with self._lock:
            if ticket_id in self._active:
                logger.info(
                    "session_already_active",
                    ticket_id=ticket_id,
                    session_id=self._active[ticket_id].session.session_id,
                )
                return self._active[ticket_id].session

            session = AgentSession(
                ticket_id=ticket_id, hint=hint, dry_run=dry_run,
                trigger_type=trigger_type,
            )

            tracked = _TrackedSession(session=session)
            self._active[ticket_id] = tracked

            task = asyncio.create_task(
                self._run_with_semaphore(tracked),
                name=f"session-{ticket_id}",
            )
            tracked.task = task

            logger.info(
                "session_dispatched",
                ticket_id=ticket_id,
                session_id=session.session_id,
                active=len(self._active),
                max_concurrent=self._max_concurrent,
            )

            return session

    async def _run_with_semaphore(self, tracked: _TrackedSession) -> None:
        """在信号量保护下运行 session"""
        session = tracked.session
        ticket_id = session.ticket_id

        # 等待信号量（排队）
        if self._semaphore.locked():
            session.status = SessionStatus.QUEUED
            logger.info(
                "session_queued",
                ticket_id=ticket_id,
                waiting_for_slot=True,
            )

        async with self._semaphore:
            tracked.start_time = time.monotonic()
            try:
                await run_agent(session)
            except Exception as e:
                logger.error(
                    "session_crash",
                    ticket_id=ticket_id,
                    error=str(e),
                    exc_info=True,
                )
                session.status = SessionStatus.FAILED
            finally:
                async with self._lock:
                    self._active.pop(ticket_id, None)
                    self._completed.append(tracked)

                logger.info(
                    "session_finished",
                    ticket_id=ticket_id,
                    status=session.status.value,
                    rounds=session.round_count,
                    tokens=session.token_usage.total_tokens,
                    elapsed=f"{time.monotonic() - tracked.start_time:.1f}s",
                    remaining_active=len(self._active),
                )

    async def dispatch_batch(
        self,
        ticket_ids: list[int],
        hint: str = "",
        dry_run: bool = False,
        trigger_type: str = "auto",
    ) -> list[AgentSession]:
        """批量派发工单，返回所有 session（可并发执行）"""
        sessions = []
        for tid in ticket_ids:
            session = await self.dispatch(tid, hint=hint, dry_run=dry_run, trigger_type=trigger_type)
            sessions.append(session)
        return sessions

    async def wait_all(self) -> None:
        """等待所有活跃 session 完成"""
        async with self._lock:
            tasks = [
                t.task for t in self._active.values()
                if t.task is not None
            ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def get_active_sessions(self) -> list[SessionInfo]:
        """获取当前活跃 session 列表"""
        now = time.monotonic()
        result = []
        for tracked in self._active.values():
            s = tracked.session
            result.append(SessionInfo(
                ticket_id=s.ticket_id,
                session_id=s.session_id,
                status=s.status,
                round_count=s.round_count,
                total_tokens=s.token_usage.total_tokens,
                start_time=tracked.start_time,
                elapsed=now - tracked.start_time,
                hint=s.hint,
                dry_run=s.dry_run,
            ))
        return result

    def get_recent_completed(self, limit: int = 20) -> list[SessionInfo]:
        """获取最近完成的 session 列表"""
        result = []
        for tracked in self._completed[-limit:]:
            s = tracked.session
            result.append(SessionInfo(
                ticket_id=s.ticket_id,
                session_id=s.session_id,
                status=s.status,
                round_count=s.round_count,
                total_tokens=s.token_usage.total_tokens,
                start_time=tracked.start_time,
                elapsed=0,
                hint=s.hint,
                dry_run=s.dry_run,
            ))
        return result

    def get_all_sessions(self) -> dict[str, Any]:
        """获取全部 session 概览"""
        return {
            "max_concurrent": self._max_concurrent,
            "active": self.active_count,
            "completed": self.completed_count,
            "active_sessions": self.get_active_sessions(),
            "recent_completed": self.get_recent_completed(),
        }

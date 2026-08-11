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
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Final

from zd_agent.config import get_config
from zd_agent.log import get_logger
from zd_agent.runtime.runner import run_agent
from zd_agent.session.agent_session import AgentSession, SessionStatus

logger = get_logger(module="session_manager")

# 回调签名：(session) → coroutine。在 session 完成后异步触发，主要给 saas_app
# 用来更新 trigger state（last_agent_finish_ts）实现 post-agent cooldown。
SessionFinishedCallback = Callable[[AgentSession], Awaitable[None]]
SessionCreatedCallback = Callable[[AgentSession], None]
MAX_COMPLETED_SESSIONS: Final = 100


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

    def __init__(
        self,
        max_concurrent: int | None = None,
        *,
        on_finished: SessionFinishedCallback | None = None,
        force_dry_run: bool = False,
    ) -> None:
        cfg = get_config()
        self._max_concurrent = max_concurrent or cfg.agent.max_concurrent_sessions
        self._semaphore = asyncio.Semaphore(self._max_concurrent)

        # ticket_id → _TrackedSession
        self._active: dict[int, _TrackedSession] = {}
        # 已完成的 session 记录（供 status 查看最近结果）
        self._completed: deque[_TrackedSession] = deque(
            maxlen=MAX_COMPLETED_SESSIONS,
        )
        self._lock = asyncio.Lock()
        self._on_finished = on_finished
        self._force_dry_run = force_dry_run

    def set_on_finished(self, callback: SessionFinishedCallback | None) -> None:
        """注册 session 完成回调（典型用于 saas_app 在 state 创建后挂钩）。"""
        self._on_finished = callback

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def completed_count(self) -> int:
        return len(self._completed)

    def mark_customer_context_dirty(
        self,
        ticket_id: int,
        *,
        source: str,
        event_id: str = "",
        expected_session_id: str = "",
    ) -> bool:
        """Signal that an active session must re-fetch customer messages.

        All production callers run on the same asyncio event loop, so this
        small in-memory mutation is atomic without awaiting the manager lock.
        The optional owner fence prevents a delayed queue event from marking a
        newer session generation.
        """
        tracked = self._active.get(int(ticket_id))
        if tracked is None:
            return False
        session = tracked.session
        if expected_session_id and session.session_id != expected_session_id:
            logger.info(
                "customer_context_signal_stale_owner",
                ticket_id=ticket_id,
                expected_session_id=expected_session_id,
                active_session_id=session.session_id,
                source=source,
            )
            return False

        session.customer_context_generation += 1
        session.customer_context_dirty = True
        session.customer_context_source = str(source or "")[:80]
        session.customer_context_event_id = str(event_id or "")[:160]
        logger.info(
            "active_session_customer_context_marked_dirty",
            ticket_id=ticket_id,
            session_id=session.session_id,
            source=session.customer_context_source,
            event_id=session.customer_context_event_id or None,
            generation=session.customer_context_generation,
            round=session.round_count,
        )
        return True

    async def dispatch(
        self,
        ticket_id: int,
        hint: str = "",
        dry_run: bool = False,
        trigger_type: str = "auto",
        delivery_attempt_id: str = "",
        on_session_created: SessionCreatedCallback | None = None,
    ) -> AgentSession:
        """派发工单处理任务

        - 如果同一工单正在处理，返回已有 session
        - 否则创建新 session，受信号量控制并发
        """
        async with self._lock:
            if ticket_id in self._active:
                existing_session = self._active[ticket_id].session
                logger.info(
                    "session_already_active",
                    ticket_id=ticket_id,
                    session_id=self._active[ticket_id].session.session_id,
                )
                return existing_session

            session = AgentSession(
                ticket_id=ticket_id,
                hint=hint,
                dry_run=bool(dry_run or self._force_dry_run),
                trigger_type=trigger_type,
                delivery_attempt_id=delivery_attempt_id,
            )

            # Bind durable ownership before the background task can run.  A
            # very short session may otherwise finish between create_task()
            # and dispatch() returning to its caller.
            if on_session_created is not None:
                on_session_created(session)

            tracked = _TrackedSession(session=session)
            self._active[ticket_id] = tracked
            run_coro = self._run_with_semaphore(tracked)
            try:
                task = asyncio.create_task(
                    run_coro,
                    name=f"session-{ticket_id}",
                )
            except BaseException:
                run_coro.close()
                self._active.pop(ticket_id, None)
                raise
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
                session_id=session.session_id,
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
                    session_id=session.session_id,
                    status=session.status.value,
                    rounds=session.round_count,
                    tokens=session.token_usage.total_tokens,
                    elapsed=f"{time.monotonic() - tracked.start_time:.1f}s",
                    remaining_active=len(self._active),
                )

                # 触发完成回调（用于更新 trigger state 的 last_agent_finish_ts）
                # 失败不影响 session 结果，只 warn。
                if self._on_finished is not None:
                    try:
                        await self._on_finished(session)
                    except Exception as e:
                        logger.warning(
                            "session_on_finished_callback_failed",
                            ticket_id=ticket_id,
                            error=str(e),
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
            session = await self.dispatch(
                tid, hint=hint, dry_run=dry_run, trigger_type=trigger_type
            )
            sessions.append(session)
        return sessions

    async def wait_all(self) -> None:
        """等待所有活跃 session 完成"""
        async with self._lock:
            tasks = [t.task for t in self._active.values() if t.task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def get_active_sessions(self) -> list[SessionInfo]:
        """获取当前活跃 session 列表"""
        now = time.monotonic()
        result = []
        for tracked in self._active.values():
            s = tracked.session
            result.append(
                SessionInfo(
                    ticket_id=s.ticket_id,
                    session_id=s.session_id,
                    status=s.status,
                    round_count=s.round_count,
                    total_tokens=s.token_usage.total_tokens,
                    start_time=tracked.start_time,
                    elapsed=now - tracked.start_time,
                    hint=s.hint,
                    dry_run=s.dry_run,
                )
            )
        return result

    def get_recent_completed(self, limit: int = 20) -> list[SessionInfo]:
        """获取最近完成的 session 列表"""
        result = []
        for tracked in list(self._completed)[-limit:]:
            s = tracked.session
            result.append(
                SessionInfo(
                    ticket_id=s.ticket_id,
                    session_id=s.session_id,
                    status=s.status,
                    round_count=s.round_count,
                    total_tokens=s.token_usage.total_tokens,
                    start_time=tracked.start_time,
                    elapsed=0,
                    hint=s.hint,
                    dry_run=s.dry_run,
                )
            )
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

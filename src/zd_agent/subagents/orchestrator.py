from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from zd_agent.runtime.config_snapshot import (
    CaseworkRuntimeSnapshot,
    SubagentSlotRuntimeSnapshot,
)
from zd_agent.session.agent_session import AgentSession
from zd_agent.subagents.models import (
    TERMINAL_SUBAGENT_STATUSES,
    SubagentTask,
)
from zd_agent.subagents.store import SubagentStore


SubagentRunner = Callable[
    [SubagentTask, SubagentSlotRuntimeSnapshot, AgentSession],
    Awaitable[dict[str, object]],
]


class SubagentOrchestrator:
    """Depth-one child scheduler with a pool separate from root sessions."""

    def __init__(
        self,
        *,
        store: SubagentStore,
        snapshot: CaseworkRuntimeSnapshot,
        root_session_id: str,
        ticket_id: int,
        runner: SubagentRunner,
    ) -> None:
        self._store = store
        self._snapshot = snapshot
        self._root_session_id = root_session_id
        self._ticket_id = ticket_id
        self._runner = runner
        self._slots = {slot.id: slot for slot in snapshot.subagent_slots}
        self._semaphore = asyncio.Semaphore(snapshot.subagent_max_parallel)
        self._running: dict[str, asyncio.Task[None]] = {}

    def spawn(
        self,
        *,
        slot_id: str,
        objective: str,
        idempotency_key: str,
    ) -> SubagentTask:
        if not self._snapshot.subagents_enabled:
            raise RuntimeError("subagent orchestration is disabled")
        slot_id = slot_id.strip()
        objective = objective.strip()
        idempotency_key = idempotency_key.strip()
        if not slot_id or len(slot_id) > 64:
            raise ValueError("slot_id must contain 1-64 non-whitespace characters")
        if not objective or len(objective) > 4_000:
            raise ValueError("objective must contain 1-4000 non-whitespace characters")
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError(
                "idempotency_key must contain 1-128 non-whitespace characters"
            )
        slot = self._slots.get(slot_id)
        if slot is None:
            raise ValueError(f"unknown or disabled subagent slot: {slot_id}")
        existing = self._store.get_by_key(self._root_session_id, idempotency_key)
        if existing is not None:
            return existing
        if (
            len(self._store.list_for_root(self._root_session_id))
            >= self._snapshot.subagent_max_tasks_per_run
        ):
            raise RuntimeError("subagent task limit reached for this root session")
        task = self._store.create(
            root_session_id=self._root_session_id,
            parent_session_id=self._root_session_id,
            ticket_id=self._ticket_id,
            slot_id=slot_id,
            objective=objective,
            idempotency_key=idempotency_key,
            config_digest=self._snapshot.config_digest,
            model_snapshot={
                "provider": slot.llm.provider,
                "model": slot.llm.model,
                "reasoning_effort": slot.llm.reasoning_effort,
                "base_url": slot.llm.base_url,
            },
        )
        self._running[task.id] = asyncio.create_task(
            self._run(task, slot), name=f"subagent-{task.id[:8]}"
        )
        return task

    async def _run(
        self, task: SubagentTask, slot: SubagentSlotRuntimeSnapshot
    ) -> None:
        session = AgentSession(
            ticket_id=task.ticket_id,
            root_session_id=task.root_session_id,
            parent_session_id=task.parent_session_id,
            session_kind="subagent",
            subagent_task_id=task.id,
            subagent_slot_id=task.slot_id,
            runtime_config_digest=task.config_digest,
            trigger_type="subagent",
            dry_run=True,
        )
        try:
            async with self._semaphore:
                self._store.mark_running(task.id, session_id=session.session_id)
                result = await asyncio.wait_for(
                    self._runner(task, slot, session),
                    timeout=self._snapshot.subagent_task_timeout_seconds,
                )
            self._store.complete(task.id, result=dict(result))
        except TimeoutError:
            self._store.timeout(task.id)
        except asyncio.CancelledError:
            self._store.cancel(task.id)
            raise
        except Exception as error:  # noqa: BLE001 - persisted typed failure boundary
            self._store.fail(
                task.id,
                code=type(error).__name__,
                detail=str(error)[:2000],
            )

    async def wait(
        self, task_ids: tuple[str, ...], *, timeout_seconds: float
    ) -> tuple[SubagentTask, ...]:
        pending = [self._running[task_id] for task_id in task_ids if task_id in self._running]
        if pending:
            await asyncio.wait(pending, timeout=max(timeout_seconds, 0.0))
        tasks: list[SubagentTask] = []
        for task_id in task_ids:
            task = self._store.get(task_id)
            if task is None or task.root_session_id != self._root_session_id:
                raise KeyError(task_id)
            tasks.append(task)
        return tuple(tasks)

    def status(self, task_id: str) -> SubagentTask:
        task = self._store.get(task_id)
        if task is None or task.root_session_id != self._root_session_id:
            raise KeyError(task_id)
        return task

    async def cancel(self, task_id: str) -> SubagentTask:
        current = self.status(task_id)
        if current.status in TERMINAL_SUBAGENT_STATUSES:
            return current
        running = self._running.get(task_id)
        if running is not None:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        current = self.status(task_id)
        if current.status not in TERMINAL_SUBAGENT_STATUSES:
            self._store.cancel(task_id)
        return self.status(task_id)

    async def cancel_all(self) -> None:
        await asyncio.gather(
            *(self.cancel(task.id) for task in self._store.list_for_root(self._root_session_id)),
            return_exceptions=True,
        )

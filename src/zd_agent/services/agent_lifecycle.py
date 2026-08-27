"""Persistent control-plane ownership of the Agent worker process."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from zd_agent.log import get_logger
from zd_agent.services.atomic_write import atomic_write_text
from zd_agent.services.child_process_registry import (
    CHILD_PROCESS_GROUP_REGISTRY_ENV,
    ChildProcessGroupRegistry,
)

logger = get_logger(module="services.agent_lifecycle")

LIFECYCLE_FILE = "agent_lifecycle.json"
LIFECYCLE_INITIALIZED_FILE = "agent_lifecycle.initialized"
WORKER_STATUS_FILE = "agent_worker_status.json"
ACCEPT_AFTER_ENV = "ZD_AGENT_ACCEPT_EVENTS_AFTER"
NEW_EPOCH_ENV = "ZD_AGENT_NEW_EPOCH"
FORCE_DRY_RUN_ENV = "ZD_AGENT_FORCE_DRY_RUN"

SpawnFn = Callable[..., Awaitable[asyncio.subprocess.Process]]
TransitionCallback = Callable[[dict[str, Any]], None]


def _utc_iso(timestamp: float | None = None) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(timestamp or time.time(), tz=timezone.utc).isoformat()


def _iso_unix(value: object) -> float | None:
    from datetime import datetime

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _default_lifecycle() -> dict[str, Any]:
    return {
        "desired_state": "stopped",
        "dispatch_enabled": False,
        "dispatch_generation": 0,
        "generation": 0,
        "accept_after": 0.0,
        "stopped_at": "",
        "started_at": "",
        "dependency_interlock_active": False,
        "dependency_interlock_dependency": "",
        "dependency_interlock_reason": "",
        "dependency_interlock_changed_at": "",
        "dependency_interlock_resume_requested": False,
        "transition_allow_preserved_outbox": False,
    }


def _fail_closed_lifecycle(error: str) -> dict[str, Any]:
    return {
        **_default_lifecycle(),
        "desired_state": "stopped",
        "dispatch_enabled": False,
        "state_error": error,
    }


class AgentLifecycleStore:
    """Durable lifecycle state; existing unreadable state always fails closed."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.path = state_dir / LIFECYCLE_FILE
        self.initialized_path = state_dir / LIFECYCLE_INITIALIZED_FILE
        self.worker_status_path = state_dir / WORKER_STATUS_FILE
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    def read(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            if self.initialized_path.exists():
                logger.error("agent_lifecycle_missing_after_initialization")
                return _fail_closed_lifecycle("FileNotFoundError")
            return {**_default_lifecycle(), "state_missing": True}
        except OSError as error:
            logger.error("agent_lifecycle_read_failed", error_type=type(error).__name__)
            return _fail_closed_lifecycle(type(error).__name__)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            logger.error("agent_lifecycle_corrupt", error_type=type(error).__name__)
            return _fail_closed_lifecycle(type(error).__name__)
        if not isinstance(value, dict):
            return _fail_closed_lifecycle("invalid_top_level")
        try:
            required = {
                "desired_state",
                "dispatch_enabled",
                "dispatch_generation",
                "generation",
                "accept_after",
            }
            if not required.issubset(value):
                raise ValueError("missing required lifecycle fields")
            desired = value["desired_state"]
            if not isinstance(desired, str):
                raise TypeError("desired_state must be a string")
            if desired not in {"running", "stopped"}:
                raise ValueError("invalid desired_state")
            if not isinstance(value["dispatch_enabled"], bool):
                raise TypeError("dispatch_enabled must be a boolean")
            for field in ("dispatch_generation", "generation"):
                if isinstance(value[field], bool) or not isinstance(value[field], int):
                    raise TypeError(f"{field} must be an integer")
            if isinstance(value["accept_after"], bool) or not isinstance(
                value["accept_after"], int | float
            ):
                raise TypeError("accept_after must be numeric")
            return {
                **_default_lifecycle(),
                **value,
                "desired_state": desired,
                "dispatch_enabled": value["dispatch_enabled"],
                "dispatch_generation": max(
                    0, int(value.get("dispatch_generation") or 0)
                ),
                "generation": max(0, int(value.get("generation") or 0)),
                "accept_after": max(0.0, float(value.get("accept_after") or 0.0)),
            }
        except (TypeError, ValueError) as error:
            logger.error("agent_lifecycle_invalid", error_type=type(error).__name__)
            return _fail_closed_lifecycle(type(error).__name__)

    def write(self, value: dict[str, Any]) -> None:
        payload = dict(value)
        payload.pop("state_missing", None)
        payload.pop("state_error", None)
        atomic_write_text(
            self.path,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        )
        self.path.chmod(0o600)
        if not self.initialized_path.exists():
            atomic_write_text(self.initialized_path, "initialized\n")
            self.initialized_path.chmod(0o600)

    def read_worker_status(self) -> dict[str, Any]:
        try:
            raw = self.worker_status_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"status": "unknown", "status_error": "missing"}
        except OSError as error:
            return {"status": "unknown", "status_error": type(error).__name__}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            return {"status": "unknown", "status_error": type(error).__name__}
        if not isinstance(value, dict):
            return {"status": "unknown", "status_error": "invalid_top_level"}
        return value

    def write_worker_status(self, value: dict[str, Any]) -> None:
        atomic_write_text(
            self.worker_status_path,
            json.dumps(value, ensure_ascii=False, sort_keys=True, default=str),
        )
        self.worker_status_path.chmod(0o600)


class ControlPlaneSessionView:
    """Read-only SessionManager-compatible view backed by worker heartbeat."""

    def __init__(self, lifecycle_store: AgentLifecycleStore) -> None:
        self._store = lifecycle_store

    @property
    def active_count(self) -> int:
        return int(self._store.read_worker_status().get("active") or 0)

    def get_all_sessions(self) -> dict[str, Any]:
        status = self._store.read_worker_status()
        return {
            "active": int(status.get("active") or 0),
            "max_concurrent": int(status.get("max_concurrent") or 0),
            "active_sessions": status.get("active_sessions") or [],
            "recent_completed": status.get("recent_completed") or [],
        }

    def mark_customer_context_dirty(self, *_args: Any, **_kwargs: Any) -> bool:
        return False


class AgentWorkerSupervisor:
    """Keep the control plane online while owning one isolated Agent process."""

    def __init__(
        self,
        *,
        state_dir: Path,
        trigger_queue: Any,
        spawn_fn: SpawnFn = asyncio.create_subprocess_exec,
        command: tuple[str, ...] | None = None,
        stop_timeout_seconds: float = 40.0,
        drain_timeout_seconds: float = 1_500.0,
    ) -> None:
        self.store = AgentLifecycleStore(state_dir)
        self.trigger_queue = trigger_queue
        self._spawn = spawn_fn
        self._command = command or (
            sys.executable,
            "-m",
            "zd_agent.entry.cli",
            "worker",
        )
        self._stop_timeout_seconds = stop_timeout_seconds
        self._drain_timeout_seconds = drain_timeout_seconds
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._monitor_task: asyncio.Task[None] | None = None
        self._operation_task: asyncio.Task[None] | None = None
        self._closed = False
        self._runtime_state = "stopped"
        self._last_error = ""
        self._intentional_pids: set[int] = set()
        self._transition_callback: TransitionCallback | None = None
        self._child_groups = ChildProcessGroupRegistry(
            state_dir / "worker_child_process_groups.json"
        )

    def set_transition_callback(self, callback: TransitionCallback | None) -> None:
        self._transition_callback = callback

    def ensure_terminal_audit_delivered(self) -> None:
        """Prevent a later lifecycle action from overwriting an audit outbox."""
        lifecycle = self.store.read()
        self._redeliver_terminal_transition(lifecycle)
        current = self.store.read()
        terminal = str(current.get("transition_status") or "")
        if (
            self._transition_callback is not None
            and terminal in {"completed", "blocked"}
            and current.get("transition_audit_status") != terminal
        ):
            raise AgentLifecycleConflict("audit_delivery_pending")

    async def initialize(self) -> None:
        self._closed = False
        lifecycle = self.store.read()
        if lifecycle.get("state_missing"):
            # A missing lifecycle file is not proof that processing was
            # enabled. First boot is deliberately control-plane-only.
            lifecycle = _default_lifecycle()
            self.store.write(lifecycle)
        elif (
            not lifecycle.get("state_error")
            and not self.store.initialized_path.exists()
        ):
            # Upgrade an existing pre-control-plane lifecycle file by creating
            # the independent initialization marker before any Worker starts.
            self.store.write(lifecycle)
        if lifecycle.get("state_error"):
            self._runtime_state = "failed"
            self._last_error = "lifecycle_state_unreadable"
            return
        if lifecycle.get("dependency_interlock_active"):
            # A dependency failure persisted across a control-plane restart.
            # No Worker is running yet, so close the prior epoch without
            # replaying any work accumulated while the dependency was unsafe.
            resume_requested = bool(
                lifecycle.get("dependency_interlock_resume_requested")
                or lifecycle.get("desired_state") == "running"
            )
            lifecycle.update(
                {
                    "desired_state": "stopped",
                    "dispatch_enabled": False,
                    "dependency_interlock_resume_requested": resume_requested,
                    "stopped_at": _utc_iso(),
                    "transition_action": "dependency_stop",
                    "transition_status": "completed",
                    "transition_completed_at": _utc_iso(),
                }
            )
            self.store.write(lifecycle)
            await self.trigger_queue.discard_backlog("dependency_interlock_startup")
            self._runtime_state = "stopped"
            return
        self._redeliver_terminal_transition(lifecycle)
        lifecycle = self.store.read()
        if lifecycle["desired_state"] == "running":
            transition_action = str(lifecycle.get("transition_action") or "")
            transition_pending = lifecycle.get("transition_status") == "pending"
            transition_generation = int(lifecycle.get("transition_generation") or -1)
            if not lifecycle.get("dispatch_enabled") and not (
                transition_pending
                and transition_action in {"stop", "restart"}
                and transition_generation
                == int(lifecycle.get("dispatch_generation") or 0)
            ):
                self._runtime_state = "failed"
                self._last_error = "incomplete_lifecycle_transition"
                return
            async with self._lock:
                await self._spawn_worker_locked(new_epoch=False)
                if transition_pending:
                    self._runtime_state = (
                        "stopping" if transition_action == "stop" else "restarting"
                    )
                    self._operation_task = asyncio.create_task(
                        self._drain_and_transition(
                            action=transition_action,
                            generation=transition_generation,
                        ),
                        name=f"agent-{transition_action}-{transition_generation}",
                    )

    async def close(self) -> None:
        self._closed = True
        operation = self._operation_task
        if operation is not None and not operation.done():
            operation.cancel()
            try:
                await operation
            except asyncio.CancelledError:
                pass
        async with self._lock:
            await self._terminate_worker_locked(force=True)
        monitor = self._monitor_task
        if monitor is not None and not monitor.done():
            monitor.cancel()
            try:
                await monitor
            except asyncio.CancelledError:
                pass

    def accepting_events(self) -> bool:
        """Fail closed unless both lifecycle and dependency fences are open."""
        lifecycle = self.store.read()
        return bool(
            not lifecycle.get("state_error")
            and lifecycle["desired_state"] == "running"
            and not lifecycle.get("dependency_interlock_active")
        )

    def ingress_state(self) -> str:
        lifecycle = self.store.read()
        if lifecycle.get("state_error"):
            return "unavailable"
        return "queue" if self.accepting_events() else "stopped"

    def accept_after(self) -> float:
        return float(self.store.read().get("accept_after") or 0.0)

    def record_dropped_event(self, *, source: str, ticket_id: str) -> None:
        lifecycle = self.store.read()
        if lifecycle.get("state_error"):
            return
        lifecycle.update(
            {
                "dropped_events": int(lifecycle.get("dropped_events") or 0) + 1,
                "last_drop_at": _utc_iso(),
                "last_drop_source": str(source)[:64],
                "last_drop_ticket_id": str(ticket_id)[:32],
            }
        )
        self.store.write(lifecycle)

    def _current_worker_status(self) -> tuple[dict[str, Any], bool]:
        worker = self.store.read_worker_status()
        process = self._process
        live = bool(process is not None and process.returncode is None)
        if not live or worker.get("status_error"):
            return worker, False
        if int(worker.get("pid") or 0) != int(process.pid):
            return worker, False
        heartbeat_at = float(worker.get("heartbeat_unix") or 0.0)
        return worker, bool(heartbeat_at and time.time() - heartbeat_at <= 10)

    def status(self) -> dict[str, Any]:
        lifecycle = self.store.read()
        worker, worker_fresh = self._current_worker_status()
        process = self._process
        live = bool(process is not None and process.returncode is None)
        heartbeat_at = float(worker.get("heartbeat_unix") or 0.0)
        heartbeat_age = max(0.0, time.time() - heartbeat_at) if heartbeat_at else None
        task_health = worker.get("background_tasks") or {}
        all_tasks_ready = bool(task_health) and all(task_health.values())
        fence_matches = bool(
            int(worker.get("observed_dispatch_generation") or -1)
            == int(lifecycle.get("dispatch_generation") or 0)
            and bool(worker.get("dispatch_enabled"))
            == bool(lifecycle.get("dispatch_enabled"))
        )
        state = self._runtime_state
        if lifecycle.get("state_error"):
            state = "failed"
        elif lifecycle["desired_state"] == "stopped":
            state = "stopped"
        elif state in {"stopping", "restarting"}:
            pass
        elif worker_fresh and all_tasks_ready and fence_matches:
            state = "running"
        elif live:
            state = "starting"
        elif state != "failed":
            state = "restarting"
        return {
            "status": state,
            "desired_state": lifecycle["desired_state"],
            "accepting_events": self.accepting_events(),
            "dispatch_enabled": bool(lifecycle.get("dispatch_enabled")),
            "process_running": live,
            "pid": process.pid if live and process is not None else None,
            "generation": lifecycle["generation"],
            "dispatch_generation": lifecycle.get("dispatch_generation") or 0,
            "started_at": lifecycle.get("started_at") or "",
            "stopped_at": lifecycle.get("stopped_at") or "",
            "accept_after": lifecycle.get("accept_after") or 0.0,
            "active": int(worker.get("active") or 0) if worker_fresh else 0,
            "active_sessions": worker.get("active_sessions") or [],
            "heartbeat_age_seconds": (
                round(heartbeat_age, 1) if heartbeat_age is not None else None
            ),
            "worker_ready": worker_fresh and all_tasks_ready and fence_matches,
            "background_tasks": task_health,
            "last_error": self._last_error or str(lifecycle.get("state_error") or ""),
            "transition_action": lifecycle.get("transition_action") or "",
            "transition_status": lifecycle.get("transition_status") or "",
            "transition_operation_id": lifecycle.get("transition_operation_id") or "",
            "transition_error": lifecycle.get("transition_error") or "",
            "transition_blockers": lifecycle.get("transition_blockers") or {},
            "offline_backlog_policy": "discard",
            "dropped_events": int(lifecycle.get("dropped_events") or 0),
            "last_drop_at": lifecycle.get("last_drop_at") or "",
            "dependency_interlock": {
                "active": bool(lifecycle.get("dependency_interlock_active")),
                "dependency": lifecycle.get("dependency_interlock_dependency") or "",
                "reason": lifecycle.get("dependency_interlock_reason") or "",
                "changed_at": lifecycle.get("dependency_interlock_changed_at") or "",
                "resume_requested": bool(
                    lifecycle.get("dependency_interlock_resume_requested")
                ),
            },
        }

    async def engage_dependency_interlock(
        self,
        *,
        dependency: str,
        reason: str,
    ) -> dict[str, Any]:
        """Fence ingress immediately, then stop the Worker without losing outbox state."""
        dependency = str(dependency).strip()[:128]
        reason = str(reason).strip()[:256]
        discard_without_worker = False
        async with self._lock:
            lifecycle = self.store.read()
            if lifecycle.get("state_error"):
                raise AgentLifecycleConflict("lifecycle_state_unreadable")
            if (
                lifecycle.get("dependency_interlock_active")
                and lifecycle.get("dependency_interlock_dependency") == dependency
                and lifecycle.get("dependency_interlock_reason") == reason
                and not lifecycle.get("dispatch_enabled")
                and (
                    lifecycle["desired_state"] == "stopped"
                    or lifecycle.get("transition_status") == "pending"
                )
            ):
                return self.status()

            interlock = {
                "dependency_interlock_active": True,
                "dependency_interlock_dependency": dependency,
                "dependency_interlock_reason": reason,
                "dependency_interlock_changed_at": _utc_iso(),
                "dependency_interlock_resume_requested": bool(
                    lifecycle.get("dependency_interlock_resume_requested")
                    or lifecycle.get("desired_state") == "running"
                ),
            }
            if lifecycle["desired_state"] == "stopped":
                lifecycle.update(interlock)
                lifecycle["dispatch_enabled"] = False
                self.store.write(lifecycle)
                return self.status()

            if self._process is None or self._process.returncode is not None:
                lifecycle.update(
                    {
                        **interlock,
                        "desired_state": "stopped",
                        "dispatch_enabled": False,
                        "dispatch_generation": int(
                            lifecycle.get("dispatch_generation") or 0
                        )
                        + 1,
                        "stopped_at": _utc_iso(),
                        "transition_action": "dependency_stop",
                        "transition_status": "completed",
                        "transition_completed_at": _utc_iso(),
                    }
                )
                self.store.write(lifecycle)
                self._runtime_state = "stopped"
                discard_without_worker = True
            elif (
                self._operation_task is not None and not self._operation_task.done()
            ) or lifecycle.get("transition_status") == "pending":
                # An existing stop/restart fence already prevents dispatch.
                lifecycle.update(interlock)
                lifecycle["dispatch_enabled"] = False
                self.store.write(lifecycle)
            else:
                self._start_drain_locked(
                    action="stop",
                    operation_id=f"dependency-{int(time.time())}",
                    actor="system",
                    reason=f"{dependency}:{reason}",
                    ip="local",
                    allow_preserved_outbox=True,
                )
                lifecycle = self.store.read()
                lifecycle.update(interlock)
                self.store.write(lifecycle)
        if discard_without_worker:
            await self.trigger_queue.discard_backlog("dependency_interlock")
        return self.status()

    async def clear_dependency_interlock(self, *, dependency: str) -> dict[str, Any]:
        """Clear the alarm and resume only when the pre-failure intent was running."""
        should_resume = False
        async with self._lock:
            self.ensure_terminal_audit_delivered()
            lifecycle = self.store.read()
            if lifecycle.get("state_error"):
                raise AgentLifecycleConflict("lifecycle_state_unreadable")
            current = str(lifecycle.get("dependency_interlock_dependency") or "")
            if current and current != str(dependency).strip():
                raise AgentLifecycleConflict("dependency_interlock_mismatch")
            process_live = bool(
                self._process is not None and self._process.returncode is None
            )
            if lifecycle["desired_state"] != "stopped" and process_live:
                raise AgentLifecycleConflict("dependency_stop_in_progress")
            if lifecycle["desired_state"] != "stopped":
                # Recovery detected during control-plane startup. Keep the
                # operator-visible state stopped; readiness recovery never
                # revives a prior Agent epoch automatically.
                lifecycle.update(
                    {
                        "desired_state": "stopped",
                        "dispatch_enabled": False,
                        "stopped_at": _utc_iso(),
                    }
                )
            should_resume = bool(lifecycle.get("dependency_interlock_resume_requested"))
            lifecycle.update(
                {
                    "dependency_interlock_active": False,
                    "dependency_interlock_dependency": "",
                    "dependency_interlock_reason": "",
                    "dependency_interlock_changed_at": _utc_iso(),
                }
            )
            if not should_resume:
                lifecycle["dependency_interlock_resume_requested"] = False
            self.store.write(lifecycle)
        if not should_resume:
            return self.status()
        status = await self.start(
            operation_id=f"dependency-recovered-{int(time.time())}",
            actor="system",
            reason=f"{dependency}:recovered",
            ip="local",
        )
        async with self._lock:
            lifecycle = self.store.read()
            lifecycle["dependency_interlock_resume_requested"] = False
            self.store.write(lifecycle)
        return status

    async def start(
        self,
        *,
        operation_id: str = "",
        actor: str = "",
        reason: str = "",
        ip: str = "",
    ) -> dict[str, Any]:
        async with self._lock:
            self.ensure_terminal_audit_delivered()
            lifecycle = self.store.read()
            if lifecycle.get("state_error"):
                raise AgentLifecycleConflict("lifecycle_state_unreadable")
            if lifecycle.get("dependency_interlock_active"):
                raise AgentLifecycleConflict("dependency_interlock_active")
            if (
                self._operation_task is not None and not self._operation_task.done()
            ) or lifecycle.get("transition_status") == "pending":
                raise AgentLifecycleConflict("lifecycle_operation_in_progress")
            if lifecycle["desired_state"] == "running":
                if not lifecycle.get("dispatch_enabled"):
                    raise AgentLifecycleConflict("incomplete_lifecycle_transition")
                if self._process is None or self._process.returncode is not None:
                    await self._spawn_worker_locked(new_epoch=False)
                now = time.time()
                lifecycle.update(
                    {
                        "transition_action": "start",
                        "transition_status": "completed",
                        "transition_operation_id": operation_id,
                        "transition_actor": actor,
                        "transition_reason": reason,
                        "transition_ip": ip,
                        "transition_completed_at": _utc_iso(now),
                        "transition_audit_status": "pending",
                        "last_operation_id": operation_id,
                        "last_operation_action": "start",
                    }
                )
                self.store.write(lifecycle)
                return self.status()
            await self.trigger_queue.discard_backlog("agent_start_watermark")
            self._migrate_old_epoch_states()
            now = time.time()
            # Prefer a conservative offline boundary: timestamps with only
            # whole-second precision that equal this instant are discarded.
            # This can omit an event created in the remaining fraction of the
            # same second, but it guarantees that stopped-period work is never
            # replayed after an explicit Start.
            accept_after = now
            lifecycle.update(
                {
                    "desired_state": "running",
                    "dispatch_enabled": True,
                    "dispatch_generation": int(
                        lifecycle.get("dispatch_generation") or 0
                    )
                    + 1,
                    "generation": int(lifecycle["generation"]) + 1,
                    "accept_after": accept_after,
                    "started_at": _utc_iso(now),
                    "transition_action": "start",
                    "transition_status": "completed",
                    "transition_operation_id": operation_id,
                    "transition_actor": actor,
                    "transition_reason": reason,
                    "transition_ip": ip,
                    "transition_completed_at": _utc_iso(now),
                    "transition_audit_status": "pending",
                    "last_operation_id": operation_id,
                    "last_operation_action": "start",
                }
            )
            self.store.write(lifecycle)
            await self._spawn_worker_locked(new_epoch=True)
            return self.status()

    async def stop(
        self,
        *,
        operation_id: str = "",
        actor: str = "",
        reason: str = "",
        ip: str = "",
    ) -> tuple[dict[str, Any], int]:
        async with self._lock:
            self.ensure_terminal_audit_delivered()
            lifecycle = self.store.read()
            if lifecycle.get("state_error"):
                raise AgentLifecycleConflict("lifecycle_state_unreadable")
            if lifecycle["desired_state"] == "stopped":
                now = time.time()
                lifecycle.update(
                    {
                        "dependency_interlock_resume_requested": False,
                        "transition_action": "stop",
                        "transition_status": "completed",
                        "transition_operation_id": operation_id,
                        "transition_actor": actor,
                        "transition_reason": reason,
                        "transition_ip": ip,
                        "transition_completed_at": _utc_iso(now),
                        "transition_audit_status": "pending",
                        "last_operation_id": operation_id,
                        "last_operation_action": "stop",
                    }
                )
                self.store.write(lifecycle)
                return self.status(), 0
            blockers = self._outbox_blockers()
            if blockers:
                raise AgentLifecycleConflict("unsafe_outbox", blockers=blockers)
            self._start_drain_locked(
                action="stop",
                operation_id=operation_id,
                actor=actor,
                reason=reason,
                ip=ip,
            )
            return self.status(), 0

    async def restart(
        self,
        *,
        operation_id: str = "",
        actor: str = "",
        reason: str = "",
        ip: str = "",
    ) -> dict[str, Any]:
        async with self._lock:
            self.ensure_terminal_audit_delivered()
            lifecycle = self.store.read()
            if lifecycle.get("state_error"):
                raise AgentLifecycleConflict("lifecycle_state_unreadable")
            if lifecycle["desired_state"] != "running":
                raise AgentLifecycleConflict("agent_stopped")
            blockers = self._outbox_blockers()
            if blockers:
                raise AgentLifecycleConflict("unsafe_outbox", blockers=blockers)
            self._start_drain_locked(
                action="restart",
                operation_id=operation_id,
                actor=actor,
                reason=reason,
                ip=ip,
            )
            return self.status()

    async def wait_for_operation(self, timeout: float = 5.0) -> None:
        task = self._operation_task
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)

    def _start_drain_locked(
        self,
        *,
        action: str,
        operation_id: str,
        actor: str,
        reason: str,
        ip: str,
        allow_preserved_outbox: bool = False,
    ) -> None:
        if self._operation_task is not None and not self._operation_task.done():
            raise AgentLifecycleConflict("lifecycle_operation_in_progress")
        lifecycle = self.store.read()
        started_at = time.time()
        lifecycle.update(
            {
                "dispatch_enabled": False,
                "dispatch_generation": int(lifecycle.get("dispatch_generation") or 0)
                + 1,
                "transition_action": action,
                "transition_status": "pending",
                "transition_generation": int(lifecycle.get("dispatch_generation") or 0)
                + 1,
                "transition_operation_id": operation_id,
                "transition_actor": actor,
                "transition_reason": reason,
                "transition_ip": ip,
                "transition_started_at": _utc_iso(started_at),
                "transition_deadline_at": _utc_iso(
                    started_at + self._drain_timeout_seconds
                ),
                "transition_audit_status": "pending",
                "transition_error": "",
                "transition_blockers": {},
                "transition_allow_preserved_outbox": allow_preserved_outbox,
                "last_operation_id": operation_id,
                "last_operation_action": action,
            }
        )
        self.store.write(lifecycle)
        self._runtime_state = "stopping" if action == "stop" else "restarting"
        self._last_error = ""
        generation = int(lifecycle["dispatch_generation"])
        self._operation_task = asyncio.create_task(
            self._drain_and_transition(
                action=action,
                generation=generation,
                allow_preserved_outbox=allow_preserved_outbox,
            ),
            name=f"agent-{action}-{generation}",
        )

    async def _drain_and_transition(
        self,
        *,
        action: str,
        generation: int,
        allow_preserved_outbox: bool = False,
    ) -> None:
        lifecycle = self.store.read()
        deadline_at = _iso_unix(lifecycle.get("transition_deadline_at"))
        if deadline_at is None:
            started_at = _iso_unix(lifecycle.get("transition_started_at"))
            deadline_at = (
                started_at + self._drain_timeout_seconds
                if started_at is not None
                else time.time() + self._drain_timeout_seconds
            )
        remaining = max(0.0, deadline_at - time.time())
        deadline = time.monotonic() + remaining
        try:
            while not self._closed:
                process = self._process
                live = bool(process is not None and process.returncode is None)
                blockers = self._outbox_blockers()
                if not live:
                    if not blockers or allow_preserved_outbox:
                        break
                else:
                    worker, fresh = self._current_worker_status()
                    fence_applied = bool(
                        fresh
                        and int(worker.get("observed_dispatch_generation") or -1)
                        == generation
                        and worker.get("dispatch_enabled") is False
                    )
                    if (
                        fence_applied
                        and int(worker.get("active") or 0) == 0
                        and blockers.get("public_reply_ambiguous")
                        and not allow_preserved_outbox
                    ):
                        raise AgentLifecycleConflict(
                            "unsafe_outbox",
                            blockers=blockers,
                        )
                    acknowledged = bool(
                        fence_applied
                        and worker.get("background_tasks_quiesced") is True
                        and int(worker.get("active") or 0) == 0
                    )
                    if acknowledged and (not blockers or allow_preserved_outbox):
                        break
                if time.monotonic() >= deadline:
                    worker = self.store.read_worker_status()
                    raise AgentLifecycleConflict(
                        "drain_timeout",
                        active_sessions=worker.get("active_sessions") or [],
                        blockers=blockers,
                    )
                await asyncio.sleep(0.2)

            async with self._lock:
                lifecycle = self.store.read()
                if int(lifecycle.get("dispatch_generation") or 0) != generation:
                    return
                if action == "stop":
                    # Keep ingress durable until the Worker process has truly
                    # exited.  If termination fails, desired_state remains
                    # running and no webhook is falsely acknowledged/dropped.
                    await self._terminate_worker_locked(force=False)
                    lifecycle.update(
                        {
                            "desired_state": "stopped",
                            "dispatch_enabled": False,
                            "stopped_at": _utc_iso(),
                            "transition_status": "completed",
                            "transition_completed_at": _utc_iso(),
                            "transition_audit_status": "pending",
                            "transition_blockers": (
                                self._outbox_blockers()
                                if allow_preserved_outbox
                                else {}
                            ),
                            "transition_allow_preserved_outbox": False,
                        }
                    )
                    self.store.write(lifecycle)
                    await self.trigger_queue.discard_backlog("agent_stopped")
                    self._migrate_old_epoch_states()
                    self._runtime_state = "stopped"
                else:
                    await self._terminate_worker_locked(force=False)
                    lifecycle = self.store.read()
                    lifecycle.update(
                        {
                            "dispatch_enabled": True,
                            "dispatch_generation": int(
                                lifecycle.get("dispatch_generation") or 0
                            )
                            + 1,
                            "started_at": _utc_iso(),
                            "transition_status": "completed",
                            "transition_completed_at": _utc_iso(),
                            "transition_audit_status": "pending",
                            "transition_allow_preserved_outbox": False,
                        }
                    )
                    self.store.write(lifecycle)
                    await self._spawn_worker_locked(new_epoch=False)
                self._deliver_transition_audit(lifecycle, status="completed")
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            self._runtime_state = "failed"
            reason = (
                error.reason
                if isinstance(error, AgentLifecycleConflict)
                else type(error).__name__
            )
            blockers = (
                error.blockers if isinstance(error, AgentLifecycleConflict) else {}
            )
            self._last_error = reason
            lifecycle = self.store.read()
            if not lifecycle.get("state_error"):
                lifecycle.update(
                    {
                        "transition_status": "blocked",
                        "transition_error": reason,
                        "transition_blockers": blockers,
                        "transition_completed_at": _utc_iso(),
                        "transition_audit_status": "pending",
                    }
                )
                self.store.write(lifecycle)
                self._deliver_transition_audit(
                    lifecycle,
                    status="blocked",
                    error=reason,
                    blockers=blockers,
                )
            logger.exception("agent_lifecycle_transition_failed", action=action)

    def _deliver_transition_audit(
        self,
        lifecycle: dict[str, Any],
        *,
        status: str,
        error: str = "",
        blockers: dict[str, Any] | None = None,
    ) -> None:
        if self._transition_callback is None:
            return
        try:
            self._transition_callback(
                {
                    "action": lifecycle.get("transition_action") or "",
                    "status": status,
                    "operation_id": lifecycle.get("transition_operation_id") or "",
                    "actor": lifecycle.get("transition_actor") or "",
                    "reason": lifecycle.get("transition_reason") or "",
                    "ip": lifecycle.get("transition_ip") or "",
                    "generation": lifecycle.get("generation") or 0,
                    "error": error,
                    "blockers": blockers or {},
                }
            )
        except Exception:  # noqa: BLE001 - audit callback must not break lifecycle.
            logger.exception("agent_lifecycle_transition_callback_failed")
            return
        current = self.store.read()
        if (
            not current.get("state_error")
            and current.get("transition_action") == lifecycle.get("transition_action")
            and current.get("transition_operation_id")
            == lifecycle.get("transition_operation_id")
            and current.get("transition_status") == status
        ):
            current["transition_audit_status"] = status
            self.store.write(current)

    def _redeliver_terminal_transition(self, lifecycle: dict[str, Any]) -> None:
        status = str(lifecycle.get("transition_status") or "")
        if status not in {"completed", "blocked"}:
            return
        if lifecycle.get("transition_audit_status") == status:
            return
        self._deliver_transition_audit(
            lifecycle,
            status=status,
            error=str(lifecycle.get("transition_error") or ""),
            blockers=dict(lifecycle.get("transition_blockers") or {}),
        )

    def _outbox_blockers(self) -> dict[str, int]:
        from zd_agent.services.saas_routes import _outbox_health

        values = _outbox_health(self.store.state_dir)
        safe_terminal_keys = {"manual_reconciliation_tickets"}
        return {
            key: int(value)
            for key, value in values.items()
            if key not in safe_terminal_keys and int(value) > 0
        }

    def _migrate_old_epoch_states(self) -> None:
        from zd_agent.trigger.state_store import TicketStateTracker

        tracker = TicketStateTracker(self.store.state_dir / "tickets.json")
        for ticket_id, record in tracker.all().items():
            if record.state not in {
                "processing",
                "in_silence_window",
                "awaiting_customer",
                "waiting_for_form",
                "waiting_for_auth",
                "waiting_for_issue",
            }:
                continue

            def _migrate(current: Any) -> Any:
                if current is None:
                    raise RuntimeError("ticket state disappeared during migration")
                if current.state == "processing":
                    current.state = "manual_reconciliation"
                    current.last_verdict = "operator_offline_boundary"
                else:
                    current.state = "out_of_scope"
                    current.last_verdict = "offline_wait_state_discarded"
                current.active_session_id = ""
                current.delivery_attempt_id = ""
                current.silence_window_end = None
                current.pending_customer_fields = ()
                current.pending_issue_question = ""
                current.pending_trigger_hint = ""
                return current

            tracker.update_if_revision(
                ticket_id,
                expected_revision=record.state_revision,
                expected_state=record.state,
                mutate=_migrate,
            )

    async def _spawn_worker_locked(self, *, new_epoch: bool) -> None:
        if self._closed:
            return
        if self._process is not None and self._process.returncode is None:
            return
        lifecycle = self.store.read()
        if lifecycle.get("state_error") or lifecycle["desired_state"] != "running":
            return
        env = os.environ.copy()
        env[ACCEPT_AFTER_ENV] = str(lifecycle.get("accept_after") or 0.0)
        env[NEW_EPOCH_ENV] = "1" if new_epoch else "0"
        env[CHILD_PROCESS_GROUP_REGISTRY_ENV] = str(self._child_groups.path)
        self._runtime_state = "starting"
        self._last_error = ""
        await asyncio.to_thread(self._child_groups.terminate_all)
        try:
            self._process = await self._spawn(
                *self._command,
                env=env,
                start_new_session=True,
            )
        except Exception as exc:
            self._runtime_state = "failed"
            self._last_error = type(exc).__name__
            logger.exception("agent_worker_spawn_failed")
            raise
        process = self._process
        self.store.write_worker_status(
            {
                "status": "starting",
                "pid": process.pid,
                "active": 0,
                "active_sessions": [],
                "heartbeat_unix": time.time(),
            }
        )
        self._monitor_task = asyncio.create_task(self._monitor_worker(process))
        logger.info("agent_worker_started", pid=process.pid)

    async def _monitor_worker(self, process: asyncio.subprocess.Process) -> None:
        returncode = await process.wait()
        await self._cleanup_worker_process_groups(process.pid)
        intentional = process.pid in self._intentional_pids
        self._intentional_pids.discard(process.pid)
        if self._process is not process:
            return
        self._process = None
        lifecycle = self.store.read()
        if self._closed or lifecycle["desired_state"] == "stopped" or intentional:
            if lifecycle["desired_state"] == "stopped":
                self._runtime_state = "stopped"
            return
        if not lifecycle.get("dispatch_enabled"):
            return
        self._runtime_state = "restarting"
        self._last_error = f"worker_exit_{returncode}"
        logger.error("agent_worker_exited", returncode=returncode)
        await asyncio.sleep(5)
        async with self._lock:
            lifecycle = self.store.read()
            if (
                not self._closed
                and lifecycle["desired_state"] == "running"
                and lifecycle.get("dispatch_enabled")
            ):
                await self._spawn_worker_locked(new_epoch=False)

    async def _terminate_worker_locked(self, *, force: bool) -> None:
        process = self._process
        if process is None:
            await self._cleanup_registered_tool_groups()
            self._process = None
            return
        if process.returncode is not None:
            await self._cleanup_worker_process_groups(process.pid)
            self._process = None
            return
        self._intentional_pids.add(process.pid)
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=self._stop_timeout_seconds)
        except TimeoutError:
            if not force:
                raise AgentLifecycleConflict("worker_shutdown_timeout")
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
        await self._cleanup_worker_process_groups(process.pid)
        if self._process is process:
            self._process = None
        logger.info("agent_worker_stopped", returncode=process.returncode)

    async def _cleanup_worker_process_groups(self, worker_pgid: int) -> None:
        try:
            os.killpg(worker_pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        terminated = await self._cleanup_registered_tool_groups()
        if terminated:
            logger.warning("orphaned_worker_tool_groups_terminated", count=terminated)

    async def _cleanup_registered_tool_groups(self) -> int:
        return await asyncio.to_thread(self._child_groups.terminate_all)


class AgentLifecycleConflict(RuntimeError):
    def __init__(
        self,
        reason: str,
        *,
        active_sessions: list[Any] | None = None,
        blockers: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.active_sessions = active_sessions or []
        self.blockers = blockers or {}


async def write_worker_heartbeat(
    *,
    store: AgentLifecycleStore,
    manager: Any,
    health: dict[str, Any],
    interval_seconds: float = 1.0,
) -> None:
    """Publish operator-safe Worker readiness and fence acknowledgement."""
    while True:
        try:
            overview = manager.get_all_sessions()
        except (AttributeError, TypeError):
            overview = {}
        if not isinstance(overview, dict):
            overview = {}
        active_sessions = _session_items(overview.get("active_sessions"))
        recent_completed = _session_items(overview.get("recent_completed"))
        store.write_worker_status(
            {
                "status": "running",
                "pid": os.getpid(),
                "heartbeat_unix": time.time(),
                "active": int(overview.get("active") or 0),
                "max_concurrent": int(overview.get("max_concurrent") or 0),
                "active_sessions": active_sessions,
                "recent_completed": recent_completed[:8],
                "observed_dispatch_generation": manager.dispatch_generation,
                "dispatch_enabled": manager.accepting_dispatches(),
                "background_tasks": dict(health.get("background_tasks") or {}),
                "background_tasks_quiesced": bool(
                    health.get("background_tasks_quiesced")
                ),
            }
        )
        await asyncio.sleep(interval_seconds)


def _session_items(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if is_dataclass(item):
            payload = asdict(item)
        elif isinstance(item, dict):
            payload = dict(item)
        else:
            continue
        status = payload.get("status")
        if hasattr(status, "value"):
            payload["status"] = status.value
        result.append(payload)
    return result

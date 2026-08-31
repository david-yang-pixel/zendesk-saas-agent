from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zd_agent.subagents.models import SubagentTask, SubagentTaskStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SubagentStore:
    """Durable task ledger; no model credentials are persisted."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS subagent_tasks (
                    id TEXT PRIMARY KEY,
                    root_session_id TEXT NOT NULL,
                    parent_session_id TEXT NOT NULL,
                    ticket_id INTEGER NOT NULL,
                    slot_id TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    config_digest TEXT NOT NULL,
                    model_snapshot_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    session_id TEXT NOT NULL DEFAULT '',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error_code TEXT NOT NULL DEFAULT '',
                    error_detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    UNIQUE(root_session_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS subagent_tasks_root_status
                    ON subagent_tasks(root_session_id, status);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    @staticmethod
    def _task(row: sqlite3.Row) -> SubagentTask:
        return SubagentTask(
            id=row["id"],
            root_session_id=row["root_session_id"],
            parent_session_id=row["parent_session_id"],
            ticket_id=row["ticket_id"],
            slot_id=row["slot_id"],
            objective=row["objective"],
            idempotency_key=row["idempotency_key"],
            config_digest=row["config_digest"],
            model_snapshot=json.loads(row["model_snapshot_json"]),
            status=SubagentTaskStatus(row["status"]),
            session_id=row["session_id"],
            result=json.loads(row["result_json"]),
            error_code=row["error_code"],
            error_detail=row["error_detail"],
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=(
                datetime.fromisoformat(row["started_at"])
                if row["started_at"]
                else None
            ),
            finished_at=(
                datetime.fromisoformat(row["finished_at"])
                if row["finished_at"]
                else None
            ),
        )

    def create(
        self,
        *,
        root_session_id: str,
        parent_session_id: str,
        ticket_id: int,
        slot_id: str,
        objective: str,
        idempotency_key: str,
        config_digest: str,
        model_snapshot: dict[str, Any] | None = None,
    ) -> SubagentTask:
        task_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO subagent_tasks(
                    id, root_session_id, parent_session_id, ticket_id, slot_id,
                    objective, idempotency_key, config_digest, model_snapshot_json,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    root_session_id,
                    parent_session_id,
                    ticket_id,
                    slot_id,
                    objective,
                    idempotency_key,
                    config_digest,
                    json.dumps(model_snapshot or {}, sort_keys=True),
                    SubagentTaskStatus.QUEUED.value,
                    _now().isoformat(),
                ),
            )
            row = connection.execute(
                """SELECT * FROM subagent_tasks
                   WHERE root_session_id = ? AND idempotency_key = ?""",
                (root_session_id, idempotency_key),
            ).fetchone()
        assert row is not None
        return self._task(row)

    def get(self, task_id: str) -> SubagentTask | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM subagent_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return self._task(row) if row is not None else None

    def get_by_key(self, root_session_id: str, key: str) -> SubagentTask | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM subagent_tasks
                   WHERE root_session_id = ? AND idempotency_key = ?""",
                (root_session_id, key),
            ).fetchone()
        return self._task(row) if row is not None else None

    def list_for_root(self, root_session_id: str) -> tuple[SubagentTask, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM subagent_tasks
                   WHERE root_session_id = ? ORDER BY created_at, id""",
                (root_session_id,),
            ).fetchall()
        return tuple(self._task(row) for row in rows)

    def list_recent(
        self,
        *,
        limit: int = 100,
        root_session_id: str = "",
    ) -> tuple[SubagentTask, ...]:
        params: list[Any] = []
        sql = "SELECT * FROM subagent_tasks"
        if root_session_id:
            sql += " WHERE root_session_id = ?"
            params.append(root_session_id)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return tuple(self._task(row) for row in rows)

    def _transition(
        self,
        task_id: str,
        *,
        status: SubagentTaskStatus,
        session_id: str | None = None,
        result: dict[str, Any] | None = None,
        error_code: str = "",
        error_detail: str = "",
        started: bool = False,
        finished: bool = False,
    ) -> SubagentTask:
        assignments = ["status = ?", "error_code = ?", "error_detail = ?"]
        params: list[Any] = [status.value, error_code, error_detail]
        if session_id is not None:
            assignments.append("session_id = ?")
            params.append(session_id)
        if result is not None:
            assignments.append("result_json = ?")
            params.append(json.dumps(result, ensure_ascii=False, sort_keys=True))
        timestamp = _now().isoformat()
        if started:
            assignments.append("started_at = ?")
            params.append(timestamp)
        if finished:
            assignments.append("finished_at = ?")
            params.append(timestamp)
        params.append(task_id)
        with self._connect() as connection:
            changed = connection.execute(
                f"UPDATE subagent_tasks SET {', '.join(assignments)} WHERE id = ?",
                params,
            ).rowcount
        if not changed:
            raise KeyError(task_id)
        task = self.get(task_id)
        assert task is not None
        return task

    def mark_running(self, task_id: str, *, session_id: str) -> SubagentTask:
        return self._transition(
            task_id,
            status=SubagentTaskStatus.RUNNING,
            session_id=session_id,
            started=True,
        )

    def complete(self, task_id: str, *, result: dict[str, Any]) -> SubagentTask:
        return self._transition(
            task_id,
            status=SubagentTaskStatus.COMPLETED,
            result=result,
            finished=True,
        )

    def fail(self, task_id: str, *, code: str, detail: str) -> SubagentTask:
        return self._transition(
            task_id,
            status=SubagentTaskStatus.FAILED,
            error_code=code,
            error_detail=detail,
            finished=True,
        )

    def timeout(self, task_id: str) -> SubagentTask:
        return self._transition(
            task_id,
            status=SubagentTaskStatus.TIMED_OUT,
            error_code="task_timeout",
            error_detail="subagent task exceeded its configured timeout",
            finished=True,
        )

    def cancel(self, task_id: str) -> SubagentTask:
        return self._transition(
            task_id,
            status=SubagentTaskStatus.CANCELLED,
            error_code="cancelled",
            error_detail="subagent task was cancelled by the root session",
            finished=True,
        )

    def recover_interrupted(self) -> int:
        with self._connect() as connection:
            changed = connection.execute(
                """UPDATE subagent_tasks
                   SET status = ?, error_code = ?, error_detail = ?, finished_at = ?
                   WHERE status IN (?, ?)""",
                (
                    SubagentTaskStatus.FAILED.value,
                    "process_interrupted",
                    "subagent process stopped before a durable terminal result",
                    _now().isoformat(),
                    SubagentTaskStatus.RUNNING.value,
                    SubagentTaskStatus.QUEUED.value,
                ),
            ).rowcount
        return changed

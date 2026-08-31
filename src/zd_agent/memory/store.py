from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zd_agent.memory.models import (
    MemoryEntry,
    MemoryFeedback,
    MemoryKind,
    MemorySearchHit,
    MemoryStatus,
    WorkingMemoryItem,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryStore:
    """SQLite/WAL memory store with review-gated long-term recall."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_entries (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    source_ticket_id INTEGER,
                    evidence_json TEXT NOT NULL,
                    proposed_by TEXT NOT NULL,
                    reviewed_by TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS memory_entries_status_kind
                    ON memory_entries(status, kind);
                CREATE TABLE IF NOT EXISTS working_memory (
                    root_session_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    writer_session_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(root_session_id, key)
                );
                CREATE TABLE IF NOT EXISTS memory_feedback (
                    id TEXT PRIMARY KEY,
                    entry_id TEXT NOT NULL,
                    useful INTEGER NOT NULL,
                    note TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(entry_id) REFERENCES memory_entries(id)
                );
                """
            )

    @staticmethod
    def _entry(row: sqlite3.Row) -> MemoryEntry:
        return MemoryEntry(
            id=row["id"],
            kind=MemoryKind(row["kind"]),
            status=MemoryStatus(row["status"]),
            title=row["title"],
            content=row["content"],
            tags=tuple(json.loads(row["tags_json"])),
            source_ticket_id=row["source_ticket_id"],
            evidence_references=tuple(json.loads(row["evidence_json"])),
            proposed_by=row["proposed_by"],
            reviewed_by=row["reviewed_by"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def propose(
        self,
        *,
        kind: MemoryKind,
        title: str,
        content: str,
        proposed_by: str,
        tags: tuple[str, ...] = (),
        source_ticket_id: int | None = None,
        evidence_references: tuple[str, ...] = (),
    ) -> MemoryEntry:
        entry_id = uuid.uuid4().hex
        timestamp = _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO memory_entries(
                    id, kind, status, title, content, tags_json,
                    source_ticket_id, evidence_json, proposed_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry_id,
                    kind.value,
                    MemoryStatus.PROPOSED.value,
                    title.strip(),
                    content.strip(),
                    json.dumps(tuple(dict.fromkeys(tags)), ensure_ascii=False),
                    source_ticket_id,
                    json.dumps(
                        tuple(dict.fromkeys(evidence_references)), ensure_ascii=False
                    ),
                    proposed_by,
                    timestamp.isoformat(),
                    timestamp.isoformat(),
                ),
            )
        entry = self.get(entry_id, include_inactive=True)
        assert entry is not None
        return entry

    def _review(
        self, entry_id: str, *, status: MemoryStatus, reviewer: str
    ) -> MemoryEntry:
        with self._connect() as connection:
            changed = connection.execute(
                """UPDATE memory_entries
                   SET status = ?, reviewed_by = ?, updated_at = ?
                   WHERE id = ?""",
                (status.value, reviewer, _now().isoformat(), entry_id),
            ).rowcount
        if not changed:
            raise KeyError(entry_id)
        entry = self.get(entry_id, include_inactive=True)
        assert entry is not None
        return entry

    def approve(self, entry_id: str, *, reviewer: str) -> MemoryEntry:
        return self._review(entry_id, status=MemoryStatus.ACTIVE, reviewer=reviewer)

    def reject(self, entry_id: str, *, reviewer: str) -> MemoryEntry:
        return self._review(entry_id, status=MemoryStatus.REJECTED, reviewer=reviewer)

    def get(
        self, entry_id: str, *, include_inactive: bool = False
    ) -> MemoryEntry | None:
        sql = "SELECT * FROM memory_entries WHERE id = ?"
        params: tuple[Any, ...] = (entry_id,)
        if not include_inactive:
            sql += " AND status = ?"
            params += (MemoryStatus.ACTIVE.value,)
        with self._connect() as connection:
            row = connection.execute(sql, params).fetchone()
        return self._entry(row) if row is not None else None

    def search(
        self,
        query: str,
        *,
        kinds: tuple[MemoryKind, ...] = (),
        limit: int = 10,
    ) -> tuple[MemorySearchHit, ...]:
        clauses = ["status = ?", "lower(title || ' ' || content || ' ' || tags_json) LIKE ?"]
        params: list[Any] = [MemoryStatus.ACTIVE.value, f"%{query.strip().lower()}%"]
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            clauses.append(f"kind IN ({placeholders})")
            params.extend(kind.value for kind in kinds)
        params.append(max(1, min(int(limit), 50)))
        sql = (
            "SELECT * FROM memory_entries WHERE "
            + " AND ".join(clauses)
            + " ORDER BY updated_at DESC LIMIT ?"
        )
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return tuple(MemorySearchHit(entry=self._entry(row)) for row in rows)

    def list_entries(
        self,
        *,
        status: MemoryStatus | None = None,
        limit: int = 100,
    ) -> tuple[MemoryEntry, ...]:
        sql = "SELECT * FROM memory_entries"
        params: list[Any] = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(status.value)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return tuple(self._entry(row) for row in rows)

    def put_working(
        self,
        *,
        root_session_id: str,
        key: str,
        value: Any,
        writer_session_id: str,
    ) -> WorkingMemoryItem:
        timestamp = _now()
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO working_memory(
                    root_session_id, key, value_json, writer_session_id, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(root_session_id, key) DO UPDATE SET
                    value_json = excluded.value_json,
                    writer_session_id = excluded.writer_session_id,
                    updated_at = excluded.updated_at""",
                (root_session_id, key, encoded, writer_session_id, timestamp.isoformat()),
            )
        item = self.get_working(root_session_id, key)
        assert item is not None
        return item

    def get_working(self, root_session_id: str, key: str) -> WorkingMemoryItem | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM working_memory WHERE root_session_id = ? AND key = ?",
                (root_session_id, key),
            ).fetchone()
        if row is None:
            return None
        return WorkingMemoryItem(
            root_session_id=row["root_session_id"],
            key=row["key"],
            value=json.loads(row["value_json"]),
            writer_session_id=row["writer_session_id"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def add_feedback(
        self,
        entry_id: str,
        *,
        useful: bool,
        note: str,
        actor: str,
    ) -> MemoryFeedback:
        feedback = MemoryFeedback(
            id=uuid.uuid4().hex,
            entry_id=entry_id,
            useful=useful,
            note=note,
            actor=actor,
            created_at=_now(),
        )
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO memory_feedback(
                    id, entry_id, useful, note, actor, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    feedback.id,
                    feedback.entry_id,
                    int(feedback.useful),
                    feedback.note,
                    feedback.actor,
                    feedback.created_at.isoformat(),
                ),
            )
        return feedback

    def list_feedback(self, entry_id: str) -> tuple[MemoryFeedback, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM memory_feedback WHERE entry_id = ? ORDER BY created_at",
                (entry_id,),
            ).fetchall()
        return tuple(
            MemoryFeedback(
                id=row["id"],
                entry_id=row["entry_id"],
                useful=bool(row["useful"]),
                note=row["note"],
                actor=row["actor"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        )

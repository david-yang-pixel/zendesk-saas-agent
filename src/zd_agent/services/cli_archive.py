"""Archive inspection helpers for CLI commands."""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class ArchiveStats:
    archive_exists: bool
    cutoff: datetime
    total: int
    completed: int
    failed: int
    total_tokens: int
    total_rounds: int
    reply_sent_count: int


@dataclass(frozen=True)
class TicketLogResult:
    kind: str
    base_dir: Path | None
    entries: list[dict[str, str]]


def collect_sandbox_status(
    sandbox_base: Path,
    *,
    archive_base: Path,
) -> list[dict[str, object]]:
    """Collect sandbox status rows for the CLI status command."""
    if not sandbox_base.exists():
        return []

    rows: list[dict[str, object]] = []
    for sandbox_dir in sorted(
        (p for p in sandbox_base.iterdir() if p.is_dir()),
        key=lambda p: p.name,
    ):
        ticket_id = sandbox_dir.name
        file_count = sum(1 for p in sandbox_dir.rglob("*") if p.is_file())

        attachments_dir = sandbox_dir / "attachments"
        attachment_count = (
            sum(1 for p in attachments_dir.rglob("*") if p.is_file())
            if attachments_dir.exists()
            else 0
        )

        worktree_dir = sandbox_dir / "worktree"
        has_worktree = worktree_dir.exists() and any(worktree_dir.iterdir())
        has_archive = (archive_base / ticket_id).exists()

        rows.append(
            {
                "ticket_id": ticket_id,
                "file_count": file_count,
                "attachment_count": attachment_count,
                "has_worktree": has_worktree,
                "has_archive": has_archive,
            }
        )

    return rows


def find_sandbox_cleanup_targets(
    sandbox_base: Path,
    *,
    ticket_id: int | None = None,
) -> list[Path]:
    """Return sandbox directories selected for cleanup."""
    if not sandbox_base.exists():
        return []

    if ticket_id is not None:
        target = sandbox_base / str(ticket_id)
        return [target] if target.exists() else []

    return sorted(
        (p for p in sandbox_base.iterdir() if p.is_dir()),
        key=lambda p: p.name,
    )


def remove_sandbox_targets(targets: list[Path]) -> list[Path]:
    """Remove sandbox directories, ignoring per-directory deletion errors."""
    removed: list[Path] = []
    for target in targets:
        shutil.rmtree(target, ignore_errors=True)
        removed.append(target)
    return removed


def collect_ticket_log(
    *,
    ticket_id: int,
    archive_base: Path,
    sandbox_base: Path,
    max_content_chars: int = 5000,
) -> TicketLogResult:
    """Collect archived or sandbox log data for one ticket."""
    archive_dir = archive_base / str(ticket_id)
    if archive_dir.exists():
        entries: list[dict[str, str]] = []
        for file_path in sorted(archive_dir.iterdir()):
            if not file_path.is_file():
                continue
            content = file_path.read_text(encoding="utf-8", errors="replace")
            if len(content) > max_content_chars:
                content = content[:max_content_chars] + "\n\n... (截断)"
            entries.append({"name": file_path.name, "content": content})
        return TicketLogResult(kind="archive", base_dir=archive_dir, entries=entries)

    sandbox_dir = sandbox_base / str(ticket_id)
    if sandbox_dir.exists():
        entries = []
        for file_path in sorted(sandbox_dir.rglob("*")):
            if file_path.is_file():
                entries.append({"path": str(file_path.relative_to(sandbox_dir))})
        return TicketLogResult(kind="sandbox", base_dir=sandbox_dir, entries=entries)

    return TicketLogResult(kind="missing", base_dir=None, entries=[])


def parse_time_range(last: str) -> timedelta:
    """Parse CLI time range strings like 7d, 30d, or 24h."""
    match = re.match(r"^(\d+)([dhm])$", last)
    if not match:
        raise ValueError("时间格式错误，支持: 7d, 30d, 24h")
    amount, unit = int(match.group(1)), match.group(2)
    return {
        "d": timedelta(days=amount),
        "h": timedelta(hours=amount),
        "m": timedelta(minutes=amount),
    }[unit]


def collect_archive_stats(
    archive_base: Path,
    *,
    last: str,
    now: datetime | None = None,
) -> ArchiveStats:
    """Collect archived ticket processing stats for the CLI."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - parse_time_range(last)
    empty = ArchiveStats(
        archive_exists=archive_base.exists(),
        cutoff=cutoff,
        total=0,
        completed=0,
        failed=0,
        total_tokens=0,
        total_rounds=0,
        reply_sent_count=0,
    )
    if not archive_base.exists():
        return empty

    total = 0
    completed = 0
    failed = 0
    total_tokens = 0
    total_rounds = 0
    reply_sent_count = 0

    for ticket_dir in archive_base.iterdir():
        if not ticket_dir.is_dir():
            continue

        meta_file = ticket_dir / "meta.json"
        if not meta_file.exists():
            dir_mtime = datetime.fromtimestamp(ticket_dir.stat().st_mtime, tz=timezone.utc)
            if dir_mtime < cutoff:
                continue
            total += 1
            continue

        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            total += 1
            continue

        created = meta.get("created_at", "")
        if created:
            try:
                ts = datetime.fromisoformat(created)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < cutoff:
                    continue
            except ValueError:
                pass

        total += 1
        status_val = meta.get("status", "")
        if status_val == "completed":
            completed += 1
        elif status_val in ("failed", "timeout"):
            failed += 1

        total_tokens += meta.get("total_tokens", 0)
        total_rounds += meta.get("rounds", 0)
        if meta.get("reply_sent"):
            reply_sent_count += 1

    return ArchiveStats(
        archive_exists=True,
        cutoff=cutoff,
        total=total,
        completed=completed,
        failed=failed,
        total_tokens=total_tokens,
        total_rounds=total_rounds,
        reply_sent_count=reply_sent_count,
    )

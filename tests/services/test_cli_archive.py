import json
from datetime import datetime, timedelta, timezone

import pytest


def test_collect_archive_stats_counts_recent_meta(tmp_path):
    from zd_agent.services.cli_archive import collect_archive_stats

    now = datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)
    recent = tmp_path / "100"
    recent.mkdir()
    (recent / "meta.json").write_text(
        json.dumps(
            {
                "created_at": (now - timedelta(days=1)).isoformat(),
                "status": "completed",
                "total_tokens": 120,
                "rounds": 3,
                "reply_sent": True,
            }
        ),
        encoding="utf-8",
    )
    old = tmp_path / "101"
    old.mkdir()
    (old / "meta.json").write_text(
        json.dumps(
            {
                "created_at": (now - timedelta(days=10)).isoformat(),
                "status": "failed",
                "total_tokens": 999,
                "rounds": 9,
            }
        ),
        encoding="utf-8",
    )

    stats = collect_archive_stats(tmp_path, last="7d", now=now)

    assert stats.total == 1
    assert stats.completed == 1
    assert stats.failed == 0
    assert stats.total_tokens == 120
    assert stats.total_rounds == 3
    assert stats.reply_sent_count == 1
    assert stats.cutoff == now - timedelta(days=7)


def test_collect_archive_stats_counts_each_session_archive_without_legacy_duplication(
    tmp_path,
):
    from zd_agent.services.cli_archive import collect_archive_stats

    now = datetime(2026, 7, 17, 8, 0, tzinfo=timezone.utc)
    ticket_dir = tmp_path / "3485"
    first = ticket_dir / "runs" / "first-session"
    second = ticket_dir / "runs" / "second-session"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "meta.json").write_text(
        json.dumps(
            {
                "created_at": (now - timedelta(minutes=10)).isoformat(),
                "status": "deferred",
                "total_tokens": 219_938,
                "rounds": 6,
                "reply_sent": False,
            }
        ),
        encoding="utf-8",
    )
    (second / "meta.json").write_text(
        json.dumps(
            {
                "created_at": (now - timedelta(minutes=5)).isoformat(),
                "status": "completed",
                "total_tokens": 195_457,
                "rounds": 7,
                "reply_sent": True,
            }
        ),
        encoding="utf-8",
    )
    (ticket_dir / "meta.json").write_text(
        json.dumps(
            {
                "created_at": (now - timedelta(minutes=10)).isoformat(),
                "status": "deferred",
                "total_tokens": 219_938,
                "rounds": 6,
            }
        ),
        encoding="utf-8",
    )

    stats = collect_archive_stats(tmp_path, last="1d", now=now)

    assert stats.total == 2
    assert stats.completed == 1
    assert stats.failed == 0
    assert stats.total_tokens == 415_395
    assert stats.total_rounds == 13
    assert stats.reply_sent_count == 1


def test_collect_archive_stats_keeps_distinct_legacy_run_during_layout_migration(
    tmp_path,
):
    from zd_agent.services.cli_archive import collect_archive_stats

    now = datetime(2026, 7, 17, 8, 0, tzinfo=timezone.utc)
    ticket_dir = tmp_path / "3485"
    current = ticket_dir / "runs" / "current-session"
    current.mkdir(parents=True)
    (ticket_dir / "meta.json").write_text(
        json.dumps(
            {
                "session_id": "legacy-session",
                "created_at": (now - timedelta(minutes=20)).isoformat(),
                "status": "failed",
                "total_tokens": 50,
                "rounds": 2,
                "reply_sent": False,
            }
        ),
        encoding="utf-8",
    )
    (current / "meta.json").write_text(
        json.dumps(
            {
                "session_id": "current-session",
                "created_at": (now - timedelta(minutes=5)).isoformat(),
                "status": "completed",
                "total_tokens": 100,
                "rounds": 3,
                "reply_sent": True,
            }
        ),
        encoding="utf-8",
    )

    stats = collect_archive_stats(tmp_path, last="1d", now=now)

    assert stats.total == 2
    assert stats.completed == 1
    assert stats.failed == 1
    assert stats.total_tokens == 150
    assert stats.total_rounds == 5


def test_collect_archive_stats_counts_bad_meta_as_total(tmp_path):
    from zd_agent.services.cli_archive import collect_archive_stats

    now = datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)
    ticket_dir = tmp_path / "bad"
    ticket_dir.mkdir()
    (ticket_dir / "meta.json").write_text("{bad json", encoding="utf-8")

    stats = collect_archive_stats(tmp_path, last="7d", now=now)

    assert stats.total == 1
    assert stats.completed == 0
    assert stats.failed == 0


def test_collect_archive_stats_rejects_bad_range(tmp_path):
    from zd_agent.services.cli_archive import collect_archive_stats

    with pytest.raises(ValueError, match="时间格式错误"):
        collect_archive_stats(tmp_path, last="week")


def test_collect_archive_stats_missing_archive_returns_empty(tmp_path):
    from zd_agent.services.cli_archive import collect_archive_stats

    missing = tmp_path / "missing"
    stats = collect_archive_stats(missing, last="7d")

    assert stats.total == 0
    assert stats.archive_exists is False


def test_collect_sandbox_status_rows(tmp_path):
    from zd_agent.services.cli_archive import collect_sandbox_status

    sandbox_base = tmp_path / "sandbox"
    archive_base = tmp_path / "archive"
    ticket_dir = sandbox_base / "100"
    attachments_dir = ticket_dir / "attachments"
    worktree_dir = ticket_dir / "worktree"
    attachments_dir.mkdir(parents=True)
    worktree_dir.mkdir()
    (ticket_dir / "note.txt").write_text("x", encoding="utf-8")
    (attachments_dir / "a.log").write_text("x", encoding="utf-8")
    (worktree_dir / "file.py").write_text("x", encoding="utf-8")
    (archive_base / "100").mkdir(parents=True)

    rows = collect_sandbox_status(sandbox_base, archive_base=archive_base)

    assert rows == [
        {
            "ticket_id": "100",
            "file_count": 3,
            "attachment_count": 1,
            "has_worktree": True,
            "has_archive": True,
        }
    ]


def test_collect_sandbox_status_missing_or_empty_returns_empty(tmp_path):
    from zd_agent.services.cli_archive import collect_sandbox_status

    assert (
        collect_sandbox_status(tmp_path / "missing", archive_base=tmp_path / "archive")
        == []
    )

    empty = tmp_path / "empty"
    empty.mkdir()
    assert collect_sandbox_status(empty, archive_base=tmp_path / "archive") == []


def test_find_sandbox_cleanup_targets_for_ticket(tmp_path):
    from zd_agent.services.cli_archive import find_sandbox_cleanup_targets

    sandbox_base = tmp_path / "sandbox"
    target = sandbox_base / "123"
    target.mkdir(parents=True)

    assert find_sandbox_cleanup_targets(sandbox_base, ticket_id=123) == [target]
    assert find_sandbox_cleanup_targets(sandbox_base, ticket_id=999) == []


def test_find_sandbox_cleanup_targets_all_dirs_sorted(tmp_path):
    from zd_agent.services.cli_archive import find_sandbox_cleanup_targets

    sandbox_base = tmp_path / "sandbox"
    (sandbox_base / "200").mkdir(parents=True)
    (sandbox_base / "100").mkdir()
    (sandbox_base / "file.txt").write_text("x", encoding="utf-8")

    assert [p.name for p in find_sandbox_cleanup_targets(sandbox_base)] == [
        "100",
        "200",
    ]
    assert find_sandbox_cleanup_targets(tmp_path / "missing") == []


def test_remove_sandbox_targets(tmp_path):
    from zd_agent.services.cli_archive import remove_sandbox_targets

    target_a = tmp_path / "a"
    target_b = tmp_path / "b"
    target_a.mkdir()
    target_b.mkdir()

    removed = remove_sandbox_targets([target_a, target_b])

    assert removed == [target_a, target_b]
    assert not target_a.exists()
    assert not target_b.exists()


def test_collect_ticket_log_prefers_archive_and_truncates(tmp_path):
    from zd_agent.services.cli_archive import collect_ticket_log

    archive_base = tmp_path / "archive"
    ticket_dir = archive_base / "123"
    ticket_dir.mkdir(parents=True)
    (ticket_dir / "a.txt").write_text("short", encoding="utf-8")
    (ticket_dir / "b.txt").write_text("x" * 5001, encoding="utf-8")

    result = collect_ticket_log(
        ticket_id=123,
        archive_base=archive_base,
        sandbox_base=tmp_path / "sandbox",
        max_content_chars=5000,
    )

    assert result.kind == "archive"
    assert result.base_dir == ticket_dir
    assert [entry["name"] for entry in result.entries] == ["a.txt", "b.txt"]
    assert result.entries[0]["content"] == "short"
    assert result.entries[1]["content"].endswith("... (截断)")


def test_collect_ticket_log_includes_each_session_archive(tmp_path):
    from zd_agent.services.cli_archive import collect_ticket_log

    archive_base = tmp_path / "archive"
    ticket_dir = archive_base / "3485"
    first = ticket_dir / "runs" / "first-session"
    second = ticket_dir / "runs" / "second-session"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "meta.json").write_text('{"status":"deferred"}', encoding="utf-8")
    (second / "meta.json").write_text('{"status":"completed"}', encoding="utf-8")
    (second / "analysis.md").write_text("latest analysis", encoding="utf-8")

    result = collect_ticket_log(
        ticket_id=3485,
        archive_base=archive_base,
        sandbox_base=tmp_path / "sandbox",
    )

    assert result.kind == "archive"
    assert [entry["name"] for entry in result.entries] == [
        "runs/first-session/meta.json",
        "runs/second-session/analysis.md",
        "runs/second-session/meta.json",
    ]


def test_collect_ticket_log_falls_back_to_sandbox_file_list(tmp_path):
    from zd_agent.services.cli_archive import collect_ticket_log

    sandbox_base = tmp_path / "sandbox"
    ticket_dir = sandbox_base / "123"
    nested = ticket_dir / "nested"
    nested.mkdir(parents=True)
    (ticket_dir / "a.txt").write_text("short", encoding="utf-8")
    (nested / "b.txt").write_text("short", encoding="utf-8")

    result = collect_ticket_log(
        ticket_id=123,
        archive_base=tmp_path / "archive",
        sandbox_base=sandbox_base,
    )

    assert result.kind == "sandbox"
    assert result.base_dir == ticket_dir
    assert result.entries == [
        {"path": "a.txt"},
        {"path": "nested/b.txt"},
    ]


def test_collect_ticket_log_returns_missing(tmp_path):
    from zd_agent.services.cli_archive import collect_ticket_log

    result = collect_ticket_log(
        ticket_id=123,
        archive_base=tmp_path / "archive",
        sandbox_base=tmp_path / "sandbox",
    )

    assert result.kind == "missing"
    assert result.base_dir is None
    assert result.entries == []

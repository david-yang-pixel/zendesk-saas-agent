from __future__ import annotations

import json
from types import SimpleNamespace

from zd_agent.runtime.runner_archive import ensure_archive


def test_auto_archive_writes_one_meta_per_session(tmp_path, monkeypatch) -> None:
    cfg = SimpleNamespace(
        knowledge=SimpleNamespace(archive_dir=str(tmp_path / "tickets")),
    )
    monkeypatch.setattr("zd_agent.runtime.runner_archive.get_config", lambda: cfg)

    first = SimpleNamespace(
        ticket_id=3485,
        session_id="first-session",
        analysis="first analysis",
        reply_draft="first reply",
    )
    second = SimpleNamespace(
        ticket_id=3485,
        session_id="second-session",
        analysis="second analysis",
        reply_draft="second reply",
    )
    monkeypatch.setattr(
        "zd_agent.runtime.runner_archive.build_archive_meta",
        lambda *, session, archived_at, elapsed_seconds: {
            "ticket_id": session.ticket_id,
            "session_id": session.session_id,
            "created_at": archived_at,
            "elapsed_seconds": elapsed_seconds,
        },
    )
    guardrails = SimpleNamespace(elapsed_seconds=12.5)

    ensure_archive(first, guardrails)
    ensure_archive(second, guardrails)

    first_meta = tmp_path / "tickets" / "3485" / "runs" / "first-session" / "meta.json"
    second_meta = tmp_path / "tickets" / "3485" / "runs" / "second-session" / "meta.json"
    assert json.loads(first_meta.read_text(encoding="utf-8"))["session_id"] == "first-session"
    assert json.loads(second_meta.read_text(encoding="utf-8"))["session_id"] == "second-session"
    assert (first_meta.parent / "analysis.md").read_text(encoding="utf-8") == "first analysis"
    assert (second_meta.parent / "analysis.md").read_text(encoding="utf-8") == "second analysis"


def test_auto_archive_hashes_dot_segment_session_id(tmp_path, monkeypatch) -> None:
    cfg = SimpleNamespace(
        knowledge=SimpleNamespace(archive_dir=str(tmp_path / "tickets")),
    )
    monkeypatch.setattr("zd_agent.runtime.runner_archive.get_config", lambda: cfg)
    monkeypatch.setattr(
        "zd_agent.runtime.runner_archive.build_archive_meta",
        lambda *, session, archived_at, elapsed_seconds: {
            "ticket_id": session.ticket_id,
            "session_id": session.session_id,
            "created_at": archived_at,
        },
    )
    session = SimpleNamespace(
        ticket_id=3485,
        session_id="..",
        analysis="",
        reply_draft="",
    )

    ensure_archive(session, SimpleNamespace(elapsed_seconds=1.0))

    assert not (tmp_path / "tickets" / "3485" / "meta.json").exists()
    meta_files = list((tmp_path / "tickets" / "3485" / "runs").glob("*/meta.json"))
    assert len(meta_files) == 1
    assert meta_files[0].parent.name.startswith("session-")

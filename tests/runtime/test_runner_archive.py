from __future__ import annotations

import json
from types import SimpleNamespace

from zd_agent.runtime.archive_meta import build_archive_meta
from zd_agent.runtime.runner_archive import ensure_archive
from zd_agent.session.agent_session import AgentSession, SessionStatus


def test_auto_archive_writes_one_meta_per_session(tmp_path, monkeypatch) -> None:
    cfg = SimpleNamespace(
        archive=SimpleNamespace(archive_dir=str(tmp_path / "tickets")),
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
    ensure_archive(first, elapsed_seconds=12.5)
    ensure_archive(second, elapsed_seconds=12.5)

    first_meta = tmp_path / "tickets" / "3485" / "runs" / "first-session" / "meta.json"
    second_meta = (
        tmp_path / "tickets" / "3485" / "runs" / "second-session" / "meta.json"
    )
    assert (
        json.loads(first_meta.read_text(encoding="utf-8"))["session_id"]
        == "first-session"
    )
    assert (
        json.loads(second_meta.read_text(encoding="utf-8"))["session_id"]
        == "second-session"
    )
    assert (first_meta.parent / "analysis.md").read_text(
        encoding="utf-8"
    ) == "first analysis"
    assert (second_meta.parent / "analysis.md").read_text(
        encoding="utf-8"
    ) == "second analysis"


def test_auto_archive_hashes_dot_segment_session_id(tmp_path, monkeypatch) -> None:
    cfg = SimpleNamespace(
        archive=SimpleNamespace(archive_dir=str(tmp_path / "tickets")),
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

    ensure_archive(session, elapsed_seconds=1.0)

    assert not (tmp_path / "tickets" / "3485" / "meta.json").exists()
    meta_files = list((tmp_path / "tickets" / "3485" / "runs").glob("*/meta.json"))
    assert len(meta_files) == 1
    assert meta_files[0].parent.name.startswith("session-")


def test_archive_meta_retains_typed_casework_and_exact_evidence_calls() -> None:
    session = AgentSession(ticket_id=3798, session_id="evidence-session")
    session.root_session_id = "root-session"
    session.parent_session_id = "root-session"
    session.session_kind = "subagent"
    session.subagent_task_id = "task-1"
    session.subagent_slot_id = "reader"
    session.runtime_config_digest = "digest-1"
    session.status = SessionStatus.COMPLETED
    session.casework_disposition = "engineering_escalation"
    session.casework_reply_kind = "solution"
    session.casework_ownership_action = "engineering"
    session.casework_reason_code = "confirmed_platform_defect"
    session.casework_delivery_state = "confirmed"
    session.casework_ownership_state = "confirmed"
    session.casework_effect_reference = "casework-effect:3798"
    session.casework_attempt = 2
    session.log_tool_call(
        "inspector_exec",
        {"section": "billing", "email": "user@example.com"},
        "tenant=tenant-1 plan=team",
        success=True,
        error=None,
        call_id="billing-1",
        metadata={"source": "billing"},
    )

    meta = build_archive_meta(
        session=session,
        archived_at="2026-08-06T00:00:00+00:00",
    )

    assert meta["casework_phase"] == "complete"
    assert meta["orchestration"] == {
        "root_session_id": "root-session",
        "parent_session_id": "root-session",
        "session_kind": "subagent",
        "subagent_task_id": "task-1",
        "subagent_slot_id": "reader",
        "runtime_config_digest": "digest-1",
    }
    assert meta["casework"] == {
        "disposition": "engineering_escalation",
        "reply_kind": "solution",
        "ownership_action": "engineering",
        "reason_code": "confirmed_platform_defect",
        "delivery_state": "confirmed",
        "ownership_state": "confirmed",
        "effect_reference": "casework-effect:3798",
        "attempt": 2,
    }
    assert meta["tool_calls"][0]["call_id"] == "billing-1"
    assert meta["tool_calls"][0]["tool"] == "inspector_exec"
    assert meta["tool_calls"][0]["metadata"] == {"source": "billing"}


def test_archive_meta_retains_context_compaction_metrics() -> None:
    session = AgentSession(ticket_id=4100, session_id="compaction-session")
    session.log_context_compaction(
        trigger="compact",
        tokens_before=200_000,
        tokens_after=80_000,
        checkpoint={"ticket_id": 4100, "generation": 2, "body": {}},
    )

    meta = build_archive_meta(
        session=session,
        archived_at="2026-08-26T00:00:00+00:00",
    )

    assert meta["active_context_tokens"] == 80_000
    assert meta["context_compaction_count"] == 1
    assert meta["context_compactions"][0]["reclaimed_tokens"] == 120_000


def test_auto_archive_writes_checkpoints_and_compaction_events(
    tmp_path,
    monkeypatch,
) -> None:
    cfg = SimpleNamespace(
        archive=SimpleNamespace(archive_dir=str(tmp_path / "tickets")),
    )
    monkeypatch.setattr("zd_agent.runtime.runner_archive.get_config", lambda: cfg)
    session = AgentSession(ticket_id=4100, session_id="compaction-session")
    session.log_context_compaction(
        trigger="compact",
        tokens_before=200_000,
        tokens_after=80_000,
        checkpoint={"ticket_id": 4100, "generation": 2, "body": {}},
    )

    ensure_archive(session, elapsed_seconds=4.2)

    run_dir = tmp_path / "tickets" / "4100" / "runs" / "compaction-session"
    checkpoint = json.loads(
        (run_dir / "checkpoints" / "001.json").read_text(encoding="utf-8")
    )
    events = [
        json.loads(line)
        for line in (run_dir / "compaction-events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert checkpoint["generation"] == 2
    assert events[0]["tokens_before"] == 200_000


def test_auto_archive_failure_never_breaks_agent_completion(
    tmp_path,
    monkeypatch,
) -> None:
    not_a_directory = tmp_path / "not-a-directory"
    not_a_directory.write_text("occupied", encoding="utf-8")
    cfg = SimpleNamespace(
        knowledge=SimpleNamespace(archive_dir=str(not_a_directory)),
    )
    monkeypatch.setattr("zd_agent.runtime.runner_archive.get_config", lambda: cfg)
    session = SimpleNamespace(
        ticket_id=3485,
        session_id="archive-failure",
        analysis="analysis",
        reply_draft="reply",
    )

    ensure_archive(session, elapsed_seconds=1.0)

    assert not (not_a_directory / "3485").exists()

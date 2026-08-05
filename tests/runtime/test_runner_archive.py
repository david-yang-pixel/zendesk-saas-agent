from __future__ import annotations

import json
from types import SimpleNamespace

from zd_agent.runtime.archive_meta import build_archive_meta
from zd_agent.runtime.runner_archive import ensure_archive
from zd_agent.session.agent_session import AgentSession, TicketContext


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


def test_archive_meta_retains_investigation_and_exact_evidence_calls() -> None:
    session = AgentSession(ticket_id=3798, session_id="evidence-session")
    session.context = TicketContext(ticket_id=3798)
    session.ticket_type = "billing"
    session.investigation_policy_enabled = True
    session.investigation_route = "high_risk"
    session.investigation_plan = {
        "route": "high_risk",
        "customer_question": "Which workspace and plan apply?",
        "object_identifiers": ["ticket=3798", "email=user@example.com"],
        "time_window": "current",
        "execution_chain": ["form email", "workspace", "billing tenant"],
        "evidence_lanes": ["identity", "billing"],
        "independent_lanes": [],
    }
    session.evidence_ledger = {
        "route": "high_risk",
        "root_cause_status": "not_applicable",
        "claims": [{"claim": "The billing tenant is on Team."}],
    }
    session.log_tool_call(
        "run_subagents_parallel",
        {"tasks": [{"task": "resolve tenant"}]},
        "resolved",
        success=True,
        error=None,
        call_id="parent-1",
        metadata={
            "evidence_tool_calls": [
                {
                    "call_id": "child-1:billing-1",
                    "tool": "inspector_exec",
                    "args": {"section": "billing", "email": "user@example.com"},
                    "success": True,
                    "error": None,
                    "result_preview": "tenant=tenant-1 plan=team",
                }
            ]
        },
    )

    meta = build_archive_meta(
        session=session,
        archived_at="2026-08-06T00:00:00+00:00",
    )

    assert meta["investigation_phase"] == "investigate"
    assert meta["investigation_route"] == "high_risk"
    assert meta["investigation_plan"] == session.investigation_plan
    assert meta["evidence_ledger"] == session.evidence_ledger
    assert meta["tool_calls"][0]["call_id"] == "parent-1"
    nested = meta["tool_calls"][0]["metadata"]["evidence_tool_calls"]
    assert nested[0]["call_id"] == "child-1:billing-1"
    assert nested[0]["tool"] == "inspector_exec"


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

    ensure_archive(session, SimpleNamespace(elapsed_seconds=1.0))

    assert not (not_a_directory / "3485").exists()

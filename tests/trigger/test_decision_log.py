"""Test decision log JSONL append."""

import json

from zd_agent.trigger.decision_log import log_decision


def test_log_decision_writes_jsonl(tmp_path):
    p = tmp_path / "trigger_log.jsonl"
    log_decision(
        log_path=p,
        ticket_id="12345",
        verdict="substantial_issue",
        completeness=85,
        silence_min=3,
        action="enter_silence",
    )
    lines = p.read_text().splitlines()
    assert len(lines) == 1
    d = json.loads(lines[0])
    assert d["ticket"] == "12345"
    assert d["verdict"] == "substantial_issue"
    assert d["completeness"] == 85
    assert d["silence_min"] == 3
    assert d["action"] == "enter_silence"
    assert "ts" in d


def test_log_decision_appends(tmp_path):
    p = tmp_path / "log.jsonl"
    log_decision(log_path=p, ticket_id="1", verdict="greeting", action="skip")
    log_decision(log_path=p, ticket_id="2", verdict="greeting", action="skip")
    assert len(p.read_text().splitlines()) == 2


def test_log_decision_creates_parent_dir(tmp_path):
    p = tmp_path / "nested" / "dir" / "log.jsonl"
    log_decision(log_path=p, ticket_id="1", verdict="x", action="y")
    assert p.exists()


def test_log_decision_includes_extra_fields(tmp_path):
    p = tmp_path / "log.jsonl"
    log_decision(
        log_path=p,
        ticket_id="1",
        verdict="substantial_issue",
        action="enter_silence",
        extra={"rhythm": "slow", "burst_count": 2},
    )
    d = json.loads(p.read_text().splitlines()[0])
    assert d["rhythm"] == "slow"
    assert d["burst_count"] == 2


def test_log_decision_ticket_id_stringified(tmp_path):
    p = tmp_path / "log.jsonl"
    log_decision(log_path=p, ticket_id=42, verdict="x", action="y")
    d = json.loads(p.read_text().splitlines()[0])
    assert d["ticket"] == "42"

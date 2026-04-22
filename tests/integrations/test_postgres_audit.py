"""Test SQL audit logging (JSONL append)."""
import json
from pathlib import Path

from zd_agent.integrations.postgres import audit_log


def test_audit_log_writes_jsonl(tmp_path):
    log_path = tmp_path / "sql_audit.log"
    audit_log(
        log_path=log_path,
        database="prod",
        sql="SELECT * FROM tenants WHERE id='123'",
        status="OK",
        ticket_id="12345",
        row_count=1,
        elapsed_ms=123,
    )
    lines = log_path.read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["database"] == "prod"
    assert entry["status"] == "OK"
    assert entry["ticket_id"] == "12345"
    assert entry["row_count"] == 1
    assert entry["elapsed_ms"] == 123
    assert "ts" in entry
    assert entry["sql"] == "SELECT * FROM tenants WHERE id='123'"


def test_audit_log_appends(tmp_path):
    log_path = tmp_path / "sql_audit.log"
    audit_log(log_path=log_path, database="prod", sql="SELECT 1", status="OK", ticket_id="1")
    audit_log(log_path=log_path, database="prod", sql="SELECT 2", status="OK", ticket_id="2")
    lines = log_path.read_text().splitlines()
    assert len(lines) == 2


def test_audit_log_creates_parent_dir(tmp_path):
    log_path = tmp_path / "nested" / "dir" / "audit.log"
    audit_log(log_path=log_path, database="prod", sql="SELECT 1", status="OK", ticket_id="1")
    assert log_path.exists()


def test_audit_log_includes_error_field(tmp_path):
    log_path = tmp_path / "sql_audit.log"
    audit_log(
        log_path=log_path,
        database="prod", sql="SELECT 1", status="ERROR", ticket_id="1",
        error="connection refused",
    )
    entry = json.loads(log_path.read_text().splitlines()[0])
    assert entry.get("error") == "connection refused"

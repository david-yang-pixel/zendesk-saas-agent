"""Test followup mode detection."""
from zd_agent.session.followup import (
    detect_followup_mode,
    load_previous_record,
)


def test_no_record_is_first_pass(tmp_path):
    mode, rec = detect_followup_mode(
        resources_base_dir=str(tmp_path),
        ticket_records_dir="工单记录",
        ticket_id=999,
    )
    assert mode == "first_pass"
    assert rec == ""


def test_existing_record_is_followup(tmp_path):
    records = tmp_path / "工单记录"
    records.mkdir()
    (records / "12345.md").write_text("# 工单 12345\n\nprev content", encoding="utf-8")
    mode, rec = detect_followup_mode(
        resources_base_dir=str(tmp_path),
        ticket_records_dir="工单记录",
        ticket_id=12345,
    )
    assert mode == "followup"
    assert "prev content" in rec


def test_load_previous_record_returns_none_if_missing(tmp_path):
    rec = load_previous_record(
        resources_base_dir=str(tmp_path),
        ticket_records_dir="工单记录",
        ticket_id="nope",
    )
    assert rec is None


def test_load_previous_record_returns_content(tmp_path):
    records = tmp_path / "rec"
    records.mkdir()
    (records / "1.md").write_text("hello", encoding="utf-8")
    rec = load_previous_record(
        resources_base_dir=str(tmp_path),
        ticket_records_dir="rec",
        ticket_id=1,
    )
    assert rec == "hello"


def test_ticket_id_int_or_str(tmp_path):
    records = tmp_path / "工单记录"
    records.mkdir()
    (records / "42.md").write_text("content", encoding="utf-8")
    rec_int = load_previous_record(
        resources_base_dir=str(tmp_path),
        ticket_records_dir="工单记录",
        ticket_id=42,
    )
    rec_str = load_previous_record(
        resources_base_dir=str(tmp_path),
        ticket_records_dir="工单记录",
        ticket_id="42",
    )
    assert rec_int == rec_str == "content"

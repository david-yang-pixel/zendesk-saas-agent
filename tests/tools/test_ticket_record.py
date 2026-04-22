"""Test TicketRecordSearch + TicketRecordRead."""
from unittest.mock import MagicMock

import pytest

from zd_agent.tools.ticket_record import TicketRecordRead, TicketRecordSearch


@pytest.fixture
def records_setup(tmp_path, monkeypatch):
    """Create a fake resources tree with sample 工单记录 files."""
    records_dir = tmp_path / "zendesk-cli" / "工单记录"
    records_dir.mkdir(parents=True)

    (records_dir / "100.md").write_text(
        "# 工单 100\n\n## 问题\n\nworkflow 500 error on /v1/workflows\n\n## 根因\n\n代码 bug\n",
        encoding="utf-8",
    )
    (records_dir / "200.md").write_text(
        "# 工单 200\n\n## 问题\n\nknowledge retrieval returns empty\n\n## 解决\n\n调整 top_k 参数\n",
        encoding="utf-8",
    )
    (records_dir / "300.md").write_text(
        "# 工单 300\n\n## 问题\n\nworkflow timeout on specific node\n",
        encoding="utf-8",
    )

    cfg = MagicMock()
    cfg.resources.base_dir = str(tmp_path)
    cfg.resources.ticket_records_dir = "zendesk-cli/工单记录"
    monkeypatch.setattr("zd_agent.tools.ticket_record.get_config", lambda: cfg)
    return records_dir


@pytest.mark.asyncio
async def test_search_single_keyword(fake_session, records_setup):
    tool = TicketRecordSearch()
    r = await tool.execute({"keywords": ["workflow"]}, fake_session)
    assert r.success is True
    assert "100.md" in r.output
    assert "300.md" in r.output
    assert "200.md" not in r.output


@pytest.mark.asyncio
async def test_search_multiple_keywords_and_semantics(fake_session, records_setup):
    tool = TicketRecordSearch()
    r = await tool.execute(
        {"keywords": ["workflow", "timeout"]}, fake_session,
    )
    assert r.success is True
    # Only 300 has both
    assert "300.md" in r.output
    assert "100.md" not in r.output


@pytest.mark.asyncio
async def test_search_case_insensitive(fake_session, records_setup):
    tool = TicketRecordSearch()
    r = await tool.execute({"keywords": ["WORKFLOW"]}, fake_session)
    assert r.success is True
    assert "100.md" in r.output


@pytest.mark.asyncio
async def test_search_no_matches(fake_session, records_setup):
    tool = TicketRecordSearch()
    r = await tool.execute({"keywords": ["nonexistent_xyz"]}, fake_session)
    assert r.success is True
    assert "no matching" in r.output.lower()


@pytest.mark.asyncio
async def test_search_empty_keywords_rejected(fake_session, records_setup):
    tool = TicketRecordSearch()
    r = await tool.execute({"keywords": []}, fake_session)
    assert r.success is False
    assert "empty" in (r.error or "").lower()


@pytest.mark.asyncio
async def test_search_max_results_respected(fake_session, records_setup):
    tool = TicketRecordSearch()
    r = await tool.execute(
        {"keywords": ["问题"], "max_results": 2}, fake_session,
    )
    assert r.success is True
    # Should contain exactly 2 file entries (start with "- ")
    count = r.output.count("- ")
    assert count <= 2


@pytest.mark.asyncio
async def test_search_missing_dir_returns_error(fake_session, monkeypatch, tmp_path):
    cfg = MagicMock()
    cfg.resources.base_dir = str(tmp_path / "nope")
    cfg.resources.ticket_records_dir = "工单记录"
    monkeypatch.setattr("zd_agent.tools.ticket_record.get_config", lambda: cfg)
    tool = TicketRecordSearch()
    r = await tool.execute({"keywords": ["x"]}, fake_session)
    assert r.success is False
    assert "not found" in (r.error or "").lower()


@pytest.mark.asyncio
async def test_read_existing(fake_session, records_setup):
    tool = TicketRecordRead()
    r = await tool.execute({"ticket_id": "100"}, fake_session)
    assert r.success is True
    assert "workflow 500" in r.output


@pytest.mark.asyncio
async def test_read_missing(fake_session, records_setup):
    tool = TicketRecordRead()
    r = await tool.execute({"ticket_id": "999999"}, fake_session)
    assert r.success is False
    assert "no history" in (r.error or "").lower()


@pytest.mark.asyncio
async def test_read_empty_id_rejected(fake_session, records_setup):
    tool = TicketRecordRead()
    r = await tool.execute({"ticket_id": ""}, fake_session)
    assert r.success is False


@pytest.mark.asyncio
async def test_read_truncates_large_file(fake_session, records_setup, tmp_path):
    # Add a giant file
    records_dir = tmp_path / "zendesk-cli" / "工单记录"
    big = "X" * 30000
    (records_dir / "big.md").write_text(big, encoding="utf-8")
    tool = TicketRecordRead()
    r = await tool.execute({"ticket_id": "big"}, fake_session)
    assert r.success is True
    assert "truncated" in r.output
    assert len(r.output) < 21000

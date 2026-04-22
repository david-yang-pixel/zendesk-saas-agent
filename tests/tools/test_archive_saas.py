"""Test SaaS-style ArchiveTicket (single-file markdown into 工单记录/)."""
from unittest.mock import MagicMock

import pytest

from zd_agent.tools.archive import ArchiveTicket


@pytest.fixture
def records_env(tmp_path, monkeypatch):
    cfg = MagicMock()
    cfg.resources.base_dir = str(tmp_path)
    cfg.resources.ticket_records_dir = "zendesk-cli/工单记录"
    monkeypatch.setattr("zd_agent.tools.archive.get_config", lambda: cfg)
    return tmp_path / "zendesk-cli" / "工单记录"


@pytest.fixture
def session():
    s = MagicMock()
    s.reply_draft = ""
    s.ticket_type = "workflow"
    s.archived = False
    return s


@pytest.mark.asyncio
async def test_first_pass_creates_file_with_frontmatter(records_env, session):
    tool = ArchiveTicket()
    r = await tool.execute(
        {"ticket_id": 12345, "analysis": "根因: XX bug", "solution": "workaround: Y"},
        session,
    )
    assert r.success is True
    path = records_env / "12345.md"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert content.startswith("---")
    assert "ticket_id: 12345" in content
    assert "category: workflow" in content
    assert "根因: XX bug" in content
    assert "workaround: Y" in content
    assert session.archived is True


@pytest.mark.asyncio
async def test_first_pass_includes_reply_draft(records_env, session):
    session.reply_draft = "Hi,\nWe have identified the issue..."
    tool = ArchiveTicket()
    await tool.execute(
        {"ticket_id": 1, "analysis": "a", "solution": "s"},
        session,
    )
    content = (records_env / "1.md").read_text(encoding="utf-8")
    assert "客户回复草稿" in content
    assert "We have identified" in content


@pytest.mark.asyncio
async def test_followup_appends_not_overwrites(records_env, session):
    tool = ArchiveTicket()
    await tool.execute(
        {"ticket_id": 2, "analysis": "first", "solution": "s1"},
        session,
    )
    await tool.execute(
        {"ticket_id": 2, "analysis": "more findings", "solution": "s2"},
        session,
    )
    content = (records_env / "2.md").read_text(encoding="utf-8")
    assert "first" in content
    assert "more findings" in content
    assert "## 跟进" in content
    # Frontmatter only at top, appears exactly once
    assert content.count("---\nticket_id:") == 1 or content.count("---") == 2


@pytest.mark.asyncio
async def test_explicit_category_overrides_session(records_env, session):
    session.ticket_type = "from-session"
    tool = ArchiveTicket()
    await tool.execute(
        {
            "ticket_id": 3, "analysis": "a", "solution": "s",
            "category": "refund",
        },
        session,
    )
    content = (records_env / "3.md").read_text(encoding="utf-8")
    assert "category: refund" in content


@pytest.mark.asyncio
async def test_missing_parent_dir_auto_created(records_env, session, tmp_path):
    # records_env fixture doesn't create the dir — just ensure it's absent
    import shutil
    shutil.rmtree(records_env, ignore_errors=True)
    tool = ArchiveTicket()
    r = await tool.execute(
        {"ticket_id": 7, "analysis": "a", "solution": "s"},
        session,
    )
    assert r.success is True
    assert (records_env / "7.md").exists()


@pytest.mark.asyncio
async def test_followup_without_reply_no_draft_section(records_env, session):
    session.reply_draft = ""
    tool = ArchiveTicket()
    await tool.execute(
        {"ticket_id": 9, "analysis": "a1", "solution": "s1"},
        session,
    )
    # Follow-up with empty reply
    await tool.execute(
        {"ticket_id": 9, "analysis": "a2", "solution": "s2"},
        session,
    )
    content = (records_env / "9.md").read_text(encoding="utf-8")
    # There should be a 跟进 section but no "### 客户回复草稿"
    assert "## 跟进" in content
    assert "### 客户回复草稿" not in content

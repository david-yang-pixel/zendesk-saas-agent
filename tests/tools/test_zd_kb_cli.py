"""Test ZdKbCliExec with mocked subprocess."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zd_agent.tools.zd_kb_cli import ZdKbCliExec


def _mock_proc(rc: int, stdout: bytes = b"", stderr: bytes = b""):
    proc = MagicMock()
    proc.returncode = rc
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


@pytest.mark.asyncio
async def test_search_builds_correct_cmd(fake_session):
    tool = ZdKbCliExec()
    captured = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        return _mock_proc(0, b"result 1\nresult 2\n")

    with patch("zd_agent.tools.zd_kb_cli.asyncio.create_subprocess_exec", fake_exec):
        r = await tool.execute(
            {"subcommand": "search", "query": "workflow 500"},
            fake_session,
        )
    assert r.success is True
    # 第一个 arg 是 zd 可执行路径（解析到本机 venv 或 PATH）；只校验后续 args
    assert captured["args"][0].endswith("zd"), f"first arg should be zd binary: {captured['args'][0]}"
    assert captured["args"][1:] == ("kb", "search", "workflow 500")


@pytest.mark.asyncio
async def test_article_with_url_only(fake_session):
    tool = ZdKbCliExec()
    captured = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        return _mock_proc(0, b"https://support.example.com/article/123")

    with patch("zd_agent.tools.zd_kb_cli.asyncio.create_subprocess_exec", fake_exec):
        r = await tool.execute(
            {"subcommand": "article", "id": "123", "url_only": True},
            fake_session,
        )
    assert r.success is True
    assert captured["args"][0].endswith("zd")
    assert captured["args"][1:] == ("kb", "article", "123", "--url-only")


@pytest.mark.asyncio
async def test_categories_no_args(fake_session):
    tool = ZdKbCliExec()

    async def fake_exec(*args, **kwargs):
        return _mock_proc(0, b"cat list")

    with patch("zd_agent.tools.zd_kb_cli.asyncio.create_subprocess_exec", fake_exec):
        r = await tool.execute({"subcommand": "categories"}, fake_session)
    assert r.success is True


@pytest.mark.asyncio
async def test_search_needs_query(fake_session):
    tool = ZdKbCliExec()
    r = await tool.execute({"subcommand": "search"}, fake_session)
    assert r.success is False
    assert "needs query" in (r.error or "")


@pytest.mark.asyncio
async def test_article_needs_id(fake_session):
    tool = ZdKbCliExec()
    r = await tool.execute({"subcommand": "article"}, fake_session)
    assert r.success is False


@pytest.mark.asyncio
async def test_unknown_subcommand(fake_session):
    tool = ZdKbCliExec()
    r = await tool.execute({"subcommand": "bogus"}, fake_session)
    assert r.success is False


@pytest.mark.asyncio
async def test_non_zero_exit_returns_error(fake_session):
    tool = ZdKbCliExec()

    async def fake_exec(*args, **kwargs):
        return _mock_proc(1, b"", b"error: not found")

    with patch("zd_agent.tools.zd_kb_cli.asyncio.create_subprocess_exec", fake_exec):
        r = await tool.execute(
            {"subcommand": "article", "id": "999"}, fake_session,
        )
    assert r.success is False
    assert "not found" in (r.error or "")


@pytest.mark.asyncio
async def test_cli_not_installed(fake_session):
    tool = ZdKbCliExec()

    async def fake_exec(*args, **kwargs):
        raise FileNotFoundError("zd not found")

    with patch("zd_agent.tools.zd_kb_cli.asyncio.create_subprocess_exec", fake_exec):
        r = await tool.execute(
            {"subcommand": "search", "query": "x"}, fake_session,
        )
    assert r.success is False
    assert "not installed" in (r.error or "")

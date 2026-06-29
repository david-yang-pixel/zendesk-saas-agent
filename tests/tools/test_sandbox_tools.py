from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zd_agent.tools.sandbox_tools import (
    SandboxCreateWorktree,
    SandboxList,
    SandboxRead,
    SandboxSearch,
    SandboxWrite,
)


@pytest.fixture
def sandbox_session(tmp_path: Path):
    session = MagicMock()
    session.sandbox_dir = tmp_path
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')", encoding="utf-8")
    return session


@pytest.mark.asyncio
async def test_sandbox_list_rejects_invalid_numeric_args(sandbox_session):
    tool = SandboxList()

    result = await tool.execute(
        {"path": ".", "max_depth": "deep", "limit": "many"},
        sandbox_session,
    )

    assert result.success is False
    assert "max_depth" in (result.error or "")


@pytest.mark.asyncio
async def test_sandbox_read_rejects_invalid_line_args(sandbox_session):
    tool = SandboxRead()

    result = await tool.execute(
        {"filepath": "src/app.py", "start_line": "first"},
        sandbox_session,
    )

    assert result.success is False
    assert "start_line" in (result.error or "")

    result = await tool.execute(
        {"filepath": "src/app.py", "end_line": "last"},
        sandbox_session,
    )

    assert result.success is False
    assert "end_line" in (result.error or "")


@pytest.mark.asyncio
async def test_sandbox_create_worktree_rejects_invalid_string_args(sandbox_session):
    tool = SandboxCreateWorktree()

    result = await tool.execute(
        {"repo": ["dify"], "ref": "main"},
        sandbox_session,
    )

    assert result.success is False
    assert result.error == "repo must be a string"

    result = await tool.execute(
        {"repo": "dify", "ref": {"branch": "main"}},
        sandbox_session,
    )

    assert result.success is False
    assert result.error == "ref must be a string"


@pytest.mark.asyncio
async def test_sandbox_write_rejects_invalid_string_args(sandbox_session):
    tool = SandboxWrite()

    result = await tool.execute(
        {"filepath": ["src/app.py"], "content": "print('new')"},
        sandbox_session,
    )

    assert result.success is False
    assert result.error == "filepath must be a string"

    result = await tool.execute(
        {"filepath": "src/app.py", "content": ["print('new')"]},
        sandbox_session,
    )

    assert result.success is False
    assert result.error == "content must be a string"


@pytest.mark.asyncio
async def test_sandbox_write_uses_remote_worker_without_local_write(sandbox_session):
    target = sandbox_session.sandbox_dir / "src" / "app.py"
    original = target.read_text(encoding="utf-8")

    with patch(
        "zd_agent.tools.sandbox_tools.call_sandbox_worker_tool",
        new_callable=AsyncMock,
        return_value={"success": True, "output": "已写入: src/app.py"},
    ) as worker:
        result = await SandboxWrite().execute(
            {"filepath": "src/app.py", "content": "print('remote')"},
            sandbox_session,
        )

    assert result.success is True
    assert target.read_text(encoding="utf-8") == original
    worker.assert_awaited_once()
    assert worker.await_args.args[0] == "sandbox_write"


@pytest.mark.asyncio
async def test_sandbox_read_uses_remote_worker(sandbox_session):
    with patch(
        "zd_agent.tools.sandbox_tools.call_sandbox_worker_tool",
        new_callable=AsyncMock,
        return_value={"success": True, "output": "文件: src/app.py\n1|remote"},
    ) as worker:
        result = await SandboxRead().execute(
            {"filepath": "src/app.py", "start_line": 1},
            sandbox_session,
        )

    assert result.success is True
    assert "remote" in (result.output or "")
    worker.assert_awaited_once()
    assert worker.await_args.args[0] == "sandbox_read"


@pytest.mark.asyncio
async def test_sandbox_read_rejects_invalid_filepath_arg(sandbox_session):
    result = await SandboxRead().execute(
        {"filepath": ["src/app.py"]},
        sandbox_session,
    )

    assert result.success is False
    assert result.error == "filepath must be a string"


@pytest.mark.asyncio
async def test_sandbox_list_rejects_invalid_path_arg(sandbox_session):
    result = await SandboxList().execute(
        {"path": ["src"]},
        sandbox_session,
    )

    assert result.success is False
    assert result.error == "path must be a string"


@pytest.mark.asyncio
async def test_sandbox_search_rejects_invalid_string_args(sandbox_session):
    tool = SandboxSearch()

    result = await tool.execute(
        {"pattern": ["print"], "path": "."},
        sandbox_session,
    )

    assert result.success is False
    assert result.error == "pattern must be a string"

    result = await tool.execute(
        {"pattern": "print", "path": {"dir": "src"}},
        sandbox_session,
    )

    assert result.success is False
    assert result.error == "path must be a string"

    result = await tool.execute(
        {"pattern": "print", "path": ".", "glob": ["*.py"]},
        sandbox_session,
    )

    assert result.success is False
    assert result.error == "glob must be a string"

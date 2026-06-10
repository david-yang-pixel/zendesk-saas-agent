from pathlib import Path

import pytest

from zd_agent.tools.git_source import GitDiff, GitGrep, GitLog, GitShow, ListRepoTags


@pytest.mark.asyncio
async def test_git_show_rejects_invalid_string_args(fake_session):
    tool = GitShow()

    result = await tool.execute(
        {"repo": ["dify"], "ref": "main", "filepath": "README.md"},
        fake_session,
    )

    assert result.success is False
    assert result.error == "repo must be a string"

    result = await tool.execute(
        {"repo": "dify", "ref": {"branch": "main"}, "filepath": "README.md"},
        fake_session,
    )

    assert result.success is False
    assert result.error == "ref must be a string"

    result = await tool.execute(
        {"repo": "dify", "ref": "main", "filepath": ["README.md"]},
        fake_session,
    )

    assert result.success is False
    assert result.error == "filepath must be a string"


@pytest.mark.asyncio
async def test_git_grep_rejects_invalid_string_args(fake_session):
    tool = GitGrep()

    result = await tool.execute(
        {"repo": "dify", "ref": "main", "pattern": ["queue"]},
        fake_session,
    )

    assert result.success is False
    assert result.error == "pattern must be a string"

    result = await tool.execute(
        {"repo": "dify", "ref": "main", "pattern": "queue", "glob": ["*.py"]},
        fake_session,
    )

    assert result.success is False
    assert result.error == "glob must be a string"


@pytest.mark.asyncio
async def test_git_grep_fetches_once_on_ambiguous_ref(fake_session, tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.setattr(
        "zd_agent.tools.git_source.resolve_repo",
        lambda name: repo_dir if name == "dify" else None,
    )

    calls = 0

    async def fake_run_git(repo_dir_arg, *args, max_output=30000):
        nonlocal calls
        calls += 1
        if calls == 1:
            return 128, "", "fatal: ambiguous argument 'hotfix--1.14.2': unknown revision"
        return 0, "api/foo.py\n12:queue\n", ""

    fetches = 0

    async def fake_fetch(repo_name):
        nonlocal fetches
        fetches += 1
        return True

    monkeypatch.setattr("zd_agent.tools.git_source._run_git", fake_run_git)
    monkeypatch.setattr("zd_agent.tools.git_source._auto_fetch_repo", fake_fetch)

    result = await GitGrep().execute(
        {"repo": "dify", "ref": "hotfix--1.14.2", "pattern": "queue"},
        fake_session,
    )

    assert result.success is True
    assert "api/foo.py" in result.output
    assert calls == 2
    assert fetches == 1


@pytest.mark.asyncio
async def test_git_grep_ref_error_tells_agent_to_confirm_ref(fake_session, tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.setattr(
        "zd_agent.tools.git_source.resolve_repo",
        lambda name: repo_dir if name == "dify" else None,
    )

    async def fake_run_git(repo_dir_arg, *args, max_output=30000):
        return 128, "", "fatal: unable to resolve revision: hotfix-1.14.2"

    async def fake_fetch(repo_name):
        return False

    monkeypatch.setattr("zd_agent.tools.git_source._run_git", fake_run_git)
    monkeypatch.setattr("zd_agent.tools.git_source._auto_fetch_repo", fake_fetch)

    result = await GitGrep().execute(
        {"repo": "dify", "ref": "hotfix-1.14.2", "pattern": "queue"},
        fake_session,
    )

    assert result.success is False
    assert "无法解析版本引用 'hotfix-1.14.2'" in (result.error or "")
    assert "list_repo_tags" in (result.error or "")


@pytest.mark.asyncio
async def test_git_log_rejects_invalid_string_args(fake_session):
    tool = GitLog()

    result = await tool.execute(
        {"repo": ["dify"], "ref": "main"},
        fake_session,
    )

    assert result.success is False
    assert result.error == "repo must be a string"

    result = await tool.execute(
        {"repo": "dify", "ref": {"branch": "main"}},
        fake_session,
    )

    assert result.success is False
    assert result.error == "ref must be a string"

    result = await tool.execute(
        {"repo": "dify", "ref": "main", "filepath": ["README.md"]},
        fake_session,
    )

    assert result.success is False
    assert result.error == "filepath must be a string"


@pytest.mark.asyncio
async def test_git_log_rejects_invalid_n_arg(fake_session, tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.setattr(
        "zd_agent.tools.git_source.resolve_repo",
        lambda name: repo_dir if name == "dify" else None,
    )

    result = await GitLog().execute(
        {"repo": "dify", "ref": "main", "n": "many"},
        fake_session,
    )

    assert result.success is False
    assert result.error == "limit must be an integer"


@pytest.mark.asyncio
async def test_git_diff_rejects_invalid_string_args(fake_session):
    tool = GitDiff()

    result = await tool.execute(
        {"repo": ["dify"], "ref1": "v1", "ref2": "v2"},
        fake_session,
    )

    assert result.success is False
    assert result.error == "repo must be a string"

    result = await tool.execute(
        {"repo": "dify", "ref1": ["v1"], "ref2": "v2"},
        fake_session,
    )

    assert result.success is False
    assert result.error == "ref1 must be a string"

    result = await tool.execute(
        {"repo": "dify", "ref1": "v1", "ref2": {"tag": "v2"}},
        fake_session,
    )

    assert result.success is False
    assert result.error == "ref2 must be a string"

    result = await tool.execute(
        {"repo": "dify", "ref1": "v1", "ref2": "v2", "filepath": ["README.md"]},
        fake_session,
    )

    assert result.success is False
    assert result.error == "filepath must be a string"


@pytest.mark.asyncio
async def test_list_repo_tags_rejects_invalid_repo_arg(fake_session):
    result = await ListRepoTags().execute(
        {"repo": ["dify"]},
        fake_session,
    )

    assert result.success is False
    assert result.error == "repo must be a string"


@pytest.mark.asyncio
async def test_list_repo_tags_rejects_invalid_limit(fake_session, tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.setattr(
        "zd_agent.tools.git_source.resolve_repo",
        lambda name: repo_dir if name == "dify" else None,
    )
    monkeypatch.setattr("zd_agent.tools.git_source.list_tags", lambda name: ["1.0.0"])

    result = await ListRepoTags().execute(
        {"repo": "dify", "limit": "many"},
        fake_session,
    )

    assert result.success is False
    assert "limit" in (result.error or "")

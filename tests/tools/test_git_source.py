from pathlib import Path

import pytest

from zd_agent.tools.git_source import ListRepoTags


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

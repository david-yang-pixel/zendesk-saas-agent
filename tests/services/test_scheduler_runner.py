from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_fetch_repos_task_fetches_all_repositories():
    from zd_agent.services.scheduler_runner import fetch_repos_task

    repo_a = MagicMock(name="repo_a")
    repo_a.name = "repo-a"
    repo_b = MagicMock(name="repo_b")
    repo_b.name = "repo-b"
    cfg = MagicMock()
    cfg.repos.repositories = [repo_a, repo_b]

    with (
        patch("zd_agent.services.scheduler_runner.get_config", return_value=cfg),
        patch(
            "zd_agent.services.scheduler_runner.fetch_repo",
            side_effect=[(True, "ok-a"), (False, "bad-b")],
        ) as fetch_repo,
    ):
        await fetch_repos_task()

    assert fetch_repo.call_count == 2
    assert [call.args[0] for call in fetch_repo.call_args_list] == [repo_a, repo_b]

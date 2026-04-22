"""Test SaasBranchSwitch with real temp git repos."""
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from zd_agent.tools.saas_branch import SaasBranchSwitch, _resolve_repo_name


def _make_repo_with_branches(repo_dir: Path, branches: list[str]) -> None:
    """Create a local git repo containing the given branches, plus a fake remote."""
    repo_dir.mkdir(parents=True, exist_ok=True)

    # Create a bare-ish remote in a sibling dir
    remote_dir = repo_dir.parent / f"{repo_dir.name}-remote.git"
    subprocess.run(["git", "init", "--bare", str(remote_dir)], check=True, capture_output=True)

    # Init repo
    subprocess.run(["git", "init", str(repo_dir)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.email", "t@example.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.name", "t"],
        check=True, capture_output=True,
    )
    (repo_dir / "README.md").write_text("initial", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_dir), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "commit", "-m", "initial"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "remote", "add", "origin", str(remote_dir)],
        check=True, capture_output=True,
    )

    # Create branches, commit something different on each, push to remote
    default_branch = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "--abbrev-ref", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    subprocess.run(
        ["git", "-C", str(repo_dir), "push", "origin", default_branch],
        check=True, capture_output=True,
    )

    for branch in branches:
        if branch == default_branch:
            continue
        subprocess.run(
            ["git", "-C", str(repo_dir), "checkout", "-b", branch],
            check=True, capture_output=True,
        )
        (repo_dir / f"{branch.replace('/', '_')}.txt").write_text(branch, encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repo_dir), "add", "."],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_dir), "commit", "-m", f"on {branch}"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_dir), "push", "origin", branch],
            check=True, capture_output=True,
        )

    # Back to default
    subprocess.run(
        ["git", "-C", str(repo_dir), "checkout", default_branch],
        check=True, capture_output=True,
    )

    # Delete local non-default branches so fetch actually has to pull them
    for b in branches:
        if b == default_branch:
            continue
        subprocess.run(
            ["git", "-C", str(repo_dir), "branch", "-D", b],
            capture_output=True,
        )


def test_alias_resolution():
    aliases = {"d": "dify", "bill": "dify-billing", "mb": "dify-marketplace-backend"}
    assert _resolve_repo_name("d", aliases) == "dify"
    assert _resolve_repo_name("bill", aliases) == "dify-billing"
    assert _resolve_repo_name("mb", aliases) == "dify-marketplace-backend"
    # Non-alias: pass-through
    assert _resolve_repo_name("dify", aliases) == "dify"
    # Case-insensitive alias lookup
    assert _resolve_repo_name("BILL", aliases) == "dify-billing"


@pytest.fixture
def configured_repos(tmp_path, monkeypatch):
    base = tmp_path / "repos"
    base.mkdir()
    _make_repo_with_branches(base / "dify", ["hotfix/1.11.2-fix3"])

    cfg = MagicMock()
    cfg.repos.base_dir = str(base)
    cfg.repos.aliases = {"d": "dify", "bill": "dify-billing"}
    monkeypatch.setattr("zd_agent.tools.saas_branch.get_config", lambda: cfg)
    return base


@pytest.mark.asyncio
async def test_switch_branch_success(fake_session, configured_repos):
    tool = SaasBranchSwitch()
    r = await tool.execute(
        {"repo": "dify", "branch": "hotfix/1.11.2-fix3"},
        fake_session,
    )
    assert r.success is True, r.error
    assert "dify" in r.output
    assert "hotfix/1.11.2-fix3" in r.output
    assert "@" in r.output  # commit short-sha


@pytest.mark.asyncio
async def test_switch_via_alias(fake_session, configured_repos):
    tool = SaasBranchSwitch()
    r = await tool.execute(
        {"repo": "d", "branch": "hotfix/1.11.2-fix3"},  # "d" → "dify"
        fake_session,
    )
    assert r.success is True, r.error


@pytest.mark.asyncio
async def test_missing_repo_dir_error(fake_session, configured_repos):
    tool = SaasBranchSwitch()
    r = await tool.execute(
        {"repo": "nonexistent-repo", "branch": "main"},
        fake_session,
    )
    assert r.success is False
    assert "not a git repo" in (r.error or "").lower()


@pytest.mark.asyncio
async def test_empty_repo_rejected(fake_session, configured_repos):
    tool = SaasBranchSwitch()
    r = await tool.execute({"repo": "", "branch": "main"}, fake_session)
    assert r.success is False


@pytest.mark.asyncio
async def test_empty_branch_rejected(fake_session, configured_repos):
    tool = SaasBranchSwitch()
    r = await tool.execute({"repo": "dify", "branch": ""}, fake_session)
    assert r.success is False


@pytest.mark.asyncio
async def test_nonexistent_branch_error(fake_session, configured_repos):
    tool = SaasBranchSwitch()
    r = await tool.execute(
        {"repo": "dify", "branch": "does-not-exist-branch"},
        fake_session,
    )
    assert r.success is False
    assert "fetch" in (r.error or "").lower() or "checkout" in (r.error or "").lower()

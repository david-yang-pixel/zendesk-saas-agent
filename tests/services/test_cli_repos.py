from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class FakeRepo:
    name: str
    url: str = ""


@dataclass(frozen=True)
class FakeRepoStatus:
    name: str
    cloned: bool
    is_bare: bool = False
    tag_count: int = 0
    branch_count: int = 0
    last_fetch: datetime | None = None


def test_run_repo_fetch_selects_requested_repos_and_reports_missing():
    from zd_agent.services.cli_repos import run_repo_fetch

    repos = [FakeRepo("dify"), FakeRepo("billing")]
    calls: list[str] = []

    def fake_clone(entry: FakeRepo) -> tuple[bool, str]:
        calls.append(entry.name)
        return entry.name == "dify", f"{entry.name} done"

    result = run_repo_fetch(
        repo_names=["dify", "missing"],
        clone=True,
        repositories=repos,
        clone_repo_fn=fake_clone,
    )

    assert result.not_found == {"missing"}
    assert result.total == 1
    assert result.success_count == 1
    assert result.rows == [
        {"name": "dify", "ok": True, "message": "dify done"},
    ]
    assert calls == ["dify"]


def test_run_repo_fetch_uses_fetch_when_clone_is_false():
    from zd_agent.services.cli_repos import run_repo_fetch

    repos = [FakeRepo("dify"), FakeRepo("billing")]
    calls: list[str] = []

    def fake_fetch(entry: FakeRepo) -> tuple[bool, str]:
        calls.append(entry.name)
        return True, f"{entry.name} fetched"

    result = run_repo_fetch(
        repo_names=None,
        clone=False,
        repositories=repos,
        fetch_repo_fn=fake_fetch,
    )

    assert result.not_found == set()
    assert result.total == 2
    assert result.success_count == 2
    assert [row["message"] for row in result.rows] == [
        "dify fetched",
        "billing fetched",
    ]
    assert calls == ["dify", "billing"]


def test_collect_repo_status_returns_display_ready_rows():
    from zd_agent.services.cli_repos import collect_repo_status

    last_fetch = datetime(2026, 6, 5, 8, 9, tzinfo=timezone.utc)
    repos = [FakeRepo("dify"), FakeRepo("missing")]

    def fake_status(entry: FakeRepo) -> FakeRepoStatus:
        if entry.name == "dify":
            return FakeRepoStatus(
                name=entry.name,
                cloned=True,
                is_bare=True,
                tag_count=3,
                branch_count=4,
                last_fetch=last_fetch,
            )
        return FakeRepoStatus(name=entry.name, cloned=False)

    rows = collect_repo_status(repositories=repos, get_repo_status_fn=fake_status)

    assert rows == [
        {
            "name": "dify",
            "cloned": True,
            "mode": "bare",
            "tag_count": 3,
            "branch_count": 4,
            "last_fetch": "06-05 08:09",
        },
        {
            "name": "missing",
            "cloned": False,
            "mode": "-",
            "tag_count": "-",
            "branch_count": "-",
            "last_fetch": "-",
        },
    ]


def test_collect_repo_tags_handles_found_empty_and_missing():
    from zd_agent.services.cli_repos import collect_repo_tags

    def fake_resolve(repo_name: str) -> Path | None:
        return Path("/repos") / repo_name if repo_name != "missing" else None

    def fake_tags(repo_name: str) -> list[str]:
        return ["1.2.0", "1.1.0", "1.0.0"] if repo_name == "dify" else []

    found = collect_repo_tags(
        "dify",
        limit=2,
        resolve_repo_fn=fake_resolve,
        list_tags_fn=fake_tags,
    )
    empty = collect_repo_tags(
        "empty",
        limit=2,
        resolve_repo_fn=fake_resolve,
        list_tags_fn=fake_tags,
    )
    missing = collect_repo_tags(
        "missing",
        limit=2,
        resolve_repo_fn=fake_resolve,
        list_tags_fn=fake_tags,
    )

    assert found.found is True
    assert found.tags == ["1.2.0", "1.1.0"]
    assert found.total == 3
    assert found.remaining == 1
    assert empty.found is True
    assert empty.tags == []
    assert empty.total == 0
    assert missing.found is False
    assert missing.tags == []

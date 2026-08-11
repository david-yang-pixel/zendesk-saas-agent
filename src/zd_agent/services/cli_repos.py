"""Repository management helpers for CLI commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, Sequence


class RepoEntryLike(Protocol):
    name: str


class RepoStatusLike(Protocol):
    name: str
    cloned: bool
    is_bare: bool
    tag_count: int
    branch_count: int
    last_fetch: object | None


RepoOperation = Callable[[RepoEntryLike], tuple[bool, str]]
RepoStatusProvider = Callable[[RepoEntryLike], RepoStatusLike]
RepoResolver = Callable[[str], Path | None]
TagLister = Callable[[str], list[str]]


@dataclass(frozen=True)
class RepoFetchResult:
    not_found: set[str]
    total: int
    success_count: int
    rows: list[dict[str, object]]


@dataclass(frozen=True)
class RepoTagsResult:
    found: bool
    total: int
    tags: list[str]
    remaining: int


def _default_repositories() -> Sequence[RepoEntryLike]:
    from zd_agent.config import get_config

    return get_config().repos.repositories


def _default_clone_repo(entry: RepoEntryLike) -> tuple[bool, str]:
    from zd_agent.repos import clone_repo

    return clone_repo(entry)  # type: ignore[arg-type]


def _default_fetch_repo(entry: RepoEntryLike) -> tuple[bool, str]:
    from zd_agent.repos import fetch_repo

    return fetch_repo(entry)  # type: ignore[arg-type]


def _default_get_repo_status(entry: RepoEntryLike) -> RepoStatusLike:
    from zd_agent.repos import get_repo_status

    return get_repo_status(entry)  # type: ignore[arg-type]


def _default_resolve_repo(repo_name: str) -> Path | None:
    from zd_agent.repos import resolve_repo

    return resolve_repo(repo_name)


def _default_list_tags(repo_name: str) -> list[str]:
    from zd_agent.repos import list_tags

    return list_tags(repo_name)


def run_repo_fetch(
    *,
    repo_names: list[str] | None,
    clone: bool,
    repositories: Sequence[RepoEntryLike] | None = None,
    clone_repo_fn: RepoOperation = _default_clone_repo,
    fetch_repo_fn: RepoOperation = _default_fetch_repo,
) -> RepoFetchResult:
    """Run clone/fetch operations for selected configured repositories."""
    available = list(
        repositories if repositories is not None else _default_repositories()
    )
    selected = available
    not_found: set[str] = set()
    if repo_names:
        requested = set(repo_names)
        selected = [entry for entry in available if entry.name in requested]
        not_found = requested - {entry.name for entry in selected}

    operation = clone_repo_fn if clone else fetch_repo_fn
    rows: list[dict[str, object]] = []
    success_count = 0
    for entry in selected:
        ok, message = operation(entry)
        if ok:
            success_count += 1
        rows.append({"name": entry.name, "ok": ok, "message": message})

    return RepoFetchResult(
        not_found=not_found,
        total=len(selected),
        success_count=success_count,
        rows=rows,
    )


def collect_repo_status(
    *,
    repositories: Sequence[RepoEntryLike] | None = None,
    get_repo_status_fn: RepoStatusProvider = _default_get_repo_status,
) -> list[dict[str, object]]:
    """Collect repository status rows for CLI display."""
    rows: list[dict[str, object]] = []
    for entry in repositories if repositories is not None else _default_repositories():
        status = get_repo_status_fn(entry)
        if status.cloned:
            last_fetch = (
                status.last_fetch.strftime("%m-%d %H:%M")  # type: ignore[union-attr]
                if status.last_fetch
                else "-"
            )
            rows.append(
                {
                    "name": status.name,
                    "cloned": True,
                    "mode": "bare" if status.is_bare else "normal",
                    "tag_count": status.tag_count,
                    "branch_count": status.branch_count,
                    "last_fetch": last_fetch,
                }
            )
        else:
            rows.append(
                {
                    "name": status.name,
                    "cloned": False,
                    "mode": "-",
                    "tag_count": "-",
                    "branch_count": "-",
                    "last_fetch": "-",
                }
            )

    return rows


def collect_repo_tags(
    repo_name: str,
    *,
    limit: int,
    resolve_repo_fn: RepoResolver = _default_resolve_repo,
    list_tags_fn: TagLister = _default_list_tags,
) -> RepoTagsResult:
    """Collect a limited tag list for a repository."""
    if resolve_repo_fn(repo_name) is None:
        return RepoTagsResult(found=False, total=0, tags=[], remaining=0)

    tags = list_tags_fn(repo_name)
    shown = tags[:limit]
    return RepoTagsResult(
        found=True,
        total=len(tags),
        tags=shown,
        remaining=max(len(tags) - len(shown), 0),
    )

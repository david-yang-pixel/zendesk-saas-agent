"""源码仓库池管理：bare clone、fetch、状态查询

仓库以 bare clone 方式存放（无工作区），支持：
- git show / git grep 直接在 bare repo 上执行
- git worktree add 从 bare repo 创建隔离工作区
"""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import re

from zd_agent.config import EnvSettings, RepoEntry, get_config
from zd_agent.log import get_logger

logger = get_logger(module="repos")

# git@github.com:org/repo.git → https://x-access-token:<token>@github.com/org/repo.git
_SSH_URL_RE = re.compile(r"^git@github\.com:(.+)$")


def _resolve_clone_url(url: str) -> str:
    """根据环境变量决定仓库认证方式

    有 GITHUB_TOKEN 时自动将 SSH URL 转为 HTTPS+token 形式，
    适配无 SSH key 的服务器部署场景。
    """
    env = EnvSettings()
    if not env.github_token:
        return url

    m = _SSH_URL_RE.match(url)
    if m:
        return f"https://x-access-token:{env.github_token}@github.com/{m.group(1)}"

    # 已经是 HTTPS URL，注入 token
    if url.startswith("https://github.com/"):
        return url.replace(
            "https://github.com/",
            f"https://x-access-token:{env.github_token}@github.com/",
        )

    return url


def _repo_dir(repo_name: str) -> Path:
    """获取仓库本地路径"""
    cfg = get_config()
    return Path(cfg.repos.base_dir) / repo_name


def is_git_repo(path: Path) -> bool:
    """判断路径是否为有效的 git 仓库（bare 或 normal）"""
    if not path.exists():
        return False
    # bare repo: 目录下直接有 HEAD 文件
    if (path / "HEAD").is_file():
        return True
    # normal repo: 有 .git 子目录
    if (path / ".git").exists():
        return True
    return False


def resolve_repo(repo_name: str) -> Path | None:
    """解析仓库名到本地路径，支持 bare 和 normal repo"""
    path = _repo_dir(repo_name)
    return path if is_git_repo(path) else None


@dataclass
class RepoStatus:
    name: str
    url: str
    cloned: bool
    is_bare: bool
    tag_count: int
    branch_count: int
    last_fetch: datetime | None


async def _run_git_async(
    repo_dir: str, *args: str, timeout: int = 300
) -> tuple[int, str, str]:
    """异步执行 git 命令"""
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", repo_dir, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return 1, "", f"命令超时（{timeout}s）"
    return proc.returncode or 0, stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace")


def _run_git_sync(repo_dir: str, *args: str, timeout: int = 300) -> tuple[int, str, str]:
    """同步执行 git 命令（CLI 用）"""
    try:
        result = subprocess.run(
            ["git", "-C", repo_dir, *args],
            capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 1, "", f"命令超时（{timeout}s）"


def clone_repo(entry: RepoEntry) -> tuple[bool, str]:
    """同步 bare clone 一个仓库，返回 (成功?, 消息)

    认证策略：
    - 有 GITHUB_TOKEN 环境变量 → 自动将 SSH URL 转为 HTTPS+token
    - 无 GITHUB_TOKEN → 原样使用 config 中的 URL（依赖本地 SSH key）
    """
    repo_path = _repo_dir(entry.name)
    clone_url = _resolve_clone_url(entry.url)

    if is_git_repo(repo_path):
        _update_remote_url(repo_path, clone_url)
        code, _, err = _run_git_sync(str(repo_path), "fetch", "--all", "--prune", "--tags")
        if code != 0:
            return False, f"fetch 失败: {err.strip()}"
        return True, "已存在，fetch 完成"

    repo_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["git", "clone", "--bare", clone_url, str(repo_path)],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            return False, f"clone 失败: {result.stderr.strip()}"
        return True, "bare clone 完成"
    except subprocess.TimeoutExpired:
        return False, "clone 超时（600s）"


def _update_remote_url(repo_path: Path, url: str) -> None:
    """更新已有仓库的 remote URL（GITHUB_TOKEN 变更时自动同步）"""
    code, current_url, _ = _run_git_sync(
        str(repo_path), "remote", "get-url", "origin",
    )
    if code == 0 and current_url.strip() != url:
        _run_git_sync(str(repo_path), "remote", "set-url", "origin", url)
        logger.info("remote_url_updated", repo=repo_path.name, new_url=_mask_token(url))


def _mask_token(url: str) -> str:
    """日志输出时遮蔽 token"""
    return re.sub(r"x-access-token:[^@]+@", "x-access-token:****@", url)


def fetch_repo(entry: RepoEntry) -> tuple[bool, str]:
    """同步 fetch 一个仓库"""
    repo_path = _repo_dir(entry.name)

    if not is_git_repo(repo_path):
        return clone_repo(entry)

    _update_remote_url(repo_path, _resolve_clone_url(entry.url))
    code, _, err = _run_git_sync(str(repo_path), "fetch", "--all", "--prune", "--tags")
    if code != 0:
        return False, f"fetch 失败: {err.strip()}"
    return True, "fetch 完成"


def get_repo_status(entry: RepoEntry) -> RepoStatus:
    """获取单个仓库的状态信息"""
    repo_path = _repo_dir(entry.name)

    if not is_git_repo(repo_path):
        return RepoStatus(
            name=entry.name, url=entry.url,
            cloned=False, is_bare=False,
            tag_count=0, branch_count=0, last_fetch=None,
        )

    is_bare = (repo_path / "HEAD").is_file() and not (repo_path / ".git").exists()

    # tag 数量
    code, out, _ = _run_git_sync(str(repo_path), "tag", "-l")
    tag_count = len([t for t in out.strip().split("\n") if t]) if code == 0 and out.strip() else 0

    # 远程分支数量
    code, out, _ = _run_git_sync(str(repo_path), "branch", "-r" if not is_bare else "-a")
    branch_count = len([b for b in out.strip().split("\n") if b.strip()]) if code == 0 and out.strip() else 0

    # 最后 fetch 时间（通过 FETCH_HEAD 文件的修改时间）
    last_fetch = None
    fetch_head = repo_path / "FETCH_HEAD"
    if not fetch_head.exists():
        fetch_head = repo_path / ".git" / "FETCH_HEAD"
    if fetch_head.exists():
        mtime = fetch_head.stat().st_mtime
        last_fetch = datetime.fromtimestamp(mtime, tz=timezone.utc)

    return RepoStatus(
        name=entry.name, url=entry.url,
        cloned=True, is_bare=is_bare,
        tag_count=tag_count, branch_count=branch_count,
        last_fetch=last_fetch,
    )


def list_tags(repo_name: str) -> list[str]:
    """列出仓库的所有 tag"""
    repo_path = _repo_dir(repo_name)
    if not is_git_repo(repo_path):
        return []
    code, out, _ = _run_git_sync(
        str(repo_path), "tag", "-l", "--sort=-version:refname",
    )
    if code != 0 or not out.strip():
        return []
    return [t.strip() for t in out.strip().split("\n") if t.strip()]

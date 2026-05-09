"""Sandbox 抽象基类与具体实现

- LocalSandbox: 基于临时目录的本地沙箱（当前唯一实现）

之前还有 DockerSandbox 占位类（仅 NotImplementedError stub），2026-05 删除：
未来真要做容器隔离时会换更现代的方案（如 Firecracker / gVisor），不会复用
docker-py 接口；保留死代码反而误导。
"""

from __future__ import annotations

import asyncio
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from zd_agent.config import get_config
from zd_agent.log import get_logger
from zd_agent.repos import resolve_repo

logger = get_logger(module="sandbox")


@dataclass
class ExecResult:
    returncode: int
    stdout: str
    stderr: str


class Sandbox(ABC):
    """沙箱抽象基类（保留以便将来扩展隔离实现）。"""

    @abstractmethod
    async def create(self, ticket_id: int) -> Path:
        ...

    @abstractmethod
    async def cleanup(self) -> None:
        ...

    @abstractmethod
    def get_dir(self) -> Path | None:
        ...


class LocalSandbox(Sandbox):
    """基于临时目录的本地沙箱"""

    def __init__(self) -> None:
        self._sandbox_dir: Path | None = None
        self._ticket_id: int | None = None

    async def create(self, ticket_id: int) -> Path:
        cfg = get_config()
        self._ticket_id = ticket_id
        self._sandbox_dir = Path(cfg.sandbox.base_dir) / str(ticket_id)
        self._sandbox_dir.mkdir(parents=True, exist_ok=True)

        (self._sandbox_dir / "attachments").mkdir(exist_ok=True)

        logger.info("sandbox_created", ticket_id=ticket_id, path=str(self._sandbox_dir))
        return self._sandbox_dir

    async def cleanup(self) -> None:
        """无条件清理 sandbox 目录（由 runner 决定是否调用）"""
        if not self._sandbox_dir:
            return

        # resolve() 消除 macOS /tmp → /private/tmp 符号链接差异
        sandbox_resolved = self._sandbox_dir.resolve()
        worktree_dir = sandbox_resolved / "worktree"

        if worktree_dir.exists():
            for child in worktree_dir.iterdir():
                if not child.is_dir():
                    continue
                child_resolved = child.resolve()
                logger.info("removing_worktree", path=str(child_resolved))
                repo_dir = resolve_repo(child.name)
                if repo_dir is not None:
                    # unlock → remove → prune 三步确保完全清理
                    for wt_path in (str(child_resolved), str(child)):
                        unlock = await asyncio.create_subprocess_exec(
                            "git", "-C", str(repo_dir),
                            "worktree", "unlock", wt_path,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        await unlock.communicate()
                    proc = await asyncio.create_subprocess_exec(
                        "git", "-C", str(repo_dir),
                        "worktree", "remove", "--force", str(child_resolved),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await proc.communicate()
                    prune_proc = await asyncio.create_subprocess_exec(
                        "git", "-C", str(repo_dir),
                        "worktree", "prune",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await prune_proc.communicate()
                shutil.rmtree(child_resolved, ignore_errors=True)

        if sandbox_resolved.exists():
            shutil.rmtree(sandbox_resolved, ignore_errors=True)

        # 清理未 resolve 的原始路径（双重保险）
        if self._sandbox_dir.exists():
            shutil.rmtree(self._sandbox_dir, ignore_errors=True)

        logger.info("sandbox_cleaned", ticket_id=self._ticket_id)

    def get_dir(self) -> Path | None:
        return self._sandbox_dir


def create_sandbox() -> Sandbox:
    """创建 Sandbox 实例。当前唯一实现是 LocalSandbox。

    config.yaml 里 ``sandbox.type`` 字段保留向后兼容（可能旧配置文件设了
    "docker" 或别的值），但目前一律忽略并返回 LocalSandbox。如果将来加新
    实现，再在这里按 type 分发。
    """
    cfg = get_config()
    if cfg.sandbox.type and cfg.sandbox.type != "local":
        logger.warning(
            "sandbox_type_ignored",
            requested=cfg.sandbox.type,
            note="DockerSandbox 已移除；当前一律使用 LocalSandbox",
        )
    return LocalSandbox()

"""Sandbox 抽象基类与具体实现

- LocalSandbox: 基于临时目录的本地沙箱（当前使用）
- DockerSandbox: 基于 Docker 容器的隔离沙箱（骨架，未来实现）
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
    """沙箱抽象基类"""

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
        if self._sandbox_dir and self._sandbox_dir.exists():
            worktree_dir = self._sandbox_dir / "worktree"
            if worktree_dir.exists():
                for child in worktree_dir.iterdir():
                    if child.is_dir():
                        logger.info("removing_worktree", path=str(child))
                        repo_dir = resolve_repo(child.name)
                        if repo_dir is not None:
                            proc = await asyncio.create_subprocess_exec(
                                "git", "-C", str(repo_dir),
                                "worktree", "remove", "--force", str(child),
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
                        shutil.rmtree(child, ignore_errors=True)

            shutil.rmtree(self._sandbox_dir, ignore_errors=True)
            logger.info("sandbox_cleaned", ticket_id=self._ticket_id)

    def get_dir(self) -> Path | None:
        return self._sandbox_dir


class DockerSandbox(Sandbox):
    """基于 Docker 容器的隔离沙箱（骨架）

    未来实现要点：
    - 每个 session 启动一个临时 Docker 容器
    - 挂载 repos/ 为只读卷，sandbox 工作目录为读写卷
    - 容器内执行命令有完全的文件系统和网络隔离
    - session 结束时自动销毁容器
    - 通过 Docker SDK (docker-py) 管理容器生命周期

    配置项（config.yaml sandbox.docker_*）：
    - docker_image: 容器基础镜像（如 python:3.12-slim）
    - docker_memory_limit: 内存限制（如 512m）
    - docker_cpu_limit: CPU 限制（如 1.0）
    - docker_network_mode: 网络模式（如 none 禁止网络）
    """

    def __init__(self) -> None:
        self._sandbox_dir: Path | None = None
        self._ticket_id: int | None = None
        self._container_id: str | None = None

    async def create(self, ticket_id: int) -> Path:
        # TODO: 实现 Docker 容器创建
        # 1. 创建临时目录作为挂载点
        # 2. docker.from_env().containers.run(...)
        # 3. 保存 container_id
        raise NotImplementedError(
            "DockerSandbox 尚未实现，请在 config.yaml 中设置 sandbox.type: local"
        )

    async def cleanup(self) -> None:
        # TODO: 实现容器销毁和目录清理
        # 1. container.stop() + container.remove()
        # 2. 清理挂载目录
        if self._container_id:
            logger.info("docker_sandbox_cleanup", container=self._container_id)

    def get_dir(self) -> Path | None:
        return self._sandbox_dir


def create_sandbox() -> Sandbox:
    """根据配置创建对应类型的 Sandbox"""
    cfg = get_config()
    if cfg.sandbox.type == "docker":
        return DockerSandbox()
    return LocalSandbox()

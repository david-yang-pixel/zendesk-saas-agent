"""Durable registry for Worker tools that create their own process groups."""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
import signal
from typing import Iterator

from zd_agent.services.atomic_write import atomic_write_text

CHILD_PROCESS_GROUP_REGISTRY_ENV = "ZD_AGENT_CHILD_PROCESS_GROUP_REGISTRY"


class ChildProcessGroupRegistryError(RuntimeError):
    pass


class ChildProcessGroupRegistry:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(f"{self.path.name}.lock")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _read_unlocked(self) -> set[int]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return set()
        except (json.JSONDecodeError, OSError) as error:
            raise ChildProcessGroupRegistryError(type(error).__name__) from error
        if not isinstance(value, list):
            raise ChildProcessGroupRegistryError("invalid_top_level")
        groups: set[int] = set()
        for item in value:
            if isinstance(item, bool) or not isinstance(item, int) or item <= 1:
                raise ChildProcessGroupRegistryError("invalid_process_group")
            groups.add(item)
        return groups

    def _write_unlocked(self, groups: set[int]) -> None:
        atomic_write_text(self.path, json.dumps(sorted(groups)))
        self.path.chmod(0o600)

    def add(self, pgid: int) -> None:
        if pgid <= 1:
            return
        with self._locked():
            groups = self._read_unlocked()
            groups.add(int(pgid))
            self._write_unlocked(groups)

    def remove(self, pgid: int) -> None:
        with self._locked():
            groups = self._read_unlocked()
            groups.discard(int(pgid))
            self._write_unlocked(groups)

    def terminate_all(self) -> int:
        """Kill only explicitly registered, isolated tool process groups."""
        with self._locked():
            groups = self._read_unlocked()
            current_group = os.getpgrp()
            terminated = 0
            for pgid in groups:
                if pgid == current_group:
                    continue
                try:
                    os.killpg(pgid, signal.SIGKILL)
                    terminated += 1
                except ProcessLookupError:
                    continue
            self._write_unlocked(set())
            return terminated

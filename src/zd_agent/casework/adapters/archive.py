from __future__ import annotations

import os
from pathlib import Path

from zd_agent.casework.domain.shadow import ShadowArchiveRecord


class JsonlShadowArchive:
    """Append redacted shadow decisions using one durable O_APPEND write."""

    def __init__(self, archive_dir: Path) -> None:
        self._archive_dir = archive_dir

    def append(self, record: ShadowArchiveRecord) -> Path:
        self._archive_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self._archive_dir / "shadow-decisions.jsonl"
        payload = (record.model_dump_json() + "\n").encode("utf-8")
        descriptor = os.open(
            path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return path

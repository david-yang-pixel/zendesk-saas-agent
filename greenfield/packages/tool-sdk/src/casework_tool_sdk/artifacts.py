"""Sandbox-scoped storage for raw Tool outputs excluded from model context."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from pydantic import JsonValue

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    reference: str
    sha256: str
    size_bytes: int
    media_type: str = "application/json"


class ArtifactStore(Protocol):
    def write_json(self, value: JsonValue) -> ArtifactRecord: ...


class FileArtifactStore:
    """Content-addressed store rooted inside one ephemeral sandbox session."""

    def __init__(self, root: Path, *, session_id: str) -> None:
        if not _SAFE_SEGMENT.fullmatch(session_id):
            raise ValueError("invalid artifact session identifier")
        self._session_id = session_id
        self._root = root.resolve()
        self._directory = self._root / session_id
        self._directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def write_json(self, value: JsonValue) -> ArtifactRecord:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        filename = f"{digest}.json"
        target = self._directory / filename
        if not target.exists():
            temporary = self._directory / f".{filename}.tmp"
            temporary.write_bytes(payload)
            temporary.chmod(0o600)
            temporary.replace(target)
        return ArtifactRecord(
            reference=f"artifact://{self._session_id}/{filename}",
            sha256=digest,
            size_bytes=len(payload),
        )

    def read_json(self, reference: str) -> JsonValue:
        prefix = f"artifact://{self._session_id}/"
        if not reference.startswith("artifact://"):
            raise ValueError("invalid artifact reference")
        if not reference.startswith(prefix):
            raise ValueError("artifact reference belongs to another session")
        filename = reference.removeprefix(prefix)
        if not re.fullmatch(r"[0-9a-f]{64}\.json", filename):
            raise ValueError("invalid artifact reference")
        payload = (self._directory / filename).read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if filename != f"{digest}.json":
            raise ValueError("artifact digest mismatch")
        return cast(JsonValue, json.loads(payload))

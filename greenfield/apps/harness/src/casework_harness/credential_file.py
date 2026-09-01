"""Reload the dedicated credential bridge before every Sandbox launch."""

from __future__ import annotations

import json
from pathlib import Path


class CredentialFileError(RuntimeError):
    pass


class CredentialFileEnvironment:
    def __init__(self, path: Path) -> None:
        self._path = path

    def __call__(self) -> dict[str, str]:
        if not self._path.is_file():
            raise CredentialFileError("sandbox credential file is unavailable")
        values: dict[str, str] = {}
        for raw_line in self._path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            value = value.strip()
            if not name.startswith("CASEWORK_CREDENTIAL_"):
                continue
            if len(value) >= 2 and value[0] == value[-1] == '"':
                try:
                    decoded = json.loads(value)
                except json.JSONDecodeError as error:
                    raise CredentialFileError(
                        "sandbox credential file is invalid"
                    ) from error
                if not isinstance(decoded, str):
                    raise CredentialFileError("sandbox credential file is invalid")
                value = decoded.replace("$$", "$")
            elif len(value) >= 2 and value[0] == value[-1] == "'":
                value = value[1:-1]
            if value and "\n" not in value and "\r" not in value:
                values[name] = value
        if not values:
            raise CredentialFileError("sandbox credential file has no credentials")
        return values


__all__ = ["CredentialFileEnvironment", "CredentialFileError"]

"""Admin-console authentication state."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zd_agent.services.atomic_write import atomic_write_text

_PBKDF2_ITERATIONS = 210_000
_SESSION_TTL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class PasswordCheck:
    ok: bool
    force_password_change: bool = False


def _hash_password(password: str, *, salt: bytes | None = None) -> dict[str, Any]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
    )
    return {
        "algorithm": "pbkdf2_sha256",
        "iterations": _PBKDF2_ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "hash": base64.b64encode(digest).decode("ascii"),
    }


def _verify_password(password: str, stored: dict[str, Any]) -> bool:
    try:
        salt = base64.b64decode(str(stored["salt"]))
        expected = base64.b64decode(str(stored["hash"]))
        iterations = int(stored.get("iterations", _PBKDF2_ITERATIONS))
    except Exception:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(digest, expected)


class AdminAuthStore:
    """File-backed admin account plus in-memory sessions."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._sessions: dict[str, dict[str, Any]] = {}
        self._ensure_file()

    def _ensure_file(self) -> None:
        if self.path.exists():
            return
        data = {
            "users": {
                "admin": {
                    "password": _hash_password("admin123"),
                    "force_password_change": True,
                    "created_at": int(time.time()),
                }
            }
        }
        self._write(data)

    def _read(self) -> dict[str, Any]:
        self._ensure_file()
        try:
            data = json.loads(self.path.read_text())
        except (json.JSONDecodeError, FileNotFoundError):
            data = {"users": {}}
        if not isinstance(data, dict):
            return {"users": {}}
        users = data.get("users")
        if not isinstance(users, dict):
            data["users"] = {}
        return data

    def _write(self, data: dict[str, Any]) -> None:
        atomic_write_text(self.path, json.dumps(data, indent=2, sort_keys=True))
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def verify_password(self, username: str, password: str) -> PasswordCheck:
        username = username.strip()
        data = self._read()
        user = data["users"].get(username)
        if not isinstance(user, dict):
            return PasswordCheck(ok=False)
        stored_password = user.get("password")
        if not isinstance(stored_password, dict):
            return PasswordCheck(ok=False)
        if not _verify_password(password, stored_password):
            return PasswordCheck(ok=False)
        return PasswordCheck(
            ok=True,
            force_password_change=bool(user.get("force_password_change")),
        )

    def requires_password_change(self, username: str) -> bool:
        username = username.strip()
        data = self._read()
        user = data["users"].get(username)
        if not isinstance(user, dict):
            return False
        return bool(user.get("force_password_change"))

    def change_password(
        self,
        *,
        username: str,
        current_password: str,
        new_password: str,
    ) -> bool:
        username = username.strip()
        if len(new_password) < 8:
            return False
        if not self.verify_password(username, current_password).ok:
            return False
        data = self._read()
        user = data["users"].get(username)
        if not isinstance(user, dict):
            return False
        user["password"] = _hash_password(new_password)
        user["force_password_change"] = False
        user["updated_at"] = int(time.time())
        self._write(data)
        return True

    def reset_password(self, *, username: str, new_password: str) -> bool:
        username = username.strip()
        if len(new_password) < 8:
            return False
        data = self._read()
        users = data.setdefault("users", {})
        users[username] = {
            "password": _hash_password(new_password),
            "force_password_change": True,
            "updated_at": int(time.time()),
        }
        self._write(data)
        return True

    def create_session(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        self._sessions[token] = {
            "username": username,
            "expires_at": time.time() + _SESSION_TTL_SECONDS,
        }
        return token

    def validate_session(self, token: str | None) -> str | None:
        if not token:
            return None
        session = self._sessions.get(token)
        if not session:
            return None
        if float(session.get("expires_at", 0)) < time.time():
            self._sessions.pop(token, None)
            return None
        username = session.get("username")
        return str(username) if username else None

    def revoke_session(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)

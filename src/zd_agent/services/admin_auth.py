"""Admin-console authentication state."""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zd_agent.services.admin_credentials import hash_password, verify_password
from zd_agent.services.admin_login_limiter import AdminLoginLimiter
from zd_agent.services.atomic_write import atomic_write_text

_SESSION_TTL_SECONDS = 24 * 60 * 60
_LOGIN_FAILURE_LIMIT = 5
_LOGIN_ACCOUNT_FAILURE_LIMIT = 20
_LOGIN_FAILURE_WINDOW_SECONDS = 5 * 60
_LOGIN_FAILURE_MAX_KEYS = 4096


@dataclass(frozen=True, slots=True)
class PasswordCheck:
    ok: bool
    force_password_change: bool = False


@dataclass(frozen=True, slots=True)
class LoginCheck:
    ok: bool
    force_password_change: bool = False
    rate_limited: bool = False
    retry_after_seconds: int = 0


class AdminAuthStore:
    """File-backed admin account plus in-memory sessions."""

    def __init__(
        self,
        path: Path,
        *,
        initial_password: str | None = None,
        login_failure_limit: int = _LOGIN_FAILURE_LIMIT,
        login_account_failure_limit: int = _LOGIN_ACCOUNT_FAILURE_LIMIT,
        login_failure_window_seconds: float = _LOGIN_FAILURE_WINDOW_SECONDS,
        login_failure_max_keys: int = _LOGIN_FAILURE_MAX_KEYS,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self.path = path
        self.bootstrap_password_path = path.with_name("admin_bootstrap_password.txt")
        self._initial_password = (initial_password or "").strip()
        self._legacy_migration_checked = False
        self._sessions: dict[str, dict[str, Any]] = {}
        self._login_limiter = AdminLoginLimiter(
            per_ip_limit=login_failure_limit,
            per_account_limit=login_account_failure_limit,
            window_seconds=login_failure_window_seconds,
            max_keys=login_failure_max_keys,
            now_fn=now_fn,
        )
        self._ensure_file()

    def _ensure_file(self) -> None:
        if self.path.exists():
            if not self._legacy_migration_checked:
                self._rotate_legacy_default_password()
                self._legacy_migration_checked = True
            return
        expose_bootstrap = (
            not self._initial_password or self._initial_password == "admin123"
        )
        bootstrap_password = (
            secrets.token_urlsafe(24) if expose_bootstrap else self._initial_password
        )
        data = {
            "users": {
                "admin": {
                    "password": hash_password(bootstrap_password),
                    "force_password_change": True,
                    "created_at": int(time.time()),
                }
            }
        }
        self._write(data)
        self._legacy_migration_checked = True
        if expose_bootstrap:
            atomic_write_text(self.bootstrap_password_path, bootstrap_password + "\n")
            self.bootstrap_password_path.chmod(0o600)

    def _rotate_legacy_default_password(self) -> None:
        try:
            data = json.loads(self.path.read_text())
            user = data["users"]["admin"]
            stored_password = user["password"]
        except (json.JSONDecodeError, FileNotFoundError, KeyError, TypeError):
            return
        if not user.get("force_password_change"):
            return
        if not isinstance(stored_password, dict):
            return
        if not verify_password("admin123", stored_password):
            return

        configured_password = self._initial_password
        expose_bootstrap = not configured_password or configured_password == "admin123"
        bootstrap_password = (
            secrets.token_urlsafe(24) if expose_bootstrap else configured_password
        )
        user["password"] = hash_password(bootstrap_password)
        user["force_password_change"] = True
        user["updated_at"] = int(time.time())
        self._write(data)
        if expose_bootstrap:
            atomic_write_text(self.bootstrap_password_path, bootstrap_password + "\n")
            self.bootstrap_password_path.chmod(0o600)

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
            return

    def verify_password(self, username: str, password: str) -> PasswordCheck:
        username = username.strip()
        data = self._read()
        user = data["users"].get(username)
        if not isinstance(user, dict):
            return PasswordCheck(ok=False)
        stored_password = user.get("password")
        if not isinstance(stored_password, dict):
            return PasswordCheck(ok=False)
        if not verify_password(password, stored_password):
            return PasswordCheck(ok=False)
        return PasswordCheck(
            ok=True,
            force_password_change=bool(user.get("force_password_change")),
        )

    def verify_login(
        self,
        *,
        username: str,
        password: str,
        remote_ip: str,
    ) -> LoginCheck:
        normalized_username = username.strip().casefold()
        normalized_ip = remote_ip.strip().casefold() or "unknown"
        retry_after = self._login_limiter.retry_after(
            username=normalized_username,
            remote_ip=normalized_ip,
        )
        if retry_after > 0:
            return LoginCheck(
                ok=False,
                rate_limited=True,
                retry_after_seconds=retry_after,
            )

        password_check = self.verify_password(username, password)
        if password_check.ok:
            self._login_limiter.clear_success(
                username=normalized_username,
                remote_ip=normalized_ip,
            )
            return LoginCheck(
                ok=True,
                force_password_change=password_check.force_password_change,
            )

        self._login_limiter.record_failure(
            username=normalized_username,
            remote_ip=normalized_ip,
        )
        return LoginCheck(ok=False)

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
        user["password"] = hash_password(new_password)
        user["force_password_change"] = False
        user["updated_at"] = int(time.time())
        self._write(data)
        self.revoke_user_sessions(username)
        self.bootstrap_password_path.unlink(missing_ok=True)
        return True

    def reset_password(self, *, username: str, new_password: str) -> bool:
        username = username.strip()
        if len(new_password) < 8:
            return False
        data = self._read()
        users = data.setdefault("users", {})
        users[username] = {
            "password": hash_password(new_password),
            "force_password_change": True,
            "updated_at": int(time.time()),
        }
        self._write(data)
        self.revoke_user_sessions(username)
        self.bootstrap_password_path.unlink(missing_ok=True)
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

    def revoke_user_sessions(self, username: str) -> None:
        tokens = [
            token
            for token, session in self._sessions.items()
            if session.get("username") == username
        ]
        for token in tokens:
            self._sessions.pop(token, None)

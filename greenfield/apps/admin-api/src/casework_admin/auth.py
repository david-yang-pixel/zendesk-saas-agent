"""Authentication boundary for the Greenfield management console."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Any, cast

SESSION_TTL_SECONDS = 24 * 60 * 60
PBKDF2_ITERATIONS = 210_000


@dataclass(frozen=True, slots=True)
class AdminSession:
    username: str
    csrf_token: str
    force_password_change: bool


@dataclass(frozen=True, slots=True)
class LoginResult:
    session_token: str | None = None
    session: AdminSession | None = None
    rate_limited: bool = False
    retry_after_seconds: int = 0


class LoginLimiter:
    """Bound memory while limiting both source addresses and account names."""

    def __init__(
        self,
        *,
        per_ip_limit: int = 5,
        per_account_limit: int = 20,
        window_seconds: float = 300,
        max_keys: int = 4096,
    ) -> None:
        self.per_ip_limit = per_ip_limit
        self.per_account_limit = per_account_limit
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self.failures: OrderedDict[tuple[str, str], list[float]] = OrderedDict()

    def retry_after(self, *, username: str, remote_ip: str) -> int:
        now = time.time()
        retry_at = 0.0
        ip_failures = self._active(("ip", remote_ip), now)
        account_failures = self._active(("account", username), now)
        if len(ip_failures) >= self.per_ip_limit:
            retry_at = max(retry_at, ip_failures[0] + self.window_seconds)
        if len(account_failures) >= self.per_account_limit:
            retry_at = max(retry_at, account_failures[0] + self.window_seconds)
        return max(0, ceil(retry_at - now))

    def record_failure(self, *, username: str, remote_ip: str) -> None:
        now = time.time()
        for key in (("ip", remote_ip), ("account", username)):
            failures = self._active(key, now)
            failures.append(now)
            self.failures[key] = failures
            self.failures.move_to_end(key)
        while len(self.failures) > self.max_keys:
            self.failures.popitem(last=False)

    def clear_success(self, *, username: str, remote_ip: str) -> None:
        self.failures.pop(("ip", remote_ip), None)
        self.failures.pop(("account", username), None)

    def _active(self, key: tuple[str, str], now: float) -> list[float]:
        cutoff = now - self.window_seconds
        failures = [value for value in self.failures.get(key, []) if value > cutoff]
        if failures:
            self.failures[key] = failures
            self.failures.move_to_end(key)
        else:
            self.failures.pop(key, None)
        return failures


def hash_password(password: str, *, salt: bytes | None = None) -> dict[str, Any]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return {
        "algorithm": "pbkdf2_sha256",
        "iterations": PBKDF2_ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "hash": base64.b64encode(digest).decode("ascii"),
    }


def verify_password(password: str, stored: dict[str, Any]) -> bool:
    try:
        if stored.get("algorithm") != "pbkdf2_sha256":
            return False
        iterations = int(stored.get("iterations", PBKDF2_ITERATIONS))
        if iterations < 100_000 or iterations > 2_000_000:
            return False
        salt = base64.b64decode(str(stored["salt"]), validate=True)
        expected = base64.b64decode(str(stored["hash"]), validate=True)
    except (KeyError, TypeError, ValueError, binascii.Error):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return hmac.compare_digest(digest, expected)


class AdminAuthStore:
    """Compatible password file plus process-local, revocable sessions."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.bootstrap_password_path = path.with_name("bootstrap-password.txt")
        self._sessions: dict[str, dict[str, object]] = {}
        self._limiter = LoginLimiter()
        self._lock = threading.RLock()
        self._ensure_file()

    def _ensure_file(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        if self.path.exists():
            return
        password = secrets.token_urlsafe(24)
        self._write(
            {
                "users": {
                    "admin": {
                        "password": hash_password(password),
                        "force_password_change": True,
                        "created_at": int(time.time()),
                    }
                }
            }
        )
        self._atomic_write(self.bootstrap_password_path, password + "\n")

    def _read(self) -> dict[str, Any]:
        try:
            raw: object = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {"users": {}}
        if not isinstance(raw, dict):
            return {"users": {}}
        value = cast(dict[str, Any], raw)
        if not isinstance(value.get("users"), dict):
            return {"users": {}}
        return value

    @staticmethod
    def _users(data: dict[str, Any]) -> dict[str, Any]:
        raw = data.get("users")
        if not isinstance(raw, dict):
            return {}
        return cast(dict[str, Any], raw)

    def _write(self, value: dict[str, Any]) -> None:
        self._atomic_write(self.path, json.dumps(value, indent=2, sort_keys=True))

    @staticmethod
    def _atomic_write(path: Path, value: str) -> None:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}-", suffix=".tmp", dir=path.parent
        )
        temporary_path = Path(temporary)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary_path.unlink(missing_ok=True)

    def login(self, *, username: str, password: str, remote_ip: str) -> LoginResult:
        normalized_username = username.strip().casefold()
        normalized_ip = remote_ip.strip().casefold() or "unknown"
        with self._lock:
            retry_after = self._limiter.retry_after(
                username=normalized_username, remote_ip=normalized_ip
            )
            if retry_after:
                return LoginResult(rate_limited=True, retry_after_seconds=retry_after)
            data = self._read()
            raw_user = self._users(data).get(username.strip())
            user = (
                cast(dict[str, Any], raw_user) if isinstance(raw_user, dict) else None
            )
            raw_stored = user.get("password") if user is not None else None
            stored = (
                cast(dict[str, Any], raw_stored)
                if isinstance(raw_stored, dict)
                else None
            )
            if user is None or stored is None or not verify_password(password, stored):
                self._limiter.record_failure(
                    username=normalized_username, remote_ip=normalized_ip
                )
                return LoginResult()
            self._limiter.clear_success(
                username=normalized_username, remote_ip=normalized_ip
            )
            token = secrets.token_urlsafe(32)
            csrf = secrets.token_urlsafe(32)
            force_change = bool(user.get("force_password_change"))
            self._sessions[token] = {
                "username": username.strip(),
                "csrf_token": csrf,
                "expires_at": time.time() + SESSION_TTL_SECONDS,
            }
            return LoginResult(
                session_token=token,
                session=AdminSession(username.strip(), csrf, force_change),
            )

    def validate_session(self, token: str | None) -> AdminSession | None:
        if not token:
            return None
        with self._lock:
            raw = self._sessions.get(token)
            if raw is None:
                return None
            expires_at = raw.get("expires_at")
            if not isinstance(expires_at, (int, float)) or expires_at <= time.time():
                self._sessions.pop(token, None)
                return None
            username_value = raw.get("username")
            csrf_value = raw.get("csrf_token")
            if not isinstance(username_value, str) or not isinstance(csrf_value, str):
                self._sessions.pop(token, None)
                return None
            username = username_value
            data = self._read()
            raw_user = self._users(data).get(username)
            if not isinstance(raw_user, dict):
                self._sessions.pop(token, None)
                return None
            user = cast(dict[str, Any], raw_user)
            return AdminSession(
                username=username,
                csrf_token=csrf_value,
                force_password_change=bool(user.get("force_password_change")),
            )

    def revoke_session(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._sessions.pop(token, None)

    def change_password(
        self, *, username: str, current_password: str, new_password: str
    ) -> bool:
        if len(new_password) < 12 or len(new_password) > 256:
            return False
        with self._lock:
            data = self._read()
            raw_user = self._users(data).get(username)
            user = (
                cast(dict[str, Any], raw_user) if isinstance(raw_user, dict) else None
            )
            raw_stored = user.get("password") if user is not None else None
            stored = (
                cast(dict[str, Any], raw_stored)
                if isinstance(raw_stored, dict)
                else None
            )
            if not isinstance(stored, dict) or not verify_password(
                current_password, stored
            ):
                return False
            assert user is not None
            user["password"] = hash_password(new_password)
            user["force_password_change"] = False
            user["updated_at"] = int(time.time())
            self._write(data)
            for token, raw in list(self._sessions.items()):
                if raw.get("username") == username:
                    self._sessions.pop(token, None)
            self.bootstrap_password_path.unlink(missing_ok=True)
            return True


def safe_remote_ip(headers: dict[str, str], fallback: str) -> str:
    """Nginx overwrites X-Real-IP; avoid accepting a comma-separated chain."""
    candidate = headers.get("x-real-ip", "").strip()
    if candidate and len(candidate) <= 64 and "," not in candidate:
        return candidate
    return fallback

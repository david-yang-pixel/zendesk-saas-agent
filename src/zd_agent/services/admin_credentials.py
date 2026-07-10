from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
from typing import Any

_PBKDF2_ITERATIONS = 210_000


def hash_password(password: str, *, salt: bytes | None = None) -> dict[str, Any]:
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


def verify_password(password: str, stored: dict[str, Any]) -> bool:
    try:
        salt = base64.b64decode(str(stored["salt"]))
        expected = base64.b64decode(str(stored["hash"]))
        iterations = int(stored.get("iterations", _PBKDF2_ITERATIONS))
    except (KeyError, TypeError, ValueError, binascii.Error):
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(digest, expected)

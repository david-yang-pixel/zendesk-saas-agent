"""PostgreSQL integration: read-only SQL validator + psql subprocess executor."""
from __future__ import annotations

import re

try:
    from zd_agent.log import get_logger
    logger = get_logger(module="integrations.postgres")
except Exception:  # pragma: no cover
    import logging as _logging
    logger = _logging.getLogger("integrations.postgres")  # type: ignore[assignment]


class PsqlSafetyError(ValueError):
    """Raised when SQL fails read-only safety validation."""


class TeleportAuthExpired(RuntimeError):
    """Raised when psql connection fails due to expired Teleport auth."""


# Forbidden keywords (case-insensitive, word-boundary match)
_FORBIDDEN_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "MERGE", "UPSERT",
    "DROP", "TRUNCATE", "ALTER", "CREATE",
    "GRANT", "REVOKE",
    "COPY",     # COPY FROM PROGRAM can exec shell
    "EXECUTE",  # prepared statements may hide writes
    "CALL",     # stored procedures
    "VACUUM", "ANALYZE", "REINDEX",
]


def validate_read_only_sql(sql: str) -> None:
    """Validate SQL is read-only. Raises PsqlSafetyError on violation.

    Triple-check:
    1. Strip comments (-- line and /* block */).
    2. Reject multiple statements (';' after trimming trailing).
    3. Keyword blacklist (word-boundary, case-insensitive).
    4. Require first significant word is SELECT or WITH.
    """
    # Strip line comments and block comments
    cleaned = re.sub(r"--[^\n]*", "", sql)
    cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)

    # Reject multiple statements
    stripped = cleaned.strip().rstrip(";").strip()
    if ";" in stripped:
        raise PsqlSafetyError("SQL contains multiple statements; only one SELECT allowed")

    # Keyword blacklist
    upper = cleaned.upper()
    for kw in _FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{kw}\b", upper):
            raise PsqlSafetyError(f"forbidden keyword detected: {kw}")

    # Must start with SELECT or WITH
    first_word = re.match(r"\s*(\w+)", cleaned)
    if not first_word:
        raise PsqlSafetyError("empty SQL")
    fw = first_word.group(1).upper()
    if fw not in ("SELECT", "WITH"):
        raise PsqlSafetyError(f"SQL must start with SELECT or WITH, got: {fw}")

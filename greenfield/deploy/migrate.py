"""Small compose migration gate used before the full Alembic history exists.

The command is intentionally idempotent.  Once the persistence package adds
its Alembic environment, this entrypoint can call that migration runner while
retaining the same database readiness and retry behavior.
"""

from __future__ import annotations

import asyncio
import os

import asyncpg


def _dsn() -> str:
    value = os.environ["DATABASE_URL"]
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _migrate() -> None:
    last_error: Exception | None = None
    for _ in range(30):
        try:
            connection = await asyncpg.connect(_dsn(), timeout=3)
            try:
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS casework_schema_migrations (
                        version text PRIMARY KEY,
                        applied_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                await connection.execute(
                    """
                    INSERT INTO casework_schema_migrations(version)
                    VALUES ('greenfield-bootstrap')
                    ON CONFLICT (version) DO NOTHING
                    """
                )
            finally:
                await connection.close()
            return
        except (OSError, asyncpg.PostgresError) as error:
            last_error = error
            await asyncio.sleep(1)
    raise RuntimeError("PostgreSQL did not become ready for migration") from last_error


if __name__ == "__main__":
    asyncio.run(_migrate())

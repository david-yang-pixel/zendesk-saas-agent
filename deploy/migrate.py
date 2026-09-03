"""Apply the real Alembic history for the deployment migration gate."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def migrate(config_path: Path | None = None) -> None:
    path = config_path or Path(__file__).resolve().parents[1] / "alembic.ini"
    configuration = Config(str(path))
    command.upgrade(configuration, "head")


if __name__ == "__main__":
    migrate()

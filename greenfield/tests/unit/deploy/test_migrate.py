from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from deploy import migrate


def test_migration_gate_runs_alembic_to_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "alembic.ini"
    config_path.write_text("[alembic]\nscript_location = migrations\n")
    calls: list[tuple[Config, str]] = []

    def _upgrade(config: Config, revision: str) -> None:
        calls.append((config, revision))

    monkeypatch.setattr(migrate.command, "upgrade", _upgrade)

    migrate.migrate(config_path)

    assert len(calls) == 1
    assert calls[0][1] == "head"
    assert calls[0][0].config_file_name == str(config_path)

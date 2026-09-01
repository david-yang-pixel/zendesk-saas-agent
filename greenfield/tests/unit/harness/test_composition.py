from __future__ import annotations

import pytest

from casework_harness.composition import create_app


def test_production_harness_factory_requires_database_and_sandbox_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SANDBOX_IMAGE", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        create_app()

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/casework")
    with pytest.raises(RuntimeError, match="SANDBOX_IMAGE"):
        create_app()

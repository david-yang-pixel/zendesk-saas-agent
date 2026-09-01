from __future__ import annotations

import pytest

from casework_ingress.composition import create_app


def test_production_ingress_factory_requires_postgres_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        create_app()

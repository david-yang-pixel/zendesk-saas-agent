from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute

import casework_ingress.composition as composition
from casework_ingress.composition import create_app


def test_production_ingress_factory_requires_postgres_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        create_app()


def test_production_composition_reads_zendesk_credentials_and_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "DATABASE_URL": "postgresql+asyncpg://db",
        "ZENDESK_BASE_URL": "https://tenant.zendesk.com",
        "ZENDESK_EMAIL": "support@example.com",
        "ZENDESK_API_TOKEN": "api-token",
        "ZENDESK_MESSAGING_WEBHOOK_SECRET": "webhook-secret",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    captured: dict[str, object] = {}

    class _Engine:
        async def dispose(self) -> None:
            pass

    class _Reader:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def close(self) -> None:
            pass

    def _engine_factory(_: str) -> _Engine:
        return _Engine()

    def _sessions_factory(_: _Engine) -> object:
        return object()

    def _transactions_factory(_: object) -> object:
        return object()

    def _readiness_factory(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace()

    monkeypatch.setattr(composition, "ZendeskApiTicketReader", _Reader)
    monkeypatch.setattr(composition, "create_engine", _engine_factory)
    monkeypatch.setattr(composition, "create_session_factory", _sessions_factory)
    monkeypatch.setattr(
        composition, "postgres_transaction_scope", _transactions_factory
    )
    monkeypatch.setattr(composition, "DatabaseReadiness", _readiness_factory)

    app = create_app()

    assert captured == {
        "base_url": "https://tenant.zendesk.com",
        "email": "support@example.com",
        "api_token": "api-token",
        "timeout_seconds": 10.0,
    }
    assert any(
        isinstance(route, APIRoute) and route.path == "/webhook/zendesk-messaging"
        for route in app.routes
    )


def test_production_ingress_requires_messaging_webhook_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://db")
    monkeypatch.setenv("ZENDESK_BASE_URL", "https://tenant.zendesk.com")
    monkeypatch.setenv("ZENDESK_EMAIL", "support@example.com")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "api-token")
    monkeypatch.delenv("ZENDESK_MESSAGING_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("ZENDESK_MESSAGING_SIGNING_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="ZENDESK_MESSAGING_WEBHOOK_SECRET"):
        create_app()

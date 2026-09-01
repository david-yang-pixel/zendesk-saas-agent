from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from casework_admin.config import (
    AdminConfig,
    ConfigConflict,
    ModelConfig,
    ProcessingRulesConfig,
)
from casework_admin.postgres_config import PostgresConfigRepository
from casework_persistence.models import ConfigRevisionRow
from casework_persistence.repositories import ConfigRevisionConflict


def _config(*, root_model: str = "gpt-root") -> AdminConfig:
    return AdminConfig(
        desired_state="running",
        root_model=ModelConfig(
            provider="openai",
            model=root_model,
            credential_ref="secret/root",
        ),
        rules=ProcessingRulesConfig(
            saas_tag="saas",
            saas_assignee="saas-support",
            reply_enabled=False,
        ),
    )


class _Gateway:
    def __init__(self) -> None:
        self.current_row = ConfigRevisionRow(
            revision="cfg-1",
            config=_config().model_dump(mode="json"),
            actor="bootstrap",
            created_at=datetime(2026, 9, 1, tzinfo=UTC),
        )

    async def current(self) -> ConfigRevisionRow:
        return self.current_row

    async def bootstrap(
        self,
        *,
        revision: str,
        config: dict[str, object],
        actor: str,
    ) -> ConfigRevisionRow:
        return self.current_row

    async def append(
        self,
        *,
        expected_revision: str,
        revision: str,
        config: dict[str, object],
        actor: str,
    ) -> ConfigRevisionRow:
        if expected_revision != self.current_row.revision:
            raise ConfigRevisionConflict("configuration revision is stale")
        self.current_row = ConfigRevisionRow(
            revision=revision,
            config=config,
            actor=actor,
            created_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
        return self.current_row


@asynccontextmanager
async def _gateway_scope(gateway: _Gateway) -> AsyncGenerator[_Gateway, None]:
    yield gateway


@pytest.mark.asyncio
async def test_postgres_admin_adapter_round_trips_typed_config() -> None:
    gateway = _Gateway()
    repository = PostgresConfigRepository(
        gateway_scope=lambda: _gateway_scope(gateway)
    )

    current = await repository.current()
    updated = await repository.append(
        expected_revision=current.revision,
        config=_config(root_model="gpt-root-v2"),
        actor="operator@example.com",
    )

    assert updated.config.root_model.model == "gpt-root-v2"
    assert updated.actor == "operator@example.com"
    assert updated.revision != current.revision


@pytest.mark.asyncio
async def test_postgres_admin_adapter_maps_stale_revision_to_domain_conflict() -> None:
    gateway = _Gateway()
    repository = PostgresConfigRepository(
        gateway_scope=lambda: _gateway_scope(gateway)
    )

    with pytest.raises(ConfigConflict):
        await repository.append(
            expected_revision="stale",
            config=_config(),
            actor="operator@example.com",
        )

"""PostgreSQL adapter for the management configuration port."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol, cast
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from casework_admin.config import AdminConfig, ConfigConflict, ConfigSnapshot
from casework_persistence.models import ConfigRevisionRow
from casework_persistence.repositories import (
    ConfigRevisionConflict,
    ConfigRevisionRepository,
)


class ConfigRevisionGateway(Protocol):
    async def current(self) -> ConfigRevisionRow: ...

    async def bootstrap(
        self,
        *,
        revision: str,
        config: dict[str, object],
        actor: str,
    ) -> ConfigRevisionRow: ...

    async def append(
        self,
        *,
        expected_revision: str,
        revision: str,
        config: dict[str, object],
        actor: str,
    ) -> ConfigRevisionRow: ...


GatewayScope = Callable[[], AbstractAsyncContextManager[ConfigRevisionGateway]]


def postgres_gateway_scope(
    sessions: async_sessionmaker[AsyncSession],
) -> GatewayScope:
    @asynccontextmanager
    async def scope() -> AsyncGenerator[ConfigRevisionGateway, None]:
        async with sessions() as session, session.begin():
            yield cast(ConfigRevisionGateway, ConfigRevisionRepository(session))

    return scope


class PostgresConfigRepository:
    """Own one short database transaction per configuration operation."""

    def __init__(self, *, gateway_scope: GatewayScope) -> None:
        self._gateway_scope = gateway_scope

    async def bootstrap(self, *, initial: AdminConfig) -> ConfigSnapshot:
        async with self._gateway_scope() as revisions:
            row = await revisions.bootstrap(
                revision="bootstrap-v1",
                config=initial.model_dump(mode="json"),
                actor="bootstrap",
            )
        return self._snapshot(row)

    async def current(self) -> ConfigSnapshot:
        async with self._gateway_scope() as revisions:
            row = await revisions.current()
        return self._snapshot(row)

    async def append(
        self, *, expected_revision: str, config: AdminConfig, actor: str
    ) -> ConfigSnapshot:
        try:
            async with self._gateway_scope() as revisions:
                row = await revisions.append(
                    expected_revision=expected_revision,
                    revision=str(uuid4()),
                    config=config.model_dump(mode="json"),
                    actor=actor,
                )
        except ConfigRevisionConflict as error:
            raise ConfigConflict(str(error)) from error
        return self._snapshot(row)

    @staticmethod
    def _snapshot(row: ConfigRevisionRow) -> ConfigSnapshot:
        return ConfigSnapshot(
            revision=row.revision,
            config=AdminConfig.model_validate(row.config),
            created_at=row.created_at,
            actor=row.actor,
        )


__all__ = ["PostgresConfigRepository", "postgres_gateway_scope"]

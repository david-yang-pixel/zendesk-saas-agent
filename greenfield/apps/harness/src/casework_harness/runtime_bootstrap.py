"""Load one immutable config revision into the secret-free sandbox contract."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contracts.runtime import (
    ChildModelBinding,
    ModelBinding,
    RuntimeBootstrap,
    registry_digest,
    runtime_model_digest,
)


class RuntimeBootstrapUnavailable(RuntimeError):
    """Raised when a job's immutable config revision cannot be loaded."""


class PostgresRuntimeBootstrapLoader:
    """Read config JSON in Harness and pass only a validated projection on."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def load(self, *, config_revision: str) -> RuntimeBootstrap:
        from casework_persistence.models import ConfigRevisionRow

        async with self._sessions() as session, session.begin():
            result = await session.execute(
                select(ConfigRevisionRow).where(
                    ConfigRevisionRow.revision == config_revision
                )
            )
            row = result.scalar_one_or_none()
        if row is None:
            raise RuntimeBootstrapUnavailable(
                f"config revision is unavailable: {config_revision}"
            )
        return runtime_bootstrap_from_config(revision=row.revision, config=row.config)


def runtime_bootstrap_from_config(
    *, revision: str, config: dict[str, object]
) -> RuntimeBootstrap:
    """Project admin config without carrying its mutable wrapper or secrets."""

    root = _mapping(config.get("root_model"), "root_model")
    raw_children_value = config.get("subagent_slots", ())
    if not isinstance(raw_children_value, (list, tuple)):
        raise RuntimeBootstrapUnavailable("subagent_slots must be an array")
    raw_children = cast(
        list[object] | tuple[object, ...], raw_children_value
    )
    children: list[ChildModelBinding] = []
    for raw in raw_children:
        child = _mapping(raw, "subagent_slots[]")
        if not bool(child.get("enabled", True)):
            continue
        children.append(
            ChildModelBinding(
                slot_id=_string(child, "slot_id"),
                provider=_string(child, "provider"),
                model=_string(child, "model"),
                credential_ref=_string(child, "credential_ref"),
                context_window_tokens=_integer(
                    child.get("context_window_tokens"),
                    32_000,
                    "context_window_tokens",
                ),
                max_tool_rounds=_integer(
                    child.get("max_tool_rounds"), 12, "max_tool_rounds"
                ),
                max_parallel=int(child.get("max_parallel", 1)),
            )
        )
    skills = _strings(config.get("enabled_skills", ()), "enabled_skills")
    tools = _strings(config.get("enabled_tools", ()), "enabled_tools")
    root_binding = ModelBinding(
        provider=_string(root, "provider"),
        model=_string(root, "model"),
        credential_ref=_string(root, "credential_ref"),
    )
    return RuntimeBootstrap(
        config_revision=revision,
        root_model=root_binding,
        child_models=tuple(children),
        enabled_skills=skills,
        enabled_tools=tools,
        model_digest=runtime_model_digest(root_binding, tuple(children)),
        skill_digest=registry_digest(skills),
        tool_digest=registry_digest(tools),
        context_window_tokens=_integer(
            config.get("context_window_tokens"), 128_000, "context_window_tokens"
        ),
        soft_compaction_percent=_integer(
            config.get("soft_compaction_percent"), 60, "soft_compaction_percent"
        ),
        hard_checkpoint_percent=_integer(
            config.get("hard_checkpoint_percent"), 80, "hard_checkpoint_percent"
        ),
        max_tool_rounds=_integer(
            config.get("max_tool_rounds"), 24, "max_tool_rounds"
        ),
    )


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeBootstrapUnavailable(f"{field} must be an object")
    return cast(dict[str, Any], value)


def _string(value: dict[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise RuntimeBootstrapUnavailable(f"{field} must be a non-empty string")
    return item


def _strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise RuntimeBootstrapUnavailable(f"{field} must be an array")
    values = cast(list[object] | tuple[object, ...], value)
    result = tuple(item for item in values if isinstance(item, str) and item.strip())
    if len(result) != len(values):
        raise RuntimeBootstrapUnavailable(f"{field} contains an invalid value")
    return result


def _integer(value: object, default: int, field: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeBootstrapUnavailable(f"{field} must be an integer")
    return value


__all__ = [
    "PostgresRuntimeBootstrapLoader",
    "RuntimeBootstrapUnavailable",
    "runtime_bootstrap_from_config",
]

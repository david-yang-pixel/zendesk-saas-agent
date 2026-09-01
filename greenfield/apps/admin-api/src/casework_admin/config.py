"""Versioned editable allowlist exposed by the management API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ModelConfig(_ConfigModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    credential_ref: str = Field(min_length=1)

    @field_validator("credential_ref")
    @classmethod
    def _credential_reference_only(cls, value: str) -> str:
        """Keep the API contract reference-only; secret values never enter config."""
        normalized = value.strip().casefold()
        secret_prefixes = ("sk-", "sk_", "api_key=", "bearer ", "ghp_")
        if normalized.startswith(secret_prefixes):
            raise ValueError(
                "credential_ref must reference a secret, not contain secret material"
            )
        if "\n" in value or "\r" in value:
            raise ValueError("credential_ref must be a single-line reference")
        return value


class SubagentSlotConfig(ModelConfig):
    slot_id: str = Field(min_length=1)
    enabled: bool = True
    max_parallel: int = Field(default=1, ge=1, le=8)


class ProcessingRulesConfig(_ConfigModel):
    saas_tag: str = Field(min_length=1)
    saas_assignee: str = Field(min_length=1)
    statuses: tuple[Literal["new", "open"], ...] = ("new", "open")
    human_aliases: tuple[str, ...] = ("human",)
    reply_enabled: bool = False
    default_locale: str = Field(default="en", min_length=2)
    footers: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _default_footer_exists(self) -> ProcessingRulesConfig:
        if self.reply_enabled and not self.footers.get(self.default_locale):
            raise ValueError("reply-enabled config requires a default locale footer")
        return self


class AdminConfig(_ConfigModel):
    desired_state: Literal["running", "paused"] = "paused"
    root_model: ModelConfig
    subagent_slots: tuple[SubagentSlotConfig, ...] = ()
    rules: ProcessingRulesConfig
    enabled_skills: tuple[str, ...] = ()
    enabled_tools: tuple[str, ...] = ()
    context_window_tokens: int = Field(default=128_000, ge=16_000, le=1_000_000)
    soft_compaction_percent: int = Field(default=60, ge=20, le=90)
    hard_checkpoint_percent: int = Field(default=80, ge=40, le=95)
    max_tool_rounds: int = Field(default=24, ge=1, le=100)

    @model_validator(mode="after")
    def _independent_subagent_slots(self) -> AdminConfig:
        slot_ids = tuple(slot.slot_id for slot in self.subagent_slots)
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("subagent slot ids must be unique")
        return self


class ConfigSnapshot(_ConfigModel):
    revision: str = Field(min_length=1)
    config: AdminConfig
    created_at: datetime
    actor: str = Field(min_length=1)


class ConfigConflict(RuntimeError):
    """Raised when an operator saves against a stale configuration revision."""


class ConfigRepository(Protocol):
    async def current(self) -> ConfigSnapshot: ...

    async def append(
        self, *, expected_revision: str, config: AdminConfig, actor: str
    ) -> ConfigSnapshot: ...


class InMemoryConfigRepository:
    """Test/local adapter preserving the same append-only revision semantics."""

    def __init__(self, *, initial: AdminConfig) -> None:
        self._history = [self._snapshot(config=initial, actor="bootstrap")]

    async def current(self) -> ConfigSnapshot:
        return self._history[-1]

    async def append(
        self, *, expected_revision: str, config: AdminConfig, actor: str
    ) -> ConfigSnapshot:
        if self._history[-1].revision != expected_revision:
            raise ConfigConflict("configuration revision is stale")
        snapshot = self._snapshot(config=config, actor=actor)
        self._history.append(snapshot)
        return snapshot

    async def history(self) -> tuple[ConfigSnapshot, ...]:
        return tuple(self._history)

    @staticmethod
    def _snapshot(*, config: AdminConfig, actor: str) -> ConfigSnapshot:
        return ConfigSnapshot(
            revision=str(uuid4()),
            config=config,
            created_at=datetime.now(UTC),
            actor=actor,
        )


class AdminConfigService:
    def __init__(self, *, repository: ConfigRepository) -> None:
        self._repository = repository

    async def current(self) -> ConfigSnapshot:
        return await self._repository.current()

    async def save(
        self, *, expected_revision: str, config: AdminConfig, actor: str
    ) -> ConfigSnapshot:
        return await self._repository.append(
            expected_revision=expected_revision,
            config=config,
            actor=actor,
        )

    @staticmethod
    def fixed_sandbox_policy() -> dict[str, object]:
        return {
            "attempt_1": {
                "cpu": 0.5,
                "memory_mib": 1024,
                "timeout_seconds": 1800,
            },
            "attempt_2": {
                "cpu": 1.0,
                "memory_mib": 1024,
                "timeout_seconds": 1800,
            },
            "retry_condition": "resource_exhausted_only",
        }

"""Versioned, secret-free runtime configuration passed to a sandbox."""

from __future__ import annotations

import hashlib
import json
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, field_validator, model_validator

from contracts._base import ContractModel, unique_strings


def _credential_reference(value: str) -> str:
    """Accept a reference, never an inline provider credential."""

    normalized = value.strip().casefold()
    if "\n" in value or "\r" in value:
        raise ValueError("credential_ref must be a single-line reference")
    if normalized.startswith(("sk-", "sk_", "api_key=", "bearer ", "ghp_")):
        raise ValueError(
            "credential_ref must reference a secret, not contain secret material"
        )
    return value


class ModelBinding(ContractModel):
    """Provider/model selection with a handle resolved outside the sandbox."""

    provider: str = Field(min_length=1)
    base_url: str = Field(default="https://api.openai.com", min_length=1)
    model: str = Field(min_length=1)
    credential_ref: str = Field(min_length=1, max_length=255)

    _validate_credential_ref = field_validator("credential_ref")(_credential_reference)

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("model base_url must be a safe HTTPS origin or path")
        return value.rstrip("/")


class ChildModelBinding(ModelBinding):
    """An enabled depth-one child slot with an independent model binding."""

    slot_id: str = Field(min_length=1)
    enabled: bool = True
    context_window_tokens: int = Field(default=32_000, gt=0, le=1_000_000)
    max_tool_rounds: int = Field(default=12, gt=0, le=100)
    max_parallel: int = Field(default=1, ge=1, le=8)


class RuntimeBootstrap(ContractModel):
    """One immutable, allowlisted runtime snapshot for one sandbox attempt.

    The Harness may pass this value once in ``SandboxStartRequest``.  It only
    contains model/skill/tool identifiers and credential references; secret
    material is resolved by an adapter inside the process and is never part of
    this contract or persisted job payload.
    """

    version: Literal[1] = 1
    config_revision: str = Field(min_length=1)
    root_model: ModelBinding
    child_models: tuple[ChildModelBinding, ...] = Field(
        default=(), validation_alias=AliasChoices("child_models", "child_slots")
    )
    enabled_skills: tuple[str, ...] = ()
    enabled_tools: tuple[str, ...] = ()
    model_digest: str = Field(min_length=1)
    skill_digest: str = Field(min_length=1)
    tool_digest: str = Field(min_length=1)
    context_window_tokens: int = Field(default=128_000, gt=0, le=1_000_000)
    soft_compaction_percent: int = Field(default=60, ge=20, le=90)
    hard_checkpoint_percent: int = Field(default=80, ge=40, le=95)
    max_tool_rounds: int = Field(default=24, gt=0, le=100)

    @field_validator("enabled_skills", "enabled_tools")
    @classmethod
    def _unique_allowlist(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unique_strings(value, "runtime allowlist")
        if any(not item.strip() for item in value):
            raise ValueError("runtime allowlist entries must not be blank")
        return value

    @model_validator(mode="after")
    def _validate_children(self) -> RuntimeBootstrap:
        child_ids = tuple(child.slot_id for child in self.child_models)
        unique_strings(child_ids, "child model slots")
        if any(not child.enabled for child in self.child_models):
            raise ValueError("RuntimeBootstrap may contain enabled child slots only")
        if self.hard_checkpoint_percent <= self.soft_compaction_percent:
            raise ValueError(
                "hard_checkpoint_percent must exceed soft_compaction_percent"
            )
        return self

    @property
    def enabled_child_slots(self) -> tuple[ChildModelBinding, ...]:
        return self.child_models


def runtime_model_digest(
    root: ModelBinding, children: tuple[ChildModelBinding, ...]
) -> str:
    """Return the canonical cross-service digest for explicit model bindings."""

    return registry_digest(
        {
            "root": root.model_dump(mode="json"),
            "children": [child.model_dump(mode="json") for child in children],
        }
    )


def registry_digest(value: object) -> str:
    """Hash one JSON-compatible immutable registry projection."""

    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


__all__ = [
    "ChildModelBinding",
    "ModelBinding",
    "RuntimeBootstrap",
    "registry_digest",
    "runtime_model_digest",
]

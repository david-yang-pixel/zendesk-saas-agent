"""Runtime configuration resolution kept separate from durable contracts."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from contracts.runtime import ChildModelBinding, ModelBinding, RuntimeBootstrap


class RuntimeConfigError(ValueError):
    """Raised when an immutable runtime snapshot cannot be safely resolved."""


class SecretResolver(Protocol):
    """Resolve one allowlisted reference without exposing a secret in config."""

    def resolve(self, credential_ref: str) -> str | None: ...


class MappingSecretResolver:
    """Small adapter for tests or a process-local secret manager bridge."""

    def __init__(self, secrets: Mapping[str, str]) -> None:
        self._secrets = dict(secrets)

    def resolve(self, credential_ref: str) -> str | None:
        return self._secrets.get(credential_ref)


class EnvironmentSecretResolver:
    """Resolve only references represented by a prefixed environment variable.

    The environment is read at execution time and is never copied into the
    bootstrap contract.  References are normalized to a conservative key so a
    caller cannot select arbitrary environment names.
    """

    def __init__(
        self,
        environ: Mapping[str, str] | None = None,
        *,
        prefix: str = "CASEWORK_CREDENTIAL_",
    ) -> None:
        if not prefix or not prefix.endswith("_") or not prefix[:-1].isidentifier():
            raise ValueError("credential environment prefix must be safe")
        self._environ = os.environ if environ is None else environ
        self._prefix = prefix

    def resolve(self, credential_ref: str) -> str | None:
        key = self._prefix + _environment_key(credential_ref)
        return self._environ.get(key)


@dataclass(frozen=True, slots=True)
class ResolvedModelBinding:
    """In-memory model connection; deliberately not a contracts model."""

    provider: str
    model: str
    api_key: str


@dataclass(frozen=True, slots=True)
class ResolvedRuntimeModels:
    root: ResolvedModelBinding
    children: tuple[tuple[ChildModelBinding, ResolvedModelBinding], ...]


def resolve_model_credentials(
    binding: ModelBinding,
    *,
    resolver: SecretResolver,
) -> ResolvedModelBinding:
    secret = resolver.resolve(binding.credential_ref)
    if secret is None or not secret.strip():
        raise RuntimeConfigError("model credential unavailable")
    return ResolvedModelBinding(
        provider=binding.provider,
        model=binding.model,
        api_key=secret,
    )


def resolve_runtime_models(
    bootstrap: RuntimeBootstrap,
    *,
    resolver: SecretResolver,
) -> ResolvedRuntimeModels:
    """Resolve root and every child independently; no root fallback exists."""

    root = resolve_model_credentials(bootstrap.root_model, resolver=resolver)
    children = tuple(
        (
            child,
            resolve_model_credentials(child, resolver=resolver),
        )
        for child in bootstrap.enabled_child_slots
    )
    return ResolvedRuntimeModels(root=root, children=children)


_SAFE_REFERENCE = re.compile(r"[^A-Za-z0-9]+")


def _environment_key(credential_ref: str) -> str:
    normalized = _SAFE_REFERENCE.sub("_", credential_ref).strip("_").upper()
    if not normalized:
        raise RuntimeConfigError(
            "credential_ref cannot map to an empty environment key"
        )
    return normalized


__all__ = [
    "EnvironmentSecretResolver",
    "MappingSecretResolver",
    "ResolvedModelBinding",
    "ResolvedRuntimeModels",
    "RuntimeConfigError",
    "SecretResolver",
    "resolve_model_credentials",
    "resolve_runtime_models",
]

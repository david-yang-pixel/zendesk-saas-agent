"""Shared validation helpers for cross-process contracts."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, cast

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """Base class for immutable, forward-compatible JSON contracts."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class InvalidTransitionError(ValueError):
    """Raised when a state machine receives an unsupported transition."""


def validate_transition[E: StrEnum](
    current: E,
    target: E,
    transitions: Mapping[E, Collection[E]],
    entity: str,
) -> None:
    """Validate one edge in a finite-state machine.

    Terminal states are represented by an absent or empty outgoing edge set,
    so attempting to move out of one gets a useful terminal-state error.
    """

    if current in transitions and target in transitions[current]:
        return
    if not transitions.get(current):
        raise InvalidTransitionError(
            f"{entity} is terminal at {current.value}; cannot transition to "
            f"{target.value}"
        )
    raise InvalidTransitionError(
        f"invalid {entity} transition: {current.value} -> {target.value}"
    )


def utc_now() -> datetime:
    """Return an aware UTC timestamp suitable for a contract default."""

    return datetime.now(UTC)


def require_aware(value: datetime, field_name: str) -> datetime:
    """Reject naive timestamps at process boundaries."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


def unique_strings(values: Collection[str], field_name: str) -> None:
    """Reject duplicate identifiers while preserving caller order."""

    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must not contain duplicate values")


def json_object(value: Any) -> dict[str, Any]:
    """Narrow a generic mapping to the object shape used by tool arguments."""

    if not isinstance(value, dict):
        raise ValueError("tool arguments must be a JSON object")
    return cast(dict[str, Any], value)

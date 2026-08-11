"""Model-aware OpenAI reasoning-effort capabilities and validation."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class _ReasoningCapability:
    model_prefix: str
    model_pattern: str
    efforts: tuple[str, ...]
    responses_only: bool = False


_CAPABILITIES: tuple[_ReasoningCapability, ...] = (
    _ReasoningCapability(
        "gpt-5.6",
        r"^gpt-5\.6(?:-|$)",
        ("none", "low", "medium", "high", "xhigh", "max"),
    ),
    _ReasoningCapability(
        "gpt-5.3-codex",
        r"^gpt-5\.3-codex(?:-|$)",
        ("low", "medium", "high", "xhigh"),
        responses_only=True,
    ),
    _ReasoningCapability(
        "gpt-5.5-pro",
        r"^gpt-5\.5-pro(?:-|$)",
        ("medium", "high", "xhigh"),
        responses_only=True,
    ),
    _ReasoningCapability(
        "gpt-5.5",
        r"^gpt-5\.5(?:-|$)",
        ("none", "low", "medium", "high", "xhigh"),
    ),
    _ReasoningCapability(
        "gpt-5.4-pro",
        r"^gpt-5\.4-pro(?:-|$)",
        ("medium", "high", "xhigh"),
        responses_only=True,
    ),
    _ReasoningCapability(
        "gpt-5.4",
        r"^gpt-5\.4(?:-|$)",
        ("none", "low", "medium", "high", "xhigh"),
    ),
    _ReasoningCapability(
        "gpt-5.2-codex",
        r"^gpt-5\.2-codex(?:-|$)",
        ("low", "medium", "high", "xhigh"),
        responses_only=True,
    ),
    _ReasoningCapability(
        "gpt-5.2-pro",
        r"^gpt-5\.2-pro(?:-|$)",
        ("medium", "high", "xhigh"),
        responses_only=True,
    ),
    _ReasoningCapability(
        "gpt-5.2",
        r"^gpt-5\.2(?:-|$)",
        ("none", "low", "medium", "high", "xhigh"),
    ),
    _ReasoningCapability(
        "gpt-5.1-codex",
        r"^gpt-5\.1-codex(?:-|$)",
        (),
        responses_only=True,
    ),
    _ReasoningCapability(
        "gpt-5.1",
        r"^gpt-5\.1(?:-|$)",
        ("none", "low", "medium", "high"),
    ),
    _ReasoningCapability(
        "gpt-5-codex",
        r"^gpt-5-codex(?:-|$)",
        (),
        responses_only=True,
    ),
    _ReasoningCapability(
        "gpt-5-pro",
        r"^gpt-5-pro(?:-|$)",
        ("high",),
        responses_only=True,
    ),
    _ReasoningCapability(
        "gpt-5",
        r"^gpt-5(?:$|-\d{4}-\d{2}-\d{2}$|-(?:mini|nano)(?:-|$))",
        ("minimal", "low", "medium", "high"),
    ),
)

_REASONING_COMPLETION_TOKEN_LIMITS = {
    "minimal": 512,
    "low": 1024,
    "medium": 4096,
    "high": 8192,
    "xhigh": 16384,
    "max": 25000,
}

_REASONING_TIMEOUT_SECONDS = {
    "minimal": 15.0,
    "low": 30.0,
    "medium": 60.0,
    "high": 120.0,
    "xhigh": 180.0,
    "max": 300.0,
}


def normalized_model_id(model: str) -> str:
    """Normalize optional provider-prefixed model identifiers."""
    return (model or "").strip().lower().rsplit("/", 1)[-1]


def supported_reasoning_efforts(model: str) -> tuple[str, ...]:
    """Return the official configurable reasoning levels for a model ID."""
    normalized = normalized_model_id(model)
    for capability in _CAPABILITIES:
        if re.match(capability.model_pattern, normalized):
            return capability.efforts
    return ()


def model_requires_responses_api(model: str) -> bool:
    """Whether the model is documented as Responses-only."""
    normalized = normalized_model_id(model)
    for capability in _CAPABILITIES:
        if re.match(capability.model_pattern, normalized):
            return capability.responses_only
    return False


def validate_trigger_model(model: str) -> str:
    """Reject Responses-only models on helper paths that use Chat Completions."""
    normalized = (model or "").strip()
    if model_requires_responses_api(normalized):
        raise ValueError(
            f"trigger decider does not support Responses-only model {normalized!r}"
        )
    return normalized


def validate_reasoning_effort(model: str, effort: str | None) -> str:
    """Normalize and validate one configured effort against its model."""
    normalized_effort = (effort or "").strip().lower()
    if not normalized_effort:
        return ""
    if normalized_effort not in supported_reasoning_efforts(model):
        raise ValueError(
            f"model {model!r} does not support reasoning effort {normalized_effort!r}"
        )
    return normalized_effort


def reasoning_completion_token_limit(default: int, effort: str | None) -> int:
    """Keep hidden reasoning from exhausting small structured-output budgets."""
    normalized_effort = (effort or "").strip().lower()
    minimum = _REASONING_COMPLETION_TOKEN_LIMITS.get(normalized_effort, default)
    return max(default, minimum)


def reasoning_timeout_seconds(default: float, effort: str | None) -> float:
    """Allow higher-effort reasoning calls enough wall-clock time to finish."""
    normalized_effort = (effort or "").strip().lower()
    minimum = _REASONING_TIMEOUT_SECONDS.get(normalized_effort, default)
    return max(default, minimum)


def reasoning_capabilities_manifest() -> list[dict[str, object]]:
    """Return serializable rules consumed by the admin console."""
    return [
        {
            "model_prefix": capability.model_prefix,
            "model_pattern": capability.model_pattern,
            "efforts": list(capability.efforts),
            "responses_only": capability.responses_only,
        }
        for capability in _CAPABILITIES
    ]

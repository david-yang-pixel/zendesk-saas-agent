from __future__ import annotations

import pytest

from zd_agent.runtime.openai_reasoning import (
    model_requires_responses_api,
    normalized_model_id,
    reasoning_completion_token_limit,
    reasoning_timeout_seconds,
    reasoning_capabilities_manifest,
    supported_reasoning_efforts,
    validate_trigger_model,
    validate_reasoning_effort,
)


def test_normalized_model_id_supports_provider_namespaces() -> None:
    assert normalized_model_id("openai/gpt-5.6-terra") == "gpt-5.6-terra"


@pytest.mark.parametrize(
    ("effort", "expected"),
    [
        ("", 60),
        ("none", 60),
        ("minimal", 512),
        ("low", 1024),
        ("medium", 4096),
        ("high", 8192),
        ("xhigh", 16384),
        ("max", 25000),
        ("ultra", 32768),
    ],
)
def test_reasoning_completion_token_limit_scales_with_effort(
    effort: str,
    expected: int,
) -> None:
    assert reasoning_completion_token_limit(60, effort) == expected


@pytest.mark.parametrize(
    ("effort", "expected"),
    [
        ("", 8.0),
        ("none", 8.0),
        ("minimal", 15.0),
        ("low", 30.0),
        ("medium", 60.0),
        ("high", 120.0),
        ("xhigh", 180.0),
        ("max", 300.0),
        ("ultra", 600.0),
    ],
)
def test_reasoning_timeout_scales_with_effort(
    effort: str,
    expected: float,
) -> None:
    assert reasoning_timeout_seconds(8.0, effort) == expected


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (
            "gpt-5.6-terra",
            ("none", "low", "medium", "high", "xhigh", "max", "ultra"),
        ),
        (
            " GPT-5.6-SOL ",
            ("none", "low", "medium", "high", "xhigh", "max", "ultra"),
        ),
        (
            "gpt-5.6-luna",
            ("none", "low", "medium", "high", "xhigh", "max"),
        ),
        (
            "gpt-5.5-2026-04-23",
            ("none", "low", "medium", "high", "xhigh"),
        ),
        ("gpt-5.5-pro", ("medium", "high", "xhigh")),
        ("gpt-5.4-pro-2026-03-05", ("medium", "high", "xhigh")),
        ("gpt-5.3-codex", ("low", "medium", "high", "xhigh")),
        ("gpt-5.2-codex", ("low", "medium", "high", "xhigh")),
        ("gpt-5.1-codex", ()),
        ("gpt-5-codex", ()),
        ("gpt-5.2-pro", ("medium", "high", "xhigh")),
        ("gpt-5-pro", ("high",)),
        (
            "gpt-5.4-mini",
            ("none", "low", "medium", "high", "xhigh"),
        ),
        ("gpt-5.1", ("none", "low", "medium", "high")),
        ("gpt-5-2025-08-07", ("minimal", "low", "medium", "high")),
        ("gpt-4.1", ()),
        ("custom-provider-model", ()),
    ],
)
def test_supported_reasoning_efforts_are_model_specific(
    model: str,
    expected: tuple[str, ...],
) -> None:
    assert supported_reasoning_efforts(model) == expected


def test_validate_reasoning_effort_accepts_default_and_supported_value() -> None:
    assert validate_reasoning_effort("gpt-5.6-terra", "") == ""
    assert validate_reasoning_effort("gpt-5.6-terra", "max") == "max"
    assert validate_reasoning_effort("gpt-5.6-terra", "ultra") == "ultra"


def test_validate_reasoning_effort_rejects_ultra_for_luna() -> None:
    with pytest.raises(ValueError, match="does not support reasoning effort 'ultra'"):
        validate_reasoning_effort("gpt-5.6-luna", "ultra")


def test_validate_reasoning_effort_rejects_model_mismatch() -> None:
    with pytest.raises(ValueError, match="does not support reasoning effort 'max'"):
        validate_reasoning_effort("gpt-5.4-mini", "max")


def test_pro_models_require_responses_and_are_rejected_for_trigger_helpers() -> None:
    assert model_requires_responses_api("gpt-5.5-pro") is True
    assert model_requires_responses_api("gpt-5.4") is False
    with pytest.raises(ValueError, match="Responses-only"):
        validate_trigger_model("gpt-5.5-pro")


@pytest.mark.parametrize(
    "model",
    [
        "gpt-5.3-codex",
        "gpt-5.2-codex",
        "gpt-5.1-codex",
        "gpt-5.1-codex-max",
        "gpt-5-codex",
    ],
)
def test_codex_models_require_responses_api(model: str) -> None:
    assert model_requires_responses_api(model) is True
    with pytest.raises(ValueError, match="Responses-only"):
        validate_trigger_model(model)


def test_reasoning_capabilities_manifest_exposes_backend_rules() -> None:
    manifest = reasoning_capabilities_manifest()

    assert manifest[0]["model_prefix"] == "gpt-5.6-sol"
    assert manifest[0]["efforts"] == [
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra",
    ]
    assert manifest[0]["model_pattern"]
    assert manifest[-1]["model_prefix"] == "gpt-5"
    assert manifest[-1]["efforts"] == ["minimal", "low", "medium", "high"]

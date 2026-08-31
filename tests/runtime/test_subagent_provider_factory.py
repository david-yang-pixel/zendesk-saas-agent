from __future__ import annotations

from zd_agent.runtime.config_snapshot import LLMRuntimeSnapshot
from zd_agent.runtime.llm.factory import create_llm_provider_from_snapshot


def test_subagent_provider_factory_uses_only_snapshot_credential(monkeypatch) -> None:
    captured = {}

    def fake_provider(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(
        "zd_agent.runtime.llm.openai_provider.OpenAIProvider", fake_provider
    )
    snapshot = LLMRuntimeSnapshot(
        provider="openai",
        model="child-model",
        reasoning_effort="low",
        enable_vision_input=False,
        temperature=0.0,
        base_url="https://child.example.test/v1",
        timeout=45,
        max_tokens=2048,
        context_window_tokens=64_000,
        api_key="child-secret",
    )

    create_llm_provider_from_snapshot(snapshot)

    assert captured["api_key"] == "child-secret"
    assert captured["model"] == "child-model"
    assert captured["base_url"] == "https://child.example.test/v1"

"""Immutable per-root-session runtime configuration snapshots."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from zd_agent.config import AppConfig, EnvSettings, LLMConfig, SubagentLLMConfig
from zd_agent.tools.base import ToolRegistry


@dataclass(frozen=True)
class LLMRuntimeSnapshot:
    provider: str
    model: str
    reasoning_effort: str
    enable_vision_input: bool
    temperature: float
    base_url: str | None
    timeout: int
    max_tokens: int
    context_window_tokens: int
    api_key: str = field(repr=False)


@dataclass(frozen=True)
class SubagentSlotRuntimeSnapshot:
    id: str
    label: str
    llm: LLMRuntimeSnapshot


@dataclass(frozen=True)
class CaseworkRuntimeSnapshot:
    main_llm: LLMRuntimeSnapshot
    subagents_enabled: bool
    subagent_slots: tuple[SubagentSlotRuntimeSnapshot, ...]
    subagent_max_parallel: int
    subagent_max_tasks_per_run: int
    subagent_task_timeout_seconds: int
    tool_names: tuple[str, ...]
    config_revision: str
    config_digest: str


def _main_api_key(cfg: LLMConfig, env: EnvSettings) -> str:
    if cfg.provider == "openai":
        return env.openai_api_key
    if cfg.provider == "anthropic":
        return env.anthropic_api_key
    return ""


def _llm_snapshot(cfg: LLMConfig, *, api_key: str) -> LLMRuntimeSnapshot:
    return LLMRuntimeSnapshot(
        provider=cfg.provider,
        model=cfg.model,
        reasoning_effort=cfg.reasoning_effort,
        enable_vision_input=cfg.enable_vision_input,
        temperature=cfg.temperature,
        base_url=cfg.base_url,
        timeout=cfg.timeout,
        max_tokens=cfg.max_tokens,
        context_window_tokens=cfg.context_window_tokens,
        api_key=api_key,
    )


def _child_llm_snapshot(cfg: SubagentLLMConfig) -> LLMRuntimeSnapshot:
    # Deliberately use only the slot's own credential and model fields. Child
    # sessions must never inherit the main provider, model, or environment key.
    return _llm_snapshot(cfg, api_key=cfg.api_key)


def _digest_payload(
    *,
    main: LLMRuntimeSnapshot,
    slots: tuple[SubagentSlotRuntimeSnapshot, ...],
    tool_names: tuple[str, ...],
    revision: str,
    subagents_enabled: bool,
    subagent_max_parallel: int,
    subagent_max_tasks_per_run: int,
    subagent_task_timeout_seconds: int,
) -> str:
    payload = {
        "main": {
            "provider": main.provider,
            "model": main.model,
            "reasoning_effort": main.reasoning_effort,
            "enable_vision_input": main.enable_vision_input,
            "temperature": main.temperature,
            "base_url": main.base_url,
            "timeout": main.timeout,
            "max_tokens": main.max_tokens,
            "context_window_tokens": main.context_window_tokens,
        },
        "slots": [
            {
                "id": slot.id,
                "provider": slot.llm.provider,
                "model": slot.llm.model,
                "reasoning_effort": slot.llm.reasoning_effort,
                "enable_vision_input": slot.llm.enable_vision_input,
                "temperature": slot.llm.temperature,
                "base_url": slot.llm.base_url,
                "timeout": slot.llm.timeout,
                "max_tokens": slot.llm.max_tokens,
                "context_window_tokens": slot.llm.context_window_tokens,
            }
            for slot in slots
        ],
        "orchestration": {
            "enabled": subagents_enabled,
            "max_parallel": subagent_max_parallel,
            "max_tasks_per_run": subagent_max_tasks_per_run,
            "task_timeout_seconds": subagent_task_timeout_seconds,
        },
        "tools": tool_names,
        "revision": revision,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_casework_runtime_snapshot(
    cfg: AppConfig,
    *,
    env: EnvSettings,
    registry: ToolRegistry,
    config_revision: str = "",
) -> CaseworkRuntimeSnapshot:
    """Freeze model, orchestration, and tool-registry choices for one root run."""

    main = _llm_snapshot(cfg.llm, api_key=_main_api_key(cfg.llm, env))
    slots = (
        tuple(
            SubagentSlotRuntimeSnapshot(
                id=slot.id,
                label=slot.label or slot.id,
                llm=_child_llm_snapshot(slot.llm),
            )
            for slot in cfg.subagents.slots
            if slot.enabled
        )
        if cfg.subagents.enabled
        else ()
    )
    tool_names = tuple(sorted(tool.name for tool in registry.list_tools()))
    return CaseworkRuntimeSnapshot(
        main_llm=main,
        subagents_enabled=cfg.subagents.enabled,
        subagent_slots=slots,
        subagent_max_parallel=cfg.subagents.max_parallel,
        subagent_max_tasks_per_run=cfg.subagents.max_tasks_per_run,
        subagent_task_timeout_seconds=cfg.subagents.task_timeout_seconds,
        tool_names=tool_names,
        config_revision=config_revision,
        config_digest=_digest_payload(
            main=main,
            slots=slots,
            tool_names=tool_names,
            revision=config_revision,
            subagents_enabled=cfg.subagents.enabled,
            subagent_max_parallel=cfg.subagents.max_parallel,
            subagent_max_tasks_per_run=cfg.subagents.max_tasks_per_run,
            subagent_task_timeout_seconds=cfg.subagents.task_timeout_seconds,
        ),
    )

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from zd_agent.casework.domain.enums import ResolutionStatus
from zd_agent.casework.domain.models import (
    CaseContext,
    IdentityResolution,
    TicketSnapshot,
)
from zd_agent.config import AppConfig, SubagentConfig, SubagentLLMConfig, SubagentSlotConfig
from zd_agent.runtime.config_snapshot import build_casework_runtime_snapshot
from zd_agent.session.agent_session import AgentSession
from zd_agent.subagents.harness_investigator import HarnessCaseInvestigator
from zd_agent.tools.base import ToolRegistry


NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


def _context() -> CaseContext:
    return CaseContext(
        ticket=TicketSnapshot(
            ticket_id=4001,
            generation=1,
            status="open",
            latest_event_id="event-1",
            latest_event_at=NOW,
            questions=("Original question",),
        ),
        built_at=NOW,
        identity=IdentityResolution(
            contact_email="owner@example.test",
            target_identity="owner@example.test",
            status=ResolutionStatus.NO_MEMBERSHIP,
        ),
    )


@pytest.mark.asyncio
async def test_root_harness_investigator_exposes_memory_and_subagent_control_tools(tmp_path) -> None:
    cfg = AppConfig(
        subagents=SubagentConfig(
            enabled=True,
            slots=[
                SubagentSlotConfig(
                    id="reader",
                    llm=SubagentLLMConfig(
                        provider="openai",
                        model="child-model",
                        api_key="child-secret",
                    ),
                )
            ],
        )
    )
    business_registry = ToolRegistry()
    snapshot = build_casework_runtime_snapshot(
        cfg,
        env=SimpleNamespace(openai_api_key="main", anthropic_api_key=""),
        registry=business_registry,
    )
    captured = {}
    sentinel = object()

    class FakeRootInvestigator:
        async def investigate(self, context):
            captured["context"] = context
            return sentinel

    def investigator_factory(**kwargs):
        captured["registry"] = kwargs["registry"]
        captured["provider"] = kwargs["provider"]
        return FakeRootInvestigator()

    root_provider = object()
    investigator = HarnessCaseInvestigator(
        provider=root_provider,
        business_registry=business_registry,
        session=AgentSession(ticket_id=4001, session_id="root-1"),
        clock=SimpleNamespace(now=lambda: NOW),
        cfg=cfg,
        snapshot=snapshot,
        state_dir=tmp_path,
        investigator_factory=investigator_factory,
    )

    result = await investigator.investigate(_context())
    names = {tool.name for tool in captured["registry"].list_tools()}

    assert result is sentinel
    assert captured["provider"] is root_provider
    assert {"memory_search", "memory_propose", "subagent_spawn", "subagent_wait"} <= names

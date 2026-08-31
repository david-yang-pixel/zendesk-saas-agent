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
from zd_agent.memory.store import MemoryStore
from zd_agent.runtime.config_snapshot import LLMRuntimeSnapshot, SubagentSlotRuntimeSnapshot
from zd_agent.session.agent_session import AgentSession
from zd_agent.subagents.models import SubagentTask, SubagentTaskStatus
from zd_agent.subagents.runner import CaseworkSubagentRunner
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
            questions=("Original customer question",),
        ),
        built_at=NOW,
        identity=IdentityResolution(
            contact_email="owner@example.test",
            target_identity="owner@example.test",
            status=ResolutionStatus.NO_MEMBERSHIP,
        ),
    )


def _slot() -> SubagentSlotRuntimeSnapshot:
    return SubagentSlotRuntimeSnapshot(
        id="reader",
        label="Reader",
        llm=LLMRuntimeSnapshot(
            provider="openai",
            model="child-model",
            reasoning_effort="low",
            enable_vision_input=False,
            temperature=0.0,
            base_url=None,
            timeout=60,
            max_tokens=4096,
            context_window_tokens=128_000,
            api_key="child-secret",
        ),
    )


@pytest.mark.asyncio
async def test_child_runner_binds_objective_and_uses_independent_slot_provider(tmp_path) -> None:
    captured = {}

    def provider_factory(llm):
        captured["llm"] = llm
        return object()

    class FakeInvestigator:
        async def investigate(self, context):
            captured["context"] = context
            return SimpleNamespace(
                proposal=SimpleNamespace(
                    answer_status=SimpleNamespace(value="bounded"),
                    proposed_claims=(),
                ),
                model_dump=lambda mode: {
                    "proposal": {"question": context.ticket.active_question},
                    "evidence": [],
                },
            )

    def investigator_factory(**kwargs):
        captured["registry"] = kwargs["registry"]
        captured["provider"] = kwargs["provider"]
        return FakeInvestigator()

    runner = CaseworkSubagentRunner(
        context=_context(),
        business_registry=ToolRegistry(),
        memory_store=MemoryStore(tmp_path / "memory.sqlite3"),
        root_session_id="root-1",
        clock=SimpleNamespace(now=lambda: NOW),
        provider_factory=provider_factory,
        investigator_factory=investigator_factory,
    )
    task = SubagentTask(
        id="task-1",
        root_session_id="root-1",
        parent_session_id="root-1",
        ticket_id=4001,
        slot_id="reader",
        objective="Inspect current logs only",
        idempotency_key="logs",
        config_digest="digest-1",
        status=SubagentTaskStatus.QUEUED,
        created_at=NOW,
    )
    session = AgentSession(
        ticket_id=4001,
        root_session_id="root-1",
        session_kind="subagent",
    )

    result = await runner(task, _slot(), session)

    assert captured["llm"].model == "child-model"
    assert captured["llm"].api_key == "child-secret"
    assert captured["context"].ticket.active_question == "Inspect current logs only"
    assert all(not tool.root_only for tool in captured["registry"].list_tools())
    assert result["task_id"] == "task-1"
    assert result["authority"] == "delegated_analysis"
    assert result["may_directly_support_current_claims"] is False
    assert result["runtime"]["session_id"] == session.session_id
    assert result["runtime"]["root_session_id"] == "root-1"
    assert result["runtime"]["model"] == "child-model"

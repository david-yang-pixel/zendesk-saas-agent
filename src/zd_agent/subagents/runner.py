from __future__ import annotations

from collections.abc import Callable

from zd_agent.casework.adapters.tool_investigator import BoundedToolCaseInvestigator
from zd_agent.casework.domain.models import CaseContext
from zd_agent.casework.ports.readers import Clock
from zd_agent.memory.store import MemoryStore
from zd_agent.memory.tools import create_memory_tools
from zd_agent.runtime.config_snapshot import (
    LLMRuntimeSnapshot,
    SubagentSlotRuntimeSnapshot,
)
from zd_agent.runtime.llm.factory import create_llm_provider_from_snapshot
from zd_agent.runtime.llm.types import LLMProvider
from zd_agent.services.instruction_store import InstructionStore
from zd_agent.session.agent_session import AgentSession
from zd_agent.subagents.models import SubagentTask
from zd_agent.tools.base import ToolRegistry
from zd_agent.tools.session_registry import build_session_tool_registry


ProviderFactory = Callable[[LLMRuntimeSnapshot], LLMProvider]
InvestigatorFactory = Callable[..., BoundedToolCaseInvestigator]


class CaseworkSubagentRunner:
    """Run one scoped objective in its own provider, messages, and session state."""

    def __init__(
        self,
        *,
        context: CaseContext,
        business_registry: ToolRegistry,
        memory_store: MemoryStore,
        root_session_id: str,
        clock: Clock,
        instruction_store: InstructionStore | None = None,
        provider_factory: ProviderFactory = create_llm_provider_from_snapshot,
        investigator_factory: InvestigatorFactory = BoundedToolCaseInvestigator,
        context_compaction_soft_ratio: float = 0.70,
        context_compaction_hard_ratio: float = 0.88,
        context_checkpoint_max_tokens: int = 12_000,
        context_retain_recent_rounds: int = 2,
    ) -> None:
        self._context = context
        self._business_registry = business_registry
        self._memory_store = memory_store
        self._root_session_id = root_session_id
        self._clock = clock
        self._instruction_store = instruction_store
        self._provider_factory = provider_factory
        self._investigator_factory = investigator_factory
        self._soft_ratio = context_compaction_soft_ratio
        self._hard_ratio = context_compaction_hard_ratio
        self._checkpoint_tokens = context_checkpoint_max_tokens
        self._retain_rounds = context_retain_recent_rounds

    async def __call__(
        self,
        task: SubagentTask,
        slot: SubagentSlotRuntimeSnapshot,
        session: AgentSession,
    ) -> dict[str, object]:
        provider = self._provider_factory(slot.llm)
        registry = build_session_tool_registry(
            self._business_registry,
            extensions=create_memory_tools(self._memory_store, self._root_session_id),
            root=False,
        )
        scoped_context = self._context.model_copy(
            update={
                "ticket": self._context.ticket.model_copy(
                    update={"active_question": task.objective}
                )
            }
        )
        investigator = self._investigator_factory(
            provider=provider,
            registry=registry,
            session=session,
            clock=self._clock,
            context_window_tokens=slot.llm.context_window_tokens,
            context_compaction_soft_ratio=self._soft_ratio,
            context_compaction_hard_ratio=self._hard_ratio,
            context_checkpoint_max_tokens=self._checkpoint_tokens,
            context_retain_recent_rounds=self._retain_rounds,
            instruction_store=self._instruction_store,
        )
        result = await investigator.investigate(scoped_context)
        serialized = result.model_dump(mode="json")
        claims = [
            claim.statement
            for claim in result.proposal.proposed_claims
            if claim.statement.strip()
        ]
        summary = " ".join(claims) or (
            f"Child investigation ended with {result.proposal.answer_status.value}."
        )
        return {
            "task_id": task.id,
            "slot_id": slot.id,
            "summary": summary,
            "authority": "delegated_analysis",
            "may_directly_support_current_claims": False,
            "investigation": serialized,
            "evidence": serialized.get("evidence", []),
            "runtime": {
                "session_id": session.session_id,
                "root_session_id": session.root_session_id,
                "parent_session_id": session.parent_session_id,
                "model": slot.llm.model,
                "provider": slot.llm.provider,
                "rounds": session.round_count,
                "prompt_tokens": session.token_usage.prompt_tokens,
                "completion_tokens": session.token_usage.completion_tokens,
                "context_compactions": session.context_compaction_count,
                "checkpoints": list(session.context_checkpoints),
                "tool_calls": list(session.tool_call_log),
            },
        }

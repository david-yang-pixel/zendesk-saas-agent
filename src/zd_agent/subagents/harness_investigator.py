from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from zd_agent.casework.adapters.tool_investigator import BoundedToolCaseInvestigator
from zd_agent.casework.domain.models import CaseContext, InvestigationResult
from zd_agent.casework.ports.readers import Clock
from zd_agent.config import AppConfig
from zd_agent.memory.store import MemoryStore
from zd_agent.memory.tools import create_memory_tools
from zd_agent.runtime.config_snapshot import CaseworkRuntimeSnapshot
from zd_agent.runtime.llm.types import LLMProvider
from zd_agent.services.instruction_store import InstructionStore
from zd_agent.session.agent_session import AgentSession
from zd_agent.subagents.orchestrator import SubagentOrchestrator
from zd_agent.subagents.runner import CaseworkSubagentRunner
from zd_agent.subagents.store import SubagentStore
from zd_agent.subagents.tools import create_subagent_control_tools
from zd_agent.tools.base import Tool, ToolRegistry
from zd_agent.tools.session_registry import build_session_tool_registry


InvestigatorFactory = Callable[..., Any]


class HarnessCaseInvestigator:
    """Compose root memory, depth-one children, and the shared Tool Loop."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        business_registry: ToolRegistry,
        session: AgentSession,
        clock: Clock,
        cfg: AppConfig,
        snapshot: CaseworkRuntimeSnapshot,
        state_dir: Path,
        instruction_store: InstructionStore | None = None,
        investigator_factory: InvestigatorFactory = BoundedToolCaseInvestigator,
    ) -> None:
        self._provider = provider
        self._business_registry = business_registry
        self._session = session
        self._clock = clock
        self._cfg = cfg
        self._snapshot = snapshot
        self._state_dir = state_dir
        self._instruction_store = instruction_store
        self._investigator_factory = investigator_factory

    def _root_investigator(self, registry: ToolRegistry) -> Any:
        return self._investigator_factory(
            provider=self._provider,
            registry=registry,
            session=self._session,
            clock=self._clock,
            context_window_tokens=self._snapshot.main_llm.context_window_tokens,
            context_compaction_soft_ratio=(
                self._cfg.casework.context_compaction_soft_ratio
            ),
            context_compaction_hard_ratio=(
                self._cfg.casework.context_compaction_hard_ratio
            ),
            context_checkpoint_max_tokens=(
                self._cfg.casework.context_checkpoint_max_tokens
            ),
            context_retain_recent_rounds=(
                self._cfg.casework.context_retain_recent_rounds
            ),
            instruction_store=self._instruction_store,
        )

    async def investigate(self, context: CaseContext) -> InvestigationResult:
        root_session_id = self._session.root_session_id or self._session.session_id
        self._session.root_session_id = root_session_id
        self._session.runtime_config_digest = self._snapshot.config_digest
        memory_store = MemoryStore(self._state_dir / "casework_memory.sqlite3")
        extensions: list[Tool] = list(
            create_memory_tools(memory_store, root_session_id)
        )
        orchestrator: SubagentOrchestrator | None = None
        if self._snapshot.subagents_enabled and self._snapshot.subagent_slots:
            child_runner = CaseworkSubagentRunner(
                context=context,
                business_registry=self._business_registry,
                memory_store=memory_store,
                root_session_id=root_session_id,
                clock=self._clock,
                instruction_store=self._instruction_store,
                context_compaction_soft_ratio=(
                    self._cfg.casework.context_compaction_soft_ratio
                ),
                context_compaction_hard_ratio=(
                    self._cfg.casework.context_compaction_hard_ratio
                ),
                context_checkpoint_max_tokens=(
                    self._cfg.casework.context_checkpoint_max_tokens
                ),
                context_retain_recent_rounds=(
                    self._cfg.casework.context_retain_recent_rounds
                ),
            )
            orchestrator = SubagentOrchestrator(
                store=SubagentStore(self._state_dir / "subagent_tasks.sqlite3"),
                snapshot=self._snapshot,
                root_session_id=root_session_id,
                ticket_id=context.ticket.ticket_id,
                runner=child_runner,
            )
            extensions.extend(create_subagent_control_tools(orchestrator))

        root_registry = build_session_tool_registry(
            self._business_registry,
            extensions=extensions,
            root=True,
        )
        investigator = self._root_investigator(root_registry)
        try:
            return await investigator.investigate(context)
        finally:
            if orchestrator is not None:
                await orchestrator.cancel_all()

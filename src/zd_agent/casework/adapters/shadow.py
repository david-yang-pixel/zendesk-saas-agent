from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Literal, Protocol, cast

from zd_agent.casework.adapters.archive import JsonlShadowArchive
from zd_agent.casework.adapters.legacy_evidence import (
    LegacyCaseMaterial,
    build_legacy_case_material,
)
from zd_agent.casework.adapters.ticket_snapshot import build_ticket_snapshot
from zd_agent.casework.adapters.read_tools import (
    InspectorBillingReader,
    InspectorIdentityReader,
    ReadOnlySessionToolCaller,
    SystemClock,
)
from zd_agent.casework.adapters.model import (
    LlmCaseInvestigator,
    NoOpReplyReviewer,
    PolicyReplyWriter,
)
from zd_agent.casework.adapters.vision import LlmVisualAnalyzer, ZendeskImageLoader
from zd_agent.casework.application.build_context import CaseContextBuilder
from zd_agent.casework.application.compose_reply import ReplyComposer
from zd_agent.casework.application.handle_case import CaseworkOrchestrator
from zd_agent.casework.domain.models import (
    CaseContext,
    InvestigationResult,
    TicketSnapshot,
    VisualObservation,
)
from zd_agent.casework.domain.shadow import (
    LegacyOutcomeSummary,
    ShadowArchiveRecord,
    ShadowFailureRecord,
    build_shadow_record,
)
from zd_agent.casework.ports.readers import Clock
from zd_agent.log import get_logger
from zd_agent.runtime.llm.types import LLMProvider
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import ToolRegistry

logger = get_logger(module="casework_shadow")


class CurrentTicketLoader(Protocol):
    async def load(
        self,
        ticket_id: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]: ...


class VisualAnalyzer(Protocol):
    async def analyze(
        self,
        ticket: TicketSnapshot,
    ) -> tuple[VisualObservation, ...]: ...


class ZendeskCurrentTicketLoader:
    async def load(
        self,
        ticket_id: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        from zd_agent.services.ticket_messages import _fetch_customer_messages

        messages, ticket = await _fetch_customer_messages(ticket_id)
        if ticket is None:
            raise RuntimeError("current Zendesk ticket snapshot is unavailable")
        return cast(list[dict[str, Any]], messages), cast(dict[str, Any], ticket)


class NoVisualAnalyzer:
    async def analyze(
        self,
        ticket: TicketSnapshot,
    ) -> tuple[VisualObservation, ...]:
        _ = ticket
        return ()


_READ_TOOL_EXACT = frozenset(
    {
        "inspector_exec",
        "list_repo_tags",
        "list_repos",
        "saas_deployed_version",
        "sql_query",
    },
)
_READ_TOOL_PREFIXES = (
    "git_",
    "github_",
    "knowledge_",
    "official_docs_",
    "signoz_",
    "ticket_record_",
    "zendesk_get_",
    "zendesk_search",
)
_BILLING_RE = re.compile(
    r"\b(?:billing|charge|credits?|document limit|invoice|payment|pricing|quota|"
    r"refund|renewal|subscription)\b|"
    r"\b(?:what|which|current|active|paid|free|basic|team|professional|enterprise)"
    r"\b.{0,20}\bplan\b|"
    r"\bplan\b.{0,20}\b(?:active|using|subscription|upgrade|downgrade|billing)\b|"
    r"套餐|扣费|支付|账单|续费|退款|配额",
    re.IGNORECASE,
)
_LEGACY_REPLY_TYPES = frozenset({"ask", "solution", "handoff", "closed"})


def _read_tool_names(registry: ToolRegistry) -> frozenset[str]:
    names = set()
    for tool in registry.list_tools():
        allowed_name = tool.name in _READ_TOOL_EXACT or tool.name.startswith(
            _READ_TOOL_PREFIXES,
        )
        if allowed_name and not tool.is_write_operation:
            names.add(tool.name)
    return frozenset(names)


def billing_context_required(ticket: TicketSnapshot, session: AgentSession) -> bool:
    if session.ticket_type.casefold() in {"billing", "refund"}:
        return True
    question = "\n".join(ticket.questions)
    return bool(_BILLING_RE.search(question))


def _legacy_reply_type(
    value: str,
) -> Literal["", "ask", "solution", "handoff", "closed"]:
    normalized = value.strip().casefold()
    if normalized not in _LEGACY_REPLY_TYPES:
        return ""
    return cast(Literal["ask", "solution", "handoff", "closed"], normalized)


class LegacySessionInvestigator:
    def __init__(
        self,
        *,
        session: AgentSession,
        provider: LLMProvider,
        read_tool_names: frozenset[str],
        clock: Clock,
    ) -> None:
        self._session = session
        self._provider = provider
        self._read_tool_names = read_tool_names
        self._clock = clock

    def _material(self, context: CaseContext) -> LegacyCaseMaterial:
        return build_legacy_case_material(
            session=self._session,
            context=context,
            read_tool_names=self._read_tool_names,
            observed_at=self._clock.now(),
        )

    async def investigate(self, context: CaseContext) -> InvestigationResult:
        investigator = LlmCaseInvestigator(
            provider=self._provider,
            material=self._material(context),
        )
        return await investigator.investigate(context)


class ShadowRuntime:
    """Evaluate the new decision core after legacy completion, without delivery."""

    def __init__(
        self,
        *,
        session: AgentSession,
        provider: LLMProvider,
        registry: ToolRegistry,
        archive: JsonlShadowArchive,
        ticket_loader: CurrentTicketLoader,
        visual_analyzer: VisualAnalyzer,
        clock: Clock,
    ) -> None:
        self._session = session
        self._provider = provider
        self._registry = registry
        self._archive = archive
        self._ticket_loader = ticket_loader
        self._visual_analyzer = visual_analyzer
        self._clock = clock

    def _legacy_outcome(self) -> LegacyOutcomeSummary:
        return LegacyOutcomeSummary(
            status=self._session.status.value,
            reply_type=_legacy_reply_type(self._session.public_reply_type),
            reply_sent=self._session.reply_sent,
        )

    async def evaluate(self) -> ShadowArchiveRecord:
        messages, raw_ticket = await self._ticket_loader.load(self._session.ticket_id)
        ticket = build_ticket_snapshot(
            session=self._session,
            customer_messages=messages,
            ticket=raw_ticket,
        )
        observations = await self._visual_analyzer.analyze(ticket)
        caller = ReadOnlySessionToolCaller(self._registry, self._session)
        context_builder = CaseContextBuilder(
            identity_reader=InspectorIdentityReader(caller),
            billing_reader=InspectorBillingReader(caller, now=self._clock.now),
            clock=self._clock,
        )
        investigator = LegacySessionInvestigator(
            session=self._session,
            provider=self._provider,
            read_tool_names=_read_tool_names(self._registry),
            clock=self._clock,
        )
        orchestrator = CaseworkOrchestrator(
            context_builder=context_builder,
            investigator=investigator,
            reply_composer=ReplyComposer(
                writer=PolicyReplyWriter(),
                reviewer=NoOpReplyReviewer(),
            ),
            clock=self._clock,
        )
        run = await orchestrator.run(
            ticket=ticket,
            billing_required=billing_context_required(ticket, self._session),
            visual_observations=observations,
        )
        return build_shadow_record(
            run,
            recorded_at=self._clock.now(),
            legacy=self._legacy_outcome(),
        )

    def _failure(self, error: Exception) -> ShadowFailureRecord:
        legacy = self._legacy_outcome()
        return ShadowFailureRecord.from_error(
            recorded_at=self._clock.now(),
            ticket_id=self._session.ticket_id,
            generation=self._session.customer_context_generation,
            stage="shadow_evaluation",
            error=error,
            legacy_status=legacy.status,
            legacy_reply_type=legacy.reply_type,
            legacy_reply_sent=legacy.reply_sent,
        )

    async def run_best_effort(self) -> ShadowArchiveRecord:
        try:
            record = await self.evaluate()
        except Exception as error:
            record = self._failure(error)
            logger.warning(
                "casework_shadow_failed",
                ticket_id=record.ticket_id,
                error_type=record.error_type,
                error_fingerprint=record.error_fingerprint,
            )
        try:
            self._archive.append(record)
        except Exception as error:
            failure = self._failure(error)
            logger.warning(
                "casework_shadow_archive_failed",
                ticket_id=failure.ticket_id,
                error_type=failure.error_type,
                error_fingerprint=failure.error_fingerprint,
            )
        return record


async def run_casework_shadow_best_effort(
    *,
    session: AgentSession,
    provider: LLMProvider,
    registry: ToolRegistry,
    archive_dir: Path,
    enable_vision: bool,
) -> ShadowArchiveRecord:
    visual_analyzer: VisualAnalyzer = (
        LlmVisualAnalyzer(provider=provider, image_loader=ZendeskImageLoader())
        if enable_vision
        else NoVisualAnalyzer()
    )
    runtime = ShadowRuntime(
        session=session,
        provider=provider,
        registry=registry,
        archive=JsonlShadowArchive(archive_dir),
        ticket_loader=ZendeskCurrentTicketLoader(),
        visual_analyzer=visual_analyzer,
        clock=SystemClock(),
    )
    return await runtime.run_best_effort()

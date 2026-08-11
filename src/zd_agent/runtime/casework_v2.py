from __future__ import annotations

from pathlib import Path

from zd_agent.casework.adapters.audit import render_casework_audit
from zd_agent.casework.adapters.effects import configured_casework_effects
from zd_agent.casework.adapters.read_tools import (
    InspectorBillingReader,
    InspectorIdentityReader,
    ReadOnlySessionToolCaller,
    SystemClock,
)
from zd_agent.casework.adapters.ticket_snapshot import build_ticket_snapshot
from zd_agent.casework.adapters.model import NoOpReplyReviewer, PolicyReplyWriter
from zd_agent.casework.adapters.ticket import (
    NoVisualAnalyzer,
    VisualAnalyzer,
    ZendeskCurrentTicketLoader,
)
from zd_agent.casework.adapters.tool_investigator import BoundedToolCaseInvestigator
from zd_agent.casework.adapters.vision import LlmVisualAnalyzer, ZendeskImageLoader
from zd_agent.casework.application.build_context import CaseContextBuilder
from zd_agent.casework.application.compose_reply import ReplyComposer
from zd_agent.casework.application.execute_case import CaseworkExecutor
from zd_agent.casework.application.handle_case import (
    CaseworkOrchestrator,
    deterministic_intake_required,
)
from zd_agent.casework.domain.enums import (
    DeliveryState,
    Disposition,
    ExecutionMode,
)
from zd_agent.casework.domain.models import (
    CaseworkExecution,
    TicketSnapshot,
    VisualObservation,
)
from zd_agent.casework.policies.routing import billing_context_required
from zd_agent.config import AppConfig
from zd_agent.runtime.llm.types import LLMProvider
from zd_agent.session.agent_session import AgentSession, SessionStatus
from zd_agent.tools.base import ToolRegistry


def _snapshot_session(
    session: AgentSession,
    customer_messages: list[dict[str, object]],
) -> None:
    session.customer_msgs_snapshot_count = len(customer_messages)
    session.customer_msgs_snapshot_last_id = (
        str(customer_messages[-1].get("id") or "") if customer_messages else ""
    )
    session.customer_msgs_snapshot_captured = True


async def _analyze_visuals_when_needed(
    *,
    ticket: TicketSnapshot,
    visual_analyzer: VisualAnalyzer,
) -> tuple[VisualObservation, ...]:
    """Keep form-only and human takeover paths free of image/model reads."""
    if deterministic_intake_required(ticket):
        return ()
    return await visual_analyzer.analyze(ticket)


async def execute_casework_v2(
    *,
    session: AgentSession,
    cfg: AppConfig,
    provider: LLMProvider,
    registry: ToolRegistry,
) -> CaseworkExecution:
    clock = SystemClock()
    customer_messages, raw_ticket = await ZendeskCurrentTicketLoader().load(
        session.ticket_id,
    )
    _snapshot_session(session, customer_messages)
    ticket = build_ticket_snapshot(
        session=session,
        customer_messages=customer_messages,
        ticket=raw_ticket,
    )
    visual_analyzer: VisualAnalyzer = (
        LlmVisualAnalyzer(
            provider=provider,
            image_loader=ZendeskImageLoader(),
            session=session,
            max_tokens=cfg.casework.max_investigation_tokens,
        )
        if cfg.llm.enable_vision_input
        else NoVisualAnalyzer()
    )
    observations = await _analyze_visuals_when_needed(
        ticket=ticket,
        visual_analyzer=visual_analyzer,
    )
    caller = ReadOnlySessionToolCaller(registry, session)
    context_builder = CaseContextBuilder(
        identity_reader=InspectorIdentityReader(caller),
        billing_reader=InspectorBillingReader(caller, now=clock.now),
        clock=clock,
    )
    investigator = BoundedToolCaseInvestigator(
        provider=provider,
        registry=registry,
        session=session,
        clock=clock,
        max_rounds=cfg.casework.max_investigation_rounds,
        max_calls=cfg.casework.max_investigation_calls,
        max_tokens=cfg.casework.max_investigation_tokens,
    )
    orchestrator = CaseworkOrchestrator(
        context_builder=context_builder,
        investigator=investigator,
        reply_composer=ReplyComposer(
            writer=PolicyReplyWriter(),
            reviewer=NoOpReplyReviewer(),
        ),
        clock=clock,
    )
    state_dir = Path(cfg.resources.base_dir) / cfg.resources.state_dir
    executor = CaseworkExecutor(
        orchestrator=orchestrator,
        effects=configured_casework_effects(
            state_dir=state_dir,
            snapshot_customer_message_count=session.customer_msgs_snapshot_count,
            snapshot_customer_last_message_id=session.customer_msgs_snapshot_last_id,
            public_reply_enabled=cfg.public_reply.enabled,
        ),
    )
    mode = cfg.casework.mode
    if mode not in {ExecutionMode.CANARY, ExecutionMode.CASEWORK}:
        raise ValueError(f"v2 execution cannot run in {mode.value} mode")
    return await executor.execute(
        ticket=ticket,
        billing_required=billing_context_required(ticket),
        automation_attempt=session.casework_attempt,
        visual_observations=observations,
        execution_mode=mode,
        delivery_attempt_id=session.delivery_attempt_id or session.session_id,
        dry_run=session.dry_run,
    )


def apply_casework_execution_to_session(
    session: AgentSession,
    execution: CaseworkExecution,
) -> None:
    run = execution.run
    receipt = execution.effect_receipt
    session.analysis = receipt.authoritative_audit or render_casework_audit(run)
    session.reply_draft = run.reply_draft.body if run.reply_draft else ""
    session.casework_disposition = run.decision.disposition.value
    session.casework_reply_kind = run.decision.reply_kind.value
    session.casework_ownership_action = run.decision.ownership_action.value
    session.casework_reason_code = run.decision.reason_code.value
    session.casework_delivery_state = receipt.delivery_state.value
    session.casework_ownership_state = receipt.ownership_state.value
    session.casework_effect_reference = (
        receipt.ownership_reference or receipt.delivery_reference
    )
    session.casework_required_customer_fields = run.decision.required_customer_fields
    session.casework_question = run.investigation.proposal.question
    if receipt.accepted_snapshot_last_id:
        session.customer_msgs_snapshot_count = receipt.accepted_snapshot_count or 0
        session.customer_msgs_snapshot_last_id = receipt.accepted_snapshot_last_id
        session.customer_msgs_snapshot_captured = True
    if receipt.delivery_state is DeliveryState.CONFIRMED:
        session.reply_sent = True
        session.status = SessionStatus.COMPLETED
        return
    if receipt.delivery_state is DeliveryState.DRAFTED:
        session.status = SessionStatus.COMPLETED
        return
    if receipt.delivery_state is DeliveryState.PAUSED:
        session.status = SessionStatus.COMPLETED
        session.error_message = receipt.detail or "v2 public delivery paused"
        return
    if (
        receipt.delivery_state is DeliveryState.NOT_REQUIRED
        and run.decision.disposition is Disposition.RETRY_PENDING
    ):
        session.status = SessionStatus.DEFERRED
        session.error_message = "v2 internal retry pending"
        return
    if receipt.delivery_state in {DeliveryState.DEFERRED, DeliveryState.AMBIGUOUS}:
        session.status = SessionStatus.DEFERRED
        session.error_message = receipt.detail
        return
    session.status = SessionStatus.FAILED
    session.error_message = receipt.detail or "v2 casework effects failed"

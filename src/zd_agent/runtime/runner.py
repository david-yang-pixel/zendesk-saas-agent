"""Run the v2 casework controller for one Zendesk ticket."""

from __future__ import annotations

from pathlib import Path
import time

from zd_agent.casework.adapters.tool_investigator import CustomerContextStaleError
from zd_agent.casework.adapters.tool_registry import create_casework_tool_registry
from zd_agent.casework.policies.rollout import casework_controls_ticket
from zd_agent.config import AppConfig, EnvSettings, get_config
from zd_agent.integrations.zendesk_mcp import call_zendesk_tool
from zd_agent.log import get_logger
from zd_agent.runtime.casework_v2 import (
    apply_casework_execution_to_session,
    execute_casework_v2,
)
from zd_agent.runtime.llm.factory import create_llm_provider
from zd_agent.runtime.runner_archive import ensure_archive
from zd_agent.services.casework_runtime_failures import (
    persist_runtime_failure_ownership,
)
from zd_agent.services.feishu_notifications import notify_session_outcome_durable
from zd_agent.services.instance_fence import verify_active_instance
from zd_agent.session.agent_session import AgentSession, SessionStatus

logger = get_logger(module="runner")

_ACK_NOTE = (
    "🤖 **AI Agent is now processing this ticket.** "
    "Please wait while the agent analyzes and prepares a response."
)


async def _send_ack_note(ticket_id: int) -> None:
    """Leave a best-effort internal trace that v2 started processing."""
    try:
        payload = await call_zendesk_tool(
            "add_internal_note",
            {
                "ticket_id": ticket_id,
                "body": _ACK_NOTE,
                "explicit_user_internal_note_request": "agent processing ack",
                "dry_run": False,
                "confirm": True,
                "actor": "zd-agent",
            },
        )
        if not payload.get("success"):
            raise RuntimeError(f"{payload.get('code')}: {payload.get('error')}")
        logger.info("ack_note_sent", ticket_id=ticket_id)
    except Exception as error:  # noqa: BLE001 - ACK must not block casework.
        logger.warning(
            "ack_note_failed",
            ticket_id=ticket_id,
            error_type=type(error).__name__,
        )


async def _handle_casework_runtime_failure(
    session: AgentSession,
    cfg: AppConfig,
    error: Exception,
) -> None:
    """Retry v2 failures, then persist one typed support ownership command."""
    session.status = SessionStatus.FAILED
    session.error_message = str(error)
    if session.dry_run or session.casework_attempt < 3:
        return

    state_dir = Path(cfg.resources.base_dir) / cfg.resources.state_dir
    try:
        receipt = await persist_runtime_failure_ownership(
            state_dir=state_dir,
            ticket_id=session.ticket_id,
            delivery_attempt_id=session.delivery_attempt_id or session.session_id,
            generation=session.customer_context_generation,
            source_event_id=session.customer_msgs_snapshot_last_id,
            snapshot_customer_message_count=(
                session.customer_msgs_snapshot_count
                if session.customer_msgs_snapshot_captured
                else None
            ),
            snapshot_customer_last_message_id=(session.customer_msgs_snapshot_last_id),
            automation_attempt=session.casework_attempt,
            failure_type=type(error).__name__,
        )
    except Exception as ownership_error:  # noqa: BLE001 - preserve retry state.
        logger.error(
            "casework_runtime_ownership_failed",
            ticket_id=session.ticket_id,
            session_id=session.session_id,
            error_type=type(ownership_error).__name__,
            exc_info=True,
        )
        return

    session.casework_disposition = "human_action_required"
    session.casework_ownership_action = receipt.ownership_action.value
    session.casework_reason_code = receipt.reason_code.value
    session.casework_delivery_state = "not_required"
    session.casework_ownership_state = receipt.ownership_state.value
    session.casework_effect_reference = receipt.reference
    session.analysis = (
        "v2 automatic retry budget exhausted; a typed support ownership "
        f"command was persisted as {receipt.reference}"
    )
    session.status = SessionStatus.COMPLETED


async def _run_casework_agent(session: AgentSession, cfg: AppConfig) -> None:
    """Run the only production decision and effect path."""
    started_at = time.monotonic()
    verify_active_instance(Path(cfg.resources.base_dir) / cfg.resources.state_dir)
    if not casework_controls_ticket(
        mode=cfg.casework.mode,
        canary_ticket_ids=cfg.casework.canary_ticket_ids,
        ticket_id=session.ticket_id,
    ):
        session.status = SessionStatus.DEFERRED
        session.error_message = "ticket is outside the v2 canary allowlist"
        session.analysis = (
            "v2 rollout gate deferred this ticket before any customer-visible "
            "or ownership effect."
        )
        ensure_archive(
            session,
            elapsed_seconds=time.monotonic() - started_at,
        )
        logger.info(
            "casework_v2_canary_deferred",
            ticket_id=session.ticket_id,
            session_id=session.session_id,
        )
        return
    if not session.dry_run and session.casework_attempt == 1:
        await _send_ack_note(session.ticket_id)
    logger.info(
        "casework_v2_start",
        ticket_id=session.ticket_id,
        session_id=session.session_id,
        execution_mode=cfg.casework.mode.value,
        dry_run=session.dry_run,
    )
    try:
        if not session.dry_run and session.casework_attempt > 3:
            raise RuntimeError("typed v2 ownership persistence retry")
        registry = create_casework_tool_registry()
        provider = create_llm_provider(cfg.llm, EnvSettings())
        execution = await execute_casework_v2(
            session=session,
            cfg=cfg,
            provider=provider,
            registry=registry,
        )
        apply_casework_execution_to_session(session, execution)
    except CustomerContextStaleError as error:
        session.status = SessionStatus.DEFERRED
        session.error_message = str(error)
        session.analysis = (
            "Customer context changed during investigation; this generation "
            "was stopped before delivery and must be replaced by a fresh generation."
        )
        logger.info(
            "casework_v2_customer_context_stale",
            ticket_id=session.ticket_id,
            session_id=session.session_id,
            generation=session.customer_context_generation,
        )
    except Exception as error:  # noqa: BLE001 - bounded retry owns all failures.
        logger.error(
            "casework_v2_error",
            ticket_id=session.ticket_id,
            session_id=session.session_id,
            error_type=type(error).__name__,
            exc_info=True,
        )
        await _handle_casework_runtime_failure(session, cfg, error)

    await notify_session_outcome_durable(session, cfg)
    ensure_archive(
        session,
        elapsed_seconds=time.monotonic() - started_at,
    )
    logger.info(
        "casework_v2_done",
        ticket_id=session.ticket_id,
        session_id=session.session_id,
        status=session.status.value,
        rounds=session.round_count,
        total_tokens=session.token_usage.total_tokens,
        reply_sent=session.reply_sent,
        disposition=session.casework_disposition,
        ownership_action=session.casework_ownership_action,
        delivery_state=session.casework_delivery_state,
    )


async def run_agent(session: AgentSession) -> None:
    """Run one ticket through v2 intake, investigation, decision, and effects."""
    cfg = get_config()
    await _run_casework_agent(session, cfg)

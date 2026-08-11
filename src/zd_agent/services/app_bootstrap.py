"""Runtime dependency bootstrap for the SaaS aiohttp app."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web

from zd_agent.config import AppConfig
from zd_agent.log import get_logger
from zd_agent.services.app_context import install_saas_app_context
from zd_agent.services.app_lifecycle import wire_session_finished_callback
from zd_agent.services.admin_config import AdminConfigStore
from zd_agent.services.casework_effect_store import CaseworkEffectStore
from zd_agent.services.casework_ownership_effects import (
    reconcile_casework_ownership_effects,
)
from zd_agent.services.durable_trigger_queue import DurableTriggerQueue
from zd_agent.services.public_reply_internal_note import (
    PostPublicInternalNoteStore,
    reconcile_pending_post_public_internal_notes,
)
from zd_agent.services.public_reply_delivery_store import PublicReplyDeliveryStore
from zd_agent.services.public_reply_handoff_effects import (
    PostPublicHandoffStore,
    reconcile_pending_post_public_handoff_effects,
)
from zd_agent.services.webhook_dedup import WebhookDedup
from zd_agent.services.v2_state_migration import migrate_ticket_state_to_v2
from zd_agent.trigger.state_store import TicketStateTracker

if TYPE_CHECKING:
    from zd_agent.session.manager import SessionManager

logger = get_logger(module="services.app_bootstrap")


@dataclass(frozen=True)
class SaasAppRuntime:
    state_dir: Path
    trigger_queue: DurableTriggerQueue
    state: TicketStateTracker


def apply_saved_admin_config(cfg: AppConfig) -> Path:
    state_dir = Path(cfg.resources.base_dir) / cfg.resources.state_dir
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_dir.chmod(0o700)
    AdminConfigStore(state_dir).apply_saved(cfg)
    from zd_agent.tools.public_reply_delivery import (
        validate_public_reply_transport_config,
    )

    validate_public_reply_transport_config(cfg)
    return state_dir


def create_configured_session_manager(cfg: AppConfig) -> SessionManager:
    from zd_agent.session.manager import SessionManager

    apply_saved_admin_config(cfg)
    return SessionManager(max_concurrent=cfg.agent.max_concurrent_sessions)


def prepare_saas_app_runtime(
    *,
    app: web.Application,
    cfg: AppConfig,
    manager: SessionManager,
) -> SaasAppRuntime:
    """Create shared runtime dependencies and install them into the app."""
    state_dir = apply_saved_admin_config(cfg)

    trigger_queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    state = TicketStateTracker(state_dir / "tickets.json")
    migration = migrate_ticket_state_to_v2(state)
    if migration.migrated:
        logger.warning(
            "ticket_state_migrated_to_v2",
            migrated=migration.migrated,
            backup_path=str(migration.backup_path),
        )
    recovered_ticket_ids: list[int] = []
    for ticket_id in state.list_by_state("processing"):
        try:
            numeric_ticket_id = int(ticket_id)
        except ValueError:
            logger.error("processing_recovery_invalid_ticket_id", ticket_id=ticket_id)
            continue
        record = state.get(ticket_id)
        recovered = trigger_queue.ensure_processing_recovery(
            numeric_ticket_id,
            delivery_attempt_id=(
                record.delivery_attempt_id if record is not None else ""
            ),
        )
        if recovered:
            recovered_ticket_ids.append(numeric_ticket_id)
        else:
            logger.info(
                "processing_session_recovery_already_queued",
                ticket_id=numeric_ticket_id,
            )
    if recovered_ticket_ids:
        logger.warning(
            "processing_sessions_recovered",
            ticket_ids=recovered_ticket_ids,
            count=len(recovered_ticket_ids),
        )
    webhook_dedup = WebhookDedup(
        state_dir / "processed_webhooks.json",
        max_size=10000,
    )
    wire_session_finished_callback(manager=manager, state=state)
    install_saas_app_context(
        app=app,
        cfg=cfg,
        trigger_queue=trigger_queue,
        state=state,
        webhook_dedup=webhook_dedup,
        manager=manager,
    )

    async def _reconcile_dead_letters(_app: web.Application) -> None:
        del _app
        try:
            from zd_agent.services.trigger_consumer import (
                reconcile_dead_lettered_triggers,
            )

            reconciled = await reconcile_dead_lettered_triggers(
                queue=trigger_queue,
                state=state,
            )
            if reconciled:
                logger.warning("trigger_dead_letters_startup_reconciled", count=reconciled)
        except Exception as exc:  # noqa: BLE001 - next periodic pass retries.
            logger.warning(
                "trigger_dead_letter_startup_reconcile_failed",
                error=str(exc)[:200],
            )

    async def _reconcile_internal_notes(_app: web.Application) -> None:
        del _app
        try:
            from zd_agent.integrations.zendesk_mcp import call_zendesk_tool

            note_store = PostPublicInternalNoteStore(
                state_dir / "post_public_internal_note_outbox.sqlite3"
            )
            handoff_store = PostPublicHandoffStore(
                state_dir / "post_public_handoff_outbox.sqlite3"
            )
            casework_store = CaseworkEffectStore(
                state_dir / "casework_effects.sqlite3"
            )
            note_pending = bool(note_store.pending(limit=1))
            handoff_pending = bool(handoff_store.pending(limit=1))
            casework_pending = bool(casework_store.pending_ownership(limit=1))
            if not note_pending and not handoff_pending and not casework_pending:
                return
            delivery_store = PublicReplyDeliveryStore(
                state_dir / "public_reply_outbox.sqlite3"
            )
            reconciled_notes = 0
            reconciled_handoffs = 0
            reconciled_casework = 0
            if note_pending:
                reconciled_notes = await asyncio.wait_for(
                    reconcile_pending_post_public_internal_notes(
                        note_store=note_store,
                        zendesk_tool_caller=call_zendesk_tool,
                        delivery_store=delivery_store,
                    ),
                    timeout=90,
                )
            if handoff_pending:
                reconciled_handoffs = await asyncio.wait_for(
                    reconcile_pending_post_public_handoff_effects(
                        store=handoff_store,
                        delivery_store=delivery_store,
                    ),
                    timeout=90,
                )
            if casework_pending:
                reconciled_casework = await asyncio.wait_for(
                    reconcile_casework_ownership_effects(store=casework_store),
                    timeout=90,
                )
            logger.info(
                "post_public_side_effects_startup_reconciled",
                notes=reconciled_notes,
                handoffs=reconciled_handoffs,
                casework_ownership=reconciled_casework,
            )
        except Exception as exc:  # noqa: BLE001 - startup recovery is best effort.
            logger.warning(
                "post_public_internal_note_startup_reconcile_failed",
                error=str(exc)[:200],
            )

    app.on_startup.append(_reconcile_dead_letters)
    app.on_startup.append(_reconcile_internal_notes)

    return SaasAppRuntime(
        state_dir=state_dir,
        trigger_queue=trigger_queue,
        state=state,
    )

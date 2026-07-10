"""Runtime dependency bootstrap for the SaaS aiohttp app."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import web

from zd_agent.log import get_logger
from zd_agent.services.app_context import install_saas_app_context
from zd_agent.services.app_lifecycle import wire_session_finished_callback
from zd_agent.services.admin_config import AdminConfigStore
from zd_agent.services.durable_trigger_queue import DurableTriggerQueue
from zd_agent.services.webhook_dedup import WebhookDedup
from zd_agent.trigger.state_store import TicketStateTracker

logger = get_logger(module="services.app_bootstrap")


@dataclass(frozen=True)
class SaasAppRuntime:
    state_dir: Path
    trigger_queue: DurableTriggerQueue
    state: TicketStateTracker


def prepare_saas_app_runtime(
    *,
    app: web.Application,
    cfg: Any,
    manager: Any,
) -> SaasAppRuntime:
    """Create shared runtime dependencies and install them into the app."""
    state_dir = Path(cfg.resources.base_dir) / cfg.resources.state_dir
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_dir.chmod(0o700)
    AdminConfigStore(state_dir).apply_saved(cfg)

    trigger_queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    state = TicketStateTracker(state_dir / "tickets.json")
    recovered_ticket_ids: list[int] = []
    for ticket_id in state.list_by_state("processing"):
        try:
            numeric_ticket_id = int(ticket_id)
        except ValueError:
            logger.error("processing_recovery_invalid_ticket_id", ticket_id=ticket_id)
            continue
        trigger_queue.ensure_processing_recovery(numeric_ticket_id)
        recovered_ticket_ids.append(numeric_ticket_id)
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

    return SaasAppRuntime(
        state_dir=state_dir,
        trigger_queue=trigger_queue,
        state=state,
    )

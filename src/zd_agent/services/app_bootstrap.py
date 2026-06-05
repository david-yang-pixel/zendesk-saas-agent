"""Runtime dependency bootstrap for the SaaS aiohttp app."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import web

from zd_agent.services.app_context import install_saas_app_context
from zd_agent.services.app_lifecycle import wire_session_finished_callback
from zd_agent.services.teleport_tokens import TokenStore
from zd_agent.services.webhook_dedup import WebhookDedup
from zd_agent.trigger.state_store import TicketStateTracker


@dataclass(frozen=True)
class SaasAppRuntime:
    state_dir: Path
    trigger_queue: asyncio.Queue
    state: TicketStateTracker


def prepare_saas_app_runtime(
    *,
    app: web.Application,
    cfg: Any,
    manager: Any,
) -> SaasAppRuntime:
    """Create shared runtime dependencies and install them into the app."""
    state_dir = Path(cfg.resources.base_dir) / cfg.resources.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)

    trigger_queue: asyncio.Queue = asyncio.Queue()
    state = TicketStateTracker(state_dir / "tickets.json")
    webhook_dedup = WebhookDedup(
        state_dir / "processed_webhooks.json",
        max_size=10000,
    )
    teleport_auth_token_store = TokenStore()

    wire_session_finished_callback(manager=manager, state=state)
    install_saas_app_context(
        app=app,
        cfg=cfg,
        trigger_queue=trigger_queue,
        state=state,
        webhook_dedup=webhook_dedup,
        teleport_auth_token_store=teleport_auth_token_store,
        manager=manager,
    )

    return SaasAppRuntime(
        state_dir=state_dir,
        trigger_queue=trigger_queue,
        state=state,
    )

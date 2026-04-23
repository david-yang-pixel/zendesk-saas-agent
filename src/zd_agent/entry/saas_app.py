"""SaaS unified aiohttp app factory + background tasks."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from aiohttp import web

from zd_agent.config import get_config
from zd_agent.entry.silence_waker import run_silence_waker
from zd_agent.entry.webhook_dedup import WebhookDedup
from zd_agent.entry.zendesk_webhook import handle_zendesk_webhook
from zd_agent.log import get_logger
from zd_agent.trigger.state_store import TicketStateTracker

logger = get_logger(module="entry.saas_app")


async def _healthz(request: web.Request) -> web.Response:
    return web.Response(status=200, text="ok")


async def _trigger_consumer(
    queue: asyncio.Queue, manager: Any,
) -> None:
    """Consume trigger_queue; dispatch each ticket to the SessionManager."""
    while True:
        msg = await queue.get()
        try:
            ticket_id = int(msg["ticket_id"])
            source = msg.get("source", "unknown")
            await manager.dispatch(
                ticket_id,
                dry_run=False,
                trigger_type=f"webhook-{source}",
            )
        except Exception as e:
            logger.error(
                "trigger_consumer_error",
                error=str(e), msg=msg,
            )


def create_saas_app(manager: Any) -> web.Application:
    """Build the aiohttp application used by `zd-agent serve`.

    Caller wires in a SessionManager for ticket dispatch.
    """
    cfg = get_config()

    # Shared state
    state_dir = Path(cfg.resources.base_dir) / cfg.resources.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)

    trigger_queue: asyncio.Queue = asyncio.Queue()

    state = TicketStateTracker(state_dir / "tickets.json")
    webhook_dedup = WebhookDedup(
        state_dir / "processed_webhooks.json",
        max_size=10000,
    )

    app = web.Application()
    app["config"] = cfg
    app["trigger_queue"] = trigger_queue
    app["ticket_state"] = state
    app["webhook_dedup"] = webhook_dedup
    app["session_manager"] = manager

    app.router.add_post(cfg.webhook.zendesk_path, handle_zendesk_webhook)
    app.router.add_get("/healthz", _healthz)

    async def _on_startup(_app: web.Application) -> None:
        _app["silence_task"] = asyncio.create_task(
            run_silence_waker(
                state=state,
                trigger_queue=trigger_queue,
                scan_interval_seconds=cfg.silence_waker.scan_interval_seconds,
            )
        )
        _app["trigger_task"] = asyncio.create_task(
            _trigger_consumer(trigger_queue, manager),
        )
        logger.info("saas_app_started", port=cfg.webhook.listen_port)

    async def _on_cleanup(_app: web.Application) -> None:
        for key in ("silence_task", "trigger_task"):
            task = _app.get(key)
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)

    return app


def run_saas_app(manager: Any) -> None:
    """Convenience: build + run the app via aiohttp.web.run_app."""
    cfg = get_config()
    app = create_saas_app(manager)
    web.run_app(
        app,
        host=cfg.webhook.listen_host,
        port=cfg.webhook.listen_port,
    )

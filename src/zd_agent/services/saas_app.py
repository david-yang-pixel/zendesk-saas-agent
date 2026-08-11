"""SaaS unified aiohttp app factory + background tasks."""

from __future__ import annotations

from typing import Any

from aiohttp import web

from zd_agent.config import get_config
from zd_agent.services.app_bootstrap import prepare_saas_app_runtime
from zd_agent.services.admin_routes import register_admin_routes
from zd_agent.services.app_lifecycle import (
    register_background_lifecycle,
)
from zd_agent.services.saas_background import default_background_runners
from zd_agent.services.saas_routes import register_saas_routes
from zd_agent.services.trigger_consumer import run_saas_trigger_consumer


def create_saas_app(manager: Any) -> web.Application:
    """Build the aiohttp application used by `zd-agent serve`."""
    cfg = get_config()
    app = web.Application()
    runtime = prepare_saas_app_runtime(app=app, cfg=cfg, manager=manager)

    register_saas_routes(app=app, cfg=cfg)
    register_admin_routes(app=app, cfg=cfg, state_dir=runtime.state_dir)

    register_background_lifecycle(
        app=app,
        cfg=cfg,
        state_dir=runtime.state_dir,
        state=runtime.state,
        trigger_queue=runtime.trigger_queue,
        manager=manager,
        trigger_consumer_fn=run_saas_trigger_consumer,
        background_runners=default_background_runners(),
    )

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

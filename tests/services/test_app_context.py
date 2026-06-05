"""Tests for typed aiohttp application context keys."""
from __future__ import annotations

import asyncio
import warnings
from unittest.mock import MagicMock

from aiohttp import web
from aiohttp.web_exceptions import NotAppKeyWarning

from zd_agent.config import AppConfig
from zd_agent.services.app_context import (
    app_config,
    install_saas_app_context,
    session_manager,
    ticket_state,
    trigger_queue,
    webhook_dedup,
)
from zd_agent.services.teleport_tokens import TokenStore
from zd_agent.services.webhook_dedup import WebhookDedup
from zd_agent.trigger.state_store import TicketStateTracker


def test_install_saas_app_context_uses_typed_app_keys(tmp_path) -> None:
    app = web.Application()
    cfg = AppConfig()
    queue: asyncio.Queue = asyncio.Queue()
    state = TicketStateTracker(tmp_path / "tickets.json")
    dedup = WebhookDedup(tmp_path / "dedup.json", max_size=10)
    token_store = TokenStore()
    manager = MagicMock()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        install_saas_app_context(
            app=app,
            cfg=cfg,
            trigger_queue=queue,
            state=state,
            webhook_dedup=dedup,
            teleport_auth_token_store=token_store,
            manager=manager,
        )

    assert not [w for w in caught if issubclass(w.category, NotAppKeyWarning)]
    assert app_config(app) is cfg
    assert trigger_queue(app) is queue
    assert ticket_state(app) is state
    assert webhook_dedup(app) is dedup
    assert session_manager(app) is manager

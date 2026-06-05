"""Typed aiohttp app context for the SaaS service."""
from __future__ import annotations

from typing import Any

from aiohttp import web

CONFIG_KEY = web.AppKey("zd_agent.config", object)
TRIGGER_QUEUE_KEY = web.AppKey("zd_agent.trigger_queue", object)
TICKET_STATE_KEY = web.AppKey("zd_agent.ticket_state", object)
WEBHOOK_DEDUP_KEY = web.AppKey("zd_agent.webhook_dedup", object)
TELEPORT_AUTH_TOKEN_STORE_KEY = web.AppKey(
    "zd_agent.teleport_auth_token_store",
    object,
)
SESSION_MANAGER_KEY = web.AppKey("zd_agent.session_manager", object)

SILENCE_TASK_KEY = web.AppKey("zd_agent.silence_task", object)
WAITING_POLLER_TASK_KEY = web.AppKey("zd_agent.waiting_poller_task", object)
MENTION_POLLER_TASK_KEY = web.AppKey("zd_agent.mention_poller_task", object)
TRIGGER_TASK_KEY = web.AppKey("zd_agent.trigger_task", object)
MENTION_TRACKER_KEY = web.AppKey("zd_agent.mention_tracker", object)

BACKGROUND_TASK_KEYS = (
    SILENCE_TASK_KEY,
    WAITING_POLLER_TASK_KEY,
    MENTION_POLLER_TASK_KEY,
    TRIGGER_TASK_KEY,
)

_LEGACY_KEYS = {
    CONFIG_KEY: "config",
    TRIGGER_QUEUE_KEY: "trigger_queue",
    TICKET_STATE_KEY: "ticket_state",
    WEBHOOK_DEDUP_KEY: "webhook_dedup",
    TELEPORT_AUTH_TOKEN_STORE_KEY: "teleport_auth_token_store",
    SESSION_MANAGER_KEY: "session_manager",
    SILENCE_TASK_KEY: "silence_task",
    WAITING_POLLER_TASK_KEY: "waiting_poller_task",
    MENTION_POLLER_TASK_KEY: "mention_poller_task",
    TRIGGER_TASK_KEY: "trigger_task",
    MENTION_TRACKER_KEY: "mention_tracker",
}


def install_saas_app_context(
    *,
    app: web.Application,
    cfg: Any,
    trigger_queue: Any,
    state: Any,
    webhook_dedup: Any,
    teleport_auth_token_store: Any,
    manager: Any,
) -> None:
    """Install shared SaaS app dependencies under typed aiohttp keys."""
    app[CONFIG_KEY] = cfg
    app[TRIGGER_QUEUE_KEY] = trigger_queue
    app[TICKET_STATE_KEY] = state
    app[WEBHOOK_DEDUP_KEY] = webhook_dedup
    app[TELEPORT_AUTH_TOKEN_STORE_KEY] = teleport_auth_token_store
    app[SESSION_MANAGER_KEY] = manager


def _get(app: web.Application, key: web.AppKey[Any]) -> Any:
    if key in app:
        return app[key]
    return app[_LEGACY_KEYS[key]]


def app_config(app: web.Application) -> Any:
    return _get(app, CONFIG_KEY)


def trigger_queue(app: web.Application) -> Any:
    return _get(app, TRIGGER_QUEUE_KEY)


def ticket_state(app: web.Application) -> Any:
    return _get(app, TICKET_STATE_KEY)


def webhook_dedup(app: web.Application) -> Any:
    return _get(app, WEBHOOK_DEDUP_KEY)


def teleport_auth_token_store(app: web.Application) -> Any:
    return _get(app, TELEPORT_AUTH_TOKEN_STORE_KEY)


def session_manager(app: web.Application) -> Any:
    return _get(app, SESSION_MANAGER_KEY)

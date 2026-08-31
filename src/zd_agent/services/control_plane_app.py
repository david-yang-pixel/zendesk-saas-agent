"""Always-on HTTP control plane for an optional Agent worker."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aiohttp import web

from zd_agent import __version__
from zd_agent.config import get_config
from zd_agent.services.admin_routes import register_admin_routes
from zd_agent.services.agent_lifecycle import (
    AgentWorkerSupervisor,
    ControlPlaneSessionView,
)
from zd_agent.services.app_bootstrap import apply_saved_admin_config
from zd_agent.services.app_context import (
    admin_config,
    agent_lifecycle,
    install_agent_lifecycle,
    install_saas_app_context,
    trigger_queue,
)
from zd_agent.services.browser_relay_readiness import (
    BROWSER_RELAY_READINESS_KEY,
    BrowserRelayReadinessMonitor,
)
from zd_agent.services.durable_trigger_queue import DurableTriggerQueue
from zd_agent.services.instance_fence import verify_active_instance
from zd_agent.services.saas_routes import (
    PUBLIC_PRE_SEND_FRESHNESS_PATH,
    handle_agent_config,
    handle_agent_models,
    handle_agent_tools,
    handle_pre_send_freshness,
    handle_update_agent_config,
)
from zd_agent.services.webhook_dedup import WebhookDedup
from zd_agent.services.zendesk_messaging_webhook import (
    handle_zendesk_messaging_webhook,
)
from zd_agent.services.zendesk_webhook import handle_zendesk_webhook
from zd_agent.trigger.state_store import TicketStateTracker


async def handle_control_plane_health(request: web.Request) -> web.Response:
    lifecycle = agent_lifecycle(request.app)
    queue = trigger_queue(request.app)
    agent = lifecycle.status()
    relay_readiness = request.app[BROWSER_RELAY_READINESS_KEY].status()
    ready = bool(
        agent["status"] == "running"
        or (
            agent["status"] == "stopped"
            and agent["desired_state"] == "stopped"
            and agent["process_running"] is False
        )
    )
    return web.json_response(
        {
            "ok": ready,
            "ready": ready,
            "service": "zd-agent-control-plane",
            "version": __version__,
            "control_plane": {"status": "running"},
            "dependencies": {"zendesk_browser_relay": relay_readiness},
            "agent": {
                "status": agent["status"],
                "desired_state": agent["desired_state"],
                "process_running": agent["process_running"],
                "generation": agent["generation"],
                "active": agent["active"],
                "heartbeat_age_seconds": agent["heartbeat_age_seconds"],
                "worker_ready": agent["worker_ready"],
                "offline_backlog_policy": agent["offline_backlog_policy"],
            },
            "queue": {
                "pending": await queue.pending_count(),
                "dead_letter": await queue.dead_letter_count(),
            },
        },
        status=200 if ready else 503,
    )


def _register_control_routes(app: web.Application, cfg: Any) -> None:
    app.router.add_post(cfg.webhook.zendesk_path, handle_zendesk_webhook)
    app.router.add_post(
        cfg.webhook.zendesk_messaging_path,
        handle_zendesk_messaging_webhook,
    )
    app.router.add_get("/agent/config", handle_agent_config)
    app.router.add_put("/agent/config", handle_update_agent_config)
    app.router.add_get("/agent/models", handle_agent_models)
    app.router.add_get("/agent/tools", handle_agent_tools)
    app.router.add_post("/internal/pre-send-freshness", handle_pre_send_freshness)
    app.router.add_post(PUBLIC_PRE_SEND_FRESHNESS_PATH, handle_pre_send_freshness)
    callback_url = getattr(cfg.public_reply, "freshness_callback_url", "")
    if isinstance(callback_url, str) and callback_url.strip():
        callback_path = urlparse(callback_url).path
        if callback_path and callback_path not in {
            "/internal/pre-send-freshness",
            PUBLIC_PRE_SEND_FRESHNESS_PATH,
        }:
            app.router.add_post(callback_path, handle_pre_send_freshness)
    app.router.add_get("/healthz", handle_control_plane_health)


def create_control_plane_app(cfg: Any | None = None) -> web.Application:
    cfg = cfg or get_config()
    app = web.Application()
    state_dir = apply_saved_admin_config(cfg)
    verify_active_instance(state_dir)
    queue = DurableTriggerQueue(state_dir / "trigger_queue.sqlite3")
    state = TicketStateTracker(state_dir / "tickets.json")
    dedup = WebhookDedup(state_dir / "processed_webhooks.json", max_size=10000)
    supervisor = AgentWorkerSupervisor(state_dir=state_dir, trigger_queue=queue)
    relay_readiness = BrowserRelayReadinessMonitor(
        lifecycle=supervisor,
        readiness_url=cfg.public_reply.browser_relay_readiness_url,
        api_key=cfg.public_reply.browser_relay_api_key,
        interval_seconds=cfg.public_reply.browser_relay_readiness_interval_seconds,
        enabled=bool(cfg.public_reply.enabled and cfg.public_reply.browser_relay_url),
    )
    app[BROWSER_RELAY_READINESS_KEY] = relay_readiness
    session_view = ControlPlaneSessionView(supervisor.store)
    install_saas_app_context(
        app=app,
        cfg=cfg,
        trigger_queue=queue,
        state=state,
        webhook_dedup=dedup,
        manager=session_view,
    )
    install_agent_lifecycle(app=app, lifecycle=supervisor)
    register_admin_routes(app=app, cfg=cfg, state_dir=Path(state_dir))

    def _audit_transition(result: dict[str, Any]) -> None:
        status = str(result.get("status") or "unknown")
        action = str(result.get("action") or "unknown")
        operation_id = str(result.get("operation_id") or "")
        audit_action = f"agent_{action}_{status}"
        audit_store = admin_config(app)
        existing = audit_store.list_audit_logs(limit=500, action=audit_action)
        for item in existing.get("items", []):
            for change in item.get("diff", []):
                new_value = change.get("new")
                if (
                    isinstance(new_value, dict)
                    and operation_id
                    and new_value.get("operation_id") == operation_id
                ):
                    return
        audit_store.audit_event(
            actor=str(result.get("actor") or "system"),
            ip=str(result.get("ip") or "local"),
            action=audit_action,
            reason=str(result.get("reason") or "lifecycle transition"),
            diff=[
                {
                    "path": "agent.lifecycle",
                    "label": "Agent 生命周期最终结果",
                    "group": "部署状态",
                    "old": "pending",
                    "new": {
                        "operation_id": operation_id,
                        "status": status,
                        "generation": result.get("generation") or 0,
                        "error": result.get("error") or "",
                        "blockers": result.get("blockers") or {},
                    },
                    "secret": False,
                    "restart_required": False,
                }
            ],
        )

    supervisor.set_transition_callback(_audit_transition)
    _register_control_routes(app, cfg)

    async def _startup(_app: web.Application) -> None:
        # Probe before Worker initialization. A bad or missing login session
        # closes the old epoch before any queued ticket can be resumed.
        await relay_readiness.check_once()
        await supervisor.initialize()
        relay_readiness.start()

    async def _cleanup(_app: web.Application) -> None:
        await relay_readiness.close()
        await supervisor.close()

    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)
    return app


def run_control_plane_app(cfg: Any | None = None) -> None:
    cfg = cfg or get_config()
    web.run_app(
        create_control_plane_app(cfg),
        host=cfg.webhook.listen_host,
        port=cfg.webhook.listen_port,
    )

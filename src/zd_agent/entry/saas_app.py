"""SaaS unified aiohttp app factory + background tasks."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from aiohttp import web

from zd_agent.config import get_config
from zd_agent.entry.silence_waker import run_silence_waker
from zd_agent.entry.teleport_auth_web import (
    TokenStore,
    handle_teleport_auth_get,
    handle_teleport_auth_issue,
    handle_teleport_auth_post,
)
from zd_agent.entry.webhook_dedup import WebhookDedup
from zd_agent.entry.zendesk_webhook import handle_zendesk_webhook
from zd_agent.log import get_logger
from zd_agent.trigger.state_store import TicketStateTracker

logger = get_logger(module="entry.saas_app")


async def _healthz(request: web.Request) -> web.Response:
    return web.Response(status=200, text="ok")


def _customer_public_comments(ticket: dict, comments: list[dict]) -> list[dict]:
    """筛出工单请求者（客户）发的 public 评论，按时间升序。"""
    requester_id = ticket.get("requester_id")
    out = []
    for c in comments:
        if not c.get("public"):
            continue
        if c.get("author_id") != requester_id:
            continue
        out.append(c)
    out.sort(key=lambda c: c.get("created_at", ""))
    return out


async def _fetch_customer_messages(ticket_id: int) -> tuple[list[dict], dict | None]:
    """拉工单 + 客户 public 评论。返回 (comments, ticket_dict) 或 ([], None) 失败时。"""
    from zd.client import ZendeskClient
    import asyncio as _asyncio

    def _sync():
        client = ZendeskClient()
        t = client.get_ticket(ticket_id).get("ticket", {})
        c = client.get_ticket_comments(ticket_id).get("comments", [])
        return t, c

    try:
        ticket, comments = await _asyncio.to_thread(_sync)
    except Exception as e:
        logger.error("trigger_fetch_ticket_failed",
                     ticket_id=ticket_id, error=str(e))
        return [], None
    return _customer_public_comments(ticket, comments), ticket


async def _trigger_consumer(
    queue: asyncio.Queue,
    manager: Any,
    state: "TicketStateTracker",
) -> None:
    """Consume trigger_queue with TriggerDecider logic:
    - webhook 来的：判断客户消息，是寒暄/信息不足 → 静默；是问题 → 进入 5min 静默期
    - silence_waker 来的（静默期到期）：直接 dispatch
    - mention 触发：直接 dispatch（绕过判定）
    """
    from datetime import datetime, timezone, timedelta
    from zd_agent.config import EnvSettings
    from zd_agent.trigger.decider import classify_messages
    from zd_agent.trigger.decision_log import log_decision
    from zd_agent.trigger.state_store import TicketState

    cfg = get_config()
    env = EnvSettings()
    log_path = (
        Path(cfg.resources.base_dir)
        / cfg.resources.state_dir
        / "logs"
        / "trigger_log.jsonl"
    )

    while True:
        msg = await queue.get()
        try:
            ticket_id = int(msg["ticket_id"])
            source = msg.get("source", "unknown")

            # 1. silence_waker 唤醒 or mention 触发 → 跳过判定，直接 dispatch
            if source in ("silence_waker", "mention"):
                logger.info("trigger_dispatch_direct",
                            ticket_id=ticket_id, source=source)
                await manager.dispatch(
                    ticket_id, dry_run=False,
                    trigger_type=f"webhook-{source}",
                )
                continue

            # 2. 拉客户消息
            customer_msgs, ticket_info = await _fetch_customer_messages(ticket_id)
            if ticket_info is None:
                continue  # 拉取失败，skip

            if not customer_msgs:
                # 工单刚创建，客户还没发消息 → 不处理，等 Change 事件
                logger.info("trigger_skip_no_customer_msg", ticket_id=ticket_id)
                log_decision(log_path=log_path, ticket_id=ticket_id,
                             verdict="no_customer_message", action="skip")
                continue

            # 3. 幂等：同 comment_id 已处理过则 skip
            latest = customer_msgs[-1]
            latest_cid = latest.get("id")
            existing = state.get(ticket_id)
            if existing and existing.last_customer_comment_id == latest_cid:
                logger.info(
                    "trigger_skip_duplicate_comment",
                    ticket_id=ticket_id, comment_id=latest_cid,
                    current_state=existing.state,
                )
                continue

            # 4. LLM 预判
            bodies = [c.get("body", "") for c in customer_msgs]
            verdict = await classify_messages(
                messages=bodies,
                model=cfg.trigger_decider.llm_model,
                api_key=env.openai_api_key,
            )
            logger.info(
                "trigger_verdict",
                ticket_id=ticket_id, verdict=verdict.verdict,
                completeness=verdict.completeness, reason=verdict.reason[:100],
            )

            now = datetime.now(timezone.utc)
            latest_ts_str = latest.get("created_at", "")
            try:
                latest_ts = datetime.fromisoformat(
                    latest_ts_str.replace("Z", "+00:00")
                )
            except Exception:
                latest_ts = now

            # 5. 根据 verdict 更新 state
            if verdict.verdict == "substantial_issue":
                # 进入静默期，等 N 分钟客户不再发新消息才处理
                silence_min = cfg.trigger_decider.silence_minutes_default
                window_end = now + timedelta(minutes=silence_min)
                state.set(ticket_id, TicketState(
                    state="in_silence_window",
                    first_seen=existing.first_seen if existing else now,
                    last_customer_comment_id=latest_cid,
                    last_comment_ts=latest_ts,
                    last_eval_at=now,
                    silence_window_end=window_end,
                    last_verdict=verdict.verdict,
                ))
                log_decision(
                    log_path=log_path, ticket_id=ticket_id,
                    verdict=verdict.verdict, completeness=verdict.completeness,
                    silence_min=silence_min, action="enter_silence",
                )
                logger.info(
                    "trigger_enter_silence",
                    ticket_id=ticket_id, silence_min=silence_min,
                    window_end=window_end.isoformat(),
                )
            else:
                # greeting / insufficient_info → 等下一条客户消息
                state.set(ticket_id, TicketState(
                    state="waiting_for_issue",
                    first_seen=existing.first_seen if existing else now,
                    last_customer_comment_id=latest_cid,
                    last_comment_ts=latest_ts,
                    last_eval_at=now,
                    last_verdict=verdict.verdict,
                ))
                log_decision(
                    log_path=log_path, ticket_id=ticket_id,
                    verdict=verdict.verdict, completeness=verdict.completeness,
                    action="wait_for_issue",
                )
                logger.info(
                    "trigger_waiting_for_issue",
                    ticket_id=ticket_id, verdict=verdict.verdict,
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
    teleport_auth_token_store = TokenStore()

    app = web.Application()
    app["config"] = cfg
    app["trigger_queue"] = trigger_queue
    app["ticket_state"] = state
    app["webhook_dedup"] = webhook_dedup
    app["teleport_auth_token_store"] = teleport_auth_token_store
    app["session_manager"] = manager

    app.router.add_post(cfg.webhook.zendesk_path, handle_zendesk_webhook)
    app.router.add_get("/healthz", _healthz)
    # Teleport 认证中转页：watchdog 生成 token 后推飞书，用户点开这个 URL
    app.router.add_get("/teleport-auth", handle_teleport_auth_get)
    app.router.add_post("/teleport-auth", handle_teleport_auth_post)
    # Internal: watchdog 调用 mint token（nginx 不暴露 /internal/*）
    app.router.add_post("/internal/teleport-auth/issue", handle_teleport_auth_issue)

    async def _on_startup(_app: web.Application) -> None:
        _app["silence_task"] = asyncio.create_task(
            run_silence_waker(
                state=state,
                trigger_queue=trigger_queue,
                scan_interval_seconds=cfg.silence_waker.scan_interval_seconds,
            )
        )
        _app["trigger_task"] = asyncio.create_task(
            _trigger_consumer(trigger_queue, manager, state),
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

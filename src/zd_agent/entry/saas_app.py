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
    """筛出工单请求者（客户）发的 public 评论，按时间升序。

    每条评论里把图片附件抽出来记到 ``image_urls`` 字段，便于上层喂给 vision LLM。
    """
    requester_id = ticket.get("requester_id")
    out = []
    for c in comments:
        if not c.get("public"):
            continue
        if c.get("author_id") != requester_id:
            continue
        # 抽图片附件
        image_urls: list[str] = []
        for att in c.get("attachments", []) or []:
            ctype = (att.get("content_type") or "").lower()
            if ctype.startswith("image/"):
                url = att.get("content_url") or att.get("mapped_content_url")
                if url:
                    image_urls.append(url)
        c = {**c, "image_urls": image_urls}
        out.append(c)
    out.sort(key=lambda c: c.get("created_at", ""))
    return out


def _strip_html(s: str) -> str:
    import re
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _messaging_customer_messages(events: list[dict]) -> list[dict]:
    """从 conversation_log events 提取 end-user（客户）发的消息事件。

    Zendesk messaging conversation_log 的 content 结构：
      - type=text   → text in content["text"]
      - type=html   → html in content["body"]（剥 HTML）
      - type=formResponse → 表单提交，含 fields；用 text_fallback 或拼字段
    """
    out: list[dict] = []
    for e in events:
        author = e.get("author") or {}
        if author.get("type") != "end-user":
            continue
        content = e.get("content") or {}
        ctype = content.get("type", "")
        body = ""
        image_urls: list[str] = []
        if ctype == "text":
            body = content.get("text", "") or ""
        elif ctype == "html":
            body = _strip_html(content.get("body", "") or "")
        elif ctype == "formResponse":
            # 表单 fallback 文本，或拼装字段
            body = content.get("text_fallback") or ""
            if not body:
                fields = content.get("fields") or []
                parts = []
                for f in fields:
                    label = f.get("label", "") or f.get("name", "")
                    value = f.get("value", "") or f.get("text", "")
                    if label or value:
                        parts.append(f"{label}: {value}".strip(": "))
                body = "\n".join(parts)
        elif ctype == "image":
            # 客户发了截图 → 记录作为消息（含文件名 + URL 让 LLM 知晓）
            alt = content.get("alt_text") or "image"
            url = content.get("media_url", "")
            body = f"[客户附图 / Customer attached image: {alt}]"
            if url:
                body += f"\nURL: {url}"
                image_urls.append(url)
        elif ctype in ("file", "video"):
            alt = content.get("alt_text") or content.get("type")
            url = content.get("media_url", "")
            body = f"[客户附件 / Customer attached {ctype}: {alt}]"
            if url:
                body += f"\nURL: {url}"
        else:
            # 未知类型 — 留个标记
            body = f"[unsupported message content type: {ctype}]"
        body = body.strip()
        if not body:
            continue
        out.append({
            "id": e.get("id"),
            "author_id": author.get("zen:support:user_id"),
            "created_at": e.get("created_at"),
            "body": body,
            "public": True,
            "image_urls": image_urls,
        })
    return out


# Vision: image download config
_IMG_MAX_BYTES = 4 * 1024 * 1024  # 单图 4 MiB 上限
_IMG_TOTAL_BUDGET = 6  # 一次判定最多送几张图（控制 token + 速度）


async def _download_image_data_url(
    url: str,
    *,
    timeout: float = 15.0,
    max_bytes: int = _IMG_MAX_BYTES,
) -> str | None:
    """下载图片返回 ``data:image/...;base64,...`` URL。

    Zendesk 的 ``content_url`` 需要 basic auth；Smooch CDN 的 ``media_url`` 一般匿名可访问。
    这里先尝试匿名；遇到 401/403 时若是 zendesk.com 域则用配置里的邮箱+token 重试。
    """
    import base64
    import httpx
    from zd_agent.config import EnvSettings, get_config

    cfg = get_config()
    env = EnvSettings()
    is_zd = "zendesk.com" in url
    auth = None
    if is_zd and env.zendesk_email and env.zendesk_api_token:
        auth = (f"{env.zendesk_email}/token", env.zendesk_api_token)

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            # 先尝试无 auth（Zendesk 的 content_url 多数也接受预签名重定向）
            resp = await client.get(url)
            if resp.status_code in (401, 403) and auth is not None:
                resp = await client.get(url, auth=auth)
            if resp.status_code != 200:
                logger.warning(
                    "vision_image_download_http_error",
                    url=url[:120], status=resp.status_code,
                )
                return None
            data = resp.content
            if len(data) > max_bytes:
                logger.warning(
                    "vision_image_too_large",
                    url=url[:120], size=len(data), limit=max_bytes,
                )
                return None
            ctype = (resp.headers.get("Content-Type") or "image/png").split(";")[0].strip()
            if not ctype.startswith("image/"):
                # 兜底
                ctype = "image/png"
            b64 = base64.b64encode(data).decode("ascii")
            return f"data:{ctype};base64,{b64}"
    except Exception as e:
        logger.warning(
            "vision_image_download_failed",
            url=url[:120], error=str(e),
        )
        return None


async def _collect_recent_image_data_urls(
    customer_msgs: list[dict],
    *,
    lookback: int = 5,
    budget: int = _IMG_TOTAL_BUDGET,
) -> list[str]:
    """从最后 ``lookback`` 条客户消息里抽图片，下载并编码为 data URL。

    并发下载，按消息时间顺序保留最多 ``budget`` 张。
    """
    import asyncio as _asyncio

    recent = customer_msgs[-lookback:] if customer_msgs else []
    urls: list[str] = []
    for m in recent:
        for u in m.get("image_urls") or []:
            urls.append(u)
            if len(urls) >= budget:
                break
        if len(urls) >= budget:
            break

    if not urls:
        return []

    results = await _asyncio.gather(
        *[_download_image_data_url(u) for u in urls],
        return_exceptions=False,
    )
    return [r for r in results if r]


async def _fetch_customer_messages(ticket_id: int) -> tuple[list[dict], dict | None]:
    """拉工单 + 客户消息（含 messaging 渠道）。

    普通工单 → comments.json 里的 public 客户评论。
    Messaging 渠道（via.channel=native_messaging）→ 额外查 conversation_log
      把 end-user 事件作为客户消息（comments 里只有系统占位）。
    """
    from zd.client import ZendeskClient
    import asyncio as _asyncio

    def _sync():
        client = ZendeskClient()
        t = client.get_ticket(ticket_id).get("ticket", {})
        c = client.get_ticket_comments(ticket_id).get("comments", [])
        events: list[dict] = []
        via_channel = (t.get("via") or {}).get("channel", "")
        if via_channel == "native_messaging":
            try:
                log = client.get_ticket_conversation_log(ticket_id, page_size=100)
                events = log.get("events", []) or []
            except Exception as e:
                logger.warning(
                    "conversation_log_fetch_failed",
                    ticket_id=ticket_id, error=str(e),
                )
        return t, c, events

    try:
        ticket, comments, events = await _asyncio.to_thread(_sync)
    except Exception as e:
        logger.error("trigger_fetch_ticket_failed",
                     ticket_id=ticket_id, error=str(e))
        return [], None

    msgs = _customer_public_comments(ticket, comments)
    if events:
        msgs.extend(_messaging_customer_messages(events))
    msgs.sort(key=lambda c: c.get("created_at", ""))
    logger.info(
        "trigger_fetch_messages_ok",
        ticket_id=ticket_id,
        comments_count=len(comments),
        events_count=len(events),
        customer_msgs_count=len(msgs),
        via=(ticket.get("via") or {}).get("channel"),
    )
    return msgs, ticket


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

            # 4. LLM 预判（如有客户截图，下载并喂给 vision 模型）
            bodies = [c.get("body", "") for c in customer_msgs]
            image_data_urls = await _collect_recent_image_data_urls(customer_msgs)
            verdict = await classify_messages(
                messages=bodies,
                model=cfg.trigger_decider.llm_model,
                api_key=env.openai_api_key,
                image_data_urls=image_data_urls,
            )
            logger.info(
                "trigger_verdict",
                ticket_id=ticket_id, verdict=verdict.verdict,
                completeness=verdict.completeness,
                reason=verdict.reason[:100],
                images_used=len(image_data_urls),
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

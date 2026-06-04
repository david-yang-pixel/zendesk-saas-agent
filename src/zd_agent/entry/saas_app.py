"""SaaS unified aiohttp app factory + background tasks."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from aiohttp import web

from zd_agent.config import get_config
from zd_agent.entry.mention_poller import run_mention_poller
from zd_agent.entry.human_takeover import is_human_assignee
from zd_agent.entry.silence_waker import run_silence_waker
from zd_agent.entry.waiting_poller import run_waiting_poller
from zd_agent.entry.teleport_auth_web import (
    TokenStore,
    handle_teleport_auth_get,
    handle_teleport_auth_issue,
    handle_teleport_auth_post,
)
from zd_agent.entry.webhook_dedup import WebhookDedup
from zd_agent.entry.zendesk_webhook import handle_zendesk_webhook
from zd_agent.entry.zendesk_messaging_webhook import handle_zendesk_messaging_webhook
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


_SC_ATTACHMENT_RE = __import__("re").compile(r"/sc/attachments/v2/([^/]+)/")


async def _download_image_data_url(
    url: str,
    *,
    timeout: float = 15.0,
    max_bytes: int = _IMG_MAX_BYTES,
) -> str | None:
    """下载图片返回 ``data:image/...;base64,...`` URL。

    三种 Zendesk 附件 URL 都要支持：
    1. Smooch CDN ``media_url``（匿名可下） — 第一次直接 GET 即可
    2. Zendesk Help Center ``content_url`` — 401/403 时用 email/token basic auth 重试
    3. **Sunshine ``/sc/attachments/v2/{att_id}/...``**（messaging 工单的截图）
       直接 GET 永远 401。要绕路：
       ``GET /api/v2/ticket_attachments/{att_id}/content`` → 302 → 预签名 URL → 下载
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

    async def _from_response(resp: httpx.Response) -> str | None:
        if resp.status_code != 200:
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
            ctype = "image/png"
        return f"data:{ctype};base64,{base64.b64encode(data).decode('ascii')}"

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            # 路径 1：直接 GET（Smooch CDN / 预签名 URL 走这条）
            resp = await client.get(url)
            if resp.status_code == 200:
                return await _from_response(resp)

            # 路径 2：401/403 + Zendesk 域名 → 试 basic auth
            if resp.status_code in (401, 403) and auth is not None:
                resp = await client.get(url, auth=auth)
                if resp.status_code == 200:
                    return await _from_response(resp)

            # 路径 3：仍然 401 + URL 是 /sc/attachments/v2/{att_id}/...
            #         → 走 ticket_attachments/{id}/content 重定向（messaging 截图必经）
            sc_match = _SC_ATTACHMENT_RE.search(url)
            if (
                resp.status_code in (401, 403)
                and sc_match
                and is_zd
                and auth is not None
            ):
                att_id = sc_match.group(1)
                subdomain = (env.zendesk_subdomain or cfg.zendesk.subdomain or "").strip()
                if not subdomain:
                    # 从 url 抽（langgenius.zendesk.com → langgenius）
                    import re as _re
                    sd_match = _re.search(r"https://([^.]+)\.zendesk\.com", url)
                    if sd_match:
                        subdomain = sd_match.group(1)
                if subdomain:
                    redir_url = f"https://{subdomain}.zendesk.com/api/v2/ticket_attachments/{att_id}/content"
                    redir_resp = await client.get(
                        redir_url, auth=auth, follow_redirects=False,
                    )
                    if redir_resp.status_code in (301, 302, 303, 307):
                        loc = redir_resp.headers.get("Location", "")
                        if loc:
                            # 预签名 URL 自带 token，匿名 GET 即可
                            final = await client.get(loc)
                            if final.status_code == 200:
                                return await _from_response(final)
                            logger.warning(
                                "vision_sc_redirect_dl_failed",
                                url=url[:120], status=final.status_code,
                            )
                            return None
                    logger.warning(
                        "vision_sc_redirect_unexpected",
                        url=url[:120],
                        attempted=redir_url[:120],
                        status=redir_resp.status_code,
                    )

            logger.warning(
                "vision_image_download_http_error",
                url=url[:120], status=resp.status_code,
            )
            return None
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


# Agent 发出去的 public reply / Internal Note 都会带这个 footer 文本（见
# zendesk_public_reply._FOOTER_STANDARD / _FOOTER_HANDOFF / _FOOTER_CLOSED）。
# 用作"区分 agent 自动发 vs yonggang 通过 Zendesk UI 手动发"的标记 —
# 因为两者 author_id 都是 agent_user_id，无法靠 author 区分。
_AGENT_REPLY_MARKER = "Dify SaaS Agent"


async def _detect_human_takeover(
    *,
    ticket_id: int,
    ticket_info: dict,
    agent_user_id: int,
) -> tuple[bool, str]:
    """检测此工单是否已有受托人/人工 SE 接手 → 若是，应停止 agent 处理。

    检测三种情况：
    1. ``assignee_id`` 非空（Zendesk 已有受托人）
    2. 最后一条 public comment 的 author **不是** agent_user_id → 其他 SE 发的
    3. 最后一条 public comment author **是** agent_user_id 但 body **不含**
       agent footer 标记 → yonggang 在 Zendesk UI 手动发的（不是 agent 自动）

    Returns:
        (detected, reason)。detected=False 时 reason=""。

    检测失败（fetch 拉不到 comments 等）→ 返回 (False, "") 保守放过。
    """
    # Case 1: assignee 检查
    assignee_id = ticket_info.get("assignee_id")
    if is_human_assignee(
        assignee_id,
        agent_user_id=agent_user_id,
    ):
        return True, f"assignee={assignee_id}（工单已有受托人）"

    if not agent_user_id:
        # 没配 agent_user_id → 无法区分评论作者，保守放过评论检测
        return False, ""

    # Case 2/3: 看最后一条 public comment
    try:
        from zd.client import ZendeskClient
        import asyncio as _asyncio
        comments_resp = await _asyncio.to_thread(
            lambda: ZendeskClient().get_ticket_comments(ticket_id),
        )
    except Exception as e:
        logger.warning(
            "takeover_fetch_comments_failed",
            ticket_id=ticket_id, error=str(e)[:200],
        )
        return False, ""  # 拉不到就保守放过

    comments = (comments_resp or {}).get("comments") or []
    # 从尾往前找最后一条 public comment
    last_public = None
    for c in reversed(comments):
        if c.get("public"):
            last_public = c
            break

    if last_public is None:
        # 没 public 回复过 → 不算 takeover
        return False, ""

    last_author = last_public.get("author_id")
    last_body = last_public.get("body") or ""

    # Case 2: 其他 SE 发的 public（author 不是 agent_user_id 且不是客户 requester）
    requester_id = ticket_info.get("requester_id")
    try:
        if (
            last_author
            and int(last_author) != int(agent_user_id)
            and (not requester_id or int(last_author) != int(requester_id))
        ):
            return True, f"last public reply by other SE (author={last_author})"
    except (TypeError, ValueError):
        pass

    # Case 3: author = agent_uid 但 body 不含 agent footer → yonggang 手动发的
    try:
        if (
            last_author
            and int(last_author) == int(agent_user_id)
            and _AGENT_REPLY_MARKER not in last_body
        ):
            return True, "last public reply from agent_uid but missing agent footer (yonggang 手动发?)"
    except (TypeError, ValueError):
        pass

    return False, ""


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


def _form_data_from_msgs(customer_msgs: list[dict]) -> dict:
    """Pull the formResponse 'fields' from messaging customer messages, if any.

    SaaS messaging tickets always have a formResponse first event with
    Name / Email / Workspace ID. This helper digs it out for downstream use
    (nudge language detection, future prefetch, ...).

    Returns empty dict if no formResponse or no recognisable fields found.
    """
    out: dict = {}
    for m in customer_msgs:
        body = m.get("body") or ""
        # 不是结构化数据，但 nudge 语言检测够用：从 body 找名字行
        # body 形如 "Name: 张三\nEmail: ...\nWorkspace ID: ..."
        if "Name:" in body or "Workspace ID:" in body:
            for line in body.splitlines():
                if ":" not in line:
                    continue
                k, _, v = line.partition(":")
                k = k.strip().lower()
                v = v.strip()
                if not v:
                    continue
                if k == "name":
                    out["name"] = v
                elif k == "email":
                    out["email"] = v
                elif k in ("workspace id", "wid"):
                    out["workspace_id"] = v
            break  # 只取第一条 formResponse
    return out


async def _handle_silence_expired_ask(
    *,
    ticket_id: int,
    state: "TicketStateTracker",
    log_path: Path,
    nudge_message_fn,
) -> None:
    """Silence 期到期 + verdict 非 substantial 时的分支处理。

    enable_public_nudge=true  → 用客户语言给工单回一条 public ask 消息
    enable_public_nudge=false → 推飞书内部告警代替（保守默认）
    任一情况都设 nudge_sent_at 防重复，并保持 state=waiting_for_issue。
    """
    from datetime import datetime, timezone
    from zd_agent.trigger.decision_log import log_decision

    cfg = get_config()
    rec = state.get(ticket_id)
    if rec is None:
        logger.warning("silence_expired_ask_no_state", ticket_id=ticket_id)
        return
    if rec.nudge_sent_at is not None:
        logger.info("silence_expired_ask_already_sent",
                    ticket_id=ticket_id,
                    sent_at=rec.nudge_sent_at.isoformat())
        return

    # 拉客户消息只为提取 form_data 做语言检测；这里允许失败，失败用英文兜底
    customer_msgs, _ticket = await _fetch_customer_messages(ticket_id)
    form = _form_data_from_msgs(customer_msgs or [])
    customer_name = form.get("name", "")
    lang, body = nudge_message_fn(customer_name=customer_name)

    now = datetime.now(timezone.utc)

    if cfg.trigger_decider.enable_public_nudge:
        # 真的发到工单 public comment
        # 同时打 AGENT_ASKED_TAG —— 因为 reply_ticket(public=True) 会让 Zendesk
        # 自动把 status 从 new 翻成 open，poller 默认 status=new only 会失去这条
        # 工单的可见性。tag 让 poller 例外保留这条工单。
        try:
            from zd.client import ZendeskClient
            from zd_agent.entry.waiting_poller import AGENT_ASKED_TAG
            import asyncio as _asyncio

            def _send_reply_and_tag():
                client = ZendeskClient()
                client.reply_ticket(ticket_id, body=body, public=True)
                # add_ticket_tags 是增量操作，不会覆盖已有 tags
                try:
                    client.add_ticket_tags(ticket_id, [AGENT_ASKED_TAG])
                except Exception as e:
                    logger.warning(
                        "agent_asked_tag_add_failed",
                        ticket_id=ticket_id, error=str(e),
                    )

            await _asyncio.to_thread(_send_reply_and_tag)
            logger.info(
                "public_nudge_sent",
                ticket_id=ticket_id, lang=lang, body_chars=len(body),
                tagged=True,
            )
            log_decision(
                log_path=log_path, ticket_id=ticket_id,
                verdict=rec.last_verdict or "",
                action="public_nudge_sent",
                extra={"lang": lang, "tagged": True},
            )
        except Exception as e:
            logger.error(
                "public_nudge_send_failed",
                ticket_id=ticket_id, error=str(e),
            )
            # 不设 nudge_sent_at，下个 silence cycle 会重试（罕见）
            return
    else:
        # feature 关：飞书内部告警，等真人介入
        try:
            from zd_agent.integrations.feishu.client import FeishuClient
            fcfg = cfg.feishu
            if fcfg.webhook_url:
                client = FeishuClient(
                    webhook_url=fcfg.webhook_url,
                    webhook_secret=fcfg.webhook_secret,
                )
                await client.send_alert(
                    title=f"Ticket #{ticket_id} 信息不足等待处理",
                    message=(
                        f"工单 #{ticket_id} 静默期已结束，但客户消息判为 "
                        f"`{rec.last_verdict or 'unknown'}`，"
                        "未达到处理标准。enable_public_nudge=false，"
                        "未给客户发 ask 消息。请人工介入或确认是否打开 nudge。"
                    ),
                    urgent=False,
                )
            logger.info(
                "silence_expired_ask_feishu_alert",
                ticket_id=ticket_id, verdict=rec.last_verdict,
            )
            log_decision(
                log_path=log_path, ticket_id=ticket_id,
                verdict=rec.last_verdict or "",
                action="silence_expired_feishu_alert",
            )
        except Exception as e:
            logger.warning(
                "silence_expired_ask_feishu_failed",
                ticket_id=ticket_id, error=str(e),
            )

    rec.nudge_sent_at = now
    state.set(ticket_id, rec)


async def _send_ask_template(
    *,
    ticket_id: int,
    verdict_kind: str,
    customer_msgs: list[dict],
    ticket_info: dict,
    ask_count: int,
    source: str,
) -> bool:
    """发公开 ask 模板给客户索取问题信息（M6 实施版本）。

    流程：
    1. 从 formResponse 抽 customer_name
    2. 按 verdict_kind + ask_count 选模板（A/B/C/D）
    3. 调 send_public_reply 直接送出（含 footer + kill switch 检查）

    返回值：True 表示"已发"，False 表示"未发（kill switch / 出错）"。
    返回 False 时 trigger_consumer **不**累加 ask_count（避免空跑吞 quota）。

    设计：
    - verdict_kind ∈ {no_message, greeting, insufficient_info} → 模板 A/B/C
    - ask_count >= 1 → 一律升级模板 D（措辞更直接）
    - 客户语言永远 English（决策见 2026-05-13 讨论）
    """
    from zd_agent.config import EnvSettings
    from zd_agent.prompts.public_reply.templates import build_ask_body
    from zd_agent.tools.zendesk_public_reply import send_public_reply

    cfg = get_config()
    env = EnvSettings()

    # 1. 从 formResponse 抽客户名
    form = _form_data_from_msgs(customer_msgs or [])
    customer_name = (form.get("name", "") or "").strip()

    # 2. 选模板 + 生成 body
    customer_bodies = [m.get("body", "") for m in (customer_msgs or [])]
    try:
        body, template_name = await build_ask_body(
            verdict_kind=verdict_kind,
            ask_count=ask_count,
            customer_name=customer_name,
            customer_messages=customer_bodies,
            model=cfg.trigger_decider.llm_model,
            api_key=env.openai_api_key,
        )
    except Exception as e:
        logger.error(
            "ask_template_build_failed",
            ticket_id=ticket_id, verdict_kind=verdict_kind,
            error=str(e)[:200], exc_info=True,
        )
        return False

    logger.info(
        "ask_template_built",
        ticket_id=ticket_id,
        verdict_kind=verdict_kind,
        ask_count=ask_count,
        template=template_name,
        customer_name=customer_name or "(none)",
        body_len=len(body),
        source=source,
    )

    # 3. 发送（reply_type="ask"，footer 由 send_public_reply 内部追加）
    success, msg = await send_public_reply(
        ticket_id=ticket_id,
        body=body,
        reply_type="ask",
    )
    if not success:
        logger.warning(
            "ask_template_send_failed",
            ticket_id=ticket_id, template=template_name,
            error=msg[:200], source=source,
        )
        return False

    logger.info(
        "ask_template_sent",
        ticket_id=ticket_id, template=template_name,
        ask_count_now=ask_count + 1,
        source=source,
    )
    return True


async def _set_escalated_to_human(
    *,
    ticket_id: int,
    reason: str,
    existing,
    state: "TicketStateTracker",
    log_path: Path,
    source: str,
    now,
    verdict_str: str,
    latest_cid,
    latest_ts,
    completeness: int,
    customer_msgs: list[dict] | None = None,
    customer_phrase: str = "",
    agent_last_reply: str = "",
    extra_log: dict | None = None,
) -> None:
    """把工单标记为 escalated_to_human + 触发升级副作用。

    state 写入是**强制**的（哪怕副作用全失败也保证 state 正确），3 个副作用是
    **best-effort**（任一失败不影响其它，全部记 log）：

    1. 发 public handoff 模板 E 给客户（仅 agent 主动升级场景）
    2. 飞书推送 ticket_escalate 卡片给 SE
    3. Zendesk 工单加 tag ``escalated-to-human``

    public handoff 受 ``cfg.public_reply.enabled`` 总开关门控，且已有人工/受托人
    接手（reason=human_takeover_detected）时不再给客户发自动回复。
    """
    from zd_agent.trigger.decision_log import log_decision
    from zd_agent.trigger.state_store import TicketState

    # ───── 1. 写 state（强制）─────
    state.set(ticket_id, TicketState(
        state="escalated_to_human",
        first_seen=existing.first_seen if existing else now,
        last_customer_comment_id=latest_cid,
        last_comment_ts=latest_ts,
        last_eval_at=now,
        last_verdict=verdict_str,
        ask_count=existing.ask_count if existing else 0,
        escalated_at=now,
        escalated_reason=reason,
        # 升级后这些 alert 状态不再相关，但保留 nudge_sent_at / abandoned_alert
        # 等便于事后审计
        nudge_sent_at=existing.nudge_sent_at if existing else None,
        alert_sent_at=existing.alert_sent_at if existing else None,
        abandoned_alert_sent_at=(
            existing.abandoned_alert_sent_at if existing else None
        ),
        last_agent_finish_ts=(
            existing.last_agent_finish_ts if existing else None
        ),
    ))

    log_payload = {"source": source, "reason": reason}
    if extra_log:
        log_payload.update(extra_log)
    log_decision(
        log_path=log_path, ticket_id=ticket_id,
        verdict=verdict_str, completeness=completeness,
        action="escalated_to_human",
        extra=log_payload,
    )
    logger.info(
        "trigger_escalated_to_human",
        ticket_id=ticket_id, reason=reason, source=source,
        verdict=verdict_str,
    )

    # M8 metric: 客户主动喊 human 才算 "customer_requested_human"
    # （max_asks_reached / high_risk_* 是 agent 主动放弃，不算客户拒绝）
    if reason == "customer_requested":
        from zd_agent.observability.metrics import emit_customer_requested_human
        emit_customer_requested_human(
            ticket_id=ticket_id,
            source=source,
            customer_phrase=customer_phrase,
            ask_count_so_far=existing.ask_count if existing else 0,
        )

    # ───── 2. 发 handoff 模板给客户（kill switch 受控，best-effort）─────
    if reason == "human_takeover_detected":
        logger.info(
            "escalated_handoff_reply_skipped_human_takeover",
            ticket_id=ticket_id,
            reason=reason,
        )
    else:
        try:
            from zd_agent.prompts.public_reply.templates import handoff_template
            from zd_agent.tools.zendesk_public_reply import send_public_reply

            form = _form_data_from_msgs(customer_msgs or [])
            customer_name = (form.get("name", "") or "").strip()
            handoff_body = handoff_template(customer_name=customer_name)

            success, msg = await send_public_reply(
                ticket_id=ticket_id, body=handoff_body, reply_type="handoff",
            )
            if success:
                logger.info(
                    "escalated_handoff_reply_sent",
                    ticket_id=ticket_id, reason=reason,
                )
            else:
                logger.warning(
                    "escalated_handoff_reply_failed",
                    ticket_id=ticket_id, error=msg[:200],
                )
        except Exception as e:
            logger.warning(
                "escalated_handoff_reply_exception",
                ticket_id=ticket_id, error=str(e)[:200], exc_info=True,
            )

    # ───── 3. 飞书推送 ticket_escalate 卡片（best-effort）─────
    try:
        from zd_agent.config import get_config as _gc
        from zd_agent.integrations.feishu.client import FeishuClient

        cfg = _gc()
        if cfg.feishu.webhook_url:
            zendesk_url = (
                f"https://{cfg.zendesk.subdomain}.zendesk.com"
                f"/agent/tickets/{ticket_id}"
                if cfg.zendesk.subdomain else ""
            )
            # 构造 reason 内容（含客户原话 + agent 上次回复摘要）
            reason_lines = [f"**升级原因**: {reason}"]
            if customer_phrase:
                reason_lines.append(
                    f"**客户原话**: {customer_phrase[:200]}",
                )
            if agent_last_reply:
                reason_lines.append(
                    f"**Agent 上次回复**: {agent_last_reply[:200]}",
                )
            reason_lines.append(f"**source**: {source}")

            client = FeishuClient(
                webhook_url=cfg.feishu.webhook_url,
                webhook_secret=cfg.feishu.webhook_secret,
            )
            await client.send_ticket_escalate(
                ticket_id=str(ticket_id),
                reason="\n".join(reason_lines),
                zendesk_url=zendesk_url,
            )
            logger.info(
                "escalated_feishu_pushed",
                ticket_id=ticket_id, reason=reason,
            )
        else:
            logger.debug(
                "escalated_feishu_skipped_no_webhook", ticket_id=ticket_id,
            )
    except Exception as e:
        logger.warning(
            "escalated_feishu_failed",
            ticket_id=ticket_id, error=str(e)[:200], exc_info=True,
        )

    # ───── 4. Zendesk 加 tag escalated-to-human（best-effort）─────
    try:
        from zd.client import ZendeskClient
        import asyncio as _asyncio

        def _add_tag():
            ZendeskClient().add_ticket_tags(
                ticket_id, ["escalated-to-human"],
            )

        await _asyncio.to_thread(_add_tag)
        logger.info(
            "escalated_zendesk_tag_added", ticket_id=ticket_id,
        )
    except Exception as e:
        logger.warning(
            "escalated_zendesk_tag_failed",
            ticket_id=ticket_id, error=str(e)[:200], exc_info=True,
        )


async def _handle_post_verdict_dispatch(
    *,
    ticket_id: int,
    verdict,
    customer_msgs: list[dict],
    ticket_info: dict,
    latest_cid,
    latest_ts,
    existing,
    state: "TicketStateTracker",
    log_path: Path,
    source: str,
    now,
    cfg,
) -> None:
    """根据 verdict 派发到对应 state transition + 副作用动作。

    分发优先级（高 → 低，分别对应客户类不同行为）：

    1. ``request_human``                       → escalate (reason=customer_requested)
    2. ``self_resolved``                       → state=processed（工单收尾）
    3. ``substantial_issue`` + high_risk       → escalate (reason=high_risk_<cat>)
    4. ``substantial_issue`` + safe            → enter silence（agent 后续处理）
    5. ``no_message``/``greeting``/``insufficient_info`` + ask_count >= max
                                               → escalate (reason=max_asks_reached)
    6. ``no_message``/``greeting``/``insufficient_info`` + ask_count < max
                                               → send ask + ask_count++
                                                 (M6 才真发，M4 stub)
    """
    from datetime import timedelta
    from zd_agent.trigger.decision_log import log_decision
    from zd_agent.trigger.state_store import TicketState

    verdict_str = verdict.verdict
    ask_count_now = existing.ask_count if existing else 0
    max_asks = cfg.public_reply.max_ask_count

    # ───── Priority 1: request_human ─────
    if verdict_str == "request_human":
        # 客户原话：用最近一条客户消息（fast-path 走的就是这个）
        last_msg_body = ""
        if customer_msgs:
            last_msg_body = (customer_msgs[-1].get("body") or "")[:200]
        await _set_escalated_to_human(
            ticket_id=ticket_id, reason="customer_requested",
            existing=existing, state=state, log_path=log_path,
            source=source, now=now, verdict_str=verdict_str,
            latest_cid=latest_cid, latest_ts=latest_ts,
            completeness=verdict.completeness,
            customer_msgs=customer_msgs,
            customer_phrase=last_msg_body,
            extra_log={"customer_phrase": (verdict.reason or "")[:120]},
        )
        return

    # ───── Priority 2: self_resolved ─────
    if verdict_str == "self_resolved":
        state.set(ticket_id, TicketState(
            state="processed",
            first_seen=existing.first_seen if existing else now,
            last_customer_comment_id=latest_cid,
            last_comment_ts=latest_ts,
            last_eval_at=now,
            last_verdict=verdict_str,
            ask_count=ask_count_now,
            nudge_sent_at=existing.nudge_sent_at if existing else None,
            abandoned_alert_sent_at=(
                existing.abandoned_alert_sent_at if existing else None
            ),
            last_agent_finish_ts=(
                existing.last_agent_finish_ts if existing else None
            ),
        ))
        log_decision(
            log_path=log_path, ticket_id=ticket_id,
            verdict=verdict_str, completeness=verdict.completeness,
            action="self_resolved",
            extra={"source": source},
        )
        logger.info(
            "trigger_self_resolved",
            ticket_id=ticket_id, source=source,
        )
        return

    # ───── Priority 3: substantial + high_risk → escalate ─────
    #
    # 演化（2026-05-27）：原本 refund / emotional / incident 三类高风险
    # 都直接 escalate（客户看不到任何 agent 回复，纯沉默等 SE），但实践证明
    # 这"看似安全"的设计反而把客户体验拉低 + agent 学不到决策：
    #
    # - #2680 case：客户说"影响整个组织" → 旧逻辑直接转人工，没去 SigNoz 验证。
    #   实际只是单 workspace 问题。**incident 凭客户一句话 escalate 是误判**。
    # - refund case：客户问退款 → 旧逻辑直接转人工，客户经常等几小时无回复。
    #   实际 agent 完全可以：查订阅状态 + 查政策 → 告知"您符合 X 条退款条件，
    #   财务团队会跟进具体操作"。这给客户一个明确预期，比沉默好得多。
    #   实际退款操作（Stripe）仍然必须人工，agent 同时 escalate 飞书。
    #
    # 现在只有 emotional 立刻 escalate（情绪客户必须人接手安抚，agent 写得
    # 再好都可能火上浇油）。其他高风险类**进 silence_window 走 agent 路径**：
    # - refund   → agent 查 DB/政策后用 public_reply 告知客户判定 + 飞书推财务
    # - incident → agent 用 SigNoz/Grafana 验证是不是真平台故障再决定
    _AUTO_ESCALATE_RISKS = {"emotional"}
    if (
        verdict_str == "substantial_issue"
        and verdict.high_risk_category in _AUTO_ESCALATE_RISKS
    ):
        last_msg_body = ""
        if customer_msgs:
            last_msg_body = (customer_msgs[-1].get("body") or "")[:200]
        await _set_escalated_to_human(
            ticket_id=ticket_id,
            reason=f"high_risk_{verdict.high_risk_category}",
            existing=existing, state=state, log_path=log_path,
            source=source, now=now, verdict_str=verdict_str,
            latest_cid=latest_cid, latest_ts=latest_ts,
            completeness=verdict.completeness,
            customer_msgs=customer_msgs,
            customer_phrase=last_msg_body,
            extra_log={
                "high_risk_category": verdict.high_risk_category,
                "high_risk_reason": (verdict.high_risk_reason or "")[:120],
            },
        )
        return

    # refund / incident 类只是 hint，不直接 escalate：log + 走 silence_window
    # 由 agent 在主循环里查证据后决定回复内容 + 是否还要 escalate
    if (
        verdict_str == "substantial_issue"
        and verdict.high_risk_category in ("refund", "incident")
    ):
        logger.info(
            "verdict_high_risk_hint_not_auto_escalating",
            ticket_id=ticket_id,
            high_risk_category=verdict.high_risk_category,
            high_risk_reason=(verdict.high_risk_reason or "")[:120],
            note=(
                "refund: agent 查 DB/政策后 public_reply + 飞书推财务；"
                "incident: agent 用 SigNoz/Grafana 验证再决定"
            ),
        )
        # 不 return，让代码流到下面 Priority 4（silence_window → agent 处理）

    # ───── Priority 4: substantial + safe → 入静默 ─────
    if verdict_str == "substantial_issue":
        silence_min = cfg.trigger_decider.silence_minutes_default
        window_end = now + timedelta(minutes=silence_min)
        state.set(ticket_id, TicketState(
            state="in_silence_window",
            first_seen=existing.first_seen if existing else now,
            last_customer_comment_id=latest_cid,
            last_comment_ts=latest_ts,
            last_eval_at=now,
            silence_window_end=window_end,
            last_verdict=verdict_str,
            ask_count=ask_count_now,
            nudge_sent_at=None,
            alert_sent_at=None,
            abandoned_alert_sent_at=(
                existing.abandoned_alert_sent_at if existing else None
            ),
            last_agent_finish_ts=(
                existing.last_agent_finish_ts if existing else None
            ),
        ))
        log_decision(
            log_path=log_path, ticket_id=ticket_id,
            verdict=verdict_str, completeness=verdict.completeness,
            silence_min=silence_min, action="enter_silence",
            extra={"source": source},
        )
        logger.info(
            "trigger_enter_silence",
            ticket_id=ticket_id, silence_min=silence_min,
            window_end=window_end.isoformat(), source=source,
        )
        return

    # ───── Priority 5/6: ask 路径（no_message / greeting / insufficient_info）─────
    # 上限检查：累计 ask 已达 max → 转人工
    if ask_count_now >= max_asks:
        last_msg_body = ""
        if customer_msgs:
            last_msg_body = (customer_msgs[-1].get("body") or "")[:200]
        await _set_escalated_to_human(
            ticket_id=ticket_id, reason="max_asks_reached",
            existing=existing, state=state, log_path=log_path,
            source=source, now=now, verdict_str=verdict_str,
            latest_cid=latest_cid, latest_ts=latest_ts,
            completeness=verdict.completeness,
            customer_msgs=customer_msgs,
            customer_phrase=last_msg_body,
            extra_log={
                "ask_count": ask_count_now, "max_ask_count": max_asks,
            },
        )
        return

    # 还有 ask 配额 → 发模板 + 累加（M4 stub，M6 实际实施）
    sent = await _send_ask_template(
        ticket_id=ticket_id,
        verdict_kind=verdict_str,
        customer_msgs=customer_msgs,
        ticket_info=ticket_info,
        ask_count=ask_count_now,
        source=source,
    )

    new_ask_count = ask_count_now + 1 if sent else ask_count_now
    state.set(ticket_id, TicketState(
        state="waiting_for_issue",
        first_seen=existing.first_seen if existing else now,
        last_customer_comment_id=latest_cid,
        last_comment_ts=latest_ts,
        last_eval_at=now,
        last_verdict=verdict_str,
        ask_count=new_ask_count,
        # 客户新消息到达 → 清 nudge/alert 标记让下一轮可重发
        nudge_sent_at=None,
        alert_sent_at=None,
        abandoned_alert_sent_at=(
            existing.abandoned_alert_sent_at if existing else None
        ),
        last_agent_finish_ts=(
            existing.last_agent_finish_ts if existing else None
        ),
    ))
    log_decision(
        log_path=log_path, ticket_id=ticket_id,
        verdict=verdict_str, completeness=verdict.completeness,
        action="ask_customer",
        extra={
            "source": source, "ask_count_before": ask_count_now,
            "ask_count_after": new_ask_count, "sent": sent,
        },
    )
    logger.info(
        "trigger_ask_customer",
        ticket_id=ticket_id, verdict=verdict_str,
        ask_count_before=ask_count_now, ask_count_after=new_ask_count,
        source=source, sent=sent,
    )


async def _trigger_consumer(
    queue: asyncio.Queue,
    manager: Any,
    state: "TicketStateTracker",
) -> None:
    """Consume trigger_queue:

    Source dispatch:
      - webhook | poller_new_msg : LLM 判定 + state 转换（同入口）
      - silence_waker            : 静默期到期 + 实质问题 → 直接 dispatch AgentSession
      - silence_expired_ask      : 静默期到期 + 非实质问题 → 公开 ask 客户 / 飞书内部告警
      - mention                  : 真人 @yonggang → 跳过判定直接 dispatch
    """
    from datetime import datetime, timezone, timedelta
    from zd_agent.config import EnvSettings
    from zd_agent.trigger.decider import classify_messages
    from zd_agent.trigger.decision_log import log_decision
    from zd_agent.trigger.nudge import nudge_message
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

            # 0a. 灰度边界：trigger_consumer 顶部按 ticket_id 过滤。
            #     上线初期 min_ticket_id=2624 → 只处理 ≥ 2624 的新工单，
            #     已经在飞的旧工单不让新逻辑搞乱。设为 0 = 处理全部。
            min_tid = cfg.zendesk.min_ticket_id
            if min_tid and ticket_id < min_tid:
                logger.info(
                    "trigger_skip_below_min_ticket_id",
                    ticket_id=ticket_id, source=source, min_ticket_id=min_tid,
                )
                log_decision(
                    log_path=log_path, ticket_id=ticket_id,
                    verdict="below_min_ticket_id", action="skip",
                    extra={"source": source, "min_ticket_id": min_tid},
                )
                continue

            # 0. M7: 已转人工的工单 4 个 entry 入口（mention / webhook /
            #    silence_waker / waiting_poller）一律 skip。所有 entry 都经
            #    trigger_queue 进来，所以一处检查覆盖全部。
            existing_state_for_escalate_check = state.get(ticket_id)
            if (
                existing_state_for_escalate_check is not None
                and existing_state_for_escalate_check.state == "escalated_to_human"
            ):
                logger.info(
                    "trigger_skip_escalated",
                    ticket_id=ticket_id, source=source,
                    escalated_reason=existing_state_for_escalate_check.escalated_reason,
                    escalated_at=(
                        existing_state_for_escalate_check.escalated_at.isoformat()
                        if existing_state_for_escalate_check.escalated_at else None
                    ),
                )
                log_decision(
                    log_path=log_path, ticket_id=ticket_id,
                    verdict="escalated_to_human", action="skip",
                    extra={
                        "source": source,
                        "escalated_reason": existing_state_for_escalate_check.escalated_reason,
                    },
                )
                continue

            # 1. silence_waker 唤醒 or mention 触发 → 跳过判定，直接 dispatch
            if source in ("silence_waker", "mention"):
                hint = msg.get("hint") or ""

                # Post-agent cooldown 也覆盖直 dispatch 路径：
                # - silence_waker：同一静默窗口可能被多次 schedule（看到 ticket
                #   2393 在 5 分钟间隔被 dispatch 两次，verdict 都 substantial）
                # - mention：SE 在 agent 跑完后短时间内连发 mention（看到 ticket
                #   2320 三次相邻 mention 各跑一次）。SE 主动 mention 通常是给
                #   新 hint，所以 hint 非空时 cooldown 缩短到 30s 以保留"立即
                #   re-run"语义；hint 为空（poller 自动塞入的固定模板）则按完整
                #   cooldown。
                existing_state = state.get(ticket_id)
                if existing_state and existing_state.last_agent_finish_ts is not None:
                    full_cd = cfg.trigger_decider.post_agent_cooldown_seconds
                    short_cd = 30
                    cd_seconds = short_cd if (source == "mention" and hint) else full_cd
                    elapsed_s = (
                        datetime.now(timezone.utc) - existing_state.last_agent_finish_ts
                    ).total_seconds()
                    if elapsed_s < cd_seconds:
                        logger.info(
                            "trigger_skip_post_agent_cooldown_direct",
                            ticket_id=ticket_id, source=source,
                            elapsed_s=round(elapsed_s, 1),
                            cooldown_s=cd_seconds,
                            had_hint=bool(hint),
                        )
                        log_decision(
                            log_path=log_path, ticket_id=ticket_id,
                            verdict=f"post_agent_cooldown_{source}",
                            action="skip",
                        )
                        continue

                logger.info("trigger_dispatch_direct",
                            ticket_id=ticket_id, source=source,
                            has_hint=bool(hint))
                await manager.dispatch(
                    ticket_id, dry_run=False,
                    trigger_type=f"webhook-{source}",
                    hint=hint,
                )
                continue

            # 1b. silence_expired_ask: 静默期到期 + 非实质问题
            #     enable_public_nudge ON  → 给客户发 public ask
            #     OFF                    → 推飞书内部告警代替
            if source == "silence_expired_ask":
                await _handle_silence_expired_ask(
                    ticket_id=ticket_id, state=state, log_path=log_path,
                    nudge_message_fn=nudge_message,
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

            # 2b. 人工 SE 中途接手检测（Case 3 gap）
            #    检测 3 种情况：assignee=非 agent / 最后 public reply 是其他 SE /
            #    yonggang 通过 Zendesk UI 手动发了 public（body 缺 agent footer）
            takeover, takeover_reason = await _detect_human_takeover(
                ticket_id=ticket_id,
                ticket_info=ticket_info,
                agent_user_id=cfg.zendesk.agent_user_id,
            )
            if takeover:
                logger.info(
                    "trigger_human_takeover_detected",
                    ticket_id=ticket_id, reason=takeover_reason,
                )
                log_decision(
                    log_path=log_path, ticket_id=ticket_id,
                    verdict="human_takeover", action="escalate",
                    extra={"reason": takeover_reason},
                )
                # 标记 state=escalated_to_human，后续 webhook 一律 M7 顶部 skip
                existing_for_takeover = state.get(ticket_id)
                latest_for_takeover = customer_msgs[-1] if customer_msgs else {}
                latest_cid_takeover = latest_for_takeover.get("id", "")
                latest_ts_takeover = datetime.now(timezone.utc)
                try:
                    latest_ts_takeover = datetime.fromisoformat(
                        (latest_for_takeover.get("created_at") or "").replace("Z", "+00:00")
                    )
                except Exception:
                    pass
                await _set_escalated_to_human(
                    ticket_id=ticket_id,
                    reason="human_takeover_detected",
                    existing=existing_for_takeover,
                    state=state,
                    log_path=log_path,
                    source=source,
                    now=datetime.now(timezone.utc),
                    verdict_str="human_takeover",
                    latest_cid=latest_cid_takeover,
                    latest_ts=latest_ts_takeover,
                    completeness=0,
                    customer_msgs=customer_msgs,
                    customer_phrase="",
                    extra_log={"takeover_reason": takeover_reason},
                )
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

            # 3a. M8 metric: 客户在 agent 标 processed 之后又回来 → reopen
            #     （新 comment 不重复 + 状态是 processed）
            if existing and existing.state == "processed":
                from zd_agent.observability.metrics import emit_customer_reopen
                hours_since = None
                if existing.last_eval_at is not None:
                    last_eval = existing.last_eval_at
                    if last_eval.tzinfo is None:
                        last_eval = last_eval.replace(tzinfo=timezone.utc)
                    hours_since = (
                        datetime.now(timezone.utc) - last_eval
                    ).total_seconds() / 3600
                emit_customer_reopen(
                    ticket_id=ticket_id,
                    prev_state=existing.state,
                    source=source,
                    hours_since_processed=hours_since,
                )

            # 3b. Post-agent cooldown：上次 agent 完成后 N 秒内的客户新消息
            #     视为"补充上一条问题"，跳过新一轮 dispatch（防止客户连发两条
            #     —— 如日文+英文翻译 —— 触发 agent 跑两次同语义任务）。
            if existing and existing.last_agent_finish_ts is not None:
                cooldown_s = cfg.trigger_decider.post_agent_cooldown_seconds
                elapsed_s = (
                    datetime.now(timezone.utc) - existing.last_agent_finish_ts
                ).total_seconds()
                if elapsed_s < cooldown_s:
                    # 仍把 last_customer_comment_id 推进，避免下一次 webhook
                    # 重复评估这条；但不入静默、不 dispatch
                    existing.last_customer_comment_id = latest_cid
                    state.set(ticket_id, existing)
                    logger.info(
                        "trigger_skip_post_agent_cooldown",
                        ticket_id=ticket_id,
                        comment_id=latest_cid,
                        elapsed_s=round(elapsed_s, 1),
                        cooldown_s=cooldown_s,
                    )
                    log_decision(
                        log_path=log_path, ticket_id=ticket_id,
                        verdict="post_agent_cooldown", action="skip",
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
                model=cfg.trigger_decider.llm_model,
            )

            now = datetime.now(timezone.utc)
            latest_ts_str = latest.get("created_at", "")
            try:
                latest_ts = datetime.fromisoformat(
                    latest_ts_str.replace("Z", "+00:00")
                )
            except Exception:
                latest_ts = now

            # 5. 根据 verdict 分发 state transition + 副作用动作
            #    详细分发规则见 _handle_post_verdict_dispatch()
            await _handle_post_verdict_dispatch(
                ticket_id=ticket_id,
                verdict=verdict,
                customer_msgs=customer_msgs,
                ticket_info=ticket_info,
                latest_cid=latest_cid,
                latest_ts=latest_ts,
                existing=existing,
                state=state,
                log_path=log_path,
                source=source,
                now=now,
                cfg=cfg,
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

    # 挂 session 完成回调：写 last_agent_finish_ts 到 state，供
    # trigger_consumer 的 post-agent cooldown 检查使用（防止客户连发两条
    # 触发 agent 跑两次）。回调签名 `(session) -> coroutine`。
    async def _on_session_finished(session: Any) -> None:  # noqa: ANN401
        try:
            from datetime import datetime as _dt, timezone as _tz
            from zd_agent.session.agent_session import SessionStatus
            ticket_state = state.get(session.ticket_id)
            if ticket_state is None:
                return  # 没 state 说明根本没走 trigger 路径（CLI 手动派单等），跳过
            ticket_state.last_agent_finish_ts = _dt.now(_tz.utc)

            # 修法 e2: session failed 时回滚 state=processing 到 in_silence_window
            # 让下次 webhook（客户新消息）能重新评估并 dispatch，否则永远卡 processing。
            # 跑成功的也回滚（已经发了 reply，没必要继续 processing）。
            # 只有特殊 state (escalated_to_human / abandoned) 不动。
            if (
                ticket_state.state == "processing"
                and session.status in (SessionStatus.FAILED, SessionStatus.COMPLETED)
            ):
                # 失败 → 回到 in_silence_window 让客户下次发消息重触发
                # 成功 → 标 processed，避免重复跑同一 ticket
                new_state = (
                    "in_silence_window"
                    if session.status == SessionStatus.FAILED
                    else "processed"
                )
                logger.info(
                    "session_finished_state_reset",
                    ticket_id=session.ticket_id,
                    from_state="processing",
                    to_state=new_state,
                    session_status=session.status.value,
                )
                ticket_state.state = new_state
                # 失败的话清掉 silence_window_end（不再等了，下次客户消息直接评估）
                if session.status == SessionStatus.FAILED:
                    ticket_state.silence_window_end = None

            state.set(session.ticket_id, ticket_state)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "set_last_agent_finish_ts_failed",
                ticket_id=getattr(session, "ticket_id", None),
                error=str(e),
            )

    if hasattr(manager, "set_on_finished"):
        manager.set_on_finished(_on_session_finished)

    app = web.Application()
    app["config"] = cfg
    app["trigger_queue"] = trigger_queue
    app["ticket_state"] = state
    app["webhook_dedup"] = webhook_dedup
    app["teleport_auth_token_store"] = teleport_auth_token_store
    app["session_manager"] = manager

    app.router.add_post(cfg.webhook.zendesk_path, handle_zendesk_webhook)
    # Messaging webhook：messaging_ticket.message_added 事件，独立 endpoint + secret
    app.router.add_post(
        cfg.webhook.zendesk_messaging_path,
        handle_zendesk_messaging_webhook,
    )
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
        _app["waiting_poller_task"] = asyncio.create_task(
            run_waiting_poller(
                state=state,
                trigger_queue=trigger_queue,
                scan_interval_seconds=cfg.waiting_poller.scan_interval_seconds,
                abandoned_hours=cfg.waiting_poller.abandoned_hours,
                greeting_alert_minutes=cfg.waiting_poller.greeting_alert_minutes,
            )
        )
        # @yonggang mention 识别（独立于 webhook，处理"工单已 processed/closed
        # 后被人手动 mention"的场景；webhook 触发的 mention 也走同 queue 分支）
        from zd_agent.mention.tracker import MentionTracker
        mention_tracker = MentionTracker(state_dir / "processed_mentions.json")
        _app["mention_tracker"] = mention_tracker
        _app["mention_poller_task"] = asyncio.create_task(
            run_mention_poller(
                trigger_queue=trigger_queue,
                tracker=mention_tracker,
                poll_interval_seconds=cfg.zendesk.mention_poll_interval_seconds,
            )
        )
        _app["trigger_task"] = asyncio.create_task(
            _trigger_consumer(trigger_queue, manager, state),
        )
        logger.info("saas_app_started", port=cfg.webhook.listen_port)

    async def _on_shutdown(_app: web.Application) -> None:
        """Graceful shutdown: 停止接受新工单，等 in-flight session 自然结束。

        aiohttp 触发顺序：on_shutdown → on_cleanup。在 on_shutdown 阶段：
          1. cancel poller / trigger consumer → 不再产生新 dispatch
          2. wait_all() in-flight AgentSession，最多等 in_flight_grace_seconds 秒
          3. 超时 → 走 on_cleanup 强制 cancel

        修之前的 RuntimeError: Event loop is closed —— 那是 SIGTERM 直接 kill
        了循环，asyncio task 还在 run，loop 被 close 后 tools 里挂着的 httpx
        client / subprocess 报错。
        """
        # 先 cancel poller / consumer，不再吃新事件
        for key in (
            "silence_task", "waiting_poller_task",
            "mention_poller_task", "trigger_task",
        ):
            task = _app.get(key)
            if task is not None and not task.done():
                task.cancel()

        # 等 manager 里在跑的 session 收尾（不要 cancel，让它正常 archive）
        grace_s = 30  # 经验值：典型 session 8-15s，给 30s 容忍长尾
        if hasattr(manager, "wait_all"):
            in_flight = getattr(manager, "active_count", 0)
            if in_flight > 0:
                logger.info(
                    "shutdown_waiting_in_flight_sessions",
                    in_flight=in_flight, grace_s=grace_s,
                )
                try:
                    await asyncio.wait_for(manager.wait_all(), timeout=grace_s)
                    logger.info("shutdown_in_flight_drained")
                except asyncio.TimeoutError:
                    remaining = getattr(manager, "active_count", 0)
                    logger.warning(
                        "shutdown_grace_expired_forcing_cleanup",
                        still_in_flight=remaining,
                    )

    async def _on_cleanup(_app: web.Application) -> None:
        # on_shutdown 已经 cancel 过 task；这里 await 等真正退出
        for key in (
            "silence_task", "waiting_poller_task",
            "mention_poller_task", "trigger_task",
        ):
            task = _app.get(key)
            if task is not None:
                if not task.cancelled() and not task.done():
                    task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    app.on_startup.append(_on_startup)
    app.on_shutdown.append(_on_shutdown)
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

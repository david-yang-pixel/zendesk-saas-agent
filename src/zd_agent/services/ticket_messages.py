"""Zendesk ticket message fetching and attachment helpers."""
from __future__ import annotations

from zd_agent.log import get_logger

logger = get_logger(module="services.ticket_messages")


def _message_created_at_sort_key(message: dict) -> str:
    value = message.get("created_at")
    return value if isinstance(value, str) else ""


def extract_form_data_from_msgs(customer_msgs: list[dict]) -> dict:
    """Pull Name / Email / Workspace ID from the first form-like message body."""
    out: dict = {}
    for m in customer_msgs:
        body = m.get("body") or ""
        if "Name:" not in body and "Workspace ID:" not in body:
            continue

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
        break
    return out


def _customer_public_comments(ticket: dict, comments: list[dict]) -> list[dict]:
    """筛出工单请求者（客户）发的 public 评论，按时间升序。"""
    requester_id = ticket.get("requester_id")
    out = []
    for c in comments:
        if not c.get("public"):
            continue
        if c.get("author_id") != requester_id:
            continue
        image_urls: list[str] = []
        for att in c.get("attachments", []) or []:
            ctype = (att.get("content_type") or "").lower()
            if ctype.startswith("image/"):
                url = att.get("content_url") or att.get("mapped_content_url")
                if url:
                    image_urls.append(url)
        c = {**c, "image_urls": image_urls}
        out.append(c)
    out.sort(key=_message_created_at_sort_key)
    return out


def _strip_html(s: str) -> str:
    import re
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _messaging_customer_messages(events: list[dict]) -> list[dict]:
    """从 conversation_log events 提取 end-user（客户）发的消息事件。"""
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


_IMG_MAX_BYTES = 4 * 1024 * 1024
_IMG_TOTAL_BUDGET = 6
_SC_ATTACHMENT_RE = __import__("re").compile(r"/sc/attachments/v2/([^/]+)/")


async def _download_image_data_url(
    url: str,
    *,
    timeout: float = 15.0,
    max_bytes: int = _IMG_MAX_BYTES,
) -> str | None:
    """下载图片返回 ``data:image/...;base64,...`` URL."""
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
            resp = await client.get(url)
            if resp.status_code == 200:
                return await _from_response(resp)

            if resp.status_code in (401, 403) and auth is not None:
                resp = await client.get(url, auth=auth)
                if resp.status_code == 200:
                    return await _from_response(resp)

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
    """从最后 ``lookback`` 条客户消息里抽图片，下载并编码为 data URL。"""
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


async def _fetch_customer_messages(
    ticket_id: int,
    *,
    log_success: bool = True,
) -> tuple[list[dict], dict | None]:
    """拉工单 + 客户消息（含 messaging 渠道）。"""
    from zd.client import ZendeskClient
    from zd_agent.tools.zendesk import _call_with_rate_limit_retry

    client = ZendeskClient()

    try:
        ticket_resp = await _call_with_rate_limit_retry(
            client.get_ticket, ticket_id,
        )
        t = ticket_resp.get("ticket", {})
        comments_resp = await _call_with_rate_limit_retry(
            client.get_ticket_comments, ticket_id,
        )
        c = comments_resp.get("comments", [])
        events: list[dict] = []
        via_channel = (t.get("via") or {}).get("channel", "")
        if via_channel == "native_messaging":
            try:
                log = await _call_with_rate_limit_retry(
                    client.get_ticket_conversation_log,
                    ticket_id,
                    page_size=100,
                )
                events = log.get("events", []) or []
            except Exception as e:
                logger.warning(
                    "conversation_log_fetch_failed",
                    ticket_id=ticket_id, error=str(e),
                )
    except Exception as e:
        logger.error(
            "trigger_fetch_ticket_failed",
            ticket_id=ticket_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        return [], None

    ticket, comments = t, c
    msgs = _customer_public_comments(ticket, comments)
    if events:
        msgs.extend(_messaging_customer_messages(events))
    msgs.sort(key=_message_created_at_sort_key)
    if log_success:
        logger.info(
            "trigger_fetch_messages_ok",
            ticket_id=ticket_id,
            comments_count=len(comments),
            events_count=len(events),
            customer_msgs_count=len(msgs),
            via=(ticket.get("via") or {}).get("channel"),
        )
    return msgs, ticket

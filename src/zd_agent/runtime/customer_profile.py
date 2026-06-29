"""P2-8 最小版：dispatch 前预拉客户画像。

只拉**最便宜的 3 个字段**给 agent，避免把 agent 启动延迟拖太长：

- ``requester_email`` — 工单提交人邮箱
- ``recent_ticket_ids`` — 同 requester 过去 30 天的 SaaS 工单 ID（最多 5 个）
- ``recent_ticket_count`` — 总数

Agent 看到 system prompt 里的 ``recent_ticket_ids`` 后可以决定要不要用
``ticket_record_read`` 或 ``zendesk_get_ticket`` 去看历史细节，**agent 自己**
判断是否相关 —— 我们不强行把所有历史塞进 prompt（那样既贵又可能不相关）。

实现保守：
- 全程 try/except + 5s timeout，拉不到画像不影响主流程（agent 仍能跑）
- 只通过远程 Zendesk MCP 读取（不在 agent 进程内直连 Zendesk API）
"""
from __future__ import annotations

import asyncio
from typing import Any

from zd_agent.integrations.zendesk_mcp import call_zendesk_tool
from zd_agent.log import get_logger

logger = get_logger(module="runtime.customer_profile")

_PROFILE_TIMEOUT_SECONDS = 5.0
_MAX_RECENT_TICKETS = 5
_RECENT_WINDOW_DAYS = 30


async def prefetch_customer_profile(ticket_id: int) -> dict[str, Any]:
    """从 Zendesk 拉 ``ticket_id`` 的 requester 画像。

    Returns:
        ``{requester_email, recent_ticket_ids, recent_ticket_count}``。
        任一步骤失败 → 返回空 dict（caller 当作"没画像"处理，不报错）。
    """
    try:
        return await asyncio.wait_for(
            _do_prefetch(ticket_id),
            timeout=_PROFILE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "customer_profile_prefetch_timeout",
            ticket_id=ticket_id, timeout_s=_PROFILE_TIMEOUT_SECONDS,
        )
        return {}
    except Exception as e:
        logger.warning(
            "customer_profile_prefetch_error",
            ticket_id=ticket_id, error=str(e)[:200],
        )
        return {}


async def _do_prefetch(ticket_id: int) -> dict[str, Any]:
    """实际预拉逻辑（被 prefetch_customer_profile timeout/exception 包裹）。"""
    # 1. 拉工单 → 拿 requester_id
    ticket_payload = await call_zendesk_tool("get_ticket", {"ticket_id": ticket_id})
    if not ticket_payload.get("success"):
        logger.warning(
            "customer_profile_ticket_fetch_failed",
            ticket_id=ticket_id,
            error=ticket_payload.get("error"),
            code=ticket_payload.get("code"),
        )
        return {}
    ticket_data = ticket_payload.get("data") or {}
    ticket = ticket_data.get("ticket", ticket_data)
    if not ticket:
        return {}
    requester_id = ticket.get("requester_id")
    if not requester_id:
        return {}

    # 2. 拉 requester → 拿 email
    user_payload = await call_zendesk_tool("get_user", {"user_id": requester_id})
    if not user_payload.get("success"):
        logger.warning(
            "customer_profile_user_fetch_failed",
            ticket_id=ticket_id,
            requester_id=requester_id,
            error=user_payload.get("error"),
            code=user_payload.get("code"),
        )
        return {}
    user_data = user_payload.get("data") or {}
    user = user_data.get("user", user_data)
    if not user:
        return {}
    email = user.get("email") or ""
    if not email:
        return {}

    # 3. 搜同 requester 过去 30 天的 SaaS 工单（排除当前工单）
    # 用 Zendesk search API：requester:<email> tags:saas created>YYYY-MM-DD
    from datetime import datetime, timedelta, timezone
    since = (
        datetime.now(timezone.utc) - timedelta(days=_RECENT_WINDOW_DAYS)
    ).strftime("%Y-%m-%d")
    query = (
        f"requester:{email} tags:saas created>{since}"
    )
    try:
        search_payload = await call_zendesk_tool(
            "search_tickets",
            {
                "query": query,
                "sort_by": "updated_at",
                "sort_order": "desc",
                "page": 1,
                "per_page": _MAX_RECENT_TICKETS + 1,
            },
        )
        if not search_payload.get("success"):
            raise RuntimeError(
                f"{search_payload.get('code')}: {search_payload.get('error')}"
            )
        search_resp = search_payload.get("data") or {}
    except Exception as e:
        logger.warning(
            "customer_profile_search_failed",
            ticket_id=ticket_id, error=str(e)[:200],
        )
        search_resp = {}

    results = (search_resp or {}).get("results") or []
    recent_ids: list[int] = []
    for t in results:
        tid = t.get("id")
        if isinstance(tid, int) and tid != ticket_id:
            recent_ids.append(tid)
            if len(recent_ids) >= _MAX_RECENT_TICKETS:
                break

    profile = {
        "requester_email": email,
        "recent_ticket_ids": recent_ids,
        "recent_ticket_count": len(recent_ids),
    }
    logger.info(
        "customer_profile_prefetched",
        ticket_id=ticket_id,
        email=email,
        recent_count=len(recent_ids),
    )
    return profile

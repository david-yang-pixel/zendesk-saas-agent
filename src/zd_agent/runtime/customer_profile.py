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
- 只用 Zendesk 标准 API（不依赖 inspector binary，避免容器内 binary 缺失）
"""
from __future__ import annotations

import asyncio
from typing import Any

from zd_agent.log import get_logger
from zd_agent.tools.zendesk import _get_client

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
    client = _get_client()

    # 1. 拉工单 → 拿 requester_id
    ticket = await asyncio.to_thread(client.get_ticket, ticket_id)
    if not ticket:
        return {}
    requester_id = ticket.get("requester_id")
    if not requester_id:
        return {}

    # 2. 拉 requester → 拿 email
    user = await asyncio.to_thread(client.get_user, requester_id)
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
        # client.search_tickets 是同步的，用 to_thread；返回
        # {"results": [...], "count": ...} 的标准 search payload
        search_resp = await asyncio.to_thread(
            client.search_tickets, query, "updated_at", "desc", 1, _MAX_RECENT_TICKETS + 1,
        )
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

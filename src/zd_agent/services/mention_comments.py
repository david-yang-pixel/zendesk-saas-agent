from __future__ import annotations

from collections.abc import Awaitable, Callable
from math import ceil
from typing import Any


ZendeskCall = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


async def fetch_recent_ticket_comments(
    ticket_id: int,
    *,
    call_fn: ZendeskCall,
    page_size: int = 100,
    max_pages: int = 10,
    target_comment_id: str = "",
) -> tuple[dict[str, Any], ...]:
    """Return a bounded newest-first comment window from an oldest-first API."""

    async def _page(page: int) -> dict[str, Any]:
        payload = await call_fn(
            "get_ticket_comments",
            {"ticket_id": ticket_id, "page": page, "per_page": page_size},
        )
        if not payload.get("success"):
            raise RuntimeError(f"{payload.get('code')}: {payload.get('error')}")
        return payload.get("data") or {}

    first = await _page(1)
    first_comments = list(first.get("comments", []) or [])
    if target_comment_id:
        target = next(
            (
                comment
                for comment in first_comments
                if str(comment.get("id") or "") == target_comment_id
            ),
            None,
        )
        if target is not None:
            return (target,)
    if not first.get("next_page"):
        return tuple(reversed(first_comments))

    try:
        count = int(first.get("count") or 0)
    except (TypeError, ValueError):
        count = 0
    if count > page_size:
        last_page = max(2, ceil(count / page_size))
        remaining_pages = max(1, max_pages - 1)
        stop_page = max(1, last_page - remaining_pages)
        recent: list[dict[str, Any]] = []
        for page in range(last_page, stop_page, -1):
            data = await _page(page)
            page_comments = list(data.get("comments", []) or [])
            if target_comment_id:
                target = next(
                    (
                        comment
                        for comment in page_comments
                        if str(comment.get("id") or "") == target_comment_id
                    ),
                    None,
                )
                if target is not None:
                    return (target,)
            recent.extend(reversed(page_comments))
        return tuple(recent)

    # Compatibility fallback for older MCP responses without ``count``.
    comments = first_comments
    for page in range(2, max_pages + 1):
        data = await _page(page)
        page_comments = list(data.get("comments", []) or [])
        if target_comment_id:
            target = next(
                (
                    comment
                    for comment in page_comments
                    if str(comment.get("id") or "") == target_comment_id
                ),
                None,
            )
            if target is not None:
                return (target,)
        comments.extend(page_comments)
        if not page_comments or not data.get("next_page"):
            break
    return tuple(reversed(comments))

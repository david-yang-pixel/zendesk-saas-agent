"""Human takeover detection for Zendesk tickets."""

from __future__ import annotations

from math import ceil
from typing import Any

from zd_agent.config import get_config
from zd_agent.integrations.zendesk_mcp import call_zendesk_tool
from zd_agent.log import get_logger
from zd_agent.services.assignee import is_human_assignee

logger = get_logger(module="services.human_takeover")

_COMMENT_PAGE_SIZE = 100
_MAX_TAKEOVER_COMMENT_PAGES = 1_000


class _TakeoverCommentHistoryTruncated(RuntimeError):
    pass


async def _latest_public_comment(ticket_id: int) -> dict[str, Any] | None:
    async def fetch_page(page: int) -> dict[str, Any]:
        payload = await call_zendesk_tool(
            "get_ticket_comments",
            {"ticket_id": ticket_id, "page": page, "per_page": _COMMENT_PAGE_SIZE},
        )
        if not payload.get("success"):
            raise RuntimeError(f"{payload.get('code')}: {payload.get('error')}")
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            raise RuntimeError("Zendesk comments response must be an object")
        return data

    def newest_public(data: dict[str, Any]) -> dict[str, Any] | None:
        comments = data.get("comments") or []
        if not isinstance(comments, list):
            raise RuntimeError("Zendesk comments must be a list")
        return next(
            (
                comment
                for comment in reversed(comments)
                if isinstance(comment, dict) and comment.get("public")
            ),
            None,
        )

    first = await fetch_page(1)
    if not first.get("next_page"):
        return newest_public(first)

    try:
        count = int(first.get("count") or 0)
    except (TypeError, ValueError):
        count = 0
    if count > _COMMENT_PAGE_SIZE:
        last_page = ceil(count / _COMMENT_PAGE_SIZE)
        if last_page > _MAX_TAKEOVER_COMMENT_PAGES:
            raise _TakeoverCommentHistoryTruncated(
                f"ticket comment history has {last_page} pages"
            )
        fetched_tail: dict[int, dict[str, Any]] = {}
        page = last_page
        data = await fetch_page(page)
        fetched_tail[page] = data
        seen_next_pages: set[str] = set()
        while next_page := data.get("next_page"):
            marker = str(next_page)
            if marker in seen_next_pages:
                raise _TakeoverCommentHistoryTruncated(
                    "ticket comments returned a repeated next_page"
                )
            seen_next_pages.add(marker)
            page += 1
            if page > _MAX_TAKEOVER_COMMENT_PAGES:
                raise _TakeoverCommentHistoryTruncated(
                    "ticket comment history exceeded the takeover scan limit"
                )
            data = await fetch_page(page)
            fetched_tail[page] = data
        actual_last_page = page
        for page in range(actual_last_page, 0, -1):
            if page == 1:
                data = first
            else:
                data = fetched_tail.get(page) or await fetch_page(page)
            comment = newest_public(data)
            if comment is not None:
                return comment
        return None

    latest: dict[str, Any] | None = newest_public(first)
    data = first
    for page in range(2, _MAX_TAKEOVER_COMMENT_PAGES + 1):
        data = await fetch_page(page)
        if comment := newest_public(data):
            latest = comment
        if not data.get("next_page"):
            return latest
    if data.get("next_page"):
        raise _TakeoverCommentHistoryTruncated(
            "ticket comment history exceeded the takeover scan limit"
        )
    return latest


AGENT_REPLY_MARKERS = (
    "This reply was sent by Dify AI Agent.",
    "This reply was sent by Dify AI Support.",
    "Dify SaaS Agent",
)


async def detect_human_takeover(
    *,
    ticket_id: int,
    ticket_info: dict,
    agent_user_id: int,
) -> tuple[bool, str]:
    """Return whether a ticket already appears to be handled by a human."""
    assignee_id = ticket_info.get("assignee_id")
    if is_human_assignee(
        assignee_id,
        agent_user_id=agent_user_id,
        non_human_assignee_ids=get_config().zendesk.non_human_assignee_ids,
    ):
        return True, f"assignee={assignee_id}（工单已有受托人）"

    if not agent_user_id:
        return False, ""

    try:
        last_public = await _latest_public_comment(ticket_id)
    except _TakeoverCommentHistoryTruncated as error:
        logger.warning(
            "takeover_comment_history_truncated",
            ticket_id=ticket_id,
            error=str(error),
        )
        return True, "human takeover check stopped: comment history truncated"
    except Exception as e:
        logger.warning(
            "takeover_fetch_comments_failed",
            ticket_id=ticket_id,
            error=str(e)[:200],
        )
        return False, ""

    if last_public is None:
        return False, ""

    last_author = last_public.get("author_id")
    last_body = last_public.get("body") or ""

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

    try:
        if (
            last_author
            and int(last_author) == int(agent_user_id)
            and not any(marker in last_body for marker in AGENT_REPLY_MARKERS)
        ):
            return True, (
                "last public reply from agent_uid but missing agent footer "
                "(yonggang 手动发?)"
            )
    except (TypeError, ValueError):
        pass

    return False, ""

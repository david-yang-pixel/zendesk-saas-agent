"""Human takeover detection for Zendesk tickets."""

from __future__ import annotations

from zd_agent.config import get_config
from zd_agent.integrations.zendesk_mcp import call_zendesk_tool
from zd_agent.log import get_logger
from zd_agent.services.assignee import is_human_assignee

logger = get_logger(module="services.human_takeover")

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
        payload = await call_zendesk_tool(
            "get_ticket_comments",
            {"ticket_id": ticket_id, "page": 1, "per_page": 100},
        )
        if not payload.get("success"):
            raise RuntimeError(f"{payload.get('code')}: {payload.get('error')}")
        comments_resp = payload.get("data") or {}
    except Exception as e:
        logger.warning(
            "takeover_fetch_comments_failed",
            ticket_id=ticket_id,
            error=str(e)[:200],
        )
        return False, ""

    comments = (comments_resp or {}).get("comments") or []
    last_public = None
    for comment in reversed(comments):
        if comment.get("public"):
            last_public = comment
            break

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

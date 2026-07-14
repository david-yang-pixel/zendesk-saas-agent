"""Create one idempotent internal analysis note for each public reply."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from hashlib import sha256
from typing import Any

from zd_agent.log import get_logger

logger = get_logger(module="services.public_reply_internal_note")

ZendeskToolCaller = Callable[..., Awaitable[dict[str, Any]]]

_DEFAULT_ANALYSIS = {
    "ask": (
        "The available information was insufficient for a reliable diagnosis. "
        "The public reply requested the minimum missing details needed to continue."
    ),
    "solution": (
        "The agent completed the available evidence checks and sent the customer a "
        "technical response. See the run archive for the full evidence trail."
    ),
    "handoff": (
        "The issue requires human ownership or action. The customer was told that a "
        "support engineer will follow up."
    ),
    "closed": (
        "The customer reported that the issue was resolved. The public reply "
        "acknowledged closure and explained how to reopen the conversation."
    ),
}


def _analysis_reference(*, ticket_id: int, body_with_footer: str) -> str:
    normalized = " ".join((body_with_footer or "").split())
    digest = sha256(f"{ticket_id}\0{normalized}".encode()).hexdigest()[:16]
    return f"public-reply-analysis:{digest}"


def build_post_public_internal_note(
    *,
    ticket_id: int,
    reply_type: str,
    body_with_footer: str,
    internal_analysis: str = "",
) -> str:
    """Build the concise internal record written after customer delivery."""
    reference = _analysis_reference(
        ticket_id=ticket_id,
        body_with_footer=body_with_footer,
    )
    analysis = (internal_analysis or "").strip() or _DEFAULT_ANALYSIS.get(
        reply_type,
        _DEFAULT_ANALYSIS["solution"],
    )
    return (
        "🤖 **AI Agent public reply record**\n\n"
        "## Internal Analysis\n\n"
        f"{analysis}\n\n"
        "## Delivery\n\n"
        "- Result: Public reply delivered to the customer\n"
        f"- Reply type: {reply_type}\n"
        "- Channel: Zendesk Messaging\n"
        f"- Reference: `{reference}`"
    )


def _comments_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    comments = data.get("comments")
    if not isinstance(comments, list):
        return []
    return [item for item in comments if isinstance(item, dict)]


def _payload_contains_reference(payload: dict[str, Any], reference: str) -> bool:
    for comment in _comments_from_payload(payload):
        for key in ("plain_body", "body", "html_body"):
            value = comment.get(key)
            if isinstance(value, str) and reference in value:
                return True
    return False


async def ensure_post_public_internal_note(
    *,
    ticket_id: int,
    reply_type: str,
    body_with_footer: str,
    internal_analysis: str,
    zendesk_tool_caller: ZendeskToolCaller,
) -> bool:
    """Write the note once, retrying transient failures without duplicating it."""
    reference = _analysis_reference(
        ticket_id=ticket_id,
        body_with_footer=body_with_footer,
    )
    lookup_args = {
        "ticket_id": ticket_id,
        "include_conversation_log": False,
        "force_refresh": True,
    }
    note_body = build_post_public_internal_note(
        ticket_id=ticket_id,
        reply_type=reply_type,
        body_with_footer=body_with_footer,
        internal_analysis=internal_analysis,
    )
    for attempt in range(1, 4):
        try:
            existing = await zendesk_tool_caller("get_ticket_comments", lookup_args)
            if existing.get("success") is True and _payload_contains_reference(
                existing,
                reference,
            ):
                logger.info(
                    "post_public_internal_note_already_exists",
                    ticket_id=ticket_id,
                    reference=reference,
                )
                return True

            result = await zendesk_tool_caller(
                "add_internal_note",
                {
                    "ticket_id": ticket_id,
                    "body": note_body,
                    "explicit_user_internal_note_request": (
                        "post-public internal analysis"
                    ),
                    "dry_run": False,
                    "confirm": True,
                    "actor": "zd-agent",
                },
            )
            if result.get("success") is True:
                logger.info(
                    "post_public_internal_note_sent",
                    ticket_id=ticket_id,
                    reference=reference,
                    attempt=attempt,
                )
                return True

            # A write can succeed remotely even if its response is lost. Verify
            # before retrying so a transient response error cannot duplicate notes.
            verified = await zendesk_tool_caller("get_ticket_comments", lookup_args)
            if verified.get("success") is True and _payload_contains_reference(
                verified,
                reference,
            ):
                logger.info(
                    "post_public_internal_note_verified_after_error",
                    ticket_id=ticket_id,
                    reference=reference,
                    attempt=attempt,
                )
                return True
            logger.warning(
                "post_public_internal_note_retry",
                ticket_id=ticket_id,
                attempt=attempt,
                code=result.get("code"),
                error=str(result.get("error") or "unknown")[:200],
            )
        except Exception as exc:  # noqa: BLE001 - retry only the internal note.
            logger.warning(
                "post_public_internal_note_retry_exception",
                ticket_id=ticket_id,
                attempt=attempt,
                error=str(exc)[:200],
                exc_info=True,
            )

    logger.error(
        "post_public_internal_note_failed",
        ticket_id=ticket_id,
        attempts=3,
    )
    return False

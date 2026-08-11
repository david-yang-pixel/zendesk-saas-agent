from __future__ import annotations

from datetime import datetime
import re
from zoneinfo import ZoneInfo

_REQUIRED_AI_FOOTER = (
    "This reply was sent by Dify AI Agent. If this does not resolve your issue, "
    "reply \u201chuman\u201d. A human support engineer will respond as soon as possible "
    "during support hours, 9:00 AM\u20136:00 PM Beijing Time (UTC+8)."
)
_FOOTER_BY_TYPE = {
    "ask": _REQUIRED_AI_FOOTER,
    "solution": _REQUIRED_AI_FOOTER,
    "handoff": _REQUIRED_AI_FOOTER,
    "closed": _REQUIRED_AI_FOOTER,
}
_VALID_REPLY_TYPES = frozenset(_FOOTER_BY_TYPE)
_DOCS_MD_PATTERN = re.compile(r"(https?://docs\.dify\.ai/[^\s\)\]>]+?)\.md\b")
_BULLET_RE = re.compile(r"^\s*[-*]\s+")
_NUMBERED_RE = re.compile(r"^\s*\d+\.\s+")
_GREETING_RE = re.compile(r"^\s*Hi\b.*,\s*$", re.IGNORECASE)
_SIGNATURE_RE = re.compile(
    r"^\s*(Best regards|Regards|Thanks|Thank you),?\s*$",
    re.IGNORECASE,
)
_MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_FULL_BOLD_RE = re.compile(r"^\*\*(.+?)\*\*$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_UNDERLINE_BOLD_RE = re.compile(r"(?<!\w)__(.+?)__(?!\w)")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_DECORATIVE_PREFIX_RE = re.compile(r"^[\s]*(?:💡|✅|⚠️?|🔍|📌|👉|🤖)\s*")
_DECORATIVE_RULE_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_ZENDESK_VISUAL_BLANK_LINE = "\u200b"
_SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")
_HUMAN_SUPPORT_HOURS = "9:00 AM to 6:00 PM Shanghai time (UTC+8)"


def _is_within_human_support_hours(now: datetime | None = None) -> bool:
    current = now or datetime.now(tz=_SHANGHAI_TIMEZONE)
    if current.tzinfo is None:
        shanghai_now = current.replace(tzinfo=_SHANGHAI_TIMEZONE)
    else:
        shanghai_now = current.astimezone(_SHANGHAI_TIMEZONE)
    return 9 <= shanghai_now.hour < 18


def _handoff_availability_message(now: datetime | None = None) -> str:
    if _is_within_human_support_hours(now):
        return (
            f"Human support hours are {_HUMAN_SUPPORT_HOURS}. "
            "Your request was submitted during this window.\n\n"
            "A support engineer will review this ticket and reply as soon as "
            "possible. You can add any relevant details here in the meantime.\n\n"
            "Thank you for your patience."
        )
    return (
        f"Human support hours are {_HUMAN_SUPPORT_HOURS}. The team is currently "
        "outside these hours.\n\n"
        "Your request has been queued. A support engineer will review this ticket "
        "when the next support window begins. You do not need to open another "
        "ticket, and you can add any relevant details here in the meantime.\n\n"
        "Thank you for your patience."
    )


def _strip_docs_md_suffix(body: str) -> str:
    return _DOCS_MD_PATTERN.sub(r"\1", body)


def _format_reply_body(body: str) -> str:
    raw_lines = (body or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = _collapse_blank_lines(
        [_normalize_customer_line(line) for line in raw_lines]
    )
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            _append_blank_once(out)
            continue

        previous = _previous_nonblank(out)
        is_list_item = _is_list_item(stripped)
        previous_is_list_item = _is_list_item(previous or "")
        if out and previous is not None:
            if _GREETING_RE.match(previous):
                _append_blank_once(out)
            elif previous.endswith(":") and not is_list_item:
                _append_blank_once(out)
            elif stripped.endswith(":") and not out[-1].strip():
                pass
            elif stripped.endswith(":") and not previous_is_list_item:
                _append_blank_once(out)
            elif is_list_item and previous.endswith(":"):
                _append_blank_once(out)
            elif (
                is_list_item
                and not previous_is_list_item
                and not previous.endswith(":")
            ):
                _append_blank_once(out)
            elif not is_list_item and previous_is_list_item:
                _append_blank_once(out)
            elif _SIGNATURE_RE.match(stripped):
                _append_blank_once(out)
        out.append(stripped)
    return "\n".join(_collapse_blank_lines(out)).strip()


def _normalize_customer_line(line: str) -> str:
    """Render common Markdown decorations as readable Messaging plain text."""
    stripped = line.rstrip().strip()
    if not stripped or _DECORATIVE_RULE_RE.fullmatch(stripped):
        return ""
    if stripped in {"```", "~~~"}:
        return ""

    is_heading = bool(_MARKDOWN_HEADING_RE.match(stripped))
    if is_heading:
        stripped = _MARKDOWN_HEADING_RE.sub("", stripped, count=1).strip()

    full_bold = _FULL_BOLD_RE.fullmatch(stripped)
    is_bold_heading = full_bold is not None
    if full_bold:
        stripped = full_bold.group(1).strip()

    without_decoration = _DECORATIVE_PREFIX_RE.sub("", stripped, count=1).strip()
    had_decorative_prefix = without_decoration != stripped
    stripped = without_decoration
    stripped = _BOLD_RE.sub(r"\1", stripped)
    stripped = _UNDERLINE_BOLD_RE.sub(r"\1", stripped)
    stripped = _INLINE_CODE_RE.sub(r"\1", stripped)

    if (
        stripped
        and (is_heading or is_bold_heading or had_decorative_prefix)
        and not stripped.endswith((":", ".", "?", "!"))
    ):
        stripped += ":"
    return stripped


def _is_list_item(line: str) -> bool:
    return bool(_BULLET_RE.match(line) or _NUMBERED_RE.match(line))


def _append_blank_once(lines: list[str]) -> None:
    if lines and lines[-1].strip():
        lines.append("")


def _previous_nonblank(lines: list[str]) -> str | None:
    for line in reversed(lines):
        if line.strip():
            return line.strip()
    return None


def _collapse_blank_lines(lines: list[str]) -> list[str]:
    collapsed: list[str] = []
    blank_seen = False
    for line in lines:
        if line.strip():
            collapsed.append(line)
            blank_seen = False
        elif not blank_seen:
            collapsed.append("")
            blank_seen = True
    while collapsed and not collapsed[0].strip():
        collapsed.pop(0)
    while collapsed and not collapsed[-1].strip():
        collapsed.pop()
    return collapsed


def _preserve_visual_blank_lines_for_messaging(text: str) -> str:
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    rendered: list[str] = []
    blank_seen = False
    for line in lines:
        if line.strip():
            rendered.append(line.rstrip())
            blank_seen = False
        elif not blank_seen:
            rendered.append(_ZENDESK_VISUAL_BLANK_LINE)
            blank_seen = True
    while rendered and rendered[0] == _ZENDESK_VISUAL_BLANK_LINE:
        rendered.pop(0)
    while rendered and rendered[-1] == _ZENDESK_VISUAL_BLANK_LINE:
        rendered.pop()
    return "\n".join(rendered)


def _append_footer(
    body: str,
    reply_type: str,
    *,
    now: datetime | None = None,
) -> str:
    body = _format_reply_body(_strip_docs_md_suffix(body))
    footer = _FOOTER_BY_TYPE[reply_type]
    if body.rstrip().endswith(footer):
        return _preserve_visual_blank_lines_for_messaging(body)
    return _preserve_visual_blank_lines_for_messaging(
        f"{body.rstrip()}\n\n{footer}",
    )

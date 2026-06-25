"""Feishu interactive card builders. Pure functions returning JSON-ready dicts."""
from __future__ import annotations

import re
import textwrap
from typing import Any


_TICKET_DONE_SECTION_TITLES = (
    "处理结果",
    "关键事实",
    "数据库核查",
    "代码路径",
    "日志核查",
    "客户回复",
    "后续动作",
    "注意事项",
)
_TICKET_DONE_SECTION_RE = re.compile(
    r"^\s*(处理结果|关键事实|数据库核查|代码路径|日志核查|客户回复|后续动作|注意事项)\s*[:：]\s*(.*)\s*$"
)
_TICKET_DONE_STRUCTURED_SECTION_TITLES = {"数据库核查", "代码路径", "日志核查"}
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+")
_STRIPE_SIMPLE_ID_PREFIXES = (
    "sub|cus|pi|in|pm|ch|re|evt|seti|si|price|prod|promo"
)
_STRIPE_FIELD_ID_VALUE_PATTERN = (
    rf"(?:cs_(?:test|live)_[A-Za-z0-9]{{8,}}|"
    rf"(?:{_STRIPE_SIMPLE_ID_PREFIXES})_[A-Za-z0-9]{{8,}})"
)
_STRIPE_ID_VALUE_PATTERN = (
    rf"(?:cs_(?:test|live)_(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{{8,}}|"
    rf"(?:{_STRIPE_SIMPLE_ID_PREFIXES})_(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{{8,}})"
)
_SENSITIVE_STRIPE_FIELD_RE = re.compile(
    rf"(?i)(?P<prefix>`?(?:stripe_)?"
    rf"(?:subscription|customer|payment|payment_intent|invoice|last_invoice|promo|price|product)_id"
    rf"`?\s*[:=]\s*)(?P<quote>`?)(?P<value>{_STRIPE_FIELD_ID_VALUE_PATTERN})(?P=quote)"
)
_STRIPE_ID_VALUE_RE = re.compile(
    rf"(?i)(?<![A-Za-z0-9_])(?P<value>{_STRIPE_ID_VALUE_PATTERN})(?![A-Za-z0-9_])"
)
_SECTION_MAX_CHARS = 500
_STRUCTURED_SECTION_MAX_CHARS = 800
_PLAIN_SENTENCE_MAX_CHARS = 320
_PLAIN_DIGEST_MAX_CHARS = _SECTION_MAX_CHARS


def _truncate_text(text: str, *, max_chars: int) -> str:
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    if max_chars <= 3:
        return cleaned[:max_chars]
    return cleaned[: max_chars - 3].rstrip() + "..."


def _mask_identifier(value: str) -> str:
    if len(value) <= 12:
        return value
    return f"{value[:8]}...{value[-4:]}"


def _redact_sensitive_values(text: str) -> str:
    field_redacted = _SENSITIVE_STRIPE_FIELD_RE.sub(
        lambda match: (
            f"{match.group('prefix')}{match.group('quote')}"
            f"{_mask_identifier(match.group('value'))}{match.group('quote')}"
        ),
        text,
    )
    return _STRIPE_ID_VALUE_RE.sub(
        lambda match: _mask_identifier(match.group("value")),
        field_redacted,
    )


def _section_element(title: str, content: str) -> dict[str, Any]:
    max_chars = (
        _STRUCTURED_SECTION_MAX_CHARS
        if title in _TICKET_DONE_STRUCTURED_SECTION_TITLES
        else _SECTION_MAX_CHARS
    )
    rendered = _truncate_text(_redact_sensitive_values(content), max_chars=max_chars)
    return {
        "tag": "div",
        "text": {"tag": "lark_md", "content": f"**{title}**\n{rendered}"},
    }


def _append_section(
    sections: dict[str, list[str]],
    title: str,
    content: str,
) -> None:
    cleaned = content.strip()
    if cleaned:
        sections.setdefault(title, []).append(cleaned)


def _parse_ticket_done_sections(summary: str) -> list[tuple[str, str]]:
    sections: dict[str, list[str]] = {}
    current_title: str | None = None
    unlabeled_lines: list[str] = []

    for raw_line in summary.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _TICKET_DONE_SECTION_RE.match(line)
        if match:
            current_title = match.group(1)
            _append_section(sections, current_title, match.group(2))
            continue
        if current_title:
            _append_section(sections, current_title, line)
        else:
            unlabeled_lines.append(line)

    if not sections:
        return []

    if unlabeled_lines:
        fallback_title = "注意事项" if "注意事项" in sections else "关键事实"
        _append_section(sections, fallback_title, " ".join(unlabeled_lines))

    return [
        (title, "\n".join(sections[title]).strip())
        for title in _TICKET_DONE_SECTION_TITLES
        if sections.get(title)
    ]


def _split_plain_summary(summary: str, *, max_items: int = 6) -> list[str]:
    normalized = " ".join(line.strip() for line in summary.splitlines() if line.strip())
    if not normalized:
        return ["未提供摘要"]
    parts = [part.strip() for part in _SENTENCE_SPLIT_RE.split(normalized) if part.strip()]
    return [
        _truncate_text(part, max_chars=_PLAIN_SENTENCE_MAX_CHARS)
        for part in (parts or [normalized])[:max_items]
    ]


def _wrap_bullet(text: str, *, width: int = 110) -> list[str]:
    wrapped = textwrap.wrap(
        text,
        width=width,
        break_long_words=False,
        break_on_hyphens=False,
    )
    return wrapped or [text]


def _plain_summary_digest(summary: str) -> str:
    lines: list[str] = []
    for sentence in _split_plain_summary(summary):
        wrapped = _wrap_bullet(sentence)
        lines.append(f"- {wrapped[0]}")
        lines.extend(f"  {line}" for line in wrapped[1:])
    return _truncate_text("\n".join(lines), max_chars=_PLAIN_DIGEST_MAX_CHARS)


def _ticket_done_body_elements(summary: str) -> list[dict[str, Any]]:
    sections = _parse_ticket_done_sections(summary)
    if sections:
        return [_section_element(title, content) for title, content in sections]
    return [_section_element("摘要", _plain_summary_digest(summary))]


def ticket_done_card(*, ticket_id: str, summary: str, zendesk_url: str) -> dict[str, Any]:
    """🟢 工单处理完成。"""
    actions: list[dict[str, Any]] = []
    if zendesk_url:
        actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "查看 Zendesk"},
            "url": zendesk_url, "type": "primary",
        })
    actions.append({
        "tag": "button",
        "text": {"tag": "plain_text", "content": "重新处理"},
        "value": {"command": "reprocess", "ticket_id": ticket_id},
        "type": "default",
    })
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"🟢 工单 #{ticket_id} 处理完成"},
            "template": "green",
        },
        "elements": [
            *_ticket_done_body_elements(summary),
            {"tag": "action", "actions": actions},
        ],
    }


def ticket_escalate_card(*, ticket_id: str, reason: str, zendesk_url: str) -> dict[str, Any]:
    """⚠️ 工单需要人工介入。"""
    actions: list[dict[str, Any]] = []
    if zendesk_url:
        actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "查看工单"},
            "url": zendesk_url, "type": "primary",
        })
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"⚠️ 工单 #{ticket_id} 需要人工介入"},
            "template": "orange",
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**原因**: {reason}"}},
            {"tag": "action", "actions": actions},
        ],
    }


def auth_renew_card(*, url: str, user: str) -> dict[str, Any]:
    """🔐 Teleport 认证快过期，请点击续期。"""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🔐 Teleport 证书需要续期"},
            "template": "red",
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content":
                f"用户 `{user}` 的 Teleport 证书即将过期。点击下方链接完成认证（约 10 秒）。"}},
            {"tag": "action", "actions": [
                {"tag": "button",
                 "text": {"tag": "plain_text", "content": "点击认证"},
                 "url": url, "type": "primary"},
            ]},
        ],
    }


def daily_summary_card(*, date: str, stats: dict[str, Any]) -> dict[str, Any]:
    """📊 每日报告。"""
    content = (
        f"**昨日处理工单**: {stats.get('total', 0)}\n"
        f"├─ 成功: {stats.get('success', 0)}\n"
        f"├─ 需人工介入: {stats.get('escalated', 0)}\n"
        f"└─ 失败: {stats.get('failed', 0)}\n\n"
        f"**退款工单**: {stats.get('refunds', 0)}\n"
        f"**未决工单**（等客户）: {stats.get('pending_customer', 0)}\n\n"
        f"**Token 消耗**: ~{stats.get('tokens', 0):,}\n"
        f"**Teleport 剩余**: {stats.get('teleport_remaining', 'N/A')}"
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"📊 zd-agent 每日报告 · {date}"},
            "template": "blue",
        },
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": content}}],
    }


def alert_card(*, title: str, message: str, urgent: bool = False) -> dict[str, Any]:
    """⚠️/🚨 通用告警。"""
    template = "red" if urgent else "yellow"
    prefix = "🚨 " if urgent else "⚠️ "
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": prefix + title},
            "template": template,
        },
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": message}}],
    }

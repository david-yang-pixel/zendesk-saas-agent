"""Internal Note HTML 渲染器：摘要卡片 + Markdown→HTML

将 AI 输出的结构化摘要和 Markdown 正文转换为 Zendesk 可渲染的 HTML。
使用 Zendesk 兼容的 inline style 子集（padding / background / border / color / font-weight）。
"""

from __future__ import annotations

from typing import Any

import mistune


_md = mistune.create_markdown(escape=False)

# ── 置信度映射 ──────────────────────────────────────
_CONFIDENCE_MAP: dict[str, dict[str, str]] = {
    "high": {"emoji": "🟢", "label": "High", "bg": "#dcfce7", "color": "#166534"},
    "medium": {"emoji": "🟡", "label": "Medium", "bg": "#fef9c3", "color": "#854d0e"},
    "low": {"emoji": "🔴", "label": "Low", "bg": "#fee2e2", "color": "#991b1b"},
}

# ── 修复状态配色 ──────────────────────────────────
_FIX_STATUS_STYLE: dict[str, dict[str, str]] = {
    "released": {"emoji": "✅", "bg": "#dcfce7", "color": "#166534"},
    "merged_unreleased": {"emoji": "🔵", "bg": "#dbeafe", "color": "#1e40af"},
    "in_progress": {"emoji": "🟡", "bg": "#fef9c3", "color": "#854d0e"},
    "not_fixed": {"emoji": "🔴", "bg": "#fee2e2", "color": "#991b1b"},
    "unknown": {"emoji": "❓", "bg": "#fef9c3", "color": "#854d0e"},
}

_LABEL_STYLE = "padding:8px 14px;font-weight:bold;color:#555;background:#f9fafb;border:1px solid #e5e7eb;width:100px"
_VALUE_STYLE = "padding:8px 14px;border:1px solid #e5e7eb"


def _pill(text: str, bg: str, color: str) -> str:
    """渲染小标签（pill badge）。"""
    return (
        f'<span style="display:inline-block;padding:2px 10px;border-radius:12px;'
        f'font-size:12px;font-weight:bold;background:{bg};color:{color}">{text}</span>'
    )


def _tag_pill(text: str) -> str:
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:4px;'
        f'font-size:11px;font-weight:bold;margin-right:4px;background:#eef2ff;color:#4338ca">'
        f'{text}</span>'
    )


def render_summary_card(summary: dict[str, Any]) -> str:
    """将结构化摘要渲染为 HTML 表格。"""
    rows: list[str] = []

    def _row(label: str, value_html: str) -> None:
        rows.append(
            f'<tr>'
            f'<td style="{_LABEL_STYLE}">{label}</td>'
            f'<td style="{_VALUE_STYLE}">{value_html}</td>'
            f'</tr>'
        )

    pt = summary.get("problem_type", "")
    if pt:
        _row("Type", _pill(pt, "#dbeafe", "#1e40af"))

    conf = summary.get("confidence", "medium")
    ci = _CONFIDENCE_MAP.get(conf, _CONFIDENCE_MAP["medium"])
    conf_reason = summary.get("confidence_reason", "")
    text = _pill(f'{ci["emoji"]} {ci["label"]}', ci["bg"], ci["color"])
    if conf_reason:
        text += f" &mdash; {conf_reason}"
    _row("Confidence", text)

    lang = summary.get("customer_language", "")
    if lang:
        _row("Language", lang)

    ver = summary.get("version", "")
    if ver:
        _row("Version", ver)

    finding = summary.get("key_finding", "")
    if finding:
        _row("Key Finding", f"<strong>{finding}</strong>")

    fix = summary.get("fix_status", "")
    if fix:
        fs = _FIX_STATUS_STYLE.get(fix, {"emoji": "", "bg": "#f3f4f6", "color": "#4b5563"})
        detail = summary.get("fix_status_detail", "")
        text = _pill(f'{fs["emoji"]} {fix}', fs["bg"], fs["color"])
        if detail:
            text += f" &mdash; {detail}"
        _row("Fix Status", text)

    needs = summary.get("needs_human", True)
    reason = summary.get("needs_human_reason", "")
    if needs:
        verdict = '<strong style="color:#dc2626">⚠️ Yes</strong>'
    else:
        verdict = '<strong style="color:#059669">✅ No</strong>'
    if reason:
        verdict += f" &mdash; {reason}"
    _row("Needs Review", verdict)

    tags = summary.get("tags", [])
    if tags:
        _row("Tags", " ".join(_tag_pill(t) for t in tags))

    rows_html = "\n".join(rows)
    return (
        f'<table style="width:100%;border-collapse:collapse;margin-bottom:20px">'
        f'<tr><td colspan="2" style="padding:10px 14px;font-weight:bold;font-size:15px;'
        f'background:#f0f4ff;color:#3b5bdb;border:1px solid #d0d7f7">📋 Summary</td></tr>'
        f'\n{rows_html}\n</table>'
    )


def _split_body_sections(body_markdown: str) -> dict[str, str]:
    """将 Markdown 正文按 ## 标题拆分为 {标题: 内容} 字典。"""
    sections: dict[str, str] = {}
    current_title = ""
    current_lines: list[str] = []

    for line in body_markdown.split("\n"):
        stripped = line.strip()
        if stripped.startswith("## "):
            if current_title:
                sections[current_title] = "\n".join(current_lines)
            current_title = stripped[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_title:
        sections[current_title] = "\n".join(current_lines)

    return sections


def _find_section(sections: dict[str, str], keys: list[str]) -> tuple[str, str] | None:
    """在 sections 中匹配标题（支持 emoji 前缀），匹配后从 sections 中删除。"""
    for key in keys:
        for title in list(sections.keys()):
            clean = title.lstrip("💬🔍✅ ").strip()
            if clean == key or title == key:
                content = sections.pop(title)
                return clean, content
    return None


def render_internal_note(
    summary: dict[str, Any],
    body_markdown: str,
) -> str:
    """将摘要 + Markdown 正文渲染为完整的 Internal Note HTML。"""
    parts: list[str] = []

    # Banner
    parts.append(
        '<div style="background:#f0f4ff;border-left:4px solid #4f6ef7;padding:10px 16px;'
        'margin-bottom:16px;font-size:13px;color:#4f6ef7;font-weight:bold">'
        '🤖 AI-generated reply for reference only.</div>'
    )

    # 摘要卡片
    if summary:
        parts.append(render_summary_card(summary))

    # 拆分正文 sections
    sections = _split_body_sections(body_markdown)

    result = _find_section(sections, ["Suggested Reply", "客户回复建议", "Customer Reply", "Reply Suggestion"])
    if result:
        title, content = result
        needs_human = summary.get("needs_human", True)
        confidence = summary.get("confidence", "medium")
        is_safe = not needs_human and confidence == "high"

        if is_safe:
            box_bg = "#f0fdf4"
            box_border = "#86efac"
            title_bg = "#dcfce7"
            title_color = "#166534"
        else:
            box_bg = "#fffbeb"
            box_border = "#fcd34d"
            title_bg = "#fef3c7"
            title_color = "#92400e"

        section_html = _md(content.strip())
        warn = ""
        if not is_safe:
            warn = '<p style="color:#92400e;font-weight:bold">⚠️ Low confidence — please review before sending</p>'

        parts.append(
            f'<div style="border:1px solid {box_border};border-radius:6px;margin-bottom:20px;overflow:hidden">'
            f'<div style="background:{title_bg};padding:10px 16px;font-weight:bold;color:{title_color};font-size:15px">'
            f'💬 {title}</div>'
            f'<div style="padding:14px 16px;background:{box_bg}">'
            f'{warn}{section_html}</div></div>'
        )

    # 分隔线
    parts.append(
        '<div style="border-top:1px solid #e5e7eb;margin:20px 0;padding-top:8px">'
        '<em style="color:#9ca3af;font-size:12px">Detailed analysis below for reference ↓</em></div>'
    )

    result = _find_section(sections, ["Internal Analysis", "内部分析", "Analysis"])
    if result:
        title, content = result
        section_html = _md(content.strip())
        parts.append(
            f'<div style="border:1px solid #e5e7eb;border-radius:6px;margin-bottom:16px;overflow:hidden">'
            f'<div style="background:#f9fafb;padding:10px 16px;font-weight:bold;color:#374151;font-size:14px">'
            f'🔍 {title}</div>'
            f'<div style="padding:14px 16px;font-size:13px;color:#4b5563">'
            f'{section_html}</div></div>'
        )

    result = _find_section(sections, ["Review Guide", "人工复核指引", "Reviewer Checklist"])
    if result:
        title, content = result
        section_html = _md(content.strip())
        parts.append(
            f'<div style="border:1px solid #fde68a;border-radius:6px;margin-bottom:16px;overflow:hidden">'
            f'<div style="background:#fef9c3;padding:10px 16px;font-weight:bold;color:#92400e;font-size:14px">'
            f'✅ {title}</div>'
            f'<div style="padding:14px 16px;font-size:13px;color:#4b5563">'
            f'{section_html}</div></div>'
        )

    # 剩余 section（兜底）
    for title, content in sections.items():
        section_html = _md(content.strip())
        parts.append(
            f'<div style="border:1px solid #e5e7eb;border-radius:6px;margin-bottom:16px;overflow:hidden">'
            f'<div style="background:#f9fafb;padding:10px 16px;font-weight:bold;color:#374151;font-size:14px">'
            f'{title}</div>'
            f'<div style="padding:14px 16px;font-size:13px;color:#4b5563">'
            f'{section_html}</div></div>'
        )

    # Footer
    parts.append(
        '<div style="border-top:1px solid #e5e7eb;margin-top:16px;padding-top:12px;'
        'font-size:12px;color:#9ca3af">'
        '<em>You can follow up by mentioning @support-agent in an internal note.</em></div>'
    )

    return "\n\n".join(parts)

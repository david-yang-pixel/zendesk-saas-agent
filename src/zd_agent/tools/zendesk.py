"""Zendesk 工具组：通过远程 Zendesk MCP 执行读写操作。"""

from __future__ import annotations

import base64
import binascii
import re
from pathlib import Path
from typing import Any

from zd.cli import (
    _collect_all_image_urls,
)

from zd_agent.config import get_config
from zd_agent.integrations.zendesk_mcp import call_zendesk_tool
from zd_agent.log import get_logger
from zd_agent.services.ticket_images import (
    collect_image_urls_from_attachments,
    extract_inline_image_urls,
    filename_from_url,
)
from zd_agent.services.ticket_message_parsing import normalize_messaging_content
from zd_agent.runtime.source_selector import suggest_sources
from zd_agent.runtime.image_evidence import (
    guard_against_stale_no_image_claim,
    guard_text_values_against_stale_no_image_claim,
)
from zd_agent.session.agent_session import AgentSession, TicketContext
from zd_agent.tools.base import Tool, ToolResult

logger = get_logger(module="tools.zendesk")

_INTERNAL_REPLY_BANNER = (
    "> **AI-generated reply for reference only.**"
)
def _mention_footer() -> str:
    """动态从 config 读 mention_trigger，避免老 agent 轮询到硬编码 @support-agent 造成循环。"""
    try:
        trigger = get_config().zendesk.mention_trigger or "@agent"
    except Exception:
        trigger = "@agent"
    return f"\n\n---\n*You can follow up by mentioning {trigger} in an internal note.*"

_JA_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")

# 语言检测前剔除技术内容，避免英文日志/代码干扰判断
_FENCED_CODE_RE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_URL_RE = re.compile(r"https?://\S+")
_TECHNICAL_LINE_RE = re.compile(
    r"^[ \t]*(?:"
    r"at\s+[\w.$<>/]+|"                          # Java/JS/Go 堆栈行
    r'File\s+"[^"]+"|'                            # Python 堆栈行
    r"Traceback\s+\(|"                            # Python traceback 头
    r"[\w.]*(?:Error|Exception|Warning)[:\s]|"    # 错误/异常行
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}|"         # 日志时间戳
    r"(?:WARN|INFO|DEBUG|ERROR|FATAL)[\s\[]|"     # 日志级别
    r"[A-Z_]{3,}=|"                               # ENV_VAR=value
    r"(?:kubectl|docker|helm|npm|pip|git)\s"       # CLI 命令
    r").*$",
    re.MULTILINE,
)


def _strip_technical_content(text: str) -> str:
    """剔除代码块、行内代码、URL、堆栈/日志行等技术内容，保留自然语言部分。"""
    result = _FENCED_CODE_RE.sub("", text)
    result = _INLINE_CODE_RE.sub("", result)
    result = _URL_RE.sub("", result)
    result = _TECHNICAL_LINE_RE.sub("", result)
    return result


def _count_script_chars(text: str) -> tuple[int, int, int]:
    """统计日文假名、CJK、拉丁字母数量。"""
    return (
        len(_JA_KANA_RE.findall(text)),
        len(_CJK_RE.findall(text)),
        len(_LATIN_RE.findall(text)),
    )


def _detect_reply_language(text: str) -> str:
    """根据文本中各语系字符占比判断语言。

    先剔除代码块/日志/URL 等技术内容，仅对客户自然语言做判断，
    避免英文报错日志导致中文/日文客户被误判为英文。
    """
    normalized = (text or "").strip()
    if not normalized:
        return ""

    cleaned = _strip_technical_content(normalized)
    ja_count, cjk_count, latin_count = _count_script_chars(cleaned)
    total = ja_count + cjk_count + latin_count

    # 剔除后无有效字符（客户只贴了代码/日志），回退到原文检测
    if total == 0:
        ja_count, cjk_count, latin_count = _count_script_chars(normalized)
        total = ja_count + cjk_count + latin_count
        if total == 0:
            return ""

    # 日语假名出现即优先判定为日语（假名几乎不会在非日语文本中大量出现）
    if ja_count > 0 and ja_count / total > 0.05:
        return "ja"
    # 中文 vs 英文按占比判断，避免英文邮件中混入少量中文人名/术语被误判
    if cjk_count > latin_count:
        return "zh"
    if latin_count > 0:
        return "en"
    return ""


def _language_label(language: str) -> str:
    return {
        "ja": "Japanese",
        "zh": "Chinese",
        "en": "English",
    }.get(language, language or "unknown")


def _extract_event_text(event: dict[str, Any]) -> str:
    content = event.get("content") or {}
    if content.get("type") == "formResponse":
        return ""
    return str(normalize_messaging_content(content)["body"]).strip()


def _is_external_author(author_type: str) -> bool:
    normalized = (author_type or "").strip().lower()
    if not normalized:
        return False
    external_markers = ("end-user", "requester", "customer", "user", "external")
    internal_markers = ("agent", "admin", "staff", "support", "ai_agent")
    return any(marker in normalized for marker in external_markers) and not any(
        marker in normalized for marker in internal_markers
    )


def _extract_reply_language_hint(
    *, thread_type: str, thread_items: list[dict[str, Any]], ticket_description: str,
) -> dict[str, str] | None:
    """从最近一条客户公开消息中提取回复语言建议。"""
    if thread_type == "conversation_log":
        for event in reversed(thread_items):
            author = event.get("author") or {}
            if not _is_external_author(str(author.get("type", ""))):
                continue
            body = _extract_event_text(event)
            language = _detect_reply_language(body)
            if language:
                return {"language": language, "evidence": body[:200]}
    else:
        for comment in reversed(thread_items):
            if not comment.get("public"):
                continue
            body = str(comment.get("body") or comment.get("plain_body") or "").strip()
            language = _detect_reply_language(body)
            if language:
                return {"language": language, "evidence": body[:200]}

    fallback = (ticket_description or "").strip()
    language = _detect_reply_language(fallback)
    if language:
        return {"language": language, "evidence": fallback[:200]}
    return None


def _format_custom_fields(
    custom_fields: list[dict[str, Any]], field_map: dict[int, dict[str, Any]],
) -> list[str]:
    """将工单的 custom_fields 数组格式化为可读文本行"""
    lines: list[str] = []
    for cf in custom_fields:
        fid = cf.get("id")
        value = cf.get("value")
        if value is None or value == "" or value == []:
            continue

        meta = field_map.get(fid)
        if not meta:
            lines.append(f"- field_{fid}: {value}")
            continue

        title = meta["title"]

        # 对 tagger / multiselect 类型展示可读 label
        if meta["options"]:
            if isinstance(value, list):
                readable = [meta["options"].get(str(v), str(v)) for v in value]
                display_val = ", ".join(readable)
            else:
                display_val = meta["options"].get(str(value), str(value))
        elif isinstance(value, bool):
            display_val = "是" if value else "否"
        else:
            display_val = str(value)

        lines.append(f"- **{title}**: {display_val}")

    return lines


def _format_mcp_error(tool_name: str, payload: dict[str, Any]) -> str:
    code = payload.get("code") or "ZENDESK_MCP_ERROR"
    error = payload.get("error") or "unknown error"
    return f"Zendesk MCP {tool_name} failed [{code}]: {error}"


def _extract_custom_field_values(
    raw_custom: list[dict[str, Any]], field_map: dict[int, dict[str, Any]],
) -> dict[str, str]:
    """把 Zendesk 自定义字段整理成 title -> 可读值。"""
    values: dict[str, str] = {}

    for item in raw_custom:
        field_id = item.get("id")
        value = item.get("value")
        if field_id is None or value in (None, "", []):
            continue

        meta = field_map.get(field_id)
        if not meta:
            continue

        title = meta["title"]
        options = meta["options"]
        if options:
            if isinstance(value, list):
                display_val = ", ".join(options.get(str(v), str(v)) for v in value)
            else:
                display_val = options.get(str(value), str(value))
        elif isinstance(value, bool):
            display_val = "是" if value else "否"
        else:
            display_val = str(value)

        values[title] = display_val

    return values


def _format_source_recommendations(custom_fields: dict[str, str], ticket: dict[str, Any]) -> list[str]:
    """根据工单内容给出默认知识源建议。"""
    recommendations = suggest_sources(
        subject=ticket.get("subject", ""),
        description=ticket.get("description", ""),
        product=custom_fields.get("Product", ""),
        tags=ticket.get("tags", []),
        custom_fields=custom_fields,
    )

    tool_hints = {
        "zendesk_kb": "zendesk_search_articles / zendesk_get_article",
        "local_knowledge": "knowledge_search / knowledge_read",
        "official_docs_enterprise": "official_docs_search(source=official_docs_enterprise) / official_docs_read",
        "official_docs_community": "official_docs_search(source=official_docs_community) / official_docs_read",
        "source_code": "list_repos / list_repo_tags / sandbox_search / sandbox_read",
        "attachments": "zendesk_get_attachments + sandbox_read",
    }

    lines = ["## 初始知识源建议", "以下建议由代码规则生成，仅作为默认起点，你仍需根据证据动态调整：", ""]
    for item in recommendations[:5]:
        lines.append(f"- **{item.source}**: {item.reason}")
        if hint := tool_hints.get(item.source):
            lines.append(f"  - 建议工具：`{hint}`")
    lines.append("")
    return lines


# ── 工具定义 ──────────────────────────────────────────────────


def _coerce_positive_int_arg(args: dict[str, Any], name: str) -> int | ToolResult:
    value = args.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return ToolResult(success=False, error=f"{name} must be a positive integer")
    return value


def _coerce_required_string_arg(args: dict[str, Any], name: str) -> str | ToolResult:
    value = args.get(name)
    if not isinstance(value, str):
        return ToolResult(success=False, error=f"{name} must be a string")
    return value


def _coerce_optional_string_arg(
    args: dict[str, Any],
    name: str,
    *,
    default: str,
) -> str | ToolResult:
    value = args.get(name, default)
    if not isinstance(value, str):
        return ToolResult(success=False, error=f"{name} must be a string")
    return value


def _image_record(url: str, *, source: str, index: int, file_name: str = "") -> dict[str, Any]:
    return {
        "url": url,
        "file_name": file_name or filename_from_url(url),
        "source": source,
        "index": index,
    }


def _dedupe_image_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        url = str(record.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append({**record, "url": url})
    return out


def _collect_additional_image_urls(
    thread_type: str, thread_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if thread_type == "conversation_log":
        for index, event in enumerate(thread_items, 1):
            content = event.get("content") or {}
            content_type = content.get("type", "")
            if content_type == "image":
                url = str(content.get("media_url") or "").strip()
                if url:
                    records.append(_image_record(
                        url,
                        source="conversation_image",
                        index=index,
                        file_name=content.get("alt_text") or "",
                    ))
            for field in ("body", "text", "text_fallback"):
                for url in extract_inline_image_urls(content.get(field)):
                    records.append(_image_record(
                        url, source="conversation_inline_image", index=index,
                    ))
            for url in collect_image_urls_from_attachments(event.get("attachments")):
                records.append(_image_record(
                    url, source="conversation_attachment", index=index,
                ))
    else:
        for index, comment in enumerate(thread_items, 1):
            for field in ("body", "html_body", "plain_body"):
                for url in extract_inline_image_urls(comment.get(field)):
                    records.append(_image_record(
                        url, source="comment_inline_image", index=index,
                    ))
            for url in collect_image_urls_from_attachments(comment.get("attachments")):
                records.append(_image_record(
                    url, source="comment_attachment", index=index,
                ))
    return _dedupe_image_records(records)


def _collect_ticket_image_urls(
    thread_type: str, thread_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return _dedupe_image_records(
        list(_collect_all_image_urls(thread_type, thread_items))
        + _collect_additional_image_urls(thread_type, thread_items)
    )


def _mcp_ticket_thread(data: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    conversation = data.get("conversation_log") or {}
    if isinstance(conversation, dict):
        events = conversation.get("events")
        if isinstance(events, list):
            return "conversation_log", [e for e in events if isinstance(e, dict)]
        nested = conversation.get("conversation_log")
        if isinstance(nested, dict) and isinstance(nested.get("events"), list):
            return "conversation_log", [
                e for e in nested["events"] if isinstance(e, dict)
            ]
    comments = data.get("comments")
    if isinstance(comments, list):
        return "comments", [c for c in comments if isinstance(c, dict)]
    return "comments", []


def _mcp_attachment_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    attachments = data.get("attachments") or []
    rows: list[dict[str, Any]] = []
    if not isinstance(attachments, list):
        return rows
    for index, attachment in enumerate(attachments, 1):
        if not isinstance(attachment, dict):
            continue
        url = str(attachment.get("content_url") or attachment.get("url") or "").strip()
        if not url:
            continue
        rows.append(
            {
                "url": url,
                "file_name": attachment.get("file_name")
                or attachment.get("filename")
                or attachment.get("name")
                or filename_from_url(url),
                "content_type": attachment.get("content_type")
                or attachment.get("mime_type")
                or "",
                "source": attachment.get("source") or "zendesk_mcp",
                "index": attachment.get("comment_id")
                or attachment.get("event_id")
                or index,
            }
        )
    return rows


def _safe_attachment_name(name: str, url: str, index: int) -> str:
    candidate = str(name or filename_from_url(url) or f"attachment-{index}").strip()
    candidate = candidate.replace("\x00", "")
    candidate = Path(candidate).name
    if not candidate or candidate in {".", ".."}:
        candidate = f"attachment-{index}"
    return candidate


def _dedupe_attachment_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        url = str(record.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append({**record, "url": url})
    return out


async def _download_attachment_records_via_mcp(
    records: list[dict[str, Any]],
    out_dir: Path,
) -> tuple[dict[str, Path], list[dict[str, Any]], dict[str, Path]]:
    downloaded: dict[str, Path] = {}
    failed: list[dict[str, Any]] = []
    skipped: dict[str, Path] = {}
    out_dir.mkdir(parents=True, exist_ok=True)

    for index, record in enumerate(records, 1):
        url = str(record.get("url") or "").strip()
        if not url:
            continue
        file_name = _safe_attachment_name(str(record.get("file_name") or ""), url, index)
        save_path = out_dir / file_name
        if save_path.exists():
            skipped[url] = save_path
            continue

        payload = await call_zendesk_tool(
            "download_attachment",
            {"content_url": url, "file_name": file_name},
        )
        if not payload.get("success"):
            failed.append({
                **record,
                "file_name": file_name,
                "reason": _format_mcp_error("download_attachment", payload),
            })
            continue
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            failed.append({**record, "file_name": file_name, "reason": "invalid MCP payload"})
            continue
        encoded = data.get("data_base64")
        if not isinstance(encoded, str) or not encoded:
            failed.append({**record, "file_name": file_name, "reason": "missing data_base64"})
            continue
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as e:
            failed.append({**record, "file_name": file_name, "reason": str(e)[:80]})
            continue

        response_name = _safe_attachment_name(
            str(data.get("file_name") or file_name),
            url,
            index,
        )
        save_path = out_dir / response_name
        save_path.write_bytes(content)
        downloaded[url] = save_path

    return downloaded, failed, skipped


class ZendeskGetTicket(Tool):
    name = "zendesk_get_ticket"
    description = (
        "获取 Zendesk 工单详情，包含自定义字段（Product、Version、Environment、Plan 等）、"
        "完整对话记录和附件列表"
    )
    parameters = {
        "type": "object",
        "properties": {
            "ticket_id": {
                "type": "integer",
                "description": "工单 ID",
            },
        },
        "required": ["ticket_id"],
    }

    group = "zendesk"

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        tid = _coerce_positive_int_arg(args, "ticket_id")
        if isinstance(tid, ToolResult):
            return tid
        try:
            payload = await call_zendesk_tool(
                "get_ticket",
                {
                    "ticket_id": tid,
                    "include_comments": True,
                    "include_conversation_log": True,
                    "force_refresh": True,
                },
            )
            if not payload.get("success"):
                return ToolResult(
                    success=False,
                    error=_format_mcp_error("get_ticket", payload),
                )
            tdata = payload.get("data") or {}
            ticket = tdata.get("ticket", tdata)
            if not isinstance(ticket, dict):
                return ToolResult(
                    success=False,
                    error="Zendesk MCP get_ticket returned invalid ticket payload",
                )
            thread_type, thread_items = _mcp_ticket_thread(tdata)

            # 收集图片 URL 供多模态输入
            collected_images = _collect_ticket_image_urls(thread_type, thread_items)

            lines = [
                f"# 工单 #{tid}",
                f"**主题**: {ticket.get('subject', 'N/A')}",
                f"**状态**: {ticket.get('status', 'N/A')}",
                f"**优先级**: {ticket.get('priority', 'N/A')}",
                f"**创建时间**: {ticket.get('created_at', 'N/A')}",
                f"**更新时间**: {ticket.get('updated_at', 'N/A')}",
                f"**标签**: {', '.join(ticket.get('tags', [])) or 'N/A'}",
                f"**请求者 ID**: {ticket.get('requester_id', 'N/A')}",
                f"**受理人 ID**: {ticket.get('assignee_id', 'N/A')}",
                f"**组织 ID**: {ticket.get('organization_id', 'N/A')}",
                "",
            ]

            field_map: dict[int, dict[str, Any]] = {}
            custom_fields: dict[str, str] = {}

            # 解析自定义字段（Product / Version / Environment / Plan 等）
            raw_custom = ticket.get("custom_fields") or ticket.get("fields") or []
            if raw_custom:
                # Field metadata stays inside Zendesk MCP. Until that service exposes
                # readable field labels, preserve raw field_id/value rows here.
                field_map = {}
                custom_fields = _extract_custom_field_values(raw_custom, field_map)
                cf_lines = _format_custom_fields(raw_custom, field_map)
                if cf_lines:
                    lines.append("## 工单字段")
                    lines.extend(cf_lines)
                    lines.append("")

            session.context = TicketContext(
                ticket_id=tid,
                subject=ticket.get("subject", ""),
                description=ticket.get("description", ""),
                customer_language="",
                customer_version=custom_fields.get("Version", ""),
                deployment_method=custom_fields.get("Environment", ""),
                raw_data={
                    "ticket": ticket,
                    "tags": ticket.get("tags", []),
                    "custom_fields": custom_fields,
                    "field_map": field_map,
                },
            )

            lines.extend(_format_source_recommendations(custom_fields, ticket))

            reply_language_hint = _extract_reply_language_hint(
                thread_type=thread_type,
                thread_items=thread_items,
                ticket_description=ticket.get("description", ""),
            )

            # @mention 触发时，hint 包含触发评论原文——
            # 如果公开消息检测不到语言，从 hint fallback 检测
            if not reply_language_hint and session.hint:
                hint_lang = _detect_reply_language(session.hint)
                if hint_lang:
                    reply_language_hint = {
                        "language": hint_lang,
                        "evidence": session.hint[:200],
                        "source": "mention_hint",
                    }

            if reply_language_hint:
                session.context.customer_language = reply_language_hint["language"]
                session.context.raw_data["reply_language_hint"] = reply_language_hint
                source_label = (
                    "（来源: @mention 触发评论）"
                    if reply_language_hint.get("source") == "mention_hint"
                    else ""
                )
                lines.extend([
                    "## 回复语言建议（初步检测，仅供参考）",
                    f"- 以下为基于字符统计的初步检测结果，仅供参考{source_label}",
                    "- 请你结合对话全文独立判断客户实际使用的语言，并使用该语言撰写回复",
                    (
                        f"- 初步检测: {reply_language_hint['language']} "
                        f"({ _language_label(reply_language_hint['language']) })"
                    ),
                    f"- 参考文本: {reply_language_hint['evidence']}",
                    "",
                ])

            if collected_images:
                lines.append(f"**截图数量**: {len(collected_images)} 张（已自动提交给你查看）")
                lines.append("")

            lines.append("## 对话记录")

            if thread_type == "conversation_log":
                for i, event in enumerate(thread_items, 1):
                    author = (event.get("author") or {}).get("display_name", "?")
                    author_type = (event.get("author") or {}).get("type", "")
                    created = event.get("created_at", "")
                    content = event.get("content") or {}

                    body = normalize_messaging_content(content)["body"]

                    lines.append(f"\n### 消息 #{i} ({author} [{author_type}], {created})")
                    lines.append(body.strip() if body else "(空)")

                    attachments = event.get("attachments", [])
                    if attachments:
                        lines.append("\n**附件:**")
                        for att in attachments:
                            fname = att.get("file_name") or att.get("alt_text", "unknown")
                            lines.append(f"  - {fname}")
            else:
                for i, c in enumerate(thread_items, 1):
                    author = c.get("author_id", "unknown")
                    public = "公开" if c.get("public") else "内部"
                    body = c.get("body", c.get("plain_body", "")).strip()

                    lines.append(f"\n### 回复 #{i} (作者ID: {author}, {public})")
                    lines.append(body if body else "(空)")

                    attachments = c.get("attachments", [])
                    if attachments:
                        lines.append("\n**附件:**")
                        for att in attachments:
                            fname = att.get("file_name", "unknown")
                            ctype = att.get("content_type", "")
                            size = att.get("size", 0)
                            size_str = f"{size // 1024}KB" if size >= 1024 else f"{size}B"
                            lines.append(f"  - {fname} ({ctype}, {size_str})")

            # 将图片 URL 存到 session，由 tool_loop 注入多模态消息
            if collected_images:
                for img in collected_images:
                    url = img.get("url", "")
                    if url and not url.startswith("data:"):
                        session.image_urls.append({
                            "url": url,
                            "file_name": img.get("file_name", "image"),
                            "source": img.get("source", ""),
                            "comment_index": img.get("index", 0),
                        })

            return ToolResult(output="\n".join(lines))

        except Exception as e:
            return ToolResult(success=False, error=f"{type(e).__name__}: {e}")


class ZendeskGetAttachments(Tool):
    name = "zendesk_get_attachments"
    description = (
        "下载工单的所有附件和图片到本地目录。"
        "返回已下载文件列表和路径，文本类附件可用 sandbox_read 读取内容。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "ticket_id": {
                "type": "integer",
                "description": "工单 ID",
            },
        },
        "required": ["ticket_id"],
    }

    group = "zendesk"

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        tid = _coerce_positive_int_arg(args, "ticket_id")
        if isinstance(tid, ToolResult):
            return tid

        if not session.sandbox_dir:
            return ToolResult(success=False, error="Sandbox 未初始化")

        try:
            payload = await call_zendesk_tool(
                "list_attachments",
                {
                    "ticket_id": tid,
                    "include_conversation_log": True,
                    "force_refresh": True,
                },
            )
            if not payload.get("success"):
                return ToolResult(
                    success=False,
                    error=_format_mcp_error("list_attachments", payload),
                )
            data = payload.get("data") or {}
            if not isinstance(data, dict):
                return ToolResult(
                    success=False,
                    error="Zendesk MCP list_attachments returned invalid payload",
            )
            att_rows = _mcp_attachment_rows(data)
            ticket_payload = await call_zendesk_tool(
                "get_ticket",
                {
                    "ticket_id": tid,
                    "include_comments": True,
                    "include_conversation_log": True,
                    "force_refresh": True,
                },
            )
            thread_images: list[dict[str, Any]] = []
            if ticket_payload.get("success"):
                ticket_data = ticket_payload.get("data") or {}
                if isinstance(ticket_data, dict):
                    thread_type, thread_items = _mcp_ticket_thread(ticket_data)
                    thread_images = _collect_ticket_image_urls(thread_type, thread_items)
            else:
                logger.warning(
                    "zendesk_mcp_attachment_thread_image_probe_failed",
                    ticket_id=tid,
                    error=ticket_payload.get("error"),
                    code=ticket_payload.get("code"),
                )

            att_urls = {r.get("url") for r in att_rows if r.get("url")}
            extra_images = [img for img in thread_images if img.get("url") not in att_urls]

            total = len(att_rows) + len(extra_images)
            if total == 0:
                return ToolResult(output="该工单没有附件或图片")

            out_dir = session.sandbox_dir / "attachments"
            download_records = _dedupe_attachment_records(att_rows + extra_images)
            downloaded, failed, skipped = await _download_attachment_records_via_mcp(
                download_records,
                out_dir,
            )

            lines = [f"附件下载目录: {out_dir}"]
            if downloaded:
                lines.append(f"\n已下载 {len(downloaded)} 个文件:")
                for url, path in downloaded.items():
                    lines.append(f"  ✓ {path.name}")
            if skipped:
                lines.append(f"\n复用本地缓存 {len(skipped)} 个:")
                for url, path in skipped.items():
                    lines.append(f"  ↺ {path.name}")
            if failed:
                lines.append(f"\n下载失败 {len(failed)} 个:")
                for f_item in failed:
                    lines.append(f"  ✗ {f_item.get('file_name', '?')} — {f_item.get('reason', '?')}")

            return ToolResult(output="\n".join(lines))

        except Exception as e:
            return ToolResult(success=False, error=f"{type(e).__name__}: {e}")


class ZendeskSearch(Tool):
    name = "zendesk_search"
    description = "在 Zendesk 中搜索历史工单"
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词（支持 Zendesk 搜索语法）",
            },
        },
        "required": ["query"],
    }

    group = "zendesk"
    max_output_tokens = 4000

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        query = _coerce_required_string_arg(args, "query")
        if isinstance(query, ToolResult):
            return query
        try:
            payload = await call_zendesk_tool("search_tickets", {"query": query})
            if not payload.get("success"):
                return ToolResult(
                    success=False,
                    error=_format_mcp_error("search_tickets", payload),
                )
            data = payload.get("data") or {}
            results = data.get("results", [])
            if not results:
                return ToolResult(output="未找到匹配的历史工单")

            total = data.get("count", len(results))
            lines = [f"找到 {total} 个相关工单:\n"]
            for r in results[:10]:
                lines.append(
                    f"- **#{r.get('id')}** {r.get('subject', 'N/A')} "
                    f"(状态: {r.get('status', 'N/A')})"
                )
            if total > 10:
                lines.append(f"\n... 共 {total} 条，仅显示前 10 条")
            return ToolResult(output="\n".join(lines))

        except Exception as e:
            return ToolResult(success=False, error=f"{type(e).__name__}: {e}")


class ZendeskSearchArticles(Tool):
    name = "zendesk_search_articles"
    description = "搜索 Zendesk Help Center 知识库文章"
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词",
            },
            "locale": {
                "type": "string",
                "description": "语言区域（默认 zh-cn）",
            },
        },
        "required": ["query"],
    }

    group = "zendesk"
    max_output_tokens = 4000

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        query = _coerce_required_string_arg(args, "query")
        if isinstance(query, ToolResult):
            return query
        locale = _coerce_optional_string_arg(args, "locale", default="zh-cn")
        if isinstance(locale, ToolResult):
            return locale
        try:
            payload = await call_zendesk_tool(
                "search_help_center",
                {"query": query, "locale": locale},
            )
            if not payload.get("success"):
                return ToolResult(
                    success=False,
                    error=_format_mcp_error("search_help_center", payload),
                )
            data = payload.get("data") or {}
            results = data.get("results", [])
            if not results:
                return ToolResult(output="未找到匹配的知识库文章")

            lines = [f"找到 {len(results)} 篇相关文章:\n"]
            for a in results[:10]:
                lines.append(
                    f"- **[{a.get('id')}]** {a.get('title', 'N/A')}"
                )
                if a.get("html_url"):
                    lines.append(f"  链接: {a['html_url']}")
            return ToolResult(output="\n".join(lines))

        except Exception as e:
            return ToolResult(success=False, error=f"{type(e).__name__}: {e}")


class ZendeskGetArticle(Tool):
    name = "zendesk_get_article"
    description = "获取 Zendesk Help Center 单篇文章详情"
    parameters = {
        "type": "object",
        "properties": {
            "article_id": {
                "type": "integer",
                "description": "文章 ID",
            },
            "locale": {
                "type": "string",
                "description": "语言区域（默认 zh-cn）",
            },
        },
        "required": ["article_id"],
    }

    group = "zendesk"

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        article_id = _coerce_positive_int_arg(args, "article_id")
        if isinstance(article_id, ToolResult):
            return article_id
        locale = _coerce_optional_string_arg(args, "locale", default="zh-cn")
        if isinstance(locale, ToolResult):
            return locale
        try:
            payload = await call_zendesk_tool(
                "get_article",
                {"article_id": article_id, "locale": locale},
            )
            if not payload.get("success"):
                return ToolResult(
                    success=False,
                    error=_format_mcp_error("get_article", payload),
                )
            data = payload.get("data") or {}
            article = data.get("article", data)

            import re
            body_html = article.get("body", "")
            body_plain = re.sub(r"<[^>]+>", "", body_html).strip() if body_html else ""
            if len(body_plain) > 5000:
                body_plain = body_plain[:5000] + "\n\n... (内容过长，已截断)"

            lines = [
                f"# {article.get('title', '(无标题)')}",
                f"ID: {article.get('id')}",
                f"更新时间: {article.get('updated_at', 'N/A')}",
                f"链接: {article.get('html_url', 'N/A')}",
                "",
                body_plain,
            ]
            return ToolResult(output="\n".join(lines))

        except Exception as e:
            return ToolResult(success=False, error=f"{type(e).__name__}: {e}")


class ZendeskInternalReply(Tool):
    name = "zendesk_internal_reply"
    description = (
        "向工单发送 Internal Note（内部备注，客户不可见）。"
        "它只记录分析，不会自动同步 Public Reply，也不会算作客户已收到回复。"
        "需要回复客户时必须直接调用 zendesk_public_reply。"
        "必须同时提供 body（Markdown 正文）和 summary（结构化摘要），"
        "系统会自动将其渲染为带摘要卡片的 HTML 格式。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "ticket_id": {
                "type": "integer",
                "description": "工单 ID",
            },
            "body": {
                "type": "string",
                "description": "回复正文（Markdown 格式），应包含三段：## Suggested Reply、## Internal Analysis、## Review Guide",
            },
            "summary": {
                "type": "object",
                "description": "结构化摘要信息，用于渲染快速摘要卡片",
                "properties": {
                    "problem_type": {
                        "type": "string",
                        "description": "问题分类（如 code-bug / config-deploy / usage-howto / feature-request）",
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                        "description": (
                            "置信度判定标准（不要默认 medium 兜底）：\n"
                            "- high: 有直接证据（DB 行返回 / 源码具体行 / GitHub PR / 历史工单同类 / 官方文档明确条款）"
                            "证明根因或答案，且客户诉求无歧义。**大多数处理充分的工单都应该是 high**。\n"
                            "- medium: 证据是间接的或部分的（如：能查到现象但无法定位根因；或客户描述含 2-3 种可能解读）。\n"
                            "- low: 已按 strategies 段完整跑过所有必查路径仍无法定位（罕见）。\n"
                            "**不要因为'怕错'而打 medium**——agent 的 confidence 应该反映证据强度，不是反映安全感。"
                        ),
                    },
                    "confidence_reason": {
                        "type": "string",
                        "description": "置信度判断依据（如：来自源码验证 + 官方文档交叉确认）",
                    },
                    "customer_language": {
                        "type": "string",
                        "description": "客户使用的语言（如：中文 / English / 日本語）",
                    },
                    "version": {
                        "type": "string",
                        "description": "客户版本（如：Enterprise 3.7.3 (chart) / App 1.11.2）",
                    },
                    "key_finding": {
                        "type": "string",
                        "description": "一句话核心结论",
                    },
                    "fix_status": {
                        "type": "string",
                        "description": "Fix status (released / merged_unreleased / in_progress / not_fixed / unknown)",
                        "enum": ["released", "merged_unreleased", "in_progress", "not_fixed", "unknown"],
                    },
                    "fix_status_detail": {
                        "type": "string",
                        "description": "修复状态补充说明（如版本号 v1.12.0）",
                    },
                    "needs_human": {
                        "type": "boolean",
                        "description": (
                            "是否需要人工复核才能发送给客户。**默认应该是 false**——"
                            "agent 写的 Suggested Reply 是为了 SE 可以直接 forward 给客户，"
                            "不是给 SE 改的。只有下列场景才设 true：\n"
                            "- refund / 计费纠纷（任何 Stripe 操作）\n"
                            "- 账号级敏感操作（封号 / 数据删除 / 转移）\n"
                            "- 不可重现的 platform_issue（agent 没拿到决定性证据，需 SRE 进一步 drill down）\n"
                            "- agent 已按 strategies 跑完所有必查路径仍 confidence=low\n"
                            "**不要因为'怕错'而 opt-in 人工审核**——大多数充分调查过的工单 needs_human 应该是 false。"
                        ),
                    },
                    "needs_human_reason": {
                        "type": "string",
                        "description": "needs_human=true 的具体原因（如：'退款金额需财务审批' / 'platform_issue 但 SigNoz 未见错误信号'）。needs_human=false 可省略。",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "工单标签列表",
                    },
                },
                "required": [
                    "problem_type", "confidence", "confidence_reason",
                    "customer_language", "key_finding", "needs_human",
                ],
            },
        },
        "required": ["ticket_id", "body", "summary"],
    }

    group = "zendesk"
    concurrency_safe = False
    is_write_operation = True

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        if "body" not in args:
            return ToolResult(success=False, error="body is required")
        if "summary" not in args:
            return ToolResult(success=False, error="summary is required")

        body_md = args["body"]
        summary = args.get("summary", {})
        ticket_id = _coerce_positive_int_arg(args, "ticket_id")
        if isinstance(ticket_id, ToolResult):
            return ticket_id
        if not isinstance(body_md, str):
            return ToolResult(success=False, error="body must be a string")
        if not body_md.strip():
            return ToolResult(success=False, error="body is empty")
        if not isinstance(summary, dict):
            return ToolResult(success=False, error="summary must be an object")
        body_md = guard_against_stale_no_image_claim(body_md, session)
        summary = guard_text_values_against_stale_no_image_claim(summary, session)

        if getattr(session, "reply_sent", False):
            logger.info(
                "internal_reply_skipped_public_already_sent",
                ticket_id=ticket_id,
            )
            return ToolResult(
                output=(
                    f"Skipped Internal Note for ticket #{ticket_id} "
                    "because a customer-visible public reply was already sent."
                ),
            )

        try:
            public_reply_enabled = bool(get_config().public_reply.enabled)
        except Exception:
            public_reply_enabled = False
        if public_reply_enabled and summary.get("needs_human") is False:
            return ToolResult(
                success=False,
                error=(
                    "needs_human=false means this ticket is customer-resolvable; "
                    "use zendesk_public_reply instead of zendesk_internal_reply."
                ),
            )

        # LLM 判定的客户语言（以 LLM 判断为准，覆盖字符统计的初步检测）
        llm_language = summary.get("customer_language", "")
        if llm_language and session.context:
            session.context.customer_language = llm_language

        # P2-9: 工具使用审计 - 检测 reply 里的 PR#/源码路径/版本号
        # 是否真的出现过在 tool_call_log 里，可能虚构 → 加 warning section
        try:
            from zd_agent.runtime.reply_audit import audit_reply_against_tools
            audit_warnings = audit_reply_against_tools(
                body=body_md,
                tool_call_log=session.tool_call_log,
                current_ticket_id=session.ticket_id,
            )
            if audit_warnings:
                logger.warning(
                    "reply_audit_warnings",
                    ticket_id=session.ticket_id,
                    warning_count=len(audit_warnings),
                    warnings=audit_warnings[:5],
                )
                # 把 warning 追加到 body_md 末尾，render_internal_note 会照样渲染
                body_md += (
                    "\n\n## ⚠️ Audit Warnings (potential hallucination)\n\n"
                    + "\n".join(f"- {w}" for w in audit_warnings)
                )
        except Exception as e:
            logger.warning(
                "reply_audit_failed",
                ticket_id=session.ticket_id, error=str(e)[:200],
            )

        # 纯文本版（作为 body 回退）
        plain_body = _prepend_internal_reply_banner(body_md)
        plain_body = _append_mention_footer(plain_body)

        session.reply_draft = plain_body

        if session.dry_run:
            return ToolResult(output="[Dry Run] 回复已保存到草稿，未实际发送")

        try:
            payload = await call_zendesk_tool(
                "add_internal_note",
                {
                    "ticket_id": ticket_id,
                    "body": plain_body,
                    "explicit_user_internal_note_request": "agent internal escalation",
                    "dry_run": False,
                    "confirm": True,
                    "actor": "zd-agent",
                },
            )
            if not payload.get("success"):
                return ToolResult(
                    success=False,
                    error=_format_mcp_error("add_internal_note", payload),
                )
        except Exception as e:
            return ToolResult(success=False, error=f"{type(e).__name__}: {e}")

        return ToolResult(
            output=(
                f"已发送 Internal Note 到工单 #{ticket_id} "
                "(not customer-visible; use zendesk_public_reply for customer replies)"
            ),
        )


# B 路径辅助函数 (2026-05-26)：从 agent 写的结构化 body 里抽客户面 Suggested
# Reply 段，丢给 public_reply。匹配 ## Suggested Reply / ## 客户回复建议
# 等中英文标题（含 emoji 前缀如 ## 💬 Suggested Reply）。
_SUGGESTED_REPLY_HEADERS = (
    "Suggested Reply",
    "客户回复建议",
    "Customer Reply",
    "Reply Suggestion",
    "建议客户回复",
)


def _extract_suggested_reply_section(body_md: str) -> str:
    """从 agent body 里抽 Suggested Reply 段正文。

    支持两种实际格式（agent 输出不一定带 ## 前缀）：
    1. ``## Suggested Reply\\n\\n<content>\\n## Next Section``（标准 markdown）
    2. ``💬 Suggested Reply\\n\\n<content>\\n⚠️ Audit Warnings``（实际 agent
       常见格式，emoji 充当 header 标志，**没有 ## 前缀**）

    截止条件：下一个 section header（## / emoji header / 'Audit Warnings' /
    'Internal Analysis' / 'Review Guide' / '人工指引' 等）或 body 末尾。

    返回 trim 后的正文，找不到返回空串。
    """
    # 下一段终止符：另一个 ## 标题，或常见 emoji header 行
    # （📋 / 💬 / 🔍 / ✅ / ⚠️ / _You can / _Detailed analysis），或 body 末尾
    next_section = (
        r"(?=\n##\s"
        r"|\n📋\s|\n💬\s|\n🔍\s|\n✅\s|\n⚠️\s"
        r"|\n_You can|\n_Detailed analysis"
        r"|\Z)"
    )
    for header in _SUGGESTED_REPLY_HEADERS:
        # 同时匹配：(a) "## [emoji ]Header" markdown 标准
        #          (b) "[emoji ]Header" 行首裸标题（实际 agent 输出）
        # 用同义的捕获：(?:##\s+)?\s*(?:emoji\s+)? 然后 header 文本
        pattern = re.compile(
            r"(?:^|\n)(?:##\s+)?(?:[💬📋🔍✅⚠️]+\s+)?"
            + re.escape(header)
            + r"\s*\n+(.*?)"
            + next_section,
            re.DOTALL,
        )
        m = pattern.search(body_md)
        if m:
            return m.group(1).strip()
    return ""


# Reply_type 推断：Suggested Reply 段含问句信号 → ask；否则 solution
_ASK_SIGNAL_PATTERNS = (
    "could you share", "could you provide", "could you confirm",
    "please share", "please provide", "please confirm",
    "请提供", "请确认", "能否分享", "能否提供",
)


def _infer_reply_type(suggested_reply: str) -> str:
    low = suggested_reply.lower()
    for sig in _ASK_SIGNAL_PATTERNS:
        if sig in low:
            return "ask"
    return "solution"


def _prepend_internal_reply_banner(body: str) -> str:
    """为 Internal Reply 统一添加顶部 AI 提示，避免重复追加。"""
    normalized = body.lstrip()
    if normalized.startswith(_INTERNAL_REPLY_BANNER):
        return body
    return f"{_INTERNAL_REPLY_BANNER}\n\n{body}"


def _append_mention_footer(body: str) -> str:
    """为 Internal Reply 追加 @mention 追问提示，避免重复追加。"""
    footer = _mention_footer()
    if body.endswith(footer):
        return body
    return body + footer


_ALLOWED_TAGS = frozenset({
    "bug", "feature", "plugin", "workflow-and-rag",
    "enterprise-backend", "dify-backend", "dify-frontend",
    "enterprise-frontend", "helm", "k8s",
})


class ZendeskAddTags(Tool):
    name = "zendesk_add_tags"
    description = (
        "为工单增量添加标签（不影响已有标签，不可删除标签）。"
        "可用标签：bug, feature, plugin, workflow-and-rag, "
        "enterprise-backend, dify-backend, dify-frontend, "
        "enterprise-frontend, helm, k8s。一次可加多个。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "ticket_id": {
                "type": "integer",
                "description": "工单 ID",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "要添加的标签列表，只能使用允许的标签："
                    "bug, feature, plugin, workflow-and-rag, "
                    "enterprise-backend, dify-backend, dify-frontend, "
                    "enterprise-frontend, helm, k8s"
                ),
            },
        },
        "required": ["ticket_id", "tags"],
    }

    group = "zendesk"
    concurrency_safe = False
    is_write_operation = True

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        ticket_id = _coerce_positive_int_arg(args, "ticket_id")
        if isinstance(ticket_id, ToolResult):
            return ticket_id
        if "tags" not in args:
            return ToolResult(success=False, error="tags is required")
        raw_tags = args["tags"]
        if not isinstance(raw_tags, list) or not all(isinstance(t, str) for t in raw_tags):
            return ToolResult(success=False, error="tags must be a list of strings")
        tags = list(dict.fromkeys(t.strip().lower() for t in raw_tags if t.strip()))
        if not tags:
            return ToolResult(success=False, error="标签列表不能为空")

        invalid = [t for t in tags if t not in _ALLOWED_TAGS]
        if invalid:
            return ToolResult(
                success=False,
                error=f"不允许的标签: {', '.join(invalid)}。"
                f"可用标签: {', '.join(sorted(_ALLOWED_TAGS))}",
            )

        if session.dry_run:
            return ToolResult(output=f"[Dry Run] 将为工单 #{ticket_id} 添加标签: {', '.join(tags)}")

        try:
            payload = await call_zendesk_tool(
                "update_ticket_metadata",
                {
                    "ticket_id": ticket_id,
                    "add_tags": tags,
                    "explicit_user_metadata_request": "agent add tags",
                    "dry_run": False,
                    "confirm": True,
                    "actor": "zd-agent",
                },
            )
            if not payload.get("success"):
                return ToolResult(
                    success=False,
                    error=_format_mcp_error("update_ticket_metadata", payload),
                )
            after = payload.get("after") or {}
            updated = after.get("tags", [])
            if not updated:
                updated = tags
            return ToolResult(output=f"工单 #{ticket_id} 标签已更新: {', '.join(updated)}")
        except Exception as e:
            return ToolResult(success=False, error=f"{type(e).__name__}: {e}")


class ZendeskFollowTicket(Tool):
    name = "zendesk_follow_ticket"
    description = (
        "关注 Zendesk 工单。默认让当前 MCP 配置的 Zendesk agent 账号关注该工单；"
        "可选指定 user_id 或 user_email。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "ticket_id": {
                "type": "integer",
                "description": "工单 ID",
            },
            "user_id": {
                "type": "integer",
                "description": "可选：要添加为 follower 的 Zendesk user_id",
            },
            "user_email": {
                "type": "string",
                "description": "可选：要添加为 follower 的邮箱；不填则使用 MCP 配置的 ZENDESK_EMAIL",
            },
        },
        "required": ["ticket_id"],
    }

    group = "zendesk"
    concurrency_safe = False
    is_write_operation = True

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        ticket_id = _coerce_positive_int_arg(args, "ticket_id")
        if isinstance(ticket_id, ToolResult):
            return ticket_id
        if "user_id" in args and "user_email" in args:
            return ToolResult(success=False, error="provide either user_id or user_email, not both")
        payload_args: dict[str, Any] = {
            "ticket_id": ticket_id,
            "explicit_user_follow_request": "agent follow ticket",
            "dry_run": bool(session.dry_run),
            "confirm": not bool(session.dry_run),
            "actor": "zd-agent",
        }
        if args.get("user_id") is not None:
            user_id = _coerce_positive_int_arg(args, "user_id")
            if isinstance(user_id, ToolResult):
                return user_id
            payload_args["user_id"] = user_id
        if args.get("user_email") is not None:
            user_email = _coerce_optional_string_arg(args, "user_email", default="")
            if isinstance(user_email, ToolResult):
                return user_email
            if not user_email.strip():
                return ToolResult(success=False, error="user_email is empty")
            payload_args["user_email"] = user_email.strip()

        try:
            payload = await call_zendesk_tool("follow_ticket", payload_args)
            if not payload.get("success"):
                return ToolResult(
                    success=False,
                    error=_format_mcp_error("follow_ticket", payload),
                )
            if session.dry_run:
                return ToolResult(output=f"[Dry Run] 将关注工单 #{ticket_id}")
            return ToolResult(output=f"已关注工单 #{ticket_id}")
        except Exception as e:
            return ToolResult(success=False, error=f"{type(e).__name__}: {e}")


class ZendeskGetUser(Tool):
    name = "zendesk_get_user"
    description = "获取 Zendesk 用户信息（根据用户 ID 查看客户或受理人信息）"
    parameters = {
        "type": "object",
        "properties": {
            "user_id": {
                "type": "integer",
                "description": "用户 ID",
            },
        },
        "required": ["user_id"],
    }

    group = "zendesk"

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        user_id = _coerce_positive_int_arg(args, "user_id")
        if isinstance(user_id, ToolResult):
            return user_id
        try:
            payload = await call_zendesk_tool("get_user", {"user_id": user_id})
            if not payload.get("success"):
                return ToolResult(
                    success=False,
                    error=_format_mcp_error("get_user", payload),
                )
            data = payload.get("data") or {}
            user = data.get("user", data)
            lines = [
                f"**姓名**: {user.get('name', 'N/A')}",
                f"**邮箱**: {user.get('email', 'N/A')}",
                f"**角色**: {user.get('role', 'N/A')}",
                f"**组织 ID**: {user.get('organization_id', 'N/A')}",
                f"**状态**: {'活跃' if user.get('active') else '非活跃'}",
            ]
            return ToolResult(output="\n".join(lines))

        except Exception as e:
            return ToolResult(success=False, error=f"{type(e).__name__}: {e}")

"""Read-only Zendesk evidence tools used by casework v2."""

from __future__ import annotations

import re
from typing import Any

from zd.cli import (
    _collect_all_image_urls,
)

from zd_agent.integrations.zendesk_mcp import call_zendesk_tool
from zd_agent.log import get_logger
from zd_agent.services.ticket_images import (
    collect_image_urls_from_attachments,
    extract_inline_image_urls,
    filename_from_url,
)
from zd_agent.services.ticket_message_parsing import normalize_messaging_content
from zd_agent.services.ticket_messages import load_complete_ticket_payload
from zd_agent.runtime.source_selector import suggest_sources
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolResult

logger = get_logger(module="tools.zendesk")

_JA_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")

# 语言检测前剔除技术内容，避免英文日志/代码干扰判断
_FENCED_CODE_RE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_URL_RE = re.compile(r"https?://\S+")
_TECHNICAL_LINE_RE = re.compile(
    r"^[ \t]*(?:"
    r"at\s+[\w.$<>/]+|"  # Java/JS/Go 堆栈行
    r'File\s+"[^"]+"|'  # Python 堆栈行
    r"Traceback\s+\(|"  # Python traceback 头
    r"[\w.]*(?:Error|Exception|Warning)[:\s]|"  # 错误/异常行
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}|"  # 日志时间戳
    r"(?:WARN|INFO|DEBUG|ERROR|FATAL)[\s\[]|"  # 日志级别
    r"[A-Z_]{3,}=|"  # ENV_VAR=value
    r"(?:kubectl|docker|helm|npm|pip|git)\s"  # CLI 命令
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
    if content.get("type") not in {"text", "html"}:
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
    *,
    thread_type: str,
    thread_items: list[dict[str, Any]],
    ticket_description: str,
) -> dict[str, str] | None:
    """从最近一条客户公开消息中提取回复语言建议。"""
    if thread_type == "conversation_log":
        for event in reversed(thread_items):
            event_type = str(event.get("type") or "")
            if event_type != "Messaging::ConversationMessage":
                continue
            metadata = event.get("metadata") or {}
            if event.get("public") is False or metadata.get("is_public") is False:
                continue
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
    custom_fields: list[dict[str, Any]],
    field_map: dict[int, dict[str, Any]],
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
    raw_custom: list[dict[str, Any]],
    field_map: dict[int, dict[str, Any]],
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


def _format_source_recommendations(
    custom_fields: dict[str, str], ticket: dict[str, Any]
) -> list[str]:
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
        "source_code": "list_repos / list_repo_tags / git_grep / git_show",
        "attachments": "工单快照已自动提取附件与视觉证据",
    }

    lines = [
        "## 初始知识源建议",
        "以下建议由代码规则生成，仅作为默认起点，你仍需根据证据动态调整：",
        "",
    ]
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


def _image_record(
    url: str, *, source: str, index: int, file_name: str = ""
) -> dict[str, Any]:
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
    thread_type: str,
    thread_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if thread_type == "conversation_log":
        for index, event in enumerate(thread_items, 1):
            content = event.get("content") or {}
            content_type = content.get("type", "")
            if content_type == "image":
                url = str(content.get("media_url") or "").strip()
                if url:
                    records.append(
                        _image_record(
                            url,
                            source="conversation_image",
                            index=index,
                            file_name=content.get("alt_text") or "",
                        )
                    )
            for field in ("body", "text", "text_fallback"):
                for url in extract_inline_image_urls(content.get(field)):
                    records.append(
                        _image_record(
                            url,
                            source="conversation_inline_image",
                            index=index,
                        )
                    )
            for url in collect_image_urls_from_attachments(event.get("attachments")):
                records.append(
                    _image_record(
                        url,
                        source="conversation_attachment",
                        index=index,
                    )
                )
    else:
        for index, comment in enumerate(thread_items, 1):
            for field in ("body", "html_body", "plain_body"):
                for url in extract_inline_image_urls(comment.get(field)):
                    records.append(
                        _image_record(
                            url,
                            source="comment_inline_image",
                            index=index,
                        )
                    )
            for url in collect_image_urls_from_attachments(comment.get("attachments")):
                records.append(
                    _image_record(
                        url,
                        source="comment_attachment",
                        index=index,
                    )
                )
    return _dedupe_image_records(records)


def _collect_ticket_image_urls(
    thread_type: str,
    thread_items: list[dict[str, Any]],
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
            tdata = await load_complete_ticket_payload(
                tid,
                zendesk_tool_caller=call_zendesk_tool,
            )
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
                source_label = (
                    "（来源: @mention 触发评论）"
                    if reply_language_hint.get("source") == "mention_hint"
                    else ""
                )
                lines.extend(
                    [
                        "## 回复语言建议（初步检测，仅供参考）",
                        f"- 以下为基于字符统计的初步检测结果，仅供参考{source_label}",
                        "- 请你结合对话全文独立判断客户实际使用的语言，并使用该语言撰写回复",
                        (
                            f"- 初步检测: {reply_language_hint['language']} "
                            f"({_language_label(reply_language_hint['language'])})"
                        ),
                        f"- 参考文本: {reply_language_hint['evidence']}",
                        "",
                    ]
                )

            if collected_images:
                lines.append(
                    f"**截图数量**: {len(collected_images)} 张（由 v2 工单快照视觉链路读取）"
                )
                lines.append("")

            lines.append("## 对话记录")

            if thread_type == "conversation_log":
                for i, event in enumerate(thread_items, 1):
                    author = (event.get("author") or {}).get("display_name", "?")
                    author_type = (event.get("author") or {}).get("type", "")
                    created = event.get("created_at", "")
                    content = event.get("content") or {}

                    body = normalize_messaging_content(content)["body"]

                    lines.append(
                        f"\n### 消息 #{i} ({author} [{author_type}], {created})"
                    )
                    lines.append(body.strip() if body else "(空)")

                    attachments = event.get("attachments", [])
                    if attachments:
                        lines.append("\n**附件:**")
                        for att in attachments:
                            fname = att.get("file_name") or att.get(
                                "alt_text", "unknown"
                            )
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
                            size_str = (
                                f"{size // 1024}KB" if size >= 1024 else f"{size}B"
                            )
                            lines.append(f"  - {fname} ({ctype}, {size_str})")

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
                lines.append(f"- **[{a.get('id')}]** {a.get('title', 'N/A')}")
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

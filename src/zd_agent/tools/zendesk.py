"""Zendesk 工具组：复用 zendesk-cli 的 ZendeskClient

zendesk-cli 是同步库（基于 requests），通过 asyncio.to_thread 桥接到异步环境。
包含 429 限流自动重试（指数退避 + Retry-After 头支持）。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any, TypeVar

from zd.client import ZendeskClient, ZendeskError
from zd.cli import (
    _collect_all_image_urls,
    _collect_attachment_rows,
    _download_ticket_images,
    _get_ticket_thread,
)

from zd_agent.log import get_logger
from zd_agent.runtime.source_selector import suggest_sources
from zd_agent.runtime.retry import RetryPolicy, get_retry_policy, retry_async
from zd_agent.session.agent_session import AgentSession, TicketContext
from zd_agent.tools.base import Tool, ToolResult

logger = get_logger(module="tools.zendesk")

_client: ZendeskClient | None = None
_INTERNAL_REPLY_BANNER = (
    "> **AI-generated reply for reference only.**"
)

# 字段 ID → 元数据缓存：{field_id: {"title": ..., "type": ..., "options": {value: label}}}
_field_map: dict[int, dict[str, Any]] | None = None


def _get_client() -> ZendeskClient:
    global _client
    if _client is None:
        _client = ZendeskClient()
    return _client


def _load_field_map(client: ZendeskClient) -> dict[int, dict[str, Any]]:
    """从 Zendesk Ticket Fields API 加载字段定义并构建映射表（同步调用）"""
    global _field_map
    if _field_map is not None:
        return _field_map

    try:
        resp = client.session.get(f"{client.base_url}/ticket_fields.json")
        resp.raise_for_status()
        fields = resp.json().get("ticket_fields", [])
    except Exception:
        logger.warning("ticket_fields_load_failed", exc_info=True)
        _field_map = {}
        return _field_map

    mapping: dict[int, dict[str, Any]] = {}
    for f in fields:
        fid = f.get("id")
        if not fid:
            continue
        options: dict[str, str] = {}
        for opt in f.get("custom_field_options", []):
            options[str(opt.get("value", ""))] = opt.get("name", opt.get("value", ""))
        mapping[fid] = {
            "title": f.get("title", f.get("title_in_portal", f"field_{fid}")),
            "type": f.get("type", "unknown"),
            "options": options,
        }

    _field_map = mapping
    logger.info("ticket_fields_loaded", count=len(mapping))
    return _field_map


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


def _format_error(e: ZendeskError) -> str:
    msg = f"Zendesk API [{e.status_code}]: {e.message}"
    if e.detail:
        msg += f" — {e.detail}"
    return msg


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
        "source_code": "list_repos / list_repo_tags / sandbox_create_worktree / sandbox_search / sandbox_read",
        "helm_watchdog": "helm_get_version_details / helm_get_values / helm_list_versions",
        "attachments": "zendesk_get_attachments + sandbox_read",
    }

    lines = ["## 初始知识源建议", "以下建议由代码规则生成，仅作为默认起点，你仍需根据证据动态调整：", ""]
    for item in recommendations[:5]:
        lines.append(f"- **{item.source}**: {item.reason}")
        if hint := tool_hints.get(item.source):
            lines.append(f"  - 建议工具：`{hint}`")
    lines.append("")
    return lines


T = TypeVar("T")


async def _call_with_rate_limit_retry(
    fn: Callable[..., T], *args: Any, **kwargs: Any,
) -> T:
    """封装 Zendesk API 调用，按全局配置执行重试"""

    async def _call() -> T:
        return await asyncio.to_thread(fn, *args, **kwargs)

    return await retry_async(
        operation=getattr(fn, "__name__", "zendesk_call"),
        policy=get_retry_policy("zendesk"),
        func=_call,
        should_retry=_should_retry_zendesk_error,
        get_delay=_zendesk_retry_delay,
    )


def _should_retry_zendesk_error(error: Exception) -> bool:
    """Zendesk 重试条件：429、网络失败（0）以及常见 5xx"""
    if not isinstance(error, ZendeskError):
        return False
    return error.status_code in {0, 429, 500, 502, 503, 504}


def _zendesk_retry_delay(
    error: Exception, attempt: int, policy: RetryPolicy,
) -> float | None:
    """Zendesk 重试延迟：优先尊重 Retry-After，其次走默认指数退避"""
    if isinstance(error, ZendeskError) and error.detail and error.detail.isdigit():
        delay = max(float(error.detail), policy.base_delay_seconds)
        logger.warning(
            "zendesk_retry_after_detected",
            attempt=attempt,
            retry_after=delay,
            status_code=error.status_code,
        )
        return delay
    return None


# ── 工具定义 ──────────────────────────────────────────────────


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

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        client = _get_client()
        tid = args["ticket_id"]

        try:
            tdata = await _call_with_rate_limit_retry(client.get_ticket, tid)
            ticket = tdata.get("ticket", tdata)

            thread_type, thread_items = await _call_with_rate_limit_retry(
                _get_ticket_thread, tid, ticket
            )

            # 收集图片 URL 供多模态输入
            collected_images = _collect_all_image_urls(thread_type, thread_items)

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
                field_map = await asyncio.to_thread(_load_field_map, client)
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
                    content_type = content.get("type", "")

                    body = ""
                    if content_type == "text":
                        body = content.get("text", "")
                    elif content_type == "html":
                        body = content.get("body", "")
                    elif content_type == "image":
                        body = f"[图片] {content.get('alt_text', '')}"
                    elif content_type == "formResponse":
                        body = content.get("text_fallback", "")
                    elif content_type == "form":
                        fields = content.get("fields", [])
                        field_lines = []
                        for f in fields:
                            label = f.get("label") or f.get("name", "")
                            val = f.get("text") or f.get("email") or ""
                            if not val:
                                selected = f.get("select") or []
                                val = ", ".join(
                                    item.get("label", "") for item in selected if item.get("label")
                                )
                            field_lines.append(f"  - {label}: {val}")
                        body = "[表单]\n" + "\n".join(field_lines)

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

        except ZendeskError as e:
            return ToolResult(success=False, error=_format_error(e))
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

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        client = _get_client()
        tid = args["ticket_id"]

        if not session.sandbox_dir:
            return ToolResult(success=False, error="Sandbox 未初始化")

        try:
            tdata = await _call_with_rate_limit_retry(client.get_ticket, tid)
            ticket = tdata.get("ticket", tdata)

            thread_type, thread_items = await _call_with_rate_limit_retry(
                _get_ticket_thread, tid, ticket
            )

            att_rows = _collect_attachment_rows(thread_type, thread_items)
            all_images = _collect_all_image_urls(thread_type, thread_items)
            att_urls = {r.get("url") for r in att_rows if r.get("url")}
            extra_images = [img for img in all_images if img.get("url") not in att_urls]

            total = len(att_rows) + len(extra_images)
            if total == 0:
                return ToolResult(output="该工单没有附件或图片")

            out_dir = session.sandbox_dir / "attachments"

            downloaded, failed, skipped = await asyncio.to_thread(
                _download_ticket_images, tid, all_images, out_dir
            )

            # 单独下载非图片附件
            non_image_atts = [a for a in att_rows if not a.get("content_type", "").startswith("image/")]
            for att in non_image_atts:
                url = att.get("url", "")
                fname = att.get("file_name", "unknown")
                if not url:
                    continue
                save_path = out_dir / fname
                if save_path.exists():
                    continue
                try:
                    import requests
                    from zd.config import config
                    resp = await asyncio.to_thread(
                        lambda: requests.get(url, auth=config.auth, timeout=60)
                    )
                    if resp.ok:
                        save_path.parent.mkdir(parents=True, exist_ok=True)
                        save_path.write_bytes(resp.content)
                        downloaded[url] = save_path
                except Exception as e:
                    failed.append({**att, "reason": str(e)[:80]})

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

        except ZendeskError as e:
            return ToolResult(success=False, error=_format_error(e))
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

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        client = _get_client()
        try:
            data = await _call_with_rate_limit_retry(client.search_tickets, args["query"])
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

        except ZendeskError as e:
            return ToolResult(success=False, error=_format_error(e))
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

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        client = _get_client()
        locale = args.get("locale", "zh-cn")
        try:
            data = await _call_with_rate_limit_retry(client.search_articles, args["query"], locale)
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

        except ZendeskError as e:
            return ToolResult(success=False, error=_format_error(e))
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

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        client = _get_client()
        locale = args.get("locale", "zh-cn")
        try:
            data = await _call_with_rate_limit_retry(client.get_article, args["article_id"], locale)
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

        except ZendeskError as e:
            return ToolResult(success=False, error=_format_error(e))
        except Exception as e:
            return ToolResult(success=False, error=f"{type(e).__name__}: {e}")


class ZendeskInternalReply(Tool):
    name = "zendesk_internal_reply"
    description = "向工单发送 Internal Note（内部备注，客户不可见）"
    parameters = {
        "type": "object",
        "properties": {
            "ticket_id": {
                "type": "integer",
                "description": "工单 ID",
            },
            "body": {
                "type": "string",
                "description": "回复内容（Markdown 格式）",
            },
        },
        "required": ["ticket_id", "body"],
    }

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        body = _prepend_internal_reply_banner(args["body"])
        session.reply_draft = body

        if session.dry_run:
            return ToolResult(output="[Dry Run] 回复已保存到草稿，未实际发送")

        client = _get_client()
        try:
            await _call_with_rate_limit_retry(
                client.reply_ticket, args["ticket_id"], body, False
            )
            session.reply_sent = True
            return ToolResult(output=f"已发送 Internal Note 到工单 #{args['ticket_id']}")

        except ZendeskError as e:
            return ToolResult(success=False, error=_format_error(e))
        except Exception as e:
            return ToolResult(success=False, error=f"{type(e).__name__}: {e}")


def _prepend_internal_reply_banner(body: str) -> str:
    """为 Internal Reply 统一添加顶部 AI 提示，避免重复追加。"""
    normalized = body.lstrip()
    if normalized.startswith(_INTERNAL_REPLY_BANNER):
        return body
    return f"{_INTERNAL_REPLY_BANNER}\n\n{body}"


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

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        client = _get_client()
        try:
            data = await _call_with_rate_limit_retry(client.get_user, args["user_id"])
            user = data.get("user", data)
            lines = [
                f"**姓名**: {user.get('name', 'N/A')}",
                f"**邮箱**: {user.get('email', 'N/A')}",
                f"**角色**: {user.get('role', 'N/A')}",
                f"**组织 ID**: {user.get('organization_id', 'N/A')}",
                f"**状态**: {'活跃' if user.get('active') else '非活跃'}",
            ]
            return ToolResult(output="\n".join(lines))

        except ZendeskError as e:
            return ToolResult(success=False, error=_format_error(e))
        except Exception as e:
            return ToolResult(success=False, error=f"{type(e).__name__}: {e}")

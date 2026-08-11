"""Ticket record tools: grep/read local 工单记录/ directory for historical tickets."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

from zd_agent.config import get_config
from zd_agent.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from zd_agent.session.agent_session import AgentSession


def _records_dir() -> Path:
    cfg = get_config()
    return Path(cfg.resources.base_dir) / cfg.resources.ticket_records_dir


def _normalize_keywords(value: Any) -> list[str] | ToolResult:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        if not all(isinstance(item, str) for item in value):
            return ToolResult(
                success=False,
                error="keywords items must be strings",
            )
        return [item.strip() for item in value if item.strip()]
    return []


def _coerce_max_results(value: Any) -> int | None:
    try:
        max_results = int(value)
    except (TypeError, ValueError):
        return None
    if max_results < 1:
        return None
    return min(max_results, 50)


class TicketRecordSearch(Tool):
    name = "ticket_record_search"
    description = (
        "在本地历史工单记录（zendesk-cli/工单记录/*.md）中按关键词搜索。"
        "多个关键词之间是 AND 关系（每个文件必须包含全部关键词才算命中）。"
        "返回匹配文件列表 + 每个文件中首个命中行的上下文。用于找相似历史问题。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "关键词列表（AND 语义）",
            },
            "max_results": {
                "type": "integer",
                "default": 10,
                "description": "最多返回多少个命中文件",
            },
        },
        "required": ["keywords"],
    }
    group = "knowledge"
    concurrency_safe = True

    async def execute(
        self, args: dict[str, Any], session: "AgentSession"
    ) -> ToolResult:
        keywords = _normalize_keywords(args.get("keywords"))
        if isinstance(keywords, ToolResult):
            return keywords
        kws_lower = [k.lower() for k in keywords]
        if not kws_lower:
            return ToolResult(success=False, error="keywords is empty")
        max_results = _coerce_max_results(args.get("max_results", 10))
        if max_results is None:
            return ToolResult(
                success=False,
                error="max_results must be a positive integer",
            )

        records_dir = _records_dir()
        if not records_dir.exists():
            return ToolResult(
                success=False,
                error=f"ticket records dir not found: {records_dir}",
            )

        matches: list[str] = []
        hit_count = 0
        for md_path in sorted(records_dir.glob("*.md")):
            if hit_count >= max_results:
                break
            try:
                text = md_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            text_lower = text.lower()
            if not all(kw in text_lower for kw in kws_lower):
                continue

            # Find first line containing the first keyword for context
            first_hit_line = ""
            for line in text.splitlines():
                if kws_lower[0] in line.lower():
                    first_hit_line = line.strip()
                    break

            matches.append(
                f"- {md_path.name}\n  > {first_hit_line[:200]}"
                if first_hit_line
                else f"- {md_path.name}"
            )
            hit_count += 1

        if not matches:
            return ToolResult(output="(no matching ticket records)")
        return ToolResult(
            output=f"Found {hit_count} matching tickets:\n" + "\n".join(matches)
        )


class TicketRecordRead(Tool):
    name = "ticket_record_read"
    description = "读取指定 ticket_id 的历史分析记录（如果存在于本地 工单记录/ 中）"
    parameters = {
        "type": "object",
        "properties": {
            "ticket_id": {"type": "string"},
        },
        "required": ["ticket_id"],
    }
    group = "knowledge"
    concurrency_safe = True

    async def execute(
        self, args: dict[str, Any], session: "AgentSession"
    ) -> ToolResult:
        raw_ticket_id = args.get("ticket_id", "")
        if not isinstance(raw_ticket_id, str):
            return ToolResult(success=False, error="ticket_id must be a string")
        ticket_id = raw_ticket_id.strip()
        if not ticket_id:
            return ToolResult(success=False, error="ticket_id is empty")
        if "/" in ticket_id or "\\" in ticket_id:
            return ToolResult(
                success=False,
                error="invalid ticket_id (path separator)",
            )

        records_dir = _records_dir()
        path = records_dir / f"{ticket_id}.md"
        try:
            path_resolved = path.resolve()
            records_resolved = records_dir.resolve()
            path_resolved.relative_to(records_resolved)
            if not records_resolved.exists():
                return ToolResult(
                    success=False,
                    error=f"ticket records dir not found: {records_dir}",
                )
            if not records_resolved.is_dir():
                return ToolResult(
                    success=False,
                    error=f"ticket records path is not a directory: {records_dir}",
                )
        except ValueError:
            return ToolResult(success=False, error="invalid ticket_id (path escape)")
        except Exception:
            return ToolResult(success=False, error="path resolution failed")
        if not path.exists():
            return ToolResult(
                success=False,
                error=f"no history record for ticket {ticket_id}",
            )
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return ToolResult(success=False, error=f"read failed: {e}")
        # Truncate if very long
        if len(text) > 20000:
            text = text[:20000] + "\n\n...(truncated)"
        return ToolResult(output=text)

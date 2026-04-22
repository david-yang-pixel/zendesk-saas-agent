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

    async def execute(self, args: dict[str, Any], session: "AgentSession") -> ToolResult:
        keywords = args.get("keywords") or []
        if not keywords:
            return ToolResult(success=False, error="keywords is empty")
        max_results = int(args.get("max_results", 10))

        records_dir = _records_dir()
        if not records_dir.exists():
            return ToolResult(
                success=False,
                error=f"ticket records dir not found: {records_dir}",
            )

        kws_lower = [k.lower() for k in keywords if k.strip()]
        if not kws_lower:
            return ToolResult(success=False, error="keywords all empty after strip")

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

    async def execute(self, args: dict[str, Any], session: "AgentSession") -> ToolResult:
        ticket_id = str(args.get("ticket_id", "")).strip()
        if not ticket_id:
            return ToolResult(success=False, error="ticket_id is empty")

        records_dir = _records_dir()
        path = records_dir / f"{ticket_id}.md"
        try:
            path_resolved = path.resolve()
            records_resolved = records_dir.resolve()
            if not str(path_resolved).startswith(str(records_resolved)):
                return ToolResult(
                    success=False,
                    error=f"invalid ticket_id (path escape)",
                )
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

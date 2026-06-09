"""归档工具：将工单分析结果追加到本地 zendesk-cli/工单记录/<id>.md

SaaS 版：单 markdown 文件，按 skill 规范；支持首次写入和跟进模式追加。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zd_agent.config import get_config
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolResult


def _records_dir() -> Path:
    cfg = get_config()
    return Path(cfg.resources.base_dir) / cfg.resources.ticket_records_dir


def _ticket_file(ticket_id: str | int) -> Path:
    return _records_dir() / f"{ticket_id}.md"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ArchiveTicket(Tool):
    name = "archive_ticket"
    description = (
        "将本次工单分析结果和建议回复草稿追加到本地工单记录 "
        "(<ticket_records_dir>/<ticket_id>.md)。首次写入创建新文件（带 frontmatter）；"
        "已存在则追加一段跟进记录。工单记录供 agent 下次处理该工单时参考（跟进模式）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "ticket_id": {
                "type": "integer",
                "description": "工单 ID",
            },
            "analysis": {
                "type": "string",
                "description": "内部分析（根因 / 证据 / 结论）",
            },
            "solution": {
                "type": "string",
                "description": "解决方案摘要 / 客户回复草稿要点",
            },
            "category": {
                "type": "string",
                "description": "工单分类（来自 classify_ticket），可选",
            },
        },
        "required": ["ticket_id", "analysis", "solution"],
    }
    group = "workflow"
    concurrency_safe = False
    is_write_operation = True

    async def execute(
        self, args: dict[str, Any], session: AgentSession,
    ) -> ToolResult:
        ticket_id = args["ticket_id"]
        if not isinstance(ticket_id, int) or isinstance(ticket_id, bool):
            return ToolResult(success=False, error="ticket_id must be an integer")

        analysis = args["analysis"]
        if not isinstance(analysis, str):
            return ToolResult(success=False, error="analysis must be a string")

        solution = args["solution"]
        if not isinstance(solution, str):
            return ToolResult(success=False, error="solution must be a string")

        category = args.get("category") or session.ticket_type or ""
        if not isinstance(category, str):
            category = str(category)
        reply_draft = session.reply_draft if isinstance(session.reply_draft, str) else ""
        now = _now_iso()

        path = _ticket_file(ticket_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        is_first = not path.exists()

        try:
            if is_first:
                content = self._render_initial(
                    ticket_id=ticket_id,
                    analysis=analysis,
                    solution=solution,
                    category=category,
                    reply_draft=reply_draft,
                    now=now,
                )
                path.write_text(content, encoding="utf-8")
                mode = "first_pass"
            else:
                followup = self._render_followup(
                    analysis=analysis,
                    solution=solution,
                    reply_draft=reply_draft,
                    now=now,
                )
                # Append with a blank line separator
                with path.open("a", encoding="utf-8") as f:
                    f.write("\n\n" + followup)
                mode = "followup"
        except Exception as e:
            return ToolResult(success=False, error=f"archive failed: {e}")

        session.archived = True
        return ToolResult(
            output=f"已归档到 {path} (mode={mode})",
        )

    @staticmethod
    def _render_initial(
        *,
        ticket_id: int | str,
        analysis: str,
        solution: str,
        category: str,
        reply_draft: str,
        now: str,
    ) -> str:
        lines = [
            "---",
            f"ticket_id: {ticket_id}",
            f"archived_at: {now}",
            f"category: {category}",
            "---",
            "",
            f"# 工单 #{ticket_id}",
            "",
            "## 内部分析",
            "",
            analysis.strip(),
            "",
            "## 解决方案 / 建议",
            "",
            solution.strip(),
            "",
        ]
        if reply_draft:
            lines += [
                "## 客户回复草稿（Internal Note）",
                "",
                reply_draft.strip(),
                "",
            ]
        return "\n".join(lines)

    @staticmethod
    def _render_followup(
        *,
        analysis: str,
        solution: str,
        reply_draft: str,
        now: str,
    ) -> str:
        lines = [
            f"## 跟进 {now}",
            "",
            "### 新增分析",
            "",
            analysis.strip(),
            "",
            "### 新增建议",
            "",
            solution.strip(),
            "",
        ]
        if reply_draft:
            lines += [
                "### 客户回复草稿",
                "",
                reply_draft.strip(),
                "",
            ]
        return "\n".join(lines).rstrip() + "\n"

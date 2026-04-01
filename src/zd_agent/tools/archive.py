"""归档工具：将工单处理结果保存到知识库"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zd_agent.config import get_config
from zd_agent.runtime.archive_meta import build_archive_meta
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolResult


class ArchiveTicket(Tool):
    name = "archive_ticket"
    description = "将工单分析结果和解决方案归档到知识库（ai-docs/tickets/）"
    parameters = {
        "type": "object",
        "properties": {
            "ticket_id": {
                "type": "integer",
                "description": "工单 ID",
            },
            "analysis": {
                "type": "string",
                "description": "分析过程和结论",
            },
            "solution": {
                "type": "string",
                "description": "解决方案摘要",
            },
        },
        "required": ["ticket_id", "analysis", "solution"],
    }

    group = "workflow"
    concurrency_safe = False
    is_write_operation = True

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        cfg = get_config()
        archive_dir = Path(cfg.knowledge.archive_dir) / str(args["ticket_id"])
        archive_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc).isoformat()

        try:
            # 写入分析文档
            analysis_path = archive_dir / "analysis.md"
            analysis_content = (
                f"---\n"
                f"ticket_id: {args['ticket_id']}\n"
                f"archived_at: {now}\n"
                f"---\n\n"
                f"# 工单 #{args['ticket_id']} 分析\n\n"
                f"{args['analysis']}\n"
            )
            analysis_path.write_text(analysis_content, encoding="utf-8")

            # 写入回复内容
            if session.reply_draft:
                reply_path = archive_dir / "reply.md"
                reply_path.write_text(session.reply_draft, encoding="utf-8")

            meta = build_archive_meta(
                session=session,
                archived_at=now,
                solution_summary=args["solution"],
            )
            meta_path = archive_dir / "meta.json"
            meta_path.write_text(
                json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
            )

            session.archived = True

            return ToolResult(
                output=(
                    f"已归档到 {archive_dir}/\n"
                    f"  - analysis.md（分析过程）\n"
                    f"  - reply.md（三段式 Internal Note）\n"
                    f"  - meta.json（元数据）"
                )
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))

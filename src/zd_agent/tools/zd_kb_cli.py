"""ZdKbCliExec: wraps `zd kb search|article` shell commands."""
from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING

from zd_agent.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from zd_agent.session.agent_session import AgentSession


class ZdKbCliExec(Tool):
    name = "zd_kb_cli_exec"
    description = (
        "执行 Zendesk CLI 的 kb 子命令。subcommand: "
        "search / article / list / categories / sections。"
        "search → `zd kb search \"<query>\"`；"
        "article → `zd kb article <id>` 或加 url_only=true 获取纯链接；"
        "list → `zd kb list <section_id>`；"
        "categories / sections → 列出分类/章节。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "subcommand": {
                "type": "string",
                "enum": ["search", "article", "list", "categories", "sections"],
            },
            "query": {
                "type": "string",
                "description": "search 用的查询词",
            },
            "id": {
                "type": "string",
                "description": "article / list 用的 ID",
            },
            "url_only": {
                "type": "boolean",
                "description": "article 时只返回 URL",
                "default": False,
            },
        },
        "required": ["subcommand"],
    }
    group = "knowledge"
    concurrency_safe = True

    async def execute(self, args: dict[str, Any], session: "AgentSession") -> ToolResult:
        sub = args.get("subcommand", "")
        cmd: list[str] = ["zd", "kb"]

        if sub == "search":
            q = args.get("query")
            if not q:
                return ToolResult(success=False, error="search needs query")
            cmd += ["search", q]
        elif sub == "article":
            aid = args.get("id")
            if not aid:
                return ToolResult(success=False, error="article needs id")
            cmd += ["article", str(aid)]
            if args.get("url_only"):
                cmd.append("--url-only")
        elif sub == "list":
            sid = args.get("id")
            if not sid:
                return ToolResult(success=False, error="list needs id (section_id)")
            cmd += ["list", str(sid)]
        elif sub in ("categories", "sections"):
            cmd += [sub]
        else:
            return ToolResult(success=False, error=f"unknown subcommand: {sub}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            return ToolResult(success=False, error="zd kb CLI timeout (30s)")
        except FileNotFoundError:
            return ToolResult(success=False, error="zd CLI not installed")

        if proc.returncode != 0:
            return ToolResult(
                success=False,
                error=f"zd kb failed (rc={proc.returncode}): "
                      f"{stderr.decode('utf-8', errors='replace').strip()}",
            )
        out = stdout.decode("utf-8", errors="replace")
        return ToolResult(output=out[:20000])

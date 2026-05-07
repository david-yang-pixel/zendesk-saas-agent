"""ZdKbCliExec: wraps `zd kb search|article` shell commands."""
from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path
from typing import Any, TYPE_CHECKING

from zd_agent.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from zd_agent.session.agent_session import AgentSession


def _resolve_zd_bin() -> str | None:
    """找到 zd CLI 可执行路径。

    优先级：
    1. shutil.which("zd")（PATH 里）
    2. sys.executable 同目录（运行的 python venv 的 bin/）
    3. /app/.venv/bin/zd（容器固定位置）
    """
    found = shutil.which("zd")
    if found:
        return found
    py_bin_dir = Path(sys.executable).parent
    candidate = py_bin_dir / "zd"
    if candidate.exists():
        return str(candidate)
    fallback = Path("/app/.venv/bin/zd")
    if fallback.exists():
        return str(fallback)
    return None


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
        zd_bin = _resolve_zd_bin()
        if zd_bin is None:
            return ToolResult(success=False, error="zd CLI not installed")
        cmd: list[str] = [zd_bin, "kb"]

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

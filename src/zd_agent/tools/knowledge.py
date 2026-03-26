"""知识库工具组：搜索和读取 ai-docs/ 中的文档"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from zd_agent.config import get_config
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolResult


class KnowledgeSearch(Tool):
    name = "knowledge_search"
    description = (
        "在知识库（ai-docs/）中全文搜索，范围包含文档、历史工单归档。"
        "可用 path='tickets/' 专门搜索历史工单的分析和解决方案。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词",
            },
            "path": {
                "type": "string",
                "description": "限定搜索的子目录（可选，如 tickets/、docs/）",
            },
        },
        "required": ["query"],
    }

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        cfg = get_config()
        docs_dir = Path(cfg.knowledge.docs_dir)

        if not docs_dir.exists():
            return ToolResult(success=False, error=f"知识库目录不存在: {docs_dir}")

        search_dir = docs_dir
        if sub := args.get("path"):
            search_dir = docs_dir / sub
            if not search_dir.exists():
                return ToolResult(success=False, error=f"子目录不存在: {search_dir}")

        query = args["query"]

        try:
            # 搜索 md + json 文件
            proc = await asyncio.create_subprocess_exec(
                "rg",
                "--ignore-case",
                "--max-count", "5",
                "--context", "2",
                "-g", "*.md",
                "-g", "*.json",
                query,
                str(search_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode == 1:
                return ToolResult(output=f"未找到匹配「{query}」的内容")

            if proc.returncode != 0:
                proc = await asyncio.create_subprocess_exec(
                    "grep",
                    "-r", "-i", "-n",
                    "--include=*.md", "--include=*.json",
                    "-C", "2",
                    query,
                    str(search_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()

            output = stdout.decode("utf-8", errors="replace").strip()
            if not output:
                return ToolResult(output=f"未找到匹配「{query}」的内容")

            if len(output) > 5000:
                output = output[:5000] + "\n\n... (结果过多，已截断)"

            return ToolResult(output=output)
        except FileNotFoundError:
            return ToolResult(success=False, error="rg 和 grep 均不可用")
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class KnowledgeRead(Tool):
    name = "knowledge_read"
    description = "读取知识库中指定文件的完整内容"
    parameters = {
        "type": "object",
        "properties": {
            "filepath": {
                "type": "string",
                "description": "相对于知识库根目录的文件路径",
            },
        },
        "required": ["filepath"],
    }

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        cfg = get_config()
        docs_dir = Path(cfg.knowledge.docs_dir).resolve()
        target = (docs_dir / args["filepath"]).resolve()

        try:
            target.relative_to(docs_dir)
        except ValueError:
            return ToolResult(success=False, error="路径超出知识库范围")

        if not target.exists():
            return ToolResult(success=False, error=f"文件不存在: {args['filepath']}")

        try:
            content = target.read_text(encoding="utf-8")
            if len(content) > 50000:
                content = content[:50000] + "\n\n... (文件过大，已截断)"
            return ToolResult(output=content)
        except Exception as e:
            return ToolResult(success=False, error=str(e))

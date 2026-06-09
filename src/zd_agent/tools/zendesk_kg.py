"""Zendesk Knowledge Graph 工具：通过 zendesk-kg CLI 搜索知识图谱"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from zd_agent.log import get_logger
from zd_agent.session.agent_session import AgentSession
from zd_agent.tools.base import Tool, ToolResult

logger = get_logger(module="tools.zendesk_kg")

_KG_BIN = Path.home() / ".zendesk-kg" / "zendesk-kg"
_MAX_OUTPUT_CHARS = 8000
_VALID_PRIORITIES = {"low", "normal", "high", "urgent"}


def _coerce_top_k(value: Any) -> int | ToolResult:
    try:
        top_k = int(value)
    except (TypeError, ValueError):
        return ToolResult(success=False, error="top_k must be an integer")
    return min(max(top_k, 1), 20)


async def _run_kg(args: list[str], timeout: float = 30) -> tuple[int, str, str]:
    """执行 zendesk-kg CLI 命令"""
    proc = await asyncio.create_subprocess_exec(
        str(_KG_BIN), *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 1, "", "zendesk-kg 命令超时"
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def _format_result(item: dict[str, Any]) -> str:
    """将单条搜索结果格式化为可读文本"""
    lines = [
        f"## Ticket #{item.get('ticketId', '?')} — {item.get('subject') or '(no subject)'}",
        f"Status: {item.get('status', '-')}  Priority: {item.get('priority', '-')}  "
        f"Created: {item.get('createdAt', '-')}",
    ]
    if item.get("handledBy"):
        lines.append(f"Handled by: {', '.join(item['handledBy'])}")
    if item.get("versions"):
        lines.append(f"Versions: {', '.join(item['versions'])}")
    if item.get("keywords"):
        lines.append(f"Keywords: {', '.join(item['keywords'][:10])}")

    for field, label in (
        ("quickSummary", "Quick Summary"),
        ("issueSummary", "Issue Summary"),
        ("solutionSummary", "Solution Summary"),
    ):
        val = item.get(field)
        if val:
            lines.append(f"\n**{label}:**\n{val}")

    lines.append("")
    return "\n".join(lines)


class ZendeskKgSearch(Tool):
    name = "zendesk_kg_search"
    description = (
        "在 Zendesk 知识图谱中搜索历史工单。"
        "基于向量 + 全文混合检索，返回相关工单的问题摘要、解决方案和元数据。"
        "适合查找类似问题的历史处理方案。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索查询文本，描述要查找的问题或关键词",
            },
            "top_k": {
                "type": "integer",
                "description": "返回结果数量，1-20，默认 5",
            },
            "version": {
                "type": "string",
                "description": "按产品版本过滤，如 '1.0.0'",
            },
            "priority": {
                "type": "string",
                "description": "按优先级过滤: low, normal, high, urgent",
            },
            "keyword": {
                "type": "string",
                "description": "按关键词标签过滤",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    group = "knowledge"

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        if not _KG_BIN.exists():
            return ToolResult(
                success=False,
                error=(
                    "zendesk-kg CLI 未安装。请运行: "
                    "curl -fsSL https://raw.githubusercontent.com/sorphwer/zendesk-kg-cli-release/main/install.sh | sh"
                ),
            )

        query = args.get("query", "")
        if not isinstance(query, str):
            return ToolResult(success=False, error="query must be a string")
        if not query.strip():
            return ToolResult(success=False, error="query 不能为空")

        cmd_args = ["search", query, "--json"]

        top_k = _coerce_top_k(args.get("top_k", 5))
        if isinstance(top_k, ToolResult):
            return top_k
        cmd_args.extend(["-k", str(top_k)])

        if args.get("version"):
            cmd_args.extend(["-v", str(args["version"])])
        if args.get("priority"):
            p = str(args["priority"]).lower()
            if p in _VALID_PRIORITIES:
                cmd_args.extend(["-p", p])
        if args.get("keyword"):
            cmd_args.extend(["--keyword", str(args["keyword"])])

        returncode, stdout, stderr = await _run_kg(cmd_args)

        if returncode != 0:
            err_msg = stderr.strip() or stdout.strip() or "zendesk-kg 执行失败"
            return ToolResult(success=False, error=err_msg)

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return ToolResult(
                success=False,
                error=f"无法解析 zendesk-kg 输出: {stdout[:500]}",
            )

        results = data.get("results", [])
        if not results:
            return ToolResult(output=f"未找到与 \"{query}\" 相关的历史工单。")

        parts = [f"找到 {len(results)} 条相关历史工单：\n"]
        for item in results:
            parts.append(_format_result(item))

        output = "\n".join(parts)
        if len(output) > _MAX_OUTPUT_CHARS:
            output = output[:_MAX_OUTPUT_CHARS] + "\n\n... (输出过长，已截断)"

        return ToolResult(output=output)

"""Subagent 工具：派发子 Agent 执行独立分析任务。"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from zd_agent.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from zd_agent.session.agent_session import AgentSession


class RunSubagent(Tool):
    name = "run_subagent"
    description = (
        "派发一个子 Agent 执行独立分析任务。子 Agent 有独立上下文，"
        "不会污染主对话。适合用于：独立的源码分析、特定仓库的深度搜索、"
        "跨版本对比等需要大量工具调用但结论可摘要的任务。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "子 Agent 需要完成的任务描述",
            },
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "允许子 Agent 使用的工具名称列表。留空则使用默认子集。",
            },
            "context": {
                "type": "string",
                "description": "传递给子 Agent 的背景信息（如当前分析进展、版本号等）",
            },
        },
        "required": ["task"],
    }

    group = "subagent"
    concurrency_safe = False

    async def execute(self, args: dict[str, Any], session: AgentSession) -> ToolResult:
        from zd_agent.runtime.subagent import run_subagent

        task = args["task"]
        tool_names = args.get("tools") or []
        context = args.get("context", "")
        return await run_subagent(task, tool_names, context, session)

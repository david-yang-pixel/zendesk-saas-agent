"""RunSubagentsParallel: dispatch N subagents concurrently via asyncio.gather."""
from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING

from zd_agent.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from zd_agent.session.agent_session import AgentSession


class RunSubagentsParallel(Tool):
    name = "run_subagents_parallel"
    description = (
        "一次并行派发多个独立 subagent。用于根因分析阶段（Step 10-14，"
        "源码 / GitHub / Grafana / SigNoz 并行）和知识匹配阶段（Step 15-18，"
        "Help Center / 历史工单 / 本地知识库 / Web 并行）。"
        "每个 task 拥有独立上下文，互不污染。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "description": "要并行派发的子任务列表",
                "items": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "任务描述",
                        },
                        "tools": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "允许该 subagent 使用的工具名；留空用默认子集",
                        },
                        "context": {
                            "type": "string",
                            "description": "传给 subagent 的背景信息",
                        },
                    },
                    "required": ["task"],
                },
                "minItems": 1,
                "maxItems": 6,
            }
        },
        "required": ["tasks"],
    }
    group = "always"
    concurrency_safe = True

    async def execute(self, args: dict[str, Any], session: "AgentSession") -> ToolResult:
        tasks = args.get("tasks") or []
        if not tasks:
            return ToolResult(success=False, error="tasks is empty")

        # Late import to avoid circular dependency
        from zd_agent.runtime.subagent import run_subagent

        coros = [
            run_subagent(
                t["task"],
                t.get("tools", []) or [],
                t.get("context", "") or "",
                session,
            )
            for t in tasks
        ]

        results = await asyncio.gather(*coros, return_exceptions=True)

        lines: list[str] = []
        any_success = False
        for i, (spec, res) in enumerate(zip(tasks, results), start=1):
            title = str(spec.get("task", ""))[:80]
            lines.append(f"=== Subagent {i}: {title} ===")
            if isinstance(res, Exception):
                lines.append(f"[ERROR] {type(res).__name__}: {res}")
            elif isinstance(res, ToolResult):
                if res.success:
                    any_success = True
                    lines.append(res.output or "(empty output)")
                else:
                    lines.append(f"[FAILED] {res.error}")
            else:
                any_success = True
                lines.append(str(res))
            lines.append("")

        # Success if at least one subagent returned a successful ToolResult
        return ToolResult(
            success=any_success,
            output="\n".join(lines).rstrip(),
            error=None if any_success else "all subagents failed",
        )
